"""Confirmation d'import et suppression d'un entretien.

Extrait mécaniquement de `interviews.py` (2026-09-23, constat d'audit
risque technique : 3385 lignes / 56 routes). Code déplacé tel quel ; URL,
ordre d'enregistrement des routes et nom de logger inchangés.
"""
from __future__ import annotations

from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    Form,
)
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import (
    Answer,
    Interview,
    Verbatim,
)


from .interviews_commun import (
    _get_mission,
)
from .interviews_libre import (
    _entier_positif,
    _lire_proposition,
)

router = APIRouter(tags=["interviews"])

@router.post("/missions/{mission_id}/interviews/import/confirm")
def import_interview_confirm(
    mission_id: int,
    proposed: str = Form(...),
    keep: list[str] = Form([]),
    db: Session = Depends(get_session),
):
    _get_mission(db, mission_id)
    data, keep_ids = _lire_proposition(proposed, keep)
    identity = data.get("identity") or {}

    try:
        parsed_date = (
            date.fromisoformat(identity.get("interview_date"))
            if identity.get("interview_date")
            else None
        )
    except ValueError:
        parsed_date = None

    interview = Interview(
        mission_id=mission_id,
        interviewee_name=(identity.get("interviewee_name") or "").strip() or "Sans nom",
        interviewee_role=(identity.get("interviewee_role") or "").strip() or None,
        interviewee_entity=(identity.get("interviewee_entity") or "").strip() or None,
        interview_date=parsed_date,
        audio_backup_path=identity.get("audio_backup_path") or None,
        # Présent seulement pour le flux d'enregistrement audio
        # (record_interview()) — l'import .docx ne met jamais "transcript"
        # dans identity, l'utilisateur gardant déjà son fichier source.
        raw_transcript=(identity.get("transcript") or "").strip() or None,
        # Traversé depuis `_proposed_to_json` (2026-09-04, bmad-code-review
        # finding F2) : sans lui, le bandeau affiché sur la revue disparaissait
        # à la validation de l'import.
        tranches_manquantes=_entier_positif(data.get("tranches_manquantes")),
    )
    db.add(interview)
    db.flush()  # attribue interview.id avant de créer les réponses liées

    for row in data.get("answers") or []:
        qid = row.get("question_id")
        if qid not in keep_ids:
            continue
        db.add(
            Answer(
                interview_id=interview.id,
                question_id=qid,
                text=row.get("text") or "",
                status="to_review",
            )
        )
        for quote in row.get("verbatims") or []:
            db.add(
                Verbatim(interview_id=interview.id, question_id=qid, quote=quote)
            )

    db.commit()
    return RedirectResponse(f"/interviews/{interview.id}", status_code=303)


@router.post("/interviews/{interview_id}/delete")
def delete_interview(interview_id: int, db: Session = Depends(get_session)):
    interview = db.get(Interview, interview_id)
    mission_id = interview.mission_id if interview else None
    if interview is not None:
        db.delete(interview)
        db.commit()
    target = f"/missions/{mission_id}" if mission_id else "/missions"
    return RedirectResponse(target, status_code=303)
