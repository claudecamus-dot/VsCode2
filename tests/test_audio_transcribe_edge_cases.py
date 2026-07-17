"""Cas d'erreur/limites de `audio_transcribe.transcribe_audio()`, en
complément de `test_mission_trame_flow.py` qui ne couvre que le cas
« silence pur » (clip synthétique webm/opus généré via PyAV, imitant un
blob MediaRecorder navigateur). Ce module couvre : silence très court,
bruit blanc de faible amplitude, ton pur non-vocal, fichier corrompu,
contenu vide, ainsi qu'une régression unitaire mockée sur le paramètre
`beam_size` (bug corrigé le 2026-07-16 : `BEAM_SIZE` était défini mais
jamais transmis à `model.transcribe(...)`).

Les fixtures binaires sous `tests/fixtures/audio/synthetiques/` sont 100%
synthétiques (bruit/ton généré par numpy, silence, ou troncature d'un clip
lui-même synthétique) — aucune donnée réelle, aucun contenu privé.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import audio_transcribe

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "audio" / "synthetiques"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _read_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


# --------------------------------------------------------------------------- #
# Niveau unitaire : audio_transcribe.transcribe_audio(content) directement.
# --------------------------------------------------------------------------- #

pytestmark_skip_no_whisper = pytest.mark.skipif(
    not audio_transcribe.is_available(), reason="faster-whisper non installé"
)


@pytestmark_skip_no_whisper
@pytest.mark.parametrize(
    "fixture_name",
    [
        "silence_tres_court.webm",
        "bruit_faible_amplitude.webm",
        "ton_sinusoidal.webm",
    ],
)
def test_transcribe_audio_non_vocal_signal_no_crash(fixture_name: str) -> None:
    """Silence très court, bruit blanc faible amplitude, ton pur 440Hz : ce
    sont trois signaux non-vocaux mais de nature différente (absence de
    signal, énergie sans structure, énergie tonale sans structure vocale).
    Aucun des trois ne doit faire planter le pipeline. Deux issues sont
    légitimes selon le comportement du VAD/décodage de Whisper sur ce type
    d'entrée (déjà observé et documenté ainsi sur le cas silence dans
    `test_mission_trame_flow.py`) :
      - `TranscriptionError("Aucune parole détectée...")` si le VAD rejette
        le segment (comportement observé pour les trois fixtures actuelles
        en environnement de test) ;
      - un texte non vide, si un modèle/VAD moins strict décidait d'y voir
        de la « parole » (hallucination) — on ne veut pas figer ce
        comportement en échec de test, seulement vérifier qu'il n'y a pas
        de crash Python brut."""
    content = _read_fixture(fixture_name)
    try:
        text = audio_transcribe.transcribe_audio(content)
        assert isinstance(text, str)
    except audio_transcribe.TranscriptionError as exc:
        assert str(exc)  # message non vide, destiné à l'UI


def test_transcribe_audio_contenu_vide_leve_erreur_claire() -> None:
    """`b""` doit lever TranscriptionError immédiatement, sans même essayer
    de charger le modèle (garde précoce en tête de fonction)."""
    with pytest.raises(audio_transcribe.TranscriptionError) as exc_info:
        audio_transcribe.transcribe_audio(b"")
    assert str(exc_info.value).strip() != ""


@pytestmark_skip_no_whisper
def test_transcribe_audio_fichier_corrompu_leve_transcription_error() -> None:
    """Un webm tronqué (200 premiers octets d'un clip par ailleurs valide)
    doit remonter une TranscriptionError propre — jamais un crash brut
    (IOError/RuntimeError non catché) qui remonterait tel quel jusqu'à
    l'appelant, cf. le garde-fou générique `except Exception` déjà présent
    dans `transcribe_audio()`."""
    content = _read_fixture("corrompu.webm")
    with pytest.raises(audio_transcribe.TranscriptionError) as exc_info:
        audio_transcribe.transcribe_audio(content)
    assert str(exc_info.value).strip() != ""


# --------------------------------------------------------------------------- #
# Niveau HTTP réel : /audio/transcribe-segment, sans mock, avec de vrais
# fichiers -- la classe de bug qui a motivé les tests réels existants était
# précisément une exception non catchée par la route (500 brute /
# {"detail": ...}) au lieu du contrat JSON {"error": ...} attendu par le JS
# de record.html.
# --------------------------------------------------------------------------- #


@pytestmark_skip_no_whisper
def test_audio_transcribe_segment_bruit_faible_amplitude_reponse_bien_formee(
    client: TestClient,
) -> None:
    content = _read_fixture("bruit_faible_amplitude.webm")
    response = client.post(
        "/audio/transcribe-segment",
        files={"file": ("segment.webm", content, "audio/webm")},
    )
    assert response.status_code in (200, 422), response.text
    body = response.json()
    if response.status_code == 200:
        assert "text" in body
    else:
        assert "error" in body, response.text


@pytestmark_skip_no_whisper
def test_audio_transcribe_segment_fichier_corrompu_reponse_bien_formee(
    client: TestClient,
) -> None:
    content = _read_fixture("corrompu.webm")
    response = client.post(
        "/audio/transcribe-segment",
        files={"file": ("segment.webm", content, "audio/webm")},
    )
    # Le fichier corrompu doit produire une TranscriptionError (422,
    # {"error": ...}) -- pas une 500 brute avec un corps mal formé. On
    # tolère aussi un 500 avec corps JSON {"error": ...} (garde-fou générique
    # de la route pour une exception vraiment inattendue), mais jamais un
    # crash qui empêcherait même de parser la réponse en JSON.
    assert response.status_code in (200, 422, 500), response.text
    body = response.json()
    if response.status_code == 200:
        assert "text" in body
    else:
        assert "error" in body, response.text
    # Le cas nominal attendu pour ce fichier tronqué est bien une erreur
    # fonctionnelle propre, pas un 500.
    assert response.status_code == 422, response.text


# --------------------------------------------------------------------------- #
# Régression : BEAM_SIZE défini mais non transmis à model.transcribe(...)
# (bug corrigé le 2026-07-16). Mock complet du modèle -- tourne même sans
# faster-whisper installé, aucun poids chargé.
# --------------------------------------------------------------------------- #


def test_transcribe_audio_transmet_bien_beam_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """`model.transcribe(...)` doit recevoir `beam_size=audio_transcribe.BEAM_SIZE`
    -- pas la valeur par défaut de faster-whisper, pas une valeur codée en
    dur ailleurs. Avant le correctif du 2026-07-15/16, `BEAM_SIZE` était lu
    depuis `WHISPER_BEAM_SIZE` mais jamais passé à l'appel réel, donc le
    réglage qualité (medium + beam_size=2) n'avait aucun effet."""

    class FakeSegment:
        def __init__(self, text: str) -> None:
            self.text = text

    def fake_transcribe(audio, **kwargs):
        return [FakeSegment(" bonjour ")], MagicMock()

    fake_model = MagicMock()
    fake_model.transcribe.side_effect = fake_transcribe

    monkeypatch.setattr(audio_transcribe, "_faster_whisper", lambda: MagicMock())
    monkeypatch.setattr(audio_transcribe, "_get_model", lambda: fake_model)

    text = audio_transcribe.transcribe_audio(b"contenu factice non vide")

    assert text == "bonjour"
    fake_model.transcribe.assert_called_once()
    _, kwargs = fake_model.transcribe.call_args
    assert kwargs.get("beam_size") == audio_transcribe.BEAM_SIZE
    assert kwargs.get("language") == "fr"
    assert kwargs.get("vad_filter") is True
