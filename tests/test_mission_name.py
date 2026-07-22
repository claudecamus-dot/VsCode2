"""Renommage de la mission (US 2026-07-22) : le nom sert de titre au deck PPT et
n'était pas modifiable après création. DB jetable (engine.dispose avant unlink —
verrou Windows) ; conftest pointe DB_PATH sur une base sandbox, jamais data/app.db."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Mission


def setup_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def teardown_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _mission(name: str = "Ancien nom") -> int:
    db = SessionLocal()
    try:
        m = Mission(name=name)
        db.add(m)
        db.commit()
        return m.id
    finally:
        db.close()


def test_rename_mission_updates_name(client: TestClient) -> None:
    mid = _mission()
    r = client.post(f"/missions/{mid}/name", data={"name": "Nouveau nom de mission"})
    assert r.status_code == 200 and "enregistré" in r.text
    db = SessionLocal()
    try:
        assert db.get(Mission, mid).name == "Nouveau nom de mission"
    finally:
        db.close()


def test_rename_empty_refused_and_name_unchanged(client: TestClient) -> None:
    mid = _mission("Ancien nom")
    r = client.post(f"/missions/{mid}/name", data={"name": "   "})
    assert "obligatoire" in r.text
    db = SessionLocal()
    try:
        assert db.get(Mission, mid).name == "Ancien nom"  # inchangé
    finally:
        db.close()


def test_detail_page_exposes_editable_name(client: TestClient) -> None:
    mid = _mission("Ma mission")
    r = client.get(f"/missions/{mid}")
    assert "mission-name-input" in r.text
    assert f"/missions/{mid}/name" in r.text
