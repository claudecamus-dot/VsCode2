"""Journal d'une ligne par requête de `/audio/transcribe-segment` (incident
2026-10-27 : aucun log serveur exploitable) et route `POST /audio/warm-up`."""
from __future__ import annotations

import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import audio_transcribe

TEXTE_SECRET = "phrase-confidentielle-d-entretien"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _post_segment(client: TestClient, octets: bytes):
    return client.post(
        "/audio/transcribe-segment",
        files={"file": ("s.webm", octets, "audio/webm")},
        headers={"Origin": "http://testserver"},
    )


def _lignes(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "app.routers.interviews_audio"
            and r.getMessage().startswith("transcribe-segment")]


def test_journal_succes_sans_texte(client, monkeypatch, caplog) -> None:
    monkeypatch.setattr(audio_transcribe, "transcribe_audio", lambda c, **k: TEXTE_SECRET)
    monkeypatch.setattr("app.routers.interviews_audio.ai_common.warm_up_ollama", lambda: None)
    corps = b"journal-succes-" + b"x" * 40
    with caplog.at_level(logging.INFO):
        assert _post_segment(client, corps).status_code == 200
    lignes = _lignes(caplog)
    assert len(lignes) == 1, lignes
    assert "statut=200" in lignes[0] and f"octets={len(corps)}" in lignes[0]
    assert "duree_s=" in lignes[0]
    assert TEXTE_SECRET not in caplog.text


def test_journal_refus_422(client, monkeypatch, caplog) -> None:
    def _refus(c, **k):
        raise audio_transcribe.NoSpeechError("aucune parole " + TEXTE_SECRET)

    monkeypatch.setattr(audio_transcribe, "transcribe_audio", _refus)
    with caplog.at_level(logging.INFO):
        assert _post_segment(client, b"journal-422-" + b"y" * 30).status_code == 422
    lignes = _lignes(caplog)
    assert len(lignes) == 1 and "statut=422" in lignes[0], lignes
    assert TEXTE_SECRET not in caplog.text


def test_journal_503_busy_porte_retry_after(client, monkeypatch, caplog) -> None:
    def _busy(c, **k):
        exc = audio_transcribe.TranscriptionBusyError("occupé")
        exc.retry_after_s = 7
        raise exc

    monkeypatch.setattr(audio_transcribe, "transcribe_audio", _busy)
    with caplog.at_level(logging.INFO):
        assert _post_segment(client, b"journal-503-" + b"z" * 30).status_code == 503
    lignes = _lignes(caplog)
    assert len(lignes) == 1, lignes
    assert "statut=503" in lignes[0] and "retry_after_s=7" in lignes[0], lignes


# --- /audio/warm-up --------------------------------------------------------------


class _FauxFW:
    """Doublure du module faster_whisper : compte les constructions."""

    def __init__(self, leve: bool = False) -> None:
        self.n = 0
        self.leve = leve

        outer = self

        class WhisperModel:
            def __init__(self, *a, **k) -> None:
                outer.n += 1
                time.sleep(0.05)  # fenêtre de course
                if outer.leve:
                    raise RuntimeError("modèle indisponible")

        self.WhisperModel = WhisperModel


def _attendre_threads() -> None:
    for t in threading.enumerate():
        if t.name == "whisper-warm-up":
            t.join(timeout=10)


@pytest.fixture
def faux_fw(monkeypatch):
    faux = _FauxFW()
    monkeypatch.setenv("WHISPER_WARM_UP", "1")
    monkeypatch.setattr(audio_transcribe, "_model", None)
    monkeypatch.setattr(audio_transcribe, "_faster_whisper", lambda: faux)
    return faux


def _warm(client: TestClient):
    return client.post("/audio/warm-up", headers={"Origin": "http://testserver"})


def test_warm_up_204_sans_corps_et_idempotent(client, faux_fw) -> None:
    r1 = _warm(client)
    assert r1.status_code == 204 and r1.content == b""
    _attendre_threads()
    assert _warm(client).status_code == 204
    _attendre_threads()
    assert faux_fw.n == 1


def test_warm_up_concurrent_ne_charge_qu_une_fois(client, faux_fw) -> None:
    ts = [threading.Thread(target=audio_transcribe.warm_up) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert faux_fw.n == 1


def test_get_model_concurrent_avec_un_warm_up_ne_charge_qu_une_fois(client, faux_fw) -> None:
    """Le chemin TRANSCRIPTION (`_get_model()` direct) et le warm-up se
    disputent le chargement : un seul modèle (~1,5 Go) doit être construit."""
    cibles = [audio_transcribe.warm_up] + [audio_transcribe._get_model] * 4
    ts = [threading.Thread(target=c) for c in cibles]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert faux_fw.n == 1


def test_warm_up_desactive_ne_fait_rien(client, faux_fw, monkeypatch) -> None:
    monkeypatch.setenv("WHISPER_WARM_UP", "0")
    assert _warm(client).status_code == 204
    _attendre_threads()
    assert faux_fw.n == 0


def test_warm_up_ne_bloque_pas_la_reponse(client, faux_fw, monkeypatch) -> None:
    lib = threading.Event()
    monkeypatch.setattr(audio_transcribe, "warm_up", lambda: lib.wait(10))
    t0 = time.monotonic()
    try:
        assert _warm(client).status_code == 204
        assert time.monotonic() - t0 < 3
    finally:
        lib.set()
        _attendre_threads()


def test_warm_up_chargement_qui_leve_reste_204(client, faux_fw, caplog) -> None:
    faux_fw.leve = True
    with caplog.at_level(logging.ERROR):
        assert _warm(client).status_code == 204
        _attendre_threads()
    assert faux_fw.n == 1
    assert "Préchauffage Whisper à la demande échoué" in caplog.text


def test_warm_up_sans_credentials_refuse() -> None:
    anonyme = TestClient(app)
    anonyme.headers.pop("Authorization", None)
    anonyme.cookies.clear()
    r = anonyme.post("/audio/warm-up", headers={"Origin": "http://testserver"})
    assert r.status_code in (401, 403, 503), r.status_code


def test_warm_up_origine_etrangere_bloquee_par_csrf(client, faux_fw) -> None:
    r = client.post("/audio/warm-up", headers={"Origin": "http://attaquant.exemple"})
    assert r.status_code == 403 and "CSRF" in r.json().get("error", "")
    assert faux_fw.n == 0
