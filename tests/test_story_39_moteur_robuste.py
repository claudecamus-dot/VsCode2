"""Story 3.9 — robustesse du moteur de transcription (reste de la 3.2).

AC3.9.1 : un pool chaud garé PLUS GRAND est repris pour un fichier plus court,
sans dépasser la garde mémoire ni le plafond global de permis.
AC3.9.2 : aucune attente du pool sans battement (B1 : tâches en vol avant de
garer ; B2 : sortie d'un pool non garé sur échec d'une tâche ; B3 : arrêt des
processus) ; la garde mémoire du chemin séquentiel compte le pool garé (B5).

Aucun processus réel, jamais le vrai modèle Whisper : exécuteurs factices, et
les attentes se terminent sur ÉVÉNEMENT (le battement lui-même résout la tâche),
jamais sur un délai qui dépendrait de la charge de la machine.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future

import numpy as np
import pytest

from app.services import audio_transcribe as at
from tests.conftest_pool_chaud import pool_chaud_isole  # noqa: F401

GIO = 1024 ** 3


def _blocs(n: int) -> list:
    return [np.zeros(1600, dtype=np.float32) for _ in range(n)]


class _Proc:
    pid = 0

    def __init__(self):
        self._vivant = True
        self.joins = 0

    def is_alive(self):
        return self._vivant

    def terminate(self):
        self._vivant = False

    kill = terminate

    def join(self, timeout=None):
        self.joins += 1


class _ExecGare:
    """Exécuteur garé factice : `vivants` processus factices, chaque bloc soumis
    rend « chaud » aussitôt (aucun processus, aucun modèle)."""

    def __init__(self, vivants: int):
        self._processes = {i: _Proc() for i in range(vivants)}
        self._broken = False
        self._shutdown_thread = False
        self.soumis = 0

    def submit(self, fn, *args, **kwargs):
        self.soumis += 1
        f = Future()
        f.set_result("chaud")
        return f

    def shutdown(self, wait=True, cancel_futures=False):
        self._shutdown_thread = True


class _Compteur:
    def __init__(self, value: int):
        self.value = value


def _garer(taille: int, vivants: int, prets: int) -> _ExecGare:
    executor = _ExecGare(vivants)
    at._garer_pool(at._PoolChaud(executor, taille, _Compteur(prets), _Compteur(prets),
                                 at._cle_pool()))
    return executor


class _PoolNeuf(at.ProcessPoolExecutor):
    """Sous-classe promue « pool réel » : aucun processus, chaque bloc rend « neuf »."""
    demarres: list = []

    def __init__(self, max_workers=None, mp_context=None, **kw):
        _PoolNeuf.demarres.append(max_workers)
        super().__init__(max_workers=max_workers, mp_context=mp_context, **kw)

    def submit(self, fn, *args, **kwargs):
        f = Future()
        f.set_result("neuf")
        return f


@pytest.fixture
def pool_reel_factice(monkeypatch):
    _PoolNeuf.demarres = []
    monkeypatch.setattr(at, "ProcessPoolExecutor", _PoolNeuf)
    monkeypatch.setattr(at, "_POOL_REEL", _PoolNeuf)            # garable + garde mémoire
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    monkeypatch.setattr(at, "GLOBAL_MAX_PARALLEL_WORKERS", 4)
    monkeypatch.setattr(at, "_GLOBAL_WORKERS_SEMAPHORE", threading.Semaphore(4))
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", GIO)
    monkeypatch.setattr(at, "MEMOIRE_MARGE_OCTETS", 0)
    return _PoolNeuf


@pytest.fixture
def battements():
    compte = {"n": 0}

    def _rappel():
        compte["n"] += 1
        for action in list(compte.get("actions", ())):
            action(compte["n"])

    jeton = at.BATTEMENT_ATTENTE.set(_rappel)
    yield compte
    at.BATTEMENT_ATTENTE.reset(jeton)


# --------------------------------------------------------------------------- #
# AC3.9.1 — pool garé plus grand repris quand sa taille suffit
# --------------------------------------------------------------------------- #
def test_ac391_pool_gare_plus_grand_repris_pour_un_fichier_plus_court(pool_reel_factice, monkeypatch):
    """Fichier précédent : 4 workers. Fichier suivant, plus court : 2 blocs. Le pool
    de 4 (tous chargés) est repris tel quel — aucun pool neuf, aucun rechargement —
    puis regaré ; tous les permis sont rendus. Avant : arrêté, pool neuf de 2."""
    monkeypatch.setattr(at, "memoire_disponible", lambda: 64 * GIO)
    gros = _garer(taille=4, vivants=4, prets=4)
    rendus = list(at._drain_parallel(_blocs(2), 0, 2, 2))
    assert rendus == [(0, "chaud"), (1, "chaud")]
    assert pool_reel_factice.demarres == []
    assert not gros._shutdown_thread
    assert at._POOL_CHAUD is not None and at._POOL_CHAUD.executor is gros
    assert at._POOL_CHAUD.taille == 4
    assert at._GLOBAL_WORKERS_SEMAPHORE._value == 4


def test_ac391_pool_plus_grand_non_repris_si_la_memoire_ne_couvre_pas_sa_taille(
        pool_reel_factice, monkeypatch):
    """Pool garé de 4 processus lancés dont UN seul chargé : repris, ses 3 autres
    processus pourraient charger un modèle chacun (file partagée). 1 Gio libre +
    1 worker en crédit ne couvre que 2 modèles : il n'est pas repris, il est arrêté."""
    monkeypatch.setattr(at, "memoire_disponible", lambda: 1 * GIO)
    gros = _garer(taille=4, vivants=4, prets=1)
    rendus = list(at._drain_parallel(_blocs(2), 0, 2, 2))
    assert [i for i, _ in rendus] == [0, 1]
    assert gros._shutdown_thread and gros.soumis == 0
    assert pool_reel_factice.demarres == [1]      # garde recalculée sans crédit
    assert at._GLOBAL_WORKERS_SEMAPHORE._value == 4


def test_ac391_pool_plus_grand_non_repris_sans_permis_libres(pool_reel_factice, monkeypatch):
    """Plafond global de processus : 2 permis sur 4 tenus ailleurs. Reprendre le pool
    de 4 en ne tenant que 2 permis dépasserait le plafond : il est arrêté."""
    monkeypatch.setattr(at, "memoire_disponible", lambda: 64 * GIO)
    gros = _garer(taille=4, vivants=4, prets=4)
    assert at._GLOBAL_WORKERS_SEMAPHORE.acquire(blocking=False)
    assert at._GLOBAL_WORKERS_SEMAPHORE.acquire(blocking=False)
    try:
        rendus = list(at._drain_parallel(_blocs(2), 0, 2, 2))
    finally:
        at._GLOBAL_WORKERS_SEMAPHORE.release()
        at._GLOBAL_WORKERS_SEMAPHORE.release()
    assert [i for i, _ in rendus] == [0, 1]
    assert gros._shutdown_thread and gros.soumis == 0
    assert pool_reel_factice.demarres == [2]
    assert at._GLOBAL_WORKERS_SEMAPHORE._value == 4


# --------------------------------------------------------------------------- #
# AC3.9.2 — B1 / B2 / B3 : attentes du pool avec battement
# --------------------------------------------------------------------------- #
def test_b1_attente_des_taches_en_vol_bat_le_coeur(monkeypatch, battements):
    """Avant de garer : un bloc encore en cours est attendu (jusqu'à 900 s) — avec
    un battement par BATTEMENT_ATTENTE_S. La tâche finit au 3e battement
    (événement, pas délai) : l'attente rend vrai. Avant : 0 battement, et faux
    après le délai entier."""
    monkeypatch.setattr(at, "BATTEMENT_ATTENTE_S", 0.02)
    monkeypatch.setattr(at, "BLOC_RESULTAT_TIMEOUT_S", 5.0)
    en_cours = Future()
    assert en_cours.set_running_or_notify_cancel()            # non annulable
    battements["actions"] = [lambda n: n == 3 and en_cours.set_result("fini")]
    assert at._attendre_taches_en_vol({0: en_cours}) is True
    assert battements["n"] == 3


class _ExecNonGarable:
    """Exécuteur de test (jamais garé) : le bloc 0 échoue, le bloc 1 reste EN COURS
    jusqu'au 2e battement. Sa sortie refuse d'être appelée avec un bloc en vol :
    c'est l'attente sans borne ni battement d'avant (`shutdown(wait=True)`)."""
    instances: list = []

    def __init__(self, *args, **kwargs):
        self.futures: list = []
        _ExecNonGarable.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        assert all(f.done() for f in self.futures), "sortie avec un bloc encore en vol"
        return False

    def submit(self, fn, *args, **kwargs):
        f = Future()
        if not self.futures:
            f.set_exception(RuntimeError("bloc 0 en échec"))
        else:
            assert f.set_running_or_notify_cancel()
        self.futures.append(f)
        return f


def test_b2_sortie_sur_echec_attend_les_blocs_en_vol_avec_battement(monkeypatch, battements):
    monkeypatch.setattr(at, "ProcessPoolExecutor", _ExecNonGarable)
    monkeypatch.setattr(at, "BATTEMENT_ATTENTE_S", 0.02)
    monkeypatch.setattr(at, "BLOC_RESULTAT_TIMEOUT_S", 5.0)
    monkeypatch.setattr(at, "GLOBAL_MAX_PARALLEL_WORKERS", 4)
    monkeypatch.setattr(at, "_GLOBAL_WORKERS_SEMAPHORE", threading.Semaphore(4))
    _ExecNonGarable.instances = []

    def _finir_le_bloc_1(n):
        if n == 2:
            _ExecNonGarable.instances[0].futures[1].set_result("tard")

    battements["actions"] = [_finir_le_bloc_1]
    with pytest.raises(RuntimeError, match="bloc 0 en échec"):
        list(at._drain_parallel(_blocs(2), 0, 2, 2))
    assert battements["n"] == 2
    assert at._GLOBAL_WORKERS_SEMAPHORE._value == 4


def test_b3_arret_des_workers_bat_le_coeur_entre_les_processus(battements):
    class _Exec:
        def __init__(self):
            self._processes = {i: _Proc() for i in range(3)}

        def shutdown(self, wait=True, cancel_futures=False):
            pass

    executor = _Exec()
    procs = list(executor._processes.values())
    at._arreter_workers(executor, {})
    assert battements["n"] == 3
    assert all(not p.is_alive() for p in procs)


# --------------------------------------------------------------------------- #
# AC3.9.2 — B5 : la garde mémoire du chemin séquentiel compte le pool garé
# --------------------------------------------------------------------------- #
def test_b5_garde_memoire_du_modele_serveur_recupere_le_pool_gare(monkeypatch):
    """1,5 Gio libre, 1 Gio de marge, 1 Gio par modèle : seul, le modèle du serveur
    ne tient pas. Les 2 workers chargés du pool garé (inactif) sont récupérables :
    le pool est arrêté et le modèle peut se charger. Avant : « mémoire
    insuffisante » à cause d'un pool qui ne faisait rien."""
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", GIO)
    monkeypatch.setattr(at, "MEMOIRE_MARGE_OCTETS", GIO)
    monkeypatch.setattr(at, "memoire_disponible", lambda: int(1.5 * GIO))
    gare = _garer(taille=2, vivants=2, prets=2)
    at._exiger_memoire_pour_modele()                           # ne lève pas
    assert gare._shutdown_thread and at._POOL_CHAUD is None


def test_b5_sans_pool_gare_la_garde_refuse_toujours(monkeypatch):
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", GIO)
    monkeypatch.setattr(at, "MEMOIRE_MARGE_OCTETS", GIO)
    monkeypatch.setattr(at, "memoire_disponible", lambda: int(1.5 * GIO))
    with pytest.raises(at.TranscriptionMemoireInsuffisanteError):
        at._exiger_memoire_pour_modele()


def test_b5_pool_gare_insuffisant_reste_gare_et_la_garde_refuse(monkeypatch):
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", GIO)
    monkeypatch.setattr(at, "MEMOIRE_MARGE_OCTETS", 10 * GIO)
    monkeypatch.setattr(at, "memoire_disponible", lambda: GIO)
    gare = _garer(taille=2, vivants=2, prets=2)
    with pytest.raises(at.TranscriptionMemoireInsuffisanteError):
        at._exiger_memoire_pour_modele()
    assert not gare._shutdown_thread and at._POOL_CHAUD is not None
