"""PriorityLock + priorité du direct sur Whisper/Ollama (fond cède au live)."""
import threading
import time

import pytest

from app.services import audio_transcribe, ai_common
from app.services.priority_lock import FOND, LIVE, PriorityLock


def _attendre(cond, t=3.0):
    fin = time.monotonic() + t
    while time.monotonic() < fin:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _waiter(lock, prio, nom, ordre, timeout=-1):
    def run():
        if lock.acquire(timeout=timeout, priority=prio):
            ordre.append(nom)
            lock.release()
    t = threading.Thread(target=run)
    t.start()
    return t


def test_live_avant_fond_puis_fifo():
    lock = PriorityLock()
    lock.acquire()
    ordre = []
    ts = []
    for nom, prio in (("f1", FOND), ("l1", LIVE), ("f2", FOND), ("l2", LIVE)):
        ts.append(_waiter(lock, prio, nom, ordre))
        assert _attendre(lambda n=len(ts): len(lock._waiters) == n)
    lock.release()
    for t in ts:
        t.join(5)
    assert ordre == ["l1", "l2", "f1", "f2"]


def test_anti_famine_promeut_le_fond_ancien():
    horloge = [0.0]
    lock = PriorityLock(starvation_s=10, clock=lambda: horloge[0])
    lock.acquire()
    ordre = []
    t1 = _waiter(lock, FOND, "fond", ordre)
    assert _attendre(lambda: len(lock._waiters) == 1)
    horloge[0] = 50.0  # le fond attend depuis > plancher
    t2 = _waiter(lock, LIVE, "live", ordre)
    assert _attendre(lambda: len(lock._waiters) == 2)
    lock.release()
    t1.join(5); t2.join(5)
    assert ordre == ["fond", "live"]  # promu, et arrivé avant en FIFO


def test_timeout_et_non_bloquant():
    lock = PriorityLock()
    lock.acquire()
    assert lock.acquire(timeout=0.05) is False
    assert lock.acquire(blocking=False) is False
    assert not lock._waiters
    lock.release()
    assert lock.acquire(timeout=0) is True
    lock.release()


def test_release_sur_exception_et_double_release():
    lock = PriorityLock()
    with pytest.raises(ValueError):
        with lock:
            raise ValueError
    assert not lock.locked()
    with pytest.raises(RuntimeError):
        lock.release()


def test_live_double_un_fond_deja_en_attente_sur_whisper(monkeypatch):
    """Bloc A en cours, un AUTRE fond (B) déjà en file, puis un live : le live
    est servi avant B, à la fin du bloc A, sans 503 (timeout live non atteint)."""
    import io
    verrou = PriorityLock()
    monkeypatch.setattr(audio_transcribe, "_MODEL_LOCK", verrou)
    monkeypatch.setattr(audio_transcribe, "SEGMENT_LOCK_TIMEOUT_S", 5.0)
    debut_a = threading.Event()
    fin_a = threading.Event()
    journal = []

    class Seg:
        text = "x"

    class Modele:
        def transcribe(self, entree, **kw):
            if isinstance(entree, io.BytesIO):
                journal.append("live")
            else:
                journal.append(entree)
                if entree == "A":
                    debut_a.set()
                    fin_a.wait(5)
            return [Seg()], None

    monkeypatch.setattr(audio_transcribe, "_get_model", lambda: Modele())
    monkeypatch.setattr(audio_transcribe, "_faster_whisper", lambda: object())
    monkeypatch.setattr(audio_transcribe, "_exiger_piste_audio", lambda c: None)
    monkeypatch.setattr(audio_transcribe, "_probe_duration_s", lambda c: 1.0)

    ta = threading.Thread(target=audio_transcribe._transcribe_pcm_sequential, args=("A",))
    ta.start()
    assert debut_a.wait(3)
    tb = threading.Thread(target=audio_transcribe._transcribe_pcm_sequential, args=("B",))
    tb.start()
    assert _attendre(lambda: len(verrou._waiters) == 1)
    res = {}
    tl = threading.Thread(target=lambda: res.setdefault("t", audio_transcribe.transcribe_audio(b"abc")))
    tl.start()
    assert _attendre(lambda: verrou.attente_live())
    fin_a.set()
    for t in (ta, tb, tl):
        t.join(5)
    assert res["t"] == "x"
    assert journal == ["A", "live", "B"]


def test_ollama_once_live_double_un_fond_en_attente(monkeypatch):
    import contextlib
    import json as _json
    ordre = []
    monkeypatch.setattr(ai_common, "ollama_verrou_timeout", lambda: 5.0)

    class Rep:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    monkeypatch.setattr(ai_common.urllib.request, "urlopen",
                        lambda req, timeout=None: (ordre.append(req.data), Rep())[1])
    verrou = ai_common._OLLAMA_UN_A_LA_FOIS
    assert verrou.acquire(timeout=1)

    def appel(octets, fond):
        def run():
            with (ai_common.en_fond() if fond else contextlib.nullcontext()):
                ai_common._call_ollama_once(octets, "m")
        t = threading.Thread(target=run); t.start(); return t

    t1 = appel(b"fond", True)
    assert _attendre(lambda: len(verrou._waiters) == 1)
    t2 = appel(b"live", False)
    assert _attendre(lambda: len(verrou._waiters) == 2)
    verrou.release()
    t1.join(5); t2.join(5)
    assert ordre == [b"live", b"fond"]


def test_ollama_fond_cede_au_live():
    ordre = []
    verrou = ai_common._OLLAMA_UN_A_LA_FOIS
    assert verrou.acquire(timeout=1)

    def appel(nom, fond):
        def run():
            ctx = ai_common.en_fond() if fond else __import__("contextlib").nullcontext()
            with ctx:
                assert verrou.acquire(timeout=5, priority=ai_common._PRIORITE_OLLAMA.get())
                ordre.append(nom)
                verrou.release()
        t = threading.Thread(target=run); t.start(); return t

    t1 = appel("fond", True)
    assert _attendre(lambda: len(verrou._waiters) == 1)
    t2 = appel("live", False)
    assert _attendre(lambda: len(verrou._waiters) == 2)
    verrou.release()
    t1.join(5); t2.join(5)
    assert ordre == ["live", "fond"]


def test_segment_live_long_parallele_garde_la_priorite_live(monkeypatch):
    """Segment live > PARALLEL_THRESHOLD_S : ses tronçons séquentiels (repli)
    passent en LIVE (devant un fond en file) et lèvent Busy après le délai."""
    verrou = PriorityLock()
    monkeypatch.setattr(audio_transcribe, "_MODEL_LOCK", verrou)
    monkeypatch.setattr(audio_transcribe, "SEGMENT_LOCK_TIMEOUT_S", 0.2)
    ordre = []

    class Seg:
        text = "x"

    class Modele:
        def transcribe(self, entree, **kw):
            ordre.append(entree)
            return [Seg()], None

    monkeypatch.setattr(audio_transcribe, "_get_model", lambda: Modele())
    verrou.acquire()
    tb = threading.Thread(target=audio_transcribe._transcribe_pcm_sequential, args=("fond",))
    tb.start()
    assert _attendre(lambda: len(verrou._waiters) == 1)
    tl = threading.Thread(
        target=audio_transcribe._transcribe_pcm_sequential, args=("live", audio_transcribe.LIVE)
    )
    tl.start()
    assert _attendre(lambda: verrou.attente_live())
    verrou.release()
    tl.join(5); tb.join(5)
    assert ordre == ["live", "fond"]
    # Occupé trop longtemps : le live reçoit Busy (503), pas une file sans fin.
    verrou.acquire()
    with pytest.raises(audio_transcribe.TranscriptionBusyError):
        audio_transcribe._transcribe_pcm_sequential("x", audio_transcribe.LIVE)
    verrou.release()


def test_transcribe_audio_long_passe_live_au_parallele(monkeypatch):
    vu = {}
    monkeypatch.setattr(audio_transcribe, "_faster_whisper", lambda: object())
    monkeypatch.setattr(audio_transcribe, "_exiger_piste_audio", lambda c: None)
    monkeypatch.setattr(audio_transcribe, "_probe_duration_s",
                        lambda c: audio_transcribe.PARALLEL_THRESHOLD_S + 1)
    monkeypatch.setattr(audio_transcribe, "_transcribe_parallel",
                        lambda c, d, priority=None, **_kw: (vu.setdefault("p", priority), "ok")[1])
    assert audio_transcribe.transcribe_audio(b"abc") == "ok"
    assert vu["p"] == LIVE


def test_cibles_de_fond_portent_la_priorite_fond(monkeypatch):
    from app.services import global_synthesis_job, structuration_libre
    vu = []
    monkeypatch.setattr(structuration_libre._file, "get",
                        lambda: (_ for _ in ()).throw(KeyboardInterrupt))
    for nom, fn in (("boucle", structuration_libre._boucle),):
        with pytest.raises(KeyboardInterrupt):
            fn()
    # la ContextVar est restaurée après la cible
    assert ai_common._PRIORITE_OLLAMA.get() == LIVE
    monkeypatch.setattr(global_synthesis_job, "SessionLocal",
                        lambda: (vu.append(ai_common._PRIORITE_OLLAMA.get()), (_ for _ in ()).throw(RuntimeError))[1])
    try:
        global_synthesis_job.run_global_synthesis_job(1)
    except RuntimeError:
        pass
    assert vu and vu[0] == FOND


def test_recover_budget_epuise_donne_quand_meme_un_essai_sans_retry(monkeypatch):
    from types import SimpleNamespace
    from app.services import interview_segment_jobs as sj
    monkeypatch.setattr(sj, "_RECOVER_BUDGET_S", 0.0)
    essais = []
    monkeypatch.setattr(sj, "_extract_with_retries",
                        lambda db, job, essais=None, delai_s=None: (essais_l.append(essais), {})[1])
    essais_l = essais
    class Db:
        def commit(self): pass
    jobs = [SimpleNamespace(status="failed", text="t", error=None, turns_result=None) for _ in range(3)]
    sj.recover_stalled_or_failed_jobs(Db(), jobs)
    assert essais == [0, 0, 0]  # un essai chacun, sans retry
    assert all(j.status == "done" for j in jobs)
