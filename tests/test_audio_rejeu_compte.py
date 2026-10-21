"""Revue adversariale de la fusion (M1/M2) : un rejeu de niveau job est compté
EXACTEMENT une fois, une attente de verrou occupé n'en est pas un ; abandonner
`iter_transcribe_blocks` rend les permis globaux immédiatement (sans compter sur
le ramasse-miettes). Whisper et le pool sont doublés."""
from __future__ import annotations

import threading

import numpy as np
import pytest

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.models import AudioFileJob
from app.services import audio_file_jobs, audio_transcribe as at

_PREFIXE = "rejeu_compte_test_"


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


def _job(nom: str) -> tuple[int, str]:
    complet = f"{_PREFIXE}{nom}"
    (RECORDINGS_DIR / complet).write_bytes(b"x")
    db = SessionLocal()
    try:
        job = AudioFileJob(session_token="tok-rejeu", status="pending", filename=complet)
        db.add(job)
        db.commit()
        return job.id, complet
    finally:
        db.close()


def _faux_iter_echoue_une_fois(exc_factory, k: int, n: int = 3):
    etat = {"fait": False}

    def _iter(content, block_s=None, start_index=0, stats=None):
        if stats is not None:
            stats["blocs_rejoues"] = []
            stats["duree_conteneur_s"] = 60.0 * n
            stats["duree_decodee_s"] = 60.0 * n
        for i in range(start_index, n):
            if i == k and not etat["fait"]:
                etat["fait"] = True
                raise exc_factory()
            yield i, n, f"B{i}."
    return _iter


def _lancer(monkeypatch, exc_factory, nom):
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.0)
    monkeypatch.setattr(at, "iter_transcribe_blocks",
                        _faux_iter_echoue_une_fois(exc_factory, k=1))
    job_id, _ = _job(nom)
    audio_file_jobs.run_audio_file_job(job_id)
    db = SessionLocal()
    try:
        job = db.get(AudioFileJob, job_id)
        db.expunge(job)
        return job
    finally:
        db.close()


def test_rejeu_niveau_job_compte_une_seule_fois(monkeypatch):
    job = _lancer(monkeypatch, lambda: at.TranscriptionError(at.ECHEC_TRANSCRIPTION), "rejeu")
    assert job.status == "done", job.error
    assert job.blocks == ["B0.", "B1.", "B2."]
    assert job.completude["blocs_rejoues"] == [1]       # une fois, index absolu
    assert job.completude["statut"] == "complet"


def test_verrou_occupe_n_est_pas_un_rejeu(monkeypatch):
    job = _lancer(monkeypatch, lambda: at.TranscriptionBusyError("occupé"), "busy")
    assert job.status == "done", job.error
    assert job.blocks == ["B0.", "B1.", "B2."]
    assert job.completude["blocs_rejoues"] == []


def test_abandon_du_generateur_rend_les_permis_immediatement(monkeypatch):
    sem = threading.Semaphore(4)
    monkeypatch.setattr(at, "_GLOBAL_WORKERS_SEMAPHORE", sem)
    monkeypatch.setattr(at, "GLOBAL_MAX_PARALLEL_WORKERS", 4)

    class _Pool:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def submit(self, fn, arg):
            from concurrent.futures import Future
            f = Future()
            f.set_result("t")
            return f

    monkeypatch.setattr(at, "ProcessPoolExecutor", lambda **kw: _Pool())
    monkeypatch.setattr(at, "_faster_whisper", lambda: object())
    monkeypatch.setattr(at, "_probe_duration_s", lambda c: 180.0)
    monkeypatch.setattr(at, "_decode_to_pcm16k", lambda c: np.zeros(16000 * 180, dtype=np.int16))
    monkeypatch.setattr(at, "split_pcm_blocks", lambda pcm, s: [pcm[:1], pcm[:1], pcm[:1]])

    internes = []                      # garde une référence : pas de ramasse-miettes
    vrai = at._drain_parallel

    def _espion(*a, **k):
        g = vrai(*a, **k)
        internes.append(g)
        return g

    monkeypatch.setattr(at, "_drain_parallel", _espion)

    g = at.iter_transcribe_blocks(b"x", 60)
    next(g)
    assert sem._value < 4              # des permis sont bien tenus au yield
    g.close()
    assert internes, "le drain parallèle n'a pas été utilisé"
    assert sem._value == 4


def _faux_iter_pool_puis_echec(note: list[int], n: int = 5, k: int = 2):
    """Appel 1 : le pool rejoue les blocs `note` (stats, index relatifs comme
    `_noter_rejeu`), rend les blocs < k, puis échec générique. Appel 2 : remet
    `stats` à zéro comme la vraie fonction, puis réussit."""
    etat = {"appels": 0}

    def _iter(content, block_s=None, start_index=0, stats=None):
        etat["appels"] += 1
        if stats is not None:
            stats["blocs_rejoues"] = []
            stats["duree_conteneur_s"] = 60.0 * n
            stats["duree_decodee_s"] = 60.0 * n
        for i in range(start_index, n):
            if etat["appels"] == 1 and i == k:
                stats["blocs_rejoues"].extend(note)
                raise at.TranscriptionError(at.ECHEC_TRANSCRIPTION)
            yield i, n, f"B{i}."
    return _iter


def _lancer_pool(monkeypatch, note, nom):
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.0)
    monkeypatch.setattr(at, "iter_transcribe_blocks", _faux_iter_pool_puis_echec(note))
    job_id, _ = _job(nom)
    audio_file_jobs.run_audio_file_job(job_id)
    db = SessionLocal()
    try:
        job = db.get(AudioFileJob, job_id)
        db.expunge(job)
        return job
    finally:
        db.close()


def test_rejeu_du_pool_survit_au_retry_de_niveau_job(monkeypatch):
    job = _lancer_pool(monkeypatch, [3], "poolperdu")   # pool: bloc 3 ; échec job: bloc 2
    assert job.status == "done", job.error
    assert job.completude["blocs_rejoues"] == [2, 3]


def test_bloc_rejoue_par_le_pool_puis_en_echec_job_compte_une_fois(monkeypatch):
    job = _lancer_pool(monkeypatch, [2], "poolmeme")
    assert job.status == "done", job.error
    assert job.completude["blocs_rejoues"] == [2]
