# Interview-to-Deck

Outil de saisie d'interviews sur une trame, de synthèse transverse, et de
génération de PPT de restitution sur la base d'un template corporate.

Statut : **incrément 1 — Socle + Mission & Trame** (MVP manuel).
Voir la roadmap : [.roadmap/roadmap.json](.roadmap/roadmap.json) (rendu SVG via le skill `roadmap-keeper`).

## Stack

- **FastAPI** + **Jinja2** (server-rendered, HTMX prêt côté front)
- **SQLAlchemy 2.0** + **SQLite** (fichier `data/app.db`, créé au démarrage)
- Python 3.12+

## Périmètre de l'incrément 1

- Créer / lister / consulter / supprimer une **mission** (US0.2)
- Modèle de données `Mission → Trame → Theme → Question` (US0.1)
- Éditer la **trame** : ajouter des thèmes et des questions (US1.1)
- **Typer** les questions : ouverte / échelle / choix (US1.2)

Les entités Interview / Synthèse / Deck arrivent aux incréments suivants ;
le modèle est déjà pensé pour les accueillir sans refonte.

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
  models.py        # Mission / Trame / Theme / Question (SQLAlchemy 2.0)
  templating.py    # instance Jinja2Templates partagée
  routers/
    missions.py    # CRUD mission
    trames.py      # édition de la trame (thèmes / questions / types)
  templates/       # base.html + vues
  static/app.css
data/app.db        # base SQLite (gitignored)
```
