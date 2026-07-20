"""Tests du traitement asynchrone des tranches d'entretien libre (Palier 2 —
`docs/reflexions/enregistrement-segmente-30min.md` §4).

Couvre : la tâche de fond `run_segment_job` (succès/échec), la fusion des tours
de plusieurs jobs, l'agrégat de statut, et le flux HTTP complet (création de
job en tâche de fond, écran d'attente quand des jobs sont en cours, fusion à la
finalisation, fallback synchrone quand un job échoue ou qu'aucun job n'existe).

Comme `test_interview_libre.py`, l'IA (`extract_turns_from_text`) est
monkeypatchée — aucun appel réseau. Le `TestClient` de Starlette exécute les
`BackgroundTasks` de façon synchrone dans la requête, donc un POST de création
de job traite le job avant de rendre la main.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import InterviewSegmentJob, Mission
from app.routers import interviews as interviews_router
from app.services import interview_segment_jobs


def setup_module() -> None:
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


def _turns_payload(name: str, question: str) -> dict:
    return {
        "turns": [
            {"interlocuteur": name, "question": question, "remarque": "", "section_title": ""},
        ],
        "identity": {"interviewee_name": name, "interviewee_role": "", "interviewee_entity": ""},
    }


def _make_draft_mission() -> int:
    db = SessionLocal()
    try:
        mission = Mission(name="Mission Palier 2", is_draft=True)
        db.add(mission)
        db.commit()
        return mission.id
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Service — tâche de fond
# --------------------------------------------------------------------------- #
def test_run_segment_job_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_segment_jobs, "extract_turns_from_text",
        lambda text: _turns_payload("Alice", "Comment ça va ?"),
    )
    db = SessionLocal()
    job = InterviewSegmentJob(session_token="tok-success", position=0, status="pending")
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    interview_segment_jobs.run_segment_job(job_id, "un texte de tranche")

    db = SessionLocal()
    refreshed = db.get(InterviewSegmentJob, job_id)
    assert refreshed.status == "done"
    assert refreshed.turns_result["turns"][0]["interlocuteur"] == "Alice"
    assert refreshed.error is None
    db.close()


def test_run_segment_job_failure_records_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(text):
        raise interview_segment_jobs.InterviewLibreExtractAIError("Ollama injoignable")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _boom)
    db = SessionLocal()
    job = InterviewSegmentJob(session_token="tok-fail", position=0, status="pending")
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    interview_segment_jobs.run_segment_job(job_id, "texte")

    db = SessionLocal()
    refreshed = db.get(InterviewSegmentJob, job_id)
    assert refreshed.status == "failed"
    assert "Ollama" in refreshed.error
    assert refreshed.turns_result is None
    db.close()


# --------------------------------------------------------------------------- #
# Service — fusion + statut
# --------------------------------------------------------------------------- #
def test_merge_segment_turns_orders_by_position_and_appends_tail() -> None:
    j0 = InterviewSegmentJob(session_token="t", position=0,
                             turns_result=_turns_payload("Alice", "Q0"))
    j1 = InterviewSegmentJob(session_token="t", position=1,
                             turns_result=_turns_payload("Bob", "Q1"))
    tail = _turns_payload("Carol", "Qtail")
    # Volontairement dans le désordre pour vérifier le tri par position.
    merged = interview_segment_jobs.merge_segment_turns([j1, j0], tail)
    questions = [t["question"] for t in merged["turns"]]
    assert questions == ["Q0", "Q1", "Qtail"]
    # Première identité non vide (job position 0) l'emporte.
    assert merged["identity"]["interviewee_name"] == "Alice"


def test_merge_segment_turns_without_tail() -> None:
    j0 = InterviewSegmentJob(session_token="t", position=0,
                             turns_result=_turns_payload("Alice", "Q0"))
    merged = interview_segment_jobs.merge_segment_turns([j0], None)
    assert [t["question"] for t in merged["turns"]] == ["Q0"]


def test_segment_jobs_status_counts() -> None:
    db = SessionLocal()
    for pos, status in enumerate(["done", "done", "running"]):
        db.add(InterviewSegmentJob(session_token="tok-status", position=pos, status=status))
    db.commit()

    status = interview_segment_jobs.segment_jobs_status(db, "tok-status")
    assert status["total"] == 3
    assert status["done"] == 2
    assert status["all_done"] is False
    assert status["any_failed"] is False
    db.close()


def test_segment_jobs_status_all_done() -> None:
    db = SessionLocal()
    for pos in range(2):
        db.add(InterviewSegmentJob(session_token="tok-alldone", position=pos, status="done"))
    db.commit()
    status = interview_segment_jobs.segment_jobs_status(db, "tok-alldone")
    assert status["all_done"] is True
    assert status["any_failed"] is False
    db.close()


def test_segment_jobs_status_empty_token() -> None:
    db = SessionLocal()
    status = interview_segment_jobs.segment_jobs_status(db, "")
    assert status["total"] == 0
    assert status["all_done"] is False
    db.close()


def test_delete_segment_jobs() -> None:
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="tok-del", position=0, status="done"))
    db.commit()
    interview_segment_jobs.delete_segment_jobs(db, "tok-del")
    remaining = db.scalars(
        select(InterviewSegmentJob).where(InterviewSegmentJob.session_token == "tok-del")
    ).all()
    assert remaining == []
    db.close()


# --------------------------------------------------------------------------- #
# HTTP — création de job (tâche de fond exécutée par TestClient) + statut
# --------------------------------------------------------------------------- #
def test_create_segment_job_processes_in_background(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        interview_segment_jobs, "extract_turns_from_text",
        lambda text: _turns_payload("Alice", "Q"),
    )
    resp = client.post(
        "/interviews/segment-jobs",
        data={"session_token": "http-tok", "position": "0", "text": "tranche de texte"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    # Le BackgroundTask a tourné pendant la requête -> statut done.
    status = client.get("/interviews/segment-jobs/status", params={"session_token": "http-tok"})
    body = status.json()
    assert body["total"] == 1
    assert body["done"] == 1
    assert body["all_done"] is True


def test_status_endpoint_unknown_token_is_empty(client: TestClient) -> None:
    resp = client.get("/interviews/segment-jobs/status", params={"session_token": "does-not-exist"})
    body = resp.json()
    assert body == {"total": 0, "done": 0, "failed": 0, "all_done": False, "any_failed": False}


# --------------------------------------------------------------------------- #
# HTTP — record_libre : attente vs fusion vs fallback synchrone
# --------------------------------------------------------------------------- #
def test_record_libre_shows_wait_screen_when_jobs_pending(client: TestClient) -> None:
    mission_id = _make_draft_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="wait-tok", position=0, status="running"))
    db.commit()
    db.close()

    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "une longue transcription", "session_token": "wait-tok"},
    )
    assert resp.status_code == 200
    assert "Traitement des tranches" in resp.text
    # L'écran d'attente poste la finalisation vers from-jobs.
    assert "record-libre/from-jobs" in resp.text


def test_record_libre_from_jobs_merges_done_turns(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _make_draft_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token="merge-tok", position=0, status="done",
        turns_result=_turns_payload("Alice", "Question issue du job"),
    ))
    db.commit()
    db.close()

    # Reliquat final traité en synchrone à la finalisation.
    monkeypatch.setattr(
        interviews_router, "extract_turns_from_text",
        lambda text: _turns_payload("Bob", "Question du reliquat"),
    )
    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre/from-jobs",
        data={
            "transcript": "transcription complète",
            "session_token": "merge-tok",
            "segment_tail": "reliquat non couvert",
        },
    )
    assert resp.status_code == 200
    assert "Question issue du job" in resp.text
    assert "Question du reliquat" in resp.text
    # Jobs consommés puis supprimés.
    db = SessionLocal()
    remaining = db.scalars(
        select(InterviewSegmentJob).where(InterviewSegmentJob.session_token == "merge-tok")
    ).all()
    assert remaining == []
    db.close()


def test_record_libre_no_jobs_uses_synchronous_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-régression : un entretien court (aucun job) suit le chemin
    synchrone d'avant le Palier 2."""
    mission_id = _make_draft_mission()
    monkeypatch.setattr(
        interviews_router, "extract_turns_from_text",
        lambda text: _turns_payload("Alice", "Extraction synchrone"),
    )
    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "un entretien court", "session_token": ""},
    )
    assert resp.status_code == 200
    assert "Extraction synchrone" in resp.text


def test_record_libre_falls_back_to_synchronous_when_job_failed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un seul job en échec -> on retraite tout en synchrone (garantie de
    correction), pas d'écran d'attente."""
    mission_id = _make_draft_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="failed-tok", position=0, status="failed",
                               error="timeout"))
    db.commit()
    db.close()

    calls = {"n": 0}

    def _sync(text):
        calls["n"] += 1
        return _turns_payload("Alice", "Repli synchrone complet")

    monkeypatch.setattr(interviews_router, "extract_turns_from_text", _sync)
    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "transcription complète", "session_token": "failed-tok"},
    )
    assert resp.status_code == 200
    assert "Repli synchrone complet" in resp.text
    assert calls["n"] == 1  # traité sur la transcription entière, une fois
