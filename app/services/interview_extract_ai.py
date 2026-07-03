"""Extraction IA des réponses d'un entretien depuis un document déjà rédigé
(transcription, notes) — pré-remplissage de `Answer`/`Verbatim` (import
d'entretien).

Même plomberie que `synthese_ai.py`/`trame_extract_ai.py` (`ai_common.py`).
Pas d'heuristique de repli fiable pour un texte libre non structuré : sans IA
configurée, `extract_answers_from_text()` lève `InterviewExtractAIError`.
"""
from __future__ import annotations

import os

from .ai_common import AIError, _anthropic, _friendly, _parse_json

MODEL = os.environ.get("SYNTHESE_MODEL", "claude-opus-4-8")
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


def extract_answers_from_text(questions, text: str) -> dict[int, dict]:
    """Retourne `{question_id: {"text": str, "verbatims": [str]}}`.

    Ne contient que les questions pour lesquelles l'IA a trouvé de la
    matière. Lève `InterviewExtractAIError`.
    """
    anthropic = _anthropic()
    if anthropic is None:
        raise InterviewExtractAIError("Le SDK « anthropic » n'est pas installé.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise InterviewExtractAIError(
            "Clé absente : définissez ANTHROPIC_API_KEY pour l'extraction IA."
        )
    if not text.strip():
        raise InterviewExtractAIError("Document vide — rien à extraire.")
    if not questions:
        raise InterviewExtractAIError("La trame ne contient aucune question.")

    common = dict(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": _build_prompt(questions, text)}],
    )
    try:
        client = anthropic.Anthropic()
        try:
            resp = client.messages.create(
                system=SYSTEM,
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
                **common,
            )
        except TypeError:
            resp = client.messages.create(system=SYSTEM + _JSON_HINT, **common)
    except anthropic.APIError as exc:
        raise InterviewExtractAIError(_friendly(exc)) from exc
    except Exception as exc:
        raise InterviewExtractAIError(_friendly(exc)) from exc

    if getattr(resp, "stop_reason", None) == "refusal":
        raise InterviewExtractAIError("Extraction refusée par le modèle.")

    body = next(
        (b.text for b in resp.content if getattr(b, "type", None) == "text"), ""
    )
    try:
        data = _parse_json(body)
    except AIError as exc:
        raise InterviewExtractAIError(str(exc)) from exc

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
