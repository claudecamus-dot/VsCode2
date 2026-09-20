"""La sortie brute d'un sous-processus ne doit jamais atteindre le client.

Règle écrite par le dépôt lui-même (app/routers/interviews_audio.py:85-90) :
« Message FIXE, jamais `str(exc)` : sur ce chemin l'exception est quelconque et
son texte porte les chemins absolus du poste. » Ce test l'applique aux deux
sites d'invocation OpenCode.
"""

from __future__ import annotations

import subprocess

import pytest

from app.services import openhub_agents


_PROMPT_SECRET = "PROMPT-CONFIDENTIEL-DONNEES-MISSION"
_STDERR_SECRET = "Traceback: C:\\chemin\\absolu\\du\\poste\\secret.py"
_OSERROR_SECRET = "FileNotFoundError C:\\chemin\\absolu\\opencode.exe"


def _fuite(texte: str) -> list[str]:
    return [
        marqueur
        for marqueur in (_PROMPT_SECRET, _STDERR_SECRET, _OSERROR_SECRET)
        if marqueur in texte
    ]


@pytest.fixture()
def _which(monkeypatch):
    monkeypatch.setattr(
        openhub_agents.shutil,
        "which",
        lambda _nom: "C:\\chemin\\absolu\\du\\poste\\opencode.exe",
    )


@pytest.mark.parametrize(
    "appel",
    [openhub_agents._run_opencode_agent, openhub_agents._run_opencode_skill],
)
def test_delai_depasse_ne_publie_ni_commande_ni_prompt(monkeypatch, _which, appel):
    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd=["opencode"], timeout=90)

    monkeypatch.setattr(openhub_agents.subprocess, "run", _timeout)
    sortie = appel("un-agent", _PROMPT_SECRET)
    assert _fuite(sortie) == [], sortie
    assert "opencode.exe" not in sortie, sortie


@pytest.mark.parametrize(
    "appel",
    [openhub_agents._run_opencode_agent, openhub_agents._run_opencode_skill],
)
def test_oserror_ne_publie_pas_le_texte_de_l_exception(monkeypatch, _which, appel):
    def _oserror(*_a, **_k):
        raise OSError(_OSERROR_SECRET)

    monkeypatch.setattr(openhub_agents.subprocess, "run", _oserror)
    sortie = appel("un-agent", _PROMPT_SECRET)
    assert _fuite(sortie) == [], sortie


@pytest.mark.parametrize(
    "appel",
    [openhub_agents._run_opencode_agent, openhub_agents._run_opencode_skill],
)
def test_code_retour_non_nul_ne_publie_pas_stderr(monkeypatch, _which, appel):
    def _echec(*_a, **_k):
        return subprocess.CompletedProcess(
            args=["opencode"], returncode=2, stdout="", stderr=_STDERR_SECRET
        )

    monkeypatch.setattr(openhub_agents.subprocess, "run", _echec)
    sortie = appel("un-agent", _PROMPT_SECRET)
    assert _fuite(sortie) == [], sortie


@pytest.mark.parametrize(
    "appel",
    [openhub_agents._run_opencode_agent, openhub_agents._run_opencode_skill],
)
def test_le_detail_reste_dans_le_journal_serveur(monkeypatch, caplog, _which, appel):
    def _echec(*_a, **_k):
        return subprocess.CompletedProcess(
            args=["opencode"], returncode=2, stdout="", stderr=_STDERR_SECRET
        )

    monkeypatch.setattr(openhub_agents.subprocess, "run", _echec)
    with caplog.at_level("WARNING", logger="app.services.openhub_agents"):
        appel("un-agent", _PROMPT_SECRET)
    assert _STDERR_SECRET in caplog.text
