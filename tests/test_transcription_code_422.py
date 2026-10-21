"""Le 422 « Échec de la transcription » porte `code: "transient"` (lot « transcription
fiable », commit 3).

Avant : un worker tué par la mémoire ou une erreur Whisper sur CE chunk renvoyait un
422 sans code, indiscernable d'un refus définitif (fichier illisible…). Le client ne
pouvait ni le rejouer sans deviner d'après le texte français, ni le laisser tomber :
le segment était perdu alors que l'audio était intact.

Seul `ECHEC_TRANSCRIPTION` est rejouable ; les autres refus restent SANS code, et
`no_speech` / `busy` gardent le leur.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview, Mission
from app.services import audio_transcribe


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


def _leve(exc: Exception):
    def _transcribe(_content: bytes, abandonne=None) -> str:
        raise exc

    return _transcribe


def _entretien() -> int:
    session = SessionLocal()
    try:
        mission = Mission(name="Mission code 422")
        session.add(mission)
        session.commit()
        interview = Interview(
            mission_id=mission.id, interviewee_name="Bob", mode="libre", status="done",
        )
        session.add(interview)
        session.commit()
        return interview.id
    finally:
        session.close()


def _segment(client: TestClient):
    return client.post(
        "/audio/transcribe-segment",
        files={"file": ("seg.webm", b"un octet de blob", "audio/webm")},
    )


def _dictee(client: TestClient, interview_id: int):
    return client.post(
        f"/interviews/{interview_id}/notes/transcribe",
        files={"file": ("notes.webm", b"un octet de blob", "audio/webm")},
    )


# --------------------------------------------------------------------------- #
# fonction pure
# --------------------------------------------------------------------------- #
def test_corps_transient_seulement_pour_l_echec_de_transcription():
    ech = audio_transcribe.TranscriptionError(audio_transcribe.ECHEC_TRANSCRIPTION)
    assert audio_transcribe.corps_erreur_transcription(ech) == {
        "error": audio_transcribe.ECHEC_TRANSCRIPTION, "code": "transient",
    }
    # Refus définitifs : jamais de code (le client ne doit pas les rejouer).
    for msg in ("Fichier audio illisible.", "Aucun enregistrement reçu."):
        corps = audio_transcribe.corps_erreur_transcription(
            audio_transcribe.TranscriptionError(msg)
        )
        assert corps == {"error": msg}
    # Les sous-classes ont leurs codes ailleurs : jamais « transient » ici.
    for exc in (audio_transcribe.NoSpeechError(audio_transcribe.ECHEC_TRANSCRIPTION),
                audio_transcribe.TranscriptionBusyError(audio_transcribe.ECHEC_TRANSCRIPTION)):
        assert "code" not in audio_transcribe.corps_erreur_transcription(exc)


# --------------------------------------------------------------------------- #
# route segment (micro direct)
# --------------------------------------------------------------------------- #
def test_segment_422_transient_porte_son_code(client, monkeypatch):
    monkeypatch.setattr(
        audio_transcribe, "transcribe_audio",
        _leve(audio_transcribe.TranscriptionError(audio_transcribe.ECHEC_TRANSCRIPTION)),
    )
    r = _segment(client)
    assert r.status_code == 422
    assert r.json() == {"error": audio_transcribe.ECHEC_TRANSCRIPTION, "code": "transient"}


def test_segment_422_definitif_reste_sans_code(client, monkeypatch):
    monkeypatch.setattr(
        audio_transcribe, "transcribe_audio",
        _leve(audio_transcribe.TranscriptionError("Fichier audio illisible.")),
    )
    r = _segment(client)
    assert r.status_code == 422
    assert "code" not in r.json()


def test_segment_no_speech_et_busy_gardent_leur_code(client, monkeypatch):
    monkeypatch.setattr(
        audio_transcribe, "transcribe_audio",
        _leve(audio_transcribe.NoSpeechError("Aucune parole.")),
    )
    assert _segment(client).json()["code"] == "no_speech"

    busy = audio_transcribe.TranscriptionBusyError("occupe")
    busy.retry_after_s = 3.0
    monkeypatch.setattr(audio_transcribe, "transcribe_audio", _leve(busy))
    r = _segment(client)
    assert r.status_code == 503 and r.json()["code"] == "busy"


def test_segment_en_echec_laisse_une_trace_serveur_sans_texte(client, monkeypatch, caplog):
    monkeypatch.setattr(
        audio_transcribe, "transcribe_audio",
        _leve(audio_transcribe.TranscriptionError(audio_transcribe.ECHEC_TRANSCRIPTION)),
    )
    with caplog.at_level(logging.WARNING):
        _segment(client)
    traces = [r.getMessage() for r in caplog.records if "Segment direct refusé" in r.getMessage()]
    assert traces == ["Segment direct refusé (422, code=transient)"]


# --------------------------------------------------------------------------- #
# route dictée de notes libres
# --------------------------------------------------------------------------- #
def test_dictee_422_transient_porte_son_code(client, monkeypatch):
    interview_id = _entretien()
    monkeypatch.setattr(
        audio_transcribe, "transcribe_audio",
        _leve(audio_transcribe.TranscriptionError(audio_transcribe.ECHEC_TRANSCRIPTION)),
    )
    r = _dictee(client, interview_id)
    assert r.status_code == 422
    assert r.json()["code"] == "transient"


def test_dictee_422_definitif_reste_sans_code(client, monkeypatch):
    interview_id = _entretien()
    monkeypatch.setattr(
        audio_transcribe, "transcribe_audio",
        _leve(audio_transcribe.TranscriptionError("Fichier audio illisible.")),
    )
    r = _dictee(client, interview_id)
    assert r.status_code == 422
    assert "code" not in r.json()
