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
import warnings
from pathlib import Path

import pytest

# Base SQLite dédiée aux tests, dans le répertoire temporaire du système.
_TEST_DB = os.path.join(tempfile.gettempdir(), "interview_to_deck_test.db")
os.environ.setdefault("APP_DB_PATH", _TEST_DB)

# Images des têtes de chapitre : jamais de fetch réseau en test (offline,
# déterministe, rapide) — génération procédurale locale. cf. pptx_export.
os.environ.setdefault("PPTX_NO_PHOTO_FETCH", "1")
# Pas de préchauffage Ollama réel pendant les tests (route transcribe-segment).
os.environ.setdefault("OLLAMA_WARM_UP", "0")
# Idem Whisper : le lifespan lance le préchauffage dans un fil daemon ; sans cela
# tout TestClient(app) non stubbé chargerait un vrai modèle `medium` en fond.
# Les tests qui exercent le warm-up posent WHISPER_WARM_UP eux-mêmes (monkeypatch).
os.environ.setdefault("WHISPER_WARM_UP", "0")

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
# Ceiling for a LIVE, identified holder (Story 3.1 review): without one, a hung
# pytest would block every run forever. 12 h is far above any measured suite
# (2 h 35 on 2026-10-20, 9 h 59 on 2026-10-14) and the refusal names the PID.
_VERROU_PLAFOND_VIVANT_S = 12 * 3600


_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87
_NOYAU = None


def _noyau_windows():
    """kernel32 chargé avec `use_last_error=True` : seul moyen fiable de lire
    le GetLastError() d'un appel ctypes (`ctypes.get_last_error()`)."""
    global _NOYAU
    if _NOYAU is None:
        import ctypes

        from ctypes import wintypes

        noyau = ctypes.WinDLL("kernel32", use_last_error=True)
        # (re-review 2026-11-17, A4) Without restype the HANDLE was a C int:
        # truncated on 64-bit, and a handle whose low 32 bits are 0 read as null.
        noyau.OpenProcess.restype = wintypes.HANDLE
        noyau.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        noyau.CloseHandle.argtypes = [wintypes.HANDLE]
        noyau.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        noyau.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        _NOYAU = noyau
    return _NOYAU


def _ouvrir_processus(pid: int):
    """`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` -> (handle, GetLastError).

    Point unique (et substituable en test) : `_detenteur_vivant` doit
    distinguer « PID inexistant » (87) de « accès refusé » (5)."""
    import ctypes

    handle = _noyau_windows().OpenProcess(0x1000, False, pid)
    return handle, (0 if handle else ctypes.get_last_error())


def _etat_detenteur(pid: int) -> str:
    """« mort », « vivant » ou « refuse » (vivant, mais OpenProcess refusé : 5).

    Sur WINDOWS, `os.kill(pid, 0)` ne répond pas à la question : mesuré le
    2026-09-10, un PID inexistant y lève `OSError [WinError 87] Paramètre
    incorrect` — indistinguable d'un vrai problème d'accès. Un premier jet
    traitait ce cas en « vivant, dans le doute » : le verrou d'un pytest TUÉ
    survivait alors 4 heures et bloquait tout, c'est-à-dire précisément ce que
    son propre commentaire décrit comme pire que pas de verrou. Trouvé par le
    scénario de vérification, pas par la lecture.

    `OpenProcess` tranche, lui : handle nul pour un PID mort, non nul sinon
    (0x1000 = PROCESS_QUERY_LIMITED_INFORMATION, le droit le plus faible, il
    suffit à savoir qu'il existe). Handle nul avec ERROR_ACCESS_DENIED (5) : le
    processus EXISTE mais nous est fermé (détenteur élevé, runner non élevé) —
    vivant, jamais « mort » (re-review 2026-11-17, B1 : le verrou était volé
    avant même le code F6a de `_detenteur_actif`).

    (re-review 2026-11-17, A4) Seul ERROR_INVALID_PARAMETER (87) veut dire
    « PID inexistant ». Toute autre erreur (8 mémoire, 1450 ressources, 0) dit
    « vivant ou inconnu » : jamais un verrou volé à un détenteur vivant. Le 5 est
    rendu à part (« refuse ») : un PID recyclé par un process SYSTEM le rend
    aussi, l'appelant lui applique donc le seuil de péremption normal.
    """
    if pid <= 0:
        return "mort"
    if sys.platform == "win32":
        import ctypes

        noyau = _noyau_windows()
        handle, erreur = _ouvrir_processus(pid)
        if not handle:
            if erreur == _ERROR_INVALID_PARAMETER:
                return "mort"
            return "refuse" if erreur == _ERROR_ACCESS_DENIED else "vivant"
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
                return "vivant"  # état indéterminable : ne pas voler le verrou
            return "vivant" if code.value == 259 else "mort"
        finally:
            noyau.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "mort"
    except PermissionError:
        return "vivant"  # existe, mais appartient à quelqu'un d'autre
    except OSError:
        return "vivant"  # doute : mieux vaut refuser un run de trop
    return "vivant"


def _detenteur_vivant(pid: int) -> bool:
    """Le processus `pid` tourne-t-il encore (ou ne peut-on pas l'exclure) ?"""
    return _etat_detenteur(pid) != "mort"


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

        noyau = _noyau_windows()
        handle, _erreur = _ouvrir_processus(pid)
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
    etat = _etat_detenteur(pid)
    if etat == "mort":
        return False
    if etat == "refuse" and debut is None:
        # (re-review 2026-11-17, A4) ERROR_ACCESS_DENIED: alive-but-closed OR a
        # recycled PID now owned by SYSTEM. With NO recorded start time nothing
        # identifies the holder: the normal stale threshold applies. With a
        # recorded `debut`, fall through to the F6a ceiling below — an elevated
        # 2 h 35 suite must not lose its lock to a non-elevated runner at 45 min.
        return age < _VERROU_PERIME_S
    if debut is not None:
        actuel = _debut_processus(pid)
        if actuel is not None and actuel != debut:
            return False  # PID recycled (different start): the lock is free
        # Same start: the holder is alive, and its lock holds WHATEVER ITS AGE
        # — a 2 h 35 suite (2026-10-20) had its lock stolen at 45 min, then
        # WinError 32 (Story 3.1, D-2). Start time UNREADABLE (`debut` stored
        # but `actuel` None): OpenProcess denied between an elevated and a
        # non-elevated shell (final review F6a). `_detenteur_vivant` answers
        # « alive » on that ERROR_ACCESS_DENIED (re-review B1 — before, it said
        # « dead » and the lock was stolen before reaching this line); here
        # nothing says it is someone else, so it is treated as the holder,
        # never as a 45 min-old unknown (that was D-2 again).
        if age < _VERROU_PLAFOND_VIVANT_S:
            return True
        # (F6b) Beyond the ceiling the lock is taken over — never silently.
        warnings.warn(
            f"Verrou de la base de test REPRIS a un detenteur VIVANT (PID {pid}, "
            f"depuis {int(age)} s, au-dela du plafond de {_VERROU_PLAFOND_VIVANT_S} s) : "
            f"{_BASE}. Si ce pytest tourne encore, deux runs partagent la base "
            f"(cascade WinError 32) : taskkill /F /T /PID {pid}.",
            stacklevel=2,
        )
        return False
    # Identity unknown (old-format lock without start time): age is the only
    # remaining safety net against a lock outliving its owner.
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
        f"Si ce run est BLOQUE : taskkill /F /T /PID {pid} (Windows) ou "
        f"kill {pid}.\n"
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

    Garde-fou en LISTE BLANCHE STRICTE (revues 2026-10-19 : une liste noire du seul
    `data/` du dépôt courant effaçait l'audio réel d'un AUTRE dossier de données
    si `APP_DB_PATH` en héritait ; puis un garde fondé sur le seul nom `app.db`
    laissait passer une COPIE de la base placée sous le temporaire — perte
    irréversible). On ne supprime que si TOUT ceci est vrai :
      - `APP_DB_PATH` est ABSOLU et non vide ;
      - le dossier n'est pas le `data/` du dépôt et ne contient aucune base `*.db`
        autre que `APP_DB_PATH` lui-même (ni `app.db`, ni une copie) ;
      - ET soit `APP_DB_PATH` est EXACTEMENT la base de test que ce conftest a
        choisie (`_TEST_DB`, dans le temporaire système), soit l'autorisation est
        EXPLICITE (`APP_TEST_CLEAN_RECORDINGS=1`, bissection avec une base de test
        placée où l'on veut — sous la responsabilité de qui pose la variable).
    Sinon : on ne supprime rien."""
    from pathlib import Path

    brut = os.environ.get("APP_DB_PATH", "").strip()
    if not brut:
        return
    base = Path(brut)
    if not base.is_absolute():
        return
    try:
        reel = (Path(__file__).resolve().parent.parent / "data").resolve()
        base_res = base.resolve()
        res = (base_res.parent / "recordings").resolve()
        if reel == res.parent or reel in res.parents:
            return
        base_de_test = Path(_TEST_DB).resolve()
        est_la_base_de_test = base_res == base_de_test
        # Une autre base dans le dossier (app.db, copie, sauvegarde) = donnees
        # reelles probables : on ne touche a rien. Sauf pour LA base de test que
        # ce conftest a lui-meme posee dans le temporaire systeme : ce dossier est
        # partage avec toute la machine (des dizaines de `*.db` d'autres outils),
        # son `recordings/` n'appartient qu'aux tests, et y renoncer laissait les
        # residus s'accumuler jusqu'a faire echouer `test_mission_backups`.
        if not est_la_base_de_test:
            for voisin in res.parent.glob("*.db"):
                if voisin.resolve() != base_res:
                    return
        elif (res.parent / "app.db").exists():
            # TMP/TEMP pose sur un dossier de donnees : `_TEST_DB` y atterrit aussi.
            return
        # Un `recordings` qui est un lien ou une jonction peut pointer ailleurs
        # (le `data/recordings` d'un autre depot) : on ne vide pas.
        raw = base_res.parent / "recordings"
        if raw.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(raw)):
            return
        autorise = os.environ.get("APP_TEST_CLEAN_RECORDINGS") == "1"
        if not (est_la_base_de_test or autorise):
            return
    except (OSError, RuntimeError):
        return
    for chemin in res.glob("*"):
        if chemin.is_file():
            try:
                chemin.unlink()
            except OSError:
                pass  # verrou Windows : le run suivant le reprendra


def pytest_sessionstart(session):  # noqa: ARG001
    """Nettoyage au démarrage de la session."""
    vider_recordings_de_test()
    _ouvrir_fichier_echecs()


# --------------------------------------------------------------------------- #
# Failures written AS THEY HAPPEN (Story 3.1, AC3.1.2 — finding D-3)
#
# In `-q`, failing nodeids only appear in the final summary: a run that hangs
# or is killed after 2 h (2026-10-20: 2 h 35; 2026-10-14: 9 h 59) leaves no
# name at all. Each failed/errored phase is appended and flushed immediately.
# Path: `PYTEST_ECHECS_FICHIER`, else next to the test base
# (`<APP_DB_PATH>.echecs.log`) — same isolation key as the lock above, so two
# runners on two bases never mix their files, and the lock guarantees one run
# per base (the file is truncated at session start).
# --------------------------------------------------------------------------- #
def chemin_fichier_echecs() -> Path:
    return Path(os.environ.get("PYTEST_ECHECS_FICHIER") or (_BASE + ".echecs.log"))


def _ouvrir_fichier_echecs() -> None:
    """Run header; the file is TRUNCATED only when the lock guarantees we are
    alone on this base. With `PYTEST_SANS_VERROU=1` another run may be writing
    the same file: append, never erase its trail (Story 3.1 review)."""
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return  # only the controller writes (it receives the workers' reports)
    try:
        chemin = chemin_fichier_echecs()
        chemin.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if os.environ.get("PYTEST_SANS_VERROU") == "1" else "w"
        with open(chemin, mode, encoding="utf-8") as fh:
            fh.write(f"# pytest PID {os.getpid()} started "
                     f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    except OSError:
        pass  # never fail a suite for its diagnostic file


def pytest_report_header(config):  # noqa: ARG001
    return f"failures written as they happen to: {chemin_fichier_echecs()}"


def _ecrire_echec(nature: str, nodeid: str) -> None:
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return  # the controller writes it once: no double line under xdist
    try:
        with open(chemin_fichier_echecs(), "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {nature} pid={os.getpid()} "
                     f"{nodeid}\n")
            fh.flush()
    except OSError:
        pass


def pytest_runtest_logreport(report):
    if report.failed:
        _ecrire_echec("FAILED" if report.when == "call" else f"ERROR({report.when})",
                      report.nodeid)


def pytest_collectreport(report):
    # A module that fails to IMPORT is an error too, and the commonest one
    # after a refactor (Story 3.1 review).
    if report.failed:
        _ecrire_echec("ERROR(collect)", report.nodeid)


# --------------------------------------------------------------------------- #
# e2e: the server log tail goes into the failure report (Story 3.1 review).
# Most e2e files get their server from a MODULE-scoped fixture: pytest never
# throws the test failure into that generator, so serveur_uvicorn's own
# `add_note` cannot see it. This hook reads the registry of active servers
# kept by tests/e2e_serveur.py and attaches each log tail as a report section.
# --------------------------------------------------------------------------- #
def sections_journaux_e2e() -> list[tuple[str, str]]:
    module = sys.modules.get("e2e_serveur")
    if module is None:
        return []
    return [(f"journal uvicorn {base} ({chemin})", module.fin_du_journal(chemin))
            for base, chemin in list(module.SERVEURS_ACTIFS.items())]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):  # noqa: ARG001
    resultat = yield
    rapport = resultat.get_result()
    if rapport.failed:
        for titre, texte in sections_journaux_e2e():
            rapport.sections.append((titre, texte))


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


@pytest.fixture(autouse=True)
def _structuration_synchrone(monkeypatch: pytest.MonkeyPatch):
    """La structuration différée tourne sur un fil démon dédié
    (`structuration_libre`, F1) : en test, elle s'exécute dans l'appelant pour
    rester déterministe, et l'attente des tranches en vol est bornée court (un
    job `running` jamais terminé ferait sinon attendre 30 min). Les tests du
    worker et de l'attente reposent leurs propres valeurs. Les e2e lancent un
    vrai uvicorn (sous-processus) : ils gardent le vrai worker."""
    from app.services import structuration_libre

    monkeypatch.setattr(structuration_libre, "EXECUTION_SYNCHRONE", True)
    monkeypatch.setattr(structuration_libre, "ATTENTE_TRANCHES_S", 1.0)
    monkeypatch.setattr(structuration_libre, "ATTENTE_PAS_S", 0.05)


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


@pytest.fixture(autouse=True)
def _registre_segments_vide():
    """Le dédoublonnage de /audio/transcribe-segment (sha1 du blob) ne doit pas
    faire fuiter un résultat d'un test à l'autre."""
    from app.services.segment_dedupe import REGISTRE

    import threading

    def _joindre():
        for t in threading.enumerate():
            if t.name == "transcribe-segment":
                t.join(30)  # un thread démon en vol ne doit pas déborder sur le test suivant

    _joindre()
    REGISTRE.vider()
    yield
    _joindre()
    REGISTRE.vider()
