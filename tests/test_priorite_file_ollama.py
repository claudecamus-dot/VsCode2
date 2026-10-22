"""Lot W10 : priorité portée par l'élément de file, warm-up sans verrou, PriorityLock."""
import threading
import time

from app.services import ai_common, structuration_libre
from app.services.priority_lock import FOND, LIVE, PriorityLock


def test_element_live_servi_avant_un_fond_en_attente(monkeypatch):
    """Verrou réel tenu : un FOND attend déjà, un élément LIVE arrive derrière
    lui et doit être servi AVANT lui à la libération."""
    verrou = PriorityLock()
    monkeypatch.setattr(ai_common, "_OLLAMA_UN_A_LA_FOIS", verrou)
    ordre, vu = [], []

    def _prendre_verrou(interview_id, jeton=None):
        vu.append(ai_common._PRIORITE_OLLAMA.get())
        assert verrou.acquire(priority=ai_common._PRIORITE_OLLAMA.get())
        ordre.append(interview_id)
        verrou.release()
        return True

    monkeypatch.setattr(structuration_libre, "_prendre", lambda i: None)
    monkeypatch.setattr(structuration_libre, "_finir", _prendre_verrou)
    monkeypatch.setattr(structuration_libre, "_liberer", lambda *a, **k: None)
    monkeypatch.setattr(structuration_libre, "_tranches_en_vol", lambda j: 0)

    verrou.acquire()
    fond = threading.Thread(target=structuration_libre._traiter,
                            args=((1, object(), 0.0, "t", FOND),))
    fond.start()
    time.sleep(0.3)
    live = threading.Thread(target=structuration_libre._traiter,
                            args=((2, object(), 0.0, "t", LIVE),))
    live.start()
    time.sleep(0.3)
    verrou.release()
    fond.join(5)
    live.join(5)
    assert ordre == [2, 1]
    assert sorted(vu) == [LIVE, FOND]


def test_element_a_quatre_champs_reste_fond(monkeypatch):
    vu = []
    monkeypatch.setattr(structuration_libre, "_prendre", lambda i: None)
    structuration_libre._traiter((1, object(), None, None))
    monkeypatch.setattr(structuration_libre, "_finir",
                        lambda i, j: vu.append(ai_common._PRIORITE_OLLAMA.get()))
    monkeypatch.setattr(structuration_libre, "_prendre", lambda i: "")
    structuration_libre._traiter((1, object(), None, None))
    assert vu == [FOND]
    assert ai_common._PRIORITE_OLLAMA.get() == LIVE  # restaurée


def test_relancer_met_en_file_en_live_et_planifier_defaut_fond(monkeypatch):
    vus = []
    monkeypatch.setattr(structuration_libre, "EXECUTION_SYNCHRONE", False)
    monkeypatch.setattr(structuration_libre, "_demarrer_worker", lambda: None)
    monkeypatch.setattr(structuration_libre, "_file", __import__("queue").Queue())
    structuration_libre.planifier_structuration(910001)
    structuration_libre.planifier_structuration(910002, LIVE)
    f = structuration_libre._file
    vus = [f.get()[4], f.get()[4]]
    structuration_libre._liberer(910001)
    structuration_libre._liberer(910002)
    assert vus == [FOND, LIVE]


def test_warm_up_ollama_ne_prend_pas_le_verrou(monkeypatch):
    class Verrou:
        def acquire(self, *a, **k):
            raise AssertionError("warm_up ne doit pas prendre le verrou")
    monkeypatch.setattr(ai_common, "_OLLAMA_UN_A_LA_FOIS", Verrou())
    monkeypatch.delenv("OLLAMA_WARM_UP", raising=False)
    monkeypatch.setattr(ai_common, "active_provider", lambda: "ollama")

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(ai_common.urllib.request, "urlopen", lambda *a, **k: R())
    ai_common.warm_up_ollama()


def test_priority_lock_trois_live_fifo_et_plancher_anti_famine():
    t = [0.0]
    verrou = PriorityLock(starvation_s=10, clock=lambda: t[0])
    verrou.acquire()
    ordre = []

    def w(nom, prio):
        verrou.acquire(priority=prio)
        ordre.append(nom)
        verrou.release()

    ths = []
    for nom, prio in (("fond", FOND), ("l1", LIVE), ("l2", LIVE), ("l3", LIVE)):
        th = threading.Thread(target=w, args=(nom, prio))
        th.start()
        ths.append(th)
        time.sleep(0.15)
    verrou.release()
    for th in ths:
        th.join(5)
    assert ordre == ["l1", "l2", "l3", "fond"]

    # plancher : le fond attend depuis > starvation_s => rang FIFO parmi les live
    verrou.acquire()
    ordre.clear()
    ths = []
    for nom, prio in (("fond", FOND), ("l1", LIVE)):
        th = threading.Thread(target=w, args=(nom, prio))
        th.start()
        ths.append(th)
        time.sleep(0.15)
    t[0] = 100.0
    verrou.release()
    for th in ths:
        th.join(5)
    assert ordre == ["fond", "l1"]


def test_relancer_demande_a_l_ecran_est_planifie_en_live(monkeypatch):
    from types import SimpleNamespace
    itv = SimpleNamespace(structuration_status=next(iter(structuration_libre._STATUTS_LANCABLES)))

    class Db:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, model, i): return itv

    vu = []
    monkeypatch.setattr(structuration_libre._db, "SessionLocal", lambda: Db())
    monkeypatch.setattr(structuration_libre, "peut_relancer", lambda i: True)
    monkeypatch.setattr(structuration_libre, "planifier_structuration",
                        lambda i, priorite=FOND: vu.append(priorite) or True)
    assert structuration_libre.relancer(5)
    assert vu == [LIVE]
