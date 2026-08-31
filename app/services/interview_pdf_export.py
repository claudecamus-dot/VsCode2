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
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.geomutils import normalizeTRBL
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from ..models import Interview
from .interview_export import group_turns_into_sections

logger = logging.getLogger(__name__)

__all__ = [
    "build_interview_pdf",
    "build_transcript_only_pdf",
    "build_turns_only_pdf",
    "build_synthese_only_pdf",
]

_NAVY_HEX = "#17324D"
_TEAL_HEX = "#008A92"
_NAVY = HexColor(_NAVY_HEX)
_TEAL = HexColor(_TEAL_HEX)
_BODY = HexColor("#30383F")
_MUTED = HexColor("#4B5D6B")
_CALLOUT_BG = HexColor("#FFF7E3")
_CALLOUT_BORDER = HexColor("#F3C969")
_CALLOUT_RULE = 2.5        # épaisseur du filet gauche de l'encadré, en points
_CALLOUT_PAD_V = 8         # respiration verticale du fond de l'encadré
_CALLOUT_PAD_H = 10        # retrait du texte de l'encadré par rapport au fond

# Grille — UNE seule marge gauche/droite pour tout ce qui se dessine sur la
# page. `SimpleDocTemplate` pose son `Frame` avec 6 pt de padding qu'il ne
# retranche d'aucune marge : le texte et les filets tombaient donc à 22,1 mm
# quand l'en-tête et le pied de page, dessinés au canvas, tombaient à 20,0 mm
# et l'encadré à 25,0 mm (trois bords gauche sur une même page, mesurés le
# 2026-08-31). On retire ce padding des marges du document pour que le bord du
# texte, celui des filets et celui des ornements coïncident tous sur `_MARGIN`.
_FRAME_PAD = 6
_MARGIN = 20 * mm
_MARGIN_V = 18 * mm

# Polices : les base-14 de PDF (Helvetica & co) ne savent coder que du
# Latin-1. La matière première de ce produit étant du texte collé depuis Teams,
# « Nguyễn Thị Mai » s'imprimait « NguyIn ThI Mai », « Иванов » « IIIIII »
# et « Δημήτρης » « ∆ηµIτρης » (glyphes Symbol) — sans exception ni
# avertissement (mesuré le 2026-08-31). On embarque donc une TrueType Unicode
# du poste ; à défaut on retombe sur Helvetica, mais `_text()` signale alors
# dans le log ce qui sera perdu, plutôt que de le perdre en silence.
_FONT_DIRS = (
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
    Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts",
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/Library/Fonts"),
)
# Familles candidates, par ordre de préférence : DejaVu couvre le latin étendu
# (vietnamien), le cyrillique et le grec ; Arial, présente sur tout poste
# Windows, sert de second choix. Chaque famille = (nom, (normal, gras,
# italique, gras italique)) — les 4 fontes sont exigées pour que `<b>`/`<i>`
# du mini-HTML de reportlab restent rendus dans la même famille.
_FONT_FAMILIES = (
    ("DejaVuSans", ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf",
                    "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf")),
    ("Arial", ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf")),
)


def _register_unicode_family() -> str | None:
    """Enregistre la première famille TrueType complète trouvée sur le poste et
    retourne son nom, ou `None` si aucune n'est disponible (le rendu retombe
    alors sur les base-14, limitées au Latin-1)."""
    for family, filenames in _FONT_FAMILIES:
        for directory in _FONT_DIRS:
            paths = [directory / name for name in filenames]
            if not all(path.is_file() for path in paths):
                continue
            faces = (family, f"{family}-Bold", f"{family}-Oblique", f"{family}-BoldOblique")
            try:
                for face, path in zip(faces, paths, strict=True):
                    pdfmetrics.registerFont(TTFont(face, str(path)))
            except Exception as exc:  # fonte illisible/corrompue : famille suivante
                logger.warning("Police %s inutilisable (%s) — famille suivante", family, exc)
                continue
            # Sans la famille, reportlab ne sait pas quelle fonte servir pour un
            # `<b>` à l'intérieur d'un paragraphe : il retomberait sur
            # Helvetica-Bold, donc sur du Latin-1, au milieu du texte.
            pdfmetrics.registerFontFamily(family, *faces)
            return family
    logger.warning(
        "Aucune police Unicode trouvée (%s) — repli sur Helvetica : les caractères "
        "hors Latin-1 ne seront pas rendus.",
        ", ".join(name for name, _ in _FONT_FAMILIES),
    )
    return None


_FAMILY = _register_unicode_family()
if _FAMILY:
    _FONT, _FONT_BOLD, _FONT_ITALIC = _FAMILY, f"{_FAMILY}-Bold", f"{_FAMILY}-Oblique"
    _RENDERABLE = frozenset(pdfmetrics.getFont(_FAMILY).face.charToGlyph)
else:
    _FONT, _FONT_BOLD, _FONT_ITALIC = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
    _RENDERABLE = frozenset(range(0x100))  # Latin-1, la limite des base-14

_STYLES = {
    "title": ParagraphStyle("Title", fontName=_FONT_BOLD, fontSize=20, leading=24,
                             textColor=_NAVY, spaceAfter=4),
    "subtitle": ParagraphStyle("Subtitle", fontName=_FONT_ITALIC, fontSize=11, leading=14,
                                textColor=_TEAL, spaceAfter=14),
    "h1": ParagraphStyle("H1", fontName=_FONT_BOLD, fontSize=14, leading=18,
                          textColor=_NAVY, spaceBefore=16, spaceAfter=2),
    "h2": ParagraphStyle("H2", fontName=_FONT_BOLD, fontSize=11.5, leading=15,
                          textColor=_TEAL, spaceBefore=10, spaceAfter=4),
    "dialogue": ParagraphStyle("Dialogue", fontName=_FONT, fontSize=10, leading=14,
                                textColor=_BODY, leftIndent=10, spaceAfter=7),
    "body": ParagraphStyle("Body", fontName=_FONT, fontSize=10, leading=14,
                            textColor=_BODY, spaceAfter=8),
    # Le fond ambré est porté par le STYLE (et non plus par un tableau) : c'est
    # ce qui rend l'encadré sécable entre deux pages. `borderPadding` en T-R-B-L
    # compense exactement `leftIndent`/`rightIndent`, de sorte que le fond couvre
    # toute la largeur utile — donc exactement le même bord gauche que le texte
    # courant — pendant que le texte reste en retrait à l'intérieur.
    "callout": ParagraphStyle("Callout", fontName=_FONT_ITALIC, fontSize=9.5, leading=13,
                               textColor=_BODY, backColor=_CALLOUT_BG,
                               leftIndent=_CALLOUT_PAD_H, rightIndent=_CALLOUT_PAD_H,
                               borderPadding=(_CALLOUT_PAD_V, _CALLOUT_PAD_H,
                                              _CALLOUT_PAD_V, _CALLOUT_PAD_H),
                               spaceBefore=_CALLOUT_PAD_V + 2, spaceAfter=_CALLOUT_PAD_V + 2),
    "muted": ParagraphStyle("Muted", fontName=_FONT_ITALIC, fontSize=9, leading=13,
                             textColor=_MUTED, spaceAfter=8),
}


def _text(raw: str) -> str:
    """Échappe le texte utilisateur pour le mini-XML de reportlab puis
    convertit les retours à la ligne en `<br/>` — un `Paragraph` reportlab
    traite le texte comme du HTML et collapse les `\\n` bruts en simple
    espace, donc une réponse ou une note libre saisie sur plusieurs lignes
    s'affichait comme un seul bloc continu dans le PDF sans cette conversion.

    Signale au passage les caractères que la police retenue ne sait pas
    dessiner (emoji, symboles) : reportlab les rend en blanc SANS lever ni
    avertir, et le consultant ne découvrait la perte qu'à la relecture du
    PDF — quand il la découvrait (mesuré le 2026-08-31)."""
    perdus = sorted({c for c in raw if ord(c) not in _RENDERABLE} - set("\n\r\t"))
    if perdus:
        logger.warning(
            "Export PDF : %d caractère(s) non rendable(s) par la police %s, "
            "absent(s) du document — %s",
            len(perdus), _FONT,
            ", ".join(f"{c!r} (U+{ord(c):04X})" for c in perdus),
        )
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


class _CalloutParagraph(Paragraph):
    """Encadré ambré SÉCABLE : un `Paragraph` — que reportlab coupe entre deux
    pages, chaque fragment redessinant son fond — plutôt qu'un `Table` d'une
    seule cellule, qui était insécable.

    Ce tableau faisait lever un `LayoutError` dès qu'un verbatim dépassait la
    hauteur d'une page — mesuré le 2026-08-31 : 5 016 caractères passaient,
    12 690 cassaient l'export et la route rendait un 500 nu au consultant. Il
    laissait aussi des pages à moitié vides (jusqu'à 249 mm de blanc) quand
    l'encadré, trop haut pour la place restante, partait entier à la page
    suivante. Le fond vient désormais du style ; seul le filet gauche — qui
    n'a pas d'équivalent natif au `LINEBEFORE` d'un tableau — est redessiné
    ici."""

    def draw(self):
        # Le fond et le texte d'abord (`Paragraph.drawPara`), le filet ensuite :
        # il se pose sur le bord gauche du fond, dans la zone de padding, sans
        # jamais mordre sur le texte.
        super().draw()
        top, _, bottom, left = normalizeTRBL(self.style.borderPadding)
        canvas = self.canv
        canvas.saveState()
        canvas.setStrokeColor(_CALLOUT_BORDER)
        canvas.setLineWidth(_CALLOUT_RULE)
        x = self.style.leftIndent - left + _CALLOUT_RULE / 2
        canvas.line(x, -bottom, x, self.height + top)
        canvas.restoreState()


def _callout(text: str, label: str = "") -> Paragraph:
    """Encadré ambré (fond + filet gauche) — repris du style "Callout" du
    document modèle, utilisé ici pour le résumé (message central à retenir,
    mis en avant plutôt que noyé dans le corps du texte). `label` optionnel :
    amorce en gras (« Message central — … »), comme les callouts du document
    de synthèse modèle (`02_Synthese…docx`).

    Aucune largeur codée en dur : le flowable prend la largeur utile du frame.
    L'ancien `colWidths=[160 * mm]` posait l'encadré à 25,0 mm du bord quand le
    texte courant tombait à 22,1 mm et le pied de page à 20,0 mm."""
    lead = f"<b>{_text(label)} — </b>" if label else ""
    return _CalloutParagraph(lead + _text(text), _STYLES["callout"])


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


def _resume_flowables(resume: str) -> list:
    """Section « Résumé » (encadré « Message central ») — vide si pas de
    résumé (écran wizard sans matière encore, ou entretien plus ancien)."""
    if not (resume or "").strip():
        return []
    return [*_h1("Résumé"), _callout(resume.strip(), label="Message central"), Spacer(1, 6)]


def _dialogue_flowables(turns) -> list:
    """Tours de parole groupés en sections — même mise en forme que le
    document modèle (`01_Transcription_editee…docx`) : le préfixe
    interlocuteur est **teal** quand le tour porte une question, **navy**
    sinon (motif vérifié par analyse du document le 2026-07-19 : sur les
    tours de style « Dialogue », 16/20 des préfixes teal sont des questions,
    71/72 des préfixes navy n'en sont pas — absent du rendu avant ce
    correctif, qui utilisait une seule couleur uniforme pour tous les tours).
    `turns` : `InterviewTurn` ORM ou tout objet exposant les mêmes attributs
    (`SimpleNamespace` pour les tours pas encore enregistrés, cf.
    `build_turns_only_pdf`)."""
    flowables: list = []
    sections = group_turns_into_sections(turns)
    if not sections:
        flowables.append(Paragraph("— Aucun tour de parole —", _STYLES["muted"]))
        return flowables
    for section in sections:
        turn_flowables = []
        if section["title"]:
            turn_flowables.append(Paragraph(_text(section["title"]), _STYLES["h2"]))
        for turn in section["turns"]:
            propos = " ".join(p for p in (turn.question, turn.remarque) if p)
            color = _TEAL_HEX if turn.question else _NAVY_HEX
            text = f'<font color="{color}"><b>{_text(turn.interlocuteur)}</b></font> : {_text(propos)}'
            turn_flowables.append(Paragraph(text, _STYLES["dialogue"]))
        # Garde le titre de section collé à son premier tour de parole plutôt
        # que de le laisser seul en bas de page (saut de page malvenu).
        flowables.append(KeepTogether(turn_flowables[:2]) if turn_flowables else Spacer(0, 0))
        flowables.extend(turn_flowables[2:])
    return flowables


def _libre_body_flowables(interview: Interview) -> list:
    flowables = _resume_flowables(interview.resume)
    flowables += _h1("Transcription structurée")
    flowables.append(Paragraph(
        "Structurée par IA depuis un entretien libre — pas un verbatim mot à "
        "mot, à vérifier contre l'enregistrement en cas de doute.",
        _STYLES["muted"],
    ))
    flowables += _dialogue_flowables(interview.turns)
    # Les 5 catégories transverses ne sont plus restituées par entretien
    # (demande utilisateur 2026-07-27) — elles vivent dans la synthèse globale
    # de mission, qui croise tous les entretiens.
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
            canvas.setFont(_FONT_BOLD, 8)
            canvas.setFillColor(_MUTED)
            canvas.drawString(_MARGIN, A4[1] - 12 * mm, running_title)
            canvas.setStrokeColor(_CALLOUT_BORDER)
            canvas.setLineWidth(0.5)
            canvas.line(_MARGIN, A4[1] - 14 * mm, A4[0] - _MARGIN, A4[1] - 14 * mm)
        # Pied de page sur toutes les pages.
        canvas.setFont(_FONT, 8)
        canvas.setFillColor(_MUTED)
        canvas.drawString(_MARGIN, 10 * mm, "Export entretien — Interview-to-Deck")
        canvas.drawRightString(A4[0] - _MARGIN, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return decorate


_AUTHOR = "Interview-to-Deck"


class _InterviewDoc(SimpleDocTemplate):
    """`SimpleDocTemplate` + signets : chaque titre de niveau 1 devient une
    entrée du panneau de navigation du lecteur PDF. Un export d'entretien
    dépasse couramment la dizaine de pages et n'en proposait aucune (mesuré
    le 2026-08-31 : ni signet, ni titre, ni auteur, ni langue de document)."""

    def afterFlowable(self, flowable) -> None:
        if isinstance(flowable, Paragraph) and flowable.style.name == "H1":
            key = f"h1-{self.page}-{id(flowable)}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.getPlainText(), key, level=0)


def _document(buffer: io.BytesIO, title: str) -> _InterviewDoc:
    """Document A4 commun aux 4 exports — une seule définition de la grille
    et des métadonnées, pour qu'elles ne divergent pas d'un builder à l'autre.

    Les marges sont amputées du padding que `SimpleDocTemplate` impose à son
    `Frame` : le texte et les filets retombent ainsi exactement sur `_MARGIN`,
    où l'en-tête et le pied de page sont déjà dessinés au canvas."""
    return _InterviewDoc(
        buffer, pagesize=A4,
        topMargin=_MARGIN_V - _FRAME_PAD, bottomMargin=_MARGIN_V - _FRAME_PAD,
        leftMargin=_MARGIN - _FRAME_PAD, rightMargin=_MARGIN - _FRAME_PAD,
        title=title, author=_AUTHOR, creator=_AUTHOR,
        subject="Entretien qualitatif — restitution",
        lang="fr-FR", displayDocTitle=True,
        # Sans `initialFontName`, reportlab écrit un préambule « BT /F1 12 Tf »
        # sur CHAQUE page : le PDF déclare alors Helvetica, police NON embarquée,
        # alors qu'aucun caractère ne l'utilise — le rendu redevient dépendant de
        # la machine du lecteur. Une TTF étant « dynamique » chez reportlab, le
        # préambule disparaît dès qu'on la désigne ici.
        initialFontName=_FONT, initialFontSize=10,
    )


def build_interview_pdf(interview: Interview) -> bytes:
    """Retourne les octets d'un PDF A4 restituant un entretien (même matière
    que `build_interview_markdown()`), typeset façon transcription éditée."""
    buffer = io.BytesIO()
    running_title = f"Entretien — {interview.interviewee_name}"
    doc = _document(buffer, running_title)
    flowables = _header_flowables(interview)
    if interview.mode == "libre":
        flowables += _libre_body_flowables(interview)
    else:
        flowables += _parametre_body_flowables(interview)
    decorate = _page_decorator(running_title)
    doc.build(flowables, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()


SECOURS_SUBTITLE = (
    "Export de secours — l'extraction IA n'a pas (encore) abouti sur ce "
    "texte, qui reste disponible tel qu'enregistré ci-dessous."
)


def build_transcript_only_pdf(
    transcript: str, interviewee_name: str = "", subtitle: str = SECOURS_SUBTITLE,
) -> bytes:
    """PDF contenant uniquement une transcription — sans passer par un
    `Interview` enregistré en base (2026-07-19).

    Née comme export de SECOURS : quand l'extraction IA en aval (tours de
    parole, réponses, répartition) échoue, un texte transcrit — parfois issu
    d'un entretien d'1h ou plus — restait sinon bloqué dans le formulaire
    d'erreur, sans autre issue que de le recopier à la main. D'où le
    sous-titre par défaut.

    Depuis le 2026-07-27 elle sert aussi l'onglet « Transcription » d'un
    entretien enregistré, où ce sous-titre serait faux (rien n'a échoué) :
    l'appelant passe alors le sien. Le `title`/`running_title` reprend le nom
    de l'interviewé·e si connu, sinon un libellé générique."""
    title = f"Transcription brute — {interviewee_name}" if interviewee_name.strip() else "Transcription brute"
    buffer = io.BytesIO()
    doc = _document(buffer, title)
    flowables = [
        Paragraph(_text(title), _STYLES["title"]),
        Paragraph(_text(subtitle), _STYLES["subtitle"]),
    ]
    for paragraph in (transcript or "").strip().split("\n\n"):
        if paragraph.strip():
            flowables.append(Paragraph(_text(paragraph.strip()), _STYLES["body"]))
    if not flowables[2:]:
        flowables.append(Paragraph("— Transcription vide —", _STYLES["muted"]))
    decorate = _page_decorator(title)
    doc.build(flowables, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()


def build_turns_only_pdf(turns: list[dict], interviewee_name: str = "") -> bytes:
    """PDF des tours de parole d'un entretien libre pas encore enregistré —
    écran « Revue des questions/réponses » du wizard (2026-07-19), avant
    confirmation finale. Même mise en forme que la section « Transcription
    structurée » de `build_interview_pdf()` (mode libre) : `_dialogue_flowables()`
    est factorisée entre les deux pour ne jamais diverger.

    `turns` : liste de dicts (`interlocuteur`/`question`/`remarque`/
    `section_title`), la forme produite par le formulaire de revue du wizard
    — convertie en objets (`SimpleNamespace`) pour l'accès par attribut
    qu'attend `group_turns_into_sections()`, pas des `InterviewTurn` ORM
    puisque rien n'est encore enregistré à ce stade."""
    title = f"Tours de parole — {interviewee_name}" if interviewee_name.strip() else "Tours de parole"
    buffer = io.BytesIO()
    doc = _document(buffer, title)
    flowables = [Paragraph(_text(title), _STYLES["title"])]
    flowables += _dialogue_flowables([SimpleNamespace(**t) for t in turns])
    decorate = _page_decorator(title)
    doc.build(flowables, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()


def build_synthese_only_pdf(resume: str, repartition: dict | None = None, interviewee_name: str = "") -> bytes:
    """PDF du résumé d'un entretien libre pas encore enregistré — écran
    « Synthèse avant enregistrement » du wizard (2026-07-19), avant
    confirmation finale. Même mise en forme que la matière équivalente de
    `build_interview_pdf()` (mode libre) : `_resume_flowables()` est
    factorisée entre les deux — encadré « Message central » du document
    modèle (`02_Synthese_session_3…docx`) repris à l'identique.

    `repartition` n'est plus rendue (demande utilisateur 2026-07-27 : les 5
    catégories transverses ne se restituent qu'au niveau mission) ; le
    paramètre reste accepté pour ne pas casser les appelants."""
    title = f"Synthèse — {interviewee_name}" if interviewee_name.strip() else "Synthèse"
    buffer = io.BytesIO()
    doc = _document(buffer, title)
    flowables = [Paragraph(_text(title), _STYLES["title"])]
    flowables += _resume_flowables(resume)
    decorate = _page_decorator(title)
    doc.build(flowables, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()
