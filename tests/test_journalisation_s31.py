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
    assert lignes[0].endswith("FAILED tests/t.py::rouge")
    assert lignes[1].endswith("ERROR(setup) tests/t.py::cassé")
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


@pytest.fixture
def e2e(monkeypatch):
    import e2e_serveur

    _FauxUvicorn.lances = []
    monkeypatch.setattr(e2e_serveur.subprocess, "Popen", _FauxUvicorn)
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
