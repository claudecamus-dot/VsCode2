---
updated: 2026-07-19
confidence: confirmed
agents: [onboarder, claude]
---

# Conventions — Interview-to-Deck

## Linting & formatage

- Aucun outil de linting/formatage configuré (pas de ruff, black, ESLint, prettier)
  — `CONFIRMÉ` · onboarder · 2026-06-30
- Pas de pre-commit hooks
  — `CONFIRMÉ` · onboarder · 2026-06-30

## Nommage

- Fichiers Python : `snake_case.py`
  — `DÉDUIT` · onboarder · 2026-06-30 · `app/`
- Fonctions/variables : `snake_case`
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/*.py`
- Classes : `PascalCase`
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/models.py`
- Routers : `from ..imports` avec `from __future__ import annotations`
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/routers/*.py`
- Templates HTML : `snake_case.html`
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/templates/`
- CSS : classes BEM-lite (`.card-head`, `.theme-block`, `.btn-primary`)
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/static/app.css`

## Git

- Messages de commit en français, impératif, une ligne de titre (< 70 car.) résumant le *pourquoi* plutôt que la liste des fichiers touchés (ex. « Corrige 7 frictions UX remontées par la revue de structure », « Exécute le tri BMAD ré-arbitré ») ; corps optionnel en prose pour le détail. Toujours un nouveau commit, jamais d'amend sur un commit déjà là (garde-fou `guard_destructive_git.py`).
  — `CONFIRMÉ` · claude · 2026-07-19 · `git log`
- `.gitignore` exclut : `.venv/`, `__pycache__/`, `*.pyc`, `data/` (y compris `data/app.db`, la base dev réelle — **ne jamais la supprimer/recréer** en dehors de `tests/conftest.py`, qui isole `APP_DB_PATH`), `.roadmap/*.svg`, `.env`, `.claude/supervision/*.json(l)`, `.claude/orchestration/runs.jsonl`/`routing-hints.json`
  — `CONFIRMÉ` · claude · 2026-07-19 · `.gitignore`

## Configuration & secrets

- `.env.example` documente les variables requises ; `.env` réel est gitignoré
  — `CONFIRMÉ` · onboarder · 2026-06-30
- Clé API du fournisseur IA actif (`OPENAI_API_KEY`/`MISTRAL_API_KEY` — sans objet pour `ollama`, le défaut) présente en commentaire dans `.env.example` — ne pas committer dé-commentée
  — `CONFIRMÉ` · claude · 2026-07-17 · `.env.example`

## Patterns spécifiques à l'équipe

- `from __future__ import annotations` systématique en tête de fichier
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/*.py`
- Docstrings en français (triple quotes)
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/*.py`
- Fonctions helpers privées préfixées par `_`
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/*.py`
