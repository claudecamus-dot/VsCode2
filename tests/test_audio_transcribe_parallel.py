"""Tests de la transcription parallèle d'un fichier long (US9.18,
2026-07-16) : au-delà de `PARALLEL_THRESHOLD_S`, `audio_transcribe.
transcribe_audio()` découpe l'audio en tronçons transcrits en parallèle sur
plusieurs cœurs CPU plutôt qu'un seul appel séquentiel — voir le cadrage
`_bmad-output/cadrage-transcription-perf.md` (RTF séquentiel ~0,84-0,88 sur
du contenu réel, ramené à ~0,45-0,49 en parallèle sur ce type de matériel).

Complète `test_audio_transcribe_edge_cases.py` (chemin séquentiel/erreurs)
et `test_audio_transcribe_tts_smoke.py` (pipeline réel, clip court).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services import audio_transcribe

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "audio" / "tts_anglais_synthetique.webm"


# --------------------------------------------------------------------------- #
# _probe_duration_s — sonde de durée sans décodage complet.
# --------------------------------------------------------------------------- #
def test_probe_duration_returns_none_for_invalid_content() -> None:
    assert audio_transcribe._probe_duration_s(b"contenu factice non audio") is None


@pytest.mark.skipif(not audio_transcribe.is_available(), reason="faster-whisper non installé")
def test_probe_duration_returns_plausible_value_for_real_audio() -> None:
    audio_bytes = FIXTURE_PATH.read_bytes()
    duration = audio_transcribe._probe_duration_s(audio_bytes)
    assert duration is not None
    # Le clip TTS fait quelques secondes — large marge pour ne pas coupler
    # le test à une durée exacte.
    assert 1.0 < duration < 30.0


# --------------------------------------------------------------------------- #
# Dispatch séquentiel vs parallèle selon la durée (mock du modèle et de
# _transcribe_parallel — vérifie l'aiguillage, pas le calcul réel).
# --------------------------------------------------------------------------- #
def test_transcribe_audio_uses_sequential_path_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSegment:
        def __init__(self, text: str) -> None:
            self.text = text

    fake_model = MagicMock()
    fake_model.transcribe.side_effect = lambda audio, **kw: ([FakeSegment("bonjour")], MagicMock())

    monkeypatch.setattr(audio_transcribe, "_faster_whisper", lambda: MagicMock())
    monkeypatch.setattr(audio_transcribe, "_get_model", lambda: fake_model)
    monkeypatch.setattr(audio_transcribe, "_probe_duration_s", lambda content: 5.0)

    called = {}
    monkeypatch.setattr(
        audio_transcribe, "_transcribe_parallel",
        lambda *a, **k: called.setdefault("parallel", True),
    )

    text = audio_transcribe.transcribe_audio(b"contenu factice")

    assert text == "bonjour"
    assert "parallel" not in called
    fake_model.transcribe.assert_called_once()


def test_transcribe_audio_dispatches_to_parallel_above_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audio_transcribe, "_faster_whisper", lambda: MagicMock())
    monkeypatch.setattr(audio_transcribe, "_probe_duration_s", lambda content: 200.0)

    captured = {}

    def fake_parallel(content, duration_s):
        captured["duration_s"] = duration_s
        return "texte transcrit en parallele"

    monkeypatch.setattr(audio_transcribe, "_transcribe_parallel", fake_parallel)
    # Si le chemin séquentiel était pris par erreur, ceci lèverait une
    # exception (mock sans .transcribe configuré) et ferait échouer le test.
    monkeypatch.setattr(audio_transcribe, "_get_model", lambda: (_ for _ in ()).throw(
        AssertionError("le chemin séquentiel n'aurait pas dû être utilisé")
    ))

    text = audio_transcribe.transcribe_audio(b"contenu factice long")

    assert text == "texte transcrit en parallele"
    assert captured["duration_s"] == 200.0


# --------------------------------------------------------------------------- #
# Bout en bout réel (pas de mock) : seuil et nombre de workers abaissés pour
# rester rapide en test, mais le vrai code de découpage + ProcessPoolExecutor
# + modèle réel tourne, sur le clip TTS existant (vraie parole, quelques
# secondes) — preuve que le chemin parallèle fonctionne réellement, pas
# seulement que l'aiguillage est correct.
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not audio_transcribe.is_available(), reason="faster-whisper non installé")
def test_transcribe_audio_parallel_path_real_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio_transcribe, "PARALLEL_THRESHOLD_S", 1)
    monkeypatch.setattr(audio_transcribe, "MAX_PARALLEL_WORKERS", 2)

    audio_bytes = FIXTURE_PATH.read_bytes()
    text = audio_transcribe.transcribe_audio(audio_bytes)

    assert isinstance(text, str)
    assert text.strip() != ""
