"""Story 3.2 — une transcription longue avance toujours ou échoue clairement.

AC3.2.1 (C-1) : le délai d'un bloc ne court qu'une fois le modèle du worker prêt.
AC3.2.2 (C-2, C-3, D-10 a) : repli séquentiel, job d'un seul bloc et repli après
casse du pool bornés, vivants, gardés en mémoire ; l'attente du verrou ne compte
pas ; un chargement en cours rend un « occupé » réessayable, rejoué sans essai.
AC3.2.3 (C-6) : l'extraction des tours d'une exécution supplantée n'écrit rien.
AC3.2.5 (C-7) : `TranscriptionMemoireInsuffisanteError` au niveau job n'est jamais
avalée comme tranche illisible.

Jamais le vrai modèle Whisper : faux `faster_whisper` (paquet temporaire importé
par un processus `spawn`) ou doublures de `_get_model`.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future

import numpy as np
import pytest

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.models import AudioFileJob, Interview, InterviewSegmentJob, Mission
from app.services import audio_file_jobs, audio_transcribe as at
from tests.conftest_pool_chaud import pool_chaud_isole  # noqa: F401  (autouse)

_PREFIXE = "s32_transcription_test_"

_FAUX_FASTER_WHISPER_LENT = '''
import os, time


class _Segment:
    def __init__(self, text):
        self.text = text


class WhisperModel:
    def __init__(self, *args, **kwargs):
        time.sleep(float(os.environ["S32_CHARGEMENT_S"]))   # chargement lent

    def transcribe(self, pcm, **kwargs):
        return [_Segment(f"bloc-{len(pcm)}")], None
'''


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


class _Seg:
    def __init__(self, text):
        self.text = text


class _FauxModele:
    def __init__(self):
        self.appels = 0

    def transcribe(self, pcm, **kwargs):
        self.appels += 1
        return [_Seg("ok")], None


@pytest.fixture
def delais_courts(monkeypatch):
    monkeypatch.setattr(at, "BLOC_RESULTAT_TIMEOUT_S", 0.3)
    monkeypatch.setattr(at, "BATTEMENT_ATTENTE_S", 0.05)
    monkeypatch.setattr(at, "_MODEL_LOCK", type(at._MODEL_LOCK)())


# --------------------------------------------------------------------------- #
# AC3.2.1 — le délai du bloc ne court qu'une fois le modèle prêt
# --------------------------------------------------------------------------- #
def test_ac321_chargement_en_cours_repousse_l_echeance_du_bloc(delais_courts):
    # Revue finale 3.2 (F8) : sans minuterie concurrente. Le chargement dure tant
    # que 3x le délai du bloc (0,3 s) ne s'est pas écoulé ; le résultat arrive
    # PENDANT le chargement, donc forcément après le délai d'origine — aucune
    # course entre une minuterie et l'échéance, quelle que soit la charge.
    future: Future = Future()
    debut = time.monotonic()

    def chargement() -> bool:
        if time.monotonic() - debut > 3 * at.BLOC_RESULTAT_TIMEOUT_S and not future.done():
            future.set_result("fini")
        return True

    assert at._attendre_resultat(future, 0, 1, chargement) == "fini"
    assert time.monotonic() - debut > 3 * at.BLOC_RESULTAT_TIMEOUT_S


def test_ac321_sans_chargement_le_delai_reste_borne(delais_courts):
    future: Future = Future()
    with pytest.raises(at.TranscriptionBloqueeError):
        at._attendre_resultat(future, 0, 1, lambda: False)


def test_ac321_pool_reel_worker_lent_a_charger_ne_fait_pas_expirer_le_bloc(
        tmp_path, monkeypatch):
    """Pool RÉEL (1 processus spawn) : le faux modèle met 3 s à se construire, le
    délai d'un bloc est de 1 s. Avant le correctif : TranscriptionBloqueeError."""
    paquet = tmp_path / "fauxfw"
    paquet.mkdir()
    (paquet / "faster_whisper.py").write_text(_FAUX_FASTER_WHISPER_LENT, encoding="utf-8")
    monkeypatch.syspath_prepend(str(paquet))
    monkeypatch.setenv("S32_CHARGEMENT_S", "3")
    monkeypatch.setattr(at, "memoire_disponible", lambda: 64 * 1024 ** 3)
    monkeypatch.setattr(at, "BLOC_RESULTAT_TIMEOUT_S", 1.0)
    monkeypatch.setattr(at, "BATTEMENT_ATTENTE_S", 0.1)
    monkeypatch.setattr(at, "CHARGEMENT_MODELE_TIMEOUT_S", 20.0)   # régression : échec rapide
    blocs = [np.zeros(1600 + i, dtype=np.float32) for i in range(2)]
    rendus = list(at._drain_parallel(blocs, 0, 2, 1))
    assert rendus == [(0, "bloc-1600"), (1, "bloc-1601")]


class _PoolInitEnLigne(at.ProcessPoolExecutor):
    """Vraie classe (garde « pool réel » active), initializer joué dans le parent,
    aucune tâche exécutée : le bloc reste figé sur un worker qui n'a rien chargé."""

    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=()):
        super().__init__(max_workers=max_workers, mp_context=mp_context)
        prets, servis = initargs
        prets.value = servis.value = 1       # un worker servi et prêt, l'autre jamais servi

    def submit(self, fn, *args, **kwargs):
        return Future()


def test_ac321_worker_jamais_servi_ne_repousse_pas_l_echeance(monkeypatch, delais_courts):
    """Revue 3.2 : soumissions limitées, un seul worker servi (et prêt), l'autre
    jamais servi — un bloc figé sur le worker prêt échoue au délai du BLOC, pas à la
    borne du chargement."""
    monkeypatch.setattr(at, "ProcessPoolExecutor", _PoolInitEnLigne)
    monkeypatch.setattr(at, "memoire_disponible", lambda: 64 * 1024 ** 3)
    monkeypatch.setattr(at, "CHARGEMENT_MODELE_TIMEOUT_S", 5.0)
    debut = time.monotonic()
    with pytest.raises(at.TranscriptionBloqueeError) as e:
        list(at._drain_parallel(_blocs_s32(2), 0, 2, 2))
    assert time.monotonic() - debut < 3
    assert "n'avance plus" in str(e.value)


def _blocs_s32(n: int) -> list:
    return [np.zeros(1600 + i, dtype=np.float32) for i in range(n)]


# --------------------------------------------------------------------------- #
# AC3.2.2 / C-2 — repli séquentiel : l'attente du verrou ne compte pas
# --------------------------------------------------------------------------- #
def test_ac322_attente_du_verrou_ne_compte_pas_dans_le_delai(monkeypatch, delais_courts):
    modele = _FauxModele()
    monkeypatch.setattr(at, "_get_model", lambda: modele)
    at._MODEL_LOCK.acquire(timeout=1, priority=at.FOND)        # un autre bloc tient le verrou
    threading.Timer(0.9, at._MODEL_LOCK.release).start()
    assert at._sequentiel_borne(np.zeros(160, dtype=np.float32), at.FOND, 0, 1) == "ok"


def test_ac322_chargement_trop_long_echoue_clairement_et_le_bloc_est_abandonne(
        monkeypatch, delais_courts):
    monkeypatch.setattr(at, "BACKGROUND_LOCK_TIMEOUT_S", 0.1)
    monkeypatch.setattr(at, "CHARGEMENT_MODELE_TIMEOUT_S", 0.3)
    modele = _FauxModele()
    libere = threading.Event()
    monkeypatch.setattr(at, "_get_model", lambda: libere.wait(5) and modele)
    avant = set(threading.enumerate())
    debut = time.monotonic()
    with pytest.raises(at.TranscriptionBloqueeError) as e:
        at._sequentiel_borne(np.zeros(160, dtype=np.float32), at.FOND, 0, 1)
    assert time.monotonic() - debut < 3
    assert "pas pu être prêt" in str(e.value)
    libere.set()
    _joindre_fils_repli(avant)
    assert modele.appels == 0                                 # bloc jeté, pas transcrit
    assert at._MODEL_LOCK.acquire(timeout=1, priority=at.FOND)  # verrou rendu
    at._MODEL_LOCK.release()


def test_ac322_toute_sortie_du_demandeur_abandonne_le_bloc(monkeypatch, delais_courts):
    """Revue 3.2 : pas seulement TranscriptionBloqueeError — toute sortie du
    demandeur fait sauter le bloc au fil qui obtiendrait le verrou ensuite."""
    modele = _FauxModele()
    monkeypatch.setattr(at, "_get_model", lambda: modele)

    def _sortie(*a, **k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(at, "_attendre_resultat", _sortie)
    avant = set(threading.enumerate())
    at._MODEL_LOCK.acquire(timeout=1, priority=at.FOND)
    try:
        with pytest.raises(KeyboardInterrupt):
            at._sequentiel_borne(np.zeros(160, dtype=np.float32), at.FOND, 0, 1)
    finally:
        at._MODEL_LOCK.release()
    # Re-revue 3.2 (B3) : on attend la FIN du fil (join), pas un délai arbitraire —
    # l'assertion négative ne peut plus passer parce que le fil n'a pas encore tourné.
    _joindre_fils_repli(avant)
    assert modele.appels == 0


def _joindre_fils_repli(avant: set) -> None:
    fils = [t for t in threading.enumerate()
            if t not in avant and t.name == "whisper-repli-borne"]
    assert fils, "fil whisper-repli-borne introuvable"
    for t in fils:
        t.join(5)
        assert not t.is_alive()


# --------------------------------------------------------------------------- #
# AC3.2.2 / C-3 — job d'un seul bloc : borné, vivant, garde mémoire
# --------------------------------------------------------------------------- #
@pytest.fixture
def un_bloc(monkeypatch):
    monkeypatch.setattr(at, "_faster_whisper", lambda: object())
    monkeypatch.setattr(at, "_probe_duration_s", lambda content: 1.0)
    monkeypatch.setattr(at, "_decode_to_pcm16k", lambda content: np.zeros(16000, dtype=np.float32))


def test_ac322_job_d_un_seul_bloc_borne_et_vivant(monkeypatch, delais_courts, un_bloc):
    libere = threading.Event()
    monkeypatch.setattr(at, "_transcribe_pcm_sequential", lambda pcm: libere.wait(4) and "tard")
    battements = []
    jeton = at.BATTEMENT_ATTENTE.set(lambda: battements.append(1))
    debut = time.monotonic()
    try:
        with pytest.raises(at.TranscriptionBloqueeError):     # classe conservée (non rejouée)
            list(at.iter_transcribe_blocks(b"x", 60))
    finally:
        at.BATTEMENT_ATTENTE.reset(jeton)
        libere.set()
    assert time.monotonic() - debut < 3
    assert battements


def test_ac322_repli_sequentiel_garde_memoire_sans_charger(monkeypatch, un_bloc):
    constructions = []

    class _Fw:
        class WhisperModel:
            def __init__(self, *a, **k):
                constructions.append(1)

    monkeypatch.setattr(at, "_faster_whisper", lambda: _Fw)
    monkeypatch.setattr(at, "_model", None)
    monkeypatch.setattr(at, "memoire_disponible", lambda: 1 * 1024 ** 3)
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", int(1.6 * 1024 ** 3))
    with pytest.raises(at.TranscriptionMemoireInsuffisanteError):
        list(at.iter_transcribe_blocks(b"x", 60))
    assert constructions == []


# --------------------------------------------------------------------------- #
# AC3.2.2 / D-10 a — chargement en cours : « occupé » réessayable, rejoué sans essai
# --------------------------------------------------------------------------- #
def test_ac322_fond_pendant_un_chargement_rend_un_occupe_reessayable(monkeypatch, delais_courts):
    monkeypatch.setattr(at, "_model", None)
    monkeypatch.setattr(at, "ATTENTE_CHARGEMENT_FOND_S", 0.2)
    monkeypatch.setattr(at, "_MODEL_LOAD_LOCK", threading.Lock())
    at._MODEL_LOAD_LOCK.acquire()                               # préchauffage en cours
    try:
        debut = time.monotonic()
        with pytest.raises(at.TranscriptionChargementEnCoursError) as e:
            at._transcribe_pcm_sequential(np.zeros(160, dtype=np.float32))
        assert time.monotonic() - debut < 3
    finally:
        at._MODEL_LOAD_LOCK.release()
    assert audio_file_jobs._est_reessayable(e.value) is True
    assert at._MODEL_LOCK.acquire(timeout=1, priority=at.FOND)  # verrou rendu
    at._MODEL_LOCK.release()


def test_ac322_live_pendant_un_chargement_rend_un_occupe_borne(monkeypatch, delais_courts):
    monkeypatch.setattr(at, "_model", None)
    monkeypatch.setattr(at, "SEGMENT_LOCK_TIMEOUT_S", 0.2)
    monkeypatch.setattr(at, "ATTENTE_CHARGEMENT_FOND_S", 30.0)   # c'est la borne LIVE qui joue
    monkeypatch.setattr(at, "_MODEL_LOAD_LOCK", threading.Lock())
    at._MODEL_LOAD_LOCK.acquire()
    try:
        debut = time.monotonic()
        with pytest.raises(at.TranscriptionChargementEnCoursError):
            at._transcribe_pcm_sequential(np.zeros(160, dtype=np.float32), at.LIVE)
        assert time.monotonic() - debut < 3
    finally:
        at._MODEL_LOAD_LOCK.release()


def test_ac322_garde_memoire_pendant_un_prechauffage_rend_un_occupe(
        monkeypatch, delais_courts, un_bloc):
    """Revue 3.2 : préchauffage en cours (_MODEL_LOAD_LOCK tenu, _model None) et
    mémoire basse — pas un échec définitif « mémoire », un « occupé » réessayable."""
    monkeypatch.setattr(at, "_model", None)
    monkeypatch.setattr(at, "memoire_disponible", lambda: 1 * 1024 ** 3)
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", int(1.6 * 1024 ** 3))
    monkeypatch.setattr(at, "ATTENTE_CHARGEMENT_FOND_S", 0.2)
    monkeypatch.setattr(at, "_MODEL_LOAD_LOCK", threading.Lock())
    at._MODEL_LOAD_LOCK.acquire()
    try:
        with pytest.raises(at.TranscriptionChargementEnCoursError) as e:
            list(at.iter_transcribe_blocks(b"x", 60))
    finally:
        at._MODEL_LOAD_LOCK.release()
    assert audio_file_jobs._est_reessayable(e.value) is True


def _job(nom: str, multi: bool = False, interview_id=None) -> int:
    complet = f"{_PREFIXE}{nom}"
    (RECORDINGS_DIR / complet).write_bytes(b"x")
    with SessionLocal() as db:
        if multi:
            job = AudioFileJob(session_token=f"tok-s32-{nom}", status="pending", filename="",
                               filenames=[complet, complet], block_seconds=60, blocks=[],
                               interview_id=interview_id)
        else:
            job = AudioFileJob(session_token=f"tok-s32-{nom}", status="pending", filename=complet)
        db.add(job)
        db.commit()
        return job.id


def _relire(job_id: int) -> AudioFileJob:
    with SessionLocal() as db:
        job = db.get(AudioFileJob, job_id)
        db.expunge(job)
        return job


def test_ac322_job_rejoue_un_chargement_en_cours_sans_consommer_d_essai(monkeypatch):
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.0)
    appels = []

    def _iter(content, block_s=None, start_index=0, stats=None):
        appels.append(start_index)
        if len(appels) <= audio_file_jobs.BLOC_RETRIES + 2:      # plus que les essais
            raise at.TranscriptionChargementEnCoursError("Chargement du modèle en cours, réessayez.")
        yield 0, 1, "bloc transcrit."

    battements = []
    vrai_battement = audio_file_jobs._battement
    monkeypatch.setattr(audio_file_jobs, "_battement",
                        lambda db, job: battements.append(1) or vrai_battement(db, job))
    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    job_id = _job("chargement")
    audio_file_jobs.run_audio_file_job(job_id)
    job = _relire(job_id)
    assert job.status == "done", (job.status, job.error)
    assert job.blocks == ["bloc transcrit."]
    assert len(appels) == audio_file_jobs.BLOC_RETRIES + 3     # aucun essai consommé
    assert len(battements) >= audio_file_jobs.BLOC_RETRIES + 2  # vivant à chaque rejeu
    assert (job.completude or {}).get("blocs_rejoues") in (None, [])  # pas un rejeu


def test_ac322_un_bloc_obtenu_remet_a_zero_l_episode_de_chargement(monkeypatch):
    """Revue 3.2 : un 2e préchauffage APRÈS un bloc rendu repart avec sa borne."""
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.2)
    monkeypatch.setattr(at, "CHARGEMENT_MODELE_TIMEOUT_S", 0.3)
    appels = []

    def _iter(content, block_s=None, start_index=0, stats=None):
        appels.append(start_index)
        if len(appels) == 1:
            raise at.TranscriptionChargementEnCoursError("Chargement du modèle en cours, réessayez.")
        if start_index == 0:
            yield 0, 2, "bloc 0."
            # Nouvel épisode, bien après la borne (0,3 s) comptée depuis le 1er :
            # 0,2 + 1,0 s (F8 : marge x4, plus 0,35 contre 0,3).
            time.sleep(1.0)
            raise at.TranscriptionChargementEnCoursError("Chargement du modèle en cours, réessayez.")
        yield 1, 2, "bloc 1."

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    job_id = _job("chargement_deux_episodes")
    audio_file_jobs.run_audio_file_job(job_id)
    job = _relire(job_id)
    assert job.status == "done", (job.status, job.error)
    assert job.blocks == ["bloc 0.", "bloc 1."]


def test_ac322_chargement_execution_supplantee_s_arrete(monkeypatch):
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.05)
    monkeypatch.setattr(at, "CHARGEMENT_MODELE_TIMEOUT_S", 1.0)
    job_id = _job("chargement_supplante")
    appels = []

    def _iter(content, block_s=None, start_index=0, stats=None):
        appels.append(1)
        if len(appels) == 1:
            audio_file_jobs._prendre_job(job_id)          # reprise par une autre exécution
        raise at.TranscriptionChargementEnCoursError("Chargement du modèle en cours, réessayez.")
        yield  # noqa: unreachable

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    try:
        audio_file_jobs.run_audio_file_job(job_id)
        assert appels == [1]                                  # pas de reprise du verrou
        assert _relire(job_id).status == "running"            # rien écrit par le fantôme
    finally:
        audio_file_jobs._PROPRIETAIRES.pop(job_id, None)


def test_ac322_job_chargement_interminable_echoue_clairement(monkeypatch):
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.05)
    monkeypatch.setattr(at, "CHARGEMENT_MODELE_TIMEOUT_S", 0.2)

    def _iter(content, block_s=None, start_index=0, stats=None):
        raise at.TranscriptionChargementEnCoursError("Chargement du modèle en cours, réessayez.")
        yield  # noqa: unreachable

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    job_id = _job("chargement_sans_fin")
    audio_file_jobs.run_audio_file_job(job_id)
    job = _relire(job_id)
    assert job.status == "failed"
    assert "pas pu être prêt" in (job.error or "")


# --------------------------------------------------------------------------- #
# AC3.2.3 / C-6 — extraction des tours : une exécution supplantée n'écrit rien
# --------------------------------------------------------------------------- #
def test_ac323_extraction_supplantee_n_insere_ni_ne_redate(monkeypatch):
    with SessionLocal() as db:
        mission = Mission(name="Mission s32")
        db.add(mission)
        db.flush()
        interview = Interview(mission_id=mission.id, mode="libre", interviewee_name="Alice",
                              status="done", audio_segments=[])
        db.add(interview)
        db.commit()
        iid = interview.id
    job_id = _job("extraction", multi=True, interview_id=iid)
    vu = {}

    def _iter(content, block_s=None, start_index=0, stats=None):
        yield 0, 1, "un bloc de texte."

    def _run_segment_job(segment_id):
        if "reprise" not in vu:                     # reprise PENDANT la 1re tranche
            vu["reprise"] = audio_file_jobs._prendre_job(job_id)
            vu["date"] = _relire(job_id).created_at
            time.sleep(0.01)

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    monkeypatch.setattr(audio_file_jobs, "tranches_extraction", lambda blocs: ["t1", "t2"])
    monkeypatch.setattr(audio_file_jobs, "run_segment_job", _run_segment_job)
    try:
        audio_file_jobs.run_audio_file_job(job_id)
        jeton = _relire(job_id)
        with SessionLocal() as db:
            segments = db.query(InterviewSegmentJob).filter_by(
                session_token=jeton.session_token).all()
        assert [s.position for s in segments] == [0]          # pas de 2e tranche
        assert jeton.created_at == vu["date"]                  # pas re-daté
        assert jeton.status == "running"                       # statut du repreneur intact
    finally:
        audio_file_jobs._PROPRIETAIRES.pop(job_id, None)


def _interview_id() -> int:
    with SessionLocal() as db:
        mission = Mission(name="Mission s32 bis")
        db.add(mission)
        db.flush()
        interview = Interview(mission_id=mission.id, mode="libre", interviewee_name="Bob",
                              status="done", audio_segments=[])
        db.add(interview)
        db.commit()
        return interview.id


def _segments(session_token: str) -> list:
    with SessionLocal() as db:
        return sorted(s.position for s in db.query(InterviewSegmentJob).filter_by(
            session_token=session_token))


def test_ac323_supplantee_avant_extraction_n_insere_rien(monkeypatch):
    job_id = _job("extraction_avant", multi=True, interview_id=_interview_id())

    def _iter(content, block_s=None, start_index=0, stats=None):
        yield 0, 1, "un bloc."

    def _tranches(blocs):
        audio_file_jobs._prendre_job(job_id)       # reprise juste avant la 1re insertion
        return ["t1", "t2"]

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    monkeypatch.setattr(audio_file_jobs, "tranches_extraction", _tranches)
    monkeypatch.setattr(audio_file_jobs, "run_segment_job", lambda segment_id: None)
    try:
        audio_file_jobs.run_audio_file_job(job_id)
        assert _segments(_relire(job_id).session_token) == []
    finally:
        audio_file_jobs._PROPRIETAIRES.pop(job_id, None)


def test_ac323_supplantee_pendant_un_rejeu_de_tranche_ne_redate_ni_n_insere(monkeypatch):
    job_id = _job("extraction_rejeu", multi=True, interview_id=_interview_id())
    jeton = _relire(job_id).session_token
    with SessionLocal() as db:                      # tranche 0 connue, non aboutie
        db.add(InterviewSegmentJob(session_token=jeton, position=0, status="failed",
                                   text="t1", kind="libre_turns",
                                   interview_id=_relire(job_id).interview_id))
        db.commit()
    vu = {}

    def _iter(content, block_s=None, start_index=0, stats=None):
        yield 0, 1, "un bloc."

    def _run_segment_job(segment_id):
        if "date" not in vu:
            audio_file_jobs._prendre_job(job_id)
            vu["date"] = _relire(job_id).created_at
            time.sleep(0.01)

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    monkeypatch.setattr(audio_file_jobs, "tranches_extraction", lambda blocs: ["t1", "t2"])
    monkeypatch.setattr(audio_file_jobs, "run_segment_job", _run_segment_job)
    try:
        audio_file_jobs.run_audio_file_job(job_id)
        assert _segments(jeton) == [0]
        assert _relire(job_id).created_at == vu["date"]
    finally:
        audio_file_jobs._PROPRIETAIRES.pop(job_id, None)


# --------------------------------------------------------------------------- #
# AC3.2.5 / C-7 — mémoire insuffisante : jamais avalée comme tranche illisible
# --------------------------------------------------------------------------- #
def test_ac325_memoire_insuffisante_echoue_le_job_sans_ignorer_la_tranche(monkeypatch):
    def _iter(content, block_s=None, start_index=0, stats=None):
        raise at.TranscriptionMemoireInsuffisanteError(at.MESSAGE_MEMOIRE_INSUFFISANTE)
        yield  # noqa: unreachable

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    job_id = _job("memoire", multi=True)
    audio_file_jobs.run_audio_file_job(job_id)
    job = _relire(job_id)
    assert job.status == "failed", (job.status, job.error)
    assert "fermez des applications" in (job.error or "")
    assert job.files_done in (None, 0)                         # tranche NON ignorée
    assert "illisible" not in (job.error or "")


# --------------------------------------------------------------------------- #
# Re-revue 3.2 (2026-11-17)
# --------------------------------------------------------------------------- #
def test_m1_prechauffage_echoue_le_chargement_repasse_par_le_controle_memoire(
        monkeypatch, delais_courts, un_bloc):
    """M1 : le garde laisse passer un préchauffage en cours ; celui-ci échoue
    (`_model` reste None, verrou rendu) — le fil de repli ne charge PAS ~1,6 Gio
    sans contrôle : échec « mémoire » immédiat, aucune construction."""
    constructions = []

    class _Fw:
        class WhisperModel:
            def __init__(self, *a, **k):
                constructions.append(1)

    monkeypatch.setattr(at, "_faster_whisper", lambda: _Fw)
    monkeypatch.setattr(at, "_model", None)
    monkeypatch.setattr(at, "memoire_disponible", lambda: 1 * 1024 ** 3)
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", int(1.6 * 1024 ** 3))
    monkeypatch.setattr(at, "ATTENTE_CHARGEMENT_FOND_S", 5.0)
    monkeypatch.setattr(at, "_MODEL_LOAD_LOCK", threading.Lock())
    at._MODEL_LOAD_LOCK.acquire()                               # préchauffage en cours…
    minuterie = threading.Timer(0.3, at._MODEL_LOAD_LOCK.release)  # …qui échoue
    minuterie.start()
    try:
        with pytest.raises(at.TranscriptionMemoireInsuffisanteError):
            list(at.iter_transcribe_blocks(b"x", 60))
    finally:
        minuterie.join(2)
    assert constructions == []


def test_m3_aucun_worker_pendant_un_prechauffage_rend_un_occupe(monkeypatch):
    """M3 : aucun worker ne tient en mémoire, modèle non chargé MAIS préchauffage en
    cours — « occupé » réessayable, pas un échec définitif « mémoire »."""
    monkeypatch.setattr(at, "ProcessPoolExecutor", _PoolInitEnLigne)
    monkeypatch.setattr(at, "memoire_disponible", lambda: 0)
    monkeypatch.setattr(at, "_model", None)
    monkeypatch.setattr(at, "ATTENTE_CHARGEMENT_FOND_S", 0.2)   # F1 : attente bornée, puis « occupé »
    monkeypatch.setattr(at, "_MODEL_LOAD_LOCK", threading.Lock())
    at._MODEL_LOAD_LOCK.acquire()
    try:
        with pytest.raises(at.TranscriptionChargementEnCoursError) as e:
            list(at._drain_parallel(_blocs_s32(2), 0, 2, 2))
    finally:
        at._MODEL_LOAD_LOCK.release()
    assert audio_file_jobs._est_reessayable(e.value) is True


# --------------------------------------------------------------------------- #
# Revue finale 3.2 (2026-11-17)
# --------------------------------------------------------------------------- #
def test_f1_aucun_worker_attend_le_prechauffage_puis_transcrit(monkeypatch):
    """F1 : aucun worker en mémoire, préchauffage en cours qui ABOUTIT pendant
    l'attente — les blocs passent sur le modèle chargé au lieu d'un « occupé »
    immédiat (qui faisait re-décoder tout le fichier toutes les 2 s)."""
    monkeypatch.setattr(at, "ProcessPoolExecutor", _PoolInitEnLigne)
    monkeypatch.setattr(at, "memoire_disponible", lambda: 0)
    monkeypatch.setattr(at, "_model", None)
    monkeypatch.setattr(at, "ATTENTE_CHARGEMENT_FOND_S", 10.0)
    monkeypatch.setattr(at, "_sequentiel_borne", lambda pcm, prio, i, total: f"ok{i}")
    monkeypatch.setattr(at, "_MODEL_LOAD_LOCK", threading.Lock())
    at._MODEL_LOAD_LOCK.acquire()                               # préchauffage en cours…

    def _fin_prechauffage():
        at._model = object()                                    # …qui aboutit
        at._MODEL_LOAD_LOCK.release()

    minuterie = threading.Timer(0.3, _fin_prechauffage)
    minuterie.start()
    try:
        assert list(at._drain_parallel(_blocs_s32(2), 0, 2, 2)) == [(0, "ok0"), (1, "ok1")]
    finally:
        minuterie.join(5)


def test_f1_job_rejoue_un_chargement_au_rythme_annonce(monkeypatch):
    """F1 : le job attend `retry_after_s` (plafonné) entre deux rejeux d'un
    « chargement en cours », pas BLOC_RETRY_DELAY_S — chaque rejeu re-décode le
    fichier entier. Sur 1,5 s de chargement : ~5 essais, pas ~75."""
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.02)
    monkeypatch.setattr(at, "CHARGEMENT_MODELE_TIMEOUT_S", 1.5)
    appels = []

    def _iter(content, block_s=None, start_index=0, stats=None):
        appels.append(1)                                      # = un décodage complet
        exc = at.TranscriptionChargementEnCoursError("Chargement du modèle en cours, réessayez.")
        exc.retry_after_s = 0.4
        raise exc
        yield  # noqa: unreachable

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    job_id = _job("chargement_rythme")
    audio_file_jobs.run_audio_file_job(job_id)
    job = _relire(job_id)
    assert job.status == "failed"
    assert "pas pu être prêt" in (job.error or "")
    assert len(appels) <= 8, len(appels)


def test_f1_delai_de_rejeu_plafonne_et_plancher():
    exc = at.TranscriptionChargementEnCoursError("x")
    exc.retry_after_s = 10_000.0
    assert audio_file_jobs._delai_rejeu_chargement(exc) == at.ATTENTE_CHARGEMENT_FOND_S
    assert audio_file_jobs._delai_rejeu_chargement(
        at.TranscriptionChargementEnCoursError("x")) == audio_file_jobs.BLOC_RETRY_DELAY_S


_FAUX_FASTER_WHISPER_TRANSCRIPTION_LENTE = '''
import time


class WhisperModel:
    def __init__(self, *args, **kwargs):
        pass                                    # chargement instantané

    def transcribe(self, pcm, **kwargs):
        time.sleep(120)                          # bloc figé (mémoire saturée)
        return [], None
'''


def test_f6_pool_reel_modele_pret_bloc_fige_echoue_au_delai_du_bloc(tmp_path, monkeypatch):
    """F6 : pool RÉEL, modèle prêt aussitôt, bloc figé. Le délai du BLOC (1 s) doit
    jouer : échec « n'avance plus ». Si `_init_worker`/`_modele_worker` cessaient de
    mettre à jour les compteurs, le parent croirait le modèle toujours en
    chargement et n'échouerait qu'à la borne du chargement (« pas pu être prêt »).
    La borne du chargement (10 s) couvre le démarrage `spawn` du worker, qui compte
    dans l'épisode : à ~1 s elle ferait échouer aussi le code correct."""
    paquet = tmp_path / "fauxfw"
    paquet.mkdir()
    (paquet / "faster_whisper.py").write_text(_FAUX_FASTER_WHISPER_TRANSCRIPTION_LENTE,
                                              encoding="utf-8")
    monkeypatch.syspath_prepend(str(paquet))
    monkeypatch.setattr(at, "memoire_disponible", lambda: 64 * 1024 ** 3)
    monkeypatch.setattr(at, "BLOC_RESULTAT_TIMEOUT_S", 1.0)
    monkeypatch.setattr(at, "BATTEMENT_ATTENTE_S", 0.1)
    monkeypatch.setattr(at, "CHARGEMENT_MODELE_TIMEOUT_S", 10.0)
    with pytest.raises(at.TranscriptionBloqueeError) as e:
        list(at._drain_parallel(_blocs_s32(1), 0, 1, 1))
    assert "n'avance plus" in str(e.value), str(e.value)


def test_f7_rejeux_du_pool_acquis_pendant_un_chargement(monkeypatch):
    """F7 : un rejeu du pool signalé par un appel qui finit en « chargement en
    cours » survit à la remise à zéro de `stats` par l'appel suivant."""
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.0)
    appels = []

    def _iter(content, block_s=None, start_index=0, stats=None):
        appels.append(1)
        if stats is not None:
            stats["blocs_rejoues"] = [0] if len(appels) == 1 else []   # remis à zéro
        if len(appels) == 1:
            raise at.TranscriptionChargementEnCoursError("Chargement du modèle en cours, réessayez.")
        yield 0, 1, "bloc."

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    job_id = _job("chargement_rejeu_pool")
    audio_file_jobs.run_audio_file_job(job_id)
    job = _relire(job_id)
    assert job.status == "done", (job.status, job.error)
    assert 0 in ((job.completude or {}).get("blocs_rejoues") or [])


def test_f7_supplantee_avant_rejeu_d_une_tranche_connue_ne_la_rejoue_pas(monkeypatch):
    """F7 : supplanté juste avant le rejeu d'une tranche connue non aboutie — la
    tranche n'est pas rejouée (le garde passe AVANT `run_segment_job`)."""
    job_id = _job("extraction_rejeu_avant", multi=True, interview_id=_interview_id())
    jeton = _relire(job_id).session_token
    with SessionLocal() as db:
        db.add(InterviewSegmentJob(session_token=jeton, position=0, status="failed",
                                   text="t1", kind="libre_turns",
                                   interview_id=_relire(job_id).interview_id))
        db.commit()
    rejoues = []

    def _iter(content, block_s=None, start_index=0, stats=None):
        yield 0, 1, "un bloc."

    def _tranches(blocs):
        audio_file_jobs._prendre_job(job_id)       # reprise juste avant le rejeu
        return ["t1", "t2"]

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    monkeypatch.setattr(audio_file_jobs, "tranches_extraction", _tranches)
    monkeypatch.setattr(audio_file_jobs, "run_segment_job", rejoues.append)
    try:
        audio_file_jobs.run_audio_file_job(job_id)
        assert rejoues == []
        assert _segments(jeton) == [0]
    finally:
        audio_file_jobs._PROPRIETAIRES.pop(job_id, None)


class _PoolDeuxiemeWorkerTardif(at.ProcessPoolExecutor):
    """Vraie classe (garde « pool réel »), aucune tâche exécutée. Worker 1 servi et
    prêt d'emblée ; le bloc 0 est rendu tout de suite. Le worker 2 n'est servi
    qu'à `SERVI_A` s (après la borne de chargement comptée depuis le pool), puis
    prêt et le bloc 1 rendu à `PRET_A` s."""
    SERVI_A = 1.3
    PRET_A = 1.8

    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=()):
        super().__init__(max_workers=max_workers, mp_context=mp_context)
        self._prets, self._servis = initargs
        self._prets.value = self._servis.value = 1
        self._n = 0
        self._minuteries = []

    def submit(self, fn, *args, **kwargs):
        f: Future = Future()
        self._n += 1
        if self._n == 1:
            f.set_result("bloc 0")
            return f

        def _servir():
            with self._servis.get_lock():
                self._servis.value += 1

        def _pret():
            with self._prets.get_lock():
                self._prets.value += 1
            f.set_result("bloc 1")

        for delai, action in ((self.SERVI_A, _servir), (self.PRET_A, _pret)):
            t = threading.Timer(delai, action)
            t.daemon = True
            t.start()
            self._minuteries.append(t)
        return f


def test_m2_borne_du_chargement_comptee_depuis_l_episode_pas_le_pool(monkeypatch):
    """M2 : un 2e worker servi APRÈS CHARGEMENT_MODELE_TIMEOUT_S (compté depuis le
    démarrage du pool) dispose de sa borne entière ; avant, échec immédiat."""
    monkeypatch.setattr(at, "ProcessPoolExecutor", _PoolDeuxiemeWorkerTardif)
    monkeypatch.setattr(at, "memoire_disponible", lambda: 64 * 1024 ** 3)
    monkeypatch.setattr(at, "CHARGEMENT_MODELE_TIMEOUT_S", 1.0)
    monkeypatch.setattr(at, "BLOC_RESULTAT_TIMEOUT_S", 2.0)
    monkeypatch.setattr(at, "BATTEMENT_ATTENTE_S", 0.05)
    assert list(at._drain_parallel(_blocs_s32(2), 0, 2, 2)) == [(0, "bloc 0"), (1, "bloc 1")]


def test_b1_chargement_supplante_entre_bilan_et_battement_n_ecrit_rien(monkeypatch):
    """B1 : supplanté PENDANT la consignation du bilan (après l'ancienne
    vérification) — le battement ne commit rien pour le fantôme."""
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.05)
    monkeypatch.setattr(at, "CHARGEMENT_MODELE_TIMEOUT_S", 1.0)
    job_id = _job("chargement_supplante_bilan")
    vu = {}
    vrai_bilan = at.bilan_completude

    def _iter(content, block_s=None, start_index=0, stats=None):
        if stats is not None:
            stats["blocs_rejoues"] = [0]                       # rejeu du pool : bilan noté
        vu["chargement"] = True
        raise at.TranscriptionChargementEnCoursError("Chargement du modèle en cours, réessayez.")
        yield  # noqa: unreachable

    def _bilan(*a, **k):
        if vu.get("chargement") and "date" not in vu:
            audio_file_jobs._prendre_job(job_id)              # reprise par une autre exécution
            vu["date"] = _relire(job_id).created_at
            time.sleep(0.01)
        return vrai_bilan(*a, **k)

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    monkeypatch.setattr(at, "bilan_completude", _bilan)
    try:
        audio_file_jobs.run_audio_file_job(job_id)
        assert "date" in vu
        job = _relire(job_id)
        assert job.created_at == vu["date"]                    # pas re-daté par le fantôme
        assert job.status == "running"
    finally:
        audio_file_jobs._PROPRIETAIRES.pop(job_id, None)


def test_b3_modele_worker_compte_servis_et_rend_sur_echec(monkeypatch):
    """B3 : `servis` monte au début du chargement, redescend s'il échoue ; `prets`
    ne monte qu'au succès."""
    import multiprocessing

    essais = []

    class _Fw:
        class WhisperModel:
            def __init__(self, *a, **k):
                essais.append(servis.value)
                if len(essais) == 1:
                    raise RuntimeError("chargement raté")

    prets = multiprocessing.Value("i", 0)
    servis = multiprocessing.Value("i", 0)
    monkeypatch.setattr(at, "_faster_whisper", lambda: _Fw)
    monkeypatch.setattr(at, "_worker_model", None)
    monkeypatch.setattr(at, "_workers_prets", prets)
    monkeypatch.setattr(at, "_workers_servis", servis)
    with pytest.raises(RuntimeError):
        at._modele_worker(1)
    assert (servis.value, prets.value) == (0, 0)
    assert at._modele_worker(1) is not None
    assert essais == [1, 1]                                    # compté PENDANT le chargement
    assert (servis.value, prets.value) == (1, 1)


# --------------------------------------------------------------------------- #
# Arbitrage utilisateur 2026-11-17 (AC3.2.1, 2e clause) : pool chaud réutilisé
# --------------------------------------------------------------------------- #
_FAUX_FASTER_WHISPER_COMPTE = '''
import os, pathlib, time, uuid


class _Segment:
    def __init__(self, text):
        self.text = text


class WhisperModel:
    def __init__(self, *args, **kwargs):
        d = pathlib.Path(os.environ["S32_COMPTE_DIR"])
        (d / f"charge-{os.getpid()}-{uuid.uuid4().hex}").write_text("x")

    def transcribe(self, pcm, **kwargs):
        d = pathlib.Path(os.environ["S32_COMPTE_DIR"])
        n = len(pcm)
        if n == int(os.environ.get("S32_ECHEC_LEN", "-1")) and not (d / "echoue").exists():
            (d / "echoue").write_text("x")
            raise RuntimeError("echec transitoire du bloc")
        if n == int(os.environ.get("S32_FIGE_LEN", "-1")) and not (d / "fige").exists():
            (d / "fige").write_text("x")
            time.sleep(120)                      # worker figé
        return [_Segment(f"bloc-{n}")], None
'''


@pytest.fixture
def faux_fw_compte(tmp_path, monkeypatch):
    """Faux `faster_whisper` importé par les workers `spawn` : chaque construction
    du modèle laisse un fichier `charge-*` dans `compte`."""
    at.fermer_pool_chaud()
    paquet = tmp_path / "fauxfw"
    paquet.mkdir()
    (paquet / "faster_whisper.py").write_text(_FAUX_FASTER_WHISPER_COMPTE, encoding="utf-8")
    compte = tmp_path / "compte"
    compte.mkdir()
    monkeypatch.syspath_prepend(str(paquet))
    monkeypatch.setenv("S32_COMPTE_DIR", str(compte))
    monkeypatch.setattr(at, "memoire_disponible", lambda: 64 * 1024 ** 3)
    monkeypatch.setattr(at, "BATTEMENT_ATTENTE_S", 0.1)
    monkeypatch.setattr(at, "CHARGEMENT_MODELE_TIMEOUT_S", 20.0)
    yield compte
    at.fermer_pool_chaud()


def _chargements(compte) -> int:
    return len(list(compte.glob("charge-*")))


def test_arb1117_deux_essais_du_job_sur_pool_chaud_chargent_le_modele_une_fois(
        faux_fw_compte, monkeypatch):
    """Job réel, pool RÉEL d'un worker. Essai 1 : le bloc 0 échoue dans le worker
    (échec transitoire, worker vivant), le repli séquentiel échoue aussi — le job
    rejoue (niveau job). Essai 2 : le pool garé est repris, le modèle n'est PAS
    rechargé. Avant : un pool neuf par essai, 2 chargements."""
    monkeypatch.setenv("S32_ECHEC_LEN", str(60 * 16000))
    monkeypatch.setattr(at, "MAX_PARALLEL_WORKERS", 1)
    monkeypatch.setattr(at, "BLOC_RETRY_PAUSE_S", 0.0)
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.0)
    monkeypatch.setattr(at, "_decode_to_pcm16k",
                        lambda content: np.zeros(60 * 16000 + 8000, dtype=np.float32))

    def _sequentiel_en_echec(*args, **kwargs):
        raise at.TranscriptionError(at.ECHEC_TRANSCRIPTION)

    monkeypatch.setattr(at, "_transcrire_bloc_sequentiel", _sequentiel_en_echec)
    job_id = _job("pool-chaud")
    audio_file_jobs.run_audio_file_job(job_id)
    job = _relire(job_id)
    assert job.status == "done", (job.status, job.error)
    assert job.blocks == [f"bloc-{60 * 16000}", "bloc-8000"]
    assert (faux_fw_compte / "echoue").exists()            # l'essai 1 a bien échoué
    assert _chargements(faux_fw_compte) == 1


def test_arb1117_worker_fige_arrete_le_pool_et_le_suivant_est_neuf(faux_fw_compte, monkeypatch):
    """Un worker figé n'est jamais garé : pool arrêté (aucun processus restant),
    l'appel suivant démarre un pool neuf et recharge le modèle."""
    import multiprocessing

    monkeypatch.setenv("S32_FIGE_LEN", "1600")
    monkeypatch.setattr(at, "BLOC_RESULTAT_TIMEOUT_S", 1.0)
    with pytest.raises(at.TranscriptionBloqueeError):
        list(at._drain_parallel(_blocs_s32(1), 0, 1, 1))
    assert at._POOL_CHAUD is None
    assert multiprocessing.active_children() == []
    assert list(at._drain_parallel(_blocs_s32(1), 0, 1, 1)) == [(0, "bloc-1600")]
    assert _chargements(faux_fw_compte) == 2
    assert at._POOL_CHAUD is not None                       # pool sain : garé
    at.fermer_pool_chaud()
    assert multiprocessing.active_children() == []


def _pool_gare_factice(taille: int = 2):
    executor = at.ProcessPoolExecutor(max_workers=taille)    # aucun processus lancé
    entree = at._PoolChaud(executor, taille, None, None, at._cle_pool())
    at._garer_pool(entree)
    return executor


def test_arb1117_pool_garé_arrete_apres_inactivite(monkeypatch):
    at.fermer_pool_chaud()
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 0.2)
    executor = _pool_gare_factice()
    assert at._POOL_CHAUD is not None
    fin = time.monotonic() + 5
    # La minuterie retire le pool du holder SOUS verrou, puis l'arrête HORS
    # verrou : attendre les deux, sinon le test lit l'arrêt entre les deux
    # étapes (rouge une fois sur la CI Linux, run #77 du 2026-11-17).
    while (at._POOL_CHAUD is not None or not executor._shutdown_thread) \
            and time.monotonic() < fin:
        time.sleep(0.05)
    assert at._POOL_CHAUD is None
    assert executor._shutdown_thread


class _PoolInterdit(at.ProcessPoolExecutor):
    def __init__(self, *args, **kwargs):
        raise AssertionError("aucun pool ne doit démarrer sous pression mémoire")


def test_arb1117_pool_garé_arrete_sous_pression_memoire(monkeypatch):
    at.fermer_pool_chaud()
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    executor = _pool_gare_factice()
    # Aucun worker ne tient, même en récupérant le pool garé (marge démesurée) :
    # jamais de pool réel démarré ici, donc jamais le vrai modèle chargé.
    monkeypatch.setattr(at, "memoire_disponible", lambda: 0)
    monkeypatch.setattr(at, "MEMOIRE_MARGE_OCTETS", 10 ** 15)
    monkeypatch.setattr(at, "_model", None)
    monkeypatch.setattr(at, "ProcessPoolExecutor", _PoolInterdit)
    with pytest.raises(at.TranscriptionMemoireInsuffisanteError):
        list(at._drain_parallel(_blocs_s32(2), 0, 2, 2))
    assert at._POOL_CHAUD is None
    assert executor._shutdown_thread


def test_arb1117_garde_memoire_compte_le_pool_garé_comme_recuperable(monkeypatch):
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", 1024 ** 3)
    monkeypatch.setattr(at, "MEMOIRE_MARGE_OCTETS", 0)
    monkeypatch.setattr(at, "memoire_disponible", lambda: 1024 ** 3)
    assert at._workers_selon_memoire(4) == 1
    assert at._workers_selon_memoire(4, credit_workers=2) == 3


# --------------------------------------------------------------------------- #
# Re-revue 2026-11-17 du pool chaud (P1, P2, B4)
# --------------------------------------------------------------------------- #
class _ProcFactice:
    pid = 0

    def __init__(self, vivant: bool = True):
        self._vivant = vivant

    def is_alive(self):
        return self._vivant

    def terminate(self):
        self._vivant = False

    kill = terminate

    def join(self, timeout=None):
        pass


class _ExecFactice:
    """Exécuteur garé factice : aucun processus réel, `submit` absent (le
    reprendre à tort fait échouer le test)."""

    def __init__(self, vivants: int, morts: int = 0):
        procs = [_ProcFactice(True) for _ in range(vivants)] + [_ProcFactice(False) for _ in range(morts)]
        self._processes = dict(enumerate(procs))
        self._broken = False
        self._shutdown_thread = False

    def shutdown(self, wait=True, cancel_futures=False):
        self._shutdown_thread = True


class _Compteur:
    def __init__(self, value: int):
        self.value = value


def _garer_factice(taille: int, vivants: int, prets: int) -> _ExecFactice:
    executor = _ExecFactice(vivants)
    at._garer_pool(at._PoolChaud(executor, taille, _Compteur(prets), _Compteur(prets), at._cle_pool()))
    return executor


class _PoolEnLigne(at.ProcessPoolExecutor):
    """Vraie sous-classe promue « pool réel » par le test : aucun processus, aucune
    tâche exécutée (jamais le vrai modèle) — chaque bloc rend « ok »."""
    demarres: list = []

    def __init__(self, max_workers=None, mp_context=None, **kw):
        _PoolEnLigne.demarres.append(max_workers)
        super().__init__(max_workers=max_workers, mp_context=mp_context, **kw)

    def submit(self, fn, *args, **kwargs):
        f = Future()
        f.set_result("ok")
        return f


@pytest.fixture
def pool_en_ligne(monkeypatch):
    _PoolEnLigne.demarres = []
    monkeypatch.setattr(at, "ProcessPoolExecutor", _PoolEnLigne)
    monkeypatch.setattr(at, "_POOL_REEL", _PoolEnLigne)          # garable + garde mémoire
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    monkeypatch.setattr(at, "GLOBAL_MAX_PARALLEL_WORKERS", 4)
    monkeypatch.setattr(at, "_GLOBAL_WORKERS_SEMAPHORE", threading.Semaphore(4))
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", 1024 ** 3)
    monkeypatch.setattr(at, "MEMOIRE_MARGE_OCTETS", 0)
    return _PoolEnLigne


def test_rr_p1a_credit_memoire_ne_compte_que_les_workers_vivants_et_charges(monkeypatch):
    """Pool garé de taille 4 dont `spawn` n'a lancé qu'UN processus : seule la
    mémoire de ce worker chargé est récupérable (avant : 4 créditées)."""
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    _garer_factice(taille=4, vivants=1, prets=1)
    assert at._credit_pool_chaud() == 1
    at.fermer_pool_chaud()
    _garer_factice(taille=4, vivants=2, prets=0)                # en chargement
    assert at._credit_pool_chaud() == 0
    at.fermer_pool_chaud()
    assert at._credit_pool_chaud() == 0                         # rien de garé


def test_rr_p1b_pool_gare_repris_par_un_autre_garde_memoire_recalculee(pool_en_ligne, monkeypatch):
    """Le crédit du pool garé est lu avant l'attente des permis ; un autre appel le
    reprend entre-temps (`_prendre_pool_chaud` rend None) : la garde est recalculée
    SANS crédit — 1 Gio libre = 1 worker, pas 3."""
    monkeypatch.setattr(at, "memoire_disponible", lambda: 1024 ** 3)
    monkeypatch.setattr(at, "_credit_pool_chaud", lambda: 2)
    rendus = list(at._drain_parallel(_blocs_s32(3), 0, 3, 3))
    assert [i for i, _ in rendus] == [0, 1, 2]
    assert pool_en_ligne.demarres == [1]
    assert at._GLOBAL_WORKERS_SEMAPHORE._value == 4             # permis tous rendus


def test_rr_p2_pool_gare_plus_petit_que_le_palier_est_remplace(pool_en_ligne, monkeypatch):
    """Paliers descendants : un pool garé d'1 worker n'est pas repris par un appel
    qui en demande 2 — il est arrêté, un pool de 2 démarre."""
    monkeypatch.setattr(at, "MAX_PARALLEL_WORKERS", 2)
    monkeypatch.setattr(at, "memoire_disponible", lambda: 64 * 1024 ** 3)
    petit = _garer_factice(taille=1, vivants=1, prets=1)
    rendus = list(at._drain_parallel(_blocs_s32(2), 0, 2, at.MAX_PARALLEL_WORKERS))
    assert [i for i, _ in rendus] == [0, 1]
    assert petit._shutdown_thread                                # arrêté
    assert pool_en_ligne.demarres == [2]
    assert at._POOL_CHAUD is not None and at._POOL_CHAUD.taille == 2


def test_rr_p2_pool_gare_de_la_taille_demandee_est_repris(monkeypatch):
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    executor = _garer_factice(taille=2, vivants=2, prets=2)
    entree = at._prendre_pool_chaud(2, at._cle_pool())
    assert entree is not None and entree.executor is executor
    assert entree.minuterie is None and not executor._shutdown_thread


def test_rr_b4_inactivite_nulle_ne_gare_pas_et_ne_ferme_pas_le_pool_d_un_autre(monkeypatch):
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    autre = _garer_factice(taille=1, vivants=1, prets=1)
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 0.0)
    moi = _garer_factice(taille=1, vivants=1, prets=1)
    assert moi._shutdown_thread                                  # jamais garé
    assert at._POOL_CHAUD is not None and at._POOL_CHAUD.executor is autre
    assert not autre._shutdown_thread


def test_rr_b4_regarer_annule_la_minuterie_precedente(monkeypatch):
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    entree = at._PoolChaud(_ExecFactice(1), 1, _Compteur(1), _Compteur(1), at._cle_pool())
    at._garer_pool(entree)
    premiere = entree.minuterie
    at._garer_pool(entree)
    assert premiere.finished.is_set()                            # annulée
    assert entree.minuterie is not premiere and entree.minuterie.is_alive()
