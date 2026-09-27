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

import asyncio
import io
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session, selectinload

from ..db import PPTX_EXEMPLES_DIR, PPTX_TEMPLATES_DIR, get_session
from ..models import MATURITE_NIVEAUX, RISK_CONTROL_TYPES, RISK_LEVELS, Interview, Mission
from ..services.ai_common import api_key_env_name, is_configured
from ..services.analyse_import import (
    AnalysisParseError,
    decode_text_upload,
    parse_analysis_markdown,
)
from ..services.mission_axes import axes_of
from ..services.mission_export import build_export_markdown, slugify
from ..services.pptx_export import build_presentation
from ..services.pptx_export.archetypes import (
    LIBELLES,
    STRUCTURELS,
    Archetype,
    extraire_plan,
    extraire_plan_fichier,
    plan_applicable,
)
from ..services.synthese_ai import (
    SynthesisAIError,
    ai_precondition_error,
    generate_difficulties,
    generate_executive_summary,
    generate_kpis,
    generate_maturite,
    generate_risks,
    generate_swot,
)
from ..services.synthese_ecriture import (
    apply_difficulties_result,
    apply_executive_summary_result,
    apply_global_synthesis_result,
    apply_kpis_result,
    apply_maturite_result,
    apply_recommendations_result,
    apply_risks_result,
    apply_swot_result,
    get_or_create_executive_summary,
    get_or_create_global_synthesis,
    get_or_create_swot,
)
from ..services.synthese_material import all_theme_material as _all_theme_material
from ..services.synthese_material import total_answer_count as _total_answer_count
from ..templating import templates
from ..uploads import UploadTropVolumineux, lire_upload_borne, verifier_zip_borne

router = APIRouter(tags=["export"])

logger = logging.getLogger(__name__)


def _get_mission(db: Session, mission_id: int) -> Mission:
    """Point d'entrée commun de toutes les routes de ce fichier, TOUTES
    rendent `synthese/apercu.html` (aperçu, génération SWOT/difficultés/
    executive summary, import d'analyse).

    `selectinload` sur `interviews.verbatims` (2026-09-11, constat d'audit
    performance du 2026-09-09, N+1 systémique — « zéro selectinload dans tout
    app/ ») : `Mission.all_verbatims` (property nue, `models.py`) est évaluée
    plusieurs fois par `apercu.html` pour la planche « Paroles d'acteurs ».
    Coût mesuré en ISOLANT `_get_mission` + `all_verbatims` + `v.interview`
    (`test_apercu_verbatims_cout_sql.py`, pas le total de la route HTTP —
    celui-ci reste linéaire à cause d'`all_theme_material`, cf. plus bas) :
    4 requêtes pour 2 entretiens, 17 pour 15 sans ce correctif ; 3, constant,
    avec lui. Le double N+1 était celui d'`iv.verbatims` PAR entretien et de
    `v.interview` PAR verbatim (le retour arrière n'est peuplé par l'identity
    map que si l'entretien parent est déjà chargé — c'est ce que résout le
    `selectinload` ci-dessous, sans requête supplémentaire dédiée).
    Ciblé sur CE SEUL chemin (mandat : pas de refactor balayant) — laisse
    `Mission.all_verbatims` et les autres appelants (routers/synthese.py)
    inchangés, eux non mesurés comme coûteux ici. `all_theme_material`,
    l'autre N+1 nommé par le même audit et appelé par les mêmes routes
    (`_synthese_context` ci-dessous), a déjà été mesuré et volontairement
    laissé tel quel (revue du 2026-09-10 : 26 requêtes/8 ms, négligeable
    devant l'appel IA qui suit) — pas rouvert ici."""
    mission = db.get(
        Mission, mission_id,
        options=[selectinload(Mission.interviews).selectinload(Interview.verbatims)],
    )
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable.")
    return mission


def _plan_exemple(mission: Mission) -> list | None:
    """Plan d'archétypes du deck d'exemple de la mission (US5.2), ou None sans
    deck d'exemple. Un fichier disparu ou devenu illisible ne casse ni l'écran
    ni l'export : journalisé, et l'export retombe sur l'ordre par défaut."""
    if not mission.pptx_exemple_path:
        return None
    chemin = PPTX_EXEMPLES_DIR / mission.pptx_exemple_path
    try:
        plan = extraire_plan_fichier(chemin)
    except Exception:
        logger.warning("Deck d'exemple illisible, plan ignoré (mission %s) : %s",
                       mission.id, chemin, exc_info=True)
        return None
    # Même règle que l'upload : sans bloc de contenu, pas de plan (l'écran
    # n'annonce alors pas un plan que l'export ignorerait). Journalisé à part du
    # cas « fichier illisible » ci-dessus : les deux rendent None et l'écran dit
    # la même chose, mais le diagnostic n'est pas le même (fichier cassé vs deck
    # lisible dont aucune slide de contenu n'a été reconnue).
    if not plan_applicable(plan):
        logger.warning(
            "Deck d'exemple lisible mais sans plan de contenu reconnu, plan ignoré "
            "(mission %s) : %s — archétypes reconnus : %s",
            mission.id, chemin, [a.value for a in plan] or "aucun")
        return None
    return plan


def _synthese_context(db: Session, mission: Mission, error: str | None = None) -> dict:
    """Contexte de gabarit partagé par l'étape 1 (analyse — IA intégrée +
    export/import manuel) et l'étape 4 (export PPT) — toutes ont besoin de
    savoir si de la matière existe déjà ; l'étape 1 en plus affiche le
    sous-onglet "IA intégrée" (bouton Générer/Régénérer réutilisé depuis
    `_global_panel.html`), qui a besoin de `ai_ready`/`api_key_env`/
    `answer_count` comme la page synthèse globale elle-même."""
    material_by_theme = _all_theme_material(mission)
    plan = _plan_exemple(mission)
    return {
        "mission": mission,
        "themes": mission.trame.themes if mission.trame else [],
        "global_synthesis": mission.global_synthesis,
        "swot": mission.swot,
        "executive_summary": mission.executive_summary,
        "difficulties": mission.difficulties,
        "kpis": mission.kpis,
        "risks": mission.risks,
        "risk_levels": RISK_LEVELS,
        "risk_control_types": RISK_CONTROL_TYPES,
        "maturites": mission.maturites,
        "maturite_niveaux": MATURITE_NIVEAUX,
        "axes": mission.recommendation_axes,
        # Axes d'etude configurables (2026-07-27) : l'onglet de parametrage,
        # les champs de la synthese et l'apercu sont tous rendus depuis cette
        # liste — plus aucune rubrique n'est ecrite en dur dans un gabarit.
        "synthesis_axes": axes_of(db, mission),
        "error": error,
        "plan_exemple": [LIBELLES[a] for a in (plan or [])],
        # Cases « Définition des slides » que le plan écarte : rendues décochées
        # et désactivées (arbitrage orchestrateur incr.8, réversible).
        "plan_exclus": [a.value for a in Archetype
                        if plan and a not in plan and a not in STRUCTURELS]
                       + (["sommaire"] if plan and Archetype.SOMMAIRE not in plan else []),
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
    get_or_create_global_synthesis(db, mission)
    db.commit()
    return templates.TemplateResponse(request, "synthese/export_import.html", _synthese_context(db, mission))


@router.get("/missions/{mission_id}/export/interviews")
def export_interviews(mission_id: int, db: Session = Depends(get_session)):
    mission = _get_mission(db, mission_id)
    content = build_export_markdown(mission, axes_of(db, mission))
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
    global_synthesis = get_or_create_global_synthesis(db, mission)
    db.commit()
    try:
        raw = await lire_upload_borne(file)
        text = decode_text_upload(raw)
        parsed = parse_analysis_markdown(text, axes_of(db, mission))

        if any((v or "").strip() for v in parsed["global_synthesis"].values()):
            apply_global_synthesis_result(global_synthesis, parsed["global_synthesis"])
        if parsed["axes"]:
            apply_recommendations_result(db, mission, parsed["axes"])
        db.commit()
    except (AnalysisParseError, UploadTropVolumineux) as exc:
        # Deux exceptions PORTEUSES d'un message écrit pour l'utilisateur (le
        # détail du parsing, ou le plafond dépassé) : rendues telles quelles,
        # à la différence du garde-fou générique ci-dessous.
        db.rollback()
        return templates.TemplateResponse(
            request, "synthese/export_import.html", _synthese_context(db, mission, error=str(exc))
        )
    except Exception:  # garde-fou : jamais de 500 brute sur un import utilisateur
        # Message FIXE : l'exception attrapée ici est quelconque, son texte
        # peut porter les chemins absolus du poste. Détail dans le journal
        # serveur (même règle que les 5 sites de `interviews.py`, finding
        # audit-technique securite du 2026-09-04).
        logger.exception("Échec inattendu de l'import d'analyse (mission %s)", mission_id)
        db.rollback()
        return templates.TemplateResponse(
            request,
            "synthese/export_import.html",
            _synthese_context(
                db,
                mission,
                error="Échec de l'import : le fichier n'a pas pu être lu.",
            ),
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
    return templates.TemplateResponse(request, "synthese/apercu.html", _synthese_context(db, mission))


@router.post("/missions/{mission_id}/swot/generate")
def generate_swot_view(mission_id: int, request: Request, db: Session = Depends(get_session)):
    """Génère la matrice SWOT à partir de la synthèse globale déjà produite (pas
    des réponses brutes, comme les recommandations), puis ré-affiche l'aperçu —
    l'onglet SWOT montre le résultat, éditable. Pré-condition : la synthèse
    globale doit exister (la SWOT en découle)."""
    mission = _get_mission(db, mission_id)
    global_synthesis = mission.global_synthesis
    swot = get_or_create_swot(db, mission)

    error = ai_precondition_error(
        global_synthesis, "Générez d'abord la synthèse globale — la SWOT en découle."
    )
    if error is None:
        try:
            result = generate_swot(global_synthesis, axes_of(db, mission))
            apply_swot_result(swot, result)
            db.commit()
        except SynthesisAIError as exc:
            error = str(exc)

    return templates.TemplateResponse(
        request, "synthese/apercu.html", _synthese_context(db, mission, error)
    )


@router.post("/missions/{mission_id}/executive-summary/generate")
def generate_executive_summary_view(
    mission_id: int, request: Request, db: Session = Depends(get_session)
):
    """Génère l'executive summary à partir de la synthèse globale déjà produite
    (comme la SWOT), puis ré-affiche l'aperçu — l'onglet Executive Summary montre
    le résultat, éditable. Pré-condition : la synthèse globale doit exister."""
    mission = _get_mission(db, mission_id)
    global_synthesis = mission.global_synthesis
    es = get_or_create_executive_summary(db, mission)

    error = ai_precondition_error(
        global_synthesis,
        "Générez d'abord la synthèse globale — l'executive summary en découle.",
    )
    if error is None:
        try:
            result = generate_executive_summary(global_synthesis, axes_of(db, mission))
            apply_executive_summary_result(es, result)
            db.commit()
        except SynthesisAIError as exc:
            error = str(exc)

    return templates.TemplateResponse(
        request, "synthese/apercu.html", _synthese_context(db, mission, error)
    )


@router.post("/missions/{mission_id}/difficultes/generate")
def generate_difficulties_view(
    mission_id: int, request: Request, db: Session = Depends(get_session)
):
    """Génère la liste ordonnée des difficultés à partir de la synthèse globale
    (surtout points_amelioration), puis ré-affiche l'aperçu — l'onglet Difficultés
    montre le résultat, éditable, un verbatim liable par difficulté. Pré-condition :
    la synthèse globale doit exister."""
    mission = _get_mission(db, mission_id)
    global_synthesis = mission.global_synthesis

    error = ai_precondition_error(
        global_synthesis,
        "Générez d'abord la synthèse globale — les difficultés en découlent.",
    )
    if error is None:
        try:
            labels = generate_difficulties(global_synthesis, axes_of(db, mission))
            if not labels:
                # Ne JAMAIS écraser une liste affinée (+ liens citation) par du vide :
                # modèle muet ou clé JSON inattendue -> on garde l'existant, on signale.
                error = (
                    "La génération n'a produit aucune difficulté — liste inchangée. "
                    "Réessayez, ou vérifiez la synthèse globale."
                )
            else:
                apply_difficulties_result(db, mission, labels)
                db.commit()
        except SynthesisAIError as exc:
            error = str(exc)

    return templates.TemplateResponse(
        request, "synthese/apercu.html", _synthese_context(db, mission, error)
    )


def _generate_liste_view(
    request: Request, db: Session, mission_id: int, *, generer, appliquer,
    manque_synthese: str, vide: str,
):
    """Patron commun des listes dérivées de la trajectoire (KPIs, risques) :
    précondition IA + synthèse, génération depuis la synthèse ET les axes de
    recommandation, jamais d'écrasement d'une liste affinée par un résultat vide
    (même règle que les difficultés), puis ré-affichage de l'aperçu."""
    mission = _get_mission(db, mission_id)
    global_synthesis = mission.global_synthesis
    error = ai_precondition_error(global_synthesis, manque_synthese)
    if error is None:
        try:
            items = generer(
                global_synthesis, axes_of(db, mission), list(mission.recommendation_axes)
            )
            if not items:
                error = vide
            else:
                appliquer(mission, items)
                db.commit()
        except SynthesisAIError as exc:
            error = str(exc)
    return templates.TemplateResponse(
        request, "synthese/apercu.html", _synthese_context(db, mission, error)
    )


@router.post("/missions/{mission_id}/kpis/generate")
def generate_kpis_view(mission_id: int, request: Request, db: Session = Depends(get_session)):
    """Génère les indicateurs de suivi (US9.27 b) depuis la synthèse globale et
    les recommandations — l'onglet Indicateurs montre le résultat, éditable."""
    return _generate_liste_view(
        request, db, mission_id, generer=generate_kpis, appliquer=apply_kpis_result,
        manque_synthese="Générez d'abord la synthèse globale — les indicateurs en découlent.",
        vide=("La génération n'a produit aucun indicateur — liste inchangée. "
              "Réessayez, ou vérifiez la synthèse globale."),
    )


@router.post("/missions/{mission_id}/risques/generate")
def generate_risks_view(mission_id: int, request: Request, db: Session = Depends(get_session)):
    """Génère la matrice risques-contrôles (US9.27 c) depuis la synthèse globale
    et les recommandations — l'onglet Risques montre le résultat, éditable."""
    return _generate_liste_view(
        request, db, mission_id, generer=generate_risks, appliquer=apply_risks_result,
        manque_synthese="Générez d'abord la synthèse globale — les risques en découlent.",
        vide=("La génération n'a produit aucun risque — matrice inchangée. "
              "Réessayez, ou vérifiez la synthèse globale."),
    )


@router.post("/missions/{mission_id}/maturite/generate")
def generate_maturite_view(mission_id: int, request: Request, db: Session = Depends(get_session)):
    """Génère la grille de maturité 0-3 par pilier (incr.10 palier 3) — les
    piliers sont les THÈMES de la trame ; sans thème, rien à évaluer."""
    mission = _get_mission(db, mission_id)
    piliers = [t.title for t in (mission.trame.themes if mission.trame else [])
               if (t.title or "").strip()]
    if not piliers:
        return templates.TemplateResponse(
            request, "synthese/apercu.html", _synthese_context(
                db, mission,
                "La trame n'a aucun thème : la grille de maturité évalue un pilier par thème."),
        )
    return _generate_liste_view(
        request, db, mission_id,
        generer=lambda gs, axes, _reco: generate_maturite(gs, axes, piliers),
        appliquer=apply_maturite_result,
        manque_synthese="Générez d'abord la synthèse globale — la grille de maturité en découle.",
        vide=("La génération n'a produit aucun score sur les thèmes de la trame — grille "
              "inchangée. Réessayez, ou vérifiez la synthèse globale."),
    )


# --------------------------------------------------------------------------- #
# Étape 4 — Export PowerPoint (respecte la sélection de slides si soumise)
# --------------------------------------------------------------------------- #
@router.get("/missions/{mission_id}/export/pptx")
async def export_pptx(
    mission_id: int,
    request: Request,
    db: Session = Depends(get_session),
    config_submitted: bool = False,
    sommaire: bool = False,
    executive_summary: bool = False,
    synthese: bool = False,
    difficultes: bool = False,
    swot: bool = False,
    verbatims: bool = False,
    axes_overview: bool = False,
    matrix: bool = False,
    kpis: bool = False,
    risques: bool = False,
    maturite: bool = False,
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
            include_executive_summary=executive_summary,
            include_synthese=synthese,
            include_difficultes=difficultes,
            include_swot=swot,
            include_verbatims=verbatims,
            include_axes_overview=axes_overview,
            include_matrix=matrix,
            include_kpis=kpis,
            include_risques=risques,
            include_maturite=maturite,
            include_axis_ids=set(axis),
        )
    else:
        include_kwargs = {}

    try:
        # Le fetch photo Openverse (app/services/pptx_export/images.py) est en
        # série et purement synchrone : injoignable, il bloquait jusqu'à
        # l'ordre de 4 scènes x 3 orientations x 140 s dans le thread de CETTE
        # requête (constat audit-technique performance VSCode2, 2026-09-11).
        # Même pattern que `interviews.py` pour la transcription (CPU/réseau
        # bound hors de la boucle d'événements) : `build_presentation` part
        # dans un thread à part, la boucle d'événements reste libre pour les
        # autres requêtes pendant l'attente réseau.
        prs = await asyncio.to_thread(
            build_presentation,
            mission, template_path=template_path,
            axes_etude=axes_of(db, mission), plan=_plan_exemple(mission),
            **include_kwargs
        )
        buf = io.BytesIO()
        prs.save(buf)
    except (RuntimeError, ValueError):
        # garde-fou : jamais de 500 brut sur l'export PPT, le livrable principal.
        # `build_presentation` lève RuntimeError PAR CONCEPTION sur débordement
        # géométrique (`verifier_geometrie`, build.py) et ValueError sur une
        # manipulation de slides du template incompatible (pptx_deck.py) — un
        # template client aux dimensions inattendues déclenche l'un ou l'autre
        # (constat audit-technique robustesse VSCode2, 2026-09-04).
        logger.exception("Échec de génération du PPTX (mission %s)", mission_id)
        return templates.TemplateResponse(
            request,
            "synthese/apercu.html",
            _synthese_context(
                db,
                mission,
                error=(
                    "Échec de la génération du PowerPoint : le template "
                    "sélectionné ne convient pas à ce contenu (dimensions ou "
                    "mise en page incompatibles)."
                ),
            ),
        )
    filename = f"restitution_{slugify(mission.name)}.pptx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# Étape 4 — Uploads .pptx (template client, deck d'exemple) : préambule commun
# --------------------------------------------------------------------------- #
class PptxInvalide(Exception):
    """Fichier .pptx refusé à l'upload. Porte le message destiné à l'écran : la
    RÉPONSE (gabarit, code HTTP) reste le choix de la route, pas du préambule."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


async def _lire_pptx_upload(file: UploadFile, analyser, *, msg_extension: str,
                            msg_invalide: str):
    """Préambule COMMUN aux deux uploads .pptx : extension, bornes de taille et
    de dépaquetage ZIP (partagées avec les autres uploads via `uploads.py`),
    lecture des octets, puis `analyser(flux)` — la validation du CONTENU reste
    propre à chaque route (python-pptx seul / extraction du plan).

    Rend `(octets, résultat de l'analyse)` ou lève `PptxInvalide`. Les bornes
    passent AVANT que python-pptx ne dépaquette (finding audit-technique
    sécurité du 2026-09-04) : le .pptx est une archive ZIP, un fichier
    « client » forgé pouvait faire exploser la RAM au dépaquetage."""
    if not (file.filename or "").lower().endswith(".pptx"):
        raise PptxInvalide(msg_extension)
    try:
        content = await lire_upload_borne(file)
        verifier_zip_borne(content)
        resultat = analyser(io.BytesIO(content))
    except UploadTropVolumineux as exc:
        # Message de la borne elle-même : il nomme le plafond dépassé.
        raise PptxInvalide(str(exc)) from exc
    except Exception as exc:
        raise PptxInvalide(msg_invalide) from exc
    return content, resultat


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
    try:
        from pptx import Presentation

        content, _ = await _lire_pptx_upload(
            file,
            lambda flux: Presentation(flux),  # valide que le fichier est un vrai .pptx
            msg_extension="Un fichier .pptx est attendu.",
            msg_invalide="Fichier .pptx invalide ou corrompu.",
        )
    except PptxInvalide as exc:
        # 400 comme le deck d'exemple, comme interviews_creation/interviews_backup
        # (arbitrage utilisateur 2026-09-26) : un fichier refusé n'est pas un
        # succès. L'écran reste le même — le navigateur affiche le corps d'une
        # 400 —, seul le code dit enfin la vérité aux appelants non humains.
        return templates.TemplateResponse(
            request,
            "synthese/apercu.html",
            _synthese_context(db, mission, error=exc.message),
            status_code=400,
        )

    filename = f"{mission_id}.pptx"
    (PPTX_TEMPLATES_DIR / filename).write_bytes(content)
    mission.pptx_template_path = filename
    db.commit()

    return RedirectResponse(f"/missions/{mission_id}/synthese/apercu", status_code=303)


# --------------------------------------------------------------------------- #
# Étape 4 — Deck d'exemple (US5.2) : son plan réordonne/filtre l'export
# --------------------------------------------------------------------------- #
@router.post("/missions/{mission_id}/pptx-exemple")
async def upload_pptx_exemple(
    mission_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    """Calque de l'upload de template : mêmes bornes ZIP, même validation par
    python-pptx, même 400 sur un fichier refusé (harmonisé le 2026-09-27 ; ce
    calque répondait déjà 400 quand l'upload de template rendait 200)."""
    mission = _get_mission(db, mission_id)

    def _refus(message: str):
        return templates.TemplateResponse(
            request, "synthese/apercu.html",
            _synthese_context(db, mission, error=message), status_code=400,
        )

    try:
        content, plan = await _lire_pptx_upload(
            file,
            extraire_plan,
            msg_extension="Deck d'exemple : un fichier .pptx est attendu.",
            msg_invalide="Deck d'exemple : fichier .pptx invalide ou corrompu.",
        )
    except PptxInvalide as exc:
        return _refus(exc.message)
    if not plan_applicable(plan):
        return _refus("Deck d'exemple : aucune slide de contenu reconnue dans ce deck — "
                      "rien à reprendre comme plan.")

    filename = f"{mission_id}.pptx"
    (PPTX_EXEMPLES_DIR / filename).write_bytes(content)
    mission.pptx_exemple_path = filename
    db.commit()
    return RedirectResponse(f"/missions/{mission_id}/synthese/apercu", status_code=303)


@router.post("/missions/{mission_id}/pptx-exemple/retirer")
def retirer_pptx_exemple(mission_id: int, db: Session = Depends(get_session)):
    """Retire le deck d'exemple : l'export retrouve l'ordre par défaut. Seul le
    lien est défait (comme le template, jamais supprimé) : aucun effacement de
    fichier dans ce routeur, que `test_audio_jamais_supprime` garde. Le fichier
    `{id}.pptx` resté sur disque n'est relu que via `pptx_exemple_path`, et le
    prochain envoi l'écrase."""
    mission = _get_mission(db, mission_id)
    if mission.pptx_exemple_path:
        mission.pptx_exemple_path = None
        db.commit()
    return RedirectResponse(f"/missions/{mission_id}/synthese/apercu", status_code=303)
