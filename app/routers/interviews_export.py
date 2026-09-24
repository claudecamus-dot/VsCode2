"""Aperçu, exports (Markdown/PDF) et clôture d'un entretien.

Extrait mécaniquement de `interviews.py` (2026-09-23, constat d'audit
risque technique : 3385 lignes / 56 routes). Code déplacé tel quel ; URL,
ordre d'enregistrement des routes et nom de logger inchangés.
"""
from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import (
    Verbatim,
)
from ..services.interview_export import (
    build_interview_markdown,
    slugify,
    transcript_of,
)
from ..services.interview_pdf_export import (
    build_interview_pdf,
    build_synthese_only_pdf,
    build_transcript_only_pdf,
    build_turns_only_pdf,
)
from ..templating import templates
from .interviews_commun import (
    _coverage,
    _get_interview,
    _parse_repartition,
)
from .interviews_libre import (
    _parse_turns_from_form,
)

# Même nom de logger qu'avant le découpage : filtres et tests s'y accrochent.
logger = logging.getLogger("app.routers.interviews")

router = APIRouter(tags=["interviews"])

# --------------------------------------------------------------------------- #
# Aperçu lecture seule : toutes les questions/réponses d'un coup, pour une
# relecture complète rapide (évol) — pas de saisie possible ici, contrairement
# à la capture qui n'affiche qu'un thème à la fois.
# --------------------------------------------------------------------------- #
@router.get("/interviews/{interview_id}/preview")
def preview(interview_id: int, request: Request, db: Session = Depends(get_session)):
    interview = _get_interview(db, interview_id)
    answers = {a.question_id: a for a in interview.answers}
    verbatims_by_q: dict[int, list[Verbatim]] = {}
    for v in interview.verbatims:
        verbatims_by_q.setdefault(v.question_id, []).append(v)
    answered, total = _coverage(interview)

    return templates.TemplateResponse(
        request,
        "interviews/preview.html",
        {
            "interview": interview,
            "themes": interview.mission.trame.themes if interview.mission.trame else [],
            "answers": answers,
            "verbatims_by_q": verbatims_by_q,
            "answered": answered,
            "total": total,
        },
    )


# --------------------------------------------------------------------------- #
# Export Markdown d'un entretien (incr.9, US9.7) — un seul entretien,
# structuré ou libre, à la différence de l'export mission-wide
# (`export.py::export_interviews`) qui agrège tous les entretiens d'une
# mission pour le circuit export -> analyse externe -> réimport.
# --------------------------------------------------------------------------- #
@router.get("/interviews/{interview_id}/export/markdown")
def export_interview_markdown(interview_id: int, db: Session = Depends(get_session)):
    interview = _get_interview(db, interview_id)
    content = build_interview_markdown(interview)
    filename = f"entretien_{slugify(interview.interviewee_name)}.md"
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/interviews/{interview_id}/export/pdf")
def export_interview_pdf(interview_id: int, db: Session = Depends(get_session)):
    """Même matière que l'export Markdown ci-dessus, mais typeset (US9.20) —
    voir `interview_pdf_export.py` pour la mise en forme (inspirée d'un
    exemple de transcription éditée fourni par l'utilisateur).

    Un échec de mise en page ne doit jamais coûter sa matière au consultant :
    jusqu'au 2026-08-31 un verbatim plus haut qu'une page faisait lever
    reportlab et la route rendait un 500 nu (`text/plain`, corps
    « Internal Server Error »), sans autre issue que de retourner recopier le
    texte. La cause de fond est corrigée dans `interview_pdf_export.py`, mais
    la mise en page reste le maillon fragile : on retombe donc sur l'export de
    secours — même contenu, typographie minimale — plutôt que sur rien."""
    interview = _get_interview(db, interview_id)
    try:
        content = build_interview_pdf(interview)
    except Exception:
        logger.exception(
            "Mise en page PDF impossible pour l'entretien %s — repli sur l'export de secours",
            interview_id,
        )
        try:
            content = build_transcript_only_pdf(
                build_interview_markdown(interview),
                interview.interviewee_name,
                subtitle=(
                    "Export de secours — la mise en page complète a échoué sur cet "
                    "entretien ; son contenu est restitué ici en texte simple."
                ),
            )
        except Exception as exc:
            logger.exception("Export de secours PDF impossible pour l'entretien %s", interview_id)
            raise HTTPException(
                status_code=500,
                detail="Export PDF impossible pour cet entretien.",
            ) from exc
    filename = f"entretien_{slugify(interview.interviewee_name)}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/interviews/{interview_id}/export/transcription/pdf")
def export_interview_transcription_pdf(interview_id: int, db: Session = Depends(get_session)):
    """PDF de la seule TRANSCRIPTION d'un entretien — bouton de l'onglet
    « Transcription » de la consultation.

    Rendait jusqu'ici les tours de parole (`build_turns_only_pdf`), c'est-à-dire
    exactement le même document que le bouton « tour de table » de l'onglet
    voisin : deux boutons distincts, un seul contenu (constat utilisateur
    2026-07-27). Il rend désormais le texte que l'onglet affiche —
    `transcript_of()` tient la règle commune aux deux, transcription brute si
    elle a été conservée, sinon reconstitution depuis le tour de table."""
    interview = _get_interview(db, interview_id)
    transcript, reconstitue = transcript_of(interview)
    if not transcript.strip():
        # Comme les exports POST frères (turns/synthese) : sans transcription
        # brute NI tour de table, il n'y a rien à rendre — 400 plutôt qu'un PDF
        # au titre seul.
        raise HTTPException(
            status_code=400,
            detail="Cet entretien n'a ni transcription ni tour de parole à exporter.",
        )
    # Le sous-titre par défaut de ce builder annonce un export de SECOURS après
    # échec IA — faux ici, où rien n'a échoué : on dit d'où vient le texte.
    content = build_transcript_only_pdf(
        transcript,
        interview.interviewee_name,
        subtitle=(
            "Reconstituée à partir du tour de table — le mot-à-mot d'origine "
            "n'a pas été conservé pour cet entretien."
            if reconstitue else
            "Texte tel que transcrit à l'enregistrement, avant structuration par l'IA."
        ),
    )
    filename = f"transcription_{slugify(interview.interviewee_name)}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/interviews/transcript/export-pdf")
def export_transcript_only_pdf(
    transcript: str = Form(""),
    interviewee_name: str = Form(""),
):
    """Export PDF de secours d'une transcription pas encore enregistrée
    (aucun `Interview` en base) — bouton affiché sur les 3 écrans où
    l'extraction IA en aval peut échouer (`record.html`, `record_libre.html`,
    `libre_turns_review.html`) pour ne pas laisser le texte transcrit
    bloqué dans un formulaire sans autre issue que de le ressaisir."""
    if not transcript.strip():
        raise HTTPException(status_code=400, detail="Transcription vide — rien à exporter.")
    content = build_transcript_only_pdf(transcript, interviewee_name)
    slug = slugify(interviewee_name) if interviewee_name.strip() else "brute"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="transcription_{slug}.pdf"'},
    )


@router.post("/interviews/turns/export-pdf")
def export_turns_only_pdf(
    interviewee_name: str = Form(""),
    turn_interlocuteur: list[str] = Form([]),
    turn_question: list[str] = Form([]),
    turn_remarque: list[str] = Form([]),
    turn_section_title: list[str] = Form([]),
):
    """Export PDF des tours de parole pas encore enregistrés — bouton sur
    l'écran « Revue des questions/réponses » du wizard libre (2026-07-19),
    façon `01_Transcription_editee…docx`."""
    turns = _parse_turns_from_form(
        turn_interlocuteur, turn_question, turn_remarque, turn_section_title
    )
    if not turns:
        raise HTTPException(status_code=400, detail="Aucun tour de parole — rien à exporter.")
    content = build_turns_only_pdf(turns, interviewee_name)
    slug = slugify(interviewee_name) if interviewee_name.strip() else "brute"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="tours_{slug}.pdf"'},
    )


@router.post("/interviews/synthese/export-pdf")
def export_synthese_only_pdf(
    interviewee_name: str = Form(""),
    resume: str = Form(""),
    repartition_json: str = Form(""),
    # Les 5 champs nommés d'avant les axes configurables (2026-07-27) : gardés
    # en repli pour qu'un formulaire déjà ouvert dans un onglet du navigateur
    # (ou un test historique) continue de poster une répartition valide.
    repartition_contexte: str = Form(""),
    repartition_culture_adn: str = Form(""),
    repartition_forces_succes: str = Form(""),
    repartition_points_amelioration: str = Form(""),
    repartition_aspirations: str = Form(""),
):
    """Export PDF du résumé + de la répartition pas encore enregistrés —
    bouton sur l'écran « Synthèse avant enregistrement » du wizard libre
    (2026-07-19), façon `02_Synthese_session_3…docx`."""
    repartition = _parse_repartition(
        repartition_json,
        (repartition_contexte, repartition_culture_adn, repartition_forces_succes,
         repartition_points_amelioration, repartition_aspirations),
    )
    if not resume.strip() and not any(repartition.values()):
        raise HTTPException(status_code=400, detail="Synthèse vide — rien à exporter.")
    content = build_synthese_only_pdf(resume, repartition, interviewee_name)
    slug = slugify(interviewee_name) if interviewee_name.strip() else "brute"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="synthese_{slug}.pdf"'},
    )


# --------------------------------------------------------------------------- #
# Fin d'entretien : récap de couverture
# --------------------------------------------------------------------------- #
@router.get("/interviews/{interview_id}/finish")
def finish_view(interview_id: int, request: Request, db: Session = Depends(get_session)):
    interview = _get_interview(db, interview_id)
    answers = {a.question_id: a for a in interview.answers}
    missed = []  # questions non répondues (zappées / à poser / à revoir)
    for theme in interview.mission.trame.themes:
        for q in theme.questions:
            a = answers.get(q.id)
            status = a.status if a else "pending"
            if status != "answered":
                missed.append({"theme": theme.title, "label": q.label, "status": status})
    answered, total = _coverage(interview)
    return templates.TemplateResponse(
        request,
        "interviews/finish.html",
        {
            "interview": interview,
            "missed": missed,
            "answered": answered,
            "total": total,
        },
    )


@router.post("/interviews/{interview_id}/finish")
def finish(interview_id: int, db: Session = Depends(get_session)):
    interview = _get_interview(db, interview_id)
    interview.status = "done"
    db.commit()
    return RedirectResponse(f"/missions/{interview.mission_id}", status_code=303)
