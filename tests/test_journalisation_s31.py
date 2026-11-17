"""Story 3.1 — failures leave a dated trace (AC3.1.1, AC3.1.2, AC3.1.4).

- D-1: `app.*` INFO lines land in a rotating, timestamped app log; one handler
  only, however many lifespans run; faulthandler enabled.
- D-3: pytest has a per-test faulthandler timeout locally (like the CI) and
  failed nodeids are written as they happen.
- D-4/D-5: a red e2e test carries the end of the server log; the e2e server's
  password comes from the env passed to it, with an explicit error if missing.
"""
from __future__ import annotations

import faulthandler
import logging
import re
import types
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from app import journalisation


def _handlers_marques():
    return [h for h in logging.getLogger("app").handlers
            if getattr(h, journalisation._MARQUEUR, False)]


@pytest.fixture
def journal_isole(tmp_path, monkeypatch):
    """Detach the session's app-log handler, point APP_LOG_DIR at tmp_path,
    restore everything afterwards."""
    racine = logging.getLogger("app")
    anciens = _handlers_marques()
    niveau = racine.level
    for h in anciens:
        racine.removeHandler(h)
    monkeypatch.setenv("APP_LOG_DIR", str(tmp_path / "logs"))
    yield tmp_path / "logs"
    for h in _handlers_marques():
        racine.removeHandler(h)
        h.close()
    for h in anciens:
        racine.addHandler(h)
    racine.setLevel(niveau)


def test_un_info_app_est_ecrit_horodate_dans_un_journal_rotatif(journal_isole):
    chemin = journalisation.configurer_journalisation()
    assert chemin == journal_isole / "app.log"
    logging.getLogger("app.services.exemple").info("job fige depuis %d s", 42)
    for h in _handlers_marques():
        h.flush()
    texte = chemin.read_text(encoding="utf-8")
    assert re.search(
        r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ INFO app\.services\.exemple "
        r"\[MainThread\] job fige depuis 42 s$", texte, re.M), texte
    (h,) = _handlers_marques()
    assert isinstance(h, RotatingFileHandler) and h.maxBytes > 0 and h.backupCount > 0
    assert faulthandler.is_enabled()


def test_configurer_deux_fois_n_empile_pas_les_handlers(journal_isole):
    journalisation.configurer_journalisation()
    journalisation.configurer_journalisation()
    assert len(_handlers_marques()) == 1, "chaque ligne serait ecrite deux fois"


def test_le_lifespan_configure_le_journal(journal_isole):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app):
        pass
    with TestClient(app):  # a second lifespan (reload, next test) adds nothing
        pass
    handlers = _handlers_marques()
    assert len(handlers) == 1
    assert Path(handlers[0].baseFilename) == journal_isole / "app.log"


# --------------------------------------------------------------------------- #
# AC3.1.2 — pytest diagnostics
# --------------------------------------------------------------------------- #
def test_faulthandler_timeout_local_comme_en_ci(request):
    assert float(request.config.getini("faulthandler_timeout")) == 300


def test_un_echec_est_ecrit_au_fil_de_l_eau(tmp_path, monkeypatch):
    import conftest

    fichier = tmp_path / "echecs.log"
    monkeypatch.setenv("PYTEST_ECHECS_FICHIER", str(fichier))
    ok = types.SimpleNamespace(failed=False, when="call", nodeid="tests/t.py::vert")
    rouge = types.SimpleNamespace(failed=True, when="call", nodeid="tests/t.py::rouge")
    erreur = types.SimpleNamespace(failed=True, when="setup", nodeid="tests/t.py::cassé")
    for rapport in (ok, rouge, erreur):
        conftest.pytest_runtest_logreport(rapport)
    lignes = fichier.read_text(encoding="utf-8").splitlines()
    assert len(lignes) == 2, lignes
    assert " FAILED " in lignes[0] and lignes[0].endswith(" tests/t.py::rouge")
    assert " ERROR(setup) " in lignes[1] and lignes[1].endswith(" tests/t.py::cassé")
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d ", lignes[0])


# --------------------------------------------------------------------------- #
# AC3.1.4 — e2e server harness (no browser, no real uvicorn: Popen is faked)
# --------------------------------------------------------------------------- #
class _FauxUvicorn:
    lances: list = []

    def __init__(self, args, stdout=None, **_kw):
        _FauxUvicorn.lances.append(args)
        stdout.write("INFO: ligne-temoin-du-serveur\n")
        stdout.flush()
        self._mort = False

    def poll(self):
        return 0 if self._mort else None

    def kill(self):
        self._mort = True

    def wait(self, timeout=None):  # noqa: ARG002
        return 0


class _Reponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def installer_faux_uvicorn(mp, e2e_serveur) -> None:
    """Replace the `subprocess`/`urllib` names SEEN BY e2e_serveur only: patching
    `subprocess.Popen` itself also hijacked unrelated calls (platform.ver)."""
    import subprocess
    import urllib.error
    import urllib.request

    mp.setattr(e2e_serveur, "subprocess", types.SimpleNamespace(
        Popen=_FauxUvicorn, STDOUT=subprocess.STDOUT,
        TimeoutExpired=subprocess.TimeoutExpired))
    mp.setattr(e2e_serveur, "urllib", types.SimpleNamespace(
        request=types.SimpleNamespace(Request=urllib.request.Request,
                                      urlopen=lambda *a, **k: _Reponse()),
        error=urllib.error))


@pytest.fixture
def e2e(monkeypatch):
    import e2e_serveur

    _FauxUvicorn.lances = []
    installer_faux_uvicorn(monkeypatch, e2e_serveur)
    monkeypatch.setattr(e2e_serveur.urllib.request, "urlopen",
                        lambda *a, **k: _Reponse())
    monkeypatch.setattr(e2e_serveur, "_DELAI_DEMARRAGE_S", 3.0)
    return e2e_serveur


def test_un_e2e_rouge_porte_la_fin_du_journal_serveur(e2e, tmp_path):
    with pytest.raises(AssertionError) as info:
        with e2e.serveur_uvicorn(tmp_path):
            raise AssertionError("bouton introuvable")
    notes = "\n".join(getattr(info.value, "__notes__", []))
    assert "ligne-temoin-du-serveur" in notes, notes


def test_mot_de_passe_absent_echoue_explicitement_sans_lancer(e2e, tmp_path, monkeypatch):
    monkeypatch.delenv("APP_AUTH_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="APP_AUTH_PASSWORD"):
        with e2e.serveur_uvicorn(tmp_path):
            pass
    assert _FauxUvicorn.lances == [], "uvicorn lance alors que le mot de passe manque"


def test_mot_de_passe_lu_dans_l_env_passe_au_serveur(e2e, tmp_path, monkeypatch):
    vus = []
    monkeypatch.setattr(e2e.urllib.request, "urlopen",
                        lambda req, **k: vus.append(req.get_header("Authorization")) or _Reponse())
    monkeypatch.delenv("APP_AUTH_PASSWORD", raising=False)
    with e2e.serveur_uvicorn(tmp_path, APP_AUTH_PASSWORD="mdp-du-serveur"):
        pass
    assert vus == ["Bearer mdp-du-serveur"]


# --------------------------------------------------------------------------- #
# Story 3.1 review round
# --------------------------------------------------------------------------- #
def test_journal_des_processus_enfants_idempotent_avec_nom_de_processus(journal_isole):
    racine = logging.getLogger("app")
    try:
        chemin = journalisation.configurer_journalisation_processus()
        assert journalisation.configurer_journalisation_processus() == chemin
        marques = [h for h in racine.handlers
                   if getattr(h, journalisation._MARQUEUR_WORKER, False)]
        assert len(marques) == 1
        logging.getLogger("app.services.audio_transcribe").info("bloc 3 transcrit")
        marques[0].flush()
        texte = chemin.read_text(encoding="utf-8")
        assert re.search(r"INFO app\.services\.audio_transcribe \[MainProcess/MainThread\] "
                         r"bloc 3 transcrit", texte), texte
    finally:
        for h in [h for h in racine.handlers
                  if getattr(h, journalisation._MARQUEUR_WORKER, False)]:
            racine.removeHandler(h)
            h.close()


def test_les_fichiers_propres_a_windows_tournent_dans_le_job_windows():
    import yaml

    racine = Path(__file__).resolve().parents[1]
    ci = yaml.safe_load((racine / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    commandes = " ".join(str(e.get("run", "")) for e in ci["jobs"]["test-windows"]["steps"])
    propres = sorted(p.name for p in (racine / "tests").glob("test_*.py")
                     if 'sys.platform != "win32"' in p.read_text(encoding="utf-8"))
    oublies = [n for n in propres if f"tests/{n}" not in commandes]
    assert propres and oublies == [], f"jamais exerces en CI : {oublies}"


def test_collecte_en_echec_ecrite_et_rien_cote_worker_xdist(tmp_path, monkeypatch):
    import conftest

    fichier = tmp_path / "echecs.log"
    monkeypatch.setenv("PYTEST_ECHECS_FICHIER", str(fichier))
    conftest.pytest_collectreport(types.SimpleNamespace(failed=True, nodeid="tests/t_casse.py"))
    conftest.pytest_collectreport(types.SimpleNamespace(failed=False, nodeid="tests/t_ok.py"))
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    conftest.pytest_runtest_logreport(
        types.SimpleNamespace(failed=True, when="call", nodeid="tests/t.py::double"))
    lignes = fichier.read_text(encoding="utf-8").splitlines()
    assert len(lignes) == 1 and lignes[0].endswith("tests/t_casse.py"), lignes
    assert "ERROR(collect)" in lignes[0]


def test_sans_verrou_l_en_tete_s_ajoute_sans_effacer_l_autre_run(tmp_path, monkeypatch):
    import conftest

    fichier = tmp_path / "echecs.log"
    fichier.write_text("2026-11-16T10:00:00 FAILED pid=1 tests/autre_run.py::x\n", encoding="utf-8")
    monkeypatch.setenv("PYTEST_ECHECS_FICHIER", str(fichier))
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setenv("PYTEST_SANS_VERROU", "1")
    conftest._ouvrir_fichier_echecs()
    texte = fichier.read_text(encoding="utf-8")
    assert "tests/autre_run.py::x" in texte and "# pytest PID" in texte
    monkeypatch.delenv("PYTEST_SANS_VERROU")
    conftest._ouvrir_fichier_echecs()  # alone on the base (lock held): truncate
    assert "autre_run" not in fichier.read_text(encoding="utf-8")


_E2E_MODULE = '''
import pytest
import e2e_serveur
from test_journalisation_s31 import installer_faux_uvicorn


@pytest.fixture(scope="module")
def serveur(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        installer_faux_uvicorn(mp, e2e_serveur)
        with e2e_serveur.serveur_uvicorn(tmp_path_factory.mktemp("srv")) as base:
            yield base


def test_rouge(serveur):
    assert False, "bouton introuvable"
'''


def test_un_e2e_rouge_sur_serveur_de_module_porte_la_fin_du_journal(tmp_path):
    """Module-scoped server: the failure never reaches serveur_uvicorn's
    generator; the conftest hook must attach the log tail to the report."""
    import os
    import subprocess
    import sys

    racine = Path(__file__).resolve().parents[1]
    (tmp_path / "test_module_e2e.py").write_text(_E2E_MODULE, encoding="utf-8")
    env = dict(os.environ, APP_DB_PATH=str(tmp_path / "base.db"),
               PYTHONPATH=os.pathsep.join([str(racine / "tests"), str(racine)]))
    env.pop("PYTEST_SANS_VERROU", None)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "test_module_e2e.py", "-p", "conftest", "-q",
         "-p", "no:cacheprovider", "--basetemp", str(tmp_path / "bt")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=150)
    sortie = r.stdout + r.stderr
    assert "1 failed" in sortie, sortie[-2000:]
    assert "journal uvicorn" in sortie and "ligne-temoin-du-serveur" in sortie, sortie[-2000:]
