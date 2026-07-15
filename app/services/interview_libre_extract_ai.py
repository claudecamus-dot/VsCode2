"""Extraction IA d'un entretien en mode libre (incr.9, US9.5) — un seul appel,
depuis la transcription brute :

1. structure la conversation en tours de parole (interlocuteur/question/
   remarque), pour la lecture et l'export Markdown (US9.7) ;
2. répartit le contenu dans les 5 catégories de `GlobalSynthesis` (même
   forme que `synthese_ai.GLOBAL_SCHEMA`), pour alimenter la synthèse
   globale de mission sans passer par une synthèse par thème (pas de trame
   en mode libre).

Tourne sur la transcription brute, avant toute relecture humaine (décision
explicite : un seul écran de revue en aval, sur les deux sorties à la fois,
plutôt que deux passes séquentielles). Même plomberie que
`interview_extract_ai.py`/`synthese_ai.py` (`ai_common.call_ai_json`).
"""
from __future__ import annotations

from .ai_common import AIError, call_ai_json

MAX_TOKENS = 3000

SYSTEM = (
    "Tu es consultant·e senior. On te donne la transcription brute d'un "
    "entretien libre (pas de questionnaire prédéfini). Fais deux choses à "
    "partir de ce seul texte, fidèlement, sans rien inventer :\n"
    "1. turns : découpe la conversation en tours de parole successifs. Pour "
    "chaque tour, indique l'interlocuteur (qui parle — nom, rôle ou "
    "« Consultant·e » si identifiable, sinon une étiquette générique), et "
    "selon la nature du propos, question (une question posée) et/ou "
    "remarque (une déclaration, un constat, une réponse). Un tour peut "
    "n'avoir qu'un des deux.\n"
    "2. repartition : répartis la matière de l'entretien dans les 5 "
    "catégories contexte / culture_adn / forces_succes / "
    "points_amelioration / aspirations (mêmes définitions qu'une synthèse "
    "de mission classique). Si une catégorie manque de matière dans cet "
    "entretien, indique-le brièvement plutôt que de combler artificiellement."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "interlocuteur": {"type": "string"},
                    "question": {"type": "string"},
                    "remarque": {"type": "string"},
                },
                "required": ["interlocuteur"],
                "additionalProperties": False,
            },
        },
        "repartition": {
            "type": "object",
            "properties": {
                "contexte": {"type": "string"},
                "culture_adn": {"type": "string"},
                "forces_succes": {"type": "string"},
                "points_amelioration": {"type": "string"},
                "aspirations": {"type": "string"},
            },
            "required": [
                "contexte", "culture_adn", "forces_succes",
                "points_amelioration", "aspirations",
            ],
            "additionalProperties": False,
        },
    },
    "required": ["turns", "repartition"],
    "additionalProperties": False,
}

_JSON_HINT = (
    '\nRéponds UNIQUEMENT par un objet JSON aux clés "turns" (liste de '
    '{"interlocuteur", "question"?, "remarque"?}) et "repartition" (objet '
    'aux 5 clés contexte/culture_adn/forces_succes/points_amelioration/'
    'aspirations).'
)


class InterviewLibreExtractAIError(AIError):
    """Erreur fonctionnelle d'extraction IA (mode libre) — message UI."""


def extract_libre_from_text(text: str) -> dict:
    """Retourne `{"turns": [{"interlocuteur", "question", "remarque"}, ...],
    "repartition": {contexte, culture_adn, forces_succes,
    points_amelioration, aspirations}}`. Lève `InterviewLibreExtractAIError`."""
    if not text.strip():
        raise InterviewLibreExtractAIError("Aucun texte transcrit.")

    data = call_ai_json(
        SYSTEM,
        f"TRANSCRIPTION :\n{text}",
        _SCHEMA,
        _JSON_HINT,
        max_tokens=MAX_TOKENS,
        error_cls=InterviewLibreExtractAIError,
    )

    turns = []
    for row in data.get("turns") or []:
        interlocuteur = (row.get("interlocuteur") or "").strip()
        question = (row.get("question") or "").strip() or None
        remarque = (row.get("remarque") or "").strip() or None
        if not interlocuteur or (question is None and remarque is None):
            continue
        turns.append({
            "interlocuteur": interlocuteur,
            "question": question,
            "remarque": remarque,
        })

    repartition_raw = data.get("repartition") or {}
    repartition = {
        key: (repartition_raw.get(key) or "").strip()
        for key in (
            "contexte", "culture_adn", "forces_succes",
            "points_amelioration", "aspirations",
        )
    }

    if not turns:
        raise InterviewLibreExtractAIError(
            "Aucun tour de parole détecté dans la transcription."
        )

    return {"turns": turns, "repartition": repartition}
