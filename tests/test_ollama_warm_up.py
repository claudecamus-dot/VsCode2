"""`OLLAMA_WARM_UP=0` désactive le préchargement du modèle Ollama au démarrage
du serveur (2026-09-09, revue adversariale A2) — miroir de `WHISPER_WARM_UP`
(`tests/test_audio_transcribe_warm_up.py`). Sans la variable, le comportement
d'avant reste : un POST /api/chat sans messages dès que le fournisseur actif
est ollama.

Échec sur le code d'avant : `warm_up_ollama()` ignorait la variable et
appelait Ollama.
"""
from __future__ import annotations

import contextlib

import pytest

from app.services import ai_common


@pytest.fixture
def appels_ollama(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    appels: list[str] = []

    @contextlib.contextmanager
    def faux_urlopen(req, timeout=None):
        appels.append(req.full_url)
        yield object()

    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setattr(ai_common.urllib.request, "urlopen", faux_urlopen)
    return appels


def test_le_warm_up_ollama_appelle_le_serveur_par_defaut(
    monkeypatch: pytest.MonkeyPatch, appels_ollama: list[str],
) -> None:
    monkeypatch.delenv("OLLAMA_WARM_UP", raising=False)
    ai_common.warm_up_ollama()
    assert len(appels_ollama) == 1 and appels_ollama[0].endswith("/api/chat")


def test_ollama_warm_up_0_n_appelle_rien(
    monkeypatch: pytest.MonkeyPatch, appels_ollama: list[str],
) -> None:
    monkeypatch.setenv("OLLAMA_WARM_UP", "0")
    ai_common.warm_up_ollama()
    assert appels_ollama == []


def test_toute_autre_valeur_garde_le_warm_up_ollama(
    monkeypatch: pytest.MonkeyPatch, appels_ollama: list[str],
) -> None:
    """Seul `0` désactive : `false`, `non` ou une faute de frappe ne doivent
    pas priver l'utilisateur du préchargement en silence."""
    monkeypatch.setenv("OLLAMA_WARM_UP", "off")
    ai_common.warm_up_ollama()
    assert len(appels_ollama) == 1
