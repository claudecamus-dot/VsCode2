"""Le message d'échec de transcription rendu au CLIENT ne porte jamais le texte
de l'exception système.

Constat d'audit du hub (sécurité, 2026-09-09) : `audio_transcribe.py` était le
dernier module à interpoler `{exc}` dans une `TranscriptionError`, alors que le
reste du dépôt applique scrupuleusement la règle inverse et la commente
(« Message FIXE, jamais `str(exc)` : son texte porte les chemins absolus du
poste »). Ce n'est pas théorique : les routes de transcription rendent cette
exception telle quelle au navigateur — `interviews.py` fait
`return JSONResponse({"error": str(exc)}, status_code=422)`. Un message
d'`av`/`faster-whisper` citant un chemin arrivait donc jusqu'à l'écran.

Ces tests échouent sur le code d'avant : ils cherchent dans le message rendu
une chaîne que l'exception système contenait et qui y était recopiée.
"""
from __future__ import annotations

import logging

import pytest

from app.services import audio_transcribe
from app.services.audio_transcribe import TranscriptionError

# Ce qu'une exception système peut trimballer, et qui n'a rien à faire à
# l'écran : un chemin absolu du poste et un nom de bibliothèque interne.
SECRET = r"C:\Users\claude.camus\AppData\Local\Temp\av_buffer_0x7f.dll"


def test_un_fichier_illisible_ne_renvoie_pas_le_chemin_du_poste(monkeypatch):
    """Chemin `_decode_to_pcm16k` : le décodeur lève, le client reçoit un
    message fixe."""
    def _decodeur_qui_leve(_content):
        raise OSError(f"impossible d'ouvrir {SECRET}")

    monkeypatch.setattr(audio_transcribe, "_decode_to_pcm16k", _decodeur_qui_leve)

    with pytest.raises(TranscriptionError) as capture:
        list(audio_transcribe.iter_transcribe_blocks(b"des octets"))

    message = str(capture.value)
    assert SECRET not in message, f"le chemin du poste est rendu au client : {message}"
    assert "av_buffer" not in message
    assert message == "Fichier audio illisible."


def test_un_echec_de_transcription_ne_renvoie_pas_le_texte_systeme(monkeypatch):
    """Chemin `_transcribe_pcm_sequential` : le moteur lève au milieu, le
    client reçoit le message d'interface, pas le diagnostic."""
    import numpy as np

    # Un PCM court : un seul bloc, donc le chemin `total == 1`.
    monkeypatch.setattr(
        audio_transcribe, "_decode_to_pcm16k",
        lambda _c: np.zeros(16000, dtype=np.int16),
    )

    def _moteur_qui_leve(_bloc):
        raise RuntimeError(f"faster_whisper a plante en chargeant {SECRET}")

    monkeypatch.setattr(
        audio_transcribe, "_transcribe_pcm_sequential", _moteur_qui_leve
    )

    with pytest.raises(TranscriptionError) as capture:
        list(audio_transcribe.iter_transcribe_blocks(b"des octets"))

    message = str(capture.value)
    assert SECRET not in message, f"le chemin du poste est rendu au client : {message}"
    assert "faster_whisper" not in message
    assert message == audio_transcribe.ECHEC_TRANSCRIPTION


def test_le_detail_reste_dans_le_journal_serveur(monkeypatch, caplog):
    """La contrepartie, sans quoi le correctif serait une perte : le diagnostic
    n'est pas supprimé, il est DÉPLACÉ au journal serveur — sinon on troque une
    fuite contre une panne indiagnosticable."""
    def _decodeur_qui_leve(_content):
        raise OSError(f"impossible d'ouvrir {SECRET}")

    monkeypatch.setattr(audio_transcribe, "_decode_to_pcm16k", _decodeur_qui_leve)

    with caplog.at_level(logging.ERROR, logger=audio_transcribe.__name__):
        with pytest.raises(TranscriptionError):
            list(audio_transcribe.iter_transcribe_blocks(b"des octets"))

    trace = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert SECRET in trace, (
        "le detail a disparu du journal serveur : la panne devient "
        "indiagnosticable, ce n'est pas le but du correctif"
    )


def test_transcribe_audio_ne_renvoie_pas_non_plus_le_texte_systeme(monkeypatch):
    """`transcribe_audio` est un site DISTINCT de `iter_transcribe_blocks`, et
    c'est celui que deux routes rendent au navigateur (`interviews.py`, appels
    de transcription d'un enregistrement). Les tests ci-dessus ne l'exercent
    pas : restaurer l'interpolation sur cette seule ligne les laisserait tous
    verts (revue adversariale du 2026-09-10, finding 10). Le test le plus
    proche qui existait, `test_audio_transcribe_edge_cases`, n'assertait que
    `str(exc).strip() != ""` — vrai aussi avec l'ancien message."""
    import numpy as np

    monkeypatch.setattr(
        audio_transcribe, "_decode_to_pcm16k",
        lambda _c: np.zeros(16000, dtype=np.int16),
    )

    def _moteur_qui_leve(*_a, **_k):
        raise RuntimeError(f"le modele n'a pas pu charger {SECRET}")

    # Le modèle est résolu paresseusement : on fait lever son chargement.
    monkeypatch.setattr(audio_transcribe, "_get_model", _moteur_qui_leve, raising=False)
    monkeypatch.setattr(
        audio_transcribe, "_transcribe_pcm_sequential", _moteur_qui_leve, raising=False
    )

    with pytest.raises(TranscriptionError) as capture:
        audio_transcribe.transcribe_audio(b"des octets")

    message = str(capture.value)
    assert SECRET not in message, f"le chemin du poste est rendu au client : {message}"
    assert message == audio_transcribe.ECHEC_TRANSCRIPTION
