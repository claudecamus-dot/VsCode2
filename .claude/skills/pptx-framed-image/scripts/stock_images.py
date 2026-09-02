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
import ipaddress
import json
import os
import socket
import urllib.parse
import urllib.request

OPENVERSE_SEARCH = "https://api.openverse.org/v1/images/"
LICENSE_NOTE = "CC0 (domaine public) via Openverse — aucune attribution requise"


def search_photo(query, seed=0, aspect_ratio=None):
    params = {"q": query, "license": "cc0", "page_size": 20, "mature": "false"}
    if aspect_ratio:
        params["aspect_ratio"] = aspect_ratio
    url = f"{OPENVERSE_SEARCH}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "bmad-iap-cadrage-ppt/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
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
    hote = urllib.parse.urlsplit(url).hostname
    if not hote:
        raise ValueError(f"refused image URL without a host: {url[:80]!r}")
    try:
        adresses = [str(ipaddress.ip_address(hote))]
    except ValueError:
        try:
            infos = socket.getaddrinfo(hote, None)
        except OSError as exc:
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


# Opener dédié au téléchargement de l'image : `build_opener` reconnaît que
# `_RedirectValidant` dérive de `HTTPRedirectHandler` et REMPLACE le handler de
# redirection par défaut par celui-ci (il ne l'ajoute pas en double) — tout le
# reste (HTTPHandler, HTTPSHandler, gestion d'erreurs...) garde le comportement
# standard d'urllib. Un opener dédié, pas `install_opener` : on ne modifie pas
# le comportement global d'urllib pour le reste du process (dont `search_photo`,
# qui continue d'utiliser `urlopen` tel quel pour interroger Openverse).
_OUVREUR_TELECHARGEMENT = urllib.request.build_opener(_RedirectValidant())


def fetch_to(path, query, seed=0, aspect_ratio=None, manifest_path=None):
    img_url, creator, page_url = search_photo(query, seed=seed, aspect_ratio=aspect_ratio)
    if not img_url.lower().startswith(_SCHEMES_AUTORISES):
        raise ValueError(f"refused non-http(s) image URL from Openverse: {img_url[:80]!r}")
    _verifier_hote_public(img_url)
    req = urllib.request.Request(img_url, headers={"User-Agent": "bmad-iap-cadrage-ppt/1.0"})
    try:
        with _OUVREUR_TELECHARGEMENT.open(req, timeout=20) as r, open(path, "wb") as f:
            recu = 0
            while True:
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


def _record(manifest_path, filename, query, creator, page_url):
    entry = {
        "file": filename, "query": query, "creator": creator,
        "source": page_url, "license": LICENSE_NOTE,
    }
    manifest = []
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    manifest = [m for m in manifest if m.get("file") != filename] + [entry]
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
