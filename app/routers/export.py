"""Cycle export -> analyse externe -> import -> synthèse -> export PPT (évol).

Regroupe les routes qui gravitent autour de la synthèse globale et des
recommandations sans en faire partie directement (contrairement à
`synthese.py`, qui reste focalisé sur la génération/édition elles-mêmes) :
export Markdown de toute la matière d'entretien (étape 1), import du
résultat d'une analyse menée en dehors de la plateforme (étape 1), export
PowerPoint avec sélection de slides et upload d'un template PPT client
(étape 4).
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from ..db import PPTX_TEMPLATES_DIR, get_session
from ..models import Mission
from ..routers.synthese import (
    _all_theme_material,
    _apply_global_synthesis_result,
    _apply_recommendations_result,
    _get_or_create_global_synthesis,
    _total_answer_count,
)
from ..services.ai_common import api_key_env_name, is_configured
from ..services.analyse_import import (
    AnalysisParseError,
    decode_text_upload,
    parse_analysis_markdown,
)
from ..services.mission_export import build_export_markdown, slugify
from ..services.pptx_export import build_presentation
from ..templating import templates

router = APIRouter(tags=["export"])


def _get_mission(db: Session, mission_id: int) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable.")
    return mission


def _synthese_context(mission: Mission, error: str | None = None) -> dict:
    """Contexte de gabarit partagé par l'étape 1 (analyse — IA intégrée +
    export/import manuel) et l'étape 4 (export PPT) — toutes ont besoin de
    savoir si de la matière existe déjà ; l'étape 1 en plus affiche le
    sous-onglet "IA intégrée" (bouton Générer/Régénérer réutilisé depuis
    `_global_panel.html`), qui a besoin de `ai_ready`/`api_key_env`/
    `answer_count` comme la page synthèse globale elle-même."""
    material_by_theme = _all_theme_material(mission)
    return {
        "mission": mission,
        "themes": mission.trame.themes if mission.trame else [],
        "global_synthesis": mission.global_synthesis,
        "axes": mission.recommendation_axes,
        "error": error,
        "ai_ready": is_configured(),
        "api_key_env": api_key_env_name(),
        "answer_count": _total_answer_count(material_by_theme),
    }


# --------------------------------------------------------------------------- #
# Étape 1 — Analyse : IA intégrée (génère sans sortir de la plateforme) ou
# export/import manuel (matière + gabarit -> analyse externe -> réimport).
# --------------------------------------------------------------------------- #
@router.get("/missions/{mission_id}/synthese/export-import")
def export_import_view(mission_id: int, request: Request, db: Session = Depends(get_session)):
    mission = _get_mission(db, mission_id)
    # get_or_create (pas juste lecture) : le sous-onglet IA intégrée inclut
    # _global_panel.html, qui suppose toujours un GlobalSynthesis existant
    # (comme son autre appelant, synthese.global_synthese_view).
    _get_or_create_global_synthesis(db, mission)
    db.commit()
    return templates.TemplateResponse(request, "synthese/export_import.html", _synthese_context(mission))


@router.get("/missions/{mission_id}/export/interviews")
def export_interviews(mission_id: int, db: Session = Depends(get_session)):
    mission = _get_mission(db, mission_id)
    content = build_export_markdown(mission)
    filename = f"entretiens_{slugify(mission.name)}.md"
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# Étape 1 — Import de l'analyse externe (redirige vers l'étape 2 une fois fait)
# --------------------------------------------------------------------------- #
@router.post("/missions/{mission_id}/import/analyse")
async def import_analyse(
    mission_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    # Toujours garanti avant de rendre export_import.html en cas d'erreur
    # plus bas (le sous-onglet IA intégrée y suppose un GlobalSynthesis non
    # nul) — fait avant le parsing, qui peut échouer avant d'atteindre le
    # get_or_create plus bas dans le flux nominal.
    global_synthesis = _get_or_create_global_synthesis(db, mission)
    db.commit()
    try:
        raw = await file.read()
        text = decode_text_upload(raw)
        parsed = parse_analysis_markdown(text)

        if any((v or "").strip() for v in parsed["global_synthesis"].values()):
            _apply_global_synthesis_result(global_synthesis, parsed["global_synthesis"])
        if parsed["axes"]:
            _apply_recommendations_result(db, mission, parsed["axes"])
        db.commit()
    except AnalysisParseError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request, "synthese/export_import.html", _synthese_context(mission, error=str(exc))
        )
    except Exception as exc:  # garde-fou : jamais de 500 brute sur un import utilisateur
        db.rollback()
        return templates.TemplateResponse(
            request,
            "synthese/export_import.html",
            _synthese_context(mission, error=f"Échec de l'import : {exc}"),
        )

    # Suite logique du parcours : aller relire/éditer la synthèse globale
    # importée (étape 2), pas rester sur l'écran d'import.
    return RedirectResponse(f"/missions/{mission_id}/synthese/globale", status_code=303)


# --------------------------------------------------------------------------- #
# Étape 4 — Aperçu + configuration avant export PPT
# --------------------------------------------------------------------------- #
@router.get("/missions/{mission_id}/synthese/apercu")
def apercu_view(mission_id: int, request: Request, db: Session = Depends(get_session)):
    mission = _get_mission(db, mission_id)
    return templates.TemplateResponse(request, "synthese/apercu.html", _synthese_context(mission))


# --------------------------------------------------------------------------- #
# Étape 4 — Export PowerPoint (respecte la sélection de slides si soumise)
# --------------------------------------------------------------------------- #
@router.get("/missions/{mission_id}/export/pptx")
def export_pptx(
    mission_id: int,
    db: Session = Depends(get_session),
    config_submitted: bool = False,
    sommaire: bool = False,
    synthese: bool = False,
    axes_overview: bool = False,
    matrix: bool = False,
    axis: list[int] = Query(default=[]),
):
    mission = _get_mission(db, mission_id)
    template_path = (
        PPTX_TEMPLATES_DIR / mission.pptx_template_path if mission.pptx_template_path else None
    )

    # Une case décochée n'envoie rien en GET — `config_submitted` distingue
    # "formulaire soumis, respecter exactement les cases cochées" (y compris
    # "aucune") de "appel direct/rétrocompatible -> tout inclure par défaut".
    if config_submitted:
        include_kwargs = dict(
            include_sommaire=sommaire,
            include_synthese=synthese,
            include_axes_overview=axes_overview,
            include_matrix=matrix,
            include_axis_ids=set(axis),
        )
    else:
        include_kwargs = {}

    prs = build_presentation(mission, template_path=template_path, **include_kwargs)

    buf = io.BytesIO()
    prs.save(buf)
    filename = f"restitution_{slugify(mission.name)}.pptx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# Étape 4 — Upload d'un template PPT client
# --------------------------------------------------------------------------- #
@router.post("/missions/{mission_id}/pptx-template")
async def upload_pptx_template(
    mission_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    if not (file.filename or "").lower().endswith(".pptx"):
        return templates.TemplateResponse(
            request,
            "synthese/apercu.html",
            _synthese_context(mission, error="Un fichier .pptx est attendu."),
        )

    content = await file.read()
    try:
        from pptx import Presentation

        Presentation(io.BytesIO(content))  # valide que le fichier est un vrai .pptx
    except Exception:
        return templates.TemplateResponse(
            request,
            "synthese/apercu.html",
            _synthese_context(mission, error="Fichier .pptx invalide ou corrompu."),
        )

    filename = f"{mission_id}.pptx"
    (PPTX_TEMPLATES_DIR / filename).write_bytes(content)
    mission.pptx_template_path = filename
    db.commit()

    return RedirectResponse(f"/missions/{mission_id}/synthese/apercu", status_code=303)
