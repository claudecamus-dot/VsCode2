"""Lot 4 W9 : dédoublonnage des segments du direct, estimation du Retry-After,
segment abandonné, et correctifs B1..B7 de la revue de contrôle."""
from __future__ import annotations

import asyncio
import collections
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.main import app
from app.models import AudioFileJob, Interview, InterviewSegmentJob, InterviewTurn, Mission
from app.routers import interviews_libre, interviews_retranscription
from app.routers.missions import _compter_brouillons_vides, _draft_vide
from app.services import audio_file_jobs, segment_dedupe
from app.services import audio_transcribe as at


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


class FauxModele:
    def __init__(self, latence=0.4):
        self.appels = 0
        self.latence = latence
        self._l = threading.Lock()

    def transcribe(self, *_a, **_k):
        with self._l:
            self.appels += 1
        time.sleep(self.latence)
        return [SimpleNamespace(text=" bonjour ")], None


@pytest.fixture
def faux(monkeypatch):
    m = FauxModele()
    monkeypatch.setattr(at, "_faster_whisper", lambda: object())
    monkeypatch.setattr(at, "_exiger_piste_audio", lambda c: None)
    monkeypatch.setattr(at, "_probe_duration_s", lambda c: 1.0)
    monkeypatch.setattr(at, "_get_model", lambda: m)
    return m


def _poster(client, blob=b"audio-1", ident=None, sortie=None, i=0):
    h = {"X-Segment-Id": ident} if ident else {}
    r = client.post("/audio/transcribe-segment",
                    files={"file": ("s.webm", blob, "audio/webm")}, headers=h)
    if sortie is not None:
        sortie[i] = r


# ---------------- A : dédoublonnage ----------------
@pytest.mark.parametrize("ident", ["seg-42", None])
def test_cinq_requetes_concurrentes_un_seul_appel_modele(faux, ident):
    c = TestClient(app)
    sortie = {}
    ts = [threading.Thread(target=_poster, args=(c, b"meme-blob", ident, sortie, i))
          for i in range(5)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert faux.appels == 1
    assert [r.status_code for r in sortie.values()] == [200] * 5
    assert {r.text for r in sortie.values()} == {sortie[0].text}
    assert sortie[0].json() == {"text": "bonjour"}
    # un rejeu tardif du même segment est servi sans retranscrire
    _poster(c, b"meme-blob", ident, sortie, 9)
    assert faux.appels == 1 and sortie[9].json() == {"text": "bonjour"}


def test_segments_differents_ne_sont_pas_fusionnes(faux):
    c = TestClient(app)
    sortie = {}
    _poster(c, b"un", None, sortie, 0)
    _poster(c, b"deux", None, sortie, 1)
    assert faux.appels == 2


def test_segment_abandonne_nest_pas_transcrit(faux):
    at._MODEL_LOCK.acquire()
    res = {}

    def appel():
        try:
            at.transcribe_audio(b"x", abandonne=lambda: True)
        except BaseException as e:  # noqa: BLE001
            res["exc"] = e

    t = threading.Thread(target=appel)
    t.start()
    time.sleep(0.3)
    at._MODEL_LOCK.release()
    t.join(10)
    assert isinstance(res["exc"], at.TranscriptionAbandonnee)
    assert faux.appels == 0
    assert not at._MODEL_LOCK.locked()  # verrou rendu


def test_registre_abandon_quand_plus_personne_nattend():
    r = segment_dedupe.RegistreSegments()
    _t, t, proprio = r.rejoindre("k")
    assert proprio and not r.est_abandonne(t)
    r.quitter(t)
    assert t.abandonne
    # S4 : tant que le thread n'a pas constaté l'abandon, un rejeu le RÉACTIVE
    _t2, t2, proprio2 = r.rejoindre("k")
    assert t2 is t and not proprio2 and not r.est_abandonne(t)
    # une fois l'abandon constaté par le thread (mort), on repart sur un travail neuf
    r.quitter(t)
    assert r.est_abandonne(t)
    _t3, t3, proprio3 = r.rejoindre("k")
    assert proprio3 and t3 is not t


def test_s5_meme_id_contenus_differents_deux_travaux():
    assert segment_dedupe.cle_segment("a", b"1") != segment_dedupe.cle_segment("a", b"2")
    assert segment_dedupe.cle_segment("a", b"1") == segment_dedupe.cle_segment("a", b"1")


def _faux_request(deconnecte_apres=None, ident="s"):
    """deconnecte_apres : None = jamais ; sinon nb d'appels avant « parti »."""
    etat = {"n": 0}

    async def is_disconnected():
        etat["n"] += 1
        await asyncio.sleep(0.05)
        return deconnecte_apres is not None and etat["n"] > deconnecte_apres

    return SimpleNamespace(headers={"X-Segment-Id": ident}, is_disconnected=is_disconnected)


def test_s1_proprietaire_deconnecte_le_second_nattend_pas_indefiniment(faux):
    from app.routers.interviews_audio import _transcrire_dedoublonne

    async def scenario():
        a = asyncio.create_task(_transcrire_dedoublonne(_faux_request(0), b"blob"))
        await asyncio.sleep(0.01)
        b = asyncio.create_task(_transcrire_dedoublonne(_faux_request(None), b"blob"))
        return await asyncio.wait_for(asyncio.gather(a, b), 20)

    ra, rb = asyncio.run(scenario())
    assert ra is None and rb == "bonjour"
    assert faux.appels == 1


def test_s4_client_parti_puis_rejoue_une_seule_transcription(faux):
    from app.routers.interviews_audio import _transcrire_dedoublonne

    async def scenario():
        # Le thread transcrit déjà (0,4 s) quand le client part puis rejoue.
        ra = await _transcrire_dedoublonne(_faux_request(0), b"blob")  # parti
        rb = await asyncio.wait_for(
            _transcrire_dedoublonne(_faux_request(None), b"blob"), 20)
        return ra, rb

    ra, rb = asyncio.run(scenario())
    assert ra is None and rb == "bonjour"
    assert faux.appels == 1


def test_registre_ttl_et_borne():
    h = [0.0]
    r = segment_dedupe.RegistreSegments(clock=lambda: h[0])
    _x, t, _p = r.rejoindre("a")
    r.terminer("a", t, "txt")
    h[0] = segment_dedupe.TTL_RESULTAT_S + 1
    for i in range(segment_dedupe.MAX_RESULTATS + 5):
        _x, t, _p = r.rejoindre(f"k{i}")
        r.terminer(f"k{i}", t, "v")
    assert len(r._faits) == segment_dedupe.MAX_RESULTATS


# ---------------- A : 503 estimé ----------------
def test_busy_retry_after_estime_file_x_duree(monkeypatch, faux):
    monkeypatch.setattr(at, "SEGMENT_LOCK_TIMEOUT_S", 0.1)
    monkeypatch.setattr(at, "_DUREES_RECENTES", collections.deque([40.0, 40.0], maxlen=8))
    at._MODEL_LOCK.acquire()
    try:
        r = TestClient(app).post("/audio/transcribe-segment",
                                 files={"file": ("s.webm", b"occupe", "audio/webm")})
    finally:
        at._MODEL_LOCK.release()
    assert r.status_code == 503
    assert r.json()["retry_after_s"] == 40 and r.headers["Retry-After"] == "40"


def test_estimation_bornee(monkeypatch):
    monkeypatch.setattr(at, "_DUREES_RECENTES", collections.deque([1.0], maxlen=8))
    assert at.estimer_retry_after() == at.RETRY_AFTER_MIN_S
    monkeypatch.setattr(at, "_DUREES_RECENTES", collections.deque([1000.0], maxlen=8))
    assert at.estimer_retry_after() == at.RETRY_AFTER_MAX_S
    monkeypatch.setattr(at, "_ATTENTE_LIVE", 2)
    monkeypatch.setattr(at, "_DUREES_RECENTES", collections.deque([30.0], maxlen=8))
    assert at.estimer_retry_after() == 90.0


# ---------------- B6 ----------------
def test_b6_verrou_de_fond_non_obtenu_nest_pas_reessaye(monkeypatch):
    monkeypatch.setattr(at, "BACKGROUND_LOCK_TIMEOUT_S", 0.05)
    at._MODEL_LOCK.acquire()
    try:
        with pytest.raises(at.TranscriptionError) as e:
            at._transcribe_pcm_sequential(object(), at.FOND)
    finally:
        at._MODEL_LOCK.release()
    assert isinstance(e.value, at.TranscriptionFondBusyError)
    assert audio_file_jobs._est_reessayable(e.value) is False
    assert audio_file_jobs._est_reessayable(at.TranscriptionBusyError("x")) is True


# ---------------- B1..B5, B7 ----------------
def _mission(c, brouillon=False) -> int:
    r = c.post("/entretiens/libre/nouveau", follow_redirects=False)
    mid = int(r.headers["location"].split("/")[2])
    with SessionLocal() as db:
        db.get(Mission, mid).is_draft = brouillon
        db.commit()
    return mid


def _libre(db, mid, **kw):
    iv = Interview(mission_id=mid, mode="libre", status="done", interviewee_name="X", **kw)
    db.add(iv)
    db.commit()
    return iv.id


@pytest.mark.parametrize("statut,avec_tour", [("fait", False), ("a_traiter", False),
                                              ("en_cours", False), ("a_transcrire", True)])
def test_b1_doublon_de_jeton_ne_reecrit_pas(statut, avec_tour):
    c = TestClient(app)
    mid = _mission(c)
    jeton = f"b1-{statut}"
    with SessionLocal() as db:
        iid = _libre(db, mid, raw_transcript="court", structuration_status=statut,
                     segment_token=jeton)
        if avec_tour:
            db.add(InterviewTurn(interview_id=iid, position=0, interlocuteur="A",
                                 question="q", remarque="", section_title=""))
            db.commit()
    r = c.post(f"/missions/{mid}/interviews/record-libre/enregistrer",
               data={"transcript": "un texte beaucoup plus long que le précédent",
                     "session_token": jeton}, follow_redirects=False)
    assert r.status_code in (303, 200), r.status_code
    with SessionLocal() as db:
        assert db.get(Interview, iid).raw_transcript == "court"
        assert db.get(Interview, iid).structuration_status == statut


def test_b2_etat_audio_positions_hors_bornes(monkeypatch):
    def faux_refs(_mid, _chemin, segments, _dir):
        return "x.webm", segments

    monkeypatch.setattr(interviews_libre.mission_backups, "references_audio_sures", faux_refs)
    mission = SimpleNamespace(id=1)

    def etat(positions, attendu=0):
        import json
        ident = {"audio_backup_path": "x",
                 "audio_segments": json.dumps([{"position": p} for p in positions])}
        return interviews_libre._etat_audio(mission, ident, attendu)

    assert etat([-3]) == (True, 1)                 # négative seule : écartée
    assert etat([5000, 0]) == (True, 0)            # hors plafond écartée
    assert etat([10 ** 9, 0, 2], 3) == (True, 1)   # pas de range géant


def test_b4_parite_python_sql_blancs_et_statut_vide():
    c = TestClient(app)
    cas = {
        "retours_ligne": dict(raw_transcript="\n\t \r\n", structuration_status="fait"),
        "statut_vide": dict(raw_transcript="", structuration_status=""),
        "texte_null": dict(raw_transcript=None, structuration_status="fait"),  # statut NOT NULL en base
        "tab_seul": dict(raw_transcript="\t", structuration_status=""),
        "texte": dict(raw_transcript=" a ", structuration_status="fait"),
        "echec": dict(raw_transcript="", structuration_status="echec"),
    }
    attendu_vide = {"retours_ligne", "statut_vide", "texte_null", "tab_seul"}
    ids = {}
    with SessionLocal() as db:
        for nom, kw in cas.items():
            mid = _mission(c, brouillon=True)
            iid = _libre(db, mid, **kw)
            # Valeurs forcées en SQL brut : l'ORM peut normaliser '' / None.
            db.execute(text("UPDATE interviews SET structuration_status = :s, "
                            "raw_transcript = :r WHERE id = :i"),
                       {"s": kw["structuration_status"], "r": kw["raw_transcript"], "i": iid})
            db.commit()
            ids[nom] = mid
    with SessionLocal() as db:
        for nom, mid in ids.items():
            assert _draft_vide(db.get(Mission, mid)) is (nom in attendu_vide), nom
        drafts = db.scalars(select(Mission).where(Mission.is_draft.is_(True),
                                                  Mission.is_demo.is_(False))).all()
        assert _compter_brouillons_vides(db, demo=False) == sum(
            1 for m in drafts if _draft_vide(m))


def test_b5_job_expire_ne_vide_rien():
    c = TestClient(app)
    mid = _mission(c)
    with SessionLocal() as db:
        iid = _libre(db, mid, raw_transcript="avant", structuration_status="a_traiter",
                     segment_token="b5", tranches_manquantes=2)
        db.add(InterviewSegmentJob(session_token="b5", interview_id=iid, mission_id=mid,
                                   position=0, status="done", text="t"))
        db.commit()
    r = c.post(f"/interviews/{iid}/retranscrire/confirmer", data={
        "transcript": "N.", "turn_interlocuteur": ["A"], "turn_question": ["Q"],
        "turn_remarque": ["R"], "turn_section_title": [""]}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.get(Interview, iid).tranches_manquantes == 2
        assert db.query(InterviewSegmentJob).filter(
            InterviewSegmentJob.session_token == "b5").count() == 1


def test_b5_trou_de_positions_nest_pas_complet():
    iv = SimpleNamespace(audio_segments=[{"position": 0}, {"position": 2}])
    assert interviews_retranscription._trou_de_positions(iv) is True
    iv = SimpleNamespace(audio_segments=[{"position": 0}, {"position": 1}])
    assert interviews_retranscription._trou_de_positions(iv) is False


def test_b7_base_occupee_detection_et_en_tete(monkeypatch):
    assert interviews_libre._base_occupee(
        OperationalError("s", {}, Exception("database is locked")))
    assert interviews_libre._base_occupee(Exception("SQLITE_BUSY"))
    assert not interviews_libre._base_occupee(Exception("no such table"))

    def boom(*a, **k):
        raise OperationalError("s", {}, Exception("database is locked"))

    monkeypatch.setattr(interviews_libre, "_enregistrer_libre_direct", boom)
    c = TestClient(app)
    mid = _mission(c)
    r = c.post(f"/missions/{mid}/interviews/record-libre/enregistrer",
               data={"transcript": "du texte", "session_token": "b7"}, follow_redirects=False)
    assert r.status_code == 503 and r.headers.get("Retry-After") == "5"


def test_s8a_confirmation_route_trou_de_positions_garde_jetons_et_tranches():
    c = TestClient(app)
    mid = _mission(c)
    f0, f2 = f"{mid}_w9trou0.webm", f"{mid}_w9trou2.webm"
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    (RECORDINGS_DIR / f0).write_bytes(b"x")
    (RECORDINGS_DIR / f2).write_bytes(b"x")
    try:
        with SessionLocal() as db:
            iid = _libre(db, mid, raw_transcript="avant", structuration_status="a_traiter",
                         segment_token="s8a", tranches_manquantes=2,
                         audio_segments=[{"filename": f0, "position": 0},
                                         {"filename": f2, "position": 2}])
            db.add(InterviewSegmentJob(session_token="s8a", interview_id=iid, mission_id=mid,
                                       position=0, status="done", text="t"))
            db.add(AudioFileJob(session_token="s8a-job", filename="", filenames=[f0, f2],
                                interview_id=iid, status="done", block_seconds=60, blocks=[]))
            db.commit()
        r = c.post(f"/interviews/{iid}/retranscrire/confirmer", data={
            "transcript": "N.", "turn_interlocuteur": ["A"], "turn_question": ["Q"],
            "turn_remarque": ["R"], "turn_section_title": [""]}, follow_redirects=False)
        assert r.status_code == 303
        with SessionLocal() as db:
            assert db.get(Interview, iid).tranches_manquantes == 2
            assert db.query(InterviewSegmentJob).filter(
                InterviewSegmentJob.session_token == "s8a").count() == 1
    finally:
        (RECORDINGS_DIR / f0).unlink(missing_ok=True)
        (RECORDINGS_DIR / f2).unlink(missing_ok=True)


def test_s8b_doublon_de_jeton_a_transcrire_sans_tour_est_complete(monkeypatch):
    monkeypatch.setattr(interviews_libre, "planifier_structuration", lambda *_a, **_k: None)
    c = TestClient(app)
    mid = _mission(c)
    with SessionLocal() as db:
        iid = _libre(db, mid, raw_transcript="", structuration_status="a_transcrire",
                     segment_token="s8b")
    r = c.post(f"/missions/{mid}/interviews/record-libre/enregistrer",
               data={"transcript": "Le texte complet.", "session_token": "s8b"},
               follow_redirects=False)
    assert r.status_code in (303, 200)
    with SessionLocal() as db:
        assert db.get(Interview, iid).raw_transcript == "Le texte complet."
