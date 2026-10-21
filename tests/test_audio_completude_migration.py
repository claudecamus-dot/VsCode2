"""Migration de la colonne `audio_file_jobs.completude` (lot « transcription
fiable », commit 2). Même montage que `test_migration_opt_in.py` : la base
jetable EST la base par défaut aux yeux de `app.db`, la vraie n'est jamais
ouverte."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

import app.db as db


@pytest.fixture()
def base_par_defaut(tmp_path: Path, monkeypatch):
    chemin = tmp_path / "app.db"
    eng = create_engine(f"sqlite:///{chemin}", echo=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "DB_PATH", chemin)
    monkeypatch.setattr(db, "_CHEMIN_PAR_DEFAUT", chemin, raising=False)
    monkeypatch.delenv("APP_DB_MIGRATE", raising=False)
    yield eng
    eng.dispose()


def _colonnes(eng, table: str) -> set[str]:
    with eng.connect() as conn:
        return {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def _ancien_schema(eng) -> None:
    db.Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE audio_file_jobs DROP COLUMN completude")
    assert "completude" not in _colonnes(eng, "audio_file_jobs")


def test_la_migration_explicite_ajoute_completude(base_par_defaut, monkeypatch):
    _ancien_schema(base_par_defaut)
    monkeypatch.setenv("APP_DB_MIGRATE", "1")
    db.init_db()
    assert "completude" in _colonnes(base_par_defaut, "audio_file_jobs")


def test_une_ligne_ancienne_a_completude_null_apres_migration(base_par_defaut, monkeypatch):
    _ancien_schema(base_par_defaut)
    with base_par_defaut.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO audio_file_jobs (session_token, filename, status, "
            "block_seconds, total_blocks, blocks, error, created_at, filenames, "
            "files_done, blocks_before_file) VALUES ('t', 'f', 'done', 60, 0, '[]', "
            "NULL, '2026-01-01 00:00:00', '[]', 0, 0)"
        )
    monkeypatch.setenv("APP_DB_MIGRATE", "1")
    db.init_db()
    with base_par_defaut.connect() as conn:
        assert conn.exec_driver_sql(
            "SELECT completude FROM audio_file_jobs"
        ).scalar() is None


def test_sans_opt_in_une_base_ancienne_reste_refusee(base_par_defaut):
    _ancien_schema(base_par_defaut)
    with pytest.raises(RuntimeError, match="Aucune migration implicite") as exc:
        db.init_db()
    assert "audio_file_jobs.completude" in str(exc.value)
    assert "completude" not in _colonnes(base_par_defaut, "audio_file_jobs")
