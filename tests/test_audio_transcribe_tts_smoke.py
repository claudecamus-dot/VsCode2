"""Check sanity : exerce le pipeline de transcription (`audio_transcribe`) sur
un clip contenant de la VRAIE énergie acoustique (voix TTS synthétique), par
opposition au clip de silence pur déjà testé ailleurs (`_synthetic_webm_audio`
dans `test_mission_trame_flow.py`), qui ne fait qu'exercer le rejet VAD.

Le clip `tests/fixtures/audio/tts_anglais_synthetique.webm` a été généré via :
1. `System.Speech.Synthesis.SpeechSynthesizer` (voix `Microsoft Zira Desktop`,
   anglais — aucune voix française n'est installée sur cette machine) prononçant
   "Hello, this is a test recording for the transcription pipeline." dans un
   `.wav` via `SetOutputToWaveFile()`.
2. Ré-encodage en webm/opus 48kHz mono via PyAV (même pattern que
   `_synthetic_webm_audio()`), pour matcher le format réel d'un blob
   MediaRecorder navigateur.

Le pipeline force `language="fr"` : on n'attend PAS une transcription anglaise
correcte (l'anglais forcé en français peut donner n'importe quoi de lisible ou
non) — on vérifie seulement qu'il y a bien de la matière transcrite (texte non
vide), contrairement au silence pur qui produit "Aucune parole détectée".
Aucune donnée réelle/privée utilisée : uniquement de la synthèse TTS générée
pour ce test.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import audio_transcribe

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "audio" / "tts_anglais_synthetique.webm"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.skipif(
    not audio_transcribe.is_available(), reason="faster-whisper non installé"
)
def test_transcribe_audio_tts_clip_returns_non_empty_text() -> None:
    """Vérifie que le clip TTS (parole réelle, énergie acoustique riche) produit
    bien un texte transcrit non vide, sans lever d'exception — contrairement au
    clip de silence pur qui est légitimement rejeté par le VAD."""
    audio_bytes = FIXTURE_PATH.read_bytes()
    assert len(audio_bytes) > 0

    text = audio_transcribe.transcribe_audio(audio_bytes)

    assert isinstance(text, str)
    assert text.strip() != ""


@pytest.mark.skipif(
    not audio_transcribe.is_available(), reason="faster-whisper non installé"
)
def test_transcribe_segment_http_real_pipeline_with_tts_clip(client: TestClient) -> None:
    """Équivalent HTTP réel (pas de mock) de la route sans état
    `/audio/transcribe-segment`, avec le même clip TTS porteur de vraie parole."""
    audio_bytes = FIXTURE_PATH.read_bytes()

    response = client.post(
        "/audio/transcribe-segment",
        files={"file": ("segment.webm", audio_bytes, "audio/webm")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "text" in payload
    assert payload["text"].strip() != ""
