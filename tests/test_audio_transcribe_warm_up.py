"""`WHISPER_WARM_UP=0` désactive le chargement du modèle au démarrage du
serveur (2026-09-08) : le serveur des tests navigateur n'enregistre rien, et
charger `medium` (≈1,5 Go, téléchargé s'il manque) à chaque démarrage de test
aurait coûté le parcours en CI. Sans la variable, le comportement d'avant
reste : le modèle se charge dès que la transcription est disponible.

Échec sur le code d'avant : `warm_up()` ignorait la variable et chargeait.
"""
from __future__ import annotations

import pytest

from app.services import audio_transcribe


@pytest.fixture
def modele_espion(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    appels: list[str] = []
    monkeypatch.setattr(audio_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(audio_transcribe, "_get_model", lambda: appels.append("charge"))
    return appels


def test_le_warm_up_charge_le_modele_par_defaut(
    monkeypatch: pytest.MonkeyPatch, modele_espion: list[str],
) -> None:
    monkeypatch.delenv("WHISPER_WARM_UP", raising=False)
    audio_transcribe.warm_up()
    assert modele_espion == ["charge"]


def test_whisper_warm_up_0_ne_charge_rien(
    monkeypatch: pytest.MonkeyPatch, modele_espion: list[str],
) -> None:
    monkeypatch.setenv("WHISPER_WARM_UP", "0")
    audio_transcribe.warm_up()
    assert modele_espion == []


def test_toute_autre_valeur_garde_le_warm_up(
    monkeypatch: pytest.MonkeyPatch, modele_espion: list[str],
) -> None:
    """Seul `0` désactive : `false`, `non` ou une faute de frappe ne doivent
    pas priver l'utilisateur du warm-up en silence."""
    monkeypatch.setenv("WHISPER_WARM_UP", "false")
    audio_transcribe.warm_up()
    assert modele_espion == ["charge"]
