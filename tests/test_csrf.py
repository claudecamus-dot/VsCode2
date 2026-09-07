"""Protection anti-CSRF (`app.csrf.verifier_origine`) — finding audit-technique
securite:critique du 2026-09-04 : POST /missions/{id}/delete (et les 77 autres
routes mutatives) purgeait irréversiblement mission + audio sans aucun
contrôle Origin/Referer, atteignable cross-origin sans preflight (requête
POST simple au sens CORS). Pendant du correctif VSCode1 (`app/src/csrf.js`,
commit ef3ae7b), même patron `verifier_origine`/`meme_origine`.

Reproduit l'attaque exacte de l'audit (200/303 avant, 403 après) + une
deuxième route mutante (POST /missions, sans rapport avec la suppression)
pour prouver que la garde est globale, pas câblée route par route.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import DB_PATH, SessionLocal, engine, init_db
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


def _mission_id(client: TestClient, name: str) -> int:
    response = client.post("/missions", data={"name": name}, follow_redirects=False)
    return int(response.headers["location"].rsplit("/", 1)[-1])


def test_get_ne_verifie_pas_l_origine(client: TestClient) -> None:
    """Hors périmètre CSRF : la lecture ne mute rien, la garde ne doit ni
    exiger Origin ni la valider."""
    response = client.get("/missions", headers={"origin": ""})
    assert response.status_code == 200


def test_post_sans_origin_ni_referer_est_bloque(client: TestClient) -> None:
    """Reproduit l'attaque de l'audit : une requête POST sans Origin ni
    Referer (navigateur qui les strippe, ou attaquant qui les omet) sur une
    route mutante quelconque — ici la création de mission, pas seulement la
    suppression, pour prouver la garde GLOBALE plutôt qu'un correctif câblé
    route par route.

    Échoue sur le code d'avant : la mission était créée (303), aucune garde."""
    avant = SessionLocal().query(Mission).count()
    response = client.post(
        "/missions", data={"name": "Mission via CSRF"},
        headers={"origin": "", "referer": ""}, follow_redirects=False,
    )
    assert response.status_code == 403
    assert "Origine non autorisée" in response.json()["error"]
    apres = SessionLocal().query(Mission).count()
    assert apres == avant, "aucune mission ne doit avoir été créée malgré le 403"


def test_post_origine_etrangere_est_bloque(client: TestClient) -> None:
    """Origin présent mais pointant vers un autre hôte (page attaquante réelle,
    pas seulement un en-tête absent) — même verdict : 403."""
    response = client.post(
        "/missions", data={"name": "Mission via attaque cross-origin"},
        headers={"origin": "http://attaquant.exemple"}, follow_redirects=False,
    )
    assert response.status_code == 403


def test_delete_mission_sans_origin_est_bloque_et_ne_supprime_rien(
    client: TestClient,
) -> None:
    """La route de l'audit elle-même : suppression irréversible (mission +
    audio) — bloquée, la mission survit."""
    mission_id = _mission_id(client, "Mission à ne pas perdre")

    response = client.post(
        f"/missions/{mission_id}/delete",
        headers={"origin": "", "referer": ""},
        follow_redirects=False,
    )
    assert response.status_code == 403

    db = SessionLocal()
    try:
        assert db.get(Mission, mission_id) is not None
    finally:
        db.close()


def test_post_avec_la_bonne_origine_passe(client: TestClient) -> None:
    """Chemin nominal préservé : Origin correspondant au Host (ce que le
    patch de `conftest.py` injecte par défaut pour tout le reste de la
    suite) continue de passer — la garde ne doit pas bloquer l'usage normal."""
    response = client.post(
        "/missions", data={"name": "Mission légitime"}, follow_redirects=False,
    )
    assert response.status_code == 303


def test_referer_seul_suffit_quand_origin_est_absent(client: TestClient) -> None:
    """Un navigateur peut n'envoyer que Referer sur certaines requêtes
    (formulaire classique sans Origin) — la garde accepte Referer en repli,
    comme `app.csrf.meme_origine` le documente."""
    response = client.post(
        "/missions", data={"name": "Mission via Referer"},
        headers={"origin": "", "referer": "http://testserver/missions/new"},
        follow_redirects=False,
    )
    assert response.status_code == 303
