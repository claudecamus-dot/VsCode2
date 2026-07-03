"""Saisie manuelle des interviews — écran thème par thème (incrément 2).

Principes : autosave par champ (HTMX), navigation libre entre thèmes, suivi
de couverture en direct, statut par question (non posée / à revoir), notes
libres hors-trame, brouillon permanent.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import RECORDINGS_DIR, get_session
from ..importers.docx_trame import extract_text_bytes
from ..models import Answer, Interview, Mission, Question, Verbatim
from ..services import audio_transcribe
from ..services.interview_extract_ai import (
    InterviewExtractAIError,
    extract_answers_from_text,
)
from ..templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["interviews"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _get_mission(db: Session, mission_id: int) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable.")
    return mission


def _get_interview(db: Session, interview_id: int) -> Interview:
    interview = db.get(Interview, interview_id)
    if interview is None:
        raise HTTPException(status_code=404, detail="Entretien introuvable.")
    return interview


def _get_or_create_answer(db: Session, interview: Interview, question_id: int) -> Answer:
    answer = db.scalar(
        select(Answer).where(
            Answer.interview_id == interview.id,
            Answer.question_id == question_id,
        )
    )
    if answer is None:
        answer = Answer(interview_id=interview.id, question_id=question_id)
        db.add(answer)
    return answer


def _all_questions(interview: Interview) -> list[Question]:
    return [q for t in interview.mission.trame.themes for q in t.questions]


def _coverage(interview: Interview) -> tuple[int, int]:
    answers = {a.question_id: a for a in interview.answers}
    questions = _all_questions(interview)
    answered = sum(
        1 for q in questions
        if (a := answers.get(q.id)) is not None and a.status == "answered"
    )
    return answered, len(questions)


def _saved_response(request: Request, interview: Interview, answer: Answer):
    answered, total = _coverage(interview)
    return templates.TemplateResponse(
        request,
        "interviews/_saved.html",
        {"answer": answer, "answered": answered, "total": total},
    )


def _verbatims_for(db: Session, interview_id: int, question_id: int) -> list[Verbatim]:
    return list(
        db.scalars(
            select(Verbatim)
            .where(
                Verbatim.interview_id == interview_id,
                Verbatim.question_id == question_id,
            )
            .order_by(Verbatim.created_at)
        )
    )


def _verbatims_response(request: Request, verbatims: list[Verbatim]):
    return templates.TemplateResponse(
        request, "interviews/_verbatims.html", {"verbatims": verbatims}
    )


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


def _proposed_to_json(identity: dict, extracted: dict[int, dict]) -> str:
    return json.dumps(
        {
            "identity": identity,
            "answers": [
                {"question_id": qid, "text": v["text"], "verbatims": v["verbatims"]}
                for qid, v in extracted.items()
            ],
        }
    )


def _build_review_context(mission: Mission, extracted: dict[int, dict], identity: dict) -> dict:
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
        "proposed_json": _proposed_to_json(identity, extracted),
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
    identity = {
        "interviewee_name": interviewee_name,
        "interviewee_role": interviewee_role,
        "interviewee_entity": interviewee_entity,
        "interview_date": interview_date,
    }

    try:
        text = extract_text_bytes(await file.read())
        extracted = extract_answers_from_text(questions, text)
    except InterviewExtractAIError as exc:
        return templates.TemplateResponse(
            request,
            "interviews/import.html",
            {"mission": mission, "error": str(exc), "identity": identity},
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


# --------------------------------------------------------------------------- #
# Enregistrement d'un entretien depuis le navigateur (US3.1) — transcription
# locale (US3.2) puis même pipeline d'extraction/revue que l'import de
# document (US3.3) : seule la source du texte change.
# --------------------------------------------------------------------------- #
@router.get("/missions/{mission_id}/interviews/record")
def record_interview_form(
    mission_id: int, request: Request, db: Session = Depends(get_session)
):
    mission = _get_mission(db, mission_id)
    return templates.TemplateResponse(
        request,
        "interviews/record.html",
        {"mission": mission, "recording_available": audio_transcribe.is_available()},
    )


@router.post("/missions/{mission_id}/interviews/record")
def record_interview(
    mission_id: int,
    request: Request,
    transcript: str = Form(""),
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    audio_backup_path: str = Form(""),
    db: Session = Depends(get_session),
):
    # La transcription se fait désormais au fil de l'eau côté client, par
    # segments envoyés à /audio/transcribe-segment pendant l'enregistrement
    # (un entretien peut durer 1h-1h30 : une transcription bloquante unique
    # en fin d'enregistrement n'est pas utilisable). Cette route ne reçoit
    # donc plus que le texte déjà assemblé, plus l'extraction IA des réponses.
    mission = _get_mission(db, mission_id)
    identity = {
        "interviewee_name": interviewee_name,
        "interviewee_role": interviewee_role,
        "interviewee_entity": interviewee_entity,
        "interview_date": interview_date,
        "audio_backup_path": audio_backup_path,
        # Préservé en cas de ré-affichage du formulaire (erreur d'extraction) :
        # un transcript peut représenter 1h-1h30 d'entretien, il serait
        # inacceptable de le perdre parce que l'appel IA a échoué.
        "transcript": transcript,
    }

    if not transcript.strip():
        return templates.TemplateResponse(
            request,
            "interviews/record.html",
            {
                "mission": mission,
                "recording_available": audio_transcribe.is_available(),
                "error": "Aucun texte transcrit.",
                "identity": identity,
            },
        )

    try:
        extracted = extract_answers_from_text(_mission_questions(mission), transcript)
    except InterviewExtractAIError as exc:
        return templates.TemplateResponse(
            request,
            "interviews/record.html",
            {
                "mission": mission,
                "recording_available": audio_transcribe.is_available(),
                "error": str(exc),
                "identity": identity,
            },
        )

    if not extracted:
        return templates.TemplateResponse(
            request,
            "interviews/record.html",
            {
                "mission": mission,
                "recording_available": audio_transcribe.is_available(),
                "error": "Aucune réponse détectée dans la transcription.",
                "identity": identity,
            },
        )

    return templates.TemplateResponse(
        request,
        "interviews/import_review.html",
        _build_review_context(mission, extracted, identity),
    )


@router.post("/audio/transcribe-segment")
async def transcribe_segment(file: UploadFile = File(...)):
    """Transcrit un segment audio autonome (utilisé par la rotation de
    segments de record.html) — endpoint sans état, indépendant de toute
    mission/entretien. Même contrat d'erreur `{"error": ...}` que
    `transcribe_notes` : jamais de `{"detail": ...}` ni de 500 brute."""
    try:
        text = audio_transcribe.transcribe_audio(await file.read())
    except audio_transcribe.TranscriptionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception as exc:
        logger.exception("Échec inattendu de la transcription d'un segment")
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"text": text})


@router.post("/missions/{mission_id}/interviews/record/backup")
async def save_record_backup(mission_id: int, file: UploadFile = File(...)):
    """Sauvegarde l'audio brut complet d'un entretien enregistré (filet de
    sécurité, cf. commentaire sur `Interview.audio_backup_path`) — écrit sur
    disque, hors base de données, en tâche de fond côté client."""
    try:
        content = await file.read()
        filename = f"{mission_id}_{int(time.time())}.webm"
        (RECORDINGS_DIR / filename).write_bytes(content)
    except Exception as exc:
        logger.exception("Échec de la sauvegarde audio de secours")
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"path": filename})


@router.post("/missions/{mission_id}/interviews/import/confirm")
def import_interview_confirm(
    mission_id: int,
    proposed: str = Form(...),
    keep: list[str] = Form([]),
    db: Session = Depends(get_session),
):
    _get_mission(db, mission_id)
    data = json.loads(proposed)
    identity = data.get("identity") or {}
    keep_ids = {int(k) for k in keep}

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


# --------------------------------------------------------------------------- #
# Écran de saisie (thème par thème)
# --------------------------------------------------------------------------- #
@router.get("/interviews/{interview_id}")
def capture(
    interview_id: int,
    request: Request,
    theme: str | None = None,
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    themes = interview.mission.trame.themes
    answers = {a.question_id: a for a in interview.answers}
    verbatims_by_q: dict[int, list[Verbatim]] = {}
    for v in interview.verbatims:
        verbatims_by_q.setdefault(v.question_id, []).append(v)

    # Couverture par thème (pour les pastilles de navigation).
    theme_counts = {
        t.id: (
            sum(
                1 for q in t.questions
                if (a := answers.get(q.id)) is not None and a.status == "answered"
            ),
            len(t.questions),
        )
        for t in themes
    }
    answered, total = _coverage(interview)

    notes_view = theme == "notes"
    current = None
    prev_id = next_id = None
    if not notes_view and themes:
        ids = [t.id for t in themes]
        try:
            idx = ids.index(int(theme)) if theme is not None else 0
        except (ValueError, TypeError):
            idx = 0
        current = themes[idx]
        prev_id = ids[idx - 1] if idx > 0 else None
        next_id = ids[idx + 1] if idx < len(ids) - 1 else None

    return templates.TemplateResponse(
        request,
        "interviews/capture.html",
        {
            "interview": interview,
            "themes": themes,
            "current": current,
            "answers": answers,
            "verbatims_by_q": verbatims_by_q,
            "theme_counts": theme_counts,
            "answered": answered,
            "total": total,
            "notes_view": notes_view,
            "prev_id": prev_id,
            "next_id": next_id,
            "recording_available": audio_transcribe.is_available(),
        },
    )


@router.post("/interviews/{interview_id}/answers/{question_id}")
def save_answer(
    interview_id: int,
    question_id: int,
    request: Request,
    text: str | None = Form(None),
    value: str | None = Form(None),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    answer = _get_or_create_answer(db, interview, question_id)
    if text is not None:
        answer.text = text
    if value is not None:
        answer.value = value

    has_content = bool((answer.text or "").strip() or (answer.value or "").strip())
    if has_content:
        answer.status = "answered"
    elif answer.status not in ("skipped", "revisit"):
        answer.status = "pending"

    db.commit()
    return _saved_response(request, interview, answer)


@router.post("/interviews/{interview_id}/answers/{question_id}/status")
def set_status(
    interview_id: int,
    question_id: int,
    request: Request,
    status: str = Form(...),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    answer = _get_or_create_answer(db, interview, question_id)
    if status in ("pending", "answered", "skipped", "revisit"):
        answer.status = status
    db.commit()
    return _saved_response(request, interview, answer)


@router.post("/interviews/{interview_id}/notes")
def save_notes(
    interview_id: int,
    free_notes: str = Form(""),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    interview.free_notes = free_notes
    db.commit()
    return HTMLResponse('<span class="saved">✓ enregistré</span>')


# --------------------------------------------------------------------------- #
# Enregistrement depuis Notes libres : deux actions distinctes.
# 1) Transcription (auto, déclenchée en JS dès l'arrêt de l'enregistrement) —
#    ajoute le texte littéral aux Notes libres, sans analyse IA.
# 2) Répartition (bouton "Répartir", manuel) — analyse le contenu actuel des
#    Notes libres et propose une distribution par question, avec revue
#    obligatoire avant application : une question déjà répondue est toujours
#    proposée, jamais écrasée automatiquement.
# --------------------------------------------------------------------------- #
def _notes_review_context(interview: Interview, transcript: str, extracted: dict[int, dict]) -> dict:
    existing = {a.question_id: a for a in interview.answers}
    by_theme = []
    for theme in interview.mission.trame.themes:
        rows = []
        for q in theme.questions:
            if q.id not in extracted:
                continue
            existing_answer = existing.get(q.id)
            rows.append(
                {
                    "question": q,
                    "proposed": extracted[q.id],
                    "existing": existing_answer,
                    "default_keep": existing_answer is None or existing_answer.status != "answered",
                }
            )
        if rows:
            by_theme.append((theme, rows))

    return {
        "interview": interview,
        "transcript": transcript,
        "by_theme": by_theme,
        "proposed_json": json.dumps(
            {
                "answers": [
                    {"question_id": qid, "text": v["text"], "verbatims": v["verbatims"]}
                    for qid, v in extracted.items()
                ],
            }
        ),
    }


@router.post("/interviews/{interview_id}/notes/transcribe")
async def transcribe_notes(
    interview_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    # Toute erreur ici doit rester exploitable par le JS de capture.html, qui
    # ne lit que `{"error": ...}` — jamais laisser fuiter une HTTPException
    # (shape `{"detail": ...}`) ou une 500 brute, sans quoi l'UI retombe sur
    # un message générique qui masque la vraie cause.
    try:
        interview = _get_interview(db, interview_id)
        transcript = audio_transcribe.transcribe_audio(await file.read())
        interview.free_notes = (
            f"{interview.free_notes.strip()}\n\n{transcript}"
            if (interview.free_notes or "").strip()
            else transcript
        )
        db.commit()
    except audio_transcribe.TranscriptionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception as exc:
        logger.exception("Échec inattendu de la transcription des notes libres")
        return JSONResponse({"error": str(exc)}, status_code=500)

    return JSONResponse({"free_notes": interview.free_notes})


@router.post("/interviews/{interview_id}/notes/dispatch")
def dispatch_notes(
    interview_id: int,
    request: Request,
    free_notes: str = Form(""),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    if free_notes != (interview.free_notes or ""):
        interview.free_notes = free_notes
        db.commit()

    text = free_notes.strip()
    if not text:
        return templates.TemplateResponse(
            request,
            "interviews/notes_review.html",
            {"interview": interview, "error": "Les notes libres sont vides — rien à répartir."},
        )

    try:
        extracted = extract_answers_from_text(_all_questions(interview), text)
    except InterviewExtractAIError as exc:
        return templates.TemplateResponse(
            request,
            "interviews/notes_review.html",
            {"interview": interview, "error": str(exc)},
        )

    return templates.TemplateResponse(
        request,
        "interviews/notes_review.html",
        _notes_review_context(interview, text, extracted),
    )


@router.post("/interviews/{interview_id}/notes/confirm")
def confirm_notes(
    interview_id: int,
    proposed: str = Form(...),
    keep: list[str] = Form([]),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    data = json.loads(proposed)
    keep_ids = {int(k) for k in keep}

    for row in data.get("answers") or []:
        qid = row.get("question_id")
        if qid not in keep_ids:
            continue
        answer = _get_or_create_answer(db, interview, qid)
        answer.text = row.get("text") or ""
        answer.status = "to_review"
        for quote in row.get("verbatims") or []:
            db.add(Verbatim(interview_id=interview.id, question_id=qid, quote=quote))

    db.commit()
    return RedirectResponse(f"/interviews/{interview.id}?theme=notes", status_code=303)


@router.post("/interviews/{interview_id}/identity")
def save_identity(
    interview_id: int,
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    interview.interviewee_name = interviewee_name.strip() or "Sans nom"
    interview.interviewee_role = interviewee_role.strip() or None
    interview.interviewee_entity = interviewee_entity.strip() or None
    db.commit()
    return HTMLResponse('<span class="saved">✓ enregistré</span>')


@router.post("/interviews/{interview_id}/reference")
def save_reference(
    interview_id: int,
    reference_text: str = Form(""),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    interview.reference_text = reference_text.strip() or None
    db.commit()
    return HTMLResponse('<span class="saved">✓ enregistré</span>')


# --------------------------------------------------------------------------- #
# Verbatims (US2.3) : citations mot-pour-mot rattachées à une question
# --------------------------------------------------------------------------- #
@router.post("/interviews/{interview_id}/verbatims/{question_id}")
def add_verbatim(
    interview_id: int,
    question_id: int,
    request: Request,
    quote: str = Form(...),
    db: Session = Depends(get_session),
):
    interview = _get_interview(db, interview_id)
    quote = quote.strip()
    if quote:
        db.add(
            Verbatim(
                interview_id=interview.id,
                question_id=question_id,
                quote=quote,
            )
        )
        db.commit()
    return _verbatims_response(
        request, _verbatims_for(db, interview.id, question_id)
    )


@router.post("/verbatims/{verbatim_id}/delete")
def delete_verbatim(
    verbatim_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    verbatim = db.get(Verbatim, verbatim_id)
    if verbatim is None:
        raise HTTPException(status_code=404, detail="Verbatim introuvable.")
    interview_id, question_id = verbatim.interview_id, verbatim.question_id
    db.delete(verbatim)
    db.commit()
    return _verbatims_response(
        request, _verbatims_for(db, interview_id, question_id)
    )


# --------------------------------------------------------------------------- #
# Aperçu lecture seule : toutes les questions/réponses d'un coup, pour une
# relecture complète rapide (évol) — pas de saisie possible ici, contrairement
# à la capture qui n'affiche qu'un thème à la fois.
# --------------------------------------------------------------------------- #
@router.get("/interviews/{interview_id}/preview")
def preview(interview_id: int, request: Request, db: Session = Depends(get_session)):
    interview = _get_interview(db, interview_id)
    answers = {a.question_id: a for a in interview.answers}
    verbatims_by_q: dict[int, list[Verbatim]] = {}
    for v in interview.verbatims:
        verbatims_by_q.setdefault(v.question_id, []).append(v)
    answered, total = _coverage(interview)

    return templates.TemplateResponse(
        request,
        "interviews/preview.html",
        {
            "interview": interview,
            "themes": interview.mission.trame.themes if interview.mission.trame else [],
            "answers": answers,
            "verbatims_by_q": verbatims_by_q,
            "answered": answered,
            "total": total,
        },
    )


# --------------------------------------------------------------------------- #
# Fin d'entretien : récap de couverture
# --------------------------------------------------------------------------- #
@router.get("/interviews/{interview_id}/finish")
def finish_view(interview_id: int, request: Request, db: Session = Depends(get_session)):
    interview = _get_interview(db, interview_id)
    answers = {a.question_id: a for a in interview.answers}
    missed = []  # questions non répondues (zappées / à poser / à revoir)
    for theme in interview.mission.trame.themes:
        for q in theme.questions:
            a = answers.get(q.id)
            status = a.status if a else "pending"
            if status != "answered":
                missed.append({"theme": theme.title, "label": q.label, "status": status})
    answered, total = _coverage(interview)
    return templates.TemplateResponse(
        request,
        "interviews/finish.html",
        {
            "interview": interview,
            "missed": missed,
            "answered": answered,
            "total": total,
        },
    )


@router.post("/interviews/{interview_id}/finish")
def finish(interview_id: int, db: Session = Depends(get_session)):
    interview = _get_interview(db, interview_id)
    interview.status = "done"
    db.commit()
    return RedirectResponse(f"/missions/{interview.mission_id}", status_code=303)
