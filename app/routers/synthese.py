"""Synthèse transverse — mission (incr.3, étendue incr.9 aux missions sans trame).

Synthèse globale (5 catégories) + recommandations, agrégeant tous les
entretiens de la mission (structurés par thème et/ou libres). L'ancienne vue
de synthèse par thème (US4.1-4.3) a été retirée le 2026-07-17 : superflue
depuis l'écran unifié Analyse/Synthèse globale/Recommandations/Export PPT
d'incr.9, elle plantait de toute façon sur une mission sans trame.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import (
    GlobalSynthesis,
    Mission,
    MissionSwot,
    Recommendation,
    RecommendationAxis,
    Theme,
)
from ..services.ai_common import api_key_env_name
from ..services.pptx_export import field_fit_hint
from ..services.synthese_ai import (
    SynthesisAIError,
    generate_global_synthesis,
    generate_recommendations,
    generate_swot,
    is_configured,
)
from ..templating import templates

router = APIRouter(tags=["synthese"])

GLOBAL_SYNTH_FIELDS = (
    "contexte", "culture_adn", "forces_succes", "points_amelioration", "aspirations",
)
SWOT_FIELDS = ("forces", "faiblesses", "opportunites", "menaces")
RECO_TEXT_FIELDS = (
    "title", "objectif", "acteurs", "proposition_valeur", "plan_actions", "resultats_attendus",
)
RECO_SCORE_FIELDS = ("valeur", "complexite")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _get_mission(db: Session, mission_id: int) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable.")
    return mission


def _get_or_create_global_synthesis(db: Session, mission: Mission) -> GlobalSynthesis:
    if mission.global_synthesis is None:
        mission.global_synthesis = GlobalSynthesis(mission_id=mission.id)
        db.add(mission.global_synthesis)
    return mission.global_synthesis


def _get_or_create_swot(db: Session, mission: Mission) -> MissionSwot:
    if mission.swot is None:
        mission.swot = MissionSwot(mission_id=mission.id)
        db.add(mission.swot)
    return mission.swot


def _apply_swot_result(swot: MissionSwot, result: dict) -> None:
    for field in SWOT_FIELDS:
        setattr(swot, field, result[field])
    swot.status = "generated"
    swot.generated_at = datetime.now(timezone.utc)


def _get_recommendation(db: Session, recommendation_id: int) -> Recommendation:
    reco = db.get(Recommendation, recommendation_id)
    if reco is None:
        raise HTTPException(status_code=404, detail="Recommandation introuvable.")
    return reco


def _get_axis(db: Session, axis_id: int) -> RecommendationAxis:
    axis = db.get(RecommendationAxis, axis_id)
    if axis is None:
        raise HTTPException(status_code=404, detail="Axe introuvable.")
    return axis


def _hint_span(elem_id: str, field_key: str, text: str) -> str:
    """Repère "forme" en oob-swap (US2, éditeur par onglets) : sans effet sur
    les pages qui n'ont pas cet id dans leur DOM (recommandations.html,
    globale.html) — HTMX ignore silencieusement un oob-swap dont la cible est
    absente, donc les mêmes endpoints d'autosave servent les deux."""
    hint = field_fit_hint(field_key, text)
    return f'<span id="{elem_id}" class="fit-hint" hx-swap-oob="true">{hint}</span>'


# Le champ "title" d'une recommandation utilise une contrainte de forme
# différente des autres (titre de slide natif, pas un bloc de texte du
# gabarit) — cf. FIELD_SHAPE dans pptx_export.py.
_RECO_FIT_KEY = {"title": "reco_title"}


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


def _all_theme_material(mission: Mission) -> list[tuple[Theme, dict, list]]:
    """Matière (réponses + verbatims) de tous les thèmes de la trame — pour
    la synthèse globale, qui recoupe l'ensemble de la mission plutôt qu'un
    seul thème. Une mission brouillon née d'un entretien libre (incr.9) n'a
    pas de trame du tout."""
    if mission.trame is None:
        return []
    return [
        (theme, *_theme_material(mission, theme)) for theme in mission.trame.themes
    ]


def _libre_material(mission: Mission) -> list[tuple]:
    """Répartition (5 catégories) de chaque entretien en mode libre (incr.9,
    US9.6) — matière indépendante des thèmes, injectée à côté de
    `material_by_theme` dans `generate_global_synthesis`."""
    return [
        (iv, iv.repartition) for iv in mission.interviews
        if iv.mode == "libre" and iv.repartition
    ]


def _total_answer_count(material_by_theme: list[tuple[Theme, dict, list]]) -> int:
    return sum(_answer_count(by_question) for _theme, by_question, _v in material_by_theme)


# --------------------------------------------------------------------------- #
# Application en base d'un résultat de synthèse globale / recommandations —
# partagée entre la génération IA et l'import d'une analyse externe (évol),
# qui produisent toutes deux exactement la même forme de résultat.
# --------------------------------------------------------------------------- #
def _apply_global_synthesis_result(global_synthesis: GlobalSynthesis, result: dict) -> None:
    for field in GLOBAL_SYNTH_FIELDS:
        setattr(global_synthesis, field, result[field])
    global_synthesis.status = "generated"
    global_synthesis.generated_at = datetime.now(timezone.utc)


def _apply_recommendations_result(db: Session, mission: Mission, axes_data: list[dict]) -> None:
    # Remplace le jeu d'axes/recommandations précédent — même contrat que
    # "Régénérer" sur la synthèse par thème (un nouveau brouillon complet).
    for axis in list(mission.recommendation_axes):
        db.delete(axis)
    db.flush()
    for pos, axis_data in enumerate(axes_data):
        axis = RecommendationAxis(
            mission_id=mission.id, title=axis_data["title"], position=pos
        )
        db.add(axis)
        db.flush()
        for rpos, reco in enumerate(axis_data["recommendations"]):
            db.add(Recommendation(axis_id=axis.id, position=rpos, **reco))


# --------------------------------------------------------------------------- #
# Synthèse globale (évol) : mêmes entretiens, mais transverse à tous les
# thèmes de la trame — regroupés en 5 catégories fixes (contexte, culture,
# forces, points d'amélioration, aspirations).
# --------------------------------------------------------------------------- #
@router.get("/missions/{mission_id}/synthese/globale")
def global_synthese_view(
    mission_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    material_by_theme = _all_theme_material(mission)
    material_libre = _libre_material(mission)
    global_synthesis = _get_or_create_global_synthesis(db, mission)
    db.commit()

    return templates.TemplateResponse(
        request,
        "synthese/globale.html",
        {
            "mission": mission,
            "themes": mission.trame.themes if mission.trame else [],
            "global_synthesis": global_synthesis,
            "axes": mission.recommendation_axes,
            "ai_ready": is_configured(),
            "api_key_env": api_key_env_name(),
            "interview_count": len(mission.interviews),
            "answer_count": _total_answer_count(material_by_theme),
            "libre_count": len(material_libre),
        },
    )


@router.post("/missions/{mission_id}/synthese/globale/generate")
def generate_global(
    mission_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    material_by_theme = _all_theme_material(mission)
    material_libre = _libre_material(mission)
    global_synthesis = _get_or_create_global_synthesis(db, mission)

    error = None
    if not is_configured():
        error = (
            "Service IA indisponible — utilisez l'export pour lancer une "
            "analyse externe, puis importez le résultat."
        )
    elif _total_answer_count(material_by_theme) == 0 and not material_libre:
        error = "Aucune réponse saisie sur la mission — rien à synthétiser."
    else:
        try:
            result = generate_global_synthesis(mission, material_by_theme, material_libre)
            _apply_global_synthesis_result(global_synthesis, result)
            db.commit()
        except SynthesisAIError as exc:
            error = str(exc)

    return templates.TemplateResponse(
        request,
        "synthese/_global_panel.html",
        {
            "mission": mission,
            "global_synthesis": global_synthesis,
            "ai_ready": is_configured(),
            "api_key_env": api_key_env_name(),
            "error": error,
            "answer_count": _total_answer_count(material_by_theme),
        },
    )


@router.post("/syntheses/globale/{mission_id}/field")
def save_global_field(
    mission_id: int,
    field: str = Form(...),
    value: str = Form(""),
    db: Session = Depends(get_session),
):
    if field not in GLOBAL_SYNTH_FIELDS:
        raise HTTPException(status_code=400, detail="Champ inconnu.")
    mission = _get_mission(db, mission_id)
    global_synthesis = _get_or_create_global_synthesis(db, mission)
    setattr(global_synthesis, field, value)
    if global_synthesis.has_content:
        global_synthesis.status = "edited"
    db.commit()
    badge = (
        f'<span class="badge badge-synth-{global_synthesis.status}" id="global-synth-status" '
        f'hx-swap-oob="true">{global_synthesis.status_label}</span>'
    )
    hint = _hint_span(f"fit-hint-global-{field}", "synthese_categorie", value)
    return HTMLResponse(f'<span class="saved">✓ enregistré</span>{badge}{hint}')


# --------------------------------------------------------------------------- #
# Recommandations (évol) : dérivées de la synthèse globale déjà générée,
# regroupées en quelques axes transverses à la mission (pas un axe par thème).
# --------------------------------------------------------------------------- #
@router.get("/missions/{mission_id}/recommandations")
def recommendations_view(
    mission_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    return templates.TemplateResponse(
        request,
        "synthese/recommandations.html",
        {
            "mission": mission,
            "themes": mission.trame.themes if mission.trame else [],
            "axes": mission.recommendation_axes,
            "global_synthesis": mission.global_synthesis,
            "ai_ready": is_configured(),
            "api_key_env": api_key_env_name(),
        },
    )


@router.post("/missions/{mission_id}/recommandations/generate")
def generate_recommendations_view(
    mission_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    global_synthesis = mission.global_synthesis

    error = None
    if not is_configured():
        error = (
            "Service IA indisponible — utilisez l'export pour lancer une "
            "analyse externe, puis importez le résultat."
        )
    elif global_synthesis is None or not global_synthesis.has_content:
        error = "Générez d'abord la synthèse globale — les recommandations en découlent."
    else:
        try:
            axes_data = generate_recommendations(global_synthesis)
            _apply_recommendations_result(db, mission, axes_data)
            db.commit()
            db.refresh(mission)
        except SynthesisAIError as exc:
            error = str(exc)

    return templates.TemplateResponse(
        request,
        "synthese/recommandations.html",
        {
            "mission": mission,
            "themes": mission.trame.themes if mission.trame else [],
            "axes": mission.recommendation_axes,
            "global_synthesis": mission.global_synthesis,
            "ai_ready": is_configured(),
            "api_key_env": api_key_env_name(),
            "error": error,
        },
    )


@router.post("/recommandations/{recommendation_id}/field")
def save_recommendation_field(
    recommendation_id: int,
    field: str = Form(...),
    value: str = Form(""),
    db: Session = Depends(get_session),
):
    reco = _get_recommendation(db, recommendation_id)
    if field in RECO_TEXT_FIELDS:
        setattr(reco, field, value)
    elif field in RECO_SCORE_FIELDS:
        try:
            score = int(value)
        except ValueError:
            raise HTTPException(status_code=400, detail="Score invalide.")
        setattr(reco, field, max(1, min(5, score)))
    else:
        raise HTTPException(status_code=400, detail="Champ inconnu.")
    db.commit()
    hint = ""
    if field in RECO_TEXT_FIELDS:
        hint = _hint_span(
            f"fit-hint-reco-{recommendation_id}-{field}", _RECO_FIT_KEY.get(field, field), value
        )
    return HTMLResponse(f'<span class="saved">✓ enregistré</span>{hint}')


@router.post("/recommandations/axes/{axis_id}/field")
def save_axis_field(
    axis_id: int,
    field: str = Form(...),
    value: str = Form(""),
    db: Session = Depends(get_session),
):
    axis = _get_axis(db, axis_id)
    if field != "title":
        raise HTTPException(status_code=400, detail="Champ inconnu.")
    axis.title = value
    db.commit()
    hint = _hint_span(f"fit-hint-axis-{axis_id}", "axis_title", value)
    return HTMLResponse(f'<span class="saved">✓ enregistré</span>{hint}')


# --------------------------------------------------------------------------- #
# SWOT (Palier 1 restitution) : matrice dérivée de la synthèse globale, éditée
# dans l'onglet SWOT de l'aperçu. La génération vit dans export.py (avec
# l'aperçu) ; ici l'autosave par quadrant, même contrat que la synthèse globale.
# --------------------------------------------------------------------------- #
@router.post("/swot/{mission_id}/field")
def save_swot_field(
    mission_id: int,
    field: str = Form(...),
    value: str = Form(""),
    db: Session = Depends(get_session),
):
    if field not in SWOT_FIELDS:
        raise HTTPException(status_code=400, detail="Champ inconnu.")
    mission = _get_mission(db, mission_id)
    swot = _get_or_create_swot(db, mission)
    setattr(swot, field, value)
    if swot.has_content:
        swot.status = "edited"
    db.commit()
    badge = (
        f'<span class="badge badge-synth-{swot.status}" id="swot-status" '
        f'hx-swap-oob="true">{swot.status_label}</span>'
    )
    hint = _hint_span(f"fit-hint-swot-{field}", "swot_quadrant", value)
    return HTMLResponse(f'<span class="saved">✓ enregistré</span>{badge}{hint}')


# --------------------------------------------------------------------------- #
# Verbatims restitués (Palier 2) : sélection des citations pour la planche
# « Paroles d'acteurs ». Approche légère — on maintient sur la mission la liste
# ordonnée d'ids de `Verbatim` déjà en base (aucun nouveau modèle de citation).
# --------------------------------------------------------------------------- #
@router.post("/missions/{mission_id}/verbatims/toggle")
def toggle_verbatim_selection(
    mission_id: int,
    verbatim_id: int = Form(...),
    selected: bool = Form(False),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    valid = {v.id for v in mission.all_verbatims}
    # Repart de la sélection courante nettoyée des ids périmés, préserve l'ordre.
    current = [i for i in (mission.restitution_verbatim_ids or []) if i in valid]
    if selected and verbatim_id in valid and verbatim_id not in current:
        current.append(verbatim_id)
    elif not selected:
        current = [i for i in current if i != verbatim_id]
    # Réassignation (pas de mutation in-place) pour que SQLAlchemy voie le JSON changé.
    mission.restitution_verbatim_ids = current
    db.commit()
    return HTMLResponse(
        f'<span class="saved" id="verbatims-count" hx-swap-oob="true">'
        f'✓ {len(current)} citation(s) retenue(s) pour la planche</span>'
    )
