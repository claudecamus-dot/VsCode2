"""Espace démo vs réel (P5a-1, modèle VSCode1) : helper de mode, 1ère page de
choix, pose du cookie, tag `is_demo` à la création et filtrage des listings par
mode. DB jetable (engine.dispose avant unlink — verrou Windows, cf.
feedback-pytest-db-unlink-needs-engine-dispose)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Mission
from app.services.mode import est_mode_demo


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


def _req(cookie: str | None) -> Request:
    headers = [(b"cookie", cookie.encode())] if cookie is not None else []
    return Request({"type": "http", "headers": headers})


# --------------------------------------------------------------------------- #
# Helper de mode — seul `mode=demo` bascule en démo (jamais par défaut).
# --------------------------------------------------------------------------- #
def test_est_mode_demo_true_only_for_demo_cookie() -> None:
    assert est_mode_demo(_req("mode=demo")) is True


@pytest.mark.parametrize("cookie", [None, "mode=reel", "mode=", "mode=DEMO", "autre=x"])
def test_est_mode_demo_false_otherwise(cookie: str | None) -> None:
    assert est_mode_demo(_req(cookie)) is False


# --------------------------------------------------------------------------- #
# 1ère page + pose du cookie.
# --------------------------------------------------------------------------- #
def test_accueil_offers_demo_and_reel(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "Démonstration" in r.text
    assert "Usage réel" in r.text


def test_choisir_mode_demo_sets_cookie_and_redirects(client: TestClient) -> None:
    r = client.post("/mode/demo", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/demarrer"
    assert "mode=demo" in r.headers.get("set-cookie", "")


def test_choisir_mode_reel_sets_cookie(client: TestClient) -> None:
    r = client.post("/mode/reel", follow_redirects=False)
    assert r.status_code == 303
    assert "mode=reel" in r.headers.get("set-cookie", "")


def test_choisir_mode_unknown_falls_back_to_reel(client: TestClient) -> None:
    # Toute valeur inconnue retombe sur réel — on ne bascule jamais en démo par accident.
    r = client.post("/mode/nimporte", follow_redirects=False)
    assert "mode=reel" in r.headers.get("set-cookie", "")


def test_demo_banner_only_in_demo_mode(client: TestClient) -> None:
    assert "MODE DÉMO" in client.get("/missions", headers={"Cookie": "mode=demo"}).text
    assert "MODE DÉMO" not in client.get("/missions").text
    assert "MODE DÉMO" not in client.get("/missions", headers={"Cookie": "mode=reel"}).text


# --------------------------------------------------------------------------- #
# Tag `is_demo` à la création selon le mode courant.
# --------------------------------------------------------------------------- #
def _mission_by_name(name: str) -> Mission | None:
    db = SessionLocal()
    try:
        return db.query(Mission).filter(Mission.name == name).one_or_none()
    finally:
        db.close()


def test_create_mission_tagged_demo_in_demo_mode(client: TestClient) -> None:
    client.post("/missions", data={"name": "Mission démo X"},
                headers={"Cookie": "mode=demo"}, follow_redirects=False)
    m = _mission_by_name("Mission démo X")
    assert m is not None and m.is_demo is True


def test_create_mission_tagged_real_by_default(client: TestClient) -> None:
    client.post("/missions", data={"name": "Mission réelle X"}, follow_redirects=False)
    m = _mission_by_name("Mission réelle X")
    assert m is not None and m.is_demo is False


def test_libre_and_structure_creations_tag_demo(client: TestClient) -> None:
    client.post("/entretiens/libre/nouveau", headers={"Cookie": "mode=demo"},
                follow_redirects=False)
    client.post("/entretiens/structure/nouveau", headers={"Cookie": "mode=demo"},
                follow_redirects=False)
    db = SessionLocal()
    try:
        drafts = db.query(Mission).filter(Mission.is_draft.is_(True)).all()
        assert drafts and all(m.is_demo for m in drafts)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Filtrage des listings par mode — démo et réel ne se mélangent jamais.
# --------------------------------------------------------------------------- #
def test_list_missions_filters_by_mode(client: TestClient) -> None:
    db = SessionLocal()
    try:
        db.add_all([
            Mission(name="Réelle listée", is_demo=False),
            Mission(name="Démo listée", is_demo=True),
        ])
        db.commit()
    finally:
        db.close()

    reel = client.get("/missions").text  # pas de cookie -> réel
    assert "Réelle listée" in reel and "Démo listée" not in reel

    demo = client.get("/missions", headers={"Cookie": "mode=demo"}).text
    assert "Démo listée" in demo and "Réelle listée" not in demo
