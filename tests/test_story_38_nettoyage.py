"""Story 3.8 — nettoyage serveur de l'entretien libre (AC3.8.1, AC3.8.2)."""
from __future__ import annotations

from types import SimpleNamespace

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import InterviewSegmentJob
from app.routers.interviews_libre import _memes_tours
from app.services import interview_segment_jobs, structuration_libre
from app.services.tri_position import cle_position


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


def _mixte(tok: str) -> None:
    with SessionLocal() as db:
        db.add_all([
            InterviewSegmentJob(session_token=tok, position=0, kind="libre_turns", status="done"),
            InterviewSegmentJob(session_token=tok, position=1, kind="libre_turns", status="running"),
            InterviewSegmentJob(session_token=tok, position=0, kind="answers", status="failed"),
            InterviewSegmentJob(session_token=tok, position=1, kind="answers", status="done"),
            InterviewSegmentJob(session_token=tok, position=2, kind="answers", status="done"),
        ])
        db.commit()


def test_status_complet_filtre_sur_kind() -> None:
    _mixte("tok-38-kind")
    with SessionLocal() as db:
        libre = interview_segment_jobs.segment_jobs_status(db, "tok-38-kind", kind="libre_turns")
        rep = interview_segment_jobs.segment_jobs_status(db, "tok-38-kind", kind="answers")
        tout = interview_segment_jobs.segment_jobs_status(db, "tok-38-kind")
    assert (libre["total"], libre["done"], libre["failed"]) == (2, 1, 0)
    assert {j.kind for j in libre["jobs"]} == {"libre_turns"}
    assert (rep["total"], rep["done"], rep["failed"]) == (3, 2, 1)
    assert rep["any_failed"] and not libre["any_failed"]
    assert tout["total"] == 5  # sans kind : comportement historique


def test_tranches_en_vol_libre_ignore_les_answers() -> None:
    with SessionLocal() as db:
        db.add_all([
            InterviewSegmentJob(session_token="tok-38-vol", position=0, kind="libre_turns", status="done"),
            InterviewSegmentJob(session_token="tok-38-vol", position=0, kind="answers", status="running"),
        ])
        db.commit()
    assert structuration_libre._tranches_en_vol("tok-38-vol") == 0


def test_position_nulle_en_dernier_sans_collision_avec_zero() -> None:
    assert sorted([None, 2, 0, 1], key=cle_position) == [0, 1, 2, None]
    # None et 0 ne sont plus confondus, quel que soit l'ordre d'entrée
    assert sorted([None, 0], key=cle_position) == [0, None]
    assert sorted([0, None], key=cle_position) == [0, None]
    # départage par id à position égale
    paires = [(1, 9), (1, 3), (None, 1)]
    assert sorted(paires, key=lambda p: cle_position(*p)) == [(1, 3), (1, 9), (None, 1)]


def _tour(position, texte):
    return SimpleNamespace(position=position, interlocuteur="A", question=texte,
                           remarque=None, section_title=None, id=None)


def test_memes_tours_position_nulle_en_dernier() -> None:
    interview = SimpleNamespace(turns=[_tour(None, "dernier"), _tour(0, "premier")])
    postes = [{"interlocuteur": "A", "question": "premier"},
              {"interlocuteur": "A", "question": "dernier"}]
    assert _memes_tours(interview, postes) is True
