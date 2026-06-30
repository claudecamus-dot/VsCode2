"""Isolation de la base pour la suite de tests.

Doit s'exécuter **avant** tout import de `app.*` : pytest importe `conftest.py`
en premier, donc fixer `APP_DB_PATH` ici garantit que `app.db` crée son engine
sur une base jetable (et non sur `data/app.db`, la base de dev/prod).
"""
from __future__ import annotations

import os
import tempfile

# Base SQLite dédiée aux tests, dans le répertoire temporaire du système.
_TEST_DB = os.path.join(tempfile.gettempdir(), "interview_to_deck_test.db")
os.environ.setdefault("APP_DB_PATH", _TEST_DB)
