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
        self.permis_libres: list = []     # permis globaux libres à chaque soumission

    def submit(self, fn, *args, **kwargs):
        self.soumis += 1
        self.permis_libres.append(at._GLOBAL_WORKERS_SEMAPHORE._value)
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


def test_m4_pool_plus_grand_repris_sans_prendre_de_permis_en_plus(pool_reel_factice, monkeypatch):
    """Re-revue 3.9 (M4) : 5 blocs demandant 2 workers, pool garé de 4.
    Le fond ne tient que ses 2 permis (2 restent libres pour un direct) et n'a
    jamais plus de 2 blocs en vol. Avant : 4 permis pris, un direct concurrent
    recevait « occupé »."""
    monkeypatch.setattr(at, "memoire_disponible", lambda: 64 * GIO)
    gros = _garer(taille=4, vivants=4, prets=4)
    gen = at._drain_parallel(_blocs(5), 0, 5, 2)
    rendus = [next(gen)]
    # Fenêtre = 2 permis : 2 soumis, le bloc 0 lu, 1 resoumis = 3 (fenêtre de 4 : 5).
    assert gros.soumis == 3
    rendus += list(gen)
    assert [i for i, _ in rendus] == [0, 1, 2, 3, 4]
    assert pool_reel_factice.demarres == [] and not gros._shutdown_thread
    assert gros.permis_libres and min(gros.permis_libres) == 2, gros.permis_libres
    assert at._GLOBAL_WORKERS_SEMAPHORE._value == 4


def test_m7_direct_degrade_a_un_permis_reprend_le_pool_plus_grand(pool_reel_factice, monkeypatch):
    """Direct (LIVE) qui n'obtient qu'UN permis (3 tenus ailleurs) : il reprend le
    pool garé de 4 sans en prendre d'autres, un seul bloc en vol à la fois."""
    monkeypatch.setattr(at, "memoire_disponible", lambda: 64 * GIO)
    gros = _garer(taille=4, vivants=4, prets=4)
    for _ in range(3):
        assert at._GLOBAL_WORKERS_SEMAPHORE.acquire(blocking=False)
    try:
        rendus = list(at._drain_parallel(_blocs(2), 0, 2, 2, priority=at.LIVE))
    finally:
        for _ in range(3):
            at._GLOBAL_WORKERS_SEMAPHORE.release()
    assert [i for i, _ in rendus] == [0, 1]
    assert pool_reel_factice.demarres == [] and gros.soumis == 2
    assert gros.permis_libres == [0, 0]
    assert at._POOL_CHAUD is not None and at._POOL_CHAUD.executor is gros
    assert at._GLOBAL_WORKERS_SEMAPHORE._value == 4


def test_m3_etendre_qui_leve_arrete_le_pool_au_lieu_de_l_orpheliner(monkeypatch):
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    gros = _garer(taille=4, vivants=4, prets=4)

    def _etendre(_entree):
        raise RuntimeError("mesure impossible")

    with pytest.raises(RuntimeError, match="mesure impossible"):
        at._prendre_pool_chaud(2, at._cle_pool(), _etendre)
    assert gros._shutdown_thread
    assert all(not p.is_alive() for p in gros._processes.values())
    assert at._POOL_CHAUD is None


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


def test_b3_arret_des_workers_bat_le_coeur_entre_les_processus(monkeypatch, battements):
    """Battement au RYTHME BATTEMENT_ATTENTE_S (re-revue 3.9, m10) : échu à chaque
    processus (cadence 0) : un battement par processus ; cadence de 60 s et joins
    instantanés : aucun."""
    monkeypatch.setattr(at, "BATTEMENT_ATTENTE_S", 0.0)

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
    monkeypatch.setattr(at, "BATTEMENT_ATTENTE_S", 60.0)
    at._arreter_workers(_Exec(), {})
    assert battements["n"] == 3


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
    gare = _garer(taille=2, vivants=2, prets=2)
    # Mémoire RÉELLE relue après l'arrêt : rendue seulement une fois le pool arrêté.
    monkeypatch.setattr(at, "memoire_disponible",
                        lambda: int((3.5 if gare._shutdown_thread else 1.5) * GIO))
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


# --------------------------------------------------------------------------- #
# Re-revue 3.9 — M1, M2, M5
# --------------------------------------------------------------------------- #
def test_m1_pool_repris_entre_lecture_du_credit_et_arret_la_garde_ne_conclut_pas(monkeypatch):
    """Un `_drain_parallel` reprend le pool garé juste après la lecture de son
    crédit (insertion déterministe dans `_credit_pool_chaud`). Avant : fermeture
    sans effet et garde déclarée satisfaite alors que la mémoire du pool restait
    prise. Désormais le pool est jugé et retiré sous UN verrou, et la mémoire est
    relue : soit il a vraiment été arrêté, soit la garde refuse."""
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", GIO)
    monkeypatch.setattr(at, "MEMOIRE_MARGE_OCTETS", GIO)
    gare = _garer(taille=2, vivants=2, prets=2)
    monkeypatch.setattr(at, "memoire_disponible",
                        lambda: int((3.5 if gare._shutdown_thread else 1.5) * GIO))
    vrai_credit = at._credit_pool_chaud

    def _credit_puis_reprise():
        credit = vrai_credit()
        at._prendre_pool_chaud(2, at._cle_pool())      # l'autre appel le reprend
        return credit

    monkeypatch.setattr(at, "_credit_pool_chaud", _credit_puis_reprise)
    try:
        at._exiger_memoire_pour_modele()
        refuse = False
    except at.TranscriptionMemoireInsuffisanteError:
        refuse = True
    assert refuse or gare._shutdown_thread, "garde satisfaite, mémoire du pool toujours prise"


def test_m1_pool_arrete_mais_memoire_toujours_courte_la_garde_refuse(monkeypatch):
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", GIO)
    monkeypatch.setattr(at, "MEMOIRE_MARGE_OCTETS", GIO)
    monkeypatch.setattr(at, "memoire_disponible", lambda: int(1.5 * GIO))   # jamais rendue
    gare = _garer(taille=2, vivants=2, prets=2)
    with pytest.raises(at.TranscriptionMemoireInsuffisanteError):
        at._exiger_memoire_pour_modele()
    assert gare._shutdown_thread


def test_m2_pool_arrete_avant_le_verrou_de_chargement(monkeypatch):
    """`_get_model` sous contrôle mémoire : le pool garé est arrêté (joins) SANS
    tenir `_MODEL_LOAD_LOCK` — sinon les attentes bornées des autres appelants
    rendaient « occupé » pendant ~80 s. Faux modèle : rien de réel chargé."""
    monkeypatch.setattr(at, "POOL_CHAUD_INACTIF_S", 60.0)
    monkeypatch.setattr(at, "MEMOIRE_PAR_WORKER_OCTETS", GIO)
    monkeypatch.setattr(at, "MEMOIRE_MARGE_OCTETS", GIO)
    monkeypatch.setattr(at, "_model", None)
    gare = _garer(taille=2, vivants=2, prets=2)
    monkeypatch.setattr(at, "memoire_disponible",
                        lambda: int((3.5 if gare._shutdown_thread else 1.5) * GIO))
    verrou_tenu = []
    for proc in gare._processes.values():
        proc.join = lambda timeout=None: verrou_tenu.append(at._MODEL_LOAD_LOCK.locked())

    class _FauxWhisper:
        class WhisperModel:
            def __init__(self, *a, **k):
                pass

    monkeypatch.setattr(at, "_faster_whisper", lambda: _FauxWhisper)
    jeton = at._CONTROLE_MEMOIRE_AU_CHARGEMENT.set(True)
    try:
        modele = at._get_model()
    finally:
        at._CONTROLE_MEMOIRE_AU_CHARGEMENT.reset(jeton)
    assert isinstance(modele, _FauxWhisper.WhisperModel)
    assert gare._shutdown_thread
    assert verrou_tenu and not any(verrou_tenu), verrou_tenu


class _ExecFige:
    """Exécuteur non garable : le bloc 0 échoue, le bloc 1 reste figé (jamais fini).
    Sa sortie `__exit__` (= `shutdown(wait=True)`) ne doit plus être appelée après
    l'arrêt net : elle attendrait le bloc figé sans borne."""
    instances: list = []

    def __init__(self, *args, **kwargs):
        self.futures: list = []
        self._processes = {i: _Proc() for i in range(2)}
        self.sorties = 0
        _ExecFige.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.sorties += 1
        return False

    def shutdown(self, wait=True, cancel_futures=False):
        assert not wait, "attente sans borne après l'arrêt net"

    def submit(self, fn, *args, **kwargs):
        f = Future()
        if not self.futures:
            f.set_exception(RuntimeError("bloc 0 en échec"))
        else:
            assert f.set_running_or_notify_cancel()
        self.futures.append(f)
        return f


def test_m5_bloc_fige_au_dela_du_delai_arret_net_et_exception_d_origine(monkeypatch):
    monkeypatch.setattr(at, "ProcessPoolExecutor", _ExecFige)
    monkeypatch.setattr(at, "BLOC_RESULTAT_TIMEOUT_S", 0.1)
    monkeypatch.setattr(at, "BATTEMENT_ATTENTE_S", 0.02)
    monkeypatch.setattr(at, "GLOBAL_MAX_PARALLEL_WORKERS", 4)
    monkeypatch.setattr(at, "_GLOBAL_WORKERS_SEMAPHORE", threading.Semaphore(4))
    _ExecFige.instances = []
    with pytest.raises(RuntimeError, match="bloc 0 en échec"):
        list(at._drain_parallel(_blocs(2), 0, 2, 2))
    executor = _ExecFige.instances[0]
    assert all(not p.is_alive() for p in executor._processes.values())   # arrêt net
    assert executor.sorties == 0                       # pas de shutdown(wait=True)
    assert at._GLOBAL_WORKERS_SEMAPHORE._value == 4
