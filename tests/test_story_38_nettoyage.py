"""Story 3.8 — nettoyage serveur de l'entretien libre (AC3.8.1, AC3.8.2).

AC3.8.3 (B-14) : clos sans changement de code (aucun cas clair, rapport source
perdu) — rien à tester.

Isolation : jetons uniques et nettoyage PAR JETON, aucun `DB_PATH.unlink()`.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal, engine, init_db
from app.main import app
from app.models import (
    KIND_ANSWERS,
    KIND_LIBRE_TURNS,
    KIND_TOUS,
    Interview,
    InterviewSegmentJob,
    Mission,
)
from app.routers.interviews_libre import _memes_tours
from app.services import interview_segment_jobs, mission_backups, structuration_libre
from app.services.tranches_echec import tranches_en_echec
from app.services.tri_position import cle_position


def setup_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    init_db()


@pytest.fixture
def jeton():
    """Jeton unique ; ses jobs sont supprimés en fin de test."""
    tok = f"tok-38-{id(object())}"
    yield tok
    with SessionLocal() as db:
        db.execute(delete(InterviewSegmentJob).where(InterviewSegmentJob.session_token == tok))
        db.commit()


def _mixte(tok: str) -> None:
    with SessionLocal() as db:
        db.add_all([
            InterviewSegmentJob(session_token=tok, position=0, kind=KIND_LIBRE_TURNS,
                                status="done", text="t"),
            InterviewSegmentJob(session_token=tok, position=1, kind=KIND_LIBRE_TURNS,
                                status="running", text="t"),
            InterviewSegmentJob(session_token=tok, position=0, kind=KIND_ANSWERS,
                                status="failed", text="t"),
            InterviewSegmentJob(session_token=tok, position=1, kind=KIND_ANSWERS,
                                status="done", text="t"),
            InterviewSegmentJob(session_token=tok, position=2, kind=KIND_ANSWERS,
                                status="done", text="t"),
        ])
        db.commit()


# ------------------------------------------------------------------ AC3.8.1
def test_status_complet_filtre_sur_kind(jeton) -> None:
    _mixte(jeton)
    st = interview_segment_jobs.segment_jobs_status
    with SessionLocal() as db:
        libre = st(db, jeton, kind=KIND_LIBRE_TURNS)
        rep = st(db, jeton, kind=KIND_ANSWERS)
        tout = st(db, jeton, kind=KIND_TOUS)
    assert (libre["total"], libre["done"], libre["failed"]) == (2, 1, 0)
    assert {j.kind for j in libre["jobs"]} == {KIND_LIBRE_TURNS}
    assert (rep["total"], rep["done"], rep["failed"]) == (3, 2, 1)
    assert rep["any_failed"] and not libre["any_failed"]
    assert tout["total"] == 5


def test_kind_est_obligatoire_et_par_mot_cle(jeton) -> None:
    with SessionLocal() as db, pytest.raises(TypeError):
        interview_segment_jobs.segment_jobs_status(db, jeton)
    with SessionLocal() as db, pytest.raises(TypeError):
        interview_segment_jobs.segment_jobs_status(db, jeton, KIND_ANSWERS)


def test_kind_vide_ou_inconnu_ne_lit_jamais_toutes_les_natures(jeton) -> None:
    _mixte(jeton)
    st = interview_segment_jobs.segment_jobs_status
    with SessionLocal() as db:
        assert st(db, jeton, kind=None)["total"] == 0  # MUTANT: None ne doit pas valoir « tous »
        assert st(db, jeton, kind="")["total"] == 0
    r = TestClient(app).get(f"/interviews/segment-jobs/status?session_token={jeton}&kind=bidon")
    assert r.status_code == 422


def test_delete_segment_jobs_kind_obligatoire(jeton) -> None:
    with SessionLocal() as db, pytest.raises(TypeError):
        interview_segment_jobs.delete_segment_jobs(db, jeton)
    _mixte(jeton)
    with SessionLocal() as db:
        interview_segment_jobs.delete_segment_jobs(db, jeton, kind=KIND_TOUS)
        assert db.scalar(select(InterviewSegmentJob.id).where(
            InterviewSegmentJob.session_token == jeton)) is None


def test_route_status_filtre_sur_kind(jeton) -> None:
    _mixte(jeton)
    c = TestClient(app)
    base = f"/interviews/segment-jobs/status?session_token={jeton}"
    r_libre = c.get(base + f"&kind={KIND_LIBRE_TURNS}").json()
    r_rep = c.get(base + f"&kind={KIND_ANSWERS}").json()
    r_tout = c.get(base).json()  # défaut = comportement historique
    assert (r_libre["total"], r_libre["any_failed"]) == (2, False)
    assert (r_rep["total"], r_rep["any_failed"]) == (3, True)
    assert r_tout["total"] == 5


def test_tranches_en_vol_libre_ignore_les_answers(jeton) -> None:
    with SessionLocal() as db:
        db.add_all([
            InterviewSegmentJob(session_token=jeton, position=0, kind=KIND_LIBRE_TURNS,
                                status="done"),
            InterviewSegmentJob(session_token=jeton, position=0, kind=KIND_ANSWERS,
                                status="running"),
        ])
        db.commit()
    assert structuration_libre._tranches_en_vol(jeton) == 0


def test_fiche_libre_tranches_en_echec_ignore_les_answers(jeton) -> None:
    with SessionLocal() as db:
        m = Mission(name="S38")
        db.add(m)
        db.flush()
        iv = Interview(mission_id=m.id, mode="libre", status="done",
                       interviewee_name="A", segment_token=jeton)
        db.add(iv)
        db.add_all([
            InterviewSegmentJob(session_token=jeton, position=1, kind=KIND_ANSWERS,
                                status="failed", text="t", error="boum"),
            InterviewSegmentJob(session_token=jeton, position=2, kind=KIND_LIBRE_TURNS,
                                status="failed", text="t", error="ko"),
        ])
        db.commit()
        echecs = tranches_en_echec(iv)
        assert [t["position"] for t in echecs] == [2]
        iid = iv.id
        ids = {j.kind: j.id for j in db.scalars(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == jeton))}
    page = TestClient(app).get(f"/interviews/{iid}").text
    assert "1 passage en échec" in page
    assert f"/interviews/{iid}/tranches/{ids[KIND_LIBRE_TURNS]}/relancer" in page
    assert f"/tranches/{ids[KIND_ANSWERS]}/relancer" not in page


def test_delete_segment_jobs_ne_supprime_que_le_kind(jeton) -> None:
    _mixte(jeton)
    with SessionLocal() as db:
        interview_segment_jobs.delete_segment_jobs(db, jeton, kind=KIND_LIBRE_TURNS)
        restants = list(db.scalars(select(InterviewSegmentJob.kind).where(
            InterviewSegmentJob.session_token == jeton)))
    assert restants == [KIND_ANSWERS] * 3


# ------------------------------------------------------------------ AC3.8.2
def test_position_nulle_en_premier_distincte_de_zero() -> None:
    assert sorted([2, None, 0, 1], key=cle_position) == [None, 0, 1, 2]
    assert cle_position(None) != cle_position(0)
    assert sorted([0, None], key=cle_position) == [None, 0]
    assert sorted([None, 0], key=cle_position) == [None, 0]
    # départage : id nul avant id réel, distinct de 0
    paires = [(1, 9), (1, None), (1, 0), (None, 5)]
    assert sorted(paires, key=lambda p: cle_position(*p)) == [
        (None, 5), (1, None), (1, 0), (1, 9)]


def _tour(position, texte):
    return SimpleNamespace(position=position, interlocuteur="A", question=texte,
                           remarque=None, section_title=None, id=None)


def test_memes_tours_position_nulle_en_premier() -> None:
    interview = SimpleNamespace(turns=[_tour(0, "second"), _tour(None, "premier")])
    postes = [{"interlocuteur": "A", "question": "premier"},
              {"interlocuteur": "A", "question": "second"}]
    assert _memes_tours(interview, postes) is True


def test_signature_retranscription_position_nulle_en_premier() -> None:
    from app.routers import interviews_retranscription as rt
    fn = rt._signature_contenu
    a = SimpleNamespace(raw_transcript="x", turns=[_tour(0, "b"), _tour(None, "a")])
    b = SimpleNamespace(raw_transcript="x", turns=[_tour(None, "a"), _tour(0, "b")])
    assert fn(a) == fn(b)
    assert [t[1] for t in fn(a)[1]] == ["a", "b"]


def _interview_segments(segments, backup):
    with SessionLocal() as db:
        m = Mission(name="S38-seg")
        db.add(m)
        db.flush()
        iv = Interview(mission_id=m.id, mode="libre", status="done",
                       interviewee_name="A", audio_segments=segments,
                       audio_backup_path=backup)
        db.add(iv)
        db.commit()
        return m.id, iv.id


def test_lister_backups_segment_sans_position_a_cote_de_zero(tmp_path: Path) -> None:
    u = uuid.uuid4().hex[:8]
    nn, n0, n1 = f"{u}_s_none.webm", f"{u}_s_0.webm", f"{u}_s_1.webm"
    for n in (nn, n0, n1):
        (tmp_path / n).write_bytes(b"a")
    # ordre d'entrée volontairement défavorable
    mid, _ = _interview_segments(
        [{"filename": n1, "position": 1},
         {"filename": n0, "position": 0},
         {"filename": nn}], n1)
    with SessionLocal() as db:
        res = mission_backups.lister_backups(db.get(Mission, mid), tmp_path)
        lignes = res["rattaches"] if isinstance(res, dict) else res
        par_nom = {r["filename"]: r for r in lignes}
    # rang d'affichage calculé sur l'ordre des positions (None d'abord), quel
    # que soit l'ordre d'entrée ou l'ordre chronologique de la liste rendue
    assert par_nom[nn]["position"] is None  # jamais de rang fabriqué
    # le rang ne compte que les tranches à position réelle : « 1/2 », « 2/2 »
    assert par_nom[n0]["position"] == 0
    assert par_nom[n1]["position"] == 1
    assert all(r["nb_tranches"] == 2 for r in lignes)


def test_delete_backup_garde_la_vraie_derniere_tranche(monkeypatch, tmp_path: Path) -> None:
    from app.routers import interviews_backup
    monkeypatch.setattr(interviews_backup, "RECORDINGS_DIR", tmp_path)
    u = uuid.uuid4().hex[:8]
    nn, n0, n1, n2 = (f"{u}_d_{k}.webm" for k in ("none", "0", "1", "2"))
    for n in (nn, n0, n1, n2):
        (tmp_path / n).write_bytes(b"a")
    mid, iid = _interview_segments(
        [{"filename": n2, "position": 2},
         {"filename": nn},
         {"filename": n0, "position": 0},
         {"filename": n1, "position": 1}], n2)
    c = TestClient(app)
    r = c.post(f"/missions/{mid}/interviews/record/backup/{n1}/delete",
               follow_redirects=False)
    assert r.status_code == 303, r.text
    with SessionLocal() as db:
        iv = db.get(Interview, iid)
        assert [x["filename"] for x in iv.audio_segments] == [nn, n0, n2]
        assert iv.audio_backup_path == n2  # la vraie dernière tranche
    # supprimer la plus haute : retombe sur la plus haute RESTANTE (0), pas sur le None
    r = c.post(f"/missions/{mid}/interviews/record/backup/{n2}/delete",
               follow_redirects=False)
    assert r.status_code == 303, r.text
    with SessionLocal() as db:
        assert db.get(Interview, iid).audio_backup_path == n0
