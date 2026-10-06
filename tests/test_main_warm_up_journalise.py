"""Un préchauffage qui échoue au démarrage reste NON fatal mais n'est plus
silencieux (audit robustesse 2026-10-06)."""
from __future__ import annotations

import asyncio
import logging

import pytest


def test_echec_prechauffage_journalise_sans_bloquer(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    from app import main

    def casse(*_a, **_k):
        raise RuntimeError("boum-prechauffage")

    monkeypatch.setattr(main.audio_transcribe, "warm_up", casse)
    monkeypatch.setattr(main, "warm_up_ollama", casse)

    async def demarre() -> None:
        async with main.lifespan(main.app):
            pass

    with caplog.at_level(logging.ERROR):
        asyncio.run(demarre())
    textes = " ".join(r.getMessage() for r in caplog.records)
    assert "Whisper" in textes and "Ollama" in textes
    assert sum(1 for r in caplog.records if r.exc_info) >= 2
