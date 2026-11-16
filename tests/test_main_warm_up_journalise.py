"""Un préchauffage qui échoue au démarrage reste NON fatal mais n'est plus
silencieux (audit robustesse 2026-10-06)."""
from __future__ import annotations

import asyncio
import logging
import threading

import pytest


def test_echec_prechauffage_journalise_sans_bloquer(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    from app import main

    def casse(*_a, **_k):
        raise RuntimeError("boum-prechauffage")

    # conftest désactive les préchauffages par défaut et `app.main` les saute
    # alors avant d'appeler `warm_up()` : les réactiver pour que `casse` tourne.
    monkeypatch.setenv("WHISPER_WARM_UP", "1")
    monkeypatch.setenv("OLLAMA_WARM_UP", "1")
    monkeypatch.setattr(main.audio_transcribe, "warm_up", casse)
    monkeypatch.setattr(main, "warm_up_ollama", casse)

    async def demarre() -> None:
        async with main.lifespan(main.app):
            pass

    with caplog.at_level(logging.ERROR):
        asyncio.run(demarre())
        # les préchauffages sont des fils daemon : les attendre AVANT d'asserter
        # et avant le retrait des monkeypatch (sinon flaky + vrai warm_up)
        for f in threading.enumerate():
            if f.name.startswith("prechauffage-"):
                f.join(10)
    textes = " ".join(r.getMessage() for r in caplog.records)
    assert "Whisper" in textes and "Ollama" in textes
    assert sum(1 for r in caplog.records if r.exc_info) >= 2
