"""Tests de l'incrément « silence capté sans alerte » (2026-07-30, mission 16).

Cas réel déclencheur : un entretien Google Meet enregistré via le micro
physique — qui n'entend pas le son du casque — a produit 90 segments
consécutifs « Aucune parole détectée » (422) sans aucune alerte visible, et
les marqueurs « ⚠ [segment perdu…] » insérés dans la transcription ont été
transformés par l'extraction IA en FAUX tours de parole (« Intervenant :
Aucune parole détectée dans l'enregistrement. » répété 5 fois sur
l'entretien 13 réel).

Trois correctifs testés ici :
1. `/audio/transcribe-segment` distingue `no_speech` (code structuré dans le
   422) d'un échec technique — contrat serveur de la bannière d'alerte.
2. `ai_common.strip_segment_markers` retire les marqueurs de segment avant
   toute extraction IA (libre ET structurée) — régression R1 : avant le
   correctif, les marqueurs atteignaient le prompt du modèle.
3. Les deux écrans d'enregistrement portent la bannière `rec-nospeech-banner`
   et son JS (assertion de présence dans le HTML rendu — même limite assumée
   que le gate `lostSegments` du 2026-07-29 : pas de harnais Node dans ce
   projet pour exécuter le JS de `record*.html`).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, engine, init_db
from app.main import app
from app.services import interview_extract_ai, interview_libre_extract_ai
from app.services.ai_common import strip_segment_markers
from app.services import audio_transcribe


def setup_module() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def teardown_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _mission_id(client: TestClient) -> int:
    response = client.post("/missions", data={"name": "Mission no-speech"}, follow_redirects=False)
    assert response.status_code in (200, 303)
    location = response.headers.get("location", "")
    return int(location.rstrip("/").split("/")[-1])


# --------------------------------------------------------------------------- #
# 1. Contrat serveur : 422 + code no_speech vs 422 technique sans code
# --------------------------------------------------------------------------- #
def test_transcribe_segment_no_speech_porte_le_code_structure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_speech(content: bytes) -> str:
        raise audio_transcribe.NoSpeechError("Aucune parole détectée dans l'enregistrement.")

    monkeypatch.setattr(audio_transcribe, "transcribe_audio", _no_speech)
    response = client.post(
        "/audio/transcribe-segment",
        files={"file": ("segment.webm", b"fake", "audio/webm")},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "no_speech"
    assert "Aucune parole" in body["error"]


def test_transcribe_segment_echec_technique_sans_code(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un échec technique (fichier illisible…) reste un 422 SANS `code` : la
    bannière « vérifiez la source audio » ne doit pas se déclencher sur un
    flux corrompu, dont le remède est différent."""

    def _technique(content: bytes) -> str:
        raise audio_transcribe.TranscriptionError("Fichier audio illisible : bidon")

    monkeypatch.setattr(audio_transcribe, "transcribe_audio", _technique)
    response = client.post(
        "/audio/transcribe-segment",
        files={"file": ("segment.webm", b"fake", "audio/webm")},
    )
    assert response.status_code == 422
    body = response.json()
    assert "code" not in body
    assert "error" in body


def test_no_speech_error_reste_une_transcription_error() -> None:
    """Tous les `except TranscriptionError` existants (import fichier, jobs de
    tranche…) doivent continuer d'attraper le cas no-speech — la sous-classe
    ne doit jamais leur échapper."""
    assert issubclass(audio_transcribe.NoSpeechError, audio_transcribe.TranscriptionError)


# --------------------------------------------------------------------------- #
# 2. strip_segment_markers — unités puis intégration extraction
# --------------------------------------------------------------------------- #
def test_strip_segment_markers_retire_les_deux_formes() -> None:
    text = (
        "Bonjour à tous, on démarre la session.\r\n\r\n"
        "⚠ [segment perdu : Aucune parole détectée dans l'enregistrement.]\r\n\r\n"
        "⚠ [segment 3 non transcrit — relance possible]\r\n\r\n"
        "Merci Feriel pour ce partage."
    )
    cleaned = strip_segment_markers(text)
    assert "segment perdu" not in cleaned
    assert "non transcrit" not in cleaned
    assert "⚠" not in cleaned
    assert "Bonjour à tous, on démarre la session." in cleaned
    assert "Merci Feriel pour ce partage." in cleaned


def test_strip_segment_markers_preserve_les_paragraphes() -> None:
    """Le retrait ne doit pas fusionner tout le texte en un seul paragraphe :
    `chunk_text_by_paragraph` découpe sur les lignes vides, les frontières
    doivent survivre à l'assainissement."""
    text = "Premier paragraphe.\n\n⚠ [segment perdu : silence]\n\nSecond paragraphe."
    cleaned = strip_segment_markers(text)
    assert "Premier paragraphe." in cleaned
    assert "Second paragraphe." in cleaned
    assert "\n\n" in cleaned  # au moins une frontière de paragraphe subsiste


def test_strip_segment_markers_texte_sans_marqueur_inchange() -> None:
    text = "Un texte normal.\r\n\r\nAvec deux paragraphes [et des crochets légitimes]."
    assert strip_segment_markers(text) == text


def test_strip_segment_markers_marqueurs_seuls_donne_vide() -> None:
    text = "⚠ [segment perdu : silence]\n\n⚠ [segment perdu : silence]"
    assert strip_segment_markers(text).strip() == ""


def test_extract_turns_najoute_jamais_les_marqueurs_au_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Régression R1 (cas réel entretien 13, mission 16) : les marqueurs
    « ⚠ [segment perdu…] » passés au modèle devenaient de faux tours
    (« Intervenant : Aucune parole détectée dans l'enregistrement. »). Ce test
    échoue sur le code d'avant : le prompt contenait le texte du marqueur."""
    prompts: list[str] = []

    def fake_call_ai_json(system, prompt, schema, json_hint, **kwargs):
        prompts.append(prompt)
        return {
            "turns": [{"interlocuteur": "Consultant·e", "question": "", "remarque": "Vraie matière."}],
            "identite": {"interviewee_name": "", "interviewee_role": "", "interviewee_entity": ""},
        }

    monkeypatch.setattr(interview_libre_extract_ai, "call_ai_json", fake_call_ai_json)
    result = interview_libre_extract_ai.extract_turns_from_text(
        "Bonjour, on démarre.\r\n\r\n"
        "⚠ [segment perdu : Aucune parole détectée dans l'enregistrement.]\r\n\r\n"
        "⚠ [segment perdu : Aucune parole détectée dans l'enregistrement.]\r\n\r\n"
        "Merci pour ce partage."
    )
    assert prompts, "l'extraction n'a fait aucun appel IA"
    for prompt in prompts:
        assert "segment perdu" not in prompt
        assert "Aucune parole détectée" not in prompt
    assert result["turns"]


def test_extract_turns_transcription_100_pourcent_marqueurs_erreur_explicite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une transcription réduite à des marqueurs n'a AUCUN contenu : erreur
    explicite (sans appel IA) plutôt qu'un entretien vide ou halluciné."""
    monkeypatch.setattr(
        interview_libre_extract_ai, "call_ai_json",
        lambda *a, **k: pytest.fail("aucun appel IA attendu sur un texte sans contenu"),
    )
    with pytest.raises(interview_libre_extract_ai.InterviewLibreExtractAIError) as exc_info:
        interview_libre_extract_ai.extract_turns_from_text(
            "⚠ [segment perdu : Aucune parole détectée dans l'enregistrement.]\r\n\r\n"
            "⚠ [segment perdu : Aucune parole détectée dans l'enregistrement.]"
        )
    assert "marqueurs" in str(exc_info.value)


def test_extract_answers_najoute_jamais_les_marqueurs_au_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Même assainissement côté mode structuré (`extract_answers_from_text`),
    qui reçoit la même transcription au fil de l'eau que le mode libre."""
    prompts: list[str] = []

    def fake_call_ai_json(system, prompt, schema, json_hint, **kwargs):
        prompts.append(prompt)
        return {"answers": [{"question_id": 1, "text": "Réponse.", "verbatims": []}]}

    monkeypatch.setattr(interview_extract_ai, "call_ai_json", fake_call_ai_json)
    questions = [SimpleNamespace(id=1, label="Quelles frictions ?")]
    result = interview_extract_ai.extract_answers_from_text(
        questions,
        "Du vrai contenu de réponse.\r\n\r\n⚠ [segment perdu : silence]",
    )
    assert prompts
    for prompt in prompts:
        assert "segment perdu" not in prompt
    assert result[1]["text"] == "Réponse."


def test_extract_answers_transcription_100_pourcent_marqueurs_erreur_explicite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: pytest.fail("aucun appel IA attendu sur un texte sans contenu"),
    )
    questions = [SimpleNamespace(id=1, label="Quelles frictions ?")]
    with pytest.raises(interview_extract_ai.InterviewExtractAIError) as exc_info:
        interview_extract_ai.extract_answers_from_text(
            questions, "⚠ [segment 2 non transcrit — relance possible]"
        )
    assert "marqueurs" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# 3. Bannière no-speech présente sur les deux écrans d'enregistrement
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path_suffix", ["record", "record-libre"])
def test_ecran_enregistrement_porte_la_banniere_no_speech(
    client: TestClient, path_suffix: str
) -> None:
    """Assertion de présence dans le HTML rendu (bannière + JS qui la pilote) —
    plus faible qu'une exécution réelle du JS, documenté comme tel (pas de
    harnais Node pour `record*.html` dans ce projet, même limite que le gate
    `lostSegments` du 2026-07-29)."""
    mission_id = _mission_id(client)
    response = client.get(f"/missions/{mission_id}/interviews/{path_suffix}")
    assert response.status_code == 200
    html = response.text
    assert 'id="rec-nospeech-banner"' in html
    assert "noteNoSpeech" in html
    assert "resetNoSpeech" in html
    assert "no_speech" in html
