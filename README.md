# Interview-to-Deck

Outil interne pour consultant·es menant des entretiens qualitatifs en audit /
étude. Chaîne : **capture d'entretiens** — soit structurés sur une trame
(Mission → Trame → Thème → Question), soit **libres** (sans trame, tours de
parole structurés par IA depuis la transcription) — → **synthèse transverse
IA** → **génération d'un PPT de restitution** sur la base d'un template
corporate.

> Documentation vivante : [`docs/wiki/index.md`](docs/wiki/index.md)
> ([architecture](docs/wiki/technical/architecture.md) ·
> [conventions](docs/wiki/technical/conventions.md) ·
> [stack](docs/wiki/technical/stack.md)). Suivi d'avancement :
> [`.roadmap/roadmap.json`](.roadmap/roadmap.json) (rendu via le skill
> `roadmap-keeper`). `CLAUDE.md` sert de guide d'architecture détaillé.

## Stack

- **FastAPI** + **Jinja2**, server-rendered, **HTMX 2.0** (maj partielles,
  autosave) — pas de SPA ni d'étape de build front
- **SQLAlchemy 2.0** + **SQLite** (`data/app.db`, créé au démarrage)
- **python-pptx** pour la génération de deck
- IA multi-fournisseur (`AI_PROVIDER` : ollama-local par défaut / openai /
  mistral), transcription audio locale **faster-whisper**
- Python 3.12+

## Périmètre couvert

- **Entrée unifiée** (`/`) : entretien libre / entretien structuré / nouvelle
  mission — la mission peut être nommée ou rattachée *après* l'entretien
- **Mission & Trame** : CRUD mission, édition de trame (thèmes / questions
  typées : ouverte / échelle / choix), import `.docx` non-destructif
- **Capture d'entretien** : saisie sur trame (réponses + verbatims) **ou**
  enregistrement navigateur → transcription locale → structuration IA
  (tours de parole, répartition, résumé) revue sur un seul écran
- **Écrans de lecture** d'un entretien libre : Analyse (transcription
  sectionnée) et Synthèse (résumé + 5 catégories), export Markdown par
  entretien
- **Synthèse transverse IA** : 5 catégories fixes (contexte / culture & ADN /
  forces & succès / points d'amélioration / aspirations), mélange entretiens
  structurés et libres ; recommandations par axe scorées valeur/complexité ;
  édition autosave ; export/import Markdown pour analyse externe
- **Export PowerPoint** : rendu sur template client (hérite thème/masters) ou
  deck 16:9 vierge, sélection de slides, garde-fou géométrique
  (`verifier_geometrie()`) + vérification de rendu réel
- **Agents OpenHub** (optionnel) : voir [`OPENHUB.md`](OPENHUB.md)

## Démarrage

```bash
python -m venv .venv
.venv\Scripts\activate                 # Windows (POSIX : .venv/bin/activate)
pip install -r requirements.txt -r requirements-dev.txt

uvicorn app.main:app --reload          # http://127.0.0.1:8000/
```

Configuration IA / transcription : copier `.env.example` → `.env`. Sans clé,
l'app reste utilisable (mode démo `SYNTHESE_DEMO=1`, ou fournisseur local
`AI_PROVIDER=ollama` + faster-whisper pour une chaîne 100 % locale).

## Tests

```bash
pytest -q
pytest tests/test_mission_trame_flow.py::test_export_pptx_produces_valid_file
pytest -k "pptx or export"
```

Base SQLite jetable (`APP_DB_PATH`, fixée par `tests/conftest.py`) — la base
de dev `data/app.db` n'est jamais touchée. Pas de linter/formatter imposé :
suivre le style du fichier environnant.

## Structure

```text
app/
  main.py            # app FastAPI (lifespan -> init DB), montage static
  db.py              # engine SQLite, migrations additives légères
  models.py          # Mission / Trame / Theme / Question / Interview /
                     #   InterviewTurn / Answer / Verbatim / GlobalSynthesis /
                     #   RecommendationAxis / Recommendation / AgentResult
  routers/           # entretiens, missions, trames, interviews, synthese,
                     #   export, agents
  services/          # ai_common (multi-fournisseur), synthese_ai,
                     #   *_extract_ai, audio_transcribe (whisper),
                     #   pptx_export / pptx_deck, mission_export /
                     #   interview_export / analyse_import, openhub_agents
  importers/docx_trame.py
  templates/  static/
data/app.db          # base SQLite (gitignored)
docs/wiki/           # documentation vivante
.roadmap/roadmap.json
```
