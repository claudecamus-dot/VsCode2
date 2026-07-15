"""Extraction IA d'un entretien en mode libre (incr.9, US9.5) — un seul appel,
depuis la transcription brute :

1. structure la conversation en tours de parole (interlocuteur/question/
   remarque) groupés en sections thématiques (section_title), pour l'écran
   Analyse (rendu façon transcription structurée/éditée) et l'export
   Markdown (US9.7) ;
2. répartit le contenu dans les 5 catégories de `GlobalSynthesis` (même
   forme que `synthese_ai.GLOBAL_SCHEMA`) + un résumé court, pour l'écran
   Synthèse et pour alimenter la synthèse globale de mission sans passer
   par une synthèse par thème (pas de trame en mode libre) ;
3. identité de l'interviewé·e (nom/prénom, rôle, équipe/département) si
   elle ressort de la transcription (auto-présentation en début
   d'entretien, typiquement) — évite d'avoir à la ressaisir à la main
   quand elle a déjà été dite à l'oral.

Tourne sur la transcription brute, avant toute relecture humaine (décision
explicite : un seul écran de revue en aval, sur toutes les sorties à la
fois, plutôt que des passes séquentielles). Même plomberie que
`interview_extract_ai.py`/`synthese_ai.py` (`ai_common.call_ai_json`).
"""
from __future__ import annotations

from .ai_common import AIError, call_ai_json

MAX_TOKENS = 4000

SYSTEM = (
    "Tu es consultant·e senior. On te donne la transcription brute d'un "
    "entretien libre (pas de questionnaire prédéfini). Fais quatre choses à "
    "partir de ce seul texte, fidèlement, sans rien inventer :\n"
    "1. turns : découpe la conversation en tours de parole successifs. Pour "
    "chaque tour, indique l'interlocuteur (qui parle — nom, rôle ou "
    "« Consultant·e » si identifiable, sinon une étiquette générique), et "
    "selon la nature du propos, question (une question posée) et/ou "
    "remarque (une déclaration, un constat, une réponse). Un tour peut "
    "n'avoir qu'un des deux. Regroupe aussi la conversation en sections "
    "thématiques : quand un tour ouvre un nouveau sujet, donne-lui un "
    "section_title court (ex. « Recueillir et transcrire l'information ») ; "
    "les tours suivants qui continuent le même sujet laissent section_title "
    "vide (ils héritent de la dernière section ouverte).\n"
    "2. repartition : répartis la matière de l'entretien dans les 5 "
    "catégories contexte / culture_adn / forces_succes / "
    "points_amelioration / aspirations (mêmes définitions qu'une synthèse "
    "de mission classique). Si une catégorie manque de matière dans cet "
    "entretien, indique-le brièvement plutôt que de combler artificiellement.\n"
    "3. resume : un résumé de 1 à 3 phrases de l'entretien dans son "
    "ensemble — le message central à retenir, pas un simple sommaire des "
    "sujets abordés.\n"
    "4. identite : si la personne interviewée donne son nom/prénom, son "
    "rôle/fonction, et/ou son équipe ou département (typiquement en se "
    "présentant en début d'entretien), relève-les. Laisse un champ vide "
    "s'il n'est pas mentionné explicitement — ne devine jamais un nom à "
    "partir du style de parole."
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
                    "section_title": {"type": "string"},
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
        "resume": {"type": "string"},
        "identite": {
            "type": "object",
            "properties": {
                "interviewee_name": {"type": "string"},
                "interviewee_role": {"type": "string"},
                "interviewee_entity": {"type": "string"},
            },
            "required": ["interviewee_name", "interviewee_role", "interviewee_entity"],
            "additionalProperties": False,
        },
    },
    "required": ["turns", "repartition", "resume", "identite"],
    "additionalProperties": False,
}

_JSON_HINT = (
    '\nRéponds UNIQUEMENT par un objet JSON aux clés "turns" (liste de '
    '{"interlocuteur", "question"?, "remarque"?, "section_title"?}), '
    '"repartition" (objet aux 5 clés contexte/culture_adn/forces_succes/'
    'points_amelioration/aspirations), "resume" (chaîne, 1-3 phrases) et '
    '"identite" (objet aux clés interviewee_name/interviewee_role/'
    'interviewee_entity, chaîne vide si non mentionné).'
)


class InterviewLibreExtractAIError(AIError):
    """Erreur fonctionnelle d'extraction IA (mode libre) — message UI."""


def extract_libre_from_text(text: str) -> dict:
    """Retourne `{"turns": [{"interlocuteur", "question", "remarque",
    "section_title"}, ...], "repartition": {contexte, culture_adn,
    forces_succes, points_amelioration, aspirations}, "resume": str,
    "identity": {interviewee_name, interviewee_role, interviewee_entity}}`
    (chaînes vides si non détecté, jamais `None` — pensé pour être fusionné
    avec une saisie manuelle sans cas particulier). Lève
    `InterviewLibreExtractAIError`."""
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
        section_title = (row.get("section_title") or "").strip() or None
        if not interlocuteur or (question is None and remarque is None):
            continue
        turns.append({
            "interlocuteur": interlocuteur,
            "question": question,
            "remarque": remarque,
            "section_title": section_title,
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

    identite_raw = data.get("identite") or {}
    identity = {
        key: (identite_raw.get(key) or "").strip()
        for key in ("interviewee_name", "interviewee_role", "interviewee_entity")
    }
    resume = (data.get("resume") or "").strip()

    return {
        "turns": turns,
        "repartition": repartition,
        "resume": resume,
        "identity": identity,
    }
