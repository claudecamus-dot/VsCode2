"""Saisie manuelle des interviews — écran thème par thème (incrément 2).

Principes : autosave par champ (HTMX), navigation libre entre thèmes, suivi
de couverture en direct, statut par question (non posée / à revoir), notes
libres hors-trame, brouillon permanent.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Answer, Interview, Mission, Question, Verbatim
from ..templating import templates

router = APIRouter(tags=["interviews"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _get_mission(db: Session, mission_id: int) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable.")
    return mission


def _get_interview(db: Session, interview_id: int) -> Interview:
    interview = db.get(Interview, interview_id)
    if interview is None:
        raise HTTPException(status_code=404, detail="Entretien introuvable.")
    return interview


def _get_or_create_answer(db: Session, interview: Interview, question_id: int) -> Answer:
    answer = db.scalar(
        select(Answer).where(
            Answer.interview_id == interview.id,
            Answer.question_id == question_id,
        )
    )
    if answer is None:
        answer = Answer(interview_id=interview.id, question_id=question_id)
        db.add(answer)
    return answer


def _all_questions(interview: Interview) -> list[Question]:
    return [q for t in interview.mission.trame.themes for q in t.questions]


def _coverage(interview: Interview) -> tuple[int, int]:
    answers = {a.question_id: a for a in interview.answers}
    questions = _all_questions(interview)
    answered = sum(
        1 for q in questions
        if (a := answers.get(q.id)) is not None and a.status == "answered"
    )
    return answered, len(questions)


def _saved_response(request: Request, interview: Interview, answer: Answer):
    answered, total = _coverage(interview)
    return templates.TemplateResponse(
        request,
        "interviews/_saved.html",
        {"answer": answer, "answered": answered, "total": total},
    )


def _verbatims_for(db: Session, interview_id: int, question_id: int) -> list[Verbatim]:
    return list(
        db.scalars(
            select(Verbatim)
            .where(
                Verbatim.interview_id == interview_id,
                Verbatim.question_id == question_id,
            )
            .order_by(Verbatim.created_at)
        )
    )


def _verbatims_response(request: Request, verbatims: list[Verbatim]):
    return templates.TemplateResponse(
        request, "interviews/_verbatims.html", {"verbatims": verbatims}
    )


# --------------------------------------------------------------------------- #
# Création / cycle de vie
# --------------------------------------------------------------------------- #
@router.get("/missions/{mission_id}/interviews/new")
def new_interview(mission_id: int, request: Request, db: Session = Depends(get_session)):
    mission = _get_mission(db, mission_id)
    return templates.TemplateResponse(
        request, "interviews/new.html", {"mission": mission}
    )


@router.post("/missions/{mission_id}/interviews")
def create_interview(
    mission_id: int,
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    reference_text: str = Form(""),
    db: Session = Depends(get_session),
):
    _get_mission(db, mission_id)
    try:
        parsed_date = date.fromisoformat(interview_date) if interview_date else None
    except ValueError:
        parsed_date = None
    interview = Interview(
        mission_id=mission_id,
        interviewee_name=interviewee_name.strip() or "Sans nom",
        interviewee_role=interviewee_role.strip() or None,
        interviewee_entity=interviewee_entity.strip() or None,
        interview_date=parsed_date,
        reference_text=reference_text.strip() or None,
    )
    db.add(interview)
    db.commit()
    return RedirectResponse(f"/interviews/{interview.id}", status_code=303)


@router.post("/interviews/{interview_id}/delete")
def delete_interview(interview_id: int, db: Session = Depends(get_session)):
    interview = db.get(Interview, interview_id)
    mission_id = interview.mission_id if interview else None
    if interview is not None:
        db.delete(interview)
        db.commit()
    target = f"/missions/{mission_id}" if mission_id else "/missions"
    return RedirectResponse(target, status_code=303)


# --------------------------------------------------------------------------- #
# Écran de saisie (thème par thème)
# --------------------------------------------------------------------------- #
@router.get("/interviews/{interview_id}")
def capture(
    interview_id: int,
    request: Request,
    theme: str | None = None,
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    themes = interview.mission.trame.themes
    answers = {a.question_id: a for a in interview.answers}
    verbatims_by_q: dict[int, list[Verbatim]] = {}
    for v in interview.verbatims:
        verbatims_by_q.setdefault(v.question_id, []).append(v)

    # Couverture par thème (pour les pastilles de navigation).
    theme_counts = {
        t.id: (
            sum(
                1 for q in t.questions
                if (a := answers.get(q.id)) is not None and a.status == "answered"
            ),
            len(t.questions),
        )
        for t in themes
    }
    answered, total = _coverage(interview)

    notes_view = theme == "notes"
    current = None
    prev_id = next_id = None
    if not notes_view and themes:
        ids = [t.id for t in themes]
        try:
            idx = ids.index(int(theme)) if theme is not None else 0
        except (ValueError, TypeError):
            idx = 0
        current = themes[idx]
        prev_id = ids[idx - 1] if idx > 0 else None
        next_id = ids[idx + 1] if idx < len(ids) - 1 else None

    return templates.TemplateResponse(
        request,
        "interviews/capture.html",
        {
            "interview": interview,
            "themes": themes,
            "current": current,
            "answers": answers,
            "verbatims_by_q": verbatims_by_q,
            "theme_counts": theme_counts,
            "answered": answered,
            "total": total,
            "notes_view": notes_view,
            "prev_id": prev_id,
            "next_id": next_id,
        },
    )


@router.post("/interviews/{interview_id}/answers/{question_id}")
def save_answer(
    interview_id: int,
    question_id: int,
    request: Request,
    text: str | None = Form(None),
    value: str | None = Form(None),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    answer = _get_or_create_answer(db, interview, question_id)
    if text is not None:
        answer.text = text
    if value is not None:
        answer.value = value

    has_content = bool((answer.text or "").strip() or (answer.value or "").strip())
    if has_content:
        answer.status = "answered"
    elif answer.status not in ("skipped", "revisit"):
        answer.status = "pending"

    db.commit()
    return _saved_response(request, interview, answer)


@router.post("/interviews/{interview_id}/answers/{question_id}/status")
def set_status(
    interview_id: int,
    question_id: int,
    request: Request,
    status: str = Form(...),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    answer = _get_or_create_answer(db, interview, question_id)
    if status in ("pending", "answered", "skipped", "revisit"):
        answer.status = status
    db.commit()
    return _saved_response(request, interview, answer)


@router.post("/interviews/{interview_id}/notes")
def save_notes(
    interview_id: int,
    free_notes: str = Form(""),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    interview.free_notes = free_notes
    db.commit()
    return HTMLResponse('<span class="saved">✓ enregistré</span>')


@router.post("/interviews/{interview_id}/identity")
def save_identity(
    interview_id: int,
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    interview.interviewee_name = interviewee_name.strip() or "Sans nom"
    interview.interviewee_role = interviewee_role.strip() or None
    interview.interviewee_entity = interviewee_entity.strip() or None
    db.commit()
    return HTMLResponse('<span class="saved">✓ enregistré</span>')


@router.post("/interviews/{interview_id}/reference")
def save_reference(
    interview_id: int,
    reference_text: str = Form(""),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    interview.reference_text = reference_text.strip() or None
    db.commit()
    return HTMLResponse('<span class="saved">✓ enregistré</span>')


# --------------------------------------------------------------------------- #
# Verbatims (US2.3) : citations mot-pour-mot rattachées à une question
# --------------------------------------------------------------------------- #
@router.post("/interviews/{interview_id}/verbatims/{question_id}")
def add_verbatim(
    interview_id: int,
    question_id: int,
    request: Request,
    quote: str = Form(...),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    quote = quote.strip()
    if quote:
        db.add(
            Verbatim(
                interview_id=interview.id,
                question_id=question_id,
                quote=quote,
            )
        )
        db.commit()
    return _verbatims_response(
        request, _verbatims_for(db, interview.id, question_id)
    )


@router.post("/verbatims/{verbatim_id}/delete")
def delete_verbatim(
    verbatim_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    verbatim = db.get(Verbatim, verbatim_id)
    if verbatim is None:
        raise HTTPException(status_code=404, detail="Verbatim introuvable.")
    interview_id, question_id = verbatim.interview_id, verbatim.question_id
    db.delete(verbatim)
    db.commit()
    return _verbatims_response(
        request, _verbatims_for(db, interview_id, question_id)
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
