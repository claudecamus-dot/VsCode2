"""Lot 4 W9 (suite) : relances de tranches en échec depuis la fiche, protection du
job de retranscription, et mineurs (abandon parallèle, estimation, démarrage de thread)."""
from __future__ import annotations

import collections
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import AudioFileJob, Interview, InterviewSegmentJob, InterviewTurn, Mission
from app.routers import interviews_retranscription
from app.services import audio_file_jobs
from app.services import audio_transcribe as at
from app.services import structuration_libre as sl
from app.services import tranches_echec as te


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


def _mission(c) -> int:
    r = c.post("/entretiens/libre/nouveau", follow_redirects=False)
    return int(r.headers["location"].split("/")[2])


def _libre(db, mid, **kw):
    iv = Interview(mission_id=mid, mode="libre", status="done", interviewee_name="X", **kw)
    db.add(iv)
    db.commit()
    return iv.id


def _fiche_avec_echec(c, statut, avec_tour=False, manquantes=1):
    mid = _mission(c)
    jeton = f"te-{statut}-{avec_tour}-{mid}"
    with SessionLocal() as db:
        iid = _libre(db, mid, raw_transcript="du texte", structuration_status=statut,
                     segment_token=jeton, tranches_manquantes=manquantes)
        db.add(InterviewSegmentJob(session_token=jeton, interview_id=iid, mission_id=mid,
                                   position=1, status="failed", text="t", error="boum"))
        if avec_tour:
            db.add(InterviewTurn(interview_id=iid, position=0, interlocuteur="A",
                                 question="q", remarque="", section_title=""))
        db.commit()
        jid = db.scalars(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == jeton)).first().id
    return iid, jid


@pytest.mark.parametrize("statut,avec_tour", [
    ("fait", True), ("fait", False), ("a_transcrire", False), ("echec", False),
    ("a_traiter", False), ("en_cours", False),
])
def test_fiche_boutons_visibles_tant_quune_tranche_echoue(statut, avec_tour):
    c = TestClient(app)
    iid, jid = _fiche_avec_echec(c, statut, avec_tour)
    html = c.get(f"/interviews/{iid}").text
    assert "Relancer cette tranche" in html, statut
    assert f"/interviews/{iid}/tranches/{jid}/relancer" in html
    assert f"/interviews/{iid}/tranches/relancer" in html
    assert "1 tranche en échec" in html


def test_fiche_sans_tranche_en_echec_pas_de_boutons():
    c = TestClient(app)
    mid = _mission(c)
    with SessionLocal() as db:
        iid = _libre(db, mid, raw_transcript="t", structuration_status="fait")
    html = c.get(f"/interviews/{iid}").text
    assert "Relancer cette tranche" not in html and "tranches-echec" not in html


def test_route_individuelle_decompte_et_reprogramme_la_fusion(monkeypatch):
    def recup(db, jobs):
        for j in jobs:
            j.status = "done"
            j.error = None

    monkeypatch.setattr(te, "recover_stalled_or_failed_jobs", recup)
    planifies = []
    monkeypatch.setattr(sl, "planifier_structuration", lambda i: planifies.append(i) or True)
    c = TestClient(app)
    iid, jid = _fiche_avec_echec(c, "fait", avec_tour=True)
    r = c.post(f"/interviews/{iid}/tranches/{jid}/relancer", follow_redirects=False)
    assert r.status_code == 303 and "refusee" not in r.headers["location"]
    with SessionLocal() as db:
        iv = db.get(Interview, iid)
        assert iv.tranches_manquantes == 0
        assert iv.structuration_status == "a_traiter"
    assert planifies == [iid]
    assert "Relancer cette tranche" not in c.get(f"/interviews/{iid}").text


def test_route_individuelle_refuse_une_tranche_inconnue(monkeypatch):
    monkeypatch.setattr(te, "recover_stalled_or_failed_jobs",
                        lambda db, jobs: pytest.fail("appelée"))
    c = TestClient(app)
    iid, jid = _fiche_avec_echec(c, "fait", avec_tour=True)
    r = c.post(f"/interviews/{iid}/tranches/{jid + 9999}/relancer", follow_redirects=False)
    assert r.status_code == 303 and "tranche=indisponible" in r.headers["location"]


def test_route_globale_sur_a_transcrire(monkeypatch):
    def recup(db, jobs):
        for j in jobs:
            j.status = "done"

    monkeypatch.setattr(te, "recover_stalled_or_failed_jobs", recup)
    monkeypatch.setattr(sl, "planifier_structuration", lambda i: True)
    c = TestClient(app)
    iid, jid = _fiche_avec_echec(c, "a_transcrire", manquantes=3)
    r = c.post(f"/interviews/{iid}/tranches/relancer", follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.get(InterviewSegmentJob, jid).status == "done"
        # a_transcrire avec texte : remis en file, compteur recalculé depuis les jobs
        assert db.get(Interview, iid).tranches_manquantes == 0   # recalculé à la bascule (S-c)
        assert db.get(Interview, iid).structuration_status == "a_traiter"  # texte présent: remis en file (T4)


def test_d_purge_ne_supprime_pas_un_job_de_retranscription():
    c = TestClient(app)
    mid = _mission(c)
    with SessionLocal() as db:
        iid = _libre(db, mid, raw_transcript="t")
        for tok, ivid in (("d-ret", iid), ("d-import", None)):
            db.add(AudioFileJob(session_token=tok, filename="", filenames=[],
                                interview_id=ivid, status="done", block_seconds=60, blocks=[]))
        db.commit()
        db.add(AudioFileJob(session_token="d-ret-vieux", filename="", filenames=[],
                            interview_id=iid, status="done", block_seconds=60, blocks=[]))
        db.commit()
        db.execute(text("UPDATE audio_file_jobs SET created_at = datetime('now', '-10 days')"))
        db.execute(text("UPDATE audio_file_jobs SET created_at = '2000-01-01 00:00:00' "
                        "WHERE session_token = 'd-ret-vieux'"))
        db.commit()
        audio_file_jobs.purge_stale_audio_file_jobs(db)
        restants = {j.session_token for j in db.scalars(select(AudioFileJob))}
    assert "d-ret" in restants                     # 10 j : protégé (attend la confirmation)
    assert "d-import" not in restants              # import abandonné : purgé à 7 j
    assert "d-ret-vieux" not in restants           # retranscription oubliée depuis des années


def test_trou_de_positions_ignore_hors_bornes():
    f = interviews_retranscription._trou_de_positions
    assert f(SimpleNamespace(audio_segments=[{"position": 0}, {"position": 5000}])) is False
    assert f(SimpleNamespace(audio_segments=[{"position": -4}, {"position": 0},
                                             {"position": 1}])) is False


def test_demarrage_du_thread_en_echec_donne_503_coherent(monkeypatch):
    from app.routers import interviews_audio

    class FauxThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("thread impossible")

    monkeypatch.setattr(at, "_faster_whisper", lambda: object())
    monkeypatch.setattr(interviews_audio.threading, "Thread", FauxThread)
    r = TestClient(app).post("/audio/transcribe-segment",
                             files={"file": ("s.webm", b"thr", "audio/webm")})
    assert r.status_code == 503 and r.json()["code"] == "busy"


def test_a_abandon_propage_au_parallele(monkeypatch):
    import numpy as np

    monkeypatch.setattr(at, "_decode_to_pcm16k", lambda c: np.zeros(16000 * 120, dtype="float32"))
    vus = []

    def drain(chunks, index, total, workers, **kw):
        for i in range(index, total):
            vus.append(i)
            yield i, f"t{i}"

    monkeypatch.setattr(at, "_drain_parallel", drain)
    etat = {"n": 0}

    def abandonne():
        etat["n"] += 1
        return etat["n"] > 1

    with pytest.raises(at.TranscriptionAbandonnee):
        at._transcribe_parallel(b"x", 120.0, at.LIVE, abandonne=abandonne)
    assert 0 < len(vus) < 4
    vus.clear()
    with pytest.raises(at.TranscriptionAbandonnee):
        at._transcribe_parallel(b"x", 120.0, at.LIVE, abandonne=lambda: True)
    assert vus == []


def test_bc_parallele_compte_lattente_et_estime_le_retry_after(monkeypatch):
    import numpy as np

    monkeypatch.setattr(at, "_GLOBAL_WORKERS_SEMAPHORE", threading.Semaphore(0))
    monkeypatch.setattr(at, "SEGMENT_LOCK_TIMEOUT_S", 0.6)
    monkeypatch.setattr(at, "_DUREES_RECENTES", collections.deque([50.0], maxlen=8))
    vu = {}

    def appel():
        try:
            list(at._drain_parallel([np.zeros(10)] * 2, 0, 2, 2, at.LIVE))
        except at.TranscriptionBusyError as e:
            vu["exc"] = e

    t = threading.Thread(target=appel)
    t.start()
    time.sleep(0.25)
    vu["attente"] = at._ATTENTE_LIVE
    t.join(10)
    assert vu["attente"] == 1
    assert vu["exc"].retry_after_s == 50.0
    assert at._ATTENTE_LIVE == 0
