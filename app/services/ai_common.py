"""Plomberie IA partagée (SDK Anthropic, dégradation gracieuse).

Factorisé depuis `synthese_ai.py` : `trame_extract_ai.py` et
`interview_extract_ai.py` ont besoin exactement des mêmes helpers
(configuration, appel SDK, parsing JSON, messages d'erreur lisibles).
"""
from __future__ import annotations

import json
import os


class AIError(RuntimeError):
    """Erreur fonctionnelle d'appel IA — le message est destiné à l'UI."""


def _anthropic():
    try:
        import anthropic

        return anthropic
    except ModuleNotFoundError:
        return None


def is_configured() -> bool:
    """Vrai si une génération IA réelle est possible (SDK installé + clé présente)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and _anthropic() is not None


def demo_enabled() -> bool:
    """Vrai si le mode démo hors-ligne (sans IA, sans clé) est activé."""
    return os.environ.get("SYNTHESE_DEMO", "").strip().lower() in ("1", "true", "yes", "on")


def _friendly(exc) -> str:
    name = type(exc).__name__
    if "Authentication" in name:
        return "Clé API refusée (authentification)."
    if "RateLimit" in name:
        return "Limite de débit atteinte — réessayez dans un instant."
    if "Connection" in name:
        return "Impossible de joindre l'API Claude (réseau)."
    return f"Erreur lors de l'appel à l'IA : {getattr(exc, 'message', None) or exc}"


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise AIError("Réponse IA non exploitable (JSON invalide).")
