"""Extraction IA des réponses d'un entretien depuis un document déjà rédigé
(transcription, notes) — pré-remplissage de `Answer`/`Verbatim` (import
d'entretien).

Même plomberie que `synthese_ai.py`/`trame_extract_ai.py` (`ai_common.py`).
Pas d'heuristique de repli fiable pour un texte libre non structuré : sans IA
configurée, `extract_answers_from_text()` lève `InterviewExtractAIError`.
Fournisseur IA actif = `AI_PROVIDER` (voir `ai_common.py`), ollama par défaut.

Map-reduce (2026-07-19) : un entretien enregistré peut durer 1h-1h30 (source
du texte transmis par `record_interview()`) — même risque de dépassement de
`ollama_num_ctx()`/`ollama_timeout()` sur un texte long qu'ailleurs dans le
projet (`interview_libre_extract_ai.py`, `synthese_ai.py`), jusqu'ici non
traité pour ce chemin précis. Découpage par `ai_common.chunk_text_by_paragraph()`
(même budget `OLLAMA_CHUNK_MAX_WORDS` que le reste) ; fusion sans appel IA
supplémentaire — la première réponse non vide trouvée pour une question
l'emporte (une question posée une fois dans l'entretien n'a pas besoin d'être
recomposée à partir de plusieurs tronçons, contrairement à un résumé/une
répartition qui couvre tout l'entretien).
"""
from __future__ import annotations

from .ai_common import (
    AIError,
    call_ai_json,
    call_par_troncons_degressifs,
    chunk_text_by_paragraph,
    ollama_chunk_max_words,
    strip_segment_markers,
)

MAX_TOKENS = 3000

SYSTEM = (
    "Tu es consultant·e senior. On te donne une liste de questions "
    "d'entretien et un document (transcription, notes) rédigé après un "
    "entretien. Pour chaque question réellement abordée dans le document, "
    "produis une réponse synthétique fidèle aux propos (n'invente rien) et, "
    "si le document contient des citations mot-pour-mot pertinentes, "
    "relève-les comme verbatims. Le champ \"text\" contient ce que la "
    "personne interviewée a RÉPONDU (reformulé fidèlement) — jamais une "
    "recopie du libellé de la question. Ignore les questions non abordées — "
    "ne les fais pas apparaître dans le résultat plutôt que d'inventer une "
    "réponse."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "integer"},
                    "text": {"type": "string"},
                    "verbatims": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question_id", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["answers"],
    "additionalProperties": False,
}

_JSON_HINT = (
    '\nRéponds UNIQUEMENT par un objet JSON à la clé "answers" '
    '(liste de {"question_id", "text", "verbatims": [...]}). '
    '"question_id" est le nombre ENTIER entre crochets devant la question ; '
    '"text" est la réponse de la personne interviewée, jamais la question.'
)


class InterviewExtractAIError(AIError):
    """Erreur fonctionnelle d'extraction IA — le message est destiné à l'UI."""


def _build_prompt(questions, text: str) -> str:
    lines = ["QUESTIONS :"]
    for q in questions:
        lines.append(f"  [{q.id}] {q.label}")
    lines += ["", "DOCUMENT :", text]
    return "\n".join(lines)


def _coerce_text(value) -> str:
    """Aplatit le champ `text` d'une réponse IA en chaîne — jamais dropper
    pour une simple question de type (cf. mémoire « Ollama JSON type
    coercion: flatten don't drop ») : un modèle local renvoie parfois une
    liste de fragments, voire un dict (déjà vécu sur les quadrants SWOT),
    malgré le schéma."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(s for s in (_coerce_text(v) for v in value) if s)
    if isinstance(value, dict):
        return " ".join(s for s in (_coerce_text(v) for v in value.values()) if s)
    if value is None:
        return ""
    return str(value).strip()


def _coerce_question_id(value) -> int | None:
    """`question_id` tel que les modèles locaux le renvoient VRAIMENT : int,
    chaîne ("8"), parfois "8.0"/8.0. `bool` est rejeté (int(True) == 1
    s'attacherait à la question 1) et un float non entier (8.9) est droppé
    plutôt qu'arrondi vers la mauvaise question."""
    if isinstance(value, bool):
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    if not as_float.is_integer():
        return None
    return int(as_float)


def _normalise(value: str) -> str:
    return " ".join(value.casefold().split()).strip(" ?.!:…")


def _echoes_label(text_value: str, label: str) -> bool:
    """Vrai si la « réponse » n'est qu'une recopie du libellé de la question —
    comportement observé en réel sur un petit modèle local (qwen2.5:3b,
    2026-07-25) : le texte utile était alors dans les verbatims."""
    return bool(text_value) and _normalise(text_value) == _normalise(label)


def _extract_answers_chunk(questions, text: str) -> dict[int, dict]:
    """Un seul appel IA sur un tronçon de texte — factorisé pour le
    map-reduce de `extract_answers_from_text()`.

    Robustesse aux sorties réelles des modèles locaux (constats d'un passage
    réel qwen2.5:3b, 2026-07-25 — les mocks des tests ne renvoient que des
    types propres) : `question_id` arrive souvent en CHAÎNE ("8") — l'ancien
    `qid not in valid_ids` (set d'ints) droppait alors TOUTES les réponses en
    silence ; et le champ `text` peut n'être qu'un écho du libellé de la
    question, la matière étant dans les verbatims — on replie alors la réponse
    sur les verbatims plutôt que de livrer la question en guise de réponse."""
    data = call_ai_json(
        SYSTEM,
        _build_prompt(questions, text),
        _SCHEMA,
        _JSON_HINT,
        max_tokens=MAX_TOKENS,
        error_cls=InterviewExtractAIError,
    )
    by_id = {q.id: q for q in questions}
    result: dict[int, dict] = {}
    for row in data.get("answers") or []:
        qid = _coerce_question_id(row.get("question_id"))
        question = by_id.get(qid)
        if question is None:
            continue
        raw_verbatims = row.get("verbatims") or []
        if isinstance(raw_verbatims, str):
            raw_verbatims = [raw_verbatims]
        verbatims = [
            s for s in (str(v).strip() for v in raw_verbatims) if s
        ]
        text_value = _coerce_text(row.get("text"))
        if _echoes_label(text_value, question.label):
            # Les verbatims deviennent LA réponse : on les vide (sinon le même
            # contenu apparaît deux fois — réponse + citation — jusque dans
            # les exports), et on re-teste l'écho (modèle qui recopie le
            # libellé PARTOUT : rien d'exploitable, question écartée).
            text_value = " ".join(verbatims)
            verbatims = []
            if _echoes_label(text_value, question.label):
                text_value = ""
        if not text_value:
            continue
        result[qid] = {"text": text_value, "verbatims": verbatims}
    return result


def extract_answers_from_text(questions, text: str) -> dict[int, dict]:
    """Retourne `{question_id: {"text": str, "verbatims": [str]}}`.

    Ne contient que les questions pour lesquelles l'IA a trouvé de la
    matière. Découpe `text` en tronçons (map) si nécessaire — un seul
    tronçon fait un seul appel, comportement inchangé. Fusion (reduce) :
    pas d'appel IA supplémentaire, la première réponse non vide trouvée pour
    chaque question l'emporte (voir docstring du module). Lève
    `InterviewExtractAIError`.
    """
    if not text.strip():
        raise InterviewExtractAIError("Document vide — rien à extraire.")
    if not questions:
        raise InterviewExtractAIError("La trame ne contient aucune question.")

    # Même assainissement que l'extraction libre : les marqueurs
    # « ⚠ [segment perdu…] » d'un enregistrement au fil de l'eau ne doivent
    # jamais être traités comme de la matière d'entretien.
    cleaned = strip_segment_markers(text)
    if not cleaned.strip():
        raise InterviewExtractAIError(
            "La transcription ne contient que des marqueurs de segments en échec "
            "(« ⚠ [segment perdu…] ») — aucun contenu exploitable."
        )
    text = cleaned

    chunks = chunk_text_by_paragraph(text, ollama_chunk_max_words())

    # Chemin frère de `interview_libre_extract_ai` : un tronçon qui dépasse le
    # délai est redécoupé en deux et retenté plutôt que de faire échouer la
    # tranche entière (`call_par_troncons_degressifs`).
    merged: dict[int, dict] = {}
    for chunk in chunks:
        for partiel in call_par_troncons_degressifs(
            chunk, lambda c: _extract_answers_chunk(questions, c)
        ):
            for qid, answer in partiel.items():
                merged.setdefault(qid, answer)
    return merged
