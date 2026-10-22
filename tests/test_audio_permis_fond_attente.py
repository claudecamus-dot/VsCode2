"""AD-2 : l'attente de permis du FOND (sans échéance, par choix) bat le cœur du job.

Sans battement, un job de fond bloqué plus longtemps que le seuil de péremption
paraît mort (`is_audio_file_job_stale`) et un clic « Reprendre » le double.
Horloge et sleep sont doublés : aucune attente réelle.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

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


def test_un_commit_de_battement_rate_laisse_la_session_utilisable(monkeypatch):
    """A1 : le commit du battement échoue => rollback, la session reste utilisable
    (pas de PendingRollbackError au prochain accès) et l'attente n'est pas coupée."""
    _reinit_db()
    try:
        with SessionLocal() as db:
            job = _job_perime(db)
            vrai_commit = db.commit
            etat = {"n": 0}

            def _commit_ko():
                etat["n"] += 1
                if etat["n"] == 1:
                    # Échec RÉEL du flush au commit : la session passe à « rollback requis ».
                    job.status = None                # NOT NULL => IntegrityError
                return vrai_commit()

            monkeypatch.setattr(db, "commit", _commit_ko)
            sleeps = _attendre_un_permis_occupe(
                monkeypatch, lambda: audio_file_jobs._battement(db, job)
            )
            assert sleeps == 10                      # attente non interrompue
            assert etat["n"] >= 1
            assert db.execute(text("select 1")).scalar() == 1   # session utilisable
    finally:
        engine.dispose()


def test_le_job_de_fond_reel_bat_le_coeur_pendant_l_attente_du_permis(monkeypatch):
    """A2, de bout en bout : par `run_audio_file_job` RÉEL, la ContextVar posée dans
    le thread du job est visible dans la boucle d'attente de `_drain_parallel` et
    `created_at` AVANCE pendant l'attente. Horloge factice, exécuteur en ligne."""
    import numpy as np
    from concurrent.futures import Future

    from app.db import RECORDINGS_DIR

    _reinit_db()
    monkeypatch.setattr(at, "_faster_whisper", lambda: object())
    monkeypatch.setattr(at, "_probe_duration_s", lambda content: 130.0)
    monkeypatch.setattr(
        at, "_decode_to_pcm16k", lambda content: np.zeros(16000 * 130, dtype=np.float32)
    )

    class _ExecutorEnLigne:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, fn, *args):
            f = Future()
            f.set_result("texte")
            return f

    monkeypatch.setattr(at, "ProcessPoolExecutor", _ExecutorEnLigne)

    nom = "a2_battement_integration_test.wav"
    (RECORDINGS_DIR / nom).write_bytes(b"x")
    pris = 0
    while at._GLOBAL_WORKERS_SEMAPHORE.acquire(blocking=False):
        pris += 1                                  # jeu de permis épuisé : le fond attend
    mesures: list = []
    horloge = {"t": 1000.0, "sleeps": 0}
    tours = 10

    def _created_at(job_id):
        with SessionLocal() as autre:
            return autre.get(AudioFileJob, job_id).created_at

    def _sleep(_s):
        horloge["sleeps"] += 1
        horloge["t"] += 30.0
        if horloge["sleeps"] in (1, tours):
            mesures.append(_created_at(job_id))
        if horloge["sleeps"] == tours:             # le permis se libère
            for _ in range(pris):
                at._GLOBAL_WORKERS_SEMAPHORE.release()

    monkeypatch.setattr(
        at, "time", SimpleNamespace(monotonic=lambda: horloge["t"], sleep=_sleep)
    )
    try:
        with SessionLocal() as db:
            job = AudioFileJob(session_token="tok-a2", status="pending", filename=nom)
            db.add(job)
            db.commit()
            job_id = job.id
        audio_file_jobs.run_audio_file_job(job_id)
    finally:
        if horloge["sleeps"] < tours:              # rend les permis si le job a échoué tôt
            for _ in range(pris):
                at._GLOBAL_WORKERS_SEMAPHORE.release()
        try:
            (RECORDINGS_DIR / nom).unlink()
        except OSError:
            pass
        engine.dispose()
    assert horloge["sleeps"] >= tours, "le job n'a jamais attendu le permis"
    assert len(mesures) == 2 and mesures[1] > mesures[0], (
        "created_at n'a pas avancé pendant l'attente : battement invisible du thread du job"
    )
