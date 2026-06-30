---
updated: 2026-06-30
confidence: confirmed
agents: [onboarder]
---

# Interview-to-Deck — Index Wiki

## Stack critique

Python 3.12+ · FastAPI 0.115+ · SQLAlchemy 2.0 / SQLite · Jinja2 + HTMX 2.0 · Anthropic Claude Opus 4.8
— `CONFIRMÉ` · onboarder · 2026-06-30 · `requirements.txt`

## Architecture (résumé)

Monolithe Python SSR. FastAPI route → Service → Template Jinja2 + HTMX.
Modèle `Mission` → `Trame` → `Theme` → `Question` ; entités `Interview`/`Answer`/`Verbatim`/`Synthesis`/`AgentResult`.
Import .docx non-destructif, synthèse IA ou mode démo, intégration OpenHub pour invoquer des agents.
— `CONFIRMÉ` · onboarder · 2026-06-30

## God nodes — concepts les plus connectés

| Concept | Pages liées | Criticité |
|---------|-------------|-----------|
| Mission | [technical/architecture.md](technical/architecture.md), [business/index.md](business/index.md) | Critique |
| Synthèse IA | [technical/architecture.md](technical/architecture.md), [technical/stack.md](technical/stack.md) | Haute |

## Carte des domaines métier

- [Entretien & Synthèse](business/index.md) — Capture d'interviews sur trame, synthèse transverse, génération PPT

## Points critiques actifs 🔴

- Secret Anthropic dans `.env:9` : **commentée et `.env` gitignoré** (`.gitignore:7`) → exposition improbable ; vigilance seulement si dé-commentée en local
- Aucune authentification — accessible sans restriction
- Pas de CI/CD ni de pipeline de déploiement

## Zones d'ombre

- Déploiement : aucun runbook ni CI/CD détecté
- Tests : pas de seuil de couverture configuré
- Conventions de code : pas de linting/formatting imposé
- Environnements : dev local seulement (pas de staging/prod)
