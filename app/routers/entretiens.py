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
from ..services.mode import MODE_COOKIE, est_mode_demo
from ..templating import templates

router = APIRouter(tags=["entretiens"])

# Cookie de mode : 1 an, comme VSCode1 (samesite=lax, path=/ pour couvrir tout le site).
_MODE_MAX_AGE = 60 * 60 * 24 * 365


def _draft_mission_name() -> str:
    return f"Sans nom — {date.today().strftime('%d/%m/%Y')}"


@router.get("/")
def accueil(request: Request):
    """1ère page (P5a-1, modèle VSCode1) : choix Démo / Réel. Pose le mode via
    /mode/{choix} puis mène à l'entrée à 3 choix (/demarrer)."""
    return templates.TemplateResponse(request, "entretiens/accueil.html", {})


@router.post("/mode/{choix}")
def choisir_mode(choix: str):
    """Pose le cookie `mode` (démo ou réel — toute autre valeur retombe sur réel,
    on ne bascule jamais en démo par accident) puis redirige vers l'entrée."""
    valeur = "demo" if choix == "demo" else "reel"
    resp = RedirectResponse("/demarrer", status_code=303)
    resp.set_cookie(MODE_COOKIE, valeur, max_age=_MODE_MAX_AGE, samesite="lax", path="/")
    return resp


@router.get("/demarrer")
def entree(request: Request):
    """Entrée à 3 choix (entretien libre / structuré / nouvelle mission) —
    déplacée de `/` sous la 1ère page démo/réel (P5a-1)."""
    return templates.TemplateResponse(request, "entretiens/entree.html", {})


@router.post("/entretiens/libre/nouveau")
def nouveau_entretien_libre(request: Request, db: Session = Depends(get_session)):
    mission = Mission(
        name=_draft_mission_name(), is_draft=True, is_demo=est_mode_demo(request)
    )
    db.add(mission)
    db.commit()
    return RedirectResponse(
        f"/missions/{mission.id}/interviews/record-libre", status_code=303
    )


@router.post("/entretiens/structure/nouveau")
def nouveau_entretien_structure(request: Request, db: Session = Depends(get_session)):
    mission = Mission(
        name=_draft_mission_name(),
        is_draft=True,
        is_demo=est_mode_demo(request),
        trame=Trame(name="Trame d'entretien"),
    )
    db.add(mission)
    db.commit()
    return RedirectResponse(f"/missions/{mission.id}/trame", status_code=303)
