"""Shared isolation for tests that touch the parked ("warm") Whisper worker pool.

Imported (not auto-discovered) by the test modules that need it:

    from tests.conftest_pool_chaud import pool_chaud_isole  # noqa: F401

- Closes any parked pool before AND after each test, so a test that fails after
  parking never leaks a pool (or live `spawn` children) into the next one.
- Guard: spawned workers inherit an env pointing at a model name that does not
  exist, with the Hugging Face hub offline. A test that forgets to prepend a fake
  `faster_whisper` therefore fails fast instead of loading the real model.
"""
from __future__ import annotations

import pytest

from app.services import audio_transcribe as at

MODELE_INTERDIT = "modele-interdit-en-test"


@pytest.fixture(autouse=True)
def pool_chaud_isole(monkeypatch):
    at.fermer_pool_chaud()
    monkeypatch.setenv("WHISPER_MODEL", MODELE_INTERDIT)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    yield
    at.fermer_pool_chaud()
