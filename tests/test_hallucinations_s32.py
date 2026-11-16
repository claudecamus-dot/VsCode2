"""Story 3.2 — AC3.2.4 (C-4) : le silence ne produit pas de faux texte.

Filtre `_texte_segments` : segments à forte probabilité de non-parole ET faible
confiance écartés (seuils de faster-whisper), phrases types de silence écartées
quand isolées ou peu sûres, répétitions exactes repliées ; un bloc qui n'en
contient que rend un texte vide (= « aucune parole »). Jamais le vrai modèle.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.services import audio_transcribe as at


class _Seg:
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=-0.2):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


def test_ac324_segment_non_parole_et_peu_sur_ecarte():
    segs = [_Seg(" Bonjour à tous."), _Seg(" Sous le silence.", no_speech_prob=0.9, avg_logprob=-1.4)]
    assert at._texte_segments(segs) == "Bonjour à tous."


def test_ac324_segment_non_parole_mais_sur_garde():
    # Forte proba de non-parole mais logprob élevé : faster-whisper le garde aussi.
    segs = [_Seg(" Oui.", no_speech_prob=0.8, avg_logprob=-0.3)]
    assert at._texte_segments(segs) == "Oui."


@pytest.mark.parametrize("phrase", [
    " Merci d'avoir regardé cette vidéo !",
    " Sous-titres réalisés par la communauté d'Amara.org",
    " Thank you for watching.",
    " Sous-titrage ST' 501",
])
def test_ac324_phrase_type_isolee_peu_sure_ecartee(phrase):
    assert at._texte_segments([_Seg(phrase, no_speech_prob=0.45, avg_logprob=-0.5)]) == ""


def test_ac324_phrase_type_isolee_sure_gardee():
    # Une vraie « Merci de votre attention » prononcée avec assurance survit.
    assert at._texte_segments([_Seg(" Merci de votre attention.")]) == "Merci de votre attention."


def test_ac324_phrase_type_comparee_entiere_pas_par_prefixe():
    segs = [_Seg(" Merci de votre attention, on passe à la suite.",
                 no_speech_prob=0.45, avg_logprob=-0.8)]
    assert at._texte_segments(segs) == "Merci de votre attention, on passe à la suite."


def test_ac324_phrase_type_repetee_ecartee_bloc_vide():
    segs = [_Seg(" Merci d'avoir regardé cette vidéo !") for _ in range(7)]
    assert at._texte_segments(segs) == ""


def test_ac324_phrase_type_au_milieu_d_une_parole_sure_gardee():
    segs = [_Seg(" On a commencé le projet en mars."),
            _Seg(" Merci d'avoir regardé cette vidéo.", avg_logprob=-0.1),
            _Seg(" Ensuite on a recruté.")]
    assert "Merci d'avoir regardé" in at._texte_segments(segs)


def test_ac324_phrase_type_peu_sure_au_milieu_ecartee():
    segs = [_Seg(" On a commencé le projet en mars."),
            _Seg(" Merci d'avoir regardé cette vidéo.", no_speech_prob=0.4, avg_logprob=-0.9),
            _Seg(" Ensuite on a recruté.")]
    assert at._texte_segments(segs) == "On a commencé le projet en mars. Ensuite on a recruté."


def test_ac324_repetitions_exactes_repliees():
    segs = [_Seg(" C'est important."), _Seg(" C'est important."), _Seg(" c'est important !"),
            _Seg(" Autre chose.")]
    assert at._texte_segments(segs) == "C'est important. Autre chose."


def test_ac324_deux_oui_de_deux_interlocuteurs_survivent():
    segs = [_Seg(" Oui."), _Seg(" Oui."), _Seg(" Alors on commence.")]
    assert at._texte_segments(segs) == "Oui. Oui. Alors on commence."


def test_ac324_segments_sans_attributs_de_confiance_toleres():
    class _Nu:
        def __init__(self, text):
            self.text = text
    assert at._texte_segments([_Nu(" a "), _Nu("b")]) == "a b"


# --------------------------------------------------------------------------- #
# Les trois sites d'appel utilisent le filtre et condition_on_previous_text=False
# --------------------------------------------------------------------------- #
class _ModeleHallucine:
    def __init__(self):
        self.kwargs = []

    def transcribe(self, audio, **kwargs):
        self.kwargs.append(kwargs)
        return [_Seg(" Merci d'avoir regardé cette vidéo !") for _ in range(3)], None


def test_ac324_bloc_de_fond_d_hallucinations_compte_comme_vide(monkeypatch):
    modele = _ModeleHallucine()
    monkeypatch.setattr(at, "_get_model", lambda: modele)
    monkeypatch.setattr(at, "_MODEL_LOCK", type(at._MODEL_LOCK)())
    assert at._transcribe_pcm_sequential(np.zeros(160, dtype=np.float32)) == ""
    assert modele.kwargs[0]["condition_on_previous_text"] is False


def test_ac324_worker_filtre_aussi(monkeypatch):
    modele = _ModeleHallucine()
    monkeypatch.setattr(at, "_modele_worker", lambda threads: modele)
    assert at._transcribe_pcm_chunk((np.zeros(160, dtype=np.float32), 1)) == ""
    assert modele.kwargs[0]["condition_on_previous_text"] is False


def test_ac324_direct_d_hallucinations_rend_no_speech(monkeypatch):
    modele = _ModeleHallucine()
    monkeypatch.setattr(at, "_get_model", lambda: modele)
    monkeypatch.setattr(at, "_MODEL_LOCK", type(at._MODEL_LOCK)())
    with pytest.raises(at.NoSpeechError):
        at._transcribe_audio_live(b"x", 10.0)
    assert modele.kwargs[0]["condition_on_previous_text"] is False
