"""AD-6 : `TranscriptionFondBusyError` doit traverser `iter_transcribe_blocks`
INTACTE pour que `_est_reessayable` la refuse. Avant le correctif,
`_transcrire_bloc_sequentiel` la rhabillait en `TranscriptionError(ECHEC_TRANSCRIPTION)`
et le job attendait 3 x 1800 s. Chemin réel : whisper et décodage doublés,
verrou de fond doublé ; ni `iter_transcribe_blocks` ni le job ne le sont."""
from __future__ import annotations

import numpy as np
import pytest

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.models import AudioFileJob
from app.services import audio_file_jobs, audio_transcribe as at

_PREFIXE = "fond_busy_test_"


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
    for f in RECORDINGS_DIR.glob(f"{_PREFIXE}*"):
        try:
            f.unlink()
        except OSError:
            pass


class _VerrouFondRefuse:
    """Verrou dont l'acquisition FOND échoue (timeout) ; compte les tentatives."""
    def __init__(self):
        self.appels = 0

    def acquire(self, timeout=None, priority=None):
        self.appels += 1
        return False

    def release(self):  # pragma: no cover - jamais obtenu
        pass


@pytest.fixture
def audio_un_bloc(monkeypatch):
    monkeypatch.setattr(at, "_faster_whisper", lambda: object())
    monkeypatch.setattr(at, "_probe_duration_s", lambda content: 5.0)
    monkeypatch.setattr(
        at, "_decode_to_pcm16k", lambda content: np.zeros(16000 * 5, dtype=np.float32)
    )
    monkeypatch.setattr(at, "BLOC_RETRY_PAUSE_S", 0.0)
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.0)
    monkeypatch.setattr(at, "BACKGROUND_LOCK_TIMEOUT_S", 0.01)


def _job(nom: str) -> int:
    complet = f"{_PREFIXE}{nom}"
    (RECORDINGS_DIR / complet).write_bytes(b"x")
    with SessionLocal() as db:
        job = AudioFileJob(session_token="tok-fb", status="pending", filename=complet)
        db.add(job)
        db.commit()
        return job.id


def _lancer(nom: str):
    job_id = _job(nom)
    audio_file_jobs.run_audio_file_job(job_id)
    with SessionLocal() as db:
        job = db.get(AudioFileJob, job_id)
        db.expunge(job)
        return job


def test_1_iter_transcribe_blocks_laisse_passer_la_classe_fond_busy(monkeypatch, audio_un_bloc):
    verrou = _VerrouFondRefuse()
    monkeypatch.setattr(at, "_MODEL_LOCK", verrou)
    with pytest.raises(at.TranscriptionError) as e:
        list(at.iter_transcribe_blocks(b"x"))
    assert isinstance(e.value, at.TranscriptionFondBusyError)
    assert verrou.appels == 1                       # pas de rejeu interne
    assert audio_file_jobs._est_reessayable(e.value) is False


def test_2_job_fond_busy_tente_une_seule_fois_et_echoue(monkeypatch, audio_un_bloc):
    verrou = _VerrouFondRefuse()
    monkeypatch.setattr(at, "_MODEL_LOCK", verrou)
    job = _lancer("fond")
    assert verrou.appels == 1, verrou.appels         # 3 x 1800 s avant le correctif
    assert job.status == "failed", job.status


def test_2b_echec_transitoire_generique_reste_rejoue(monkeypatch, audio_un_bloc):
    appels = {"n": 0}

    def _pcm(pcm, priority=at.FOND):
        appels["n"] += 1
        if appels["n"] <= at.BLOC_MAX_TENTATIVES:    # épuise le 1er appel d'iter
            raise RuntimeError("transitoire")
        return "ok."
    monkeypatch.setattr(at, "_transcribe_pcm_sequential", _pcm)
    job = _lancer("generique")
    assert appels["n"] == at.BLOC_MAX_TENTATIVES + 1
    assert job.status == "done", job.error


def test_3_verrou_live_occupe_reste_rejoue(monkeypatch, audio_un_bloc):
    appels = {"n": 0}

    def _pcm(pcm, priority=at.FOND):
        appels["n"] += 1
        if appels["n"] == 1:
            raise at.TranscriptionBusyError("occupé")
        return "ok."
    monkeypatch.setattr(at, "_transcribe_pcm_sequential", _pcm)
    job = _lancer("live")
    assert appels["n"] == 2
    assert job.status == "done", job.error
