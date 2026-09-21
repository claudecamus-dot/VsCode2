"""Enregistre une ACCEPTATION d'arbitrage — le pendant manquant de
`refuser_arbitrage.py`, et le frere de `ecarter_trouvaille.py`.

POURQUOI CE SCRIPT EXISTE (diagnostic du 2026-09-19, par reexecution) :
`.claude/supervision/` ne contenait QUE des ecrivains de refus. Faute d'ecrivain
d'acceptation, des agents ont enregistre des acceptations AVEC le script de refus —
4 arbitrages reels portent la decision litterale « REFUSE : ACCEPTE + APPLIQUE »
(2026-09-17). L'edition directe du fichier est par ailleurs refusee par le
classificateur, donc il n'y avait pas d'issue propre.

Et `refuser_arbitrage.py` n'ecrit que `cible`/`date`/`decision` : jamais `titre` ni
`categories`. Or `_couvre()` traitait `categories is None` comme « ferme TOUT » —
137 arbitrages sur 258 masquaient ainsi, a perpetuite, tout constat futur de leur
cible, toutes categories confondues. Plus l'utilisateur arbitrait, plus le
dispositif devenait aveugle. Ce script rend cette entree impossible a ecrire : il
EXIGE `titre` et `categories`, et refuse en sortie non nulle sinon.

Messages en ASCII pur : la console Windows est en cp1252 et ce script est appele
en sous-processus par `serve_wiki.py` (meme contrainte que les autres ecrivains).

Usage :
  py .claude/supervision/log_arbitrage.py --cible <cible> --titre "<titre exact du
      constat>" --categories pratique-dev[,pratique-doc] --decision "ACCEPTE + ..."

Env (tests) : AGENT_SUPERVISION_ARBITRAGES, AGENT_SUPERVISION_DIAGNOSTIC,
AGENT_SUPERVISION_SKIP_SCAN.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARBITRAGES_PATH = os.environ.get("AGENT_SUPERVISION_ARBITRAGES") or os.path.join(
    ROOT, ".claude", "supervision", "arbitrages.json")
DIAGNOSTIC_PATH = os.environ.get("AGENT_SUPERVISION_DIAGNOSTIC") or os.path.join(
    ROOT, ".claude", "supervision", "diagnostic.json")

# Vocabulaire copie de scan_transcripts.CATEGORIES_CONNUES. Duplique volontairement :
# ce fichier part dans le kit chez des cibles qui n'ont pas forcement le scanner du
# hub, et un import optionnel qui echoue en silence rendrait la garde inoperante
# justement la ou personne ne regarde. Un test verrouille l'egalite des deux listes.
CATEGORIES_CONNUES = (
    "ko-repete", "inefficacite", "agent-mort", "interaction",
    "verification-manquante", "non-convergence",
    "pratique-test", "pratique-dev", "pratique-revue", "pratique-design",
    "pratique-doc", "pratique-produit",
    # Volet 3 - dimensions de l'audit technique (miroir de DIM_AUDIT dans
    # scan_projets.py). Absentes jusqu'au 2026-09-21 : un constat d'audit
    # corrige, teste et commite ne pouvait PAS etre ferme, log_arbitrage le
    # refusant « hors vocabulaire ». Mesure du 2026-09-20 : 7 constats des lots
    # VSCode et VScode6 dans ce cas, et le hook de session signalait deja
    # « categorie(s) hors vocabulaire, sans effet ». Meme raison que le
    # rattrapage du volet 2 : un garde-fou qui hurle a tort finit ignore.
    "robustesse", "performance", "risque_technique", "securite",
    # Orthographe heritee, presente dans arbitrages.json : la refuser ferait
    # crier le controle sur des entrees reelles deja ecrites.
    "risque-technique",
    "autre",
)


# --- PREUVE D'APPLICATION (arbitrage utilisateur du 2026-09-19) ---------------
# Cause racine n.2 du superviseur : l'AUTO-ATTESTATION. Mesure du jour — sur 250
# arbitrages, 195 portaient « ACCEPTE + APPLIQUE », 8 etaient des refus reels, 0
# disait « a verifier ». Un correctif applique A LA COPIE, a une SEULE occurrence,
# ou couvert par une garde verte par construction, produit EXACTEMENT la meme
# trace qu'un correctif reel : la recidive n'est pas « non corrigee », elle est
# INDISCERNABLE d'une correction. Le champ `preuve_application` reclame donc la
# commande executee et son resultat, pas une affirmation.
#
# NON RETROACTIF, par le meme mecanisme que la garde QA de log_run.py (97b6c67,
# `if au_solde and CHAMP_LIVRABLE not in run: return None`) : le controle vit au
# point d'ECRITURE et nulle part ailleurs. Les 258 arbitrages deja journalises ne
# sont jamais reevalues — aucun ne devient invalide. La date de reference est
# celle de l'amnistie de l'heritage, pas une troisieme inventee.
PREUVE_BASCULE = "2026-09-19"

# Decisions qui affirment une APPLICATION. Deux familles seulement (ACCEPTE/ADOPTE
# + APPLIQUE) : c'est ce que le journal reel contient. Un « ACCEPTE : a faire au
# prochain increment » n'affirme rien et n'a rien a prouver.
def _exige_preuve(decision: str) -> bool:
    d = (decision or "").upper()
    return ("ACCEPT" in d or "ADOPT" in d) and "APPLIQU" in d


# Marqueurs de MESURE. Liste deliberement COURTE et permissive — meme prudence
# explicite que `SIGNAUX_ECHEC_PRODUIT` dans log_run.py : une exigence large
# ferait refuser des preuves honnetes, et le refus pousserait a habiller le texte
# pour passer la garde, c'est-a-dire a mentir. Un seul chiffre suffit : la preuve
# type demandee par le superviseur est « un chiffre par cible » (grep -c du
# symbole sur chaque depot), et tout resultat de commande en porte un.
MARQUEURS_DE_MESURE = ("grep", "pytest", "git ", "py ", "npm ", "--check",
                       "sha", "commit")


def _preuve_mesuree(preuve: str) -> bool:
    """Vrai si la preuve porte une trace d'execution : un chiffre (un compte, un
    sha, une date, un nombre de tests) ou le nom d'une commande."""
    t = (preuve or "").lower()
    return any(c.isdigit() for c in t) or any(m in t for m in MARQUEURS_DE_MESURE)


def _scan_script() -> str:
    """Le scanner de CE depot — meme choix a l'execution que refuser_arbitrage.py :
    `scripts/scan_projets.py` n'existe qu'au hub, le kit part chez les cibles."""
    hub = os.path.join(ROOT, "scripts", "scan_projets.py")
    return hub if os.path.isfile(hub) else os.path.join(
        ROOT, ".claude", "supervision", "scan_transcripts.py")


SCAN_SCRIPT = _scan_script()


def _args(argv):
    """Parseur minimal --cle valeur (pas d'argparse : le script est aussi appele
    par serve_wiki.py avec des valeurs libres, et argparse avale un argument qui
    commence par un tiret comme une option)."""
    out, i = {}, 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            cle = a[2:]
            out[cle] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        else:
            i += 1
    return out


def _decision_contradictoire(decision: str) -> bool:
    """Le bug exact du 2026-09-17 : une acceptation ecrite avec le script de refus.
    Une decision qui commence par REFUSE et contient ACCEPT n'est pas une decision,
    c'est un outil manquant qui a laisse une trace."""
    d = (decision or "").strip().upper()
    return d.startswith("REFUS") and "ACCEPT" in d


def _titres_du_diagnostic(cible: str):
    """Titres de constats connus pour cette cible — lecture tolerante : le
    diagnostic peut etre absent (une cible du kit n'en a pas), illisible, ou
    reecrit entre la lecture du wiki et le clic."""
    try:
        with open(DIAGNOSTIC_PATH, encoding="utf-8") as fh:
            diag = json.load(fh)
    except (OSError, ValueError):
        return None
    findings = diag.get("findings") or diag.get("constats") or []
    if not isinstance(findings, list):
        return None
    return [str(f.get("titre") or "") for f in findings
            if isinstance(f, dict) and f.get("cible") == cible]


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    a = _args(argv)
    usage = ("log_arbitrage : usage : --cible <cible> --titre \"<titre exact du "
             "constat>\" --categories <c1[,c2]> --decision \"<ACCEPTE + ...>\"")

    cible = (a.get("cible") or "").strip()
    titre = (a.get("titre") or "").strip()
    brut = (a.get("categories") or "").strip()
    decision = (a.get("decision") or "").strip()

    if not cible:
        print("log_arbitrage : REFUS — `cible` manquante.\n" + usage, file=sys.stderr)
        return 1
    if not titre:
        print("log_arbitrage : REFUS — `titre` manquant. Le titre est repris a "
              "l'identique du constat dans diagnostic.json : sans lui, l'arbitrage "
              "ferme tous les constats (cible, categorie) au lieu de celui-la.\n"
              + usage, file=sys.stderr)
        return 1
    categories = [c.strip() for c in brut.split(",") if c.strip()]
    if not categories:
        print("log_arbitrage : REFUS — `categories` manquant ou vide (liste non vide "
              "exigee). Un arbitrage sans categories a masque a perpetuite tout "
              "constat futur de sa cible : 137 entrees sur 258 au 2026-09-19.\n"
              + usage, file=sys.stderr)
        return 1
    inconnues = [c for c in categories if c not in CATEGORIES_CONNUES]
    if inconnues:
        print(f"log_arbitrage : REFUS — categorie(s) hors vocabulaire : {inconnues}. "
              f"Connues : {list(CATEGORIES_CONNUES)}. Une faute de frappe donnerait "
              "un arbitrage qui ne ferme rien, sans un mot.", file=sys.stderr)
        return 1
    if not decision:
        print("log_arbitrage : REFUS — `decision` manquante.\n" + usage,
              file=sys.stderr)
        return 1
    preuve = (a.get("preuve") or a.get("preuve_application") or "").strip()
    if _exige_preuve(decision) and not preuve:
        print("log_arbitrage : REFUS — cette decision affirme une APPLICATION mais "
              "ne porte aucune `preuve_application`. Sur 250 arbitrages, 195 "
              "disaient « ACCEPTE + APPLIQUE » et 0 disait « a verifier » : un "
              "correctif applique a la copie, a une seule occurrence, ou couvert "
              "par une garde verte, produit exactement cette trace-la. Relancer "
              "avec --preuve \"<la commande executee ET son resultat>\" — un "
              "chiffre PAR cible, jamais un compte de cibles (ex. : grep -c "
              "<symbole> sur chaque depot : hub 4, VSCode 4, VSCode1 0, ...).",
              file=sys.stderr)
        return 1
    if preuve and not _preuve_mesuree(preuve):
        print("log_arbitrage : REFUS — `preuve_application` ne porte aucune trace "
              "d'execution (ni chiffre, ni nom de commande) : c'est une "
              "auto-attestation de plus. Ecrire la commande lancee et ce qu'elle a "
              "rendu. La garde reste volontairement permissive — un seul chiffre "
              "suffit — pour ne pas pousser a habiller le texte.", file=sys.stderr)
        return 1
    if _decision_contradictoire(decision):
        print("log_arbitrage : REFUS — cette decision commence par REFUSE et contient "
              "ACCEPT. C'est le bug de 2026-09-17 (4 entrees ecrites ainsi, faute "
              "d'ecrivain d'acceptation) : un refus et une acceptation sont deux "
              "decisions opposees, pas une seule chaine. Ecrire soit une acceptation "
              "ici, soit un refus avec refuser_arbitrage.py.", file=sys.stderr)
        return 1

    # « Corrompu » n'est PAS « absent » (leçon refuser_arbitrage : la confusion a
    # failli remplacer 94 arbitrages par un fichier a une entree).
    try:
        with open(ARBITRAGES_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {"arbitrages": []}
    except (OSError, ValueError) as exc:
        print(f"log_arbitrage : ABANDON — {ARBITRAGES_PATH} est illisible ({exc}). "
              "Rien n'a ete ecrit : ce fichier est la memoire d'arbitrage du projet "
              "(regle R4). Restaurer la derniere version saine avant de relancer.",
              file=sys.stderr)
        return 2
    if not isinstance(data, dict) or not isinstance(data.get("arbitrages", []), list):
        print(f"log_arbitrage : ABANDON — {ARBITRAGES_PATH} a une structure "
              "inattendue ({\"arbitrages\": [...]} attendu). Rien n'a ete ecrit.",
              file=sys.stderr)
        return 2
    data.setdefault("arbitrages", [])

    # Titre confronte au diagnostic : AVERTISSEMENT, pas refus. On arbitre aussi des
    # cibles hors diagnostic (veille:*, gouvernance) et le diagnostic est reecrit en
    # continu ; un refus dur rendrait le script inutilisable la moitie du temps et
    # renverrait l'utilisateur a l'edition manuelle — le mal qu'on soigne.
    connus = _titres_du_diagnostic(cible)
    if connus is not None and titre not in connus:
        print(f"log_arbitrage : AVERTISSEMENT — aucun constat de « {cible} » ne porte "
              f"ce titre dans le diagnostic. Titres connus : {connus}. Si c'est une "
              "faute de recopie, l'arbitrage sera inoperant (il ne fermera pas le "
              "constat vise). L'entree est ecrite quand meme.")

    date = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    entree = {
        "cible": cible,
        "date": date,
        "titre": titre,
        "categories": categories,
        "decision": decision,
    }
    if preuve:
        entree["preuve_application"] = preuve
    data["arbitrages"].append(entree)
    tmp = ARBITRAGES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, ARBITRAGES_PATH)
    print(f"log_arbitrage : « {cible} » / « {titre} » arbitre ({date}) — "
          f"categories {categories} — {decision}")

    if os.environ.get("AGENT_SUPERVISION_SKIP_SCAN"):
        return 0   # tests : la regeneration du wiki n'est pas leur objet
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", SCAN_SCRIPT, "--no-refresh"],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        print(r.stdout.strip())
        if r.returncode != 0:
            print(r.stderr.strip(), file=sys.stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"log_arbitrage : wiki non regenere ({exc}) — relancer le scan "
              f"({SCAN_SCRIPT})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
