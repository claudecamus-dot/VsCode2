"""Export Markdown d'un entretien individuel (incr.9, US9.7) — à la
différence de `mission_export.build_export_markdown` (matière de toute une
mission, structurée par thème, point d'entrée du circuit export -> analyse
externe -> réimport), celui-ci restitue un seul entretien tel quel,
structuré ou libre, pour être partagé/archivé indépendamment de la mission.
Pas de gabarit de demande d'analyse en pied de document : ce n'est pas un
point d'entrée du circuit d'analyse externe.
"""
from __future__ import annotations

from ..models import Interview, InterviewTurn
from .mission_export import slugify

__all__ = [
    "build_interview_markdown",
    "group_turns_into_sections",
    "slugify",
    "transcript_from_turns",
    "transcript_of",
]

REPARTITION_LABELS = {
    "contexte": "Contexte",
    "culture_adn": "Culture & ADN",
    "forces_succes": "Forces / succès",
    "points_amelioration": "Points d'amélioration",
    "aspirations": "Aspirations",
}


def group_turns_into_sections(turns: list[InterviewTurn]) -> list[dict]:
    """Regroupe les tours de parole en sections thématiques : `section_title`
    est porté par le tour qui ouvre le sujet, hérité par les suivants (pas
    stocké de façon dénormalisée sur chaque tour, voir `InterviewTurn`).
    Partagé entre l'écran Analyse (`routers.interviews`) et cet export."""
    sections: list[dict] = []
    for turn in turns:
        if turn.section_title or not sections:
            sections.append({"title": turn.section_title, "turns": []})
        sections[-1]["turns"].append(turn)
    return sections


def transcript_from_turns(turns: list[InterviewTurn]) -> str:
    """Reconstitue un texte de transcription à partir des tours de parole.

    Sert de repli quand `Interview.raw_transcript` est absent : les entretiens
    créés avant que la transcription brute soit conservée (champ ajouté le
    2026-07-19), ceux importés depuis un `.docx`, et ceux dont le texte n'a
    jamais transité par l'écran d'enregistrement n'en ont pas — l'onglet
    Transcription s'affichait alors vide alors que le tour de table, lui, était
    rempli. Demande utilisateur 2026-07-27 : si le tour de table est renseigné,
    la transcription l'est aussi.

    Ce n'est pas le mot-à-mot d'origine (il est perdu, pas récupérable) mais la
    même matière remise à plat — l'appelant doit le dire à l'utilisateur.
    """
    lignes: list[str] = []
    for section in group_turns_into_sections(turns):
        if section["title"]:
            lignes.append(f"[{section['title']}]")
        for turn in section["turns"]:
            propos = " ".join(p for p in (turn.question, turn.remarque) if p)
            if not propos.strip():
                continue
            # Même repli que `_parse_turns_from_form` : un tour porte parfois du
            # contenu sans interlocuteur (identifié). Sans ce défaut la ligne
            # s'ouvrirait sur un « : » orphelin, ou sur « None » pour une
            # ligne d'avant la contrainte de colonne.
            lignes.append(f"{turn.interlocuteur or 'Intervenant'} : {propos}")
    return "\n\n".join(lignes)


def transcript_of(interview: Interview) -> tuple[str, bool]:
    """Transcription d'un entretien et son origine : `(texte, reconstitue)`.

    Une seule règle, partagée entre l'écran de consultation et l'export PDF —
    sinon l'onglet afficherait un texte que son propre bouton de
    téléchargement refuserait d'exporter."""
    brut = (interview.raw_transcript or "").strip()
    if brut:
        return brut, False
    return transcript_from_turns(interview.turns), True


def _header_lines(interview: Interview) -> list[str]:
    meta = [p for p in (interview.interviewee_role, interview.interviewee_entity) if p]
    sub = " — ".join(meta)
    if interview.interview_date:
        date_str = interview.interview_date.strftime("%d/%m/%Y")
        sub = f"{sub} · {date_str}" if sub else date_str
    lines = [f"# Entretien — {interview.interviewee_name}"]
    if sub:
        lines.append(f"_{sub}_")
    lines.append("")
    return lines


def _build_libre_body(interview: Interview) -> list[str]:
    lines: list[str] = []
    if (interview.resume or "").strip():
        lines += ["## Résumé", "", interview.resume.strip(), ""]

    lines.append("## Transcription structurée")
    lines.append("")
    sections = group_turns_into_sections(interview.turns)
    if not sections:
        lines += ["_Aucun tour de parole._", ""]
    for section in sections:
        if section["title"]:
            lines += [f"### {section['title']}", ""]
        for turn in section["turns"]:
            propos = " ".join(p for p in (turn.question, turn.remarque) if p)
            lines.append(f"**{turn.interlocuteur}** : {propos}")
        lines.append("")

    # Les 5 catégories transverses ne sont plus restituées par entretien
    # (demande utilisateur 2026-07-27) : elles n'ont de sens qu'une fois tous
    # les entretiens croisés et vivent dans la synthèse globale de mission.
    # La répartition reste calculée en coulisse — c'est la matière de cette
    # synthèse (`synthese._libre_material`) — mais n'est plus exportée ici.
    return lines


def _build_parametre_body(interview: Interview) -> list[str]:
    lines: list[str] = []
    if (interview.free_notes or "").strip():
        lines += ["## Notes libres", "", interview.free_notes.strip(), ""]

    answers = {a.question_id: a for a in interview.answers}
    verbatims_by_q: dict[int, list] = {}
    for v in interview.verbatims:
        verbatims_by_q.setdefault(v.question_id, []).append(v)

    themes = interview.mission.trame.themes if interview.mission.trame else []
    for theme in themes:
        lines += [f"## {theme.title}", ""]
        for q in theme.questions:
            a = answers.get(q.id)
            lines.append(f"**{q.label}**")
            if a and (a.value or a.text):
                if a.value:
                    lines.append(a.value)
                if a.text:
                    lines.append(a.text)
            else:
                lines.append("_— sans réponse —_")
            for v in verbatims_by_q.get(q.id, []):
                lines.append(f"> « {v.quote} »")
            lines.append("")
    return lines


def build_interview_markdown(interview: Interview) -> str:
    lines = _header_lines(interview)
    if interview.mode == "libre":
        lines += _build_libre_body(interview)
    else:
        lines += _build_parametre_body(interview)
    return "\n".join(lines).rstrip() + "\n"
