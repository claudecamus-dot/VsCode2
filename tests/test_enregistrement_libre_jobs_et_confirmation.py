"""Lot « serveur et données » (revue 5 voix du 2026-10-19) : enregistrement direct
d'un entretien libre (audio tronqué, from-jobs, jeton, champs tolérants), nettoyage
des brouillons, confirmation de retranscription, avertissement de la liste."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.main import app
from app.models import (
    AudioFileJob,
    Interview,
    InterviewSegmentJob,
    InterviewTurn,
    Mission,
)
from app.routers.missions import _compter_brouillons_vides, _draft_vide
from app.templating import templates


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


def _mission(client, brouillon=False) -> int:
    r = client.post("/entretiens/libre/nouveau", follow_redirects=False)
    mid = int(r.headers["location"].split("/")[2])
    with SessionLocal() as db:
        db.get(Mission, mid).is_draft = brouillon
        db.commit()
    return mid


def _audio(mid: int, nom: str) -> str:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    f = f"{mid}_{nom}"
    (RECORDINGS_DIR / f).write_bytes(b"x")
    return f


def _post(client, mid, transcript, segments, token, route="enregistrer", **extra):
    return client.post(
        f"/missions/{mid}/interviews/record-libre/{route}",
        data={"transcript": transcript, "session_token": token,
              "audio_segments": json.dumps(segments), **extra},
        follow_redirects=False,
    )


def _iv(mid):
    with SessionLocal() as db:
        return db.scalars(select(Interview).where(Interview.mission_id == mid)
                          .order_by(Interview.id.desc())).first()


# ---- 1 : /from-jobs aligné sur la route sœur
def test_from_jobs_texte_vide_audio_complet_accepte():
    c = TestClient(app)
    mid = _mission(c)
    f = _audio(mid, "fj1.webm")
    r = _post(c, mid, "", [{"filename": f, "position": 0}], "w5-fj1",
              route="enregistrer/from-jobs", audio_attendu="1")
    assert r.status_code == 303, r.text[:200]
    assert _iv(mid).structuration_status == "a_transcrire"


def test_from_jobs_texte_partiel_avec_audio_est_a_transcrire(monkeypatch):
    appels = []
    monkeypatch.setattr("app.routers.interviews_libre.planifier_structuration",
                        lambda i: appels.append(i))
    c = TestClient(app)
    mid = _mission(c)
    f = _audio(mid, "fj2.webm")
    r = _post(c, mid, "Début.", [{"filename": f, "position": 0}], "w5-fj2",
              route="enregistrer/from-jobs", audio_attendu="1", transcription_incomplete="1")
    assert r.status_code == 303
    assert _iv(mid).structuration_status == "a_transcrire" and appels == []


# ---- 2 : texte partiel SANS audio => structuré, pas bloqué
def test_texte_partiel_sans_audio_est_structure_avec_tranche_manquante(monkeypatch):
    appels = []
    monkeypatch.setattr("app.routers.interviews_libre.planifier_structuration",
                        lambda i: appels.append(i))
    c = TestClient(app)
    mid = _mission(c)
    r = _post(c, mid, "Texte partiel.", [], "w5-p1", transcription_incomplete="1")
    assert r.status_code == 303
    iv = _iv(mid)
    assert iv.structuration_status == "a_traiter" and iv.tranches_manquantes == 1
    assert appels == [iv.id]


# ---- 3 : audio tronque => creation a_transcrire + avertissement, plus de refus
def test_audio_tronque_texte_vide_cree_a_transcrire_avec_avertissement():
    c = TestClient(app)
    mid = _mission(c)
    f = _audio(mid, "tr1.webm")
    r = _post(c, mid, "", [{"filename": f, "position": 1}], "w5-t1", audio_attendu="2")
    assert r.status_code == 303
    iv = _iv(mid)
    assert iv.structuration_status == "a_transcrire" and iv.tranches_manquantes == 1
    assert "Audio incomplet" in c.get(r.headers["location"]).text


def test_audio_tronque_texte_partiel_cree_a_transcrire():
    c = TestClient(app)
    mid = _mission(c)
    f = _audio(mid, "tr2.webm")
    r = _post(c, mid, "Partiel.", [{"filename": f, "position": 1}], "w5-t2",
              audio_attendu="2", transcription_incomplete="1")
    assert r.status_code == 303
    iv = _iv(mid)
    assert iv.structuration_status == "a_transcrire" and iv.raw_transcript == "Partiel."
    assert iv.tranches_manquantes == 1


def test_ni_texte_ni_audio_valide_reste_refuse_meme_avec_attendu():
    c = TestClient(app)
    mid = _mission(c)
    r = _post(c, mid, "", [], "w5-t3", audio_attendu="3")
    assert r.status_code == 200 and "Aucun texte transcrit." in r.text
    assert _iv(mid) is None


# ---- 4 : champs tolérants
def test_audio_attendu_vide_ou_non_numerique_ne_donne_pas_422():
    c = TestClient(app)
    mid = _mission(c)
    for i, val in enumerate(["", "abc", "-3", "1e9"]):
        r = _post(c, mid, "Texte.", [], f"w5-a{i}", audio_attendu=val)
        assert r.status_code == 303, (val, r.status_code)


def test_position_en_chaine_ne_leve_pas():
    c = TestClient(app)
    mid = _mission(c)
    f = _audio(mid, "pos.webm")
    r = _post(c, mid, "", [{"filename": f, "position": "x"}], "w5-pos", audio_attendu="1")
    assert r.status_code == 303
    r = _post(c, mid, "", [{"filename": f, "position": "0"}], "w5-pos2", audio_attendu="1")
    assert r.status_code == 303


# ---- 5 : jeton
def test_jeton_absent_genere_un_jeton_serveur():
    c = TestClient(app)
    mid = _mission(c)
    r = _post(c, mid, "Texte.", [], "   ")
    assert r.status_code == 303
    assert _iv(mid).segment_token


def test_doublon_de_jeton_complete_un_texte_vide_mais_n_ecrase_pas_un_plus_long(monkeypatch):
    monkeypatch.setattr("app.routers.interviews_libre.planifier_structuration", lambda i: None)
    c = TestClient(app)
    mid = _mission(c)
    f = _audio(mid, "dup.webm")
    _post(c, mid, "", [{"filename": f, "position": 0}], "w5-dup", audio_attendu="1")
    iid = _iv(mid).id
    r = _post(c, mid, "Texte arrivé après.", [], "w5-dup")
    assert r.status_code == 303
    with SessionLocal() as db:
        iv = db.get(Interview, iid)
        assert iv.raw_transcript == "Texte arrivé après."
        assert iv.structuration_status == "a_traiter"
        assert db.query(Interview).filter(Interview.mission_id == mid).count() == 1
    _post(c, mid, "Court", [], "w5-dup")
    with SessionLocal() as db:
        assert db.get(Interview, iid).raw_transcript == "Texte arrivé après."


# ---- 8 : 503 base verrouillée, 404 mission supprimée
def test_base_verrouillee_donne_503(monkeypatch):
    def boom(*a, **k):
        raise OperationalError("INSERT", {}, Exception("database is locked"))
    monkeypatch.setattr("app.routers.interviews_libre._creer_interview_libre", boom)
    c = TestClient(app)
    mid = _mission(c)
    r = _post(c, mid, "Texte.", [], "w5-lock")
    assert r.status_code == 503 and "occupée" in r.text


def test_autre_operational_error_n_est_pas_masquee(monkeypatch):
    def boom(*a, **k):
        raise OperationalError("INSERT", {}, Exception("no such table: x"))
    monkeypatch.setattr("app.routers.interviews_libre._creer_interview_libre", boom)
    c = TestClient(app, raise_server_exceptions=False)
    mid = _mission(c)
    assert _post(c, mid, "Texte.", [], "w5-ot").status_code == 500


def test_mission_supprimee_pendant_l_enregistrement_donne_404(monkeypatch):
    def boom(db, mission_id, *a, **k):
        m = db.get(Mission, mission_id)
        db.delete(m)
        db.commit()
        raise IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed"))
    monkeypatch.setattr("app.routers.interviews_libre._creer_interview_libre", boom)
    c = TestClient(app)
    mid = _mission(c)
    assert _post(c, mid, "Texte.", [], "w5-404").status_code == 404


# ---- 6 : nettoyage des brouillons + parité Python/SQL
def _libre(db, mid, **kw):
    iv = Interview(mission_id=mid, mode="libre", status="done", interviewee_name="X", **kw)
    db.add(iv)
    db.commit()
    return iv.id


def test_nettoyage_ne_supprime_pas_un_libre_a_zero_tour_qui_porte_du_contenu():
    c = TestClient(app)
    cas = {
        "texte": dict(raw_transcript="du texte", structuration_status="fait"),
        "audio": dict(audio_backup_path="1_a.webm", structuration_status="fait"),
        "segments": dict(audio_segments=[{"filename": "1_a.webm", "position": 0}],
                         structuration_status="fait"),
        "jeton": dict(segment_token="tok", structuration_status="fait"),
        "statut": dict(structuration_status="a_transcrire"),
        "echec": dict(structuration_status="echec"),
    }
    vide = {"vide": dict(structuration_status="fait")}
    ids = {}
    with SessionLocal() as db:
        for nom, kw in {**cas, **vide}.items():
            mid = _mission(c, brouillon=True)
            _libre(db, mid, **kw)
            ids[nom] = mid
    with SessionLocal() as db:
        for nom, mid in ids.items():
            m = db.get(Mission, mid)
            assert _draft_vide(m) is (nom == "vide"), nom
        # PARITE Python/SQL sur le jeu de cas
        missions = db.scalars(select(Mission).where(Mission.is_draft.is_(True),
                                                    Mission.is_demo.is_(False))).all()
        assert _compter_brouillons_vides(db, demo=False) == sum(
            1 for m in missions if _draft_vide(m))
    c.post("/missions/brouillons/nettoyer", follow_redirects=False)
    with SessionLocal() as db:
        for nom, mid in ids.items():
            assert (db.get(Mission, mid) is None) is (nom == "vide"), nom


# ---- 7 : confirmation de retranscription
def test_confirmation_remet_fait_zero_tranche_et_supprime_les_jobs():
    c = TestClient(app)
    mid = _mission(c)
    with SessionLocal() as db:
        iid = _libre(db, mid, raw_transcript="avant", structuration_status="a_traiter",
                     segment_token="w5-conf", tranches_manquantes=2)
        db.add(InterviewSegmentJob(session_token="w5-conf", interview_id=iid, mission_id=mid,
                                   position=0, status="done", text="t"))
        # Job de retranscription terminé SANS erreur (sans lui, la confirmation
        # n'est plus « complète » : lot 4 W9 B5).
        db.add(AudioFileJob(session_token="w5-job", filename="", filenames=[],
                            interview_id=iid, status="done", block_seconds=60, blocks=[]))
        db.commit()
    r = c.post(f"/interviews/{iid}/retranscrire/confirmer", data={
        "transcript": "Nouveau.", "turn_interlocuteur": ["A"], "turn_question": ["Q ?"],
        "turn_remarque": ["R"], "turn_section_title": [""]}, follow_redirects=False)
    assert r.status_code == 303, r.text[:200]
    with SessionLocal() as db:
        iv = db.get(Interview, iid)
        assert iv.structuration_status == "fait" and iv.tranches_manquantes == 0
        assert db.query(InterviewSegmentJob).filter(
            InterviewSegmentJob.session_token == "w5-conf").count() == 0
        assert db.query(InterviewTurn).filter(InterviewTurn.interview_id == iid).count() == 1


def test_confirmation_sans_tour_reste_refusee_et_garde_a_transcrire():
    c = TestClient(app)
    mid = _mission(c)
    with SessionLocal() as db:
        iid = _libre(db, mid, structuration_status="a_transcrire")
    r = c.post(f"/interviews/{iid}/retranscrire/confirmer", data={"transcript": "Texte."})
    assert r.status_code == 400
    with SessionLocal() as db:
        assert db.get(Interview, iid).structuration_status == "a_transcrire"


# ---- 8 : avertissement
def _rendre_avertissement(interviews):
    tpl = templates.env.get_template("interviews/_avertissement_non_structures.html")
    return tpl.render(mission=SimpleNamespace(interviews=interviews))


def test_avertissement_pour_un_fait_a_zero_tour_et_pas_pour_un_fait_avec_tours():
    def iv(i, turns):
        return SimpleNamespace(id=i, mode="libre", structuration_status="fait",
                               interviewee_name=f"N{i}", turns=turns)
    assert "N1" in _rendre_avertissement([iv(1, [])])
    assert "avertissement-non-structures" not in _rendre_avertissement([iv(2, [object()])])
