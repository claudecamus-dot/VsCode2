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

Appels synchrones en Python — pas de queue, pas d'événements. L'appel au fournisseur IA actif (Ollama par défaut depuis le 2026-07-17, ou OpenAI/Mistral) est synchrone (bloquant).
— `DÉDUIT` · claude · 2026-07-17

## Décisions architecturales notables

- **Import non destructif** : fusion par titre de thème, questions ajoutées sans écrasement (préserve les réponses existantes)
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/routers/trames.py:97`
- **Synthèse IA avec fallback** : fournisseur actif (Ollama par défaut, ou OpenAI/Mistral) avec sortie JSON structurée, mode démo heuristique si clé absente
  — `CONFIRMÉ` · claude · 2026-07-17 · `app/services/synthese_ai.py`
- **Entretien libre vs synthèse de mission, décorrélés (2026-07-17)** : l'écran de synthèse d'un entretien libre (`libre_synthese.html`) affiche l'apport d'**un seul** entretien aux 5 catégories transverses ; la synthèse globale de mission (`synthese/globale.html`) agrège tous les entretiens (structurés + libres). Les deux écrans montraient les mêmes 5 libellés sans distinction visuelle forte — corrigé par un bandeau explicite + traitement visuel distinct sur le premier.
  — `CONFIRMÉ` · claude · 2026-07-17 · `app/templates/interviews/libre_synthese.html`, `app/templates/synthese/globale.html`
- **Migrations additives à chaud** : les nouvelles colonnes sont ajoutées via `ALTER TABLE` au démarrage (pas de migration versionnée)
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/db.py:33`
- **HTMX pour l'interactivité** : autosave par champ, navigation entre thèmes, pas de JS framework
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/templates/`

## Points de fragilité connus

- Absence d'authentification — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/main.py`
- Pas de CI/CD — `CONFIRMÉ` · onboarder · 2026-06-30
- `generate_global_synthesis()` (synthèse de mission) n'a pas de découpage map-reduce, contrairement à l'extraction d'entretien libre — risque de troncature silencieuse du prompt par Ollama sur une mission avec beaucoup de matière (voir `index.md`)
  — `DÉDUIT` · claude · 2026-07-17 · `app/services/synthese_ai.py:206-252`
