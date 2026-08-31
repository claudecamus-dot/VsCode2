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
SCAN_SCRIPT = os.path.join(ROOT, "scripts", "scan_projets.py")


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

    try:
        with open(ARBITRAGES_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {"arbitrages": []}
    data.setdefault("arbitrages", [])

    date = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    data["arbitrages"].append({
        "cible": cible,
        "date": date,
        "decision": f"REFUSÉ : {raison}",
    })
    with open(ARBITRAGES_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"refuser_arbitrage : « {cible} » marqué REFUSÉ ({date}) — {raison}")

    if os.environ.get("AGENT_SUPERVISION_SKIP_SCAN"):
        return 0   # tests : la régénération du wiki n'est pas leur objet
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", SCAN_SCRIPT, "--no-refresh"],
                           cwd=ROOT, capture_output=True, text=True, timeout=60)
        print(r.stdout.strip())
        if r.returncode != 0:
            print(r.stderr.strip(), file=sys.stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"refuser_arbitrage : wiki non régénéré ({exc}) — relancer scripts/scan_projets.py", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
