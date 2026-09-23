"""Création d'un entretien et import depuis un document.

Extrait mécaniquement de `interviews.py` (2026-09-23, constat d'audit
risque technique : 3385 lignes / 56 routes). Code déplacé tel quel ; URL,
ordre d'enregistrement des routes et nom de logger inchangés.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..importers.docx_trame import extract_text_bytes
from ..models import (
    Interview,
    Mission,
    Question,
)
from ..services import audio_transcribe
from ..services.interview_extract_ai import (
    InterviewExtractAIError,
    extract_answers_from_text,
)
from ..templating import templates
from ..uploads import (
    UploadTropVolumineux,
    lire_upload_borne,
    verifier_zip_borne,
)


from .interviews_commun import (
    _get_mission,
)

# Même nom de logger qu'avant le découpage : filtres et tests s'y accrochent.
logger = logging.getLogger("app.routers.interviews")

router = APIRouter(tags=["interviews"])

# --------------------------------------------------------------------------- #
# Création / cycle de vie
# --------------------------------------------------------------------------- #
@router.get("/missions/{mission_id}/interviews/new")
def new_interview(mission_id: int, request: Request, db: Session = Depends(get_session)):
    mission = _get_mission(db, mission_id)
    return templates.TemplateResponse(
        request,
        "interviews/new.html",
        {
            "mission": mission,
            "recording_available": audio_transcribe.is_available(),
            "today": date.today().isoformat(),
        },
    )


@router.post("/missions/{mission_id}/interviews")
def create_interview(
    mission_id: int,
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    reference_text: str = Form(""),
    db: Session = Depends(get_session),
):
    _get_mission(db, mission_id)
    try:
        parsed_date = date.fromisoformat(interview_date) if interview_date else None
    except ValueError:
        parsed_date = None
    interview = Interview(
        mission_id=mission_id,
        interviewee_name=interviewee_name.strip() or "Sans nom",
        interviewee_role=interviewee_role.strip() or None,
        interviewee_entity=interviewee_entity.strip() or None,
        interview_date=parsed_date,
        reference_text=reference_text.strip() or None,
    )
    db.add(interview)
    db.commit()
    return RedirectResponse(f"/interviews/{interview.id}", status_code=303)


# --------------------------------------------------------------------------- #
# Import d'un entretien depuis un document (transcription, notes) — pré-
# remplissage des réponses par extraction IA, à valider avant enregistrement.
# --------------------------------------------------------------------------- #
def _mission_questions(mission: Mission) -> list[Question]:
    return [q for t in mission.trame.themes for q in t.questions]


def _build_identity(
    interviewee_name: str = "",
    interviewee_role: str = "",
    interviewee_entity: str = "",
    interview_date: str = "",
    audio_backup_path: str = "",
    audio_segments: str = "[]",
    transcript: str = "",
    session_token: str = "",
    segment_tail: str = "",
) -> dict:
    """Construit le dict `identity` porté par les écrans de revue/erreur et
    consommé à l'enregistrement (`_creer_interview_libre`, `_identite_fusionnee`).

    Un même bloc à 9 clés était recopié à l'identique sur 12 sites de ce
    fichier (4 occurrences byte-identiques) — chaque route ne fournit que le
    sous-ensemble de champs qu'elle a réellement collecté à son étape du
    parcours (formulaire, étape précédente) ; les autres restent à leur valeur
    neutre par défaut, sans changer la forme lue en aval : toutes les lectures
    de ce dict passent par `.get(cle, repli)` (constat audit-technique
    2026-09-04, `flotte:23-items-cadres`)."""
    return {
        "interviewee_name": interviewee_name,
        "interviewee_role": interviewee_role,
        "interviewee_entity": interviewee_entity,
        "interview_date": interview_date,
        "audio_backup_path": audio_backup_path,
        "audio_segments": audio_segments,
        "transcript": transcript,
        "session_token": session_token,
        "segment_tail": segment_tail,
    }


def _proposed_to_json(identity: dict, extracted: dict[int, dict], tranches_manquantes: int = 0) -> str:
    return json.dumps(
        {
            "identity": identity,
            "answers": [
                {"question_id": qid, "text": v["text"], "verbatims": v["verbatims"]}
                for qid, v in extracted.items()
            ],
            # Traverse « Valider l'import » jusqu'à l'entretien créé (2026-09-04,
            # bmad-code-review finding F2/persistance) : sans lui, le bandeau
            # affiché sur la revue disparaissait à la validation, comme s'il
            # n'avait jamais existé.
            "tranches_manquantes": tranches_manquantes,
        }
    )


def _build_review_context(
    mission: Mission, extracted: dict[int, dict], identity: dict, tranches_manquantes: int = 0,
) -> dict:
    """Contexte de gabarit pour `interviews/import_review.html`, partagé par
    l'import depuis un document et l'enregistrement audio (US3.1-US3.3) :
    seule la source du texte extrait diffère, la revue est identique."""
    by_theme = [
        (theme, [q for q in theme.questions if q.id in extracted])
        for theme in mission.trame.themes
    ]
    by_theme = [(theme, qs) for theme, qs in by_theme if qs]
    return {
        "mission": mission,
        "by_theme": by_theme,
        "extracted": extracted,
        "identity": identity,
        "proposed_json": _proposed_to_json(identity, extracted, tranches_manquantes),
    }


@router.get("/missions/{mission_id}/interviews/import")
def import_interview_form(
    mission_id: int, request: Request, db: Session = Depends(get_session)
):
    mission = _get_mission(db, mission_id)
    return templates.TemplateResponse(
        request, "interviews/import.html", {"mission": mission}
    )


@router.post("/missions/{mission_id}/interviews/import")
async def import_interview(
    mission_id: int,
    request: Request,
    file: UploadFile = File(...),
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Un fichier .docx est attendu.")

    questions = _mission_questions(mission)
    identity = _build_identity(
        interviewee_name=interviewee_name,
        interviewee_role=interviewee_role,
        interviewee_entity=interviewee_entity,
        interview_date=interview_date,
    )

    try:
        # Bornes AVANT que python-docx ne dépaquette (finding audit-technique
        # securite du 2026-09-04) : `await file.read()` nu matérialisait tout
        # le corps en RAM, et l'archive était dépaquetée sans plafond.
        contenu = await lire_upload_borne(file)
        verifier_zip_borne(contenu)
        text = extract_text_bytes(contenu)
        # L'extraction IA dure des minutes (appels LLM par question) : hors de la
        # boucle d'événements, sinon toute l'app est gelée pendant l'import.
        extracted = await asyncio.to_thread(extract_answers_from_text, questions, text)
    except InterviewExtractAIError as exc:
        return templates.TemplateResponse(
            request,
            "interviews/import.html",
            {"mission": mission, "error": str(exc), "identity": identity},
        )
    except UploadTropVolumineux as exc:
        return templates.TemplateResponse(
            request,
            "interviews/import.html",
            {"mission": mission, "error": str(exc), "identity": identity},
        )
    except Exception:
        # garde-fou : un .docx corrompu (extension correcte, contenu invalide)
        # fait lever une erreur AVANT extract_answers_from_text — hors du seul
        # type capturé ci-dessus (constat audit-technique robustesse VSCode2,
        # 2026-09-04 : `extract_text_bytes` rouvre le fichier via python-docx
        # `Document()`, dont l'échec sur un contenu corrompu n'est pas une
        # sous-classe de `InterviewExtractAIError`).
        logger.exception("Échec de lecture du .docx importé (mission %s)", mission_id)
        return templates.TemplateResponse(
            request,
            "interviews/import.html",
            {
                "mission": mission,
                "error": "Fichier .docx invalide ou corrompu.",
                "identity": identity,
            },
        )

    if not extracted:
        return templates.TemplateResponse(
            request,
            "interviews/import.html",
            {
                "mission": mission,
                "error": "Aucune réponse détectée dans ce document.",
                "identity": identity,
            },
        )

    return templates.TemplateResponse(
        request,
        "interviews/import_review.html",
        _build_review_context(mission, extracted, identity),
    )
