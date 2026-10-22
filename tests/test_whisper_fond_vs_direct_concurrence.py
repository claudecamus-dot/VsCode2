"""Lot concurrence Whisper/Ollama (w7) : fond bridé pendant un direct, timeouts,
retry de bloc sur verrou occupé, plafond dur de la récupération synchrone.
Tout est factice : aucun vrai modèle, aucun réseau."""
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from app.services import audio_file_jobs, audio_transcribe as at
from app.services import interview_segment_jobs as isj


class _FauxPool:
    """ProcessPoolExecutor factice : exécute à la soumission, trace l'en-vol."""
    def __init__(self, trace, **kw):
        self.trace = trace

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def submit(self, fn, arg):
        f = Future()
        self.trace.append(("submit", at.direct_actif()))
        f.set_result("t")
        return f


def _patch_pool(monkeypatch, trace):
    monkeypatch.setattr(at, "ProcessPoolExecutor", lambda **kw: _FauxPool(trace))


def test_defaut_timeout_direct_couvre_un_bloc_de_fond():
    assert at.SEGMENT_LOCK_TIMEOUT_S >= 150
    assert at.BACKGROUND_WORKERS_WHEN_LIVE == 1


def test_busy_retry_after_coherent_avec_le_timeout(monkeypatch):
    monkeypatch.setattr(at, "SEGMENT_LOCK_TIMEOUT_S", 0.1)
    at._MODEL_LOCK.acquire()
    try:
        with pytest.raises(at.TranscriptionBusyError) as e:
            at._transcribe_pcm_sequential(object(), at.LIVE)
        # Estimation (file x durée récente) bornée, plus la constante du timeout.
        assert e.value.retry_after_s == at.estimer_retry_after() >= at.RETRY_AFTER_MIN_S
    finally:
        at._MODEL_LOCK.release()


def _soumissions_au_premier_resultat(monkeypatch, direct: int, n_workers: int = 4):
    monkeypatch.setattr(at, "_LIVE_COMPTEUR", direct)
    soumis = []

    class Pool(_FauxPool):
        def submit(self, fn, arg):
            soumis.append(1)
            f = Future()
            f.set_result("t")
            return f
    monkeypatch.setattr(at, "ProcessPoolExecutor", lambda **kw: Pool([]))
    gen = at._drain_parallel([b"a"] * 10, 0, 10, n_workers)
    next(gen)
    n = len(soumis)
    list(gen)
    return n


def test_fond_borne_pendant_un_direct(monkeypatch):
    """Direct actif : le fond ne garde qu'UN bloc en vol (au lieu de 2 x workers)."""
    assert _soumissions_au_premier_resultat(monkeypatch, direct=1) == 2


def test_fond_plein_parallelisme_sans_direct(monkeypatch):
    assert _soumissions_au_premier_resultat(monkeypatch, direct=0) == 9


def test_permis_reduits_pendant_un_direct(monkeypatch):
    monkeypatch.setattr(at, "_LIVE_COMPTEUR", 1)
    acquis = []
    orig = at._GLOBAL_WORKERS_SEMAPHORE.acquire
    monkeypatch.setattr(at._GLOBAL_WORKERS_SEMAPHORE, "acquire",
                        lambda *a, **k: (acquis.append(1), orig(*a, **k))[1])
    _patch_pool(monkeypatch, [])
    list(at._drain_parallel([b"a"] * 6, 0, 6, 4))
    assert len(acquis) == 1


def test_compteur_direct_incremente_pendant_transcribe_audio(monkeypatch):
    vus = []
    monkeypatch.setattr(at, "_faster_whisper", lambda: object())
    monkeypatch.setattr(at, "_exiger_piste_audio", lambda c: None)
    monkeypatch.setattr(at, "_probe_duration_s", lambda c: 5.0)

    class Modele:
        def transcribe(self, *a, **k):
            vus.append(at.direct_actif())
            return [SimpleNamespace(text="bonjour")], None
    monkeypatch.setattr(at, "_get_model", lambda: Modele())
    assert at.transcribe_audio(b"x") == "bonjour"
    assert vus == [True]
    assert at.direct_actif() is False


def test_live_semaphore_global_borne_leve_busy(monkeypatch):
    monkeypatch.setattr(at, "SEGMENT_LOCK_TIMEOUT_S", 0.05)
    sem = at.threading.Semaphore(0)
    monkeypatch.setattr(at, "_GLOBAL_WORKERS_SEMAPHORE", sem)
    with pytest.raises(at.TranscriptionBusyError):
        list(at._drain_parallel([b"a", b"b"], 0, 2, 2, priority=at.LIVE))


def test_fond_lock_timeout_leve_transcription_error(monkeypatch):
    monkeypatch.setattr(at, "BACKGROUND_LOCK_TIMEOUT_S", 0.05)
    at._MODEL_LOCK.acquire()
    try:
        with pytest.raises(at.TranscriptionError):
            at._transcribe_pcm_sequential(object(), at.FOND)
    finally:
        at._MODEL_LOCK.release()


def test_retry_bloc_inclut_busy_par_classe():
    busy = at.TranscriptionBusyError("autre message")
    assert audio_file_jobs._est_reessayable(busy)
    assert audio_file_jobs._est_reessayable(at.TranscriptionError(at.ECHEC_TRANSCRIPTION))
    assert not audio_file_jobs._est_reessayable(at.TranscriptionError("Fichier audio illisible."))


def _jobs(n):
    return [SimpleNamespace(status="failed", text="du texte", error=None, turns_result=None)
            for _ in range(n)]


class _Db:
    def commit(self):
        pass


def test_recuperation_plafond_dur_sans_appel(monkeypatch):
    horloge = [0.0]
    monkeypatch.setattr(isj.time, "monotonic", lambda: horloge[0])
    appels = []

    def lent(db, job, essais=None, delai_s=None):
        appels.append(1)
        horloge[0] += 100.0   # 1 appel dépasse le budget dur
        return {"turns": []}
    monkeypatch.setattr(isj, "_extract_with_retries", lent)
    jobs = _jobs(4)
    isj.recover_stalled_or_failed_jobs(_Db(), jobs)
    assert len(appels) == 1                       # au moins un essai, puis plus d'appel
    assert jobs[0].status == "done"
    for j in jobs[1:]:
        assert j.status == "failed" and "différée" in j.error


def _sem_global(monkeypatch, n):
    import threading
    sem = threading.Semaphore(n)
    monkeypatch.setattr(at, "_GLOBAL_WORKERS_SEMAPHORE", sem)
    monkeypatch.setattr(at, "GLOBAL_MAX_PARALLEL_WORKERS", n)
    _patch_pool(monkeypatch, [])
    return sem


def test_deux_jobs_longs_ne_s_interbloquent_pas_sur_les_permis(monkeypatch):
    """EH-1 : deux jobs voulant chacun > la moitie du plafond global finissent tous
    deux. Le Barrier force chaque job a tenir 2 permis (4 au total) avant que l'autre continue :
    la prise un-par-un d'avant interbloquait donc A CHAQUE FOIS."""
    import threading
    sem = _sem_global(monkeypatch, 4)
    orig = sem.acquire
    barriere = threading.Barrier(2, timeout=0.3)
    local = threading.local()

    def acquire_synchro(*a, **k):
        r = orig(*a, **k)
        local.n = getattr(local, "n", 0) + (1 if r else 0)
        if r and local.n <= 2:        # chaque job tient 2 permis avant que l'autre continue
            try:
                barriere.wait()
            except threading.BrokenBarrierError:
                pass       # prise en bloc : l'autre job n'avance pas, c'est voulu
        return r
    monkeypatch.setattr(sem, "acquire", acquire_synchro)
    fini = []

    def job(nom):
        list(at._drain_parallel([b"a"] * 3, 0, 3, 3))
        fini.append(nom)
    ts = [threading.Thread(target=job, args=(n,), daemon=True) for n in "AB"]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=8)
    assert sorted(fini) == ["A", "B"]
    assert sem._value == 4


def test_fond_en_attente_ne_retarde_pas_un_direct(monkeypatch):
    """Un job de fond qui attend ses permis n'en garde aucun et ne tient aucun
    verrou : un segment direct qui n'en veut qu'un passe tout de suite."""
    import threading
    sem = _sem_global(monkeypatch, 2)
    monkeypatch.setattr(at, "SEGMENT_LOCK_TIMEOUT_S", 1.0)
    sem.acquire()                      # 1 permis pris par un autre job : 1 libre
    pris_fond = threading.Event()
    fond = threading.Thread(
        target=lambda: (list(at._drain_parallel([b"a"] * 2, 0, 2, 2)), pris_fond.set()),
        daemon=True)
    fond.start()
    time.sleep(0.3)                    # le fond attend son 2e permis
    res = []
    live = threading.Thread(
        target=lambda: res.append(list(at._drain_parallel([b"a"], 0, 1, 1, priority=at.LIVE))),
        daemon=True)
    t0 = time.monotonic()
    live.start()
    live.join(timeout=2)
    assert res and time.monotonic() - t0 < 0.8   # budget 1.0 s : pas d'attente jusqu'a l'echeance
    sem.release()                      # libere : le fond finit aussi
    fond.join(timeout=5)
    assert pris_fond.is_set()


def test_n_workers_superieur_au_plafond_global_termine(monkeypatch):
    import threading
    sem = _sem_global(monkeypatch, 2)
    t = threading.Thread(target=lambda: list(at._drain_parallel([b"a"] * 6, 0, 6, 5)),
                         daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
    assert sem._value == 2


def test_live_busy_respecte_le_budget(monkeypatch):
    sem = _sem_global(monkeypatch, 3)
    monkeypatch.setattr(at, "SEGMENT_LOCK_TIMEOUT_S", 0.3)
    sem.acquire(); sem.acquire(); sem.acquire()   # 0 libre : un direct n'attend que dans ce cas
    t0 = time.monotonic()
    with pytest.raises(at.TranscriptionBusyError):
        list(at._drain_parallel([b"a"] * 3, 0, 3, 3, priority=at.LIVE))
    assert time.monotonic() - t0 < 3.0   # budget 0.3 s + marge de charge
    assert sem._value == 0


def test_direct_demarre_avec_les_permis_libres(monkeypatch):
    """Un fond tient 1 permis tout le test ; un direct qui en veut 3 (cap 3) demarre
    avec les 2 libres au lieu d'attendre un jeu complet (pas de 503)."""
    import threading
    sem = _sem_global(monkeypatch, 3)
    monkeypatch.setattr(at, "SEGMENT_LOCK_TIMEOUT_S", 2.0)
    sem.acquire()                      # le fond
    max_workers = []
    pic = []

    class Pool(_FauxPool):
        def __init__(self, mw):
            super().__init__([])
            max_workers.append(mw)
            pic.append(sem._value)

    monkeypatch.setattr(at, "ProcessPoolExecutor", lambda max_workers, **kw: Pool(max_workers))
    res = []
    t = threading.Thread(target=lambda: res.append(
        list(at._drain_parallel([b"a"] * 4, 0, 4, 3, priority=at.LIVE))), daemon=True)
    t0 = time.monotonic()
    t.start()
    t.join(timeout=5)
    assert res and time.monotonic() - t0 < 1.5
    assert max_workers == [2] and pic == [0]   # 2 workers = 2 permis tenus
    assert sem._value == 2                     # rendus a la sortie (le fond garde le sien)
