"""Plomberie IA partagée — dégradation gracieuse, multi-fournisseur.

Factorisé depuis `synthese_ai.py` : `trame_extract_ai.py` et
`interview_extract_ai.py` ont besoin exactement des mêmes helpers
(configuration, appel SDK, parsing JSON, messages d'erreur lisibles).

Le fournisseur est choisi par la variable d'environnement `AI_PROVIDER`
(`anthropic` par défaut, ou `openai` / `mistral`) — un seul fournisseur actif
à la fois, pas de repli automatique de l'un vers l'autre : si la clé du
fournisseur configuré manque, l'appelant obtient un message clair plutôt
qu'un essai silencieux sur un autre fournisseur (comportement surprenant,
coût/latence doublés). `call_ai_json()` est le point d'entrée unique : les
3 modules appelants ne connaissent plus le SDK sous-jacent.
"""
from __future__ import annotations

import json
import os

_API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}

# Modèles par défaut, surchargeables par la variable d'environnement
# SYNTHESE_MODEL quel que soit le fournisseur actif.
_DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o",
    "mistral": "mistral-large-latest",
}


class AIError(RuntimeError):
    """Erreur fonctionnelle d'appel IA — le message est destiné à l'UI."""


def _anthropic():
    try:
        import anthropic

        return anthropic
    except ModuleNotFoundError:
        return None


def _openai():
    try:
        import openai

        return openai
    except ModuleNotFoundError:
        return None


def _mistral():
    try:
        from mistralai.client import Mistral

        return Mistral
    except ModuleNotFoundError:
        return None


_SDK_LOADERS = {"anthropic": _anthropic, "openai": _openai, "mistral": _mistral}


def active_provider() -> str:
    """Fournisseur configuré — `AI_PROVIDER`, replié sur `anthropic` si absent
    ou non reconnu (ne jamais planter sur une variable mal renseignée). Lu à
    chaque appel (pas mis en cache au chargement du module) pour rester
    testable et pour refléter un changement d'environnement à chaud."""
    provider = os.environ.get("AI_PROVIDER", "anthropic").strip().lower()
    return provider if provider in _SDK_LOADERS else "anthropic"


def active_model() -> str:
    provider = active_provider()
    return os.environ.get("SYNTHESE_MODEL", "").strip() or _DEFAULT_MODELS[provider]


def api_key_env_name() -> str:
    """Nom de la variable d'environnement attendue pour le fournisseur actif —
    utilisé pour des messages d'UI qui restent corrects quel que soit `AI_PROVIDER`."""
    return _API_KEY_ENV[active_provider()]


def is_configured() -> bool:
    """Vrai si une génération IA réelle est possible (SDK du fournisseur actif
    installé + sa clé API présente)."""
    provider = active_provider()
    sdk = _SDK_LOADERS[provider]()
    return sdk is not None and bool(os.environ.get(_API_KEY_ENV[provider]))


def demo_enabled() -> bool:
    """Vrai si le mode démo hors-ligne (sans IA, sans clé) est activé."""
    return os.environ.get("SYNTHESE_DEMO", "").strip().lower() in ("1", "true", "yes", "on")


def _friendly(exc) -> str:
    name = type(exc).__name__
    if "Authentication" in name or "PermissionDenied" in name:
        return "Clé API refusée (authentification)."
    if "RateLimit" in name:
        return "Limite de débit atteinte — réessayez dans un instant."
    if "Connection" in name or "Timeout" in name:
        return "Impossible de joindre l'API IA (réseau)."
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


def _call_anthropic(system: str, prompt: str, schema: dict, json_hint: str, model: str, max_tokens: int) -> str:
    anthropic = _anthropic()
    common = dict(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    client = anthropic.Anthropic()
    try:
        resp = client.messages.create(
            system=system,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            **common,
        )
    except TypeError:
        # SDK antérieur à output_config : repli sur consigne JSON + parsing.
        resp = client.messages.create(system=system + json_hint, **common)
    if getattr(resp, "stop_reason", None) == "refusal":
        raise AIError("Génération refusée par le modèle.")
    return next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")


def _call_openai(system: str, prompt: str, schema: dict, json_hint: str, model: str, max_tokens: int) -> str:
    openai = _openai()
    client = openai.OpenAI()
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system + json_hint},
            {"role": "user", "content": prompt},
        ],
    )
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", None) == "content_filter":
        raise AIError("Génération refusée par le modèle.")
    return choice.message.content or ""


def _call_mistral(system: str, prompt: str, schema: dict, json_hint: str, model: str, max_tokens: int) -> str:
    Mistral = _mistral()
    client = Mistral(api_key=os.environ.get("MISTRAL_API_KEY"))
    resp = client.chat.complete(
        model=model,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system + json_hint},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content or ""


_CALLERS = {"anthropic": _call_anthropic, "openai": _call_openai, "mistral": _call_mistral}


def call_ai_json(
    system: str,
    prompt: str,
    schema: dict,
    json_hint: str,
    *,
    max_tokens: int = 2000,
    error_cls: type[AIError] = AIError,
) -> dict:
    """Appel IA générique, sortie JSON structurée, quel que soit le
    fournisseur actif (`AI_PROVIDER`). Lève `error_cls` (une sous-classe
    d'`AIError` fournie par l'appelant, pour que les routeurs puissent
    continuer à distinguer synthèse / extraction trame / extraction
    entretien par type d'exception) avec un message toujours lisible côté
    UI, y compris si le SDK du fournisseur diffère de ce qui est attendu ici
    (garde-fou générique en plus des erreurs connues)."""
    provider = active_provider()
    sdk = _SDK_LOADERS[provider]()
    if sdk is None:
        raise error_cls(f"Le SDK « {provider} » n'est pas installé.")
    key_env = _API_KEY_ENV[provider]
    if not os.environ.get(key_env):
        raise error_cls(f"Clé absente : définissez {key_env} pour activer la génération IA.")

    try:
        text = _CALLERS[provider](system, prompt, schema, json_hint, active_model(), max_tokens)
    except AIError as exc:
        raise error_cls(str(exc)) from exc
    except Exception as exc:  # garde-fou : jamais de 500 brute sur un appel IA
        raise error_cls(_friendly(exc)) from exc

    try:
        return _parse_json(text)
    except AIError as exc:
        raise error_cls(str(exc)) from exc
