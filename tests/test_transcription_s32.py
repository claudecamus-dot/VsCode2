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
    future: Future = Future()
    debut = time.monotonic()
    threading.Timer(0.9, lambda: future.set_result("fini")).start()
    # Modèle « prêt » à 0,7 s : bien au-delà du délai de bloc (0,3 s).
    chargement = lambda: time.monotonic() - debut < 0.7   # noqa: E731
    assert at._attendre_resultat(future, 0, 1, chargement) == "fini"


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
    debut = time.monotonic()
    with pytest.raises(at.TranscriptionBloqueeError) as e:
        at._sequentiel_borne(np.zeros(160, dtype=np.float32), at.FOND, 0, 1)
    assert time.monotonic() - debut < 3
    assert "pas pu être prêt" in str(e.value)
    libere.set()
    time.sleep(0.3)
    assert modele.appels == 0                                   # bloc jeté, pas transcrit
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
    at._MODEL_LOCK.acquire(timeout=1, priority=at.FOND)
    try:
        with pytest.raises(KeyboardInterrupt):
            at._sequentiel_borne(np.zeros(160, dtype=np.float32), at.FOND, 0, 1)
    finally:
        at._MODEL_LOCK.release()
    time.sleep(0.5)
    assert modele.appels == 0


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
            time.sleep(0.35)        # nouvel épisode, après la borne comptée depuis le 1er
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
