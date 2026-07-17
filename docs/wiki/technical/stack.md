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
| IA | Ollama (local, sans SDK) | — | Fournisseur par défaut depuis le 2026-07-17 (urllib vers un serveur local, aucune clé) ; OpenAI/Mistral SDK restent en fournisseurs alternatifs. SDK Anthropic retiré. |
| Transcription audio | faster-whisper | ≥1.0 | Locale, modèle `medium` par défaut |
| Export PDF | reportlab | ≥4.0 | Par entretien, pure Python (pas de dépendance système) |
| Environnement | python-dotenv | ≥1.0 | Chargement du `.env` au démarrage |

— `CONFIRMÉ` · claude · 2026-07-17 · `requirements.txt`

## Librairies clés

- `uvicorn[standard]` — Serveur ASGI, ≥0.30
- `python-multipart` — Support multipart/form-data pour les uploads, ≥0.0.9

## Variables d'environnement requises

- `SYNTHESE_DEMO` — Mode démo hors-ligne (1 = activé, 0 = désactivé)
- `AI_PROVIDER` — Fournisseur IA actif : `ollama` (défaut, local), `openai` ou `mistral`
- `OPENAI_API_KEY` / `MISTRAL_API_KEY` — Clé du fournisseur choisi (sans objet pour `ollama`)
- `OLLAMA_HOST` — Serveur Ollama (défaut : `http://localhost:11434`, optionnel)
- `SYNTHESE_MODEL` — Modèle utilisé (défaut par fournisseur : `llama3.1` pour ollama, optionnel)

— `CONFIRMÉ` · claude · 2026-07-17 · `.env.example`

## Contraintes de version

- Aucune version figée dans `requirements.txt` (>=) — risque de régression silencieuse
- Pas de `pyproject.toml` — la gestion de projet n'est pas centralisée
- Pas de `.python-version` — incohérence de runtime possible
