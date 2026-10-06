"""Correctifs mineurs de la revue du 2026-10-06 sur l'entretien libre.

- m3 : « Retour » depuis la revue des tours perdait `session_token` et
  `segment_tail` (ni le formulaire ni la route ne les portaient).
"""
from __future__ import annotations

import re

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


def _nouvelle_mission(client: TestClient) -> int:
    response = client.post("/entretiens/libre/nouveau", follow_redirects=False)
    return int(response.headers["location"].split("/")[2])


def _champ_cache(html: str, nom: str) -> str | None:
    m = re.search(rf'name="{nom}"[^>]*value="([^"]*)"', html)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# m3
# --------------------------------------------------------------------------- #
def test_retour_reporte_jeton_et_reliquat_sur_l_ecran_de_transcription(
    client: TestClient,
) -> None:
    """Échoue sur le code d'avant : `/retour` n'acceptait pas ces champs, l'écran
    rouvert portait un jeton vide — les tranches déjà traitées étaient perdues."""
    mission_id = _nouvelle_mission(client)
    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/retour",
        data={"transcript": "Du texte.", "session_token": "tok-m3", "segment_tail": "reliquat m3"},
    )
    assert response.status_code == 200
    assert _champ_cache(response.text, "session_token") == "tok-m3"
    assert _champ_cache(response.text, "segment_tail") == "reliquat m3"


def test_revue_des_tours_porte_jeton_et_reliquat_pour_le_retour(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Échoue sur le code d'avant : le formulaire de la revue des tours (dont le
    bouton « Retour » poste vers `/retour`) n'avait pas ces deux champs."""
    monkeypatch.setattr(
        "app.routers.interviews.extract_turns_from_text",
        lambda text: {
            "turns": [{"interlocuteur": "A", "question": None, "remarque": "R",
                       "section_title": None}],
            "identity": {},
        },
    )
    mission_id = _nouvelle_mission(client)
    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "Du texte.", "session_token": "tok-revue", "segment_tail": "fin"},
    )
    assert response.status_code == 200
    assert _champ_cache(response.text, "session_token") == "tok-revue"
    assert _champ_cache(response.text, "segment_tail") == "fin"


# --------------------------------------------------------------------------- #
# m5 — date illisible : écartée, mais DITE
# --------------------------------------------------------------------------- #
def _mission_nommee(client: TestClient) -> int:
    mission_id = _nouvelle_mission(client)
    with SessionLocal() as db:
        db.get(Mission, mission_id).is_draft = False
        db.commit()
    return mission_id


def _stub_ia(monkeypatch) -> None:
    vide = lambda text: {"turns": [], "identity": {}}  # noqa: E731
    monkeypatch.setattr("app.routers.interviews.extract_turns_from_text", vide)
    try:
        monkeypatch.setattr("app.services.structuration_libre.extract_turns_from_text", vide)
    except (ImportError, AttributeError):
        pass


def test_enregistrement_libre_signale_une_date_illisible(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Échoue sur le code d'avant : la redirection ne portait aucun drapeau,
    la date disparaissait sans un mot."""
    _stub_ia(monkeypatch)
    mission_id = _mission_nommee(client)
    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "Du texte.", "interview_date": "31/12/2026"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("?date_invalide=1")


def test_enregistrement_libre_date_valide_sans_drapeau(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_ia(monkeypatch)
    mission_id = _mission_nommee(client)
    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "Du texte.", "interview_date": "2026-10-06"},
        follow_redirects=False,
    )
    assert "date_invalide" not in response.headers["location"]


def test_edition_fiche_libre_signale_une_date_illisible(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Échoue sur le code d'avant : la sauvegarde avalait la date illisible et
    la fiche rouverte n'en disait rien."""
    _stub_ia(monkeypatch)
    mission_id = _mission_nommee(client)
    cree = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "Du texte."},
        follow_redirects=False,
    )
    interview_id = int(cree.headers["location"].split("/")[2].split("?")[0])
    response = client.post(
        f"/interviews/{interview_id}/libre",
        data={"interviewee_name": "X", "interview_date": "2026-13-45"},
    )
    assert response.status_code == 200
    assert 'role="status"' in response.text
    assert "La date saisie n'a pas pu être lue" in response.text


def test_fiche_libre_sans_drapeau_n_affiche_rien(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_ia(monkeypatch)
    mission_id = _mission_nommee(client)
    cree = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "Du texte."},
        follow_redirects=True,
    )
    assert "La date saisie n'a pas pu être lue" not in cree.text
