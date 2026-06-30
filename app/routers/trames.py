"""Édition de la trame : thèmes et questions typées (US1.1, US1.2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..importers.docx_trame import parse_docx_bytes
from ..models import Mission, Question, Theme, QUESTION_TYPES
from ..templating import templates

router = APIRouter(prefix="/missions/{mission_id}/trame", tags=["trame"])


def _get_mission(db: Session, mission_id: int) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable.")
    return mission


def _next_position(items) -> int:
    return (max((i.position for i in items), default=-1)) + 1


def _build_config(qtype: str, scale_min: int, scale_max: int, choices: str) -> dict:
    """Construit le `config` JSON d'une question selon son type."""
    if qtype == "scale":
        lo, hi = sorted((scale_min, scale_max))
        return {"min": lo, "max": hi}
    if qtype == "choice":
        # Tolère les options séparées par des virgules et/ou des retours ligne.
        raw = choices.replace(",", "\n")
        options = [line.strip() for line in raw.splitlines() if line.strip()]
        return {"options": options}
    return {}


@router.get("")
def edit_trame(
    mission_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    return templates.TemplateResponse(
        request,
        "trames/edit.html",
        {"mission": mission, "trame": mission.trame},
    )


@router.get("/preview")
def preview_trame(
    mission_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    question_count = sum(len(t.questions) for t in mission.trame.themes)
    return templates.TemplateResponse(
        request,
        "trames/preview.html",
        {"mission": mission, "trame": mission.trame, "question_count": question_count},
    )


@router.get("/import")
def import_form(
    mission_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    return templates.TemplateResponse(
        request, "trames/import.html", {"mission": mission}
    )


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


@router.post("/import")
async def import_docx(
    mission_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Un fichier .docx est attendu.")

    parsed = parse_docx_bytes(await file.read())

    # Import NON destructif : on fusionne dans la trame existante sans jamais
    # supprimer un thème/une question déjà là (donc sans toucher aux réponses
    # déjà saisies). Un thème est rapproché par titre ; au sein d'un thème, on
    # n'ajoute que les questions dont l'intitulé n'existe pas encore.
    themes_by_title = {_norm(t.title): t for t in mission.trame.themes}

    for ptheme in parsed.themes:
        theme = themes_by_title.get(_norm(ptheme.title))
        if theme is None:
            theme = Theme(
                title=ptheme.title,
                position=_next_position(mission.trame.themes),
            )
            mission.trame.themes.append(theme)
            themes_by_title[_norm(ptheme.title)] = theme

        existing_labels = {_norm(q.label) for q in theme.questions}
        for pq in ptheme.questions:
            if _norm(pq.label) in existing_labels:
                continue  # question déjà présente : on ne l'écrase pas
            theme.questions.append(
                Question(
                    label=pq.label,
                    help_text=pq.help or None,
                    qtype=pq.qtype,
                    config=pq.config,
                    position=_next_position(theme.questions),
                )
            )
            existing_labels.add(_norm(pq.label))

    # Section « Objectifs et principes » -> introduction reprise en tête
    # d'entretien (on ne l'écrase que si le document en fournit une).
    if parsed.intro:
        mission.trame.intro_text = parsed.intro

    db.commit()
    # Après import : on présente l'aperçu (questions importées + actions :
    # modifier la trame / démarrer un entretien).
    return RedirectResponse(f"/missions/{mission_id}/trame/preview", status_code=303)


@router.post("/intro")
def save_intro(
    mission_id: int,
    intro_text: str = Form(""),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    mission.trame.intro_text = intro_text.strip() or None
    db.commit()
    return HTMLResponse('<span class="saved">✓ enregistré</span>')


@router.post("/themes")
def add_theme(
    mission_id: int,
    title: str = Form(...),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    title = title.strip()
    if title:
        mission.trame.themes.append(
            Theme(title=title, position=_next_position(mission.trame.themes))
        )
        db.commit()
    return RedirectResponse(f"/missions/{mission_id}/trame", status_code=303)


@router.post("/themes/{theme_id}/delete")
def delete_theme(
    mission_id: int,
    theme_id: int,
    db: Session = Depends(get_session),
):
    theme = db.get(Theme, theme_id)
    if theme is not None and theme.trame.mission_id == mission_id:
        db.delete(theme)
        db.commit()
    return RedirectResponse(f"/missions/{mission_id}/trame", status_code=303)


@router.post("/themes/{theme_id}/questions")
def add_question(
    mission_id: int,
    theme_id: int,
    label: str = Form(...),
    qtype: str = Form("open"),
    scale_min: int = Form(1),
    scale_max: int = Form(5),
    choices: str = Form(""),
    help_text: str = Form(""),
    db: Session = Depends(get_session),
):
    theme = db.get(Theme, theme_id)
    if theme is None or theme.trame.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="Thème introuvable.")
    if qtype not in QUESTION_TYPES:
        qtype = "open"
    label = label.strip()
    if label:
        theme.questions.append(
            Question(
                label=label,
                help_text=help_text.strip() or None,
                qtype=qtype,
                config=_build_config(qtype, scale_min, scale_max, choices),
                position=_next_position(theme.questions),
            )
        )
        db.commit()
    return RedirectResponse(f"/missions/{mission_id}/trame", status_code=303)


@router.post("/questions/{question_id}/edit")
def edit_question(
    mission_id: int,
    question_id: int,
    label: str = Form(...),
    qtype: str = Form("open"),
    scale_min: int = Form(1),
    scale_max: int = Form(5),
    choices: str = Form(""),
    help_text: str = Form(""),
    db: Session = Depends(get_session),
):
    question = db.get(Question, question_id)
    if question is None or question.theme.trame.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="Question introuvable.")
    if qtype not in QUESTION_TYPES:
        qtype = "open"
    label = label.strip()
    if label:
        question.label = label
        question.help_text = help_text.strip() or None
        question.qtype = qtype
        question.config = _build_config(qtype, scale_min, scale_max, choices)
        db.commit()
    return RedirectResponse(f"/missions/{mission_id}/trame", status_code=303)


@router.post("/questions/{question_id}/delete")
def delete_question(
    mission_id: int,
    question_id: int,
    db: Session = Depends(get_session),
):
    question = db.get(Question, question_id)
    if question is not None and question.theme.trame.mission_id == mission_id:
        db.delete(question)
        db.commit()
    return RedirectResponse(f"/missions/{mission_id}/trame", status_code=303)
