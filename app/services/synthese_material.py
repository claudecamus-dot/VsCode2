"""Matière (réponses + verbatims + répartition libre) dérivée des entretiens
d'une mission — pure lecture ORM, aucun appel IA. Service partagé par
`routers/synthese.py` (génération/aperçu), `routers/export.py` (SWOT,
executive summary, difficultés) et `services/mission_export.py` (export
markdown), qui les importaient jusqu'au 2026-09-04 comme symboles PRIVÉS de
`routers/synthese.py` — un couplage inversé service→router relevé par
l'audit technique (categorie risque_technique, finding
"routers/synthese.py = service déguisé") : `mission_export.py` ne pouvait pas
être testé/réutilisé sans charger tout le router. Extrait ici sans
changement de logique.
"""
from __future__ import annotations

from ..models import Mission, Theme


def theme_material(mission: Mission, theme: Theme) -> tuple[dict, list]:
    """Réponses (par question) et verbatims du thème, tous entretiens confondus."""
    qids = {q.id for q in theme.questions}
    by_question: dict[int, list[dict]] = {}
    verbatims: list[dict] = []
    for iv in mission.interviews:
        ans = {a.question_id: a for a in iv.answers}
        for q in theme.questions:
            a = ans.get(q.id)
            content = a and ((a.text or "").strip() or (a.value or "").strip())
            if content:
                by_question.setdefault(q.id, []).append(
                    {
                        "interviewee": iv.interviewee_name,
                        "role": iv.interviewee_role,
                        "text": (a.text or "").strip(),
                        "value": (a.value or "").strip(),
                    }
                )
        for v in iv.verbatims:
            if v.question_id in qids:
                verbatims.append(
                    {"interviewee": iv.interviewee_name, "quote": v.quote}
                )
    return by_question, verbatims


def answer_count(by_question: dict[int, list[dict]]) -> int:
    return sum(len(v) for v in by_question.values())


def all_theme_material(mission: Mission) -> list[tuple[Theme, dict, list]]:
    """Matière (réponses + verbatims) de tous les thèmes de la trame — pour
    la synthèse globale, qui recoupe l'ensemble de la mission plutôt qu'un
    seul thème. Une mission brouillon née d'un entretien libre (incr.9) n'a
    pas de trame du tout."""
    if mission.trame is None:
        return []
    return [
        (theme, *theme_material(mission, theme)) for theme in mission.trame.themes
    ]


def libre_material(mission: Mission) -> list[tuple]:
    """Répartition (5 catégories) de chaque entretien en mode libre (incr.9,
    US9.6) — matière indépendante des thèmes, injectée à côté de
    `material_by_theme` dans `generate_global_synthesis`."""
    return [
        (iv, iv.repartition) for iv in mission.interviews
        if iv.mode == "libre" and iv.repartition
    ]


def total_answer_count(material_by_theme: list[tuple[Theme, dict, list]]) -> int:
    return sum(answer_count(by_question) for _theme, by_question, _v in material_by_theme)
