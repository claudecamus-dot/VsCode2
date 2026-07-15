"""Transcription locale d'un enregistrement audio (US3.2) via faster-whisper.

Modèle chargé une fois (singleton lazy), sans quoi chaque transcription
rechargerait les poids depuis le disque. Aucun appel réseau pour la
transcription elle-même : la voix des personnes interviewées reste sur la
machine de l'interviewer·euse. Même convention de dégradation gracieuse que
`ai_common.py` (`is_configured()`/`_anthropic()`) : `is_available()` renvoie
False si `faster-whisper` n'est pas installé, et `transcribe_audio()` lève
`TranscriptionError` avec un message destiné à l'UI.

Compromis vitesse/qualité (WHISPER_MODEL/WHISPER_BEAM_SIZE, 2026-07-15) :
la transcription se fait au fil de l'eau par segments d'~1 min pendant
l'enregistrement (voir record.html), donc chaque segment doit rester
sensiblement plus rapide à transcrire qu'à enregistrer. Le réglage d'origine
(model="small", beam_size=1/greedy) privilégiait la vitesse CPU au prix
d'erreurs de transcription notables sur un entretien réel (accents,
vocabulaire métier, recouvrements de parole). Défaut relevé à
model="medium" + beam_size=2 : nette amélioration de qualité, encore
raisonnable au fil de l'eau sur un CPU correct — à ajuster via les variables
d'environnement selon la machine (WHISPER_MODEL=large-v3 pour la meilleure
qualité possible en local, si le CPU suit ; WHISPER_MODEL=small pour
revenir à l'ancien compromis si medium est trop lent).
"""
from __future__ import annotations

import io
import os

from .ai_common import AIError

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "medium")
BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "2"))

_model = None


class TranscriptionError(AIError):
    """Erreur fonctionnelle de transcription — le message est destiné à l'UI."""


def _faster_whisper():
    try:
        import faster_whisper

        return faster_whisper
    except ModuleNotFoundError:
        return None


def is_available() -> bool:
    """Vrai si la transcription locale est possible (paquet installé)."""
    return _faster_whisper() is not None


def _get_model():
    global _model
    if _model is None:
        faster_whisper = _faster_whisper()
        _model = faster_whisper.WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def warm_up() -> None:
    """Charge le modèle en mémoire dès le démarrage du serveur, pour que le
    premier enregistrement réel de l'utilisateur n'en paie pas le coût."""
    if is_available():
        _get_model()


def transcribe_audio(content: bytes) -> str:
    """Retourne le texte transcrit. Lève TranscriptionError."""
    if not content:
        raise TranscriptionError("Aucun enregistrement reçu.")
    faster_whisper = _faster_whisper()
    if faster_whisper is None:
        raise TranscriptionError(
            "faster-whisper n'est pas installé : pip install faster-whisper."
        )

    try:
        model = _get_model()
        # beam_size=1 (greedy) au lieu du défaut 5 : le principal poste de
        # coût CPU pour un gain de précision marginal sur des notes/entretiens.
        # vad_filter saute les silences plutôt que de les faire décoder.
        segments, _info = model.transcribe(
            io.BytesIO(content), language="fr", beam_size=1, vad_filter=True
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
    except TranscriptionError:
        raise
    except Exception as exc:  # garde-fou : ne jamais propager une 500 brute
        raise TranscriptionError(f"Échec de la transcription : {exc}") from exc

    if not text:
        raise TranscriptionError("Aucune parole détectée dans l'enregistrement.")
    return text
