# Interview-to-Deck

Outil de saisie d'interviews sur une trame, de synthèse transverse, et de
génération de PPT de restitution sur la base d'un template corporate.

Statut : **socle incréments 1 à 3** — Mission & Trame, capture d'interview, synthèse IA transverse et intégration OpenHub (agents).
Voir la roadmap : [.roadmap/roadmap.json](.roadmap/roadmap.json) (rendu SVG via le skill `roadmap-keeper`).

## Stack

- **FastAPI** + **Jinja2** (server-rendered, HTMX prêt côté front)
- **SQLAlchemy 2.0** + **SQLite** (fichier `data/app.db`, créé au démarrage)
- Python 3.12+

## Périmètre couvert

- Créer / lister / consulter / supprimer une **mission** (US0.2)
- Modèle de données `Mission → Trame → Theme → Question` (US0.1)
- Éditer la **trame** : ajouter des thèmes et des questions, import `.docx` non-destructif (US1.1)
- **Typer** les questions : ouverte / échelle / choix (US1.2)
- **Capturer** un entretien : réponses et verbatims par question (`Interview` / `Answer` / `Verbatim`)
- **Synthétiser** transversalement les réponses par thème, en mode IA (Claude) ou démo (`Synthesis`)
- **Invoquer des agents OpenHub** (skills dynamiques) depuis l'écran mission (`AgentResult`)

La génération du **deck PPT** final reste à venir ; le modèle est déjà pensé pour
l'accueillir sans refonte.

## Démarrage

```bash
# 1. Environnement virtuel
python -m venv .venv
.venv\Scripts\activate            # Windows (PowerShell : .venv\Scripts\Activate.ps1)

# 2. Dépendances
pip install -r requirements.txt

# 3. Lancer
uvicorn app.main:app --reload
```

Puis ouvrir http://127.0.0.1:8000 (redirige vers `/missions`).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Les tests tournent sur une base SQLite jetable (variable `APP_DB_PATH`, fixée
par `tests/conftest.py`) — la base de dev `data/app.db` n'est jamais touchée.

## Structure

```
app/
  main.py          # app FastAPI (lifespan -> init DB), montage static
  db.py            # engine SQLite, session, PRAGMA foreign_keys
  models.py        # Mission / Trame / Theme / Question / Interview / Answer / Verbatim / Synthesis / AgentResult
  templating.py    # instance Jinja2Templates partagée
  routers/
    missions.py    # CRUD mission
    trames.py      # édition de la trame (thèmes / questions / types), import .docx
    interviews.py  # capture d'entretien, sauvegarde réponses/verbatims
    synthese.py    # synthèse IA transverse par thème
    agents.py      # intégration OpenHub — liste et invocation de skills
  services/
    synthese_ai.py      # appel Claude / mode démo pour la synthèse
    openhub_agents.py   # client OpenHub (skills dynamiques)
  importers/
    docx_trame.py  # import non-destructif d'une trame .docx
  templates/       # base.html + vues
  static/app.css
data/app.db        # base SQLite (gitignored)
```
