"""Synthèse transverse par thème — incrément 3.

US4.1 : vue agrégée d'un thème (toutes les réponses des entretiens côte à côte
        + verbatims), pour préparer la synthèse.
US4.2 : génération IA d'un brouillon (convergences / divergences) via Claude.
US4.3 : édition humaine des champs de synthèse (autosave HTMX).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Mission, Synthesis, Theme
from ..services.synthese_ai import (
    SynthesisAIError,
    demo_enabled,
    generate_demo_synthesis,
    generate_theme_synthesis,
    is_configured,
)
from ..templating import templates

router = APIRouter(tags=["synthese"])

SYNTH_FIELDS = ("summary", "convergences", "divergences")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _get_mission(db: Session, mission_id: int) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable.")
    return mission


def _get_theme(db: Session, theme_id: int) -> Theme:
    theme = db.get(Theme, theme_id)
    if theme is None:
        raise HTTPException(status_code=404, detail="Thème introuvable.")
    return theme


def _get_or_create_synthesis(db: Session, theme: Theme) -> Synthesis:
    if theme.synthesis is None:
        theme.synthesis = Synthesis(theme_id=theme.id)
        db.add(theme.synthesis)
    return theme.synthesis


def _theme_material(mission: Mission, theme: Theme) -> tuple[dict, list]:
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


def _answer_count(by_question: dict[int, list[dict]]) -> int:
    return sum(len(v) for v in by_question.values())


# --------------------------------------------------------------------------- #
# US4.1 — vue par thème
# --------------------------------------------------------------------------- #
@router.get("/missions/{mission_id}/synthese")
def synthese_view(
    mission_id: int,
    request: Request,
    theme: str | None = None,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    themes = mission.trame.themes

    current = None
    if themes:
        ids = [t.id for t in themes]
        try:
            idx = ids.index(int(theme)) if theme is not None else 0
        except (ValueError, TypeError):
            idx = 0
        current = themes[idx]

    by_question = verbatims = None
    synthesis = None
    if current is not None:
        by_question, verbatims = _theme_material(mission, current)
        synthesis = _get_or_create_synthesis(db, current)
        db.commit()

    return templates.TemplateResponse(
        request,
        "synthese/theme.html",
        {
            "mission": mission,
            "themes": themes,
            "current": current,
            "by_question": by_question or {},
            "verbatims": verbatims or [],
            "synthesis": synthesis,
            "ai_ready": is_configured(),
            "demo_ready": demo_enabled(),
            "interview_count": len(mission.interviews),
        },
    )


# --------------------------------------------------------------------------- #
# US4.2 — génération IA
# --------------------------------------------------------------------------- #
@router.post("/syntheses/theme/{theme_id}/generate")
def generate(
    theme_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    theme = _get_theme(db, theme_id)
    mission = theme.trame.mission
    by_question, verbatims = _theme_material(mission, theme)
    synthesis = _get_or_create_synthesis(db, theme)

    error = None
    if _answer_count(by_question) == 0:
        error = "Aucune réponse saisie sur ce thème — rien à synthétiser."
    else:
        try:
            # Clé valide -> vraie IA ; sinon mode démo si activé ; sinon erreur.
            if is_configured():
                result = generate_theme_synthesis(theme, by_question, verbatims)
            elif demo_enabled():
                result = generate_demo_synthesis(theme, by_question, verbatims)
            else:
                raise SynthesisAIError(
                    "Génération indisponible : définissez ANTHROPIC_API_KEY "
                    "ou activez SYNTHESE_DEMO=1."
                )
            synthesis.summary = result["summary"]
            synthesis.convergences = result["convergences"]
            synthesis.divergences = result["divergences"]
            synthesis.status = "generated"
            synthesis.generated_at = datetime.now(timezone.utc)
            db.commit()
        except SynthesisAIError as exc:
            error = str(exc)

    return templates.TemplateResponse(
        request,
        "synthese/_panel.html",
        {
            "synthesis": synthesis,
            "theme": theme,
            "ai_ready": is_configured(),
            "demo_ready": demo_enabled(),
            "error": error,
            "answer_count": _answer_count(by_question),
        },
    )


# --------------------------------------------------------------------------- #
# US4.3 — édition (autosave)
# --------------------------------------------------------------------------- #
@router.post("/syntheses/theme/{theme_id}/field")
def save_field(
    theme_id: int,
    field: str = Form(...),
    value: str = Form(""),
    db: Session = Depends(get_session),
):
    if field not in SYNTH_FIELDS:
        raise HTTPException(status_code=400, detail="Champ inconnu.")
    theme = _get_theme(db, theme_id)
    synthesis = _get_or_create_synthesis(db, theme)
    setattr(synthesis, field, value)
    if synthesis.has_content:
        synthesis.status = "edited"
    db.commit()
    badge = (
        f'<span class="badge badge-synth-{synthesis.status}" id="synth-status" '
        f'hx-swap-oob="true">{synthesis.status_label}</span>'
    )
    return HTMLResponse(f'<span class="saved">✓ enregistré</span>{badge}')
