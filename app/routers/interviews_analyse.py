"""Écran Analyse d'un entretien libre et sa régénération.

Extrait mécaniquement de `interviews.py` (2026-09-23, constat d'audit
risque technique : 3385 lignes / 56 routes). Code déplacé tel quel ; URL,
ordre d'enregistrement des routes et nom de logger inchangés.
"""
from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import (
    Interview,
)
from ..services.interview_export import (
    group_turns_into_sections,
)
from ..services.interview_libre_extract_ai import (
    InterviewLibreExtractAIError,
    generate_repartition_from_turns,
)
from ..services.mission_axes import axes_of
from ..templating import templates
from .interviews_commun import (
    REPARTITION_KEYS,
    _get_interview,
    _parse_repartition,
)

router = APIRouter(tags=["interviews"])

# --------------------------------------------------------------------------- #
# Écran Analyse + Synthèse (incr.9) — rendu lecture d'un entretien libre,
# façon transcription structurée/éditée : regroupe les tours de parole en
# sections thématiques (section_title porté par le tour qui ouvre le sujet,
# hérité par les suivants) plutôt que de les afficher en formulaire brut
# comme le fait /interviews/{id} (revue/édition). La Synthèse (bouton depuis
# l'écran Analyse) reprend la répartition déjà enregistrée, en lecture.
# --------------------------------------------------------------------------- #
def _get_interview_libre(db: Session, interview_id: int) -> Interview:
    interview = _get_interview(db, interview_id)
    if interview.mode != "libre":
        raise HTTPException(status_code=400, detail="Cet entretien n'est pas en mode libre.")
    return interview


def _libre_analyse_context(interview: Interview) -> dict:
    return {
        "interview": interview,
        "mission": interview.mission,
        "sections": group_turns_into_sections(interview.turns),
        "repartition": interview.repartition or {},
        "repartition_keys": REPARTITION_KEYS,
    }


@router.get("/interviews/{interview_id}/analyse")
def libre_analyse(interview_id: int, request: Request, db: Session = Depends(get_session)):
    """Aperçu lecture-seule d'un entretien libre — tours de parole par
    section puis résumé/répartition, sur un seul écran (fusion 2026-07-17 de
    l'ancien libre_synthese.html, pour converger vers le modèle à 2 écrans
    édition/aperçu déjà utilisé côté entretien sur trame, cf. preview.html)."""
    interview = _get_interview_libre(db, interview_id)
    return templates.TemplateResponse(
        request, "interviews/libre_analyse.html", _libre_analyse_context(interview)
    )


@router.post("/interviews/{interview_id}/analyse/regenerer")
def libre_analyse_regenerer(
    interview_id: int, request: Request, db: Session = Depends(get_session)
):
    """Relance l'IA de répartition/résumé sur les tours de parole enregistrés
    (éventuellement édités depuis l'extraction initiale) — rien n'est écrasé
    ici : le résultat passe par un écran de revue (libre_regen_review.html)
    avant enregistrement, comme à la création (record_libre_synthese)."""
    interview = _get_interview_libre(db, interview_id)
    turns = [
        {
            "interlocuteur": turn.interlocuteur,
            "question": turn.question,
            "remarque": turn.remarque,
            "section_title": turn.section_title,
        }
        for turn in interview.turns
    ]
    try:
        synth = generate_repartition_from_turns(turns, axes_of(db, interview.mission))
    except InterviewLibreExtractAIError as exc:
        context = _libre_analyse_context(interview)
        context["error"] = str(exc)
        return templates.TemplateResponse(
            request, "interviews/libre_analyse.html", context
        )
    return templates.TemplateResponse(
        request,
        "interviews/libre_regen_review.html",
        {
            "interview": interview,
            "mission": interview.mission,
            "resume": synth["resume"],
            "repartition": synth["repartition"],
            "ancien_resume": interview.resume or "",
            "ancienne_repartition": interview.repartition or {},
        },
    )


@router.post("/interviews/{interview_id}/analyse/regenerer/confirm")
def libre_analyse_regenerer_confirm(
    interview_id: int,
    resume: str = Form(""),
    repartition_json: str = Form(""),
    # Les 5 champs nommés d'avant les axes configurables (2026-07-27) : gardés
    # en repli pour qu'un formulaire déjà ouvert dans un onglet du navigateur
    # (ou un test historique) continue de poster une répartition valide.
    repartition_contexte: str = Form(""),
    repartition_culture_adn: str = Form(""),
    repartition_forces_succes: str = Form(""),
    repartition_points_amelioration: str = Form(""),
    repartition_aspirations: str = Form(""),
    db: Session = Depends(get_session),
):
    """N'écrase que le résumé et la répartition — les tours de parole (la
    source de la régénération) et l'identité ne bougent pas."""
    interview = _get_interview_libre(db, interview_id)
    interview.resume = resume.strip() or None
    interview.repartition = _parse_repartition(
        repartition_json,
        (repartition_contexte, repartition_culture_adn, repartition_forces_succes,
         repartition_points_amelioration, repartition_aspirations),
    )
    db.commit()
    return RedirectResponse(f"/interviews/{interview_id}/analyse", status_code=303)
