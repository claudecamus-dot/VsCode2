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
from itertools import zip_longest

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import RECORDINGS_DIR, get_session
from ..importers.docx_trame import extract_text_bytes
from ..models import Answer, Interview, InterviewTurn, Mission, Question, Verbatim
from ..services import audio_transcribe
from ..services.interview_export import build_interview_markdown, group_turns_into_sections, slugify
from ..services.interview_pdf_export import build_interview_pdf
from ..services.interview_extract_ai import (
    InterviewExtractAIError,
    extract_answers_from_text,
)
from ..services.interview_libre_extract_ai import (
    InterviewLibreExtractAIError,
    extract_turns_from_text,
    generate_repartition_from_turns,
)
from ..templating import templates

REPARTITION_KEYS = (
    "contexte", "culture_adn", "forces_succes", "points_amelioration", "aspirations",
)

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


# --------------------------------------------------------------------------- #
# Entretien libre (incr.9, US9.4/US9.5) — même capture audio que le mode
# paramétré (US3.1/3.2, routes /audio/transcribe-segment et .../record/backup
# réutilisées telles quelles, indépendantes de toute trame), mais extraction
# IA différente : pas de questions à remplir, un seul appel produit à la fois
# les tours de parole et la répartition dans les 5 catégories de synthèse
# globale (voir interview_libre_extract_ai.py). Revue éditable unique avant
# enregistrement, comme pour l'import/enregistrement en mode paramétré.
# --------------------------------------------------------------------------- #
def _merge_identity(manual: dict, detected: dict) -> dict:
    """Une saisie manuelle explicite l'emporte ; sinon on prend ce que l'IA a
    identifié dans la transcription (auto-présentation typiquement) — évite
    de ressaisir à la main une identité déjà dite à l'oral (US9.5)."""
    return {
        key: (manual.get(key) or "").strip() or (detected.get(key) or "").strip()
        for key in ("interviewee_name", "interviewee_role", "interviewee_entity")
    }


@router.get("/missions/{mission_id}/interviews/record-libre")
def record_libre_form(
    mission_id: int, request: Request, db: Session = Depends(get_session)
):
    mission = _get_mission(db, mission_id)
    return templates.TemplateResponse(
        request,
        "interviews/record_libre.html",
        {"mission": mission, "recording_available": audio_transcribe.is_available()},
    )


@router.post("/missions/{mission_id}/interviews/record-libre")
def record_libre(
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
    mission = _get_mission(db, mission_id)
    identity = {
        "interviewee_name": interviewee_name,
        "interviewee_role": interviewee_role,
        "interviewee_entity": interviewee_entity,
        "interview_date": interview_date,
        "audio_backup_path": audio_backup_path,
        "transcript": transcript,
    }

    if not transcript.strip():
        return templates.TemplateResponse(
            request,
            "interviews/record_libre.html",
            {
                "mission": mission,
                "recording_available": audio_transcribe.is_available(),
                "error": "Aucun texte transcrit.",
                "identity": identity,
            },
        )

    try:
        extracted = extract_turns_from_text(transcript)
    except InterviewLibreExtractAIError as exc:
        return templates.TemplateResponse(
            request,
            "interviews/record_libre.html",
            {
                "mission": mission,
                "recording_available": audio_transcribe.is_available(),
                "error": str(exc),
                "identity": identity,
            },
        )

    merged_identity = _merge_identity(identity, extracted["identity"])
    merged_identity["interview_date"] = interview_date
    merged_identity["audio_backup_path"] = audio_backup_path

    return templates.TemplateResponse(
        request,
        "interviews/libre_turns_review.html",
        {
            "mission": mission,
            "turns": extracted["turns"],
            "identity": merged_identity,
        },
    )


def _parse_turns_from_form(
    turn_interlocuteur: list[str],
    turn_question: list[str],
    turn_remarque: list[str],
    turn_section_title: list[str],
) -> list[dict]:
    """Reconstruit la liste de tours de parole depuis les champs de
    formulaire répétés (même filtrage que `extract_turns_from_text` : un
    tour sans interlocuteur, ou ni question ni remarque, n'est pas gardé)."""
    turns = []
    for interlocuteur, question, remarque, section_title in zip_longest(
        turn_interlocuteur, turn_question, turn_remarque, turn_section_title,
        fillvalue="",
    ):
        interlocuteur = interlocuteur.strip()
        question = question.strip() or None
        remarque = remarque.strip() or None
        section_title = section_title.strip() or None
        if not interlocuteur or (question is None and remarque is None):
            continue
        turns.append({
            "interlocuteur": interlocuteur,
            "question": question,
            "remarque": remarque,
            "section_title": section_title,
        })
    return turns


@router.post("/missions/{mission_id}/interviews/record-libre/synthese")
def record_libre_synthese(
    mission_id: int,
    request: Request,
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    audio_backup_path: str = Form(""),
    turn_interlocuteur: list[str] = Form([]),
    turn_question: list[str] = Form([]),
    turn_remarque: list[str] = Form([]),
    turn_section_title: list[str] = Form([]),
    db: Session = Depends(get_session),
):
    """Étape 2 (US9.16) : à partir des tours de parole validés à l'étape
    précédente (pas de la transcription brute), génère la répartition dans
    les 5 catégories de synthèse + le résumé, puis affiche l'écran de revue
    de la synthèse avant enregistrement définitif."""
    mission = _get_mission(db, mission_id)
    identity = {
        "interviewee_name": interviewee_name,
        "interviewee_role": interviewee_role,
        "interviewee_entity": interviewee_entity,
        "interview_date": interview_date,
        "audio_backup_path": audio_backup_path,
    }
    turns = _parse_turns_from_form(
        turn_interlocuteur, turn_question, turn_remarque, turn_section_title
    )

    if not turns:
        return templates.TemplateResponse(
            request,
            "interviews/libre_turns_review.html",
            {
                "mission": mission,
                "turns": [],
                "identity": identity,
                "error": "Aucun tour de parole à synthétiser — corrige au moins un tour.",
            },
        )

    try:
        synth = generate_repartition_from_turns(turns)
    except InterviewLibreExtractAIError as exc:
        return templates.TemplateResponse(
            request,
            "interviews/libre_turns_review.html",
            {
                "mission": mission,
                "turns": turns,
                "identity": identity,
                "error": str(exc),
            },
        )

    return templates.TemplateResponse(
        request,
        "interviews/libre_review.html",
        {
            "mission": mission,
            "turns": turns,
            "repartition": synth["repartition"],
            "repartition_keys": REPARTITION_KEYS,
            "resume": synth["resume"],
            "identity": identity,
        },
    )


@router.post("/missions/{mission_id}/interviews/record-libre/confirm")
def record_libre_confirm(
    mission_id: int,
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    audio_backup_path: str = Form(""),
    resume: str = Form(""),
    turn_interlocuteur: list[str] = Form([]),
    turn_question: list[str] = Form([]),
    turn_remarque: list[str] = Form([]),
    turn_section_title: list[str] = Form([]),
    repartition_contexte: str = Form(""),
    repartition_culture_adn: str = Form(""),
    repartition_forces_succes: str = Form(""),
    repartition_points_amelioration: str = Form(""),
    repartition_aspirations: str = Form(""),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)

    try:
        parsed_date = date.fromisoformat(interview_date) if interview_date else None
    except ValueError:
        parsed_date = None

    repartition_values = (
        repartition_contexte,
        repartition_culture_adn,
        repartition_forces_succes,
        repartition_points_amelioration,
        repartition_aspirations,
    )
    interview = Interview(
        mission_id=mission_id,
        mode="libre",
        status="done",
        interviewee_name=interviewee_name.strip() or "Sans nom",
        interviewee_role=interviewee_role.strip() or None,
        interviewee_entity=interviewee_entity.strip() or None,
        interview_date=parsed_date,
        audio_backup_path=audio_backup_path or None,
        resume=resume.strip() or None,
        repartition={
            key: value.strip()
            for key, value in zip(REPARTITION_KEYS, repartition_values)
        },
    )
    db.add(interview)
    db.flush()  # attribue interview.id avant de créer les tours liés

    for position, (interlocuteur, question, remarque, section_title) in enumerate(
        zip_longest(
            turn_interlocuteur, turn_question, turn_remarque, turn_section_title,
            fillvalue="",
        )
    ):
        interlocuteur = interlocuteur.strip()
        question = question.strip() or None
        remarque = remarque.strip() or None
        section_title = section_title.strip() or None
        if not interlocuteur or (question is None and remarque is None):
            continue
        db.add(
            InterviewTurn(
                interview_id=interview.id,
                position=position,
                interlocuteur=interlocuteur,
                question=question,
                remarque=remarque,
                section_title=section_title,
            )
        )

    db.commit()

    if mission.is_draft:
        return RedirectResponse(f"/missions/{mission.id}/finaliser", status_code=303)
    return RedirectResponse(f"/interviews/{interview.id}", status_code=303)


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


@router.get("/missions/{mission_id}/interviews/record/backup/{filename}")
def get_record_backup(mission_id: int, filename: str):
    """Sert un enregistrement audio sauvegardé (écoute/téléchargement) — le
    fichier était déjà écrit sur disque (`save_record_backup`) mais jamais
    exposé par une route ; il n'y avait donc rien à lier depuis le
    formulaire d'enregistrement. Ajouté suite à un signalement utilisateur
    ("le lien pour réécouter/télécharger a disparu") — l'historique git ne
    montre aucune trace d'un tel lien ayant existé dans ce dépôt."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    path = RECORDINGS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Enregistrement introuvable.")
    return FileResponse(path, media_type="audio/webm", filename=filename)


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
# Écran Analyse + Synthèse (incr.9) — rendu lecture d'un entretien libre,
# façon transcription structurée/éditée : regroupe les tours de parole en
# sections thématiques (section_title porté par le tour qui ouvre le sujet,
# hérité par les suivants) plutôt que de les afficher en formulaire brut
# comme le fait /interviews/{id} (revue/édition). La Synthèse (bouton depuis
# l'écran Analyse) reprend la répartition déjà enregistrée, en lecture.
# --------------------------------------------------------------------------- #
@router.get("/interviews/{interview_id}/analyse")
def libre_analyse(interview_id: int, request: Request, db: Session = Depends(get_session)):
    """Aperçu lecture-seule d'un entretien libre — tours de parole par
    section puis résumé/répartition, sur un seul écran (fusion 2026-07-17 de
    l'ancien libre_synthese.html, pour converger vers le modèle à 2 écrans
    édition/aperçu déjà utilisé côté entretien sur trame, cf. preview.html)."""
    interview = _get_interview(db, interview_id)
    if interview.mode != "libre":
        raise HTTPException(status_code=400, detail="Cet entretien n'est pas en mode libre.")
    return templates.TemplateResponse(
        request,
        "interviews/libre_analyse.html",
        {
            "interview": interview,
            "mission": interview.mission,
            "sections": group_turns_into_sections(interview.turns),
            "repartition": interview.repartition or {},
            "repartition_keys": REPARTITION_KEYS,
        },
    )


@router.get("/interviews/{interview_id}/analyse/synthese")
def libre_synthese(interview_id: int):
    """Ancienne URL (contenu désormais fusionné dans /analyse, cf.
    libre_analyse ci-dessus) — conservée en redirection pour ne pas casser un
    lien existant. Le contrôle du mode (400 si pas 'libre') est fait par la
    cible de la redirection."""
    return RedirectResponse(f"/interviews/{interview_id}/analyse", status_code=308)


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
    if interview.mode == "libre":
        return templates.TemplateResponse(
            request,
            "interviews/libre_detail.html",
            {
                "interview": interview,
                "mission": interview.mission,
                "turns": interview.turns,
                "repartition": interview.repartition or {},
                "repartition_keys": REPARTITION_KEYS,
            },
        )
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


@router.post("/interviews/{interview_id}/libre")
def save_libre_detail(
    interview_id: int,
    turn_id: list[str] = Form([]),
    turn_interlocuteur: list[str] = Form([]),
    turn_question: list[str] = Form([]),
    turn_remarque: list[str] = Form([]),
    turn_section_title: list[str] = Form([]),
    resume: str = Form(""),
    repartition_contexte: str = Form(""),
    repartition_culture_adn: str = Form(""),
    repartition_forces_succes: str = Form(""),
    repartition_points_amelioration: str = Form(""),
    repartition_aspirations: str = Form(""),
    db: Session = Depends(get_session),
):
    """Édition d'un entretien libre déjà enregistré : tours de parole et
    répartition, révisables après coup (ex. un ajustement suite à relecture).
    Ne touche jamais `mode` — verrou serveur (US9.1)."""
    interview = _get_interview(db, interview_id)
    if interview.mode != "libre":
        raise HTTPException(status_code=400, detail="Cet entretien n'est pas en mode libre.")

    existing_turns = {str(t.id): t for t in interview.turns}
    for tid, interlocuteur, question, remarque, section_title in zip_longest(
        turn_id, turn_interlocuteur, turn_question, turn_remarque, turn_section_title,
        fillvalue="",
    ):
        turn = existing_turns.get(tid)
        if turn is None:
            continue
        turn.interlocuteur = interlocuteur.strip()
        turn.question = question.strip() or None
        turn.remarque = remarque.strip() or None
        turn.section_title = section_title.strip() or None

    repartition_values = (
        repartition_contexte,
        repartition_culture_adn,
        repartition_forces_succes,
        repartition_points_amelioration,
        repartition_aspirations,
    )
    interview.repartition = {
        key: value.strip() for key, value in zip(REPARTITION_KEYS, repartition_values)
    }
    interview.resume = resume.strip() or None
    db.commit()
    return RedirectResponse(f"/interviews/{interview.id}", status_code=303)


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
# Export Markdown d'un entretien (incr.9, US9.7) — un seul entretien,
# structuré ou libre, à la différence de l'export mission-wide
# (`export.py::export_interviews`) qui agrège tous les entretiens d'une
# mission pour le circuit export -> analyse externe -> réimport.
# --------------------------------------------------------------------------- #
@router.get("/interviews/{interview_id}/export/markdown")
def export_interview_markdown(interview_id: int, db: Session = Depends(get_session)):
    interview = _get_interview(db, interview_id)
    content = build_interview_markdown(interview)
    filename = f"entretien_{slugify(interview.interviewee_name)}.md"
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/interviews/{interview_id}/export/pdf")
def export_interview_pdf(interview_id: int, db: Session = Depends(get_session)):
    """Même matière que l'export Markdown ci-dessus, mais typeset (US9.20) —
    voir `interview_pdf_export.py` pour la mise en forme (inspirée d'un
    exemple de transcription éditée fourni par l'utilisateur)."""
    interview = _get_interview(db, interview_id)
    content = build_interview_pdf(interview)
    filename = f"entretien_{slugify(interview.interviewee_name)}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
