"""Enregistre un REFUS d'arbitrage — le pendant déterministe (0 token) du bouton
« Invalider » de l'onglet Actions correctives du wiki.

Une proposition présentée par une action corrective (claude -p, coûteux) peut être
refusée sans relancer de LLM : refuser est un fait (une décision humaine), pas une
tâche qui a besoin de raisonnement. Ce script écrit l'entrée dans arbitrages.json
(jamais écrasé — append) puis régénère le wiki pour que le refus apparaisse aussitôt
et que la proposition cesse d'être reproposée (même contrat que `finding_arbitre`).

Usage : py .claude/supervision/refuser_arbitrage.py "<cible>" ["<raison>"]
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


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("refuser_arbitrage : usage : <cible> [\"raison\"]")
        return 1
    cible = argv[0].strip()
    if not cible:
        print("refuser_arbitrage : cible vide")
        return 1
    raison = argv[1].strip() if len(argv) > 1 and argv[1].strip() else \
        "refusé via le bouton du wiki, sans raison précisée"

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
    print(f"refuser_arbitrage : « {cible} » marqué REFUSÉ ({date}) — {raison}")

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
