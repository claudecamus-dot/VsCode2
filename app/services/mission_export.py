"""Export Markdown de l'ensemble des entretiens d'une mission, pour analyse
externe (évol) — matière brute (toutes réponses, tous thèmes, verbatims,
notes libres) suivie d'un gabarit de demande d'analyse calqué sur un rapport
de restitution réel : synthèse en 5 catégories fixes puis axes/fiches de
recommandation transverses, avec les champs Valeur/Complexité qui
alimenteront la matrice effort/valeur (un slide dédié) dans l'export PPT.

Les titres du gabarit (## / ### / #### / #####) sont un contrat de format
avec `analyse_import.py` : les conserver exactement permet la réintégration
automatique du résultat rempli en dehors de la plateforme.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import Mission
from ..routers.synthese import _all_theme_material, _total_answer_count


def _format_answer(row: dict) -> str:
    parts = [p for p in (row.get("value"), row.get("text")) if p]
    return " — ".join(parts)


def slugify(name: str) -> str:
    keep = [c.lower() if c.isalnum() else "_" for c in name.strip()]
    slug = "".join(keep).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "mission"


def build_export_markdown(mission: Mission) -> str:
    material_by_theme = _all_theme_material(mission)
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    lines: list[str] = [
        f"# Export d'entretiens — {mission.name}",
        "",
        f"_Généré le {now}._ {len(mission.interviews)} entretien(s), "
        f"{_total_answer_count(material_by_theme)} réponse(s).",
        "",
        "## Entretiens",
        "",
    ]

    for iv in mission.interviews:
        who = iv.interviewee_name
        if iv.interviewee_role:
            who += f" — {iv.interviewee_role}"
        if iv.interviewee_entity:
            who += f" ({iv.interviewee_entity})"
        lines.append(f"- **{who}**")
        if (iv.free_notes or "").strip():
            lines.append(f"  Notes libres : {iv.free_notes.strip()}")
    lines.append("")

    lines.append("## Matière par thème")
    lines.append("")
    for theme, by_question, verbatims in material_by_theme:
        if not by_question and not verbatims:
            continue
        lines.append(f"### {theme.title}")
        lines.append("")
        for q in theme.questions:
            rows = by_question.get(q.id) or []
            if not rows:
                continue
            lines.append(f"**{q.label}**")
            for r in rows:
                who = r["interviewee"]
                if r.get("role"):
                    who += f" ({r['role']})"
                lines.append(f"- {who} : {_format_answer(r)}")
            lines.append("")
        if verbatims:
            lines.append("**Verbatims**")
            for v in verbatims:
                lines.append(f"- « {v['quote']} » — {v['interviewee']}")
            lines.append("")

    lines += [
        "---",
        "",
        "# Demande d'analyse",
        "",
        "Merci d'analyser l'ensemble des entretiens ci-dessus et de compléter "
        "les sections suivantes, en conservant **exactement** les titres "
        "ci-dessous (`##`/`###`/`####`/`#####`) pour permettre une "
        "réintégration automatique dans la plateforme.",
        "",
        "## SYNTHÈSE GLOBALE",
        "",
        "### Contexte",
        "",
        "### Culture & ADN",
        "",
        "### Forces & succès",
        "",
        "### Points d'amélioration",
        "",
        "### Aspirations (baguette magique)",
        "",
        "## RECOMMANDATIONS",
        "",
        "Regroupe 3 à 4 axes **transverses** à la mission (pas un axe par "
        "thème d'entretien). Pour chaque recommandation, renseigne tous les "
        "champs ci-dessous — Valeur et Complexité alimenteront une matrice "
        "effort/valeur, restituée comme un slide dédié dans le PPT final. "
        "Répète le bloc `##### Recommandation` pour chaque recommandation, "
        "et le bloc `#### Axe` pour chaque axe.",
        "",
        "#### Axe 1 : <titre de l'axe>",
        "",
        "##### Recommandation 1.1 : <titre>",
        "- Objectif : ",
        "- Acteurs : ",
        "- Valeur (1-5) : ",
        "- Complexité (1-5) : ",
        "- Proposition de valeur : ",
        "- Plan d'actions : ",
        "- Résultats attendus : ",
        "",
        "##### Recommandation 1.2 : <titre>",
        "- Objectif : ",
        "- Acteurs : ",
        "- Valeur (1-5) : ",
        "- Complexité (1-5) : ",
        "- Proposition de valeur : ",
        "- Plan d'actions : ",
        "- Résultats attendus : ",
        "",
        "#### Axe 2 : <titre de l'axe>",
        "",
        "##### Recommandation 2.1 : <titre>",
        "- Objectif : ",
        "- Acteurs : ",
        "- Valeur (1-5) : ",
        "- Complexité (1-5) : ",
        "- Proposition de valeur : ",
        "- Plan d'actions : ",
        "- Résultats attendus : ",
        "",
    ]

    return "\n".join(lines)
