"""Couche d'accès SQLite : engine, session, initialisation du schéma."""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# Chemin de la base, surchargeable via APP_DB_PATH (utilisé par les tests pour
# pointer vers une base jetable et ne jamais toucher la base de dev/prod).
_env_db_path = os.environ.get("APP_DB_PATH")
if _env_db_path:
    DB_PATH = Path(_env_db_path)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_DIR = DB_PATH.parent
else:
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"
    DATA_DIR.mkdir(exist_ok=True)
    DB_PATH = DATA_DIR / "app.db"

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


@event.listens_for(engine, "connect")
def _enable_sqlite_fk(dbapi_connection, _connection_record) -> None:
    """SQLite n'applique pas les clés étrangères sans ce PRAGMA."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False
)


def _add_missing_columns() -> None:
    """Migrations additives légères (SQLite) : ajoute les colonnes absentes.

    `create_all` crée les tables manquantes mais n'altère pas une table
    existante — on ajoute donc à la main les colonnes introduites après coup.
    """
    additions = {
        "interviews": {"reference_text": "TEXT"},
        "trames": {"intro_text": "TEXT"},
        "questions": {"help_text": "TEXT"},
    }
    with engine.begin() as conn:
        for table, cols in additions.items():
            existing = {
                row[1]
                for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            for name, ddl in cols.items():
                if name not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                    )


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()


def get_session() -> Iterator[Session]:
    """Dépendance FastAPI : ouvre une session par requête."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
