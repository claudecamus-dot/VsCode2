---
updated: 2026-06-30
confidence: confirmed
agents: [onboarder]
---

# Architecture — Interview-to-Deck

## Structure globale

Monolithe Python backend avec rendu serveur. Pas de séparation API/SPA — FastAPI produit directement du HTML via Jinja2.
— `CONFIRMÉ` · onboarder · 2026-06-30

## Découpage en couches

Router FastAPI (validation HTTP) → Service (logique métier) → Template Jinja2 (rendu). Les sessions SQLAlchemy sont injectées par `Depends(get_session)`.
— `CONFIRMÉ` · onboarder · 2026-06-30 · `app/routers/`, `app/services/`

## Modèle de données

Hiérarchie `Mission` → `Trame` → `Theme` → `Question`. Entités satellites : `Interview` → `Answer` + `Verbatim`, `Synthesis` (par thème), `AgentResult`.
— `CONFIRMÉ` · onboarder · 2026-06-30 · `app/models.py`

## Communication entre modules

Appels synchrones en Python — pas de queue, pas d'événements. L'appel à l'API Anthropic est synchrone (bloquant).
— `DÉDUIT` · onboarder · 2026-06-30

## Décisions architecturales notables

- **Import non destructif** : fusion par titre de thème, questions ajoutées sans écrasement (préserve les réponses existantes)
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/routers/trames.py:97`
- **Synthèse IA avec fallback** : SDK Anthropic avec sortie JSON structurée, mode démo heuristique si clé absente
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/services/synthese_ai.py`
- **Migrations additives à chaud** : les nouvelles colonnes sont ajoutées via `ALTER TABLE` au démarrage (pas de migration versionnée)
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/db.py:33`
- **HTMX pour l'interactivité** : autosave par champ, navigation entre thèmes, pas de JS framework
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/templates/`

## Points de fragilité connus

- Secret Anthropic dans `.env:9` : commentée et `.env` gitignoré (`.gitignore:7`), exposition improbable — `CONFIRMÉ` · onboarder · 2026-06-30
- Absence d'authentification — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/main.py`
- Pas de CI/CD — `CONFIRMÉ` · onboarder · 2026-06-30
