"""Export PDF d'un entretien individuel (incr.9) — même matière que
`interview_export.build_interview_markdown()` (texte brut, pour archive/
réimport), mais typeset pour être lu/partagé directement : le consultant a
fourni un exemple de transcription éditée (`tests/exemple/01_Transcription_
editee_session_IA_BizDev_10-07_PM_corrige.docx`) comme référence de mise en
forme. Palette/échelle typographique reprises de ce document (extraites via
python-docx : Titre bleu marine, sous-titres teal, tours de parole en retrait,
encadré "callout" ambré pour le résumé) — Helvetica plutôt qu'Arial (police
standard PDF, pas de police à embarquer, rendu visuellement très proche).

reportlab plutôt que weasyprint/wkhtmltopdf : wheel pure Python, aucune
dépendance système (Pango/Cairo/wkhtmltopdf) à installer sur le poste du
consultant — cohérent avec `pptx_deck.py` qui construit déjà des documents
programmatiquement plutôt que de convertir du HTML.
"""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models import Interview
from .interview_export import REPARTITION_LABELS, group_turns_into_sections

__all__ = ["build_interview_pdf"]

_NAVY = HexColor("#17324D")
_TEAL = HexColor("#008A92")
_BODY = HexColor("#30383F")
_MUTED = HexColor("#4B5D6B")
_CALLOUT_BG = HexColor("#FFF7E3")
_CALLOUT_BORDER = HexColor("#F3C969")

_STYLES = {
    "title": ParagraphStyle("Title", fontName="Helvetica-Bold", fontSize=20, leading=24,
                             textColor=_NAVY, spaceAfter=4),
    "subtitle": ParagraphStyle("Subtitle", fontName="Helvetica-Oblique", fontSize=11, leading=14,
                                textColor=_TEAL, spaceAfter=14),
    "h1": ParagraphStyle("H1", fontName="Helvetica-Bold", fontSize=14, leading=18,
                          textColor=_NAVY, spaceBefore=16, spaceAfter=2),
    "h2": ParagraphStyle("H2", fontName="Helvetica-Bold", fontSize=11.5, leading=15,
                          textColor=_TEAL, spaceBefore=10, spaceAfter=4),
    "dialogue": ParagraphStyle("Dialogue", fontName="Helvetica", fontSize=10, leading=14,
                                textColor=_BODY, leftIndent=10, spaceAfter=7),
    "body": ParagraphStyle("Body", fontName="Helvetica", fontSize=10, leading=14,
                            textColor=_BODY, spaceAfter=8),
    "callout": ParagraphStyle("Callout", fontName="Helvetica-Oblique", fontSize=9.5, leading=13,
                               textColor=_BODY),
    "muted": ParagraphStyle("Muted", fontName="Helvetica-Oblique", fontSize=9, leading=13,
                             textColor=_MUTED, spaceAfter=8),
}


def _text(raw: str) -> str:
    """Échappe le texte utilisateur pour le mini-XML de reportlab puis
    convertit les retours à la ligne en `<br/>` — un `Paragraph` reportlab
    traite le texte comme du HTML et collapse les `\\n` bruts en simple
    espace, donc une réponse ou une note libre saisie sur plusieurs lignes
    s'affichait comme un seul bloc continu dans le PDF sans cette conversion."""
    return escape(raw).replace("\n", "<br/>")


def _h1(text: str) -> list:
    """Titre de niveau 1 suivi d'un filet teal pleine largeur — repris de la
    mise en forme des titres du document modèle (`01_Transcription…docx`), où
    chaque grande section est soulignée d'un trait de couleur."""
    return [
        Paragraph(_text(text), _STYLES["h1"]),
        HRFlowable(width="100%", thickness=1.5, color=_TEAL,
                   spaceBefore=2, spaceAfter=8, lineCap="round"),
    ]


def _callout(text: str, label: str = "") -> Table:
    """Encadré ambré (fond + filet gauche) — repris du style "Callout" du
    document modèle, utilisé ici pour le résumé (message central à retenir,
    mis en avant plutôt que noyé dans le corps du texte). `label` optionnel :
    amorce en gras (« Message central — … »), comme les callouts du document
    de synthèse modèle (`02_Synthese…docx`)."""
    lead = f"<b>{_text(label)} — </b>" if label else ""
    p = Paragraph(lead + _text(text), _STYLES["callout"])
    table = Table([[p]], colWidths=[160 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _CALLOUT_BG),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, _CALLOUT_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def _header_flowables(interview: Interview) -> list:
    flowables = [Paragraph(_text(f"Entretien — {interview.interviewee_name}"), _STYLES["title"])]
    meta = [p for p in (interview.interviewee_role, interview.interviewee_entity) if p]
    sub = " — ".join(meta)
    if interview.interview_date:
        date_str = interview.interview_date.strftime("%d/%m/%Y")
        sub = f"{sub} · {date_str}" if sub else date_str
    if sub:
        flowables.append(Paragraph(_text(sub), _STYLES["subtitle"]))
    return flowables


def _libre_body_flowables(interview: Interview) -> list:
    flowables: list = []
    if (interview.resume or "").strip():
        flowables += _h1("Résumé")
        flowables.append(_callout(interview.resume.strip(), label="Message central"))
        flowables.append(Spacer(1, 6))

    flowables += _h1("Transcription structurée")
    flowables.append(Paragraph(
        "Structurée par IA depuis un entretien libre — pas un verbatim mot à "
        "mot, à vérifier contre l'enregistrement en cas de doute.",
        _STYLES["muted"],
    ))
    sections = group_turns_into_sections(interview.turns)
    if not sections:
        flowables.append(Paragraph("— Aucun tour de parole —", _STYLES["muted"]))
    for section in sections:
        turn_flowables = []
        if section["title"]:
            turn_flowables.append(Paragraph(_text(section["title"]), _STYLES["h2"]))
        for turn in section["turns"]:
            propos = " ".join(p for p in (turn.question, turn.remarque) if p)
            text = f"<b>{_text(turn.interlocuteur)}</b> : {_text(propos)}"
            turn_flowables.append(Paragraph(text, _STYLES["dialogue"]))
        # Garde le titre de section collé à son premier tour de parole plutôt
        # que de le laisser seul en bas de page (saut de page malvenu).
        flowables.append(KeepTogether(turn_flowables[:2]) if turn_flowables else Spacer(0, 0))
        flowables.extend(turn_flowables[2:])

    repartition = interview.repartition or {}
    flowables += _h1("Répartition par catégorie")
    for key, label in REPARTITION_LABELS.items():
        value = (repartition.get(key) or "").strip()
        flowables.append(Paragraph(_text(label), _STYLES["h2"]))
        flowables.append(Paragraph(
            _text(value) if value else "— pas de matière sur cette catégorie —",
            _STYLES["body"] if value else _STYLES["muted"],
        ))
    return flowables


def _parametre_body_flowables(interview: Interview) -> list:
    flowables: list = []
    if (interview.free_notes or "").strip():
        flowables += _h1("Notes libres")
        flowables.append(Paragraph(_text(interview.free_notes.strip()), _STYLES["body"]))

    answers = {a.question_id: a for a in interview.answers}
    verbatims_by_q: dict[int, list] = {}
    for v in interview.verbatims:
        verbatims_by_q.setdefault(v.question_id, []).append(v)

    themes = interview.mission.trame.themes if interview.mission.trame else []
    for theme in themes:
        flowables += _h1(theme.title)
        for q in theme.questions:
            a = answers.get(q.id)
            flowables.append(Paragraph(f"<b>{_text(q.label)}</b>", _STYLES["h2"]))
            if a and (a.value or a.text):
                if a.value:
                    flowables.append(Paragraph(_text(a.value), _STYLES["body"]))
                if a.text:
                    flowables.append(Paragraph(_text(a.text), _STYLES["body"]))
            else:
                flowables.append(Paragraph("— sans réponse —", _STYLES["muted"]))
            for v in verbatims_by_q.get(q.id, []):
                flowables.append(_callout(f"« {v.quote} »"))
                flowables.append(Spacer(1, 4))

    if (interview.raw_transcript or "").strip():
        flowables += _h1("Transcription brute")
        flowables.append(Paragraph(
            "Texte tel qu'enregistré, avant extraction IA des réponses ci-dessus "
            "— à consulter en cas de doute sur une réponse ou pour retrouver du "
            "contexte non repris dans les questions de la trame.",
            _STYLES["muted"],
        ))
        for paragraph in interview.raw_transcript.strip().split("\n\n"):
            if paragraph.strip():
                flowables.append(Paragraph(_text(paragraph.strip()), _STYLES["body"]))
    return flowables


def _page_decorator(running_title: str):
    """Fabrique le callback `onPage` de reportlab : filet + titre courant en
    en-tête (à partir de la 2ᵉ page, pour ne pas doubler le grand titre de la
    1ʳᵉ page) et pied de page « Page N » — convention du document modèle."""
    def decorate(canvas, doc):
        canvas.saveState()
        # En-tête courant, seulement à partir de la page 2 (la page 1 porte
        # déjà le grand titre de l'entretien).
        if doc.page > 1:
            canvas.setFont("Helvetica-Bold", 8)
            canvas.setFillColor(_MUTED)
            canvas.drawString(20 * mm, A4[1] - 12 * mm, running_title)
            canvas.setStrokeColor(_CALLOUT_BORDER)
            canvas.setLineWidth(0.5)
            canvas.line(20 * mm, A4[1] - 14 * mm, A4[0] - 20 * mm, A4[1] - 14 * mm)
        # Pied de page sur toutes les pages.
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(_MUTED)
        canvas.drawString(20 * mm, 10 * mm, "Export entretien — Interview-to-Deck")
        canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return decorate


def build_interview_pdf(interview: Interview) -> bytes:
    """Retourne les octets d'un PDF A4 restituant un entretien (même matière
    que `build_interview_markdown()`), typeset façon transcription éditée."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
    )
    flowables = _header_flowables(interview)
    if interview.mode == "libre":
        flowables += _libre_body_flowables(interview)
    else:
        flowables += _parametre_body_flowables(interview)
    decorate = _page_decorator(f"Entretien — {interview.interviewee_name}")
    doc.build(flowables, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()


def build_transcript_only_pdf(transcript: str, interviewee_name: str = "") -> bytes:
    """PDF de secours contenant uniquement une transcription brute — sans
    passer par un `Interview` enregistré en base (2026-07-19).

    Utilisée quand l'extraction IA en aval (tours de parole, réponses,
    répartition) échoue : avant cette fonction, un texte transcrit — parfois
    issu d'un entretien d'1h ou plus — restait bloqué dans le formulaire
    d'erreur sans aucune façon de le récupérer autrement qu'en le
    resélectionnant/copiant à la main. Le `title`/`running_title` reprend le
    nom de l'interviewé·e si connu, sinon un libellé générique."""
    title = f"Transcription brute — {interviewee_name}" if interviewee_name.strip() else "Transcription brute"
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
    )
    flowables = [
        Paragraph(_text(title), _STYLES["title"]),
        Paragraph(
            "Export de secours — l'extraction IA n'a pas (encore) abouti sur ce "
            "texte, qui reste disponible tel qu'enregistré ci-dessous.",
            _STYLES["subtitle"],
        ),
    ]
    for paragraph in (transcript or "").strip().split("\n\n"):
        if paragraph.strip():
            flowables.append(Paragraph(_text(paragraph.strip()), _STYLES["body"]))
    if not flowables[2:]:
        flowables.append(Paragraph("— Transcription vide —", _STYLES["muted"]))
    decorate = _page_decorator(title)
    doc.build(flowables, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()
