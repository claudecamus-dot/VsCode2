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

from .ai_common import AIError, call_ai_json, chunk_text_by_paragraph, ollama_chunk_max_words

MAX_TOKENS = 3000

SYSTEM = (
    "Tu es consultant·e senior. On te donne une liste de questions "
    "d'entretien et un document (transcription, notes) rédigé après un "
    "entretien. Pour chaque question réellement abordée dans le document, "
    "produis une réponse synthétique fidèle aux propos (n'invente rien) et, "
    "si le document contient des citations mot-pour-mot pertinentes, "
    "relève-les comme verbatims. Ignore les questions non abordées — ne les "
    "fais pas apparaître dans le résultat plutôt que d'inventer une réponse."
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
    '(liste de {"question_id", "text", "verbatims": [...]}).'
)


class InterviewExtractAIError(AIError):
    """Erreur fonctionnelle d'extraction IA — le message est destiné à l'UI."""


def _build_prompt(questions, text: str) -> str:
    lines = ["QUESTIONS :"]
    for q in questions:
        lines.append(f"  [{q.id}] {q.label}")
    lines += ["", "DOCUMENT :", text]
    return "\n".join(lines)


def _extract_answers_chunk(questions, text: str) -> dict[int, dict]:
    """Un seul appel IA sur un tronçon de texte — factorisé pour le
    map-reduce de `extract_answers_from_text()`."""
    data = call_ai_json(
        SYSTEM,
        _build_prompt(questions, text),
        _SCHEMA,
        _JSON_HINT,
        max_tokens=MAX_TOKENS,
        error_cls=InterviewExtractAIError,
    )
    valid_ids = {q.id for q in questions}
    result: dict[int, dict] = {}
    for row in data.get("answers") or []:
        qid = row.get("question_id")
        text_value = (row.get("text") or "").strip()
        if qid not in valid_ids or not text_value:
            continue
        result[qid] = {
            "text": text_value,
            "verbatims": [v.strip() for v in row.get("verbatims") or [] if v.strip()],
        }
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

    chunks = chunk_text_by_paragraph(text, ollama_chunk_max_words())
    if len(chunks) == 1:
        return _extract_answers_chunk(questions, chunks[0])

    merged: dict[int, dict] = {}
    for chunk in chunks:
        for qid, answer in _extract_answers_chunk(questions, chunk).items():
            merged.setdefault(qid, answer)
    return merged
