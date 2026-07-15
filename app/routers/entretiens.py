"""Écran d'entrée unifié (incr.9, US9.1) — 3 choix : entretien libre,
entretien structuré sur trame, nouvelle mission classique.

Pour les deux premiers choix, une mission « brouillon » (`Mission.is_draft`)
est créée immédiatement : une `Trame`/un `Interview` a toujours besoin d'un
`mission_id` (contrainte FK), donc la mission existe bien en base dès le
départ — mais son identité réelle (nom/description, ou rattachement à une
mission existante) n'est complétée qu'après coup, via
`/missions/{id}/finaliser` (voir `missions.py`), pas choisie en amont comme
pour le 3ᵉ choix.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Mission, Trame
from ..templating import templates

router = APIRouter(tags=["entretiens"])


def _draft_mission_name() -> str:
    return f"Sans nom — {date.today().strftime('%d/%m/%Y')}"


@router.get("/")
def entree(request: Request):
    return templates.TemplateResponse(request, "entretiens/entree.html", {})


@router.post("/entretiens/libre/nouveau")
def nouveau_entretien_libre(db: Session = Depends(get_session)):
    mission = Mission(name=_draft_mission_name(), is_draft=True)
    db.add(mission)
    db.commit()
    return RedirectResponse(
        f"/missions/{mission.id}/interviews/record-libre", status_code=303
    )


@router.post("/entretiens/structure/nouveau")
def nouveau_entretien_structure(db: Session = Depends(get_session)):
    mission = Mission(
        name=_draft_mission_name(),
        is_draft=True,
        trame=Trame(name="Trame d'entretien"),
    )
    db.add(mission)
    db.commit()
    return RedirectResponse(f"/missions/{mission.id}/trame", status_code=303)
