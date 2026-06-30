from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Mission
from ..services.openhub_agents import (
    create_agent_result,
    get_agent,
    invoke_agent,
    invoke_skill,
    list_available_agents,
    list_available_skills,
    opencode_available,
)
from ..templating import templates

router = APIRouter(tags=["agents"])


def _get_mission(db: Session, mission_id: int) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable.")
    return mission


@router.get("/missions/{mission_id}/agents")
def agents_view(
    mission_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    agents = list_available_agents()
    skills = list_available_skills()
    return templates.TemplateResponse(
        request,
        "missions/agents.html",
        {
            "mission": mission,
            "agents": agents,
            "skills": skills,
            "result": None,
            "result_history": mission.agent_results[:5],
            "runtime_available": opencode_available(),
        },
    )


@router.post("/missions/{mission_id}/skills/{skill_id:path}/invoke")
def invoke_skill_view(
    mission_id: int,
    skill_id: str,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    try:
        result = invoke_skill(skill_id, mission)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    agents = list_available_agents()
    skills = list_available_skills()
    return templates.TemplateResponse(
        request,
        "missions/agents.html",
        {
            "mission": mission,
            "agents": agents,
            "skills": skills,
            "result": result,
            "result_history": mission.agent_results[:5],
            "runtime_available": result["runtime_available"],
        },
    )


@router.post("/missions/{mission_id}/agents/{agent_id:path}/invoke")
def invoke(
    mission_id: int,
    agent_id: str,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    try:
        result = invoke_agent(agent_id, mission)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    create_agent_result(db, mission, result)
    agents = list_available_agents()
    skills = list_available_skills()
    db.refresh(mission)
    return templates.TemplateResponse(
        request,
        "missions/agents.html",
        {
            "mission": mission,
            "agents": agents,
            "skills": skills,
            "result": result,
            "result_history": mission.agent_results[:5],
            "runtime_available": result["runtime_available"],
        },
    )
