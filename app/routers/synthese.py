"""Synthèse transverse — mission (incr.3, étendue incr.9 aux missions sans trame).

Synthèse globale (axes d'étude configurables depuis le 2026-07-27, cf.
`services/mission_axes.py`) + recommandations, agrégeant tous les
entretiens de la mission (structurés par thème et/ou libres). L'ancienne vue
de synthèse par thème (US4.1-4.3) a été retirée le 2026-07-17 : superflue
depuis l'écran unifié Analyse/Synthèse globale/Recommandations/Export PPT
d'incr.9, elle plantait de toute façon sur une mission sans trame.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import (
    GlobalSynthesis,
    Mission,
    MissionDifficulty,
    MissionSynthesisAxis,
    Recommendation,
    RecommendationAxis,
)
from ..services.ai_common import api_key_env_name
from ..services.global_synthesis_job import run_global_synthesis_job
from ..services.pptx_export import field_fit_hint
from ..services.mission_axes import axes_of, creer_axe, supprimer_axe
from ..services.synthese_ecriture import (
    EXEC_SUMMARY_FIELDS,
    SWOT_FIELDS,
    apply_difficulties_result,
    apply_executive_summary_result,
    apply_global_synthesis_result,
    apply_recommendations_result,
    apply_swot_result,
    get_or_create_executive_summary,
    get_or_create_global_synthesis,
    get_or_create_swot,
)
from ..services.synthese_ai import (
    SynthesisAIError,
    ai_precondition_error,
    generate_global_synthesis,
    generate_recommendations,
    generate_swot,
    is_configured,
)
from ..services.synthese_material import (
    all_theme_material as _all_theme_material,
    libre_material as _libre_material,
    total_answer_count as _total_answer_count,
)
from ..templating import templates

router = APIRouter(tags=["synthese"])

# Les rubriques de la synthese globale ne sont plus une constante depuis le
# 2026-07-27 : ce sont les AXES de la mission (`mission_axes`), configurables.
# La constante historique ne sert plus qu'a decrire les 5 defauts, via le
# service — toute lecture passe par `axes_of(db, mission)`.
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


def _get_difficulty(db: Session, difficulty_id: int) -> MissionDifficulty:
    d = db.get(MissionDifficulty, difficulty_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Difficulté introuvable.")
    return d


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
    global_synthesis = get_or_create_global_synthesis(db, mission)
    db.commit()

    return templates.TemplateResponse(
        request,
        "synthese/globale.html",
        {
            "mission": mission,
            "themes": mission.trame.themes if mission.trame else [],
            "global_synthesis": global_synthesis,
            "synthesis_axes": axes_of(db, mission),
            "axes": mission.recommendation_axes,
            "ai_ready": is_configured(),
            "api_key_env": api_key_env_name(),
            "interview_count": len(mission.interviews),
            "answer_count": _total_answer_count(material_by_theme),
            "libre_count": len(material_libre),
            # Erreur d'une génération précédente, en LECTURE SEULE (contrairement
            # à generate_global, un chargement de page ne l'acquitte/n'efface
            # jamais) : sans ce champ, un rechargement après échec n'affichait
            # rien (revue adversariale 2026-09-07).
            "error": (
                global_synthesis.generation_error
                if global_synthesis.generation_status == "error"
                else None
            ),
        },
    )


def _global_panel_context(
    request: Request, db: Session, mission: Mission, global_synthesis: GlobalSynthesis, error: str | None,
) -> dict:
    material_by_theme = _all_theme_material(mission)
    return {
        "request": request,
        "mission": mission,
        "global_synthesis": global_synthesis,
        "synthesis_axes": axes_of(db, mission),
        "ai_ready": is_configured(),
        "api_key_env": api_key_env_name(),
        "error": error,
        "answer_count": _total_answer_count(material_by_theme),
    }


@router.post("/missions/{mission_id}/synthese/globale/generate")
def generate_global(
    mission_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Lance la génération en TÂCHE DE FOND (2026-09-04, finding
    audit-technique performance:critique) — le map-reduce peut dépasser
    100 min mesurées, inacceptable dans le thread d'une requête HTTP. N'est
    JAMAIS la cible du polling (cf. `global_synthesis_status` ci-dessous,
    en lecture seule) : si elle l'était, un poll arrivant APRÈS la fin d'un
    job (`generation_status` déjà repassé à `idle`) serait indiscernable
    d'un clic explicite et relancerait une génération inutile. Le travail
    réel vit dans `services/global_synthesis_job.run_global_synthesis_job`,
    qui ouvre sa propre session — celle-ci est fermée dès cette réponse
    renvoyée."""
    mission = _get_mission(db, mission_id)
    material_by_theme = _all_theme_material(mission)
    material_libre = _libre_material(mission)
    global_synthesis = get_or_create_global_synthesis(db, mission)

    error = None
    if global_synthesis.generation_status == "error":
        # L'erreur reste collée à l'écran (visible au chargement passif de la
        # page, cf. global_synthese_view) tant qu'aucun clic ne la consomme —
        # elle n'a donc plus besoin d'être réaffichée ICI : un clic sur ce
        # bouton VAUT acquittement, il efface le statut d'erreur ET enchaîne
        # sur la décision de lancement ci-dessous (revue adversariale
        # 2026-09-07 : avant ce correctif, un premier clic après échec ne
        # faisait qu'effacer l'erreur SANS relancer, obligeant un second clic).
        global_synthesis.generation_status = "idle"
        global_synthesis.generation_error = None
        db.commit()

    if global_synthesis.generation_status == "running":
        pass  # confort d'affichage : rien à relancer, le panneau montre l'état.
    elif not is_configured():
        error = (
            "Service IA indisponible — utilisez l'export pour lancer une "
            "analyse externe, puis importez le résultat."
        )
    elif _total_answer_count(material_by_theme) == 0 and not material_libre:
        error = "Aucune réponse saisie sur la mission — rien à synthétiser."
    else:
        # Prise de jeton ATOMIQUE, en base (audit-technique robustesse du
        # 2026-09-09) : le lire-puis-écrire Python d'avant était un TOCTOU —
        # deux requêtes quasi simultanées lisaient toutes deux un statut
        # `!= "running"` avant qu'aucune n'ait commité, et lançaient DEUX
        # map-reduce (plusieurs dizaines de minutes d'IA payées deux fois,
        # écriture concurrente du même GlobalSynthesis). Le seul rempart était
        # le bouton désactivé côté JS, contourné par un double-clic, un second
        # onglet ou un appel direct de la route.
        #
        # L'UPDATE conditionnel tranche côté SQLite : le second exécutant
        # attend le verrou d'écriture du premier, relit alors `running` et
        # repart avec `rowcount == 0`. Pas de verrou applicatif en mémoire :
        # il ne survivrait pas à un second processus, et le dépôt n'en a
        # aucun de ce genre (seul `audio_transcribe._MODEL_LOCK` existe, et il
        # protège un modèle en RAM, pas un état persistant).
        # Flush AVANT de composer le WHERE : sur une mission qui n'a pas encore
        # de ligne, `get_or_create_global_synthesis` se contente d'un `db.add()`
        # et l'id vaut encore None — la clause serait construite en `id IS NULL`,
        # ne matcherait rien, et PLUS AUCUNE première génération ne démarrerait
        # (attrapé par `test_mission_trame_flow.py::test_global_synthesis_
        # generate_and_autosave` avant commit).
        db.flush()
        pris = db.execute(
            update(GlobalSynthesis)
            .where(
                GlobalSynthesis.id == global_synthesis.id,
                GlobalSynthesis.generation_status != "running",
            )
            .values(generation_status="running", generation_error=None)
        ).rowcount
        db.commit()
        # `expire_on_commit=False` (cf. `db.SessionLocal`) : l'UPDATE Core ne
        # rafraîchit pas forcément l'objet déjà chargé, et c'est LUI que le
        # gabarit rend. Sans ce refresh, le panneau du perdant afficherait
        # encore « idle » alors qu'une génération tourne.
        db.refresh(global_synthesis)
        if pris:
            background_tasks.add_task(run_global_synthesis_job, mission.id)

    return templates.TemplateResponse(
        request,
        "synthese/_global_panel.html",
        _global_panel_context(request, db, mission, global_synthesis, error),
    )


@router.get("/missions/{mission_id}/synthese/globale/status")
def global_synthesis_status(
    mission_id: int, request: Request, db: Session = Depends(get_session)
):
    """Poll en LECTURE SEULE de l'avancement — jamais de mutation ici,
    contrairement à `generate_global` : ne JAMAIS courir avec un clic
    explicite sur le même état, et ne jamais confondre un poll arrivé tard
    avec une demande de nouvelle génération. Cible du `hx-trigger="load
    delay:2s"` du panneau tant que `generation_status == "running"`."""
    mission = _get_mission(db, mission_id)
    global_synthesis = get_or_create_global_synthesis(db, mission)
    error = (
        global_synthesis.generation_error
        if global_synthesis.generation_status == "error"
        else None
    )
    return templates.TemplateResponse(
        request,
        "synthese/_global_panel.html",
        _global_panel_context(request, db, mission, global_synthesis, error),
    )


# --------------------------------------------------------------------------- #
# Axes d'étude de la mission (2026-07-27) — onglet 1 de l'étape Analyse.
# Ils pilotent le gabarit d'export, le prompt IA, l'aperçu et le PPT : c'est
# pour ça qu'ils sont configurés AVANT les deux autres onglets.
# --------------------------------------------------------------------------- #
def _get_axe(db: Session, mission: Mission, axe_id: int) -> MissionSynthesisAxis:
    axe = db.get(MissionSynthesisAxis, axe_id)
    if axe is None or axe.mission_id != mission.id:
        # Jamais l'axe d'une autre mission, même avec un id valide.
        raise HTTPException(status_code=404, detail="Axe introuvable.")
    return axe


@router.post("/missions/{mission_id}/axes")
def create_axe(
    mission_id: int,
    label: str = Form(""),
    hint: str = Form(""),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    if label.strip():
        creer_axe(db, mission, label, hint)
    return RedirectResponse(
        f"/missions/{mission_id}/synthese/export-import", status_code=303
    )


@router.post("/missions/{mission_id}/axes/{axe_id}")
def update_axe(
    mission_id: int,
    axe_id: int,
    label: str = Form(""),
    hint: str = Form(""),
    db: Session = Depends(get_session),
):
    """Renomme un axe / change sa consigne. `key` n'est JAMAIS touchée : c'est
    l'adresse du contenu déjà rédigé (`GlobalSynthesis.valeurs`,
    `Interview.repartition`) — le renommer le rendrait orphelin."""
    mission = _get_mission(db, mission_id)
    axe = _get_axe(db, mission, axe_id)
    if label.strip():
        axe.label = label.strip()
    axe.hint = hint.strip()
    db.commit()
    return RedirectResponse(
        f"/missions/{mission_id}/synthese/export-import", status_code=303
    )


@router.post("/missions/{mission_id}/axes/{axe_id}/delete")
def delete_axe(mission_id: int, axe_id: int, db: Session = Depends(get_session)):
    mission = _get_mission(db, mission_id)
    supprimer_axe(db, mission, _get_axe(db, mission, axe_id))
    return RedirectResponse(
        f"/missions/{mission_id}/synthese/export-import", status_code=303
    )


@router.post("/syntheses/globale/{mission_id}/field")
def save_global_field(
    mission_id: int,
    field: str = Form(...),
    value: str = Form(""),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    if field not in {axe.key for axe in axes_of(db, mission)}:
        # Axe supprime entre l'affichage de l'ecran et la frappe : refuser
        # plutot que d'ecrire une cle qui ne sera plus jamais lue.
        raise HTTPException(status_code=400, detail="Champ inconnu.")
    global_synthesis = get_or_create_global_synthesis(db, mission)
    global_synthesis.set_contenu(field, value)
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

    error = ai_precondition_error(
        global_synthesis,
        "Générez d'abord la synthèse globale — les recommandations en découlent.",
    )
    if error is None:
        try:
            axes_data = generate_recommendations(global_synthesis, axes_of(db, mission))
            apply_recommendations_result(db, mission, axes_data)
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
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Score invalide.") from exc
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
    swot = get_or_create_swot(db, mission)
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


@router.post("/difficultes/{difficulty_id}/field")
def save_difficulty_field(
    difficulty_id: int,
    value: str = Form(""),
    db: Session = Depends(get_session),
):
    d = _get_difficulty(db, difficulty_id)
    d.label = value
    db.commit()
    hint = _hint_span(f"fit-hint-diff-{difficulty_id}", "difficulty_label", value)
    return HTMLResponse(f'<span class="saved">✓ enregistré</span>{hint}')


@router.post("/difficultes/{difficulty_id}/verbatim")
def set_difficulty_verbatim(
    difficulty_id: int,
    verbatim_id: str = Form(""),
    db: Session = Depends(get_session),
):
    """Lie (ou délie) un verbatim à une difficulté — l'encadré citation de la slide.
    verbatim_id vide = aucun ; sinon validé contre les verbatims de la mission de la
    difficulté (jamais un verbatim d'une autre mission)."""
    d = _get_difficulty(db, difficulty_id)
    vid = None
    if verbatim_id.strip():
        try:
            candidate = int(verbatim_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Verbatim invalide.") from exc
        if candidate not in {v.id for v in d.mission.all_verbatims}:
            raise HTTPException(status_code=400, detail="Verbatim inconnu pour cette mission.")
        vid = candidate
    d.verbatim_id = vid
    db.commit()
    return HTMLResponse('<span class="saved">✓ verbatim lié</span>')


@router.post("/executive-summary/{mission_id}/field")
def save_executive_summary_field(
    mission_id: int,
    field: str = Form(...),
    value: str = Form(""),
    db: Session = Depends(get_session),
):
    if field not in EXEC_SUMMARY_FIELDS:
        raise HTTPException(status_code=400, detail="Champ inconnu.")
    mission = _get_mission(db, mission_id)
    es = get_or_create_executive_summary(db, mission)
    setattr(es, field, value)
    if es.has_content:
        es.status = "edited"
    db.commit()
    badge = (
        f'<span class="badge badge-synth-{es.status}" id="es-status" '
        f'hx-swap-oob="true">{es.status_label}</span>'
    )
    hint = _hint_span(f"fit-hint-es-{field}", f"es_{field}", value)
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
    # Même wording que le rendu initial d'apercu.html (revue adversariale
    # 2026-07-23 : le fragment serveur réintroduisait « citation(s) … planche »
    # au premier clic, annulant le fix template P2-12/P2-13).
    s = "s" if len(current) > 1 else ""
    return HTMLResponse(
        f'<span class="saved" id="verbatims-count" hx-swap-oob="true">'
        f'✓ {len(current)} verbatim{s} retenu{s} pour la slide</span>'
    )
