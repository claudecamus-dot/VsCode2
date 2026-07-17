"""Hook UserPromptSubmit — grille de qualification de l'orchestrateur (étage O-A).

Injecte à chaque demande de travail un rappel court (~50 tokens) : qualifier
silencieusement, orchestrer si multi-étapes/multi-agents, sinon exécution directe.
Silencieux sur les commandes slash (l'utilisateur invoque déjà explicitement une skill).
Ne bloque jamais : toute erreur est avalée, exit 0.
Conception : docs/reflexions/agent-orchestrateur.md §8a.
"""
import json
import sys

GRID = (
    "[orchestrateur] Qualifier en silence : demande de travail multi-etapes ou "
    "multi-agents, ou verifications obligatoires en jeu -> suivre la skill "
    "agent-orchestrator (plan modes+modeles, journal log_run.py). Sinon executer "
    "directement sans mentionner cette grille. Catalogue : "
    ".claude/orchestration/catalogue.md"
)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    prompt = (data.get("prompt") or "").lstrip()
    if prompt.startswith("/"):
        return 0
    print(GRID)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
