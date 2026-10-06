"""M2 (revue du 2026-10-06) : les références audio postées en champ caché
(`audio_backup_path`, `audio_segments`) ne sont plus persistées sans contrôle.

Un seul validateur (`mission_backups.references_audio_sures`) pour les deux
sites d'écriture : `_creer_interview_libre` et `import_interview_confirm`.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview
from app.services import mission_backups


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


@pytest.fixture
def recordings(tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.interviews.RECORDINGS_DIR", tmp_path)
    return tmp_path


def _nouvelle_mission(client: TestClient) -> int:
    response = client.post("/entretiens/libre/nouveau", follow_redirects=False)
    return int(response.headers["location"].split("/")[2])


def _stub_ia(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.interviews.extract_turns_from_text",
        lambda text: {"turns": [], "identity": {}},
    )
    try:
        monkeypatch.setattr(
            "app.services.structuration_libre.extract_turns_from_text",
            lambda text: {"turns": [], "identity": {}},
        )
    except (ImportError, AttributeError):
        pass


# --------------------------------------------------------------------------- #
# Unitaire
# --------------------------------------------------------------------------- #
def test_validateur_ne_garde_que_les_fichiers_reels_de_la_mission(tmp_path) -> None:
    (tmp_path / "7_100_aa.webm").write_bytes(b"x")
    (tmp_path / "8_100_bb.webm").write_bytes(b"x")
    chemin, segments = mission_backups.references_audio_sures(
        7,
        "7_100_aa.webm",
        [
            {"filename": "7_100_aa.webm", "index": 0},
            1,                                   # non-dict : écarté
            "7_100_aa.webm",                     # non-dict : écarté
            {"filename": "8_100_bb.webm"},       # autre mission
            {"filename": "7_999_zz.webm"},       # absent du disque
            {"filename": "../7_100_aa.webm"},    # chemin
            {"nope": True},                      # sans nom
        ],
        tmp_path,
    )
    assert chemin == "7_100_aa.webm"
    assert segments == [{"filename": "7_100_aa.webm", "index": 0}]


@pytest.mark.parametrize(
    "nom", ["", None, 42, "../7_1_a.webm", "7_1_a/../b.webm", "77_1_a.webm", "x.webm"]
)
def test_validateur_refuse_un_chemin_invalide(tmp_path, nom) -> None:
    chemin, segments = mission_backups.references_audio_sures(7, nom, "pas une liste", tmp_path)
    assert chemin is None
    assert segments == []


# --------------------------------------------------------------------------- #
# Route libre (`_creer_interview_libre`)
# --------------------------------------------------------------------------- #
def test_enregistrement_libre_ne_persiste_pas_de_reference_audio_forgee(
    client: TestClient, monkeypatch, recordings
) -> None:
    """Échoue sur le code d'avant : `audio_segments` gardait l'entier et le
    nom forgé, `audio_backup_path` le chemin arbitraire."""
    _stub_ia(monkeypatch)
    mission_id = _nouvelle_mission(client)
    vrai = f"{mission_id}_100_aa.webm"
    (recordings / vrai).write_bytes(b"x")
    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={
            "transcript": "Du texte.",
            "audio_backup_path": "../../secret.webm",
            "audio_segments": json.dumps([1, {"filename": vrai}, {"filename": "autre.webm"}]),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        interview = db.scalars(
            select(Interview).where(Interview.mission_id == mission_id)
        ).one()
        assert interview.audio_backup_path is None
        assert interview.audio_segments == [{"filename": vrai}]


# --------------------------------------------------------------------------- #
# Route d'import (`import_interview_confirm`)
# --------------------------------------------------------------------------- #
def test_import_confirm_ne_persiste_pas_de_reference_audio_forgee(
    client: TestClient, recordings
) -> None:
    """Échoue sur le code d'avant : le chemin forgé était persisté tel quel."""
    mission_id = _nouvelle_mission(client)
    proposed = {"identity": {"interviewee_name": "X", "audio_backup_path": "../evil.webm"}}
    response = client.post(
        f"/missions/{mission_id}/interviews/import/confirm",
        data={"proposed": json.dumps(proposed)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    interview_id = int(response.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as db:
        assert db.get(Interview, interview_id).audio_backup_path is None


def test_import_confirm_garde_une_reference_audio_reelle(
    client: TestClient, recordings
) -> None:
    mission_id = _nouvelle_mission(client)
    vrai = f"{mission_id}_200_cc.webm"
    (recordings / vrai).write_bytes(b"x")
    proposed = {"identity": {"interviewee_name": "X", "audio_backup_path": vrai}}
    response = client.post(
        f"/missions/{mission_id}/interviews/import/confirm",
        data={"proposed": json.dumps(proposed)},
        follow_redirects=False,
    )
    interview_id = int(response.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as db:
        assert db.get(Interview, interview_id).audio_backup_path == vrai
