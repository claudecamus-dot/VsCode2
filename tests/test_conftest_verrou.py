"""Une seule suite pytest à la fois sur la base partagée.

Tous les modules de tests partagent `interview_to_deck_test.db` (chemin FIXE
dans le temporaire système) et beaucoup la SUPPRIMENT en `setup_module`. Deux
runs concurrents se marchent donc dessus, et le second rend une cascade de
`PermissionError [WinError 32]` qui ne décrit aucun défaut du produit.

Mesuré deux fois : 10 erreurs le 2026-09-09 (un sous-agent relecteur lançant
pytest pendant la suite complète), 20 le 2026-09-10 (un fichier de test lancé
alors que la suite tournait encore). Le motif était déjà consigné en mémoire —
il a récidivé, parce qu'une mémoire dépend d'une vigilance et pas d'une
commande. Garde arbitrée par l'utilisateur le 2026-09-10.

Ces tests exercent le VERROU LUI-MÊME dans un sous-processus : le tester depuis
la suite en cours serait impossible (elle détient déjà le verrou).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
# L'interpréteur COURANT, pas un chemin deviné. La première version pointait
# `.venv/Scripts/python.exe` et sautait les tests quand il manquait : hors
# Windows (CI POSIX, où le venv expose `bin/python`), QUATRE des six tests de ce
# fichier se taisaient donc en silence — la garde n'était jamais exercée là où
# elle serait rejouée (revue du 2026-09-10, T15 ; le compte « trois sur quatre »
# d'un premier jet était faux, corrigé en ronde 2 : `git show HEAD` donne 6
# `def test_` et 4 `skipif`).
PYTHON = Path(sys.executable)
# Un fichier de test court, sans IA ni navigateur : on mesure le VERROU, pas lui.
CIBLE = "tests/test_swot.py"


def _lancer_pytest(basetemp: Path, env: dict | None = None,
                   base: Path | None = None) -> str:
    """Un pytest en sous-processus, sur SA PROPRE base.

    Le premier jet héritait de l'environnement, donc de `APP_DB_PATH` : les
    sous-processus tournaient sur la base PARTAGÉE, et `test_swot.py` la
    supprime en `setup_module`. Ce fichier — écrit pour empêcher la cascade
    `WinError 32` — la fabriquait donc dès qu'il tournait dans la suite
    complète (revue adversariale du 2026-09-10, bloquant B3).

    Chaque sous-processus reçoit une base à lui. Le verrou étant désormais
    dérivé de `APP_DB_PATH` (correctif B4), il en reçoit un aussi — c'est ce
    qui rend ces tests possibles sans toucher au verrou de la suite parente,
    sauf là où c'est justement l'objet du test."""
    base = base or (basetemp / "base_isolee.db")
    complet = {**os.environ, "APP_DB_PATH": str(base)}
    # `PYTEST_SANS_VERROU` est RETIRÉ par défaut : s'il traîne dans
    # l'environnement de la suite parente (posé pour la faire cohabiter avec un
    # autre run), il désarmerait le verrou du sous-processus et le test « un
    # second pytest est refusé » passerait au vert sans rien prouver. Chaque
    # test qui en a besoin le repose explicitement via `env`.
    complet.pop("PYTEST_SANS_VERROU", None)
    if env:
        complet.update(env)
    r = subprocess.run(
        [str(PYTHON), "-m", "pytest", CIBLE, "-q", "--basetemp", str(basetemp)],
        cwd=str(RACINE), capture_output=True, text=True, env=complet, timeout=600,
    )
    return (r.stdout or "") + (r.stderr or "")


def _verrou_de(base: Path) -> Path:
    return Path(str(base) + ".verrou")


@pytest.fixture
def base_isolee(tmp_path):
    """Une base ET son verrou, à ce test seul.

    On ne touche JAMAIS au verrou de la suite en cours : le remplacer, même
    temporairement, ferait échouer son propre nettoyage de fin de run — et
    surtout, deux tests concurrents se le voleraient."""
    return tmp_path / "base_du_test.db"


def test_un_second_pytest_est_refuse_avec_sa_raison(base_isolee, tmp_path):
    """Échouer VITE et dire pourquoi, plutôt que 20 erreurs illisibles."""
    _verrou_de(base_isolee).write_text(f"{os.getpid()} {time.time()}", encoding="utf-8")
    sortie = _lancer_pytest(tmp_path / "run1", base=base_isolee)
    assert "Une autre suite pytest tourne deja" in sortie, (
        "un second run concurrent n'est pas refuse : la cascade de WinError 32 "
        "reviendra, et elle ne decrit aucun defaut du produit"
    )
    assert "APP_DB_PATH" in sortie, "le message ne donne pas la sortie de secours"


def test_un_verrou_orphelin_ne_bloque_pas_indefiniment(base_isolee, tmp_path):
    """Un pytest TUÉ laisse son verrou. S'il survivait à son propriétaire, il
    faudrait le supprimer à la main — et personne ne sait où il est.

    Ce test a trouvé un vrai défaut le 2026-09-10 : la détection reposait sur
    `os.kill(pid, 0)`, qui sur Windows lève `OSError [WinError 87]` pour un PID
    inexistant — indistinguable d'un problème d'accès. Le verrou d'un run tué
    bloquait donc 4 heures."""
    _verrou_de(base_isolee).write_text(f"999999 {time.time()}", encoding="utf-8")
    sortie = _lancer_pytest(tmp_path / "run2", base=base_isolee)
    assert "passed" in sortie, (
        "un verrou dont le proprietaire est mort bloque encore : "
        f"sortie = {sortie[-400:]!r}"
    )


def test_la_porte_de_sortie_documentee_fonctionne(base_isolee, tmp_path):
    """Le message propose `PYTEST_SANS_VERROU=1` : une porte de sortie qui ne
    marcherait pas serait pire que pas de porte du tout."""
    _verrou_de(base_isolee).write_text(f"{os.getpid()} {time.time()}", encoding="utf-8")
    sortie = _lancer_pytest(
        tmp_path / "run3", env={"PYTEST_SANS_VERROU": "1"}, base=base_isolee,
    )
    assert "passed" in sortie, f"la porte de sortie ne fonctionne pas : {sortie[-400:]!r}"


def _conftest(monkeypatch):
    """Le module conftest, importé sans polluer `sys.path` durablement.

    `monkeypatch.syspath_prepend` défait l'insertion à la fin du test ; la
    première version laissait `tests/` en tête de `sys.path` pour toute la
    session, où il masque les modules de premier niveau."""
    monkeypatch.syspath_prepend(str(RACINE / "tests"))
    import conftest

    return conftest


def test_la_detection_de_processus_vivant_distingue_les_deux_cas(monkeypatch):
    """La brique elle-même, sans sous-processus : notre propre PID est vivant,
    un PID inexistant ne l'est pas. Sur Windows cette distinction passe par
    `OpenProcess` — `os.kill(pid, 0)` en est incapable."""
    conftest = _conftest(monkeypatch)

    assert conftest._detenteur_vivant(os.getpid()) is True
    assert conftest._detenteur_vivant(999_999) is False
    assert conftest._detenteur_vivant(0) is False
    assert conftest._detenteur_vivant(-1) is False


def test_une_suite_POSE_son_verrou_puis_le_REND(base_isolee, tmp_path):
    """La moitié PRODUCTRICE du protocole — celle qu'aucun test ne couvrait.

    Les trois tests ci-dessus écrivent eux-mêmes le fichier de verrou avant de
    lancer l'enfant : ils prouvent que le verrou est LU, jamais qu'il est ÉCRIT.
    Supprimer tout le bloc de création dans `conftest` (ou son
    `atexit.register`) les laissait tous les quatre au vert, avec une garde
    devenue inerte : plus aucun run n'annonce sa présence, donc plus aucun
    second run n'est refusé, et la cascade `WinError 32` revient intacte
    (revue adversariale du 2026-09-10, T6).

    On observe l'enfant PENDANT qu'il tourne, seul moment où le verrou existe.
    """
    verrou = _verrou_de(base_isolee)
    assert not verrou.exists(), "le verrou existe avant meme le lancement"

    complet = {**os.environ, "APP_DB_PATH": str(base_isolee)}
    complet.pop("PYTEST_SANS_VERROU", None)
    proc = subprocess.Popen(
        [str(PYTHON), "-m", "pytest", CIBLE, "-q",
         "--basetemp", str(tmp_path / "run_temoin")],
        cwd=str(RACINE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=complet,
    )
    vu = False
    try:
        while proc.poll() is None:
            if verrou.exists():
                vu = True
                break
            time.sleep(0.01)
    finally:
        sortie = proc.communicate(timeout=600)[0] or ""

    assert vu, (
        "le verrou n'a JAMAIS ete pose pendant le run : la garde est inerte, "
        "aucun second pytest ne sera refuse. Sortie de l'enfant : "
        f"{sortie[-400:]!r}"
    )
    assert not verrou.exists(), (
        "le verrou survit a son proprietaire : le prochain run sera refuse "
        "pendant 45 min par un detenteur qui n'existe plus"
    )


def test_un_PID_RECYCLE_ne_tient_pas_le_verrou(monkeypatch, tmp_path):
    """Un PID n'identifie pas un processus : Windows comme Linux les recyclent.

    Le verrou d'un pytest tué dont le numéro a été réattribué tenait jusqu'à sa
    péremption — 45 min sans qu'aucune suite ne démarre, au nom d'un détenteur
    qui n'existe plus (revue du 2026-09-10, T14). L'instant de démarrage
    distingue les deux processus.
    """
    conftest = _conftest(monkeypatch)
    verrou = tmp_path / "recycle.db.verrou"
    monkeypatch.setattr(conftest, "_VERROU", verrou)

    vivant = subprocess.Popen([str(PYTHON), "-c", "import time; time.sleep(30)"])
    try:
        vrai_debut = conftest._debut_processus(vivant.pid)
        if vrai_debut is None:
            pytest.skip("instant de demarrage indeterminable sur cette plateforme")

        # Le detenteur est VIVANT et le verrou FRAIS : seule la date de
        # demarrage revele que ce n'est plus le meme processus.
        verrou.write_text(f"{vivant.pid} {time.time()} {vrai_debut + 1}",
                          encoding="utf-8")
        assert conftest._detenteur_actif(conftest._lire_le_verrou()) is False, (
            "un PID recycle bloque encore le verrou pendant 45 min"
        )

        # Contrepartie : le VRAI detenteur, lui, tient toujours.
        verrou.write_text(f"{vivant.pid} {time.time()} {vrai_debut}", encoding="utf-8")
        assert conftest._detenteur_actif(conftest._lire_le_verrou()) is True, (
            "le vrai detenteur s'est fait voler son verrou"
        )
    finally:
        vivant.kill()
        vivant.wait(timeout=30)


def test_un_verrou_SANS_instant_de_demarrage_reste_lisible(monkeypatch, tmp_path):
    """Compatibilité : un verrou écrit avant ce format n'a que deux champs, et
    un instant de démarrage indéterminable fait OMETTRE le troisième. Ni l'un
    ni l'autre ne doit être lu comme périmé — ce serait un verrou valide
    déclaré volable."""
    conftest = _conftest(monkeypatch)
    verrou = tmp_path / "ancien.db.verrou"
    monkeypatch.setattr(conftest, "_VERROU", verrou)

    vivant = subprocess.Popen([str(PYTHON), "-c", "import time; time.sleep(30)"])
    try:
        verrou.write_text(f"{vivant.pid} {time.time()}", encoding="utf-8")
        pid, age, debut = conftest._lire_le_verrou()
        assert pid == vivant.pid and debut is None
        assert age < 60, "un verrou a deux champs est lu comme perime"
        assert conftest._detenteur_actif((pid, age, debut)) is True
    finally:
        vivant.kill()
        vivant.wait(timeout=30)


def test_les_workers_xdist_partagent_le_verrou_de_leur_maitre(monkeypatch, tmp_path):
    """`pytest -n auto` fait importer conftest par chaque worker. Sans sortie
    dédiée, le premier prendrait le verrou et refuserait tous les autres :
    la parallélisation deviendrait impossible (revue du 2026-09-10, T14)."""
    conftest = _conftest(monkeypatch)
    verrou = tmp_path / "xdist.db.verrou"
    monkeypatch.setattr(conftest, "_VERROU", verrou)

    vivant = subprocess.Popen([str(PYTHON), "-c", "import time; time.sleep(30)"])
    try:
        # Le verrou du « maitre » : son PID ET son vrai instant de demarrage.
        # Un premier jet y mettait le PID du maitre avec NOTRE instant de
        # demarrage : la detection de PID recycle le declarait donc mort, et le
        # test echouait sur une fixture fausse, pas sur le code.
        debut = conftest._debut_processus(vivant.pid)
        verrou.write_text(f"{vivant.pid} {time.time()} {debut}", encoding="utf-8")

        # `xdist` n'est pas installe ici : on simule sa presence, puisque la
        # garde exige desormais que le module soit REELLEMENT charge (la
        # variable d'environnement seule s'herite, et ouvrait un trou).
        monkeypatch.setitem(sys.modules, "xdist", types.ModuleType("xdist"))
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
        conftest._prendre_le_verrou()  # ne doit PAS lever
        assert verrou.read_text(encoding="utf-8").startswith(str(vivant.pid)), (
            "un worker xdist a vole le verrou de son maitre"
        )

        # Et la contrepartie : la variable SEULE, sans xdist charge, ne doit
        # PAS desarmer le verrou -- sinon un pytest lance depuis un worker
        # tournerait sans protection.
        monkeypatch.delitem(sys.modules, "xdist")
        with pytest.raises(RuntimeError, match="Une autre suite pytest tourne deja"):
            conftest._prendre_le_verrou()
    finally:
        vivant.kill()
        vivant.wait(timeout=30)


def test_le_perdant_de_la_course_NE_VOLE_PAS_le_verrou(monkeypatch, tmp_path):
    """Régression du bloquant T1 (revue du 2026-09-10).

    La course que ce fichier existe pour fermer : deux runners lisent l'absence
    de verrou, l'un gagne la création exclusive, l'autre reçoit
    `FileExistsError`. La première version ÉCRASAIT alors le verrou du gagnant
    et poursuivait — les deux suites tournaient, donc exactement la cascade
    `WinError 32` qu'on prétendait empêcher, et le gagnant perdait en prime son
    propre nettoyage (`_rendre_le_verrou` ne retire que le verrou à son PID).

    On force la fenêtre : la première lecture rend « libre », puis le verrou
    existe réellement au moment du `open(..., "x")`. Sur le code d'avant ce test
    échoue — aucune exception n'était levée et le fichier finissait au PID du
    perdant.
    """
    conftest = _conftest(monkeypatch)
    verrou = tmp_path / "course.db.verrou"
    monkeypatch.setattr(conftest, "_VERROU", verrou)
    monkeypatch.setattr(conftest, "_BASE", str(tmp_path / "course.db"))

    # Le gagnant : un détenteur VIVANT et frais, qui n'est pas nous.
    gagnant = subprocess.Popen([str(PYTHON), "-c", "import time; time.sleep(30)"])
    try:
        verrou.write_text(f"{gagnant.pid} {time.time()}", encoding="utf-8")

        vrai_lire = conftest._lire_le_verrou
        appels = {"n": 0}

        def _lire_en_courant():
            appels["n"] += 1
            if appels["n"] == 1:
                return None  # « libre » : on entre dans la creation exclusive
            return vrai_lire()

        monkeypatch.setattr(conftest, "_lire_le_verrou", _lire_en_courant)

        with pytest.raises(RuntimeError, match="Une autre suite pytest tourne deja"):
            conftest._prendre_le_verrou()

        assert appels["n"] >= 2, "le perdant n'a pas RELU le verrou du gagnant"
        assert verrou.read_text(encoding="utf-8").startswith(str(gagnant.pid)), (
            "le perdant a VOLE le verrou du gagnant : les deux suites vont "
            "tourner en concurrence"
        )
    finally:
        gagnant.kill()
        gagnant.wait(timeout=30)
