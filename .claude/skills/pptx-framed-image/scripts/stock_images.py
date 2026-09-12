"""Fetch real royalty-free photos (Openverse) for a template photo frame,
instead of a procedurally generated placeholder — flat vector "landscapes"
(see nature_images.py) read as cheap next to a real client-facing deck; a
real photo doesn't.

Source: Openverse (api.openverse.org), a public search API over openly
licensed content (Wikimedia, Flickr, StockSnap, Rawpixel...). No API key
required for read access. Filtered to ``license=cc0`` (public domain) only —
zero attribution required, so a chapter/vision slide can carry the image with
no caption. (An earlier attempt used the Pexels API without a key; that
turned out to be a stale Cloudflare cache hit on one specific query, not a
real credential path — confirmed by re-querying and getting 401 on other
terms. Openverse's no-key access is the API's actual, documented behavior,
verified by repeating the same query and getting a consistent, non-cached
result each time.)

Public API
----------
- ``search_photo(query, seed=0, aspect_ratio=None)`` -> (image_url, creator, page_url)
      Query Openverse, cc0-only, return the ``seed``-th result.
- ``fetch_to(path, query, seed=0, aspect_ratio=None, manifest_path=None)``
      Download that photo to ``path``; if ``manifest_path`` is given, append/
      update a provenance record there (query, creator, source page, license).
"""
import http.client
import ipaddress
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

OPENVERSE_SEARCH = "https://api.openverse.org/v1/images/"
LICENSE_NOTE = "CC0 (domaine public) via Openverse — aucune attribution requise"

# `socket.getaddrinfo` n'a pas de parametre `timeout` : sans borne, une
# resolution DNS qui ne repond jamais bloque indefiniment (audit 2026-09-03,
# finding performance).
#
# CE QUI NE MARCHE PAS — et qui etait ecrit ici jusqu'au 2026-09-07 :
# `socket.setdefaulttimeout` pose autour de l'appel. La doc CPython ne parle
# que des « new socket objects » ; `getaddrinfo` est un appel synchrone du
# resolveur systeme, hors de sa portee. Mesure du 2026-09-07 (Windows,
# Python 3.14.5) : sous `setdefaulttimeout(0.0001)`, `getaddrinfo(
# 'api.openverse.org')` rend 2 adresses en 33,1 ms et un nom NXDOMAIN leve
# `gaierror` apres 95,4 ms — aucun timeout ne se declenche. Le garde-fou
# reglait autre chose que ce qu'il protegeait, et son test verifiait que la
# VALEUR etait posee, pas qu'elle AGISSAIT : vert par construction.
#
# CE QUI MARCHE : deporter l'appel dans un thread DEMON et borner l'ATTENTE
# (`Thread.join(timeout)`). Le thread reste bloque cote resolveur — c'est
# irreductible, aucune API Python n'annule un `getaddrinfo` en cours — mais
# l'appelant, lui, reprend la main, et un thread demon n'empeche pas le
# process de sortir. Le refus est fail-closed (cf. `_verifier_hote_public`).
_DNS_TIMEOUT_S = 10

# Le `timeout=` d'urllib est un timeout PAR LECTURE SOCKET, rearme a chaque
# `r.read()` — un serveur qui envoie un octet de temps en temps garde la
# connexion ouverte indefiniment sans jamais le declencher (audit 2026-09-03,
# finding performance : borne theorique de ~2h15 pour un seul candidat).
# `_DEADLINE_TELECHARGEMENT_S` est un budget GLOBAL sur la duree du
# telechargement, verifie a chaque bloc lu, independant du timeout par lecture.
_DEADLINE_TELECHARGEMENT_S = 60

# LA MEME BORNE, SUR L'AUTRE APPEL RESEAU. Le budget global ci-dessus n'avait
# ete pose que sur le telechargement : la RECHERCHE, elle, ne portait que le
# `timeout=15` par lecture socket — celui dont le commentaire ci-dessus dit
# qu'il ne borne rien. Un serveur qui repond par blocs espaces retenait donc le
# build sans borne, sur l'appel qui prend justement la chaine de recherche en
# entree (residu releve par l'audit 2026-09-07 en marge du finding performance
# n°1, mesure par test : sous flux infini, la recherche consommait le flux
# jusqu'au plafond memoire sans jamais regarder l'heure). 30 s et non 60 : une
# reponse de recherche pese ~40 Ko mesures, contre 25 Mo pour une image.
_DEADLINE_RECHERCHE_S = 30

# Plafond memoire sur la REPONSE DE RECHERCHE. L'image etait bornee a 25 Mo
# (`_TAILLE_MAX`) et la reponse JSON ne l'etait pas : `json.load(r)` lit tout
# ce que le serveur envoie, sur l'appel qui prend justement la chaine de
# recherche en entree (audit 2026-09-07, finding performance n°2). 4 Mo est
# large pour 20 resultats (la reponse reelle mesuree pese ~40 Ko) et borne
# pour la memoire. Le depassement est constate SUR LE CUMUL des blocs lus, et
# le refus part AVANT `json.loads` : c'est ce qui permet de distinguer
# « reponse complete » de « reponse coupee au plafond » — sans quoi un JSON
# tronque ressortirait en erreur de syntaxe, diagnostic trompeur.
_TAILLE_MAX_RECHERCHE = 4 * 1024 * 1024


def _resoudre_borne(hote, port=None, type_socket=0, timeout_s=_DNS_TIMEOUT_S):
    """`socket.getaddrinfo(hote, port)` avec un budget de temps REEL.

    Rend la liste de `getaddrinfo`, ou releve l'exception d'origine, ou leve
    `TimeoutError` si la resolution depasse `timeout_s`. `TimeoutError` derive
    d'`OSError` : les appelants qui refusent deja sur `OSError` (fail-closed)
    traitent donc le depassement comme un echec de resolution, sans code en
    plus. Cf. le commentaire de `_DNS_TIMEOUT_S` pour ce qui a ete essaye
    avant et pourquoi ca ne bornait rien.

    `type_socket` : passe tel quel a `getaddrinfo`. 0 (defaut) rend TOUS les
    types pour l'hote — ce qu'on veut pour une simple validation d'adresses.
    Qui va CONNECTER doit passer `socket.SOCK_STREAM`, comme le fait
    `socket.create_connection` : sans ce filtre, la 1re entree rendue peut
    etre une entree UDP, et une socket UDP « se connecte » sans erreur puis
    reste muette (constate en appel reel sur api.openverse.org, 2026-09-08 :
    handshake TLS en timeout de lecture au lieu d'une reponse)."""
    resultat = {}

    def _resoudre():
        try:
            resultat["infos"] = socket.getaddrinfo(hote, port, 0, type_socket)
        except BaseException as exc:   # remonte tel quel a l'appelant
            resultat["erreur"] = exc

    fil = threading.Thread(target=_resoudre, name="stock-images-dns", daemon=True)
    fil.start()
    fil.join(timeout_s)
    if fil.is_alive():
        raise TimeoutError(
            f"DNS resolution of {hote!r} exceeded {timeout_s}s")
    if "erreur" in resultat:
        raise resultat["erreur"]
    return resultat["infos"]


def search_photo(query, seed=0, aspect_ratio=None,
                 deadline_s=_DEADLINE_RECHERCHE_S):
    """`deadline_s` : budget total (secondes) pour la RECHERCHE, verifie a
    chaque bloc lu — meme mecanique que le `deadline_s` de `fetch_to`, pour la
    meme raison (le `timeout=` ci-dessous se rearme a chaque lecture et ne
    borne donc rien de global). Cf. `_DEADLINE_RECHERCHE_S`."""
    params = {"q": query, "license": "cc0", "page_size": 20, "mature": "false"}
    if aspect_ratio:
        params["aspect_ratio"] = aspect_ratio
    url = f"{OPENVERSE_SEARCH}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "bmad-iap-cadrage-ppt/1.0"})
    # `_OUVREUR_DURCI` et non `urllib.request.urlopen` : la recherche suivait
    # ses redirections sans validation d'hote et se connectait a ce que le DNS
    # rendait au dernier moment, alors que le telechargement — deux ecrans plus
    # bas — passait par un opener durci. Deux politiques reseau pour deux appels
    # au meme service, la moins protegee sur celui qui prend l'entree
    # utilisateur (audit 2026-09-07, finding securite n°4).
    debut = time.monotonic()
    morceaux = []
    recu = 0
    with _OUVREUR_DURCI.open(req, timeout=15) as r:
        while True:
            ecoule = time.monotonic() - debut
            if ecoule > deadline_s:
                raise TimeoutError(
                    f"Openverse search exceeded deadline of {deadline_s}s "
                    f"({recu} bytes received) for {query!r}")
            bloc = r.read(64 * 1024)
            if not bloc:
                break
            recu += len(bloc)
            if recu > _TAILLE_MAX_RECHERCHE:
                raise ValueError(
                    f"Openverse search response over {_TAILLE_MAX_RECHERCHE} bytes, refused")
            morceaux.append(bloc)
    data = json.loads(b"".join(morceaux))
    results = data.get("results", [])
    if not results:
        raise RuntimeError(f"no Openverse cc0 result for {query!r}")
    p = results[seed % len(results)]
    return p["url"], p.get("creator") or "inconnu", p.get("foreign_landing_url", "")


# Openverse agrège des sources tierces (Wikimedia, Flickr, StockSnap…) : `img_url` est
# une donnée qu'on ne contrôle pas. Or `urllib.request.urlopen` suit le schéma `file://`
# par défaut — vérifié, il lit un fichier local — donc une entrée dont l'`url` n'est pas
# http(s) faisait recopier un fichier arbitraire du poste dans le cache d'images du deck.
# Et `r.read()` sans plafond charge toute la réponse en mémoire : un serveur tiers
# décidait de la mémoire de la machine.
#
# Les deux fermés le 2026-09-01. La garde existait déjà dans la copie VSCode3 et
# manquait aux 6 autres, dont CELLE-CI qui est la source du kit : c'est la session
# VSCode3 qui l'a signalé, en instruisant sa propre doctrine de resynchronisation —
# laquelle aurait supprimé son correctif en la réalignant sur nous.
_SCHEMES_AUTORISES = ("http://", "https://")
_TAILLE_MAX = 25 * 1024 * 1024   # 25 Mo : large pour une photo, borné pour la mémoire


# La garde de schéma (ci-dessus) ferme `file://` mais pas la DESTINATION : une URL
# http(s) vers une adresse interne — bouclage, privée, link-local, métadonnées cloud
# (169.254.169.254) — la traverse intacte. Vu qu'`img_url` vient d'Openverse, un
# AGRÉGATEUR de sources tierces (Wikimedia, Flickr, StockSnap…), c'est une donnée non
# contrôlée : c'est un SSRF vers le réseau interne de la machine qui exécute ce script,
# pas juste vers Internet. Finding arbitré le 2026-09-02.
#
# La garde doit RÉSOUDRE l'hôte, pas seulement parser la chaîne de l'URL — un nom de
# domaine public en apparence peut très bien pointer un enregistrement A/AAAA vers
# 127.0.0.1 ou 169.254.169.254. `ipaddress.ip_address(...).ipv4_mapped` referme le
# piège classique de l'IPv4 encapsulée dans une IPv6 (`::ffff:127.0.0.1`), qui
# contournerait une garde n'inspectant que l'objet IPv6 tel quel.
def _hote_interne(adresse_texte):
    """True si `adresse_texte` (IPv4 ou IPv6) désigne une adresse non publique :
    bouclage, privée, link-local, réservée, multicast ou non spécifiée (0.0.0.0) —
    y compris quand elle encapsule une telle adresse IPv4 sous forme mappée IPv6."""
    ip = ipaddress.ip_address(adresse_texte)
    candidats = [ip]
    mappee = getattr(ip, "ipv4_mapped", None)
    if mappee is not None:
        candidats.append(mappee)
    return any(
        c.is_loopback or c.is_private or c.is_link_local
        or c.is_reserved or c.is_multicast or c.is_unspecified
        for c in candidats
    )


def _verifier_hote_public(url):
    """Refuse `url` si son hôte est — ou résout vers — une adresse non publique.

    Fail-closed assumé : un hôte qui ne résout PAS DU TOUT est refusé, pas laissé
    passer. Une garde de sécurité qui échoue ouvert sur une erreur réseau (DNS
    injoignable, timeout, nom inconnu) n'en est plus une — l'incertitude doit se
    résoudre du côté du refus, jamais du téléchargement.
    """
    # Le schema n'est verifie qu'une fois par `fetch_to`, sur l'URL DE DEPART --
    # chaque redirection re-appelle CETTE fonction (`_RedirectValidant.
    # redirect_request`) mais ne revalidait plus le schema depuis la reecriture
    # du 2026-09-07/09 (durcissement DNS/TOCTOU) : la stdlib suit `ftp://` comme
    # `http(s)://` dans son allow-list de redirection, donc une redirection vers
    # `ftp://hote-interne/...` repassait la garde d'hote et ouvrait quand meme un
    # chemin non http(s) (regression reperee lors de la fusion avec le hub,
    # 2026-09-12, finding flotte:stock-images-deux-durcissements-divergents).
    if not url.lower().startswith(_SCHEMES_AUTORISES):
        raise ValueError(f"refused non-http(s) URL: {url[:80]!r}")
    hote = urllib.parse.urlsplit(url).hostname
    if not hote:
        raise ValueError(f"refused image URL without a host: {url[:80]!r}")
    try:
        adresses = [str(ipaddress.ip_address(hote))]
    except ValueError:
        try:
            infos = _resoudre_borne(hote)
        except OSError as exc:
            # `TimeoutError` derive d'`OSError` : un DNS qui ne repond pas dans
            # le budget est traite comme un DNS qui echoue — refus, pas attente.
            raise ValueError(
                f"refused image URL: host {hote!r} did not resolve ({exc})") from None
        adresses = [info[4][0] for info in infos]
    for adresse in adresses:
        if _hote_interne(adresse):
            raise ValueError(
                f"refused image URL resolving to a non-public address: "
                f"host={hote!r} address={adresse!r}")


# La garde ci-dessus ne protège que l'URL DE DÉPART. Or `urllib.request.urlopen`
# suit les redirections 3xx TOUT SEUL, sans repasser par nous — un serveur tiers
# (Openverse agrège Wikimedia, Flickr, StockSnap...) qui répond
# `302 Location: http://127.0.0.1:8765/...` ferait viser une adresse interne alors
# que seule l'URL initiale a été contrôlée. La garde mesurerait une adresse et le
# programme en visiterait une autre — exactement la famille de défaut la plus
# coûteuse rencontrée sur ce projet (un garde-fou qui compare/valide autre chose
# que ce qu'il protège). Deuxième volet du finding arbitré le 2026-09-02.
class _RedirectValidant(urllib.request.HTTPRedirectHandler):
    """Un `HTTPRedirectHandler` qui RE-VALIDE la cible de CHAQUE redirection
    avec `_verifier_hote_public` avant de la suivre — appelé par urllib à
    chaque saut 301/302/303/307/308, avant que la connexion suivante ne parte.

    On n'override QUE `redirect_request` : `max_redirections`/`max_repeats`
    restent ceux hérités de la classe de base. Cette limite protège d'autre
    chose (une boucle de redirection infinie) et ajouter une vérification
    d'hôte n'est pas une raison de la retirer.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            _verifier_hote_public(newurl)
        except ValueError as exc:
            raise ValueError(f"refused HTTP redirect to {newurl[:80]!r}: {exc}") from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# TOCTOU : jusqu'ici, `_verifier_hote_public` resolvait l'hote et validait ses
# adresses, PUIS urllib ouvrait la connexion en RE-RESOLVANT le nom de son cote.
# Deux resolutions independantes : un hote a TTL 0 pouvait rendre une adresse
# publique a la verification et une adresse interne a la connexion. La garde
# mesurait une adresse, le programme en visitait une autre — la famille de
# defaut la plus couteuse de ce projet (audit 2026-09-07, finding securite n°3).
#
# `_connexion_vers_adresse_publique` ferme la fenetre : elle resout, valide, et
# se connecte a UNE DES ADRESSES QU'ELLE VIENT DE VALIDER — l'adresse validee
# est, par construction, celle qui est visitee. Elle est injectee a la place de
# `socket.create_connection` dans la connexion HTTP(S) d'urllib ; `Host:` et
# `server_hostname` (SNI) restent derives de `self.host`, donc le nom de domaine
# continue d'etre presente au serveur et le certificat TLS est verifie contre
# LUI, pas contre l'IP.
#
# `_verifier_hote_public` reste appele en amont : il donne un refus clair et
# precoce (avant meme d'ouvrir une socket) et couvre les redirections. Ce n'est
# plus lui qui porte la garantie, c'est le point de connexion.
def _connexion_vers_adresse_publique(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                                     source_address=None):
    """Remplace `socket.create_connection` : meme signature, mais n'ouvre une
    socket que vers une adresse dont la publicite vient d'etre verifiee.

    Fail-closed sur DEUX plans : une resolution qui echoue (ou depasse le
    budget) refuse, et il suffit qu'UNE des adresses rendues soit interne pour
    refuser toute la connexion — on ne « passe pas a l'adresse suivante » sur
    un signal de danger, seulement sur une erreur de connexion banale."""
    hote, port = address[0], address[1]
    try:
        infos = _resoudre_borne(hote, port, socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(
            f"refused connection: host {hote!r} did not resolve ({exc})") from None
    # TOUTES les adresses sont validees AVANT d'en essayer une seule : valider
    # dans la boucle de connexion ferait ouvrir une socket vers la 1re adresse
    # (publique) avant de decouvrir que la 2e est interne. Un hote qui rend une
    # IP publique ET une IP interne ne doit pas pouvoir jouer sur l'ordre de la
    # liste (verrouille par test).
    for info in infos:
        if _hote_interne(info[4][0]):
            raise ValueError(
                f"refused connection to a non-public address: "
                f"host={hote!r} address={info[4][0]!r}")
    derniere_erreur = None
    for famille, type_socket, proto, _canon, sockaddr in infos:
        s = None
        try:
            s = socket.socket(famille, type_socket, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                s.settimeout(timeout)
            if source_address:
                s.bind(source_address)
            s.connect(sockaddr)
            return s
        except OSError as exc:
            derniere_erreur = exc
            if s is not None:
                s.close()
    if derniere_erreur is not None:
        raise derniere_erreur
    raise OSError(f"no address returned for {hote!r}")


# `_create_connection` est pose en ATTRIBUT D'INSTANCE par
# `HTTPConnection.__init__` (verifie sur CPython 3.14) : le surcharger en
# attribut de classe serait ecrase a chaque construction. On l'ecrase donc
# apres l'init — trois lignes, et tout le reste du comportement d'`http.client`
# est intact.
class _ConnexionHTTPValidante(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connexion_vers_adresse_publique


class _ConnexionHTTPSValidante(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connexion_vers_adresse_publique


class _HandlerHTTPValidant(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_ConnexionHTTPValidante, req)


class _HandlerHTTPSValidant(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_ConnexionHTTPSValidante, req, context=self._context)


# Opener durci, utilise par la RECHERCHE comme par le TELECHARGEMENT (il
# s'appelait `_OUVREUR_TELECHARGEMENT` tant qu'il ne servait qu'a ce dernier ;
# le nom aurait menti des que `search_photo` s'y est branchee). `build_opener`
# reconnait que chacun de ces handlers derive du handler par defaut
# correspondant et le REMPLACE (il ne l'ajoute pas en double) — tout le reste
# (gestion d'erreurs, cookies, proxies...) garde le comportement standard
# d'urllib. Un opener dedie, pas `install_opener` : on ne modifie pas le
# comportement global d'urllib pour le reste du process.
_OUVREUR_DURCI = urllib.request.build_opener(
    _RedirectValidant(), _HandlerHTTPValidant(), _HandlerHTTPSValidant())


def fetch_to(path, query, seed=0, aspect_ratio=None, manifest_path=None,
             deadline_s=_DEADLINE_TELECHARGEMENT_S):
    """`deadline_s` : budget total (secondes) pour le TELECHARGEMENT de
    l'image, verifie a chaque bloc lu — independant du `timeout=` par lecture
    socket ci-dessous, qui se rearme a chaque lecture et ne borne donc rien de
    global (finding performance 2026-09-03)."""
    img_url, creator, page_url = search_photo(query, seed=seed, aspect_ratio=aspect_ratio)
    if not img_url.lower().startswith(_SCHEMES_AUTORISES):
        raise ValueError(f"refused non-http(s) image URL from Openverse: {img_url[:80]!r}")
    _verifier_hote_public(img_url)
    req = urllib.request.Request(img_url, headers={"User-Agent": "bmad-iap-cadrage-ppt/1.0"})
    debut = time.monotonic()
    try:
        with _OUVREUR_DURCI.open(req, timeout=20) as r, open(path, "wb") as f:
            recu = 0
            while True:
                ecoule = time.monotonic() - debut
                if ecoule > deadline_s:
                    raise TimeoutError(
                        f"download exceeded deadline of {deadline_s}s "
                        f"({recu} bytes received): {img_url[:80]!r}")
                bloc = r.read(64 * 1024)
                if not bloc:
                    break
                recu += len(bloc)
                if recu > _TAILLE_MAX:
                    raise ValueError(
                        f"image over {_TAILLE_MAX} bytes, download aborted: {img_url[:80]!r}")
                f.write(bloc)
    except BaseException:
        # Le plafond arrêtait bien le téléchargement, mais laissait les 25 Mo déjà
        # écrits SOUS LE NOM DE L'IMAGE ATTENDUE (mesuré : 26 214 400 octets) : le deck
        # aurait embarqué un fichier tronqué, ou le cache aurait grossi d'un fichier que
        # personne ne réclame. Un refus qui laisse son échec derrière lui n'est un refus
        # qu'à moitié. `BaseException` et non `Exception` : une interruption clavier
        # laisse le même déchet.
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    if manifest_path:
        _record(manifest_path, os.path.basename(path), query, creator, page_url)
    return path


def _lire_manifest(manifest_path):
    """Le manifeste de provenance existant, ou `[]` s'il est absent, illisible
    ou corrompu.

    Sans cette garde, un `json.load` sur un fichier tronque levait au milieu de
    `fetch_to`, l'image etait perdue et l'appelant rapportait « Openverse
    indisponible » : une panne LOCALE PERMANENTE diagnostiquee en panne reseau
    transitoire (audit 2026-09-07, robustesse n°5). Le manifeste est un journal
    de provenance reconstruit a chaque image posee — le repartir a vide est
    sans perte reelle, et c'est dit a voix haute plutot qu'avale."""
    if not os.path.exists(manifest_path):
        return []
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print(f"  manifeste de provenance illisible ({exc}) — reparti a vide",
              file=sys.stderr)
        return []
    if not isinstance(manifest, list):
        print(f"  manifeste de provenance inattendu ({type(manifest).__name__} "
              "au lieu d'une liste) — reparti a vide", file=sys.stderr)
        return []
    return [m for m in manifest if isinstance(m, dict)]


# Windows fait echouer `os.replace` par intermittence quand un tiers (antivirus
# temps reel, indexeur) tient encore un handle sur le fichier qu'on vient de
# fermer : PermissionError WinError 5/32, sans rapport avec les droits.
# Mesure du 2026-09-08 sur ce poste : 2 echecs sur 300 remplacements dans
# %TEMP% (0 sur 300 dans un dossier du depot). A 5 images par build, c'est
# ~3 % de builds qui perdraient une photo sur un incident qui se resout tout
# seul en quelques dizaines de millisecondes. Une reprise bornee suffit ; au-dela
# l'erreur remonte, elle ne doit pas etre avalee.
_REPRISES_REMPLACEMENT = 5
_ATTENTE_REMPLACEMENT_S = 0.05


def _ecrire_manifest(manifest_path, manifest):
    """Ecriture ATOMIQUE : fichier temporaire dans le MEME dossier, puis
    `os.replace` (atomique sur POSIX comme sur Windows).

    `open(..., 'w')` tronquait la cible AVANT de serialiser : une interruption
    entre les deux — clavier, disque plein, process tue — laissait un manifeste
    a moitie ecrit, exactement le fichier corrompu que `_lire_manifest`
    ci-dessus doit ensuite rattraper. Ici, soit l'ancien manifeste est intact,
    soit le nouveau est complet ; jamais d'etat intermediaire sur disque."""
    dossier = os.path.dirname(os.path.abspath(manifest_path)) or "."
    fd, temporaire = tempfile.mkstemp(dir=dossier, prefix=".manifest-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        for tentative in range(_REPRISES_REMPLACEMENT):
            try:
                os.replace(temporaire, manifest_path)
                break
            except PermissionError:
                if tentative == _REPRISES_REMPLACEMENT - 1:
                    raise
                time.sleep(_ATTENTE_REMPLACEMENT_S)
    except BaseException:
        try:
            os.remove(temporaire)
        except OSError:
            pass
        raise


def _record(manifest_path, filename, query, creator, page_url):
    """Note la provenance d'une image dans le manifeste (une entree par
    fichier, la derniere ecrase la precedente).

    Le manifeste est relu et reecrit EN ENTIER a chaque image (finding
    performance n°3 du 2026-09-07). Mesure du 2026-09-08 sur le manifeste reel
    (3 209 octets, 11 entrees) : ~10 ms par appel, 5 appels par build, contre un
    build de 5 s. Et ces 10 ms sont ceux du REMPLACEMENT ATOMIQUE, pas de la
    relecture O(n) — passer le fichier en JSONL append-only ne les enleverait
    pas, et couterait le seul usage reel du manifeste : l'ouvrir et lire d'un
    coup d'oeil d'ou viennent les photos du deck. Ce qui comptait dans ce geste
    n'etait donc pas son cout mais sa NON-ATOMICITE, traitee par
    `_ecrire_manifest`."""
    entry = {
        "file": filename, "query": query, "creator": creator,
        "source": page_url, "license": LICENSE_NOTE,
    }
    manifest = _lire_manifest(manifest_path)
    manifest = [m for m in manifest if m.get("file") != filename] + [entry]
    _ecrire_manifest(manifest_path, manifest)
