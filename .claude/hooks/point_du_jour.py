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
                            "categorie": (f.get("categorie") or "").strip()})
    return ouverts


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
    titre de veille porte accents et tirets cadratins."""
    return unicodedata.normalize("NFKD", texte or "").encode(
        "ascii", "ignore").decode("ascii")


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


def _constat_en_arbitrage(c):
    """Meme regle que `statut_constat` du scan, tenue ici a la main.

    Le hook est DEPLOYE chez les cibles (en-tete « GENERE -- NE PAS EDITER
    LOCALEMENT ») et ne peut donc pas importer scripts/scan_projets.py, qui
    n'existe qu'au hub. Deux implementations de la meme regle : celle-ci est
    volontairement minimale — le champ explicite, sinon le seul marqueur
    « arbitrage » dans le titre — et le test du hub verifie qu'elles s'accordent
    sur les titres reels des audits.
    """
    if not isinstance(c, dict):
        return False
    explicite = str(c.get("statut") or "").strip().lower()
    if explicite:
        return explicite == "arbitrage"
    # `.upper()` des DEUX cotes : chercher « arbitrage » en minuscules dans une
    # chaine passee en majuscules ne matche jamais -- garde-fou qui compare autre
    # chose, attrape par le test avant tout commit (motif deja paye 3 fois).
    return "ARBITRAGE" in str(c.get("titre") or "").upper()


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

    # Kit agentic installe chez les cibles vs kit publie (finding
    # flotte:write_diagnostic-deploye-refuse-les-categories-pratique, 2026-09-08) :
    # deux cibles refusaient ce que leur skill prescrivait sans qu'aucun etage le dise.
    # Fail-open : une mesure impossible se DIT, elle ne casse pas le point du jour.
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dispositif"))
        import kit_installe
        derives = kit_installe.derives_par_projet()
        if derives:
            lignes.append(kit_installe.ligne_point_du_jour(derives))
    except Exception as exc:
        lignes.append(f"kit installe : mesure impossible ({_ascii(str(exc))})")

    # Les constats d'audit qui attendent une decision humaine (2026-09-09) : ils
    # ne se ferment pas tout seuls et n'etaient affiches nulle part.
    try:
        ligne_audit = ligne_decisions_audit()
        if ligne_audit:
            lignes.append(ligne_audit)
    except Exception as exc:
        lignes.append(f"decisions d'audit : mesure impossible ({_ascii(str(exc))})")

    if not lignes:
        # Le silence est une information : rien ne vous attend. On le dit une fois,
        # brievement, plutot que de ne rien afficher -- l'absence de message se lit
        # comme un hook casse.
        print("Point du jour : rien n'attend votre arbitrage.")
        return 0

    print("Point du jour -- ce qui attend VOTRE decision :")
    for ligne in lignes[:3]:
        print("  " + ligne)
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
