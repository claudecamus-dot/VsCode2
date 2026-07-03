"""CRUD des missions (US0.2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Mission, Trame
from ..templating import templates

router = APIRouter(prefix="/missions", tags=["missions"])


@router.get("")
def list_missions(request: Request, db: Session = Depends(get_session)):
    missions = db.scalars(
        select(Mission).order_by(Mission.created_at.desc())
    ).all()
    return templates.TemplateResponse(
        request, "missions/list.html", {"missions": missions}
    )


@router.get("/new")
def new_mission(request: Request):
    return templates.TemplateResponse(request, "missions/form.html", {})


@router.post("")
def create_mission(
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_session),
):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Le nom est obligatoire.")
    mission = Mission(
        name=name,
        description=description.strip() or None,
        trame=Trame(name="Trame d'entretien"),
    )
    db.add(mission)
    db.commit()
    return RedirectResponse(f"/missions/{mission.id}", status_code=303)


@router.get("/{mission_id}")
def mission_detail(
    mission_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable.")
    return templates.TemplateResponse(
        request, "missions/detail.html", {"mission": mission}
    )


@router.post("/{mission_id}/delete")
def delete_mission(mission_id: int, db: Session = Depends(get_session)):
    mission = db.get(Mission, mission_id)
    if mission is not None:
        db.delete(mission)
        db.commit()
    return RedirectResponse("/missions", status_code=303)
