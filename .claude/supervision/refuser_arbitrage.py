"""Enregistre un REFUS d'arbitrage — le pendant déterministe (0 token) du bouton
« Invalider » de l'onglet Actions correctives du wiki.

Une proposition présentée par une action corrective (claude -p, coûteux) peut être
refusée sans relancer de LLM : refuser est un fait (une décision humaine), pas une
tâche qui a besoin de raisonnement. Ce script écrit l'entrée dans arbitrages.json
(jamais écrasé — append) puis régénère le wiki pour que le refus apparaisse aussitôt
et que la proposition cesse d'être reproposée (même contrat que `finding_arbitre`).

SYMETRIE AVEC L'ACCEPTATION (2026-09-19). Ce script n'ecrivait que
`cible`/`date`/`decision` — jamais `titre` ni `categories`. Or `_couvre()` traite
`categories is None` comme « ferme TOUT » : 137 arbitrages sur 258 masquaient ainsi
a perpetuite tout constat futur de leur cible. L'amnistie de l'heritage ne couvre
que les entrees ANTERIEURES au 2026-09-19 : un refus ecrit desormais sans
categories serait « sans categories ET posterieur a la bascule », donc il fermerait
tout — on aurait durci l'acceptation (`log_arbitrage.py`) en laissant le refus dans
l'etat qui causait le probleme. `categories` est donc EXIGE ici aussi.

Echappatoire deterministe (0 token) pour ne pas tuer le bouton « Invalider » du
wiki, qui n'a parfois que la CIBLE sous la main : a defaut de `--categories`, le
script les DERIVE des constats de cette cible dans `diagnostic.json` (union de
leurs categories, titres repris). Si la derivation ne donne rien, il REFUSE en
nommant `--categories` plutot que d'ecrire une entree aveuglante.

Usage : py .claude/supervision/refuser_arbitrage.py "<cible>" ["<raison>"]
            [--categories <c1[,c2]>] [--titre "<titre du constat>"]

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

# Duplique depuis scan_transcripts.CATEGORIES_CONNUES, pour la meme raison que dans
# log_arbitrage.py : ce fichier part dans le kit chez des cibles qui n'ont pas le
# scanner du hub, et un import optionnel qui echoue en silence rendrait la garde
# inoperante justement la ou personne ne regarde. Un test verrouille l'egalite.
CATEGORIES_CONNUES = (
    "ko-repete", "inefficacite", "agent-mort", "interaction",
    "verification-manquante", "non-convergence",
    "pratique-test", "pratique-dev", "pratique-revue", "pratique-design",
    "pratique-doc", "pratique-produit",
    "pratique-securite",  # miroir de scan_transcripts.py (2026-09-21)
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


def _constats_de(cible: str):
    """Constats de `diagnostic.json` portant cette cible — lecture tolerante : le
    diagnostic peut etre absent (cible du kit), illisible, ou reecrit entre la
    lecture du wiki et le clic."""
    try:
        with open(DIAGNOSTIC_PATH, encoding="utf-8") as fh:
            diag = json.load(fh)
    except (OSError, ValueError):
        return []
    findings = diag.get("findings") or diag.get("constats") or []
    if not isinstance(findings, list):
        return []
    return [f for f in findings if isinstance(f, dict) and f.get("cible") == cible]


def _args(argv):
    """Parseur minimal : positionnels `<cible> [raison]` (contrat historique, garde
    pour tous les appelants existants) + options `--cle valeur`."""
    positionnels, options, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            options[a[2:]] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        else:
            positionnels.append(a)
            i += 1
    return positionnels, options


def _scan_script() -> str:
    """Le scanner de CE dépôt — le hub et une cible n'ont pas le même.

    `scripts/scan_projets.py` génère le wiki de supervision : il n'existe QUE dans le
    hub. Ce fichier, lui, est publié dans le kit et part chez les 5 cibles, où le
    chemin était donc introuvable — la régénération échouait en `FileNotFoundError`
    avalée, et le message de secours nommait une commande que le lecteur n'a pas.
    Signalé par la session VSCode3 le 2026-09-01, qui l'avait corrigé chez elle en
    codant en dur SON scanner : juste là-bas, faux au hub. On choisit donc à
    l'exécution plutôt que de figer l'un ou l'autre.
    """
    hub = os.path.join(ROOT, "scripts", "scan_projets.py")
    return hub if os.path.isfile(hub) else os.path.join(
        ROOT, ".claude", "supervision", "scan_transcripts.py")


SCAN_SCRIPT = _scan_script()


USAGE = ('refuser_arbitrage : usage : <cible> ["raison"] '
         '[--categories <c1[,c2]>] [--titre "<titre du constat>"]')


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    positionnels, options = _args(argv)
    if not positionnels:
        print(USAGE)
        return 1
    cible = positionnels[0].strip()
    if not cible:
        print("refuser_arbitrage : cible vide")
        return 1
    raison = (positionnels[1].strip() if len(positionnels) > 1 and positionnels[1].strip()
              else (options.get("raison") or "").strip()
              or "refusé via le bouton du wiki, sans raison précisée")

    # --- `categories` EXIGE, derive a defaut -------------------------------------
    brut = (options.get("categories") or "").strip()
    titre = (options.get("titre") or "").strip()
    constats = _constats_de(cible)
    if brut:
        categories = [c.strip() for c in brut.split(",") if c.strip()]
        explicite = True
    else:
        categories = sorted({str(f.get("categorie") or "").strip()
                             for f in constats
                             if str(f.get("categorie") or "").strip()})
        explicite = False
    if not categories:
        print("refuser_arbitrage : REFUS — `categories` manquant et INDERIVABLE : "
              f"aucun constat de « {cible} » dans {DIAGNOSTIC_PATH}. Un refus sans "
              "categories ferme TOUT constat futur de sa cible (137 entrees sur 258 "
              "au 2026-09-19 faisaient exactement cela) et l'amnistie de l'heritage "
              "ne couvre pas les entrees ecrites aujourd'hui. Relancer avec "
              "--categories <c1[,c2]>." + chr(10) + USAGE, file=sys.stderr)
        return 1
    inconnues = [c for c in categories if c not in CATEGORIES_CONNUES]
    if inconnues:
        origine = "passée en --categories" if explicite else "lue dans le diagnostic"
        print(f"refuser_arbitrage : REFUS — categorie(s) hors vocabulaire "
              f"{inconnues} ({origine}). Connues : {list(CATEGORIES_CONNUES)}. Une "
              "faute de frappe donnerait un refus qui ne ferme rien, sans un mot.",
              file=sys.stderr)
        return 1
    if not titre:
        titres = [t for t in (str(f.get("titre") or "").strip() for f in constats) if t]
        if len(titres) == 1:
            titre = titres[0]
        elif titres:
            titre = (f"refus portant sur les {len(titres)} constats de « {cible} » : "
                     + " | ".join(titres))
        else:
            titre = f"refus sur « {cible} » (aucun titre de constat sous la main)"

    # « Corrompu » n'est PAS « absent ». Confondre les deux remplaçait 94 arbitrages
    # (~108 Ko) par un fichier à une entrée, exit 0, sans un mot — la mémoire
    # d'arbitrage du projet (règle R4) effacée par un simple fichier tronqué.
    # Seul FileNotFoundError autorise à repartir d'une liste vide.
    try:
        with open(ARBITRAGES_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {"arbitrages": []}
    except (OSError, ValueError) as exc:
        print(f"refuser_arbitrage : ABANDON — {ARBITRAGES_PATH} est illisible ({exc}). "
              "Rien n'a été écrit : ce fichier est la mémoire d'arbitrage du projet "
              "(règle R4), un contenu illisible n'est pas un fichier absent. "
              "Restaurer la dernière version saine (git checkout / sauvegarde) "
              "avant de relancer.", file=sys.stderr)
        return 2
    if not isinstance(data, dict) or not isinstance(data.get("arbitrages", []), list):
        print(f"refuser_arbitrage : ABANDON — {ARBITRAGES_PATH} est illisible "
              "(structure inattendue : un objet {\"arbitrages\": [...]} est attendu). "
              "Rien n'a été écrit.", file=sys.stderr)
        return 2
    data.setdefault("arbitrages", [])

    date = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    data["arbitrages"].append({
        "cible": cible,
        "date": date,
        "titre": titre,
        "categories": categories,
        "decision": f"REFUSÉ : {raison}",
    })
    # Écriture atomique (même motif que canon/log_run.solder) : un "w" direct sur les
    # ~108 Ko du fichier le tronque à mi-parcours si l'écriture est interrompue
    # (Ctrl-C, coupure, disque plein). Le temporaire vit dans le même répertoire pour
    # que os.replace reste atomique (même volume, Windows comme POSIX).
    tmp = ARBITRAGES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, ARBITRAGES_PATH)
    print(f"refuser_arbitrage : « {cible} » marqué REFUSÉ ({date}) — categories "
          f"{categories}"
          + ("" if explicite else " (dérivées du diagnostic)") + f" — {raison}")

    if os.environ.get("AGENT_SUPERVISION_SKIP_SCAN"):
        return 0   # tests : la régénération du wiki n'est pas leur objet
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", SCAN_SCRIPT, "--no-refresh"],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        print(r.stdout.strip())
        if r.returncode != 0:
            print(r.stderr.strip(), file=sys.stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"refuser_arbitrage : wiki non regenere ({exc}) — relancer le scan "
              f"({SCAN_SCRIPT})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
