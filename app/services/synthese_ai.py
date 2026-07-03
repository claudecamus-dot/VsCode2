"""Génération IA de la synthèse d'un thème (US4.2).

Appelle le fournisseur IA actif (`AI_PROVIDER` — anthropic par défaut, ou
openai/mistral) via `ai_common.call_ai_json()`, en sortie structurée JSON.

Dégradation gracieuse : si la clé API du fournisseur actif est absente ou son
SDK non installé, `is_configured()` renvoie False (l'UI propose alors la
saisie manuelle) et `generate_theme_synthesis()` lève `SynthesisAIError` avec
un message lisible.
"""
from __future__ import annotations

from collections import Counter
import re

from .ai_common import AIError, call_ai_json, demo_enabled, is_configured

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


class SynthesisAIError(AIError):
    """Erreur fonctionnelle d'appel IA — le message est destiné à l'UI."""


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


def _call_claude(system: str, prompt: str, schema: dict, json_hint: str, max_tokens: int = MAX_TOKENS) -> dict:
    """Appel IA générique (fournisseur actif — voir `ai_common.PROVIDER`),
    sortie JSON structurée. Lève SynthesisAIError.

    Factorisé pour être réutilisé par la synthèse par thème, la synthèse
    globale et la génération de recommandations — seuls system/prompt/schema
    changent. Le nom historique (`_call_claude`) est conservé pour limiter le
    diff des 3 sites d'appel ci-dessous ; le fournisseur réel dépend d'
    `AI_PROVIDER` (anthropic par défaut).
    """
    return call_ai_json(system, prompt, schema, json_hint, max_tokens=max_tokens, error_cls=SynthesisAIError)


def generate_theme_synthesis(theme, by_question, verbatims) -> dict:
    """Retourne {summary, convergences, divergences}. Lève SynthesisAIError."""
    prompt = _build_prompt(theme, by_question, verbatims)
    data = _call_claude(SYSTEM, prompt, _SCHEMA, _JSON_HINT)
    return {
        "summary": (data.get("summary") or "").strip(),
        "convergences": (data.get("convergences") or "").strip(),
        "divergences": (data.get("divergences") or "").strip(),
    }


# --------------------------------------------------------------------------- #
# Synthèse globale (évol) : mêmes entretiens, mais regroupés en 5 catégories
# fixes transverses à tous les thèmes (contexte, culture, forces, points
# d'amélioration, aspirations) — calqué sur un rapport de restitution réel.
# --------------------------------------------------------------------------- #
GLOBAL_SYSTEM = (
    "Tu es consultant·e senior en conduite du changement. À partir de "
    "l'ensemble des réponses de tous les entretiens d'une mission (tous "
    "thèmes de trame confondus), tu produis une synthèse transverse en "
    "français qui regroupe le contenu en sous-thèmes émergents nommés — "
    "jamais un simple dump question par question. Pour chaque catégorie, "
    "structure ta réponse en quelques sous-thèmes courts, chacun suivi de "
    "puces factuelles fidèles aux propos recueillis :\n"
    "- contexte : faits marquants du contexte (organisation, historique, "
    "évènements récents) ;\n"
    "- culture_adn : traits de culture observés, pratiques en place ;\n"
    "- forces_succes : ce qui fonctionne bien, les leviers de succès ;\n"
    "- points_amelioration : douleurs, tensions, ce qui bloque ;\n"
    "- aspirations : ce que les personnes espèrent ou proposeraient si "
    "elles le pouvaient (« baguette magique »).\n"
    "N'invente rien ; si une catégorie manque de matière, indique-le "
    "brièvement plutôt que de combler artificiellement."
)

GLOBAL_JSON_HINT = (
    "\nRéponds UNIQUEMENT par un objet JSON aux clés "
    '"contexte", "culture_adn", "forces_succes", "points_amelioration", '
    '"aspirations".'
)

GLOBAL_SCHEMA = {
    "type": "object",
    "properties": {
        "contexte": {"type": "string"},
        "culture_adn": {"type": "string"},
        "forces_succes": {"type": "string"},
        "points_amelioration": {"type": "string"},
        "aspirations": {"type": "string"},
    },
    "required": [
        "contexte", "culture_adn", "forces_succes", "points_amelioration", "aspirations",
    ],
    "additionalProperties": False,
}


def _build_global_prompt(mission, material_by_theme) -> str:
    """material_by_theme : liste de (theme, by_question, verbatims), un
    triplet par thème — même matière que `_theme_material` (synthese.py),
    mais pour tous les thèmes de la trame plutôt qu'un seul."""
    lines = [f"MISSION : {mission.name}", ""]
    for theme, by_question, verbatims in material_by_theme:
        if not by_question and not verbatims:
            continue
        lines.append(f"=== THÈME : {theme.title} ===")
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
        if verbatims:
            lines.append("Verbatims :")
            for v in verbatims:
                lines.append(f"  « {v['quote']} » — {v['interviewee']}")
        lines.append("")
    return "\n".join(lines)


def generate_global_synthesis(mission, material_by_theme) -> dict:
    """Retourne un dict aux 5 clés de `GlobalSynthesis`. Lève SynthesisAIError."""
    prompt = _build_global_prompt(mission, material_by_theme)
    data = _call_claude(GLOBAL_SYSTEM, prompt, GLOBAL_SCHEMA, GLOBAL_JSON_HINT)
    return {
        "contexte": (data.get("contexte") or "").strip(),
        "culture_adn": (data.get("culture_adn") or "").strip(),
        "forces_succes": (data.get("forces_succes") or "").strip(),
        "points_amelioration": (data.get("points_amelioration") or "").strip(),
        "aspirations": (data.get("aspirations") or "").strip(),
    }


def generate_demo_global_synthesis(mission, material_by_theme) -> dict:
    """Version heuristique hors-ligne (mode démo), même principe que
    `generate_demo_synthesis` mais à l'échelle de la mission entière."""
    rows = []  # (interviewee, texte)
    for theme, by_question, _verbatims in material_by_theme:
        for q in theme.questions:
            for r in by_question.get(q.id) or []:
                txt = " ".join(p for p in (r.get("value"), r.get("text")) if p)
                rows.append((r["interviewee"], txt))

    kw = _keywords(t for _, t in rows)
    top = [w for w, _ in kw.most_common(5)]
    contexte = "\n".join(f"- Sujet récurrent : « {w} »" for w in top) or \
        "- Pas assez de matière pour dégager des points de contexte."

    by_person: dict[str, list[str]] = {}
    for who, txt in rows:
        by_person.setdefault(who, []).append(txt.lower())
    shared = []
    for w in kw:
        n = sum(1 for texts in by_person.values() if any(w in t for t in texts))
        if n >= 2:
            shared.append((n, w))
    shared.sort(reverse=True)
    forces_succes = "\n".join(f"- « {w} » évoqué par {n} personnes" for n, w in shared[:5]) or \
        "- Aucun point commun net détecté automatiquement."

    return {
        "contexte": contexte,
        "culture_adn": "- Mode démo : pas d'analyse de culture sans IA réelle.",
        "forces_succes": forces_succes,
        "points_amelioration": "- Mode démo : pas de détection de douleurs sans IA réelle.",
        "aspirations": "- Mode démo : pas d'analyse d'aspirations sans IA réelle.",
    }


# --------------------------------------------------------------------------- #
# Recommandations (évol) : dérivées de la synthèse globale déjà générée (pas
# des réponses brutes), regroupées en quelques axes transverses — chaque
# fiche suit un schéma fixe calqué sur un rapport de restitution réel.
# --------------------------------------------------------------------------- #
RECO_MAX_TOKENS = 6000

RECO_SYSTEM = (
    "Tu es consultant·e senior en transformation organisationnelle. À "
    "partir d'une synthèse transverse d'entretiens (contexte, culture, "
    "forces, points d'amélioration, aspirations), identifie 3 à 4 axes de "
    "recommandation qui recoupent l'ensemble du sujet — pas un axe par "
    "thème d'origine, mais une nouvelle structuration stratégique à "
    "l'échelle de la mission. Pour chaque axe, propose 2 à 4 "
    "recommandations concrètes. Pour chaque recommandation, renseigne "
    "exactement ces champs :\n"
    "- title : titre court de l'action ;\n"
    "- objectif : le manque ou problème adressé ;\n"
    "- acteurs : qui est impliqué (ex. CODIR, managers, équipes, RH) ;\n"
    "- valeur : note de 1 (faible) à 5 (fort impact) ;\n"
    "- complexite : note de 1 (simple) à 5 (complexe) ;\n"
    "- proposition_valeur : une phrase résumant le bénéfice ;\n"
    "- plan_actions : puces d'actions concrètes (ateliers, chantiers) ;\n"
    "- resultats_attendus : puces des bénéfices attendus.\n"
    "Reste ancré dans la matière fournie, n'invente pas de faits nouveaux."
)

RECO_JSON_HINT = (
    "\nRéponds UNIQUEMENT par un objet JSON à la clé \"axes\", liste "
    'd\'objets {"title", "recommendations": [...]}.'
)

RECO_SCHEMA = {
    "type": "object",
    "properties": {
        "axes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "recommendations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "objectif": {"type": "string"},
                                "acteurs": {"type": "string"},
                                "valeur": {"type": "integer"},
                                "complexite": {"type": "integer"},
                                "proposition_valeur": {"type": "string"},
                                "plan_actions": {"type": "string"},
                                "resultats_attendus": {"type": "string"},
                            },
                            "required": [
                                "title", "objectif", "acteurs", "valeur",
                                "complexite", "proposition_valeur",
                                "plan_actions", "resultats_attendus",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "recommendations"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["axes"],
    "additionalProperties": False,
}


def _clamp_score(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, n))


def _build_reco_prompt(global_synthesis) -> str:
    lines = ["SYNTHÈSE TRANSVERSE DE LA MISSION", ""]
    fields = [
        ("Contexte", global_synthesis.contexte),
        ("Culture & ADN", global_synthesis.culture_adn),
        ("Forces & succès", global_synthesis.forces_succes),
        ("Points d'amélioration", global_synthesis.points_amelioration),
        ("Aspirations (baguette magique)", global_synthesis.aspirations),
    ]
    for label, content in fields:
        if (content or "").strip():
            lines.append(f"=== {label} ===")
            lines.append(content.strip())
            lines.append("")
    return "\n".join(lines)


def generate_recommendations(global_synthesis) -> list[dict]:
    """Retourne une liste d'axes {"title", "recommendations": [...]}.
    Lève SynthesisAIError."""
    prompt = _build_reco_prompt(global_synthesis)
    data = _call_claude(RECO_SYSTEM, prompt, RECO_SCHEMA, RECO_JSON_HINT, max_tokens=RECO_MAX_TOKENS)
    axes = []
    for axis in data.get("axes") or []:
        recos = []
        for r in axis.get("recommendations") or []:
            recos.append(
                {
                    "title": (r.get("title") or "").strip(),
                    "objectif": (r.get("objectif") or "").strip(),
                    "acteurs": (r.get("acteurs") or "").strip(),
                    "valeur": _clamp_score(r.get("valeur")),
                    "complexite": _clamp_score(r.get("complexite")),
                    "proposition_valeur": (r.get("proposition_valeur") or "").strip(),
                    "plan_actions": (r.get("plan_actions") or "").strip(),
                    "resultats_attendus": (r.get("resultats_attendus") or "").strip(),
                }
            )
        axes.append({"title": (axis.get("title") or "").strip(), "recommendations": recos})
    return axes


def generate_demo_recommendations(global_synthesis) -> list[dict]:
    """Fallback minimal hors-ligne (mode démo) — ne vise pas la pertinence,
    seulement à permettre de tester le parcours sans clé API."""
    return [
        {
            "title": "Axe démo — à affiner avec une vraie génération IA",
            "recommendations": [
                {
                    "title": "Explorer les points d'amélioration identifiés",
                    "objectif": (global_synthesis.points_amelioration or "").strip()[:300]
                    or "Aucun point d'amélioration détecté en mode démo.",
                    "acteurs": "À définir",
                    "valeur": 3,
                    "complexite": 3,
                    "proposition_valeur": (
                        "Recommandation générique de mode démo — activez une "
                        "vraie clé API pour une analyse réelle."
                    ),
                    "plan_actions": "- Atelier de partage des résultats de la synthèse",
                    "resultats_attendus": "- Alignement sur les priorités à traiter",
                }
            ],
        }
    ]
