"""Revue pré-commit du lot « relances de tranches » : T1 réservation + compteur exact,
T2 tranches fraîches non proposées, T3 exception journalisée, T4 remise en file,
T5 message dédié, T6 purge du job de retranscription d'un entretien a_transcrire."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import AudioFileJob, Interview, InterviewSegmentJob
from app.services import audio_file_jobs
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


def _entretien(c, statut="fait", nb_jobs=2, raw="texte", statuts=None):
    r = c.post("/entretiens/libre/nouveau", follow_redirects=False)
    mid = int(r.headers["location"].split("/")[2])
    jeton = f"t-{mid}"
    with SessionLocal() as db:
        iv = Interview(mission_id=mid, mode="libre", status="done", interviewee_name="X",
                       raw_transcript=raw, structuration_status=statut, segment_token=jeton,
                       tranches_manquantes=nb_jobs)
        db.add(iv)
        db.commit()
        iid = iv.id
        for k in range(nb_jobs):
            db.add(InterviewSegmentJob(session_token=jeton, interview_id=iid, mission_id=mid,
                                       position=k, status=(statuts or ["failed"] * nb_jobs)[k],
                                       text="t", error="boum"))
        db.commit()
        ids = [j.id for j in db.scalars(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == jeton).order_by(InterviewSegmentJob.position))]
    return iid, ids


def test_t1_double_reservation_un_seul_traitement_et_compteur_exact(monkeypatch):
    appels = []

    def recup(db, jobs):
        appels.append([j.id for j in jobs])
        for j in jobs:
            j.status = "done"

    monkeypatch.setattr(te, "recover_stalled_or_failed_jobs", recup)
    monkeypatch.setattr(sl, "planifier_structuration", lambda i: True)
    c = TestClient(app)
    iid, (j1, j2) = _entretien(c)
    with SessionLocal() as db:
        db.execute(text("UPDATE interviews SET tranches_manquantes = 5 WHERE id = :i"), {"i": iid})
        db.commit()
        iv = db.get(Interview, iid)
        premier = te.reserver_tranches(db, iv, j1)
        second = te.reserver_tranches(db, iv, j1)       # double clic
    assert premier == [j1] and second == []
    te.relancer_tranches_en_fond(iid, premier)
    assert appels == [[j1]]
    with SessionLocal() as db:
        iv = db.get(Interview, iid)
        assert iv.tranches_manquantes == 1               # vérité = jobs, pas 5-1 ; la tranche 2 est TOUJOURS en échec
        assert iv.structuration_status == "fait"          # rien à fusionner tant qu'il en reste
        assert db.get(InterviewSegmentJob, j2).status == "failed"


def test_t2_tranche_fraiche_non_proposee_perimee_proposee():
    c = TestClient(app)
    iid, (j1, j2) = _entretien(c, nb_jobs=2, statuts=["running", "running"])
    with SessionLocal() as db:
        db.execute(text("UPDATE interview_segment_jobs SET created_at = :d WHERE id = :i"),
                   {"d": (datetime.now(UTC) - timedelta(days=2)).replace(tzinfo=None), "i": j2})
        db.commit()
        iv = db.get(Interview, iid)
        assert [t["id"] for t in te.tranches_en_echec(iv)] == [j2]
    html = c.get(f"/interviews/{iid}").text
    assert f"/tranches/{j2}/relancer" in html and f"/tranches/{j1}/relancer" not in html


def test_t3_exception_journalisee_et_tranche_relancable(monkeypatch, caplog):
    def boum(db, jobs):
        raise RuntimeError("panne")

    monkeypatch.setattr(te, "recover_stalled_or_failed_jobs", boum)
    c = TestClient(app)
    iid, (j1, j2) = _entretien(c)
    with SessionLocal() as db:
        ids = te.reserver_tranches(db, db.get(Interview, iid), j1)
    with caplog.at_level("ERROR"):
        te.relancer_tranches_en_fond(iid, ids)
    assert "Échec de la relance de tranches" in caplog.text
    with SessionLocal() as db:
        assert db.get(InterviewSegmentJob, j1).status == "failed"   # relançable, pas « running »


@pytest.mark.parametrize("statut", ["echec", "fait"])
def test_t4_tout_rattrape_remet_en_file(monkeypatch, statut):
    def recup(db, jobs):
        for j in jobs:
            j.status = "done"

    planifies = []
    monkeypatch.setattr(te, "recover_stalled_or_failed_jobs", recup)
    monkeypatch.setattr(sl, "planifier_structuration", lambda i: planifies.append(i) or True)
    c = TestClient(app)
    iid, (j1,) = _entretien(c, statut=statut, nb_jobs=1)
    r = c.post(f"/interviews/{iid}/tranches/{j1}/relancer", follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.get(Interview, iid).structuration_status == "a_traiter"
        assert db.get(Interview, iid).tranches_manquantes == 0
    assert planifies == [iid]


def test_t5_message_dedie_pour_une_tranche_indisponible():
    c = TestClient(app)
    iid, _ids = _entretien(c)
    r = c.post(f"/interviews/{iid}/tranches/999999/relancer", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?tranche=indisponible")
    html = c.get(r.headers["location"]).text
    assert "Ce passage n'existe plus ou est déjà en cours de traitement." in html
    assert "déjà en file" not in html.split("Ce passage")[0].split("role=\"status\"")[-1]


def test_t6_purge_garde_le_job_dun_entretien_a_transcrire():
    c = TestClient(app)
    iid_att, _ = _entretien(c, statut="a_transcrire", nb_jobs=0, raw="")
    iid_fait, _ = _entretien(c, statut="fait", nb_jobs=0)
    with SessionLocal() as db:
        for tok, iid in (("t6-att", iid_att), ("t6-fait", iid_fait)):
            db.add(AudioFileJob(session_token=tok, filename="", filenames=[], interview_id=iid,
                                status="done", block_seconds=60, blocks=[]))
        db.commit()
        db.execute(text("UPDATE audio_file_jobs SET created_at = '2000-01-01 00:00:00'"))
        db.commit()
        audio_file_jobs.purge_stale_audio_file_jobs(db)
        restants = {j.session_token for j in db.scalars(select(AudioFileJob))}
    assert "t6-att" in restants and "t6-fait" not in restants


def _faux_recup_done(db, jobs):
    for j in jobs:
        j.status = "done"


def test_rejeu_si_structuration_en_cours_pendant_la_relance(monkeypatch):
    import threading
    import time as _t
    monkeypatch.setattr(te, "recover_stalled_or_failed_jobs", _faux_recup_done)
    monkeypatch.setattr(te, "ATTENTE_REJEU_PAS_S", 0.02)
    planifies = []
    monkeypatch.setattr(sl, "planifier_structuration", lambda i: planifies.append(i) or True)
    c = TestClient(app)
    iid, (j1,) = _entretien(c, statut="en_cours", nb_jobs=1)

    def fin_structuration():
        _t.sleep(0.3)
        with SessionLocal() as db:
            db.execute(text("UPDATE interviews SET structuration_status = 'fait' WHERE id = :i"),
                       {"i": iid})
            db.commit()

    th = threading.Thread(target=fin_structuration)
    th.start()
    te.relancer_tranches_en_fond(iid, [j1])
    th.join(10)
    assert planifies == [iid]
    with SessionLocal() as db:
        assert db.get(Interview, iid).structuration_status == "a_traiter"


def test_pas_de_rejeu_si_structuration_toujours_en_cours_a_lecheance(monkeypatch):
    monkeypatch.setattr(te, "recover_stalled_or_failed_jobs", _faux_recup_done)
    monkeypatch.setattr(te, "ATTENTE_REJEU_S", 0.1)
    monkeypatch.setattr(te, "ATTENTE_REJEU_PAS_S", 0.02)
    planifies = []
    monkeypatch.setattr(sl, "planifier_structuration", lambda i: planifies.append(i) or True)
    c = TestClient(app)
    iid, (j1,) = _entretien(c, statut="en_cours", nb_jobs=1)
    te.relancer_tranches_en_fond(iid, [j1])
    assert planifies == []


def test_pas_de_rejeu_inutile_pour_fait_sans_tranche_rattrapee(monkeypatch):
    monkeypatch.setattr(te, "recover_stalled_or_failed_jobs", _faux_recup_done)
    planifies = []
    monkeypatch.setattr(sl, "planifier_structuration", lambda i: planifies.append(i) or True)
    c = TestClient(app)
    iid, _ids = _entretien(c, statut="fait", nb_jobs=1, statuts=["done"])
    te.relancer_tranches_en_fond(iid, [])
    assert planifies == []
    with SessionLocal() as db:
        assert db.get(Interview, iid).structuration_status == "fait"


# ---------------- S-a / S-c / S-d / S-e / S-b ----------------
def test_sa_worker_vivant_refuse_la_relance_de_tranche(monkeypatch):
    monkeypatch.setattr(te, "recover_stalled_or_failed_jobs",
                        lambda db, jobs: pytest.fail("ne doit pas tourner en parallèle"))
    from app.routers import interviews_libre
    monkeypatch.setattr(interviews_libre, "peut_relancer", lambda iv: False)  # worker vivant
    c = TestClient(app)
    iid, (j1, j2) = _entretien(c, statut="en_cours")
    for url in (f"/interviews/{iid}/tranches/{j1}/relancer", f"/interviews/{iid}/tranches/relancer"):
        r = c.post(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("?tranche=indisponible")
    with SessionLocal() as db:
        assert db.get(InterviewSegmentJob, j1).status == "failed"   # rien réservé


def test_sc_a_transcrire_avec_texte_badge_recalcule(monkeypatch):
    monkeypatch.setattr(te, "recover_stalled_or_failed_jobs", _faux_recup_done)
    monkeypatch.setattr(sl, "planifier_structuration", lambda i: True)
    c = TestClient(app)
    iid, (j1,) = _entretien(c, statut="a_transcrire", nb_jobs=1)
    with SessionLocal() as db:
        db.execute(text("UPDATE interviews SET tranches_manquantes = 4 WHERE id = :i"), {"i": iid})
        db.commit()
        ids = te.reserver_tranches(db, db.get(Interview, iid), j1)
    te.relancer_tranches_en_fond(iid, ids)
    with SessionLocal() as db:
        iv = db.get(Interview, iid)
        assert iv.structuration_status == "a_traiter" and iv.tranches_manquantes == 0


def test_sd_position_illisible_audio_complet_zero_manquante(monkeypatch):
    from types import SimpleNamespace
    from app.routers import interviews_libre
    monkeypatch.setattr(interviews_libre.mission_backups, "references_audio_sures",
                        lambda m, c, segs, d: ("x.webm", segs))
    ident = {"audio_backup_path": "x", "audio_segments": '[{"position": "abc"}, {"position": 1}]'}
    assert interviews_libre._etat_audio(SimpleNamespace(id=1), ident, 2) == (True, 0)


def test_se_doublon_de_jeton_complete_aussi_le_compte(monkeypatch):
    monkeypatch.setattr(sl, "planifier_structuration", lambda i: True)
    from app.routers import interviews_libre
    monkeypatch.setattr(interviews_libre, "planifier_structuration", lambda i: True)
    monkeypatch.setattr(interviews_libre, "_etat_audio", lambda m, i, a=0: (True, 2))
    c = TestClient(app)
    r = c.post("/entretiens/libre/nouveau", follow_redirects=False)
    mid = int(r.headers["location"].split("/")[2])
    with SessionLocal() as db:
        iv = Interview(mission_id=mid, mode="libre", status="done", interviewee_name="X",
                       raw_transcript="", structuration_status="a_transcrire",
                       segment_token="se-tok", tranches_manquantes=7)
        db.add(iv)
        db.commit()
        iid = iv.id
    c.post(f"/missions/{mid}/interviews/record-libre/enregistrer",
           data={"transcript": "Un texte transcrit.", "session_token": "se-tok",
                 "transcription_incomplete": "1"}, follow_redirects=False)
    with SessionLocal() as db:
        iv = db.get(Interview, iid)
        assert iv.raw_transcript == "Un texte transcrit."
        assert iv.tranches_manquantes == 2


def test_sb_confirm_sur_les_variantes_qui_reecrivent_l_apercu():
    c = TestClient(app)
    iid, _ids = _entretien(c, statut="fait")
    with SessionLocal() as db:
        from app.models import InterviewTurn
        db.add(InterviewTurn(interview_id=iid, position=0, interlocuteur="A", question="q",
                             remarque="", section_title=""))
        db.commit()
    html = c.get(f"/interviews/{iid}").text
    bloc = html.split("tranches/relancer")[1].split("</form>")[0]
    assert "confirm(" in bloc and "réécrit" in bloc
