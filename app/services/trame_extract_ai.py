"""Extraction IA d'une trame (thèmes/questions) depuis un document libre.

Repli utilisé quand le document ne suit pas la convention reconnue par le
parser heuristique (`app/importers/docx_trame.py`) — ou quand le mode IA est
explicitement demandé à l'import. Même schéma de sortie (`ParsedTrame`) pour
que la fusion dans la trame (`app/routers/trames.py`) soit identique quelle
que soit la source.

Dégradation gracieuse : pas d'heuristique de repli fiable pour du texte libre
non structuré — sans IA configurée, `extract_trame_from_text()` lève
`TrameExtractAIError` (le routeur affiche un message lisible).
"""
from __future__ import annotations

import os

from ..importers.docx_trame import ParsedQuestion, ParsedTheme, ParsedTrame
from .ai_common import AIError, _anthropic, _friendly, _parse_json

MODEL = os.environ.get("SYNTHESE_MODEL", "claude-opus-4-8")
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
    anthropic = _anthropic()
    if anthropic is None:
        raise TrameExtractAIError("Le SDK « anthropic » n'est pas installé.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise TrameExtractAIError(
            "Clé absente : définissez ANTHROPIC_API_KEY pour l'extraction IA."
        )
    if not text.strip():
        raise TrameExtractAIError("Document vide — rien à extraire.")

    common = dict(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": text}],
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
        raise TrameExtractAIError(_friendly(exc)) from exc
    except Exception as exc:
        raise TrameExtractAIError(_friendly(exc)) from exc

    if getattr(resp, "stop_reason", None) == "refusal":
        raise TrameExtractAIError("Extraction refusée par le modèle.")

    body = next(
        (b.text for b in resp.content if getattr(b, "type", None) == "text"), ""
    )
    try:
        data = _parse_json(body)
    except AIError as exc:
        raise TrameExtractAIError(str(exc)) from exc

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
