"""Tests directs d'`interview_extract_ai.extract_answers_from_text` — la
répartition des réponses d'un entretien (transcription/notes) par question,
et sa robustesse face à une sortie IA imprécise (question_id halluciné/mal
mappé, réponse vide, doublon) — indépendamment de tout appel réseau réel
(`call_ai_json` est monkeypatché, comme dans `test_ai_common.py`).

Complète les tests d'intégration HTTP existants dans
`test_mission_trame_flow.py` (`.../interviews/import`, `.../record`,
`.../notes/dispatch`), qui mockent `extract_answers_from_text` en entier et
ne vérifient donc jamais ce que fait la fonction elle-même une fois que
`call_ai_json` a répondu."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import interview_extract_ai


def _question(qid: int, label: str = "Question ?") -> SimpleNamespace:
    return SimpleNamespace(id=qid, label=label)


def test_extract_answers_dispatches_by_question_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cas nominal de répartition : chaque réponse IA est rangée sous la
    bonne question, texte et verbatims inclus."""
    questions = [_question(1, "Quelles frictions ?"), _question(2, "Quelles réussites ?")]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {
            "answers": [
                {"question_id": 1, "text": "Beaucoup de silos", "verbatims": ["On travaille en silo"]},
                {"question_id": 2, "text": "Bonne entraide", "verbatims": []},
            ]
        },
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "un document quelconque")
    assert result == {
        1: {"text": "Beaucoup de silos", "verbatims": ["On travaille en silo"]},
        2: {"text": "Bonne entraide", "verbatims": []},
    }


def test_extract_answers_ignores_questions_not_addressed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une question sans réponse dans le document ne doit pas apparaître dans
    le résultat (ni avec un texte vide, ni du tout) — pas d'invention."""
    questions = [_question(1), _question(2)]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {"answers": [{"question_id": 1, "text": "Seule réponse trouvée"}]},
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "document")
    assert list(result) == [1]


def test_extract_answers_drops_hallucinated_question_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """« Question approximative » : si l'IA renvoie un question_id qui ne
    correspond à aucune question réelle de la trame (halluciné, mal recopié),
    la réponse est silencieusement écartée plutôt que de faire planter
    l'extraction ou de créer une réponse orpheline."""
    questions = [_question(1)]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {
            "answers": [
                {"question_id": 1, "text": "Réponse valide"},
                {"question_id": 999, "text": "Réponse rattachée à une question inexistante"},
            ]
        },
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "document")
    assert list(result) == [1]


def test_extract_answers_drops_blank_text(monkeypatch: pytest.MonkeyPatch) -> None:
    questions = [_question(1)]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {"answers": [{"question_id": 1, "text": "   "}]},
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "document")
    assert result == {}


def test_extract_answers_strips_blank_verbatims_and_defaults_missing_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    questions = [_question(1)]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {
            "answers": [{"question_id": 1, "text": "Réponse", "verbatims": ["  Une citation  ", "   ", ""]}]
        },
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "document")
    assert result[1]["verbatims"] == ["Une citation"]


def test_extract_answers_last_duplicate_wins_for_same_question_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si l'IA répond deux fois pour la même question (redite/reformulation
    dans le document), la seconde occurrence l'emporte plutôt que de lever
    une erreur ou de concaténer silencieusement deux réponses partielles."""
    questions = [_question(1)]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {
            "answers": [
                {"question_id": 1, "text": "Première mention"},
                {"question_id": 1, "text": "Seconde mention, plus complète"},
            ]
        },
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "document")
    assert result[1]["text"] == "Seconde mention, plus complète"


def test_extract_answers_raises_on_empty_document(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_call_ai_json(*a, **k):
        nonlocal called
        called = True
        return {"answers": []}

    monkeypatch.setattr(interview_extract_ai, "call_ai_json", fake_call_ai_json)
    with pytest.raises(interview_extract_ai.InterviewExtractAIError):
        interview_extract_ai.extract_answers_from_text([_question(1)], "   ")
    assert not called  # court-circuit avant tout appel IA — pas de coût gaspillé


def test_extract_answers_raises_when_trame_has_no_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_call_ai_json(*a, **k):
        nonlocal called
        called = True
        return {"answers": []}

    monkeypatch.setattr(interview_extract_ai, "call_ai_json", fake_call_ai_json)
    with pytest.raises(interview_extract_ai.InterviewExtractAIError):
        interview_extract_ai.extract_answers_from_text([], "un document avec du contenu")
    assert not called


# --------------------------------------------------------------------------- #
# Map-reduce (2026-07-19) : un entretien enregistré peut durer 1h-1h30, même
# risque de dépassement de contexte/timeout que l'extraction libre — jusqu'ici
# seul chemin du projet sans aucun découpage.
# --------------------------------------------------------------------------- #
def test_extract_answers_short_document_makes_a_single_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chemin court inchangé : un texte qui tient dans un tronçon ne fait
    qu'un seul appel IA, comme avant le map-reduce."""
    calls = []
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: (calls.append(1), {"answers": [{"question_id": 1, "text": "Reponse"}]})[1],
    )
    interview_extract_ai.extract_answers_from_text([_question(1)], "un document quelconque")
    assert len(calls) == 1


def test_extract_answers_map_reduce_splits_long_document_and_merges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un document assez long pour être découpé en plusieurs tronçons : un
    appel IA par tronçon, fusion sans appel IA supplémentaire — la première
    réponse non vide trouvée pour une question l'emporte (une question posée
    une fois n'a pas besoin d'être recomposée depuis plusieurs tronçons,
    contrairement à un résumé qui couvre tout le document)."""
    monkeypatch.setattr(interview_extract_ai, "ollama_chunk_max_words", lambda: 5)
    questions = [_question(1, "Q1 ?"), _question(2, "Q2 ?")]
    prompts = []

    def fake_call_ai_json(system, prompt, schema, json_hint, **kwargs):
        prompts.append(prompt)
        if len(prompts) == 1:
            return {"answers": [{"question_id": 1, "text": "Réponse du premier tronçon"}]}
        return {"answers": [
            {"question_id": 1, "text": "Ne doit pas écraser la première"},
            {"question_id": 2, "text": "Réponse du second tronçon"},
        ]}

    monkeypatch.setattr(interview_extract_ai, "call_ai_json", fake_call_ai_json)
    text = (
        "Paragraphe un avec plusieurs mots ici.\n\n"
        "Paragraphe deux avec plusieurs mots aussi ici."
    )

    result = interview_extract_ai.extract_answers_from_text(questions, text)

    assert len(prompts) == 2  # bien découpé en 2 tronçons distincts
    assert result[1]["text"] == "Réponse du premier tronçon"  # 1er tronçon gagne
    assert result[2]["text"] == "Réponse du second tronçon"  # absente du 1er, reprise du 2e
