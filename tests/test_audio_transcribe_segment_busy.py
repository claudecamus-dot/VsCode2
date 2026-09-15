"""Le verrou `_MODEL_LOCK` de transcription (`audio_transcribe.py`) ne bloque
plus indéfiniment : passé `SEGMENT_LOCK_TIMEOUT_S`, un segment concurrent
reçoit un refus « occupé » exploitable (`TranscriptionBusyError`, 503 +
`Retry-After` côté route) au lieu d'un thread suspendu jusqu'au timeout réseau
client.

Correctif issu de la salle `atelier-dev` du 2026-09-15 (lot 1). Diagnostic à
l'origine : « échecs réguliers de transcription de segment pendant
l'enregistrement » -> `_MODEL_LOCK` (audio_transcribe.py) sérialise tous les
segments de tous les onglets/entretiens concurrents sans file bornée ni
signal de charge, combiné à un blocage indéfini côté serveur.

`TranscriptionBusyError` n'existait pas avant ce correctif : ces tests
échouent sur le code d'avant par `AttributeError` (attribut inexistant sur le
module), la preuve la plus directe qu'ils tiennent le comportement neuf.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview, Mission
from app.routers import interviews as routeur_interviews
from app.services import audio_transcribe


def setup_module() -> None:
    # `engine.dispose()` AVANT l'unlink : un fichier de test precedent dans la
    # collection peut avoir laisse le pool de connexions ouvert sur DB_PATH,
    # ce qui fait echouer l'unlink sous Windows (WinError 32) sans lien avec
    # ce module (feedback memoire : deterministe selon l'ordre de collection,
    # invisible quand ce fichier tourne seul).
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


def _sans_decodage(monkeypatch: pytest.MonkeyPatch, duration_s: float = 5.0) -> None:
    """Neutralise le décodage/la sonde de durée pour isoler le comportement
    du verrou — pas de dépendance à faster-whisper réellement installé."""
    monkeypatch.setattr(audio_transcribe, "_faster_whisper", lambda: MagicMock())
    monkeypatch.setattr(audio_transcribe, "_exiger_piste_audio", lambda content: None)
    monkeypatch.setattr(audio_transcribe, "_probe_duration_s", lambda content: duration_s)


# --------------------------------------------------------------------------- #
# 1. Le verrou ne bloque plus indéfiniment.
# --------------------------------------------------------------------------- #
def test_verrou_occupe_leve_busy_au_lieu_de_bloquer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verrou déjà tenu par un « autre onglet », jamais libéré pendant le
    test : `transcribe_audio` doit rendre la main au bout de
    `SEGMENT_LOCK_TIMEOUT_S`, jamais bloquer indéfiniment ni tenter le modèle
    pour le segment refusé."""
    _sans_decodage(monkeypatch)
    monkeypatch.setattr(audio_transcribe, "SEGMENT_LOCK_TIMEOUT_S", 0.2)
    modele_espion = MagicMock()
    monkeypatch.setattr(audio_transcribe, "_get_model", modele_espion)

    audio_transcribe._MODEL_LOCK.acquire()
    try:
        debut = time.monotonic()
        with pytest.raises(audio_transcribe.TranscriptionBusyError) as capture:
            audio_transcribe.transcribe_audio(b"contenu factice")
        ecoule = time.monotonic() - debut
    finally:
        audio_transcribe._MODEL_LOCK.release()

    assert ecoule < 1.0, f"a bloque {ecoule:.2f}s au lieu de respecter SEGMENT_LOCK_TIMEOUT_S"
    assert capture.value.retry_after_s == pytest.approx(0.2)
    modele_espion.assert_not_called()  # le segment concurrent n'a jamais ete tente


def test_verrou_libere_apres_echec_du_modele(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un `finally` doit relâcher `_MODEL_LOCK` même si `model.transcribe`
    plante — sans ça, le premier échec de modèle condamnerait tous les
    segments suivants à un `TranscriptionBusyError` permanent (deadlock)."""
    _sans_decodage(monkeypatch)
    modele_qui_plante = MagicMock()
    modele_qui_plante.transcribe.side_effect = RuntimeError("plante")
    monkeypatch.setattr(audio_transcribe, "_get_model", lambda: modele_qui_plante)

    with pytest.raises(audio_transcribe.TranscriptionError):
        audio_transcribe.transcribe_audio(b"contenu factice")

    libere = audio_transcribe._MODEL_LOCK.acquire(timeout=0)
    assert libere, "le verrou est reste tenu apres l'echec du modele"
    audio_transcribe._MODEL_LOCK.release()


# --------------------------------------------------------------------------- #
# 2. Cas multi-onglets du diagnostic initial : deux segments concurrents.
# --------------------------------------------------------------------------- #
def test_deux_segments_concurrents_le_second_recoit_busy_proprement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduit le cas multi-onglets cité par le diagnostic (`_MODEL_LOCK`
    sérialise tous les onglets/entretiens) : deux transcriptions démarrées à
    quelques dizaines de ms d'écart, comme deux `to_thread` de la route —
    la seconde doit recevoir `TranscriptionBusyError`, jamais une exception
    non gérée ni un blocage silencieux au-delà du timeout configuré."""
    _sans_decodage(monkeypatch)
    monkeypatch.setattr(audio_transcribe, "SEGMENT_LOCK_TIMEOUT_S", 0.3)

    class FakeSegment:
        def __init__(self, text: str) -> None:
            self.text = text

    def modele_lent(*_a, **_k):
        time.sleep(0.6)  # tient le verrou plus longtemps que le timeout du second appel
        return [FakeSegment("bonjour")], MagicMock()

    fake_model = MagicMock()
    fake_model.transcribe.side_effect = modele_lent
    monkeypatch.setattr(audio_transcribe, "_get_model", lambda: fake_model)

    resultats: dict[str, object] = {}

    def _premier() -> None:
        resultats["premier"] = audio_transcribe.transcribe_audio(b"segment A")

    def _second() -> None:
        time.sleep(0.05)  # laisse le premier prendre le verrou en premier
        try:
            audio_transcribe.transcribe_audio(b"segment B")
        except Exception as exc:  # capture aussi un echec inattendu, pour l'assertion
            resultats["second"] = exc

    t1 = threading.Thread(target=_premier)
    t2 = threading.Thread(target=_second)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert resultats.get("premier") == "bonjour"
    assert isinstance(resultats.get("second"), audio_transcribe.TranscriptionBusyError), (
        f"le second segment n'a pas recu Busy : {resultats.get('second')!r}"
    )


# --------------------------------------------------------------------------- #
# 3. Contrat HTTP de la route segment.
# --------------------------------------------------------------------------- #
def test_route_segment_renvoie_503_retry_after_quand_occupe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """503 (`status >= 500` : le JS de record.html relance automatiquement) +
    en-tête `Retry-After` + `code: "busy"` — distinct du 422 `no_speech`/
    erreur de contenu, puisque le segment n'a pas été tenté."""

    def _toujours_occupe(_content: bytes) -> str:
        exc = audio_transcribe.TranscriptionBusyError("occupe")
        exc.retry_after_s = 7.0
        raise exc

    monkeypatch.setattr(audio_transcribe, "transcribe_audio", _toujours_occupe)

    response = client.post(
        "/audio/transcribe-segment",
        files={"file": ("segment.webm", b"un octet de blob", "audio/webm")},
    )

    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "7"
    body = response.json()
    assert body["code"] == "busy"
    assert body["retry_after_s"] == 7
    assert "detail" not in body  # meme contrat d'erreur que les autres branches de la route


def test_route_segment_distingue_busy_de_no_speech(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Garde-fou d'ordre des `except` : `TranscriptionBusyError` hérite de
    `TranscriptionError` — si son `except` était placé APRÈS le générique, il
    ne serait jamais atteint et Busy tomberait en 422 sans `code: "busy"`."""

    def _no_speech(_content: bytes) -> str:
        raise audio_transcribe.NoSpeechError("Aucune parole détectée dans l'enregistrement.")

    monkeypatch.setattr(audio_transcribe, "transcribe_audio", _no_speech)

    response = client.post(
        "/audio/transcribe-segment",
        files={"file": ("segment.webm", b"fake", "audio/webm")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "no_speech"


# --------------------------------------------------------------------------- #
# 4. Le site jumeau (dictée de notes libres) partage le même contrat.
# --------------------------------------------------------------------------- #
def test_route_notes_transcribe_renvoie_503_retry_after_quand_occupe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/interviews/{id}/notes/transcribe` (interviews.py) appelle le même
    `audio_transcribe.transcribe_audio()` que la route segment — même verrou,
    même `TranscriptionBusyError` possible. Revue adversariale du lot 1
    (2026-09-15) : ce site n'avait PAS l'except dédié, Busy y retombait en 422
    générique au lieu du 503+Retry-After rejouable."""
    session = SessionLocal()
    try:
        mission = Mission(name="Mission busy")
        session.add(mission)
        session.commit()
        interview = Interview(
            mission_id=mission.id, interviewee_name="Bob", mode="libre", status="done",
        )
        session.add(interview)
        session.commit()
        interview_id = interview.id
    finally:
        session.close()

    def _toujours_occupe(_content: bytes) -> str:
        exc = audio_transcribe.TranscriptionBusyError("occupe")
        exc.retry_after_s = 5.0
        raise exc

    monkeypatch.setattr(routeur_interviews.audio_transcribe, "transcribe_audio", _toujours_occupe)

    response = client.post(
        f"/interviews/{interview_id}/notes/transcribe",
        files={"file": ("notes.webm", b"un octet de blob", "audio/webm")},
    )

    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "5"
    body = response.json()
    assert body["code"] == "busy"
    assert body["retry_after_s"] == 5
