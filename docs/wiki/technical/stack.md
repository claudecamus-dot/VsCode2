---
updated: 2026-06-30
confidence: confirmed
agents: [onboarder]
---

# Stack — Interview-to-Deck

## Dépendances principales

| Catégorie | Technologie | Version | Notes |
|-----------|-------------|---------|-------|
| Langage | Python | 3.12+ | `.venv` attendu à la racine |
| Framework Web | FastAPI | ≥0.115 | SSR, pas de séparation API/SPA |
| Templating | Jinja2 | ≥3.1 | Templates par domaine dans `app/templates/` |
| ORM | SQLAlchemy | ≥2.0 | Déclarative, sessions injectées via FastAPI |
| Base de données | SQLite | — | Fichier `data/app.db` (gitignoré) |
| Frontend dynamique | HTMX | 2.0.3 | Chargé depuis unpkg CDN, pas de SRI |
| Import .docx | python-docx | ≥1.1 | Analyse des niveaux de liste Word |
| IA | Anthropic SDK | ≥0.69 | Importé en lazy, mode démo disponible |
| Environnement | python-dotenv | ≥1.0 | Chargement du `.env` au démarrage |

— `CONFIRMÉ` · onboarder · 2026-06-30 · `requirements.txt`

## Librairies clés

- `uvicorn[standard]` — Serveur ASGI, ≥0.30
- `python-multipart` — Support multipart/form-data pour les uploads, ≥0.0.9

## Variables d'environnement requises

- `SYNTHESE_DEMO` — Mode démo hors-ligne (1 = activé, 0 = désactivé)
- `ANTHROPIC_API_KEY` — Clé API Anthropic (optionnelle si mode démo)
- `SYNTHESE_MODEL` — Modèle Claude (défaut : `claude-opus-4-8`, optionnel)

— `CONFIRMÉ` · onboarder · 2026-06-30 · `.env.example`

## Contraintes de version

- Aucune version figée dans `requirements.txt` (>=) — risque de régression silencieuse
- Pas de `pyproject.toml` — la gestion de projet n'est pas centralisée
- Pas de `.python-version` — incohérence de runtime possible
