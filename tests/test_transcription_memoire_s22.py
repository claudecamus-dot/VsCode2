"""Story 2.2 — transcrire un long audio sans saturer la mémoire ni rester figé.

AC2.2.1 : un processus worker charge le modèle UNE fois, pas à chaque bloc.
AC2.2.2 : le nombre de workers est réduit à ce que la mémoire disponible permet.
AC2.2.3 : l'attente d'un bloc est bornée (échec propre, workers arrêtés, permis
rendus), le job bat le cœur pendant l'attente, et une exécution supplantée
n'écrit plus dans un job repris.

Jamais le vrai modèle Whisper : les tests « pool réel » démarrent UN processus
`spawn` qui importe un faux paquet `faster_whisper` posé en tête de `sys.path`
(le processus enfant hérite du `sys.path` et de l'environnement du parent).
"""
from __future__ import annotations

import multiprocessing
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor

import numpy as np
import pytest

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.models import AudioFileJob
from app.services import audio_file_jobs, audio_transcribe as at
from tests.conftest_pool_chaud import pool_chaud_isole  # noqa: F401  (autouse)

_PREFIXE = "s22_memoire_test_"

_FAUX_FASTER_WHISPER = '''
import os, time


class _Segment:
    def __init__(self, text):
        self.text = text


class WhisperModel:
    def __init__(self, *args, **kwargs):
        with open(os.environ["S22_COMPTEUR"], "a", encoding="ascii") as f:
            f.write(f"{os.getpid()}\\n")

    def transcribe(self, pcm, **kwargs):
        if float(pcm[0]) > 0.5:      # bloc marqué : figé (simule la pagination)
            time.sleep(120)
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


@pytest.fixture
def faux_whisper_enfant(tmp_path, monkeypatch):
    """Faux `faster_whisper` importé par les processus `spawn` du pool réel."""
    paquet = tmp_path / "fauxfw"
    paquet.mkdir()
    (paquet / "faster_whisper.py").write_text(_FAUX_FASTER_WHISPER, encoding="utf-8")
    compteur = tmp_path / "constructions.txt"
    compteur.write_text("", encoding="ascii")
    monkeypatch.syspath_prepend(str(paquet))
    monkeypatch.setenv("S22_COMPTEUR", str(compteur))
    # Assez de mémoire pour ne jamais réduire ici (le garde a son propre test).
    monkeypatch.setattr(at, "memoire_disponible", lambda: 64 * 1024 ** 3)
    return compteur


def _blocs(n: int, figes: tuple = ()) -> list:
    return [np.full(1600 + i, 0.9 if i in figes else 0.0, dtype=np.float32) for i in range(n)]


# --------------------------------------------------------------------------- #
# AC2.2.1 — pool RÉEL (1 processus spawn, faux modèle) : une construction par processus
# --------------------------------------------------------------------------- #
def test_ac221_un_worker_charge_le_modele_une_seule_fois(faux_whisper_enfant):
    rendus = list(at._drain_parallel(_blocs(4), 0, 4, 1))
    assert [i for i, _ in rendus] == [0, 1, 2, 3]
    assert [t for _, t in rendus] == [f"bloc-{1600 + i}" for i in range(4)]
    constructions = faux_whisper_enfant.read_text(encoding="ascii").split()
    assert len(constructions) == 1, constructions      # 4 avant le correctif (une par bloc)


# --------------------------------------------------------------------------- #
# AC2.2.2 — garde mémoire : workers réellement démarrés selon la mémoire injectée
# --------------------------------------------------------------------------- #
class _PoolEnregistreur(ProcessPoolExecutor):
    """Vraie classe (le garde ne vaut que pour elle) mais tâches exécutées en ligne :
    aucun processus démarré."""
    demarres: list = []

    def __init__(self, max_workers=None, mp_context=None, **kw):
        _PoolEnregistreur.demarres.append(max_workers)
        super().__init__(max_workers=max_workers, mp_context=mp_context, **kw)

    def submit(self, fn, *args, **kwargs):
        f = Future()
        f.set_result(fn(*args, **kwargs))
        return f


@pytest.fixture
def pool_enregistreur(monkeypatch):
    _PoolEnregistreur.demarres = []
    monkeypatch.setattr(at, "ProcessPoolExecutor", _PoolEnregistreur)
    monkeypatch.setattr(at, "_transcribe_pcm_chunk", lambda args: f"t{len(args[0])}")
    monkeypatch.setattr(at, "GLOBAL_MAX_PARALLEL_WORKERS", 8)
    monkeypatch.setattr(at, "_GLOBAL_WORKERS_SEMAPHORE", threading.Semaphore(8))
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", 2 * 1024 ** 3)
    monkeypatch.setattr(at, "MEMOIRE_MARGE_OCTETS", 1024 ** 3)
    return _PoolEnregistreur.demarres


def test_ac222_memoire_pour_trois_workers_en_demarre_trois(monkeypatch, pool_enregistreur):
    monkeypatch.setattr(at, "memoire_disponible", lambda: 7 * 1024 ** 3)   # (7-1)//2 = 3
    rendus = list(at._drain_parallel(_blocs(10), 0, 10, 8))
    assert pool_enregistreur == [3]
    assert len(rendus) == 10
    assert at._GLOBAL_WORKERS_SEMAPHORE._value == 8          # seuls 3 permis pris, tous rendus


def test_ac222_memoire_abondante_ne_reduit_rien(monkeypatch, pool_enregistreur):
    monkeypatch.setattr(at, "memoire_disponible", lambda: 64 * 1024 ** 3)
    list(at._drain_parallel(_blocs(10), 0, 10, 8))
    assert pool_enregistreur == [8]


def test_ac222_memoire_insuffisante_modele_charge_repli_sequentiel_borne(monkeypatch, pool_enregistreur):
    monkeypatch.setattr(at, "memoire_disponible", lambda: 2 * 1024 ** 3)   # (2-1)//2 = 0
    monkeypatch.setattr(at, "_model", object())                            # déjà chargé
    seq = []
    monkeypatch.setattr(at, "_transcribe_pcm_sequential", lambda pcm: seq.append(len(pcm)) or "s")
    rendus = list(at._drain_parallel(_blocs(3), 0, 3, 4))
    assert pool_enregistreur == []                            # aucun processus
    assert rendus == [(0, "s"), (1, "s"), (2, "s")]
    assert seq == [1600, 1601, 1602]


def test_ac222_repli_sequentiel_borne_par_le_delai_de_bloc(monkeypatch, pool_enregistreur):
    monkeypatch.setattr(at, "memoire_disponible", lambda: 2 * 1024 ** 3)
    monkeypatch.setattr(at, "_model", object())
    monkeypatch.setattr(at, "BLOC_RESULTAT_TIMEOUT_S", 0.3)
    monkeypatch.setattr(at, "BATTEMENT_ATTENTE_S", 0.05)
    libere = threading.Event()
    monkeypatch.setattr(at, "_transcribe_pcm_sequential", lambda pcm: libere.wait(10) and "tard")
    battements = []
    jeton = at.BATTEMENT_ATTENTE.set(lambda: battements.append(1))
    debut = time.monotonic()
    try:
        with pytest.raises(at.TranscriptionBloqueeError):
            list(at._drain_parallel(_blocs(2), 0, 2, 4))
    finally:
        at.BATTEMENT_ATTENTE.reset(jeton)
        libere.set()
    assert time.monotonic() - debut < 5
    assert battements                                          # vivant pendant l'attente


def test_ac222_memoire_insuffisante_modele_non_charge_echoue_sans_charger(monkeypatch, pool_enregistreur):
    monkeypatch.setattr(at, "memoire_disponible", lambda: 2 * 1024 ** 3)
    monkeypatch.setattr(at, "_model", None)
    charges = []
    monkeypatch.setattr(at, "_get_model", lambda: charges.append(1))
    monkeypatch.setattr(at, "_transcribe_pcm_sequential", lambda pcm: charges.append(2) or "s")
    with pytest.raises(at.TranscriptionMemoireInsuffisanteError) as e:
        list(at._drain_parallel(_blocs(3), 0, 3, 4))
    assert "fermez des applications" in str(e.value)
    assert charges == [] and pool_enregistreur == []
    assert audio_file_jobs._est_reessayable(e.value) is False


def test_ac222_memoire_indeterminable_garde_le_comportement_d_avant(monkeypatch, pool_enregistreur):
    monkeypatch.setattr(at, "memoire_disponible", lambda: None)
    list(at._drain_parallel(_blocs(4), 0, 4, 4))
    assert pool_enregistreur == [4]


def test_ac222_memoire_disponible_mesure_reelle_plausible():
    import os
    dispo = at.memoire_disponible()
    if os.name == "nt":
        assert dispo is not None and dispo > 0           # GlobalMemoryStatusEx doit répondre
    else:
        assert dispo is None or dispo > 0


# --------------------------------------------------------------------------- #
# AC2.2.3 — attente bornée, battement, arrêt des workers, permis rendus
# --------------------------------------------------------------------------- #
class _PoolFige:
    """Doublure dont les futures du bloc `fige` ne se résolvent jamais."""
    instances: list = []

    def __init__(self, max_workers=None, mp_context=None, fige=1, delai=None):
        self.termine = False
        self.fige, self.delai = fige, delai
        self.n = 0
        _PoolFige.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, args):
        f = Future()
        i, self.n = self.n, self.n + 1
        if i == self.fige:
            if self.delai is not None:
                threading.Timer(self.delai, f.set_result, args=(f"lent{i}",)).start()
            return f
        f.set_result(f"ok{i}")
        return f

    def terminate_workers(self):
        self.termine = True


def test_ac223_bloc_fige_echoue_proprement_et_rend_les_permis(monkeypatch):
    _PoolFige.instances = []
    monkeypatch.setattr(at, "ProcessPoolExecutor", lambda **kw: _PoolFige(**kw))
    monkeypatch.setattr(at, "GLOBAL_MAX_PARALLEL_WORKERS", 4)
    sem = threading.Semaphore(4)
    monkeypatch.setattr(at, "_GLOBAL_WORKERS_SEMAPHORE", sem)
    monkeypatch.setattr(at, "BLOC_RESULTAT_TIMEOUT_S", 0.3)
    rendus = []
    debut = time.monotonic()
    with pytest.raises(at.TranscriptionBloqueeError) as e:
        for i, t in at._drain_parallel(["a", "b", "c"], 0, 3, 2):
            rendus.append((i, t))
    assert time.monotonic() - debut < 5
    assert rendus == [(0, "ok0")]
    assert "Relancez la transcription" in str(e.value)
    assert _PoolFige.instances[0].termine is True
    assert sem._value == 4
    assert audio_file_jobs._est_reessayable(e.value) is False


def test_ac223_battement_pendant_l_attente_d_un_bloc_lent(monkeypatch):
    _PoolFige.instances = []
    monkeypatch.setattr(at, "ProcessPoolExecutor", lambda **kw: _PoolFige(fige=0, delai=0.5, **kw))
    monkeypatch.setattr(at, "BATTEMENT_ATTENTE_S", 0.1)
    monkeypatch.setattr(at, "BLOC_RESULTAT_TIMEOUT_S", 30.0)
    battements = []
    jeton = at.BATTEMENT_ATTENTE.set(lambda: battements.append(time.monotonic()))
    try:
        rendus = list(at._drain_parallel(["a", "b"], 0, 2, 1))
    finally:
        at.BATTEMENT_ATTENTE.reset(jeton)
    assert rendus == [(0, "lent0"), (1, "ok1")]
    assert len(battements) >= 3, battements                  # ~4 en 0,5 s ; 0 avant


def test_ac223_timeout_leve_par_la_tache_n_est_pas_un_bloc_fige(monkeypatch):
    class _Pool(_PoolFige):
        def submit(self, fn, args):
            f = Future()
            f.set_exception(TimeoutError("interne"))
            return f

    monkeypatch.setattr(at, "ProcessPoolExecutor", lambda **kw: _Pool(**kw))
    with pytest.raises(TimeoutError, match="interne") as e:
        list(at._drain_parallel(["a"], 0, 1, 1))
    assert not isinstance(e.value, at.TranscriptionBloqueeError)


def test_ac223_pool_reel_bloc_fige_aucun_processus_orphelin(faux_whisper_enfant, monkeypatch):
    avant = {p.pid for p in multiprocessing.active_children()}
    gen = at._drain_parallel(_blocs(3, figes=(1,)), 0, 3, 1)
    assert next(gen)[0] == 0                       # processus démarré, bloc 0 rendu
    monkeypatch.setattr(at, "BLOC_RESULTAT_TIMEOUT_S", 1.5)
    debut = time.monotonic()
    with pytest.raises(at.TranscriptionBloqueeError):
        next(gen)
    assert time.monotonic() - debut < 30           # le bloc figé dort 120 s
    restants = [p for p in multiprocessing.active_children() if p.pid not in avant]
    assert restants == [], restants


# --------------------------------------------------------------------------- #
# AC2.2.3 côté job : statut failed + message, y compris en retranscription
# --------------------------------------------------------------------------- #
def _job(nom: str, multi: bool = False) -> int:
    complet = f"{_PREFIXE}{nom}"
    (RECORDINGS_DIR / complet).write_bytes(b"x")
    with SessionLocal() as db:
        if multi:
            job = AudioFileJob(session_token=f"tok-s22-{nom}", status="pending", filename="",
                               filenames=[complet, complet], block_seconds=60, blocks=[])
        else:
            job = AudioFileJob(session_token=f"tok-s22-{nom}", status="pending", filename=complet)
        db.add(job)
        db.commit()
        return job.id


def _relire(job_id: int) -> AudioFileJob:
    with SessionLocal() as db:
        job = db.get(AudioFileJob, job_id)
        db.expunge(job)
        return job


@pytest.mark.parametrize("multi", [False, True], ids=["import", "retranscription"])
def test_ac223_job_bloc_fige_finit_failed_avec_message_de_relance(monkeypatch, multi):
    def _iter(content, block_s=None, start_index=0, stats=None):
        yield 0, 3, "premier bloc."
        raise at.TranscriptionBloqueeError(at.message_transcription_bloquee(900))

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    job_id = _job(f"fige_{multi}", multi)
    audio_file_jobs.run_audio_file_job(job_id)
    job = _relire(job_id)
    assert job.status == "failed", (job.status, job.error)
    assert "Relancez la transcription" in (job.error or "")
    assert job.blocks == ["premier bloc."]                  # acquis conservé
    assert job.files_done in (None, 0)                     # tranche NON ignorée


_ETAT_REPRENEUR = {"status": "running", "error": None, "files_done": 1,
                   "blocks_before_file": 1, "blocks": ["bloc du repreneur."]}


def _reprise_par_un_autre(job_id: int) -> object:
    """Reprise RÉELLE : un 2e propriétaire prend le job et écrit son propre état."""
    jeton = audio_file_jobs._prendre_job(job_id)
    with SessionLocal() as db:
        job = db.get(AudioFileJob, job_id)
        for cle, valeur in _ETAT_REPRENEUR.items():
            setattr(job, cle, valeur)
        db.commit()
    return jeton


@pytest.mark.parametrize("geste", [
    "bloc", "bloquee", "oserror", "runtime", "vide", "nospeech", "rejeu",
])
def test_ac223_execution_supplantee_n_ecrit_rien_chez_le_repreneur(monkeypatch, geste):
    """Le fantôme (1re exécution) agit APRÈS la reprise : l'état du repreneur
    (statut, erreur, curseurs de reprise, blocs) doit rester intact."""
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.0)
    job_id = _job(f"fantome_{geste}", multi=True)

    def _iter(content, block_s=None, start_index=0, stats=None):
        _reprise_par_un_autre(job_id)
        if geste == "bloc":
            yield 0, 2, "bloc fantome."
        elif geste == "bloquee":
            raise at.TranscriptionBloqueeError(at.message_transcription_bloquee(900))
        elif geste == "oserror":
            raise OSError("disque")
        elif geste == "runtime":
            raise RuntimeError("panne")
        elif geste == "nospeech":
            raise at.NoSpeechError("Aucune parole détectée dans l'enregistrement.")
        elif geste == "rejeu":
            raise at.TranscriptionError(at.ECHEC_TRANSCRIPTION)
        # « vide » : aucune itération, le fantôme file vers la fin du job

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    try:
        audio_file_jobs.run_audio_file_job(job_id)
        job = _relire(job_id)
        for cle, valeur in _ETAT_REPRENEUR.items():
            assert getattr(job, cle) == valeur, (geste, cle, getattr(job, cle))
    finally:
        audio_file_jobs._PROPRIETAIRES.pop(job_id, None)


def test_ac223_battement_d_une_execution_supplantee_ne_redate_pas_le_job(monkeypatch):
    job_id = _job("fantome_bat", multi=True)
    vu = {}

    def _iter(content, block_s=None, start_index=0, stats=None):
        _reprise_par_un_autre(job_id)
        vu["avant"] = _relire(job_id).created_at
        time.sleep(0.01)
        at.BATTEMENT_ATTENTE.get()()
        vu["apres"] = _relire(job_id).created_at
        raise RuntimeError("panne")
        yield  # noqa: unreachable — fait de _iter un générateur

    monkeypatch.setattr(at, "iter_transcribe_blocks", _iter)
    try:
        audio_file_jobs.run_audio_file_job(job_id)
    finally:
        audio_file_jobs._PROPRIETAIRES.pop(job_id, None)
    assert vu["apres"] == vu["avant"]


# --------------------------------------------------------------------------- #
# Consommateur parti : les workers sont arrêtés ; branche Python < 3.14
# --------------------------------------------------------------------------- #
def test_generatorexit_arrete_les_workers(monkeypatch):
    _PoolFige.instances = []
    monkeypatch.setattr(at, "ProcessPoolExecutor", lambda **kw: _PoolFige(fige=99, **kw))
    gen = at._drain_parallel(["a", "b", "c"], 0, 3, 2)
    assert next(gen) == (0, "ok0")
    gen.close()
    assert _PoolFige.instances[0].termine is True


def test_arret_sans_terminate_workers_branche_python_312():
    class _Proc:
        def __init__(self):
            self.vivant, self.appels = True, []

        def terminate(self):
            self.appels.append("terminate")
            self.vivant = False

        def join(self, timeout=None):
            self.appels.append("join")

        def is_alive(self):
            return self.vivant

        def kill(self):  # pragma: no cover - mort après terminate
            self.appels.append("kill")

    class _Pool312:
        def __init__(self):
            self._processes = {1: _Proc(), 2: _Proc()}
            self.arrets = []

        def shutdown(self, wait=True, cancel_futures=False):
            self.arrets.append((wait, cancel_futures))

    pool = _Pool312()
    procs = list(pool._processes.values())
    f = Future()
    at._arreter_workers(pool, {5: f})
    assert f.cancelled()
    assert pool.arrets == [(False, True)]
    assert all(p.appels == ["terminate", "join"] for p in procs)
