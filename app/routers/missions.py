"""CRUD des missions (US0.2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Interview, Mission, Trame
from ..services.mode import est_mode_demo
from ..templating import templates

router = APIRouter(prefix="/missions", tags=["missions"])


def _get_mission(db: Session, mission_id: int) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable.")
    return mission


def _draft_vide(mission: Mission) -> bool:
    """Brouillon abandonné sans contenu : ni entretien enregistré, ni trame
    remplie (la trame vide « Trame d'entretien » créée d'office par le parcours
    structuré ne compte pas comme du contenu). Seuls ces brouillons-là sont
    éligibles au nettoyage groupé — un brouillon avec de la matière se reprend
    via /finaliser, il ne se supprime qu'un par un, explicitement."""
    return (
        mission.is_draft
        and not mission.interviews
        and (mission.trame is None or not mission.trame.themes)
    )


@router.get("")
def list_missions(request: Request, db: Session = Depends(get_session)):
    # Filtré par le mode courant (P5a-1) : démo et réel ne se mélangent jamais
    # dans la liste. Le compteur de brouillons vides porte donc sur le seul mode
    # affiché (il itère `missions`, déjà filtré).
    missions = db.scalars(
        select(Mission)
        .where(Mission.is_demo.is_(est_mode_demo(request)))
        .order_by(Mission.created_at.desc())
    ).all()
    return templates.TemplateResponse(
        request,
        "missions/list.html",
        {
            "missions": missions,
            "nb_brouillons_vides": sum(1 for m in missions if _draft_vide(m)),
        },
    )


@router.post("/brouillons/nettoyer")
def nettoyer_brouillons(request: Request, db: Session = Depends(get_session)):
    """Supprime d'un coup les missions brouillon vides (abandonnées avant
    toute saisie — elles s'accumulent car chaque entrée « entretien libre/
    structuré » en crée une, cf. entretiens.py). Déclenché par un bouton
    explicite de la liste, jamais automatiquement. Borné au mode courant (P5a-1)
    — un nettoyage en réel ne touche jamais aux brouillons démo, et inversement."""
    q = select(Mission).where(
        Mission.is_draft.is_(True), Mission.is_demo.is_(est_mode_demo(request))
    )
    for mission in db.scalars(q).all():
        if _draft_vide(mission):
            db.delete(mission)
    db.commit()
    return RedirectResponse("/missions", status_code=303)


@router.get("/new")
def new_mission(request: Request):
    return templates.TemplateResponse(request, "missions/form.html", {})


@router.post("")
def create_mission(
    request: Request,
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
        is_demo=est_mode_demo(request),
        trame=Trame(name="Trame d'entretien"),
    )
    db.add(mission)
    db.commit()
    return RedirectResponse(f"/missions/{mission.id}", status_code=303)


@router.get("/{mission_id}/finaliser")
def finaliser_mission_form(
    mission_id: int, request: Request, db: Session = Depends(get_session)
):
    """Mission brouillon (incr.9, US9.2) née d'un entretien libre ou
    structuré à mission différée : on la nomme maintenant, ou on rattache
    ses entretiens/sa trame à une mission existante. Le rattachement n'est
    proposé que vers des missions sans trame déjà définie quand cette
    mission brouillon en porte une — évite le conflit « deux trames »
    (`Mission.trame` reste 1:1)."""
    mission = _get_mission(db, mission_id)
    if not mission.is_draft:
        return RedirectResponse(f"/missions/{mission.id}", status_code=303)

    # Même mode que le brouillon (P5a-1) : on ne rattache jamais un brouillon démo
    # à une mission réelle, ni l'inverse (le brouillon porte son propre is_demo,
    # posé à sa création selon le mode courant).
    query = select(Mission).where(
        Mission.is_draft.is_(False),
        Mission.id != mission.id,
        Mission.is_demo.is_(mission.is_demo),
    )
    if mission.trame is not None:
        query = query.where(~Mission.trame.has())
    eligible = db.scalars(query.order_by(Mission.created_at.desc())).all()

    return templates.TemplateResponse(
        request,
        "missions/finaliser.html",
        {"mission": mission, "eligible": eligible},
    )


@router.post("/{mission_id}/finaliser")
def finaliser_mission(
    mission_id: int,
    action: str = Form(...),
    name: str = Form(""),
    description: str = Form(""),
    target_mission_id: int | None = Form(None),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    if not mission.is_draft:
        raise HTTPException(status_code=400, detail="Cette mission est déjà finalisée.")

    if action == "nommer":
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Le nom est obligatoire.")
        mission.name = name
        mission.description = description.strip() or None
        mission.is_draft = False
        db.commit()
        return RedirectResponse(f"/missions/{mission.id}", status_code=303)

    if action == "rattacher":
        target = db.get(Mission, target_mission_id) if target_mission_id else None
        if (
            target is None
            or target.is_draft
            or target.id == mission.id
            or target.is_demo != mission.is_demo  # jamais de rattachement cross-mode
        ):
            raise HTTPException(status_code=400, detail="Mission cible invalide.")
        if mission.trame is not None and target.trame is not None:
            raise HTTPException(
                status_code=400,
                detail="La mission cible a déjà une trame — rattachement impossible.",
            )

        db.execute(
            update(Interview)
            .where(Interview.mission_id == mission.id)
            .values(mission_id=target.id)
        )
        if mission.trame is not None:
            db.execute(
                update(Trame)
                .where(Trame.id == mission.trame.id)
                .values(mission_id=target.id)
            )
        db.commit()

        # Ré-interroge à froid : la mission brouillon n'a alors plus aucun
        # enfant rattaché, donc la supprimer ne déclenche aucune cascade sur
        # la trame/les entretiens qu'on vient de reparenter.
        db.expire_all()
        orphan = db.get(Mission, mission.id)
        db.delete(orphan)
        db.commit()
        return RedirectResponse(f"/missions/{target.id}", status_code=303)

    raise HTTPException(status_code=400, detail="Action inconnue.")


@router.get("/{mission_id}")
def mission_detail(
    mission_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    return templates.TemplateResponse(
        request, "missions/detail.html", {"mission": mission}
    )


@router.post("/{mission_id}/name")
def update_mission_name(
    mission_id: int,
    name: str = Form(...),
    db: Session = Depends(get_session),
):
    """Renomme la mission (autosave HTMX depuis la page mission) — le nom sert de
    titre au deck PPT, jusqu'ici non modifiable après création (US demandée
    2026-07-22). Un nom vide est refusé (le nom est obligatoire)."""
    mission = _get_mission(db, mission_id)
    name = name.strip()
    if not name:
        return HTMLResponse('<span class="saved error">⚠ nom obligatoire</span>')
    mission.name = name
    db.commit()
    return HTMLResponse('<span class="saved">✓ enregistré</span>')


@router.post("/{mission_id}/delete")
def delete_mission(mission_id: int, db: Session = Depends(get_session)):
    mission = db.get(Mission, mission_id)
    if mission is not None:
        db.delete(mission)
        db.commit()
    return RedirectResponse("/missions", status_code=303)
