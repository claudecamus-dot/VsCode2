"""Extraction IA d'une trame (thèmes/questions) depuis un document libre.

Repli utilisé quand le document ne suit pas la convention reconnue par le
parser heuristique (`app/importers/docx_trame.py`) — ou quand le mode IA est
explicitement demandé à l'import. Même schéma de sortie (`ParsedTrame`) pour
que la fusion dans la trame (`app/routers/trames.py`) soit identique quelle
que soit la source.

Dégradation gracieuse : pas d'heuristique de repli fiable pour du texte libre
non structuré — sans IA configurée, `extract_trame_from_text()` lève
`TrameExtractAIError` (le routeur affiche un message lisible). Fournisseur IA
actif = `AI_PROVIDER` (voir `ai_common.py`), anthropic par défaut.
"""
from __future__ import annotations

from ..importers.docx_trame import ParsedQuestion, ParsedTheme, ParsedTrame
from .ai_common import AIError, call_ai_json

MAX_TOKENS = 3000

SYSTEM = (
    "Tu es consultant·e senior. À partir d'un document décrivant une trame "
    "d'entretien (mission, brief, notes de cadrage…), tu en extrais la "
    "structure : des thèmes, chacun avec ses questions. Une question est "
    "un point sur lequel on veut interroger la personne interviewée — "
    "reformule en question claire si le document ne la formule pas déjà "
    "sous cette forme. N'invente pas de thème ou de question absent du "
    "document. Ignore le texte hors-sujet (logistique, signatures…)."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "help": {"type": "string"},
                            },
                            "required": ["label"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "questions"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["themes"],
    "additionalProperties": False,
}

_JSON_HINT = (
    '\nRéponds UNIQUEMENT par un objet JSON à la clé "themes" '
    '(liste de {"title", "questions": [{"label", "help"}]}).'
)


class TrameExtractAIError(AIError):
    """Erreur fonctionnelle d'extraction IA — le message est destiné à l'UI."""


def extract_trame_from_text(text: str, name: str = "Trame importée (IA)") -> ParsedTrame:
    """Retourne une `ParsedTrame`. Lève `TrameExtractAIError`."""
    if not text.strip():
        raise TrameExtractAIError("Document vide — rien à extraire.")

    data = call_ai_json(
        SYSTEM, text, _SCHEMA, _JSON_HINT, max_tokens=MAX_TOKENS, error_cls=TrameExtractAIError
    )

    themes = [
        ParsedTheme(
            title=(t.get("title") or "").strip() or "Thème",
            questions=[
                ParsedQuestion(
                    label=(q.get("label") or "").strip(),
                    help=(q.get("help") or "").strip(),
                )
                for q in t.get("questions") or []
                if (q.get("label") or "").strip()
            ],
        )
        for t in data.get("themes") or []
    ]
    themes = [t for t in themes if t.questions]
    return ParsedTrame(name=name, themes=themes)
