"""AD-2 : l'attente de permis du FOND (sans échéance, par choix) bat le cœur du job.

Sans battement, un job de fond bloqué plus longtemps que le seuil de péremption
paraît mort (`is_audio_file_job_stale`) et un clic « Reprendre » le double.
Horloge et sleep sont doublés : aucune attente réelle.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import AudioFileJob
from app.services import audio_file_jobs, audio_transcribe as at


class _Stop(Exception):
    pass


def _reinit_db() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def _attendre_un_permis_occupe(monkeypatch, rappel, tours: int = 10, pas_s: float = 30.0):
    """Fait attendre `_drain_parallel` (FOND) sur un jeu de permis épuisé, `tours`
    tours de sleep d'une durée simulée `pas_s`, puis interrompt. Rend le nombre de
    sleeps effectués."""
    horloge = {"t": 1000.0, "sleeps": 0}

    def _sleep(_s):
        horloge["sleeps"] += 1
        horloge["t"] += pas_s
        if horloge["sleeps"] >= tours:
            raise _Stop

    monkeypatch.setattr(
        at, "time", SimpleNamespace(monotonic=lambda: horloge["t"], sleep=_sleep)
    )
    pris = 0
    while at._GLOBAL_WORKERS_SEMAPHORE.acquire(blocking=False):
        pris += 1
    jeton = at.BATTEMENT_ATTENTE.set(rappel)
    try:
        with pytest.raises(_Stop):
            next(at._drain_parallel([object(), object()], 0, 2, 1, priority=at.FOND))
    finally:
        at.BATTEMENT_ATTENTE.reset(jeton)
        for _ in range(pris):
            at._GLOBAL_WORKERS_SEMAPHORE.release()
    return horloge["sleeps"]


def _job_perime(db) -> AudioFileJob:
    job = AudioFileJob(session_token="tok-ad2", status="running", filename="x.wav")
    job.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=500)
    db.add(job)
    db.commit()
    return job


def test_le_fond_qui_attend_un_permis_bat_le_coeur_et_n_est_pas_perime(monkeypatch):
    _reinit_db()
    monkeypatch.setattr(audio_file_jobs, "audio_file_job_stale_after_s", lambda: 100)
    try:
        with SessionLocal() as db:
            job = _job_perime(db)
            assert audio_file_jobs.is_audio_file_job_stale(job)      # témoin : périmé
            avant = job.created_at
            appels = []

            def _rappel():
                appels.append(1)
                audio_file_jobs._battement(db, job)

            sleeps = _attendre_un_permis_occupe(monkeypatch, _rappel)
            assert appels, "aucun battement pendant l'attente du permis"
            assert len(appels) < sleeps          # périodique, jamais à chaque 0,05 s
            assert job.created_at > avant
            assert not audio_file_jobs.is_audio_file_job_stale(job)
    finally:
        engine.dispose()


def test_sans_battement_le_meme_job_reste_perime(monkeypatch):
    """Témoin : sans rappel déposé, l'attente ne bat rien et le job reste périmé."""
    _reinit_db()
    monkeypatch.setattr(audio_file_jobs, "audio_file_job_stale_after_s", lambda: 100)
    try:
        with SessionLocal() as db:
            job = _job_perime(db)
            _attendre_un_permis_occupe(monkeypatch, None)
            assert audio_file_jobs.is_audio_file_job_stale(job)
    finally:
        engine.dispose()


def test_un_rappel_qui_leve_ne_casse_pas_l_attente(monkeypatch):
    def _casse():
        raise RuntimeError("base verrouillée")

    # L'attente continue jusqu'au _Stop du sleep : pas d'autre exception.
    assert _attendre_un_permis_occupe(monkeypatch, _casse) == 10
