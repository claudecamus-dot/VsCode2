"""Édition de la trame : thèmes et questions typées (US1.1, US1.2)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..importers.docx_trame import (
    ParsedQuestion,
    ParsedTheme,
    ParsedTrame,
    extract_text_bytes,
    parse_docx_bytes,
)
from ..models import Mission, Question, Theme, QUESTION_TYPES
from ..services.trame_extract_ai import TrameExtractAIError, extract_trame_from_text
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


def _parsed_to_json(parsed: ParsedTrame) -> str:
    return json.dumps(
        {
            "name": parsed.name,
            "intro": parsed.intro,
            "themes": [
                {
                    "title": t.title,
                    "questions": [
                        {
                            "label": q.label,
                            "help": q.help,
                            "qtype": q.qtype,
                            "config": q.config,
                        }
                        for q in t.questions
                    ],
                }
                for t in parsed.themes
            ],
        }
    )


def _parsed_from_json(raw: str) -> ParsedTrame:
    data = json.loads(raw)
    return ParsedTrame(
        name=data.get("name") or "Trame importée",
        intro=data.get("intro") or "",
        themes=[
            ParsedTheme(
                title=t.get("title") or "Thème",
                questions=[
                    ParsedQuestion(
                        label=q.get("label") or "",
                        qtype=q.get("qtype") or "open",
                        config=q.get("config") or {},
                        help=q.get("help") or "",
                    )
                    for q in t.get("questions") or []
                ],
            )
            for t in data.get("themes") or []
        ],
    )


def _merge_parsed_trame(mission: Mission, parsed: ParsedTrame, keep: set[str]) -> None:
    """Fusion NON destructive dans la trame existante : ne supprime jamais un
    thème/une question déjà là (donc ne touche pas aux réponses déjà
    saisies). Un thème est rapproché par titre ; au sein d'un thème, on
    n'ajoute que les questions dont l'intitulé n'existe pas encore.

    `keep` : clés `"{theme_idx}-{question_idx}"` des questions proposées que
    l'utilisateur a validées sur l'écran de revue (les autres sont ignorées).
    """
    themes_by_title = {_norm(t.title): t for t in mission.trame.themes}

    for ti, ptheme in enumerate(parsed.themes):
        kept_questions = [
            (qi, pq) for qi, pq in enumerate(ptheme.questions) if f"{ti}-{qi}" in keep
        ]
        if not kept_questions:
            continue

        theme = themes_by_title.get(_norm(ptheme.title))
        if theme is None:
            theme = Theme(
                title=ptheme.title,
                position=_next_position(mission.trame.themes),
            )
            mission.trame.themes.append(theme)
            themes_by_title[_norm(ptheme.title)] = theme

        existing_labels = {_norm(q.label) for q in theme.questions}
        for _, pq in kept_questions:
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


@router.post("/import")
async def import_docx(
    mission_id: int,
    request: Request,
    file: UploadFile = File(...),
    ai_mode: bool = Form(False),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Un fichier .docx est attendu.")

    content = await file.read()
    parsed = None if ai_mode else parse_docx_bytes(content)

    ai_error = None
    if parsed is None or not parsed.themes:
        try:
            parsed = extract_trame_from_text(extract_text_bytes(content))
        except TrameExtractAIError as exc:
            ai_error = str(exc)
            parsed = parsed or ParsedTrame(name="Trame importée")

    if not parsed.themes:
        return templates.TemplateResponse(
            request,
            "trames/import.html",
            {
                "mission": mission,
                "error": ai_error
                or "Aucun thème ni question détecté dans ce document.",
            },
        )

    # Rien n'est encore écrit en base : l'utilisateur valide sur l'écran de
    # revue (checkbox par question) avant que la fusion ne s'applique.
    return templates.TemplateResponse(
        request,
        "trames/import_review.html",
        {"mission": mission, "parsed": parsed, "parsed_json": _parsed_to_json(parsed)},
    )


@router.post("/import/confirm")
def import_confirm(
    mission_id: int,
    parsed: str = Form(...),
    keep: list[str] = Form([]),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    parsed_trame = _parsed_from_json(parsed)
    _merge_parsed_trame(mission, parsed_trame, set(keep))
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
