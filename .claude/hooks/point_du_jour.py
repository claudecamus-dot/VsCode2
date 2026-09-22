"""Hook SessionStart — le point du jour : ce qui attend VOTRE decision.

Rupture B de docs/reflexions/approche-disruptive-wiki-2026-07-31.md, arbitree le
2026-07-31. La mesure qui la fonde : sur 242 jobs enregistres depuis les boutons du
wiki, 241 etaient des artefacts de tests et UN SEUL une action humaine. Personne ne
vient sur la page. En revanche, la conversation sert tous les jours -- 67 runs
orchestres. Donc l'information ne s'affiche plus la ou personne ne regarde : elle
arrive dans le canal reellement utilise.

CE QUE CE HOOK NE FAIT PAS. Il ne redit rien de ce que `scan_transcripts.py` annonce
deja au meme demarrage (runs a solder, reliquat non commite, diagnostic perime,
constats ecartes) : deux hooks qui se repetent forment le mur qu'on cesse de lire --
exactement la maladie dont souffrait le site. Il ne dit qu'une chose, celle que
personne ne disait : **ce qui attend un arbitrage humain**.

Il reste volontairement COURT (3 lignes au plus). Un point du jour qui deborde
redevient un tableau de bord, et on aura deplace le probleme au lieu de le regler.

Hook LOCAL au hub : `scan_transcripts.py` appartient au canon propage aux six projets
(en-tete « GENERE -- NE PAS EDITER LOCALEMENT »), le modifier pour un besoin
d'affichage du hub casserait les cibles. Lecon payee le 2026-07-31.

stdout en ASCII STRICT : les tests capturent ce flux en subprocess, et une console
cp1252 leve UnicodeDecodeError sur tout caractere hors table (incident 2026-07-29).
"""

import datetime as dt
import json
import os
import re
import sys
import unicodedata

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIAGNOSTIC = os.path.join(RACINE, ".claude", "supervision", "diagnostic.json")
VEILLE = os.path.join(RACINE, ".claude", "veille", "veille.json")
ARBITRAGES = os.path.join(RACINE, ".claude", "supervision", "arbitrages.json")
AUDITS = os.path.join(RACINE, ".claude", "audits")

# Au-dela, une decision qui attend n'est plus une file : c'est un oubli.
SEUIL_ALERTE_JOURS = 7


def _charge(chemin):
    try:
        with open(chemin, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _age_jours(iso):
    """Jours ecoules depuis une date ISO, ou None si illisible."""
    if not iso:
        return None
    try:
        d = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if d.tzinfo:
            d = d.astimezone().replace(tzinfo=None)
        return (dt.datetime.now() - d).days
    except (ValueError, TypeError):
        return None


def _canon():
    """Charge scan_transcripts.py (meme dossier de supervision) pour REUTILISER sa
    logique de fermeture des findings au lieu de la reimplémenter.

    Lecon payee le jour meme de l'ecriture de ce hook (revue fraiche, 2026-07-31) :
    la premiere version croisait cible-contre-cible, sans categorie ni re_challenge.
    Deux faux negatifs reproduits par le relecteur — un arbitrage de routage fermait
    un finding de qualite sur la meme cible (friction cible-suppression, 2026-07-21),
    et un finding re-challenge restait masque par un arbitrage anterieur (constat
    prio 5 du 2026-07-28 : 3 constats sur 4 masques). Ces deux bugs avaient DEJA ete
    payes et corriges dans `finding_arbitre()` ; les reintroduire ici en les
    recodant de tete est exactement ce que la reutilisation evite.
    """
    import importlib.util
    chemin = os.path.join(RACINE, ".claude", "supervision", "scan_transcripts.py")
    spec = importlib.util.spec_from_file_location("scan_transcripts_pdj", chemin)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def findings_ouverts():
    """Findings du diagnostic non fermes par un arbitrage, au sens CANONIQUE.

    Un finding arbitre reste ecrit dans diagnostic.json (reecrit en entier a chaque
    diagnostic) : c'est un arbitrage a sa cible ET couvrant sa categorie qui le clot
    — et `re_challenge: true` prime sur les arbitrages anterieurs au diagnostic.
    Toute cette semantique vit dans `finding_arbitre()` du scan ; on l'appelle, on ne
    la recopie pas.

    Rend cible + titre + categorie : le judas du wiki
    (`scan_projets.render_decisions_html`) et la ligne de ce hook consomment la
    MEME collecte — une seule semantique d'ouverture, deux canaux d'affichage
    (arbitrage « Judas compte » + « Vous prevenir ailleurs », 2026-08-31).
    """
    diag = _charge(DIAGNOSTIC) or {}
    findings = diag.get("findings")
    if not isinstance(findings, list) or not findings:
        return []
    arb = _charge(ARBITRAGES) or {}
    arbitrages = arb.get("arbitrages")
    if not isinstance(arbitrages, list):
        arbitrages = []
    # FAIL-OPEN (2026-09-10, verifie par tests/test_point_du_jour_distribue.py) : ce
    # hook est distribue aux cibles et tourne en SessionStart. Un depot ou
    # scan_transcripts.py manque ou ne s'importe pas ne doit PAS voir son ouverture de
    # session bloquee par une pile d'exception -- on se tait, c'est tout.
    try:
        canon = _canon()
    except Exception:
        return []
    if canon is None:
        return []
    # write_diagnostic.py ecrit la cle "generated" (ancien nom "genere" tolere pour
    # les fichiers/tests anterieurs) -- une mauvaise cle ici laisse `genere` a "" en
    # permanence et court-circuite `finding_arbitre()` (cf. sa docstring, jour="").
    genere = str(diag.get("generated") or diag.get("genere") or diag.get("date") or "")
    ouverts = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        cible = (f.get("cible") or "").strip()
        if not cible:
            continue
        if not canon.finding_arbitre(f, arbitrages, posterieur_a=genere):
            ouverts.append({"cible": cible,
                            "titre": (f.get("titre") or "").strip(),
                            "categorie": (f.get("categorie") or "").strip(),
                            "owner": (f.get("owner") or "").strip(),
                            "echeance": str(f.get("echeance") or "").strip()})
    return ouverts


def findings_echus(ouverts=None, aujourdhui=None):
    """[(cible, titre, owner, jours de depassement)] des findings OUVERTS dont
    l'echeance posee par `write_diagnostic.py` est PASSEE, du plus en retard au moins.

    Pourquoi ici : un constat non arbitre est deja annonce par la ligne precedente, mais
    en TAS (« 11 finding(s) sans arbitrage »). Rien ne distinguait celui qui attend
    depuis deux jours de celui que plus personne ne traitera. L'echeance est la
    difference, et elle ne sert a rien si personne ne la lit — d'ou cette ligne, qui
    NOMME les depasses.

    Une echeance absente ou illisible ne compte PAS comme depassee : le fail-open du
    hook vaut aussi pour son contenu, et crier sur un champ qu'on n'a pas su lire
    apprend a ignorer la ligne. Un constat ecrit avant ce champ en recevra un a la
    prochaine ecriture du diagnostic.
    """
    aujourdhui = aujourdhui or dt.date.today()
    echus = []
    for f in (ouverts if ouverts is not None else findings_ouverts()):
        try:
            echeance = dt.date.fromisoformat(str(f.get("echeance") or "")[:10])
        except ValueError:
            continue
        retard = (aujourdhui - echeance).days
        if retard > 0:
            echus.append((f.get("cible") or "", f.get("titre") or "",
                          f.get("owner") or "", retard))
    echus.sort(key=lambda t: (-t[3], t[0]))
    return echus


def ligne_findings_echus(ouverts=None, aujourdhui=None):
    """La ligne du point du jour : NOMME le constat le plus en retard, son proprietaire
    et son retard, avec le verbe qui le traite — meme forme que les autres lignes.
    Vide s'il n'y a rien : une ligne « 0 echu » serait du bruit quotidien."""
    echus = findings_echus(ouverts, aujourdhui)
    if not echus:
        return ""
    cible, titre, owner, retard = echus[0]
    reste = f" (+{len(echus) - 1} autre(s))" if len(echus) > 1 else ""
    return _ascii(
        f"{len(echus)} finding(s) ECHU(s) : {cible} - {titre[:70]} (owner {owner},"
        f" {retard} j de retard){reste}"
        f" -- taper : applique {cible} | refuse {cible}")


def findings_non_arbitres():
    """Compat : les seules cibles, dans le meme ordre (consommee par les tests et
    les appels anterieurs a la collecte enrichie)."""
    return [f["cible"] for f in findings_ouverts()]


def _normalise(texte):
    """Ne garde que alphanumerique en minuscules -- pour comparer un slug d'arbitrage
    a l'URL/titre d'une trouvaille sans se faire avoir par la ponctuation (tirets,
    accents echappes en mojibake, etc.)."""
    return re.sub(r"[^a-z0-9]+", "", (texte or "").lower())


def _texte_entree(entree):
    """L'identite normalisee d'une trouvaille : son URL et son titre, sans ponctuation."""
    return _normalise((entree.get("url") or "") + " " + (entree.get("titre") or ""))


def _veille_arbitree(entree, arbitrages):
    """Vrai si une trouvaille de veille est deja couverte par un arbitrage.

    `veille.json` ne porte pas de champ `cible` (contrairement aux findings du
    diagnostic, que `finding_arbitre()` du canon ferme dessus en comparant deux
    cibles egales) : on reconstitue le rapprochement par le slug de la cible
    `veille:<slug>` contre l'URL/titre de la trouvaille -- la seule information
    stable qu'elle porte. Le slug choisi par l'humain qui arbitre n'est pas toujours
    le nom exact du depot (ex. `veille:multi-agent-observability` pour un depot
    `claude-code-hooks-multi-agent-observability`) : la comparaison se fait donc en
    "slug contenu dans le texte de la trouvaille", pas en egalite stricte.

    Meme principe structurel que `finding_arbitre()` : la presence d'un arbitrage
    concernant la cible ferme le constat -- avec UNE exception, mesuree le 2026-08-31
    sur le fichier reel. `veille:dev-browser` porte « INSTRUIT, ADOPTION CIBLEE EN
    ATTENTE » : l'arbitrage existe, mais il dit lui-meme que la decision n'est pas
    prise. Le fermer dessus enterrait la seule attente reelle du lot, c'est-a-dire
    reproduisait par l'autre bout le defaut que le finding
    `veille:decision-non-reinjectee` reprochait a ce hook.

    On ne lit donc du champ `decision` qu'UN marqueur de convention, « EN ATTENTE »,
    au meme titre que ACCEPTE / ECARTE / INSTRUIT -- pas une analyse de prose. Tout
    le reste (savoir si un ECARTE merite d'etre rouvert) reste un rearbitrage humain,
    pas une detection."""
    texte = _texte_entree(entree)
    if not texte:
        return False
    for arb in arbitrages or []:
        cible = arb.get("cible") or ""
        if not cible.startswith("veille:"):
            continue
        # Le verdict se lit dans la TETE de la decision, avant le premier « : » -- la
        # ou la convention du fichier le place (« INSTRUIT, ADOPTION CIBLEE EN ATTENTE
        # (statut etudie) : <prose> »). Chercher le marqueur dans toute la prose
        # rouvrait des verdicts conclusifs dont le corps mentionne « en attente » a
        # propos d'autre chose : mesure le 2026-08-31, 2 cas sur 3.
        # _normalise() retire les espaces : « EN ATTENTE » y devient « enattente ».
        verdict = (arb.get("decision") or "").split(":", 1)[0]
        if "enattente" in _normalise(verdict):
            continue  # l'arbitrage se declare lui-meme non conclusif
        slug = _normalise(cible[len("veille:"):])
        if slug and slug in texte:
            return True
    return False


def trouvailles_ouvertes():
    """Les trouvailles ni adoptees ni ecartees, et pas deja couvertes par un
    arbitrage (cf. `_veille_arbitree`) — les entrees COMPLETES, pour que le judas
    du wiki et la ligne du hook consomment la meme collecte.

    Sans le second filtre, une trouvaille reste annoncee "en attente de VOTRE
    decision" indefiniment des lors que personne ne reporte a la main le statut
    d'arbitrages.json dans veille.json -- panne mecanique mesuree le 2026-08-31 :
    3 des 4 trouvailles annoncees portaient deja une decision tracee depuis le
    2026-07-31."""
    v = _charge(VEILLE) or {}
    entrees = [e for e in (v.get("entrees") or [])
               if isinstance(e, dict) and e.get("statut") in ("nouveau", "etudie")]
    if not entrees:
        return []
    arb = _charge(ARBITRAGES) or {}
    arbitrages = arb.get("arbitrages")
    if not isinstance(arbitrages, list):
        arbitrages = []

    # UN arbitrage ne ferme QU'UNE trouvaille. La comparaison « slug contenu dans le
    # texte » est deliberee (le slug humain n'est pas le nom exact du depot), mais elle
    # devient fausse des que deux trouvailles partagent une racine : mesure le
    # 2026-09-01, `veille:awesome-claude-code` fermait a la fois
    # `hesreallyhim/awesome-claude-code` ET `VoltAgent/awesome-claude-code-subagents` —
    # un arbitrage humain en enterrait deux, dont un jamais tranche (1 slug sur 16).
    # La desambiguisation se fait ICI et pas dans `_veille_arbitree` : c'est le seul
    # endroit qui voit toutes les entrees. En cas d'ambiguite, l'arbitrage revient a la
    # trouvaille dont l'IDENTITE est la plus proche du slug — la plus courte, donc celle
    # que le slug decrit en entier plutot qu'en prefixe.
    fermees = set()
    for a in arbitrages:
        if not str(a.get("cible") or "").startswith("veille:"):
            continue
        candidats = [i for i, e in enumerate(entrees) if _veille_arbitree(e, [a])]
        if candidats:
            fermees.add(min(candidats, key=lambda i: len(_texte_entree(entrees[i]))))
    return [e for i, e in enumerate(entrees) if i not in fermees]


def trouvailles_en_attente():
    """(nombre, age de la doyenne) — la forme compacte pour la ligne du hook."""
    entrees = trouvailles_ouvertes()
    if not entrees:
        return 0, None
    v = _charge(VEILLE) or {}
    ages = [a for a in (_age_jours(e.get("date") or v.get("derniere_veille"))
                        for e in entrees) if a is not None]
    return len(entrees), (max(ages) if ages else None)


def _ascii(texte):
    """Plie un texte en ASCII strict (accents decomposes puis ignores) — la
    console cp1252 leve UnicodeDecodeError sur tout caractere hors table, et un
    titre de veille porte accents et tirets cadratins.

    Neutralise aussi les caracteres de controle (retour a la ligne compris) :
    un titre de veille ou de finding est un texte tiers reinjecte tel quel en
    contexte au SessionStart (audit securite VScode5, finding ASI01/ASI04,
    2026-09-20) — sans ce filtre, un retour a la ligne suffit a faire sortir
    une charge en colonne 0, hors du prefixe qui identifie une ligne du hook.
    La troncature (60-70 car.) qui suit cet appel n'est plus la seule garde.
    """
    plie = unicodedata.normalize("NFKD", texte or "").encode(
        "ascii", "ignore").decode("ascii")
    return "".join(c if c.isprintable() and c not in "\r\n\t" else " " for c in plie)


def ligne_decisions_audit(repertoire=None):
    """Les constats d'audit qui attendent une DECISION de l'utilisateur.

    Pourquoi cette ligne existe (demande utilisateur du 2026-09-09, quatrieme
    formulation du meme besoin : « fais en sorte que je n'aie pas a redemander »).
    Trois natures se cachaient sous le compteur « pratiques en ecart » : des
    defauts que le hub peut corriger, des notes de verification sans rien a
    faire, et des points qui ne se ferment QUE sur decision humaine — purge
    d'historique irreversible, choix produit sur l'authentification, masquage
    d'un detail nominatif. Les derniers n'etaient affiches nulle part : ils
    vivaient dans .claude/audits/<projet>.json, que seul le scan lit. L'utilisateur
    les revoyait donc dans le compteur, en demandait le traitement, et s'entendait
    repondre qu'ils attendaient sa decision — a chaque fois. La boucle ne tenait
    qu'a l'absence de cette ligne.

    Ne lit que les dimensions DEGRADEES : un constat `arbitrage` dans une
    dimension verte est de l'histoire, le ressortir ferait redecider une
    decision deja prise. Fail-open integral (repertoire absent, JSON casse) :
    ce hook s'execute a l'ouverture de session, il ne doit jamais la bloquer.
    """
    repertoire = repertoire or AUDITS
    par_projet = []
    total = 0
    try:
        fichiers = sorted(f for f in os.listdir(repertoire) if f.endswith(".json"))
    except OSError:
        return ""
    for nom_fichier in fichiers:
        try:
            with open(os.path.join(repertoire, nom_fichier),
                         encoding="utf-8") as fh:
                audit = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(audit, dict):
            continue
        projet = str(audit.get("projet") or nom_fichier[:-5])
        n = 0
        for dim in (audit.get("dimensions") or {}).values():
            if not isinstance(dim, dict):
                continue
            if dim.get("niveau") not in ("moyen", "critique"):
                continue
            for c in (dim.get("findings") or dim.get("constats") or []):
                if _constat_en_arbitrage(c):
                    n += 1
        if n:
            par_projet.append(f"{_ascii(projet)} {n}")
            total += n
    if not total:
        return ""
    detail = ", ".join(par_projet)
    return (f"{total} constat(s) d'audit attendent VOTRE decision ({detail})"
            " -- taper : tranche <projet>:<sujet> | montre les decisions")


# Copie du vocabulaire de `scan_projets.ALIAS_STATUT_CONSTAT` (:1929) et de
# `lot.ALIAS_STATUT_CONSTAT` (:78). Le hook est deploye chez les cibles et ne peut
# importer ni l'un ni l'autre : la troisieme copie est le prix du deploiement, pas
# un oubli. C'est celle qui avait ete tenue a la main SANS l'alias.
ALIAS_STATUT_CONSTAT = {"a-arbitrer": "arbitrage"}


def _constat_en_arbitrage(c):
    """Meme regle que `statut_constat` du scan, tenue ici a la main.

    Le hook est DEPLOYE chez les cibles (en-tete « GENERE -- NE PAS EDITER
    LOCALEMENT ») et ne peut donc pas importer scripts/scan_projets.py, qui
    n'existe qu'au hub. Deux implementations de la meme regle : celle-ci est
    volontairement minimale — le champ explicite, sinon le seul marqueur
    « arbitrage » dans le titre — et le test du hub verifie qu'elles s'accordent
    sur les titres reels des audits.

    L'ALIAS est la moitie qui manquait (2026-09-21). `statut_constat` traduit
    « a-arbitrer » en « arbitrage » (scan_projets.py:1951, lot.py:197) ; ici la
    comparaison etait directe, donc fausse des que le champ explicite etait
    renseigne — et le branchement `if explicite:` court-circuitait aussi le repli
    sur le titre. Or les audits reels n'ecrivent QUE « a-arbitrer » : le hook
    annoncait 0 decision en attente quand il y en avait 15 (VSCode3 11, VSCode 1,
    Vscode7-CAT 3). Le test existant ne l'attrapait pas, ses fixtures ecrivant
    deja « arbitrage » — un vocabulaire que la production n'emploie pas.
    """
    if not isinstance(c, dict):
        return False
    explicite = str(c.get("statut") or "").strip().lower()
    if explicite:
        return ALIAS_STATUT_CONSTAT.get(explicite, explicite) == "arbitrage"
    # `.upper()` des DEUX cotes : chercher « arbitrage » en minuscules dans une
    # chaine passee en majuscules ne matche jamais -- garde-fou qui compare autre
    # chose, attrape par le test avant tout commit (motif deja paye 3 fois).
    return "ARBITRAGE" in str(c.get("titre") or "").upper()


# --- Derive du kit : le seuil BLOQUANT (arbitre le 2026-09-19) ---------------------
# Cause racine « vehicule sans cadence » : le kit est corrige au hub, les cibles restent
# en arriere. Mesure du 2026-09-19 : une garde posee a 12h48 etait presente sur 2 depots
# sur 8 ; le mot « kit » apparait dans 11 arbitrages — la derive n'est pas arbitree une
# fois, elle est RE-arbitree chaque semaine. Le manque n'etait pas la mesure (affichee a
# chaque SessionStart depuis le 2026-09-08) : c'est qu'elle ne declenchait rien et se
# noyait dans la liste. Au-dela de ce seuil, elle passe EN TETE avec la commande exacte.
SEUIL_DERIVE_BLOQUANTE_JOURS = 3

# CRITERE D'ECHEC DE CE RAPPEL, pose par le superviseur le 2026-09-19, a relire tel quel :
# « dans 7 jours, point_du_jour annonce encore 7 cibles qui derivent ». Le chiffre est
# publie quotidiennement par `kit_installe.ligne_point_du_jour` — il suffit de le relire
# le 2026-09-26. Conclusion a appliquer sans discuter : UN RAPPEL QUI NE FAIT PAS BAISSER
# CE CHIFFRE EST UN RAPPEL MORT, A RETIRER PLUTOT QU'A RENFORCER — supprimer alors ce
# bloc et son seuil, ne pas les rendre plus bruyants.


# --- Plans de fond ecrits hors du canal de decision -------------------------------
# Mesure du 2026-09-20 : `docs/wiki/technical/plan-solde-des-ecarts.md` (6 381 octets,
# date du 2026-09-09) portait le remede de fond a la plainte « fais en sorte que je
# n'aie pas a redemander » -- et AUCUN des 273 arbitrages ne cite un `plan-*.md`. Onze
# jours, aucun arbitrage, aucune application ; la meme plainte a ete reformulee les
# 09/09, 11/09 et 20/09. Un remede ecrit hors de diagnostic.json n'est jamais arbitre,
# donc jamais applique : ce rappel ramene le plan dans le canal.
PLANS_TECHNIQUES = os.path.join(RACINE, "docs", "wiki", "technical")
# Au-dela, un plan que personne n'a tranche n'attend plus : il dort.
SEUIL_PLAN_NON_ARBITRE_JOURS = 7


def _age_plan(chemin):
    """Age du plan, en jours. La date de reference est le `updated:` du front-matter
    — c'est la date que l'auteur pose et que le wiki affiche ; le `mtime` ne sert que
    de repli, un checkout ou une regeneration le remet a zero et ferait taire le
    rappel sur le cas meme qu'il doit attraper."""
    try:
        with open(chemin, encoding="utf-8") as fh:
            tete = fh.read(400)
    except OSError:
        return None
    m = re.search(r"^updated:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", tete, re.M)
    if m:
        age = _age_jours(m.group(1))
        if age is not None:
            return age
    try:
        return _age_jours(dt.datetime.fromtimestamp(os.path.getmtime(chemin)).isoformat())
    except OSError:
        return None


def plans_non_arbitres(repertoire=None):
    """[(nom de fichier, age en jours)] des `plan-*.md` de plus de
    SEUIL_PLAN_NON_ARBITRE_JOURS qu'AUCUN arbitrage ne cite par son nom.

    « Sans arbitrage » se decide ici par le MEME mecanisme que pour une trouvaille de
    veille (`_veille_arbitree`) : un plan ne porte pas de `cible` que
    `finding_arbitre()` pourrait comparer, sa seule identite stable est son nom de
    fichier. On le cherche donc, normalise par `_normalise`, dans le texte de
    l'arbitrage (cible + decision) — exactement la regle « slug contenu dans le texte »
    deja en service plus haut, pas une seconde definition de la meme question.

    Un plan CITE sort definitivement du rappel : c'est la sortie du dispositif. Sans
    elle, le rappel crierait a perpetuite et on apprendrait a l'ignorer — le defaut
    qu'il est cense corriger.
    """
    repertoire = repertoire or PLANS_TECHNIQUES
    try:
        noms = sorted(n for n in os.listdir(repertoire)
                      if n.startswith("plan-") and n.endswith(".md"))
    except OSError:
        return []
    if not noms:
        return []
    arb = _charge(ARBITRAGES) or {}
    arbitrages = arb.get("arbitrages")
    if not isinstance(arbitrages, list):
        arbitrages = []
    textes = [_normalise((a.get("cible") or "") + " " + (a.get("decision") or ""))
              for a in arbitrages if isinstance(a, dict)]
    orphelins = []
    for nom in noms:
        cle = _normalise(nom)
        if any(cle in t for t in textes):
            continue
        age = _age_plan(os.path.join(repertoire, nom))
        if age is not None and age > SEUIL_PLAN_NON_ARBITRE_JOURS:
            orphelins.append((nom, age))
    orphelins.sort(key=lambda t: (-t[1], t[0]))
    return orphelins


def ligne_plans_non_arbitres(repertoire=None):
    """La ligne du point du jour : nomme le fichier, son age, et le geste qui le sort
    du purgatoire (le reverser en findings arbitrables). Vide s'il n'y a rien."""
    orphelins = plans_non_arbitres(repertoire)
    if not orphelins:
        return ""
    nom, age = orphelins[0]
    reste = f" (+{len(orphelins) - 1} autre(s))" if len(orphelins) > 1 else ""
    return _ascii(
        f"{len(orphelins)} plan(s) de docs/wiki/technical attendent un arbitrage : {nom}, {age} j{reste}"
        " -- hors du canal de decision, un plan n'est jamais applique. Le reverser"
        " en finding(s) : py .claude/supervision/write_diagnostic.py --fusionner"
        )


def _blob_git(chemin):
    """Empreinte git (sha1 de `blob <taille>\\0<contenu>`) d'un fichier sur disque — la
    meme que celle que `git log --raw` imprime, donc comparable sans `git show`."""
    import hashlib
    try:
        with open(chemin, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def _date_plus_ancien_non_propage(source_rel, blob_copie):
    """Date ISO du PLUS ANCIEN commit du hub que la copie installee ne porte pas encore.

    QUELLE DATE DE REFERENCE, ET POURQUOI. Trois candidates etaient disponibles :
    - le `mtime` du fichier : detruit par tout checkout ou toute regeneration, il date
      le dernier passage du disque, pas le dernier changement reel — inexploitable ;
    - une date de derniere propagation : elle n'existe NULLE PART par fichier (ni dans
      `export/MANIFESTE.json`, ni dans `runs.jsonl`) — il aurait fallu l'inventer, donc
      partir de zero pour toute la flotte ;
    - l'historique git de la SOURCE dans le hub : il existe pour chaque source, il est
      stable (un checkout ne le change pas), et il mesure exactement ce dont on parle —
      « ce correctif attend d'etre propage depuis N jours ». C'est celui-la.

    Pas le DERNIER commit de la source, cependant : une source re-corrigee aujourd'hui
    remettrait le compteur a zero alors que la cible accumule du retard depuis huit
    jours — le rappel se tairait precisement sur le cas qu'il existe pour attraper. On
    remonte donc l'historique du plus recent au plus ancien jusqu'au commit dont
    l'empreinte de blob EGALE celle de la copie installee (c'est la version que la cible
    porte) : le commit juste apres est le plus ancien changement non propage.

    Un seul `git log --raw` par (source, copie) : les empreintes de blob sont dans sa
    sortie, aucun `git show` par commit, le hook reste sous sa seconde.

    REPLI CONSERVATEUR : si aucune empreinte ne correspond (cas reel : une copie en CRLF
    face a un depot en LF n'aura jamais le meme blob), on retombe sur la date du DERNIER
    commit. Cela SOUS-estime l'age au lieu de le surestimer — un rappel qui se tait a
    tort coute une journee, un rappel qui crie a tort se fait ignorer pour toujours.
    """
    try:
        import subprocess
        out = subprocess.run(
            ["git", "log", "-n", "40", "--format=C %cI", "--raw", "--abbrev=40",
             "--no-renames", "--", source_rel],
            cwd=RACINE, capture_output=True, text=True, encoding="utf-8", timeout=15)
    except Exception:  # pragma: no cover - fail-open
        return None
    if out.returncode != 0:
        return None
    commits = []  # [(date_iso, blob_apres)], du plus recent au plus ancien
    date = None
    for ligne in out.stdout.splitlines():
        if ligne.startswith("C "):
            date = ligne[2:].strip()
        elif ligne.startswith(":") and date:
            champs = ligne.split()
            if len(champs) >= 4:
                commits.append((date, champs[3]))
                date = None
    if not commits:
        return None
    if blob_copie:
        for i, (_d, blob) in enumerate(commits):
            if blob == blob_copie:
                return commits[i - 1][0] if i > 0 else None
    return commits[0][0]


def retards_ages(derives, kit_installe, carte, chemins=None):
    """[(projet, destination, age_jours)] des RETARDS dont le plus ancien correctif non
    propage date d'au moins `SEUIL_DERIVE_BLOQUANTE_JOURS` jours, du plus ancien au plus
    recent.

    L'age n'est calcule QUE sur les destinations qualifiees `retard` par
    `kit_installe.qualifier()` : sur une specialisation ou un structurellement local, la
    divergence n'est pas un retard du hub et son « age » ne voudrait rien dire. Source
    inconnue de la carte, ou date illisible -> destination ignoree (fail-open).
    """
    dest_source = (carte or {}).get("destinations_cibles", {})
    chemins = chemins or {}
    ages = {}
    resultat = []
    for projet, etat in (derives or {}).items():
        for destination in etat.get("derive", []):
            qualif = etat.get("qualif", {}).get(destination) or {}
            if qualif.get("nature") != "retard":
                continue
            source = dest_source.get(destination)
            if not source:
                continue
            racine = chemins.get(projet)
            blob = _blob_git(os.path.join(racine, destination)) if racine else None
            cle = (source, blob)
            if cle not in ages:
                ages[cle] = _age_jours(_date_plus_ancien_non_propage(source, blob))
            age = ages[cle]
            if age is not None and age >= SEUIL_DERIVE_BLOQUANTE_JOURS:
                resultat.append((projet, destination, age))
    resultat.sort(key=lambda t: (-t[2], t[0], t[1]))
    return resultat


def ligne_derive_bloquante(derives, kit_installe, carte, projets=None):
    """La ligne placee EN TETE du point du jour quand un correctif du hub attend depuis
    plus de trois jours — avec la commande exacte de propagation, a copier-coller."""
    retards = retards_ages(derives, kit_installe, carte, projets)
    if not retards:
        return None
    projet, destination, age = retards[0]
    chemin = (projets or {}).get(projet, "<chemin du projet>")
    cibles = sorted({p for p, _d, _a in retards})
    return _ascii(
        "BLOQUANT -- {} correctif(s) du kit attendent depuis plus de {} j sur {} cible(s)"
        " ({} ; le plus ancien : {} chez {}, {} j). Propager MAINTENANT :"
        " py export/install_agentic.py --dry-run \"{}\" puis sans --dry-run"
        .format(len(retards), SEUIL_DERIVE_BLOQUANTE_JOURS, len(cibles), ", ".join(cibles),
                destination, projet, age, chemin))


def _chemins_projets(kit_installe):
    """{nom: chemin} depuis projets.json — pour que la commande soit copiable telle quelle."""
    data = _charge(kit_installe.PROJETS) or {}
    return {p.get("nom"): p.get("chemin") for p in data.get("projets", [])
            if p.get("nom") and p.get("chemin")}


def main():
    # « Vous prevenir ailleurs » (salle atelier-idees, arbitre le 2026-08-31) : la
    # ligne ne DENOMBRE plus, elle donne la commande prete a taper — l'information
    # arrive dans le canal reellement utilise, avec le verbe qui la traite.
    lignes = []

    ouverts = findings_ouverts()
    if ouverts:
        apercu = ", ".join(f["cible"] for f in ouverts[:3]) + (
            "..." if len(ouverts) > 3 else "")
        premier = ouverts[0]["cible"]
        lignes.append(
            f"{len(ouverts)} finding(s) du diagnostic sans arbitrage : {apercu}"
            f" -- taper : applique {premier} | refuse {premier}")

    # Parmi eux, ceux dont l'ECHEANCE est passee (2026-09-20) : la ligne precedente les
    # compte, celle-ci les nomme. Fail-open comme les autres mesures du hook.
    try:
        ligne_echus = ligne_findings_echus(ouverts)
        if ligne_echus:
            lignes.append(ligne_echus)
    except Exception as exc:  # fail-open : un hook ne bloque jamais la session
        lignes.append(f"findings echus : mesure impossible ({_ascii(str(exc))})")

    # Un plan de fond ecrit hors de diagnostic.json (2026-09-20) : meme nature que la
    # ligne precedente -- quelque chose qui attend un arbitrage -- donc place juste
    # apres elle, dans la fenetre de 3 lignes reellement affichee.
    try:
        ligne_plans = ligne_plans_non_arbitres()
        if ligne_plans:
            lignes.append(ligne_plans)
    except Exception as exc:  # fail-open : un hook ne bloque jamais la session
        lignes.append(f"plans non arbitres : mesure impossible ({_ascii(str(exc))})")

    entrees = trouvailles_ouvertes()
    n, age = trouvailles_en_attente()
    if n:
        suffixe = ""
        if age is not None:
            urgence = " -- a trancher" if age >= SEUIL_ALERTE_JOURS else ""
            suffixe = f" (la plus ancienne depuis {age} j{urgence})"
        def _age_ou_moins_un(e):
            a = _age_jours(e.get("date"))
            return -1 if a is None else a
        doyenne = _ascii((max(entrees, key=_age_ou_moins_un).get("titre")
                          or "").strip())[:60]
        verbes = (f' -- taper : adopte "{doyenne}" | ecarte "{doyenne}"'
                  if doyenne else "")
        lignes.append(
            f"{n} trouvaille(s) de veille attendent votre decision{suffixe}{verbes}")

    # Les constats d'audit qui attendent une decision humaine (2026-09-09) : ils
    # ne se ferment pas tout seuls et n'etaient affiches nulle part.
    #
    # Ce bloc etait le DERNIER de la fonction jusqu'au 2026-09-21, donc
    # systematiquement coupe par la troncature d'affichage (`lignes[:3]`) : la seule
    # ligne qui nomme une decision non delegable etait celle qu'on sacrifiait en
    # premier. Remonte ici, AVANT la derive de kit, qui est une mesure d'etat et non
    # une decision de l'utilisateur.
    try:
        ligne_audit = ligne_decisions_audit()
        if ligne_audit:
            lignes.append(ligne_audit)
    except Exception as exc:
        lignes.append(f"decisions d'audit : mesure impossible ({_ascii(str(exc))})")

    # Kit agentic installe chez les cibles vs kit publie (finding
    # flotte:write_diagnostic-deploye-refuse-les-categories-pratique, 2026-09-08) :
    # deux cibles refusaient ce que leur skill prescrivait sans qu'aucun etage le dise.
    # Fail-open : une mesure impossible se DIT, elle ne casse pas le point du jour.
    #
    # HUB-ONLY, silencieusement (finding VScode5:point_du_jour-kit_installe-jamais-expedie,
    # 2026-09-21) : kit_installe.py et carte_generes.py ne sont PAS expedies dans export/ --
    # ils comparent une source du hub a sa copie installee via l'historique GIT DU HUB
    # (_date_plus_ancien_non_propage), ce qu'un depot cible ne peut structurellement pas
    # faire (il n'a pas l'historique du hub). Ce n'est donc pas un oubli d'expedition a
    # corriger, mais une fonctionnalite HUB-ONLY par nature : sur les 6 cibles, l'import
    # echoue a CHAQUE session, pour toujours, et la ligne "mesure impossible" qui en
    # resultait etait du bruit permanent plutot qu'un signal. On ne tente le bloc que
    # si scripts/scan_projets.py est present (marqueur fiable du hub, deja utilise
    # ailleurs dans ce fichier) ; ailleurs, silence.
    if os.path.isfile(os.path.join(RACINE, "scripts", "scan_projets.py")):
        try:
            sys.path.insert(0, os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dispositif"))
            import kit_installe
            derives = kit_installe.derives_par_projet()
            if derives:
                lignes.append(kit_installe.ligne_point_du_jour(derives))
                # Seuil bloquant : la derive agee passe EN TETE (SEUIL_DERIVE_BLOQUANTE_JOURS).
                try:
                    import carte_generes
                    bloquante = ligne_derive_bloquante(
                        derives, kit_installe, carte_generes.construire(),
                        _chemins_projets(kit_installe))
                    if bloquante:
                        lignes.insert(0, bloquante)
                except Exception as exc:  # fail-open : le point du jour reste affiche
                    lignes.append(
                        f"derive bloquante : mesure impossible ({_ascii(str(exc))})")
        except Exception as exc:
            lignes.append(f"kit installe : mesure impossible ({_ascii(str(exc))})")

    if not lignes:
        # Le silence est une information : rien ne vous attend. On le dit une fois,
        # brievement, plutot que de ne rien afficher -- l'absence de message se lit
        # comme un hook casse.
        print("Point du jour : rien n'attend votre arbitrage.")
        return 0

    print("Point du jour -- ce qui attend VOTRE decision :")
    # PLAFOND borne les lignes de CONTENU (hors titre) a 3, verrouille par
    # test_reste_court (<=4 lignes non vides au total). La notice de troncature
    # (2026-09-21) est elle-meme une ligne de contenu : elle consomme un slot au lieu
    # de s'ajouter, sinon la troncature redevient muette des qu'il y a >3 points --
    # exactement ce qu'elle corrige. D'ou PLAFOND-1 lignes reelles des que ca deborde.
    PLAFOND = 3
    caches = len(lignes) - PLAFOND
    a_afficher = lignes[:PLAFOND - 1] if caches > 0 else lignes[:PLAFOND]
    for ligne in a_afficher:
        print("  " + ligne)
    if caches > 0:
        caches = len(lignes) - len(a_afficher)
        print(f"  ... et {caches} autre(s) point(s) masque(s) par l'affichage --"
              " tout voir : py .claude/hooks/point_du_jour.py")
    return 0


def _signaler(exc):
    """Meme traitement que le scan : un hook qui plante le DIT et laisse une trace.

    Ce bloc portait le meme defaut que `scan_transcripts.py`, corrige le 2026-09-01 sur
    demande utilisateur : « ignore » se lit comme un saut delibere alors que c est un
    plantage, sans localisation, et la ligne disparait avec le defilement de la session.
    On REUTILISE la fonction du canon plutot que d en ecrire une seconde — deux
    definitions d une meme chose finissent par diverger, c est le finding
    `scan_transcripts.py:807` du meme jour.

    Repli si l import echoue : on imprime au moins la pile. Un garde-fou de confort ne
    doit jamais bloquer une session, meme quand il est lui-meme en panne.
    """
    try:
        import importlib.util
        chemin = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "supervision", "scan_transcripts.py")
        spec = importlib.util.spec_from_file_location("_st_incident", chemin)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.signaler_incident(exc)
    except Exception:
        import traceback
        print(f"Point du jour : ECHEC ({exc.__class__.__name__}: {exc})")
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - un hook ne doit jamais bloquer la session
        sys.exit(_signaler(exc))
