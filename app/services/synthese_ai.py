"""Génération IA de la synthèse d'un thème (US4.2).

Appelle l'API Claude via le SDK officiel `anthropic`, en sortie structurée JSON
(`output_config.format`), avec repli sur une consigne JSON si le SDK est trop
ancien. Modèle par défaut : Claude Opus 4.8 (`claude-opus-4-8`).

Dégradation gracieuse : si la clé `ANTHROPIC_API_KEY` est absente ou le SDK non
installé, `is_configured()` renvoie False (l'UI propose alors la saisie manuelle)
et `generate_theme_synthesis()` lève `SynthesisAIError` avec un message lisible.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter

# Modèle par défaut — surchargeable par variable d'environnement.
MODEL = os.environ.get("SYNTHESE_MODEL", "claude-opus-4-8")
MAX_TOKENS = 2000

SYSTEM = (
    "Tu es consultant·e senior. À partir des réponses de plusieurs personnes "
    "interviewées sur un même thème, tu produis une synthèse transverse en "
    "français, factuelle et nuancée :\n"
    "- summary : 3 à 5 points saillants (les enseignements clés du thème) ;\n"
    "- convergences : ce sur quoi les personnes se rejoignent ;\n"
    "- divergences : désaccords, tensions ou angles morts.\n"
    "Reste fidèle aux propos, n'invente rien. Si un champ manque de matière, "
    "indique-le brièvement. Rédige en puces courtes, une idée par ligne."
)

_JSON_HINT = (
    "\nRéponds UNIQUEMENT par un objet JSON aux clés "
    '"summary", "convergences", "divergences".'
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "convergences": {"type": "string"},
        "divergences": {"type": "string"},
    },
    "required": ["summary", "convergences", "divergences"],
    "additionalProperties": False,
}


class SynthesisAIError(RuntimeError):
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


def _build_prompt(theme, by_question, verbatims) -> str:
    lines = [f"THÈME : {theme.title}", ""]
    for q in theme.questions:
        rows = by_question.get(q.id) or []
        if not rows:
            continue
        lines.append(f"Question : {q.label}")
        for r in rows:
            who = r["interviewee"]
            if r.get("role"):
                who += f" ({r['role']})"
            answer = " / ".join(p for p in (r.get("value"), r.get("text")) if p)
            lines.append(f"  - {who} : {answer}")
        lines.append("")
    if verbatims:
        lines.append("VERBATIMS (citations mot pour mot) :")
        for v in verbatims:
            lines.append(f"  « {v['quote']} » — {v['interviewee']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Mode démo hors-ligne : synthèse par règles (aucun appel réseau, aucun coût).
# --------------------------------------------------------------------------- #
_STOP = set(
    "le la les un une des de du au aux et ou en on nous vous ils elles est "
    "sont pour par sur dans avec sans plus moins que qui quoi dont mais cette "
    "ces son ses leur leurs pas tout tous toute toutes c'est qu'il faute "
    "toujours après coup notre votre cela alors donc être avoir fait".split()
)


def _keywords(texts) -> Counter:
    c: Counter = Counter()
    for t in texts:
        for w in re.findall(r"[a-zàâäéèêëïîôöùûüçœ]{4,}", (t or "").lower()):
            if w not in _STOP:
                c[w] += 1
    return c


def generate_demo_synthesis(theme, by_question, verbatims) -> dict:
    """Synthèse heuristique hors-ligne (mode démo) — pas d'IA.

    summary : sujets récurrents (mots-clés) ; convergences : mots cités par
    plusieurs personnes ; divergences : écarts sur les questions fermées.
    """
    rows = []  # (interviewee, question, texte)
    for q in theme.questions:
        for r in by_question.get(q.id) or []:
            txt = " ".join(p for p in (r.get("value"), r.get("text")) if p)
            rows.append((r["interviewee"], q, txt))

    kw = _keywords(t for _, _, t in rows)
    top = [w for w, _ in kw.most_common(5)]
    summary = "\n".join(f"- Sujet récurrent : « {w} »" for w in top) or \
        "- Pas assez de matière pour dégager des points saillants."

    by_person: dict[str, list[str]] = {}
    for who, _, txt in rows:
        by_person.setdefault(who, []).append(txt.lower())
    shared = []
    for w in kw:
        n = sum(1 for texts in by_person.values() if any(w in t for t in texts))
        if n >= 2:
            shared.append((n, w))
    shared.sort(reverse=True)
    convergences = "\n".join(f"- « {w} » évoqué par {n} personnes" for n, w in shared[:5]) or \
        "- Aucun point commun net détecté automatiquement."

    div = []
    for q in theme.questions:
        qr = by_question.get(q.id) or []
        if q.qtype in ("choice", "scale"):
            vals = {r["interviewee"]: r.get("value") for r in qr if r.get("value")}
            if len(set(vals.values())) > 1:
                detail = ", ".join(f"{who} : {v}" for who, v in vals.items())
                div.append(f"- {q.label} → {detail}")
    divergences = "\n".join(div) or \
        "- Pas de divergence marquée sur les questions fermées."

    return {"summary": summary, "convergences": convergences, "divergences": divergences}


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
    raise SynthesisAIError("Réponse IA non exploitable (JSON invalide).")


def generate_theme_synthesis(theme, by_question, verbatims) -> dict:
    """Retourne {summary, convergences, divergences}. Lève SynthesisAIError."""
    anthropic = _anthropic()
    if anthropic is None:
        raise SynthesisAIError("Le SDK « anthropic » n'est pas installé.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SynthesisAIError(
            "Clé absente : définissez ANTHROPIC_API_KEY pour générer la synthèse."
        )

    prompt = _build_prompt(theme, by_question, verbatims)
    common = dict(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
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
            # SDK antérieur à output_config : repli sur consigne JSON + parsing.
            resp = client.messages.create(system=SYSTEM + _JSON_HINT, **common)
    except anthropic.APIError as exc:  # auth / rate limit / connexion / 5xx
        raise SynthesisAIError(_friendly(exc)) from exc
    except Exception as exc:  # garde-fou : ne jamais propager une 500 brute
        raise SynthesisAIError(_friendly(exc)) from exc

    if getattr(resp, "stop_reason", None) == "refusal":
        raise SynthesisAIError("Génération refusée par le modèle.")

    text = next(
        (b.text for b in resp.content if getattr(b, "type", None) == "text"), ""
    )
    data = _parse_json(text)
    return {
        "summary": (data.get("summary") or "").strip(),
        "convergences": (data.get("convergences") or "").strip(),
        "divergences": (data.get("divergences") or "").strip(),
    }
