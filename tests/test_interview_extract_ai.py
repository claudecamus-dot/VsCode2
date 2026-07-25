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
# Robustesse aux sorties réelles d'un modèle local (2026-07-25) — constats
# d'un passage RÉEL qwen2.5:3b pendant la vérification de la répartition au
# fil de l'eau : les mocks ne renvoient que des types propres, ces deux
# défauts vidaient TOUTE l'extraction en silence.
# --------------------------------------------------------------------------- #
def test_extract_answers_coerces_string_question_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bug réel : qwen2.5:3b renvoie question_id en CHAÎNE ("8") — l'ancien
    filtre `qid not in valid_ids` (set d'ints) droppait alors toutes les
    réponses, d'où un onglet Répartition vide malgré un job `done`."""
    questions = [_question(8, "Comment est organisée l'équipe ?")]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {"answers": [{"question_id": "8", "text": "Deux squads de quatre"}]},
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "document")
    assert result[8]["text"] == "Deux squads de quatre"


def test_extract_answers_label_echo_falls_back_to_verbatims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug réel : le modèle recopie le libellé de la question dans `text` et
    met la vraie matière dans `verbatims` — plutôt que de livrer la question
    en guise de réponse, on replie le texte sur les verbatims."""
    questions = [_question(9, "Quels outils utilisez-vous ?")]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {"answers": [{
            "question_id": 9,
            "text": "Quels outils utilisez-vous ?",
            "verbatims": ["on utilise Jira", "GitLab pour la CI"],
        }]},
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "document")
    assert result[9]["text"] == "on utilise Jira GitLab pour la CI"
    # Les verbatims sont devenus LA réponse : vidés, sinon le même contenu
    # apparaît deux fois (réponse + citation) jusque dans les exports.
    assert result[9]["verbatims"] == []


def test_extract_answers_label_echo_without_verbatims_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Écho du libellé SANS verbatims : rien d'exploitable — la question est
    écartée (pas de réponse inventée), comme une question non abordée."""
    questions = [_question(9, "Quels outils utilisez-vous ?")]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {"answers": [{"question_id": 9, "text": "Quels outils utilisez-vous ?"}]},
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "document")
    assert result == {}


def test_extract_answers_flattens_list_text_and_non_string_verbatims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aplatir, jamais dropper : un `text` liste devient une chaîne jointe ;
    un verbatim non-chaîne est converti sans faire planter l'extraction."""
    questions = [_question(1, "Q ?")]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {"answers": [{
            "question_id": 1,
            "text": ["Fragment un.", "Fragment deux."],
            "verbatims": [42, "  vraie citation  "],
        }]},
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "document")
    assert result[1]["text"] == "Fragment un. Fragment deux."
    assert result[1]["verbatims"] == ["42", "vraie citation"]


def test_extract_answers_question_id_coercion_edge_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revue adversariale 2026-07-25 : "8.0"/8.0 sont des ids valides (8) ;
    un booléen (int(True) == 1 !) et un float non entier (8.9, arrondi vers
    la MAUVAISE question) sont rejetés plutôt que mal attribués."""
    questions = [_question(1, "Q1 ?"), _question(8, "Q8 ?")]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {"answers": [
            {"question_id": "8.0", "text": "Attribuée à la question 8"},
            {"question_id": True, "text": "Jamais attribuée à la question 1"},
            {"question_id": 8.9, "text": "Jamais arrondie vers la question 8"},
        ]},
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "document")
    assert result == {8: {"text": "Attribuée à la question 8", "verbatims": []}}


def test_extract_answers_echo_everywhere_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modèle qui recopie le libellé PARTOUT (texte ET verbatims) : le repli
    est re-testé — rien d'exploitable, la question est écartée plutôt que de
    livrer le libellé en guise de réponse."""
    questions = [_question(9, "Quels outils utilisez-vous ?")]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {"answers": [{
            "question_id": 9,
            "text": "Quels outils utilisez-vous ?",
            "verbatims": ["Quels outils utilisez-vous ?"],
        }]},
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "document")
    assert result == {}


def test_extract_answers_flattens_dict_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un `text` dict (déjà vécu côté SWOT : {"poids": …}) est aplati en ses
    valeurs feuilles plutôt que d'afficher un repr Python."""
    questions = [_question(1, "Q ?")]
    monkeypatch.setattr(
        interview_extract_ai, "call_ai_json",
        lambda *a, **k: {"answers": [{
            "question_id": 1,
            "text": {"reponse": "Le contenu utile", "note": "complément"},
        }]},
    )
    result = interview_extract_ai.extract_answers_from_text(questions, "document")
    assert result[1]["text"] == "Le contenu utile complément"


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
    # Deux paragraphes courts, chacun SOUS la limite de 5 mots (mais leur somme
    # la dépasse) -> 2 tronçons, un par paragraphe. On teste ici le regroupement
    # par paragraphe, pas le redécoupage d'un paragraphe géant (couvert
    # séparément dans test_ai_common.py depuis le correctif du 2026-07-20).
    text = (
        "Paragraphe un ici.\n\n"
        "Paragraphe deux ici."
    )

    result = interview_extract_ai.extract_answers_from_text(questions, text)

    assert len(prompts) == 2  # bien découpé en 2 tronçons distincts
    assert result[1]["text"] == "Réponse du premier tronçon"  # 1er tronçon gagne
    assert result[2]["text"] == "Réponse du second tronçon"  # absente du 1er, reprise du 2e
