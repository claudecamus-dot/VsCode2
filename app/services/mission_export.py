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

import unicodedata
from datetime import datetime, timezone

from ..models import Mission
from ..routers.synthese import _all_theme_material, _total_answer_count


def _format_answer(row: dict) -> str:
    parts = [p for p in (row.get("value"), row.get("text")) if p]
    return " — ".join(parts)


def slugify(name: str) -> str:
    """Nom de fichier sûr, **ASCII strict**.

    L'ASCII n'est pas cosmétique : ces slugs partent dans l'en-tête
    `Content-Disposition`, que Starlette encode en latin-1 — un nom cyrillique
    ou CJK levait un `UnicodeEncodeError` APRÈS construction du document, donc
    un 500 sur des exports dont certains sont justement l'issue de secours
    quand tout le reste a échoué (revue adversariale 2026-07-30). `isalnum()`
    est vrai pour ces alphabets, ils traversaient donc le filtre intact ;
    la décomposition NFKD conserve au passage les accents français en les
    réduisant à leur lettre de base (« José » → « jose »)."""
    plat = unicodedata.normalize("NFKD", name.strip())
    plat = plat.encode("ascii", "ignore").decode("ascii")
    keep = [c.lower() if c.isalnum() else "_" for c in plat]
    slug = "".join(keep).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "mission"


def _rubriques_synthese(axes) -> list[str]:
    """Les rubriques `###` de la demande d'analyse — une par AXE de la mission
    (2026-07-27). Elles étaient écrites en dur : une mission qui avait ajouté un
    axe l'aurait vu absent du document envoyé à l'analyse externe, donc jamais
    rempli, donc jamais réimporté. Les titres doivent rester EXACTEMENT les
    libellés des axes : c'est sur eux que `analyse_import` reconnaît la
    rubrique au retour."""
    lignes: list[str] = []
    for axe in axes:
        lignes.append(f"### {axe.label}")
        if (axe.hint or "").strip():
            lignes.append(f"_{axe.hint.strip()}_")
        lignes.append("")
    return lignes


def build_export_markdown(mission: Mission, axes=None) -> str:
    # Import local : `interview_export` importe déjà `slugify` d'ici — au
    # niveau module, les deux se référenceraient circulairement.
    from .interview_export import group_turns_into_sections

    # `axes` optionnel : un appelant qui ne les passe pas (tests historiques)
    # obtient les 5 rubriques d'origine, document inchangé.
    if axes is None:
        from .synthese_ai import _axes_par_defaut

        axes = _axes_par_defaut()

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

    # Entretiens libres : aucune trame, donc aucune matière dans
    # `material_by_theme` — ils étaient purement et simplement ABSENTS d'un
    # export qui s'annonce « l'ensemble des entretiens ». On sort leur matière
    # brute (tours de parole), et surtout PAS leur répartition par catégorie :
    # les 5 catégories transverses ne se déduisent qu'en croisant tous les
    # entretiens, c'est précisément le travail demandé plus bas
    # (demande utilisateur 2026-07-27).
    libres = [iv for iv in mission.interviews if iv.mode == "libre" and iv.turns]
    if libres:
        lines += ["## Matière par entretien libre", ""]
        for iv in libres:
            lines += [f"### {iv.interviewee_name}", ""]
            if (iv.resume or "").strip():
                lines += [f"_Message central : {iv.resume.strip()}_", ""]
            for section in group_turns_into_sections(iv.turns):
                if section["title"]:
                    lines += [f"**{section['title']}**", ""]
                for turn in section["turns"]:
                    propos = " ".join(p for p in (turn.question, turn.remarque) if p)
                    lines.append(f"- {turn.interlocuteur} : {propos}")
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
        "réintégration automatique dans la plateforme. Le texte de consigne "
        "(ce paragraphe et les suivants situés juste après un titre `##`) "
        "n'est **pas** réimporté — seul le contenu placé sous les titres "
        "`###`/`####`/`#####` l'est : remplace-le par ton analyse, ne le "
        "laisse pas tel quel.",
        "",
        "## SYNTHÈSE GLOBALE",
        "",
        "Pour chaque rubrique ci-dessous, rédige une synthèse **courte et "
        "structurée en puces** (3 à 6 puces, une idée par puce) — pas un "
        "paragraphe continu. Appuie chaque point sur la matière d'entretien "
        "ci-dessus plutôt que sur une généralité, et quand c'est pertinent "
        "cite 1 à 2 **verbatims structurants** entre guillemets "
        "(« ... » — Prénom Nom) pour ancrer le point sur un propos réel qui "
        "résume bien une tension ou un constat partagé.",
        "",
        *_rubriques_synthese(axes),
        "## RECOMMANDATIONS",
        "",
        "Regroupe 3 à 4 axes **transverses** à la mission (pas un axe par "
        "thème d'entretien). Pour chaque recommandation, renseigne tous les "
        "champs ci-dessous — Valeur et Complexité alimenteront une matrice "
        "effort/valeur, restituée comme un slide dédié dans le PPT final. "
        "Répète le bloc `##### Recommandation` pour chaque recommandation, "
        "et le bloc `#### Axe` pour chaque axe.",
        "",
        "Avant le détail par axe, tu peux rédiger ici une **synthèse en "
        "3 à 5 puces** de l'ensemble des recommandations (priorités, ce "
        "qui revient le plus souvent) — utile à la lecture du document, "
        "mais uniquement les blocs `#### Axe`/`##### Recommandation` "
        "ci-dessous sont réintégrés automatiquement (l'application affiche "
        "déjà sa propre vue de synthèse, calculée depuis les fiches "
        "détaillées).",
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
