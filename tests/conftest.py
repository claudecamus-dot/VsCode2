"""Isolation de la base pour la suite de tests.

Doit s'exécuter **avant** tout import de `app.*` : pytest importe `conftest.py`
en premier, donc fixer `APP_DB_PATH` ici garantit que `app.db` crée son engine
sur une base jetable (et non sur `data/app.db`, la base de dev/prod).
"""
from __future__ import annotations

import atexit
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Base SQLite dédiée aux tests, dans le répertoire temporaire du système.
_TEST_DB = os.path.join(tempfile.gettempdir(), "interview_to_deck_test.db")
os.environ.setdefault("APP_DB_PATH", _TEST_DB)

# Images des têtes de chapitre : jamais de fetch réseau en test (offline,
# déterministe, rapide) — génération procédurale locale. cf. pptx_export.
os.environ.setdefault("PPTX_NO_PHOTO_FETCH", "1")

# --------------------------------------------------------------------------- #
# Authentification (app/auth.py, 2026-09-19) : l'app refuse desormais TOUTE
# route non publique sans credentials, et refuse aussi (503) si aucun mot de
# passe n'est configure. Les ~80 fichiers de test existants exercent le produit
# via TestClient sans rien savoir de cette garde : plutot que de les modifier un
# par un, on pose ici UN mot de passe de test et on injecte l'en-tete Bearer par
# defaut dans tout TestClient.
#
# C'est un point unique, assume et documente : tests/test_auth.py retire
# explicitement cet en-tete (`client.headers.pop("Authorization")`) pour prouver
# le 401 reel — sans quoi la garde serait verte par construction, jamais exercee.
# --------------------------------------------------------------------------- #
os.environ.setdefault("APP_AUTH_PASSWORD", "mot-de-passe-de-test")


def _patcher_testclient() -> None:
    from starlette.testclient import TestClient

    if getattr(TestClient, "_auth_par_defaut", False):
        return
    originel = TestClient.__init__

    def __init__(self, *args, **kwargs):  # noqa: N807
        originel(self, *args, **kwargs)
        # `.get` et non `[...]` : un test peut RETIRER la variable pour
        # exercer le cas « non configure » (tests/test_auth.py) — le client ne
        # doit pas exploser sur un KeyError a sa construction.
        secret = os.environ.get("APP_AUTH_PASSWORD")
        if secret:
            self.headers.setdefault("Authorization", "Bearer " + secret)

    TestClient.__init__ = __init__
    TestClient._auth_par_defaut = True


_patcher_testclient()


# --------------------------------------------------------------------------- #
# Verrou : UNE SEULE suite pytest à la fois sur cette base (2026-09-10)
#
# Tous les modules de tests partagent `_TEST_DB` (chemin FIXE dans le
# temporaire système) et beaucoup la suppriment en `setup_module`. Deux pytest
# concurrents se marchent donc dessus : le second trouve la base verrouillée ou
# supprimée sous lui, et rend une cascade de `PermissionError [WinError 32]` —
# des erreurs qui ne décrivent AUCUN défaut du produit.
#
# Mesuré deux fois : 10 erreurs le 2026-09-09 (un sous-agent relecteur lançant
# pytest pendant la suite complète), 20 le 2026-09-10 (j'ai lancé un fichier de
# test alors que la suite tournait encore). Le motif était déjà consigné en
# mémoire — il a récidivé, parce qu'une mémoire dépend d'une vigilance et pas
# d'une commande. Garde arbitrée par l'utilisateur sur le plan du superviseur.
#
# Le verrou ÉCHOUE VITE et DIT POURQUOI, plutôt que de laisser 20 erreurs
# illisibles. Il ne bloque pas un usage légitime : `PYTEST_SANS_VERROU=1` le
# désarme (une base distincte par runner via `APP_DB_PATH` est l'autre sortie,
# et la bonne si l'on veut vraiment paralléliser un jour).
# --------------------------------------------------------------------------- #
# Le verrou suit la base RÉELLEMENT utilisée, pas `_TEST_DB` : un runner lancé
# avec `APP_DB_PATH=<autre>` a sa propre base et ne doit donc PAS se voir
# refuser par le verrou du runner par défaut. Le premier jet dérivait le verrou
# de `_TEST_DB`, si bien que la sortie de secours annoncée dans son propre
# message (« lancer sur sa propre base ») ne marchait pas — il ne restait que
# `PYTEST_SANS_VERROU=1`, qui SUPPRIME la protection au lieu de l'isoler
# (revue adversariale du 2026-09-10, bloquant B4).
_BASE = os.environ["APP_DB_PATH"]
_VERROU = Path(_BASE + ".verrou")
# Au-delà, on considère le détenteur mort (processus tué, machine redémarrée) et
# on reprend le verrou : un verrou qui survit à son propriétaire est pire que
# pas de verrou du tout — il faut alors le supprimer à la main, et personne ne
# sait où il est.
# Ordre de grandeur d'une suite complète (mesurée entre 8 et 22 min ce jour-là),
# pas d'une demi-journée : le filet ne sert que si `GetExitCodeProcess` échoue à
# trancher, et un verrou qui survit une demi-heure de trop est déjà une gêne.
_VERROU_PERIME_S = 45 * 60


def _detenteur_vivant(pid: int) -> bool:
    """Le processus `pid` tourne-t-il encore ? Sans dépendance externe.

    Sur WINDOWS, `os.kill(pid, 0)` ne répond pas à la question : mesuré le
    2026-09-10, un PID inexistant y lève `OSError [WinError 87] Paramètre
    incorrect` — indistinguable d'un vrai problème d'accès. Un premier jet
    traitait ce cas en « vivant, dans le doute » : le verrou d'un pytest TUÉ
    survivait alors 4 heures et bloquait tout, c'est-à-dire précisément ce que
    son propre commentaire décrit comme pire que pas de verrou. Trouvé par le
    scénario de vérification, pas par la lecture.

    `OpenProcess` tranche, lui : handle nul pour un PID mort, non nul sinon
    (0x1000 = PROCESS_QUERY_LIMITED_INFORMATION, le droit le plus faible, il
    suffit à savoir qu'il existe).
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        noyau = ctypes.windll.kernel32
        handle = noyau.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            # `OpenProcess` ne suffit PAS : il réussit encore sur un processus
            # TERMINÉ tant qu'un handle reste ouvert quelque part (le shell
            # lanceur, un parent `Popen`, un antivirus). Un verrou de pytest tué
            # bloquait donc jusqu'à sa péremption — ce que le commentaire de
            # `_VERROU_PERIME_S` qualifie lui-même de pire que pas de verrou
            # (revue du 2026-09-10, bloquant B2). `GetExitCodeProcess` tranche :
            # seul 259 (STILL_ACTIVE) veut dire « tourne encore ».
            code = ctypes.c_ulong()
            if not noyau.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True  # état indéterminable : ne pas voler le verrou
            return code.value == 259
        finally:
            noyau.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # existe, mais appartient à quelqu'un d'autre
    except OSError:
        return True  # doute : mieux vaut refuser un run de trop
    return True


def _debut_processus(pid: int):
    """Instant de DÉMARRAGE du processus `pid`, ou `None` si indéterminable.

    Un PID ne suffit pas à identifier un processus : Windows comme Linux les
    recyclent. Un pytest tué peut donc voir son PID repris par n'importe quoi,
    et son verrou tenir jusqu'à péremption — 45 min pendant lesquelles aucune
    suite ne démarre, ce que le commentaire de `_VERROU_PERIME_S` qualifie
    lui-même de pire que pas de verrou (revue du 2026-09-10, T14). L'instant de
    démarrage, lui, distingue deux processus de même PID.

    `None` veut dire « je ne sais pas » : l'appelant NE DOIT PAS conclure à la
    mort du détenteur sur cette base — voler un verrou par excès de zèle rend
    la cascade `WinError 32` que tout ceci cherche à éviter.
    """
    if pid <= 0:
        return None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        noyau = ctypes.windll.kernel32
        handle = noyau.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            autres = (wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME())
            if not noyau.GetProcessTimes(handle, ctypes.byref(creation),
                                         *[ctypes.byref(f) for f in autres]):
                return None
            return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        finally:
            noyau.CloseHandle(handle)
    try:  # Linux/macOS : 22e champ de /proc/<pid>/stat, en tops d'horloge.
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            champs = fh.read().rsplit(")", 1)[-1].split()
        return int(champs[19])
    except Exception:
        return None


def _lire_le_verrou():
    """`(pid, age_s, debut)` du détenteur courant, ou `None` si le verrou
    n'existe pas. `debut` vaut `None` pour un verrou d'avant ce format.

    Un verrou illisible ou malformé est rendu comme PÉRIMÉ (`age` au-delà du
    seuil) plutôt que comme absent : il faut pouvoir le reprendre, mais sans
    prétendre savoir qui le tenait.
    """
    try:
        contenu = _VERROU.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return (0, _VERROU_PERIME_S + 1, None)
    try:
        champs = contenu.split()
        pid = int(champs[0])
        age = time.time() - float(champs[1])
        debut = None
        if len(champs) > 2:
            try:
                debut = int(champs[2])
            except ValueError:
                debut = None  # champ abîmé : on l'ignore, sans périmer le verrou
        return (pid, age, debut)
    except Exception:
        return (0, _VERROU_PERIME_S + 1, None)


def _contenu_du_verrou() -> str:
    """`<pid> <horodatage>[ <debut>]`.

    Le troisième champ est OMIS quand l'instant de démarrage est indéterminable,
    jamais écrit « None » : le relire lèverait, et `_lire_le_verrou` traiterait
    alors un verrou parfaitement valide comme périmé — donc à voler.
    """
    debut = _debut_processus(os.getpid())
    base = f"{os.getpid()} {time.time()}"
    return base if debut is None else f"{base} {debut}"


def _detenteur_actif(detenteur) -> bool:
    """Le verrou lu est-il tenu par QUELQU'UN D'AUTRE, vivant et non périmé ?"""
    if detenteur is None:
        return False
    pid, age, debut = detenteur
    if pid == os.getpid():
        # Notre propre verrou : ré-entrance, pas contention. Sans ce test, une
        # seconde exécution du module dans le MÊME processus (importmode,
        # second rootdir) se refuserait l'entrée à elle-même.
        return False
    if not _detenteur_vivant(pid):
        return False
    if debut is not None:
        actuel = _debut_processus(pid)
        if actuel is not None and actuel != debut:
            # Même PID, processus DIFFÉRENT : le détenteur est mort et son
            # numéro a été réattribué. Le verrou est à prendre.
            return False
    return age < _VERROU_PERIME_S


def _refuser(pid: int, age: float, debut=None) -> None:
    """`debut` est ignoré du message : il sert à identifier le détenteur, pas à
    l'expliquer. Il figure dans la signature parce que `_lire_le_verrou` rend
    un triplet et que les appelants le dépaquettent tel quel."""
    raise RuntimeError(
        f"Une autre suite pytest tourne deja sur cette base (PID {pid}, "
        f"depuis {int(age)} s) : {_BASE}\n"
        "Deux runs concurrents partagent le meme fichier et beaucoup de "
        "modules le SUPPRIMENT en setup_module -- le second produit une "
        "cascade de PermissionError (WinError 32) qui ne decrit aucun "
        "defaut du produit. Mesure les 2026-09-09 (10 erreurs) et "
        "2026-09-10 (20).\n"
        "Attendre la fin du run en cours, ou lancer celui-ci sur sa "
        "propre base : APP_DB_PATH=<autre chemin>. Pour desarmer ce "
        "verrou : PYTEST_SANS_VERROU=1."
    )


def _prendre_le_verrou() -> None:
    if os.environ.get("PYTEST_SANS_VERROU") == "1":
        return
    if os.environ.get("PYTEST_XDIST_WORKER") and "xdist" in sys.modules:
        # Les workers `pytest-xdist` sont les enfants d'un même run : ils
        # partagent le verrou pris par leur maître. Sans cette sortie, le
        # premier worker le prendrait et refuserait tous les autres, rendant
        # `-n auto` impossible (revue du 2026-09-10, T14).
        #
        # La variable SEULE ne suffit pas : elle s'hérite par l'environnement,
        # donc un pytest lancé depuis un worker (ou depuis un shell qui en a
        # gardé la trace) tournerait SANS verrou — le trou exact que tout ceci
        # ferme. On exige donc que xdist soit réellement chargé (ronde 2).
        return
    detenteur = _lire_le_verrou()
    if _detenteur_actif(detenteur):
        _refuser(*detenteur)
    # Création EXCLUSIVE (`x`) : deux runners qui démarrent en même temps ne
    # peuvent pas croire tous les deux avoir pris le verrou.
    try:
        with open(_VERROU, "x", encoding="utf-8") as fh:
            fh.write(_contenu_du_verrou())
        return
    except FileExistsError:
        pass
    except OSError:
        return  # verrou non posable : on ne bloque pas la suite pour autant
    # `x` a échoué : quelqu'un a créé le verrou ENTRE notre lecture et notre
    # écriture. C'est la course que ce fichier existe pour fermer, et la
    # première version la perdait en silence — elle écrasait d'autorité le
    # verrou du gagnant, laissait les deux suites tourner (donc la cascade
    # `WinError 32` revenait), et privait le gagnant de son propre nettoyage
    # puisque `_rendre_le_verrou` ne retire QUE le verrou portant son PID.
    # Son commentaire affirmait « le perdant retombe sur le chemin de refus en
    # relisant le fichier du gagnant » : il n'y avait aucune relecture.
    # On relit, et on ré-applique la même décision (revue du 2026-09-10, T1).
    detenteur = _lire_le_verrou()
    if _detenteur_actif(detenteur):
        _refuser(*detenteur)
    try:
        _VERROU.write_text(_contenu_du_verrou(), encoding="utf-8")
    except OSError:
        pass


def _rendre_le_verrou() -> None:
    """Ne retire QUE notre propre verrou : si un autre processus l'a repris
    entre-temps (le nôtre ayant été jugé périmé), le lui laisser."""
    try:
        contenu = _VERROU.read_text(encoding="utf-8").strip()
        if contenu.split(" ")[0] == str(os.getpid()):
            _VERROU.unlink()
    except Exception:
        pass


_prendre_le_verrou()
atexit.register(_rendre_le_verrou)



def vider_recordings_de_test() -> None:
    """Vide le répertoire d'enregistrements DE TEST.

    Rien ne l'a jamais nettoyé : les fichiers s'accumulaient d'un run à l'autre
    (178 constatés le 2026-09-01). C'était latent tant que le code supprimait
    lui-même les imports aboutis ; depuis que l'audio ne se supprime plus que
    par une action de l'utilisateur — la règle du produit — chaque run en
    laisse, et `test_mission_backups` (qui affirme un inventaire EXACT de
    `RECORDINGS_DIR`) échoue sur les résidus. Un échec de ce genre est le pire
    à diagnostiquer : il ne se reproduit pas en isolant le test, et il accuse
    un code qui n'a rien fait.

    Appelée au démarrage de la session (résidus du run PRÉCÉDENT) ET par le
    `setup_module` de `test_mission_backups` : les imports de la MEME session
    s'accumulent aussi — ils portent désormais le préfixe `1_import_…`, donc la
    mission n° 1 de ce module-là se les voit attribuer comme orphelins.

    Garde-fou : on ne touche qu'un répertoire situé sous le temporaire système
    et dérivé d'`APP_DB_PATH`. Jamais `data/recordings`, qui porte les
    enregistrements réels de l'utilisateur (une confusion de ce type a déjà
    coûté des données sur ce projet)."""
    from pathlib import Path

    recordings = Path(os.environ["APP_DB_PATH"]).parent / "recordings"
    temp = Path(tempfile.gettempdir()).resolve()
    try:
        if temp not in recordings.resolve().parents:
            return  # pas sous le temporaire système : on ne touche à rien
    except OSError:
        return
    for chemin in recordings.glob("*"):
        if chemin.is_file():
            try:
                chemin.unlink()
            except OSError:
                pass  # verrou Windows : le run suivant le reprendra


def pytest_sessionstart(session):  # noqa: ARG001
    """Nettoyage au démarrage de la session."""
    vider_recordings_de_test()


@pytest.fixture(autouse=True)
def _constats_ia_neutres(monkeypatch: pytest.MonkeyPatch):
    """Le job de synthèse globale enchaîne la génération IA des constats (I2,
    tranche 4). Les tests qui simulent `generate_global_synthesis` sans rien
    savoir de cette seconde étape partiraient sinon vers un VRAI fournisseur IA
    (Ollama écoute souvent sur le poste) : lent, non déterministe. Par défaut
    elle rend une liste vide (= constats intacts) ; un test qui l'exerce la
    remplace par son propre `monkeypatch.setattr`."""
    from app.services import global_synthesis_job

    monkeypatch.setattr(global_synthesis_job, "generate_constats",
                        lambda *a, **k: {"constats": [], "ids_rejetes": []})


@pytest.fixture
def tmp_path_git(monkeypatch: pytest.MonkeyPatch):
    """Un dossier temporaire où `git` FONCTIONNE même quand la suite tourne
    ÉLEVÉE (VS Code lancé en administrateur, donc Claude Code, donc pytest) —
    mesuré le 2026-09-09 : 12 tests rouges (hooks, check_ci, inventaire git) sans
    aucune régression, pour deux raisons distinctes.

    1. Le `tmp_path` de pytest — et `tempfile.mkdtemp` — sont créés avec
       `mode=0o700`, que Python 3.13+ honore sur Windows par une ACL restrictive
       (Système, Administrateurs, « droits du propriétaire », sans entrée pour
       l'utilisateur) : `git init` y meurt sur « unable to get current working
       directory » (ou « .git: Permission denied »). D'où `mkdir()` sans mode :
       ACL héritée du parent.
    2. Un dossier créé par un processus élevé appartient à BUILTIN\\Administrators,
       pas à l'utilisateur : `git commit` refuse (« detected dubious ownership »).
       D'où `safe.directory=*`, passé par l'environnement (`GIT_CONFIG_*`, git ≥
       2.36) pour que les `git` en sous-processus l'héritent sans toucher à la
       config du poste.

    Les tests concernés redéfinissent `tmp_path` sur cette fixture."""
    import shutil
    import uuid

    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.directory")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "*")
    dossier = Path(tempfile.gettempdir()) / f"i2d-git-{uuid.uuid4().hex[:12]}"
    dossier.mkdir()  # mode par défaut : ACL héritée du parent, git y lit son cwd
    try:
        yield dossier
    finally:
        shutil.rmtree(dossier, ignore_errors=True)  # verrous Windows : au pire il reste


# --------------------------------------------------------------------------- #
# Windows : neutraliser le crash de nettoyage tmp de fin de session de pytest.
# Le housekeeping de `pytest_sessionfinish` supprime la jonction `pytest-current`
# sous %TEMP%\pytest-of-<user>\ ; sur cette machine l'unlink lève par intermittence
# `PermissionError [WinError 5]`. Levée DANS `pytest_sessionfinish`, l'exception
# supprime la ligne de synthèse `=== N passed ===` ET force un exit code 1 alors
# que tous les tests passent — 3 fausses alertes rien qu'au 2026-07-21 (cf. mémoire
# feedback-pytest-windows-teardown-noise). On rend ce ménage non fatal, sans toucher
# à la gestion normale des tmp de pytest (auto-nettoyage des anciens dossiers). Garde-
# fou : si l'API privée `_pytest.pathlib` bouge, le try/except laisse le comportement
# d'origine (le bruit revient, mais rien ne casse).
try:
    import _pytest.pathlib as _pytest_pathlib

    _orig_cleanup_dead_symlinks = _pytest_pathlib.cleanup_dead_symlinks

    def _cleanup_dead_symlinks_safe(root):
        try:
            _orig_cleanup_dead_symlinks(root)
        except OSError:
            pass  # WinError 5 sur pytest-current : housekeeping, pas un échec de test

    _pytest_pathlib.cleanup_dead_symlinks = _cleanup_dead_symlinks_safe
except Exception:  # pragma: no cover - garde-fou si l'API interne de pytest change
    pass


# --------------------------------------------------------------------------- #
# Origine par défaut pour TOUS les TestClient (2026-09-04) : le middleware
# anti-CSRF (`app.csrf.verifier_origine`, finding audit-technique
# securite:critique) rejette désormais toute méthode mutante (POST/PUT/
# DELETE/PATCH) sans Origin/Referer correspondant au Host — ce qu'aucun des
# ~22 fichiers de test n'envoie (`TestClient(app)` nu, comportement httpx par
# défaut). Patcher `TestClient.__init__` UNE fois ici plutôt que 22 fixtures
# séparées : un test qui fixe explicitement `headers=` garde la main (on ne
# force que la valeur par défaut, jamais une valeur déjà posée par l'appelant).
# `http://testserver` : l'hôte par défaut de httpx/Starlette pour un
# `TestClient(app)` sans `base_url` explicite (vérifié empiriquement) — les
# rares tests qui passeraient un `base_url` différent devront fixer `headers`
# eux-mêmes, ce patch ne devine pas un hôte qu'il ne connaît pas d'avance.
try:
    from fastapi.testclient import TestClient as _TestClient

    _orig_testclient_init = _TestClient.__init__

    def _testclient_init_with_origin(self, *args, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        if not any(k.lower() == "origin" for k in headers):
            headers["origin"] = "http://testserver"
        kwargs["headers"] = headers
        _orig_testclient_init(self, *args, **kwargs)

    _TestClient.__init__ = _testclient_init_with_origin
except Exception:  # pragma: no cover - garde-fou si l'API TestClient change
    pass
