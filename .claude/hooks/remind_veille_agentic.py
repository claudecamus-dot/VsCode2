"""Hook SessionStart — rappelle de lancer la veille agentic si elle date de plus de 3 jours.

Lit .claude/veille/veille.json (champ derniere_veille). Sortie sur stdout = contexte
injecté en début de session. Silencieux si la veille est fraîche.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

CADENCE_JOURS = 3
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VEILLE_PATH = os.path.join(ROOT, ".claude", "veille", "veille.json")


def main():
    derniere = None
    try:
        with open(VEILLE_PATH, encoding="utf-8") as fh:
            raw = json.load(fh).get("derniere_veille")
        if raw:
            derniere = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if derniere.tzinfo:
                derniere = derniere.astimezone().replace(tzinfo=None)
    except (OSError, ValueError):
        pass

    if derniere is None:
        print(
            "[veille-agentic] Aucune veille enregistree — lancer la skill "
            "veille-agentic (cadence 3 jours, sortie: .claude/veille/veille.json "
            "+ section 2 du wiki)."
        )
        return 0

    age = (dt.datetime.now() - derniere).days
    if age >= CADENCE_JOURS:
        print(
            f"[veille-agentic] Veille agentic a lancer ou perimee — derniere veille "
            f"il y a {age} jour(s) (cadence {CADENCE_JOURS} j). Proposer de lancer "
            "la skill veille-agentic (sans interrompre une tache en cours)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
