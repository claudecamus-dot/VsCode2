"""Saisie manuelle des interviews — écran thème par thème (incrément 2).

Principes : autosave par champ (HTMX), navigation libre entre thèmes, suivi
de couverture en direct, statut par question (non posée / à revoir), notes
libres hors-trame, brouillon permanent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from datetime import date, datetime, timezone
from itertools import zip_longest

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import RECORDINGS_DIR, get_session
from ..importers.docx_trame import extract_text_bytes
from ..models import (
    SEGMENT_JOB_KINDS,
    Answer,
    AudioFileJob,
    Interview,
    InterviewSegmentJob,
    InterviewTurn,
    Mission,
    Question,
    Verbatim,
)
from ..services import audio_transcribe, mission_backups
from ..services.audio_file_jobs import (
    is_audio_file_job_stale,
    purge_stale_audio_file_jobs,
    release_audio_file,
    run_audio_file_job,
    tranches_extraction,
)
from ..services.mission_axes import axes_of
from ..services.interview_export import (
    build_interview_markdown,
    group_turns_into_sections,
    slugify,
    transcript_of,
)
from ..services.interview_pdf_export import (
    build_interview_pdf,
    build_synthese_only_pdf,
    build_transcript_only_pdf,
    build_turns_only_pdf,
)
from ..services.interview_extract_ai import (
    InterviewExtractAIError,
    extract_answers_from_text,
)
from ..services.interview_libre_extract_ai import (
    InterviewLibreExtractAIError,
    extract_turns_from_text,
    generate_repartition_from_turns,
)
from ..services.interview_segment_jobs import (
    delete_segment_jobs,
    merge_segment_answers,
    merge_segment_turns,
    purge_stale_segment_jobs,
    recover_stalled_or_failed_jobs,
    run_segment_job,
    segment_jobs_status,
)
from ..templating import templates

def _parse_repartition(repartition_json: str, valeurs_nommees: tuple) -> dict:
    """Répartition postée par le wizard libre.

    Depuis que les axes d'étude sont configurables (2026-07-27), elle voyage
    entre écrans dans UN champ JSON (`repartition_json`) : ses clés ne sont plus
    connues d'avance. Repli sur les 5 champs nommés historiques quand le JSON
    est absent ou illisible — un formulaire déjà ouvert dans un onglet, ou un
    appelant qui poste l'ancien format, ne doit pas perdre sa répartition ni
    faire échouer un enregistrement d'entretien pour ce champ annexe.
    """
    if repartition_json.strip():
        try:
            data = json.loads(repartition_json)
            if isinstance(data, dict):
                return {
                    str(key): (value or "").strip() if isinstance(value, str) else ""
                    for key, value in data.items()
                }
        except (ValueError, TypeError):
            pass
    return {key: value.strip() for key, value in zip(REPARTITION_KEYS, valeurs_nommees)}


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
    trame = interview.mission.trame
    if trame is None:
        return []  # mission sans trame : aucune question, pas une erreur
    return [q for t in trame.themes for q in t.questions]


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
        # L'extraction IA dure des minutes (appels LLM par question) : hors de la
        # boucle d'événements, sinon toute l'app est gelée pendant l'import.
        extracted = await asyncio.to_thread(extract_answers_from_text, questions, text)
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


def _record_error(request, mission, identity, message):
    """Rend l'écran d'enregistrement structuré avec un message d'erreur, en
    conservant le travail déjà saisi (transcription, identité, session de
    jobs) — chemin d'échec d'extraction. Pendant du `_libre_turns_error`."""
    return templates.TemplateResponse(
        request,
        "interviews/record.html",
        {
            "mission": mission,
            "recording_available": audio_transcribe.is_available(),
            "error": message,
            "identity": identity,
        },
    )


def _finalize_record_answers(
    db, request, mission, identity, transcript, session_token, segment_tail
):
    """Produit la répartition question/réponse puis rend l'écran de revue —
    pendant structuré de `_finalize_libre_turns` (mêmes garanties) :

    - aucun job (entretien court, jamais de tick 5 min) : chemin synchrone
      historique inchangé — seul cas où `extract_answers_from_text` voit la
      transcription entière ;
    - sinon : les jobs pas encore `done` sont re-traités INDIVIDUELLEMENT sur
      leur seule tranche (`recover_stalled_or_failed_jobs`), le reliquat
      (`segment_tail`, ≤ 5 min de parole) est traité en synchrone, puis fusion
      « première réponse non vide par question » (`merge_segment_answers`) —
      jamais de retraitement de la transcription complète. Un job qui reste en
      échec APRÈS récupération bloque la finalisation avec son message (revue
      adversariale 2026-07-25 : sinon sa tranche — jusqu'à 5 min de propos —
      disparaissait en silence dès qu'un frère avait produit des réponses) ;
      les jobs ne sont alors PAS supprimés, un nouvel « Envoyer » ne recoûte
      que les tranches encore KO.

    Limite assumée : après une finalisation réussie (jobs consommés), un
    re-POST du même formulaire (bouton Précédent depuis la revue, F5-repost)
    voit `total == 0` et retombe sur le chemin synchrone historique — lent
    sur un long entretien, mais sans perte ni doublon, identique au
    comportement d'avant ce dispositif."""
    # La trame peut avoir disparu PENDANT l'enregistrement (supprimée /
    # réattachée) : sans ce garde, `_mission_questions` (mission.trame.themes)
    # lève AttributeError (500) avant que le message propre des jobs ne
    # s'affiche. Une trame SANS questions (cas normal avant tout remplissage)
    # n'est PAS gardée ici — `extract_answers_from_text` le signale déjà
    # proprement (comportement historique, inchangé).
    if mission.trame is None:
        return _record_error(
            request, mission, identity,
            "La mission n'a plus de trame — impossible de répartir la "
            "transcription.",
        )

    status = segment_jobs_status(db, session_token)

    if status["total"] == 0:
        try:
            extracted = extract_answers_from_text(
                _mission_questions(mission), transcript
            )
        except InterviewExtractAIError as exc:
            return _record_error(request, mission, identity, str(exc))
    else:
        recover_stalled_or_failed_jobs(db, status["jobs"])
        still_ko = [
            j for j in status["jobs"] if j.status != "done" and j.text.strip()
        ]
        if still_ko:
            job_error = next((j.error for j in still_ko if j.error), None)
            return _record_error(
                request, mission, identity,
                (job_error or "Une tranche n'a pas pu être répartie.")
                + " Les tranches déjà réparties sont conservées — réessaie "
                "l'envoi (seules les tranches en échec seront retraitées).",
            )
        try:
            tail_result = None
            if segment_tail.strip():
                tail_result = extract_answers_from_text(
                    _mission_questions(mission), segment_tail
                )
        except InterviewExtractAIError as exc:
            return _record_error(request, mission, identity, str(exc))
        extracted = merge_segment_answers(status["jobs"], tail_result)
        if not extracted:
            # Parité avec le mode libre (B1) : resurfacer le message
            # ACTIONABLE d'un job en échec (levier OLLAMA_TIMEOUT/…), pas le
            # générique trompeur « aucune réponse détectée ».
            job_error = next((j.error for j in status["jobs"] if j.error), None)
            return _record_error(
                request, mission, identity,
                job_error or "Aucune réponse détectée dans la transcription.",
            )

    if not extracted:
        return _record_error(
            request, mission, identity,
            "Aucune réponse détectée dans la transcription.",
        )

    # Jobs consommés (leur seul rôle était d'alimenter cet écran) : on nettoie.
    delete_segment_jobs(db, session_token)

    return templates.TemplateResponse(
        request,
        "interviews/import_review.html",
        _build_review_context(mission, extracted, identity),
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
    session_token: str = Form(""),
    segment_tail: str = Form(""),
    db: Session = Depends(get_session),
):
    # La transcription se fait désormais au fil de l'eau côté client, par
    # segments envoyés à /audio/transcribe-segment pendant l'enregistrement
    # (un entretien peut durer 1h-1h30 : une transcription bloquante unique
    # en fin d'enregistrement n'est pas utilisable). Cette route ne reçoit
    # donc plus que le texte déjà assemblé, plus l'extraction IA des réponses
    # — elle-même faite au fil de l'eau par jobs de 5 min (`kind="answers"`)
    # depuis 2026-07-25 : à l'arrivée ici il ne reste en général que le
    # reliquat (`segment_tail`) à traiter en synchrone.
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
        "session_token": session_token,
        "segment_tail": segment_tail,
    }

    if not transcript.strip():
        return _record_error(request, mission, identity, "Aucun texte transcrit.")

    # Des tranches sont peut-être encore en traitement de fond : écran
    # d'attente (polling) plutôt qu'un retraitement synchrone — même logique
    # que le wizard libre.
    status = segment_jobs_status(db, session_token)
    if status["total"] > 0 and not status["all_done"] and not status["any_failed"]:
        return templates.TemplateResponse(
            request,
            "interviews/record_segment_wait.html",
            {
                "mission": mission,
                "identity": identity,
                "transcript": transcript,
                "session_token": session_token,
                "segment_tail": segment_tail,
                "status": status,
            },
        )

    return _finalize_record_answers(
        db, request, mission, identity, transcript, session_token, segment_tail
    )


@router.post("/missions/{mission_id}/interviews/record/from-jobs")
def record_from_jobs(
    mission_id: int,
    request: Request,
    transcript: str = Form(""),
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    audio_backup_path: str = Form(""),
    session_token: str = Form(""),
    segment_tail: str = Form(""),
    db: Session = Depends(get_session),
):
    """Finalisation après l'écran d'attente du mode structuré : tous les jobs
    sont terminés (ou un a échoué) — fusion/récupération bornée puis écran de
    revue. Même helper que `record_interview` sur le chemin sans attente."""
    mission = _get_mission(db, mission_id)
    identity = {
        "interviewee_name": interviewee_name,
        "interviewee_role": interviewee_role,
        "interviewee_entity": interviewee_entity,
        "interview_date": interview_date,
        "audio_backup_path": audio_backup_path,
        "transcript": transcript,
        "session_token": session_token,
        "segment_tail": segment_tail,
    }
    if not transcript.strip():
        return _record_error(request, mission, identity, "Aucun texte transcrit.")
    return _finalize_record_answers(
        db, request, mission, identity, transcript, session_token, segment_tail
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


def _ecran_attente_tranches(
    request, mission, identity, transcript, session_token, segment_tail,
    status, finalize_action: str, suite_label: str,
):
    """Écran d'attente des tranches encore en traitement de fond. `finalize_action`
    décide de la suite : revue des tours (wizard historique) ou enregistrement
    direct de l'entretien — l'attente elle-même est identique."""
    return templates.TemplateResponse(
        request,
        "interviews/libre_segment_wait.html",
        {
            "mission": mission,
            "identity": identity,
            "transcript": transcript,
            "session_token": session_token,
            "segment_tail": segment_tail,
            "status": status,
            "finalize_action": finalize_action,
            "suite_label": suite_label,
        },
    )


def _libre_turns_error(request, mission, identity, message, tranches_manquantes=0):
    """Rend l'écran d'enregistrement avec un message d'erreur, en conservant le
    travail déjà saisi (transcription, identité) — chemin d'échec d'extraction.

    `tranches_manquantes` > 0 fait apparaître la porte de sortie : « Enregistrer
    quand même ». Sans elle, un service d'IA durablement indisponible rendait
    l'entretien DÉFINITIVEMENT non enregistrable dès qu'une tranche avait abouti
    (revue adversariale 2026-08-31, arbitrage utilisateur du même jour) — le
    blocage protégeait la matière mais coinçait la séance."""
    return templates.TemplateResponse(
        request,
        "interviews/record_libre.html",
        {
            "mission": mission,
            "recording_available": audio_transcribe.is_available(),
            "error": message,
            "identity": identity,
            "tranches_manquantes": tranches_manquantes,
        },
    )


def _extraire_tours_libre(
    db, request, mission, identity, transcript, session_token, segment_tail,
    ignorer_manquantes=False,
):
    """Produit les tours de parole d'un entretien libre.

    Rend `(extracted, None)` en cas de succès, `(None, réponse d'erreur)` sinon
    — les deux consommateurs (revue du wizard `_finalize_libre_turns`,
    enregistrement direct `_enregistrer_libre_direct`) partagent ainsi la même
    logique d'extraction ET le même écran d'erreur, qui conserve la
    transcription.

    Palier 2 (revue du 2026-07-20 : la 1ère version retombait sur
    `extract_turns_from_text(transcript_ENTIER)` dès qu'un job n'était pas
    `done`, réintroduisant le mur synchrone multi-heures que le Palier 2
    devait précisément éviter — corrigé ici). Si aucun job n'existe (entretien
    < 30min), chemin synchrone historique inchangé. Sinon : chaque job `failed`
    ou bloqué (`recover_stalled_or_failed_jobs`) est re-traité INDIVIDUELLEMENT
    sur sa seule tranche (~30min max), jamais sur la transcription complète —
    puis fusion de tous les tours (jobs + reliquat final). Coût borné au nombre
    de tranches à récupérer, pas à la durée totale de l'entretien."""
    status = segment_jobs_status(db, session_token)

    if status["total"] == 0:
        try:
            extracted = extract_turns_from_text(transcript)
        except InterviewLibreExtractAIError as exc:
            return None, _libre_turns_error(request, mission, identity, str(exc))
        if not extracted["turns"]:
            # Revue adversariale 2026-07-29 : l'IA peut répondre sans lever
            # d'exception mais sans détecter aucun tour (silence, transcription
            # trop courte, échec silencieux malgré les relances internes de
            # `extract_turns_from_text`). Sans ce garde-fou, l'enregistrement
            # direct créait un entretien `status="done"` SANS AUCUN CONTENU et
            # sans erreur affichée — l'écran de revue qui filtrait ce cas dans
            # l'ancien wizard n'existe plus sur ce chemin.
            return None, _libre_turns_error(
                request, mission, identity,
                "Aucun tour de parole détecté dans la transcription.",
            )
    else:
        # Récupération PLAFONNÉE et perte partielle SIGNALÉE — les deux garde-fous
        # posés le 2026-07-31 sur `retranscrire_appliquer` (d36aef6) manquaient ici,
        # c'est-à-dire sur le chemin NOMINAL du mode libre (revue du 2026-08-31).
        # Sans plafond, un Ollama saturé sur un entretien de 2 h faisait enchaîner
        # 24 × (timeout + relance) dans un seul POST. Sans détection, les tranches
        # restées en échec disparaissaient du tour de table SANS un mot, et le
        # `delete_segment_jobs` de la fin détruisait le texte qui les portait :
        # l'entretien était créé `status="done"`, amputé, sans trace.
        a_recuperer = [j for j in status["jobs"] if not j.turns_result]
        tentees = a_recuperer[:RECUP_TRANCHES_MAX]
        recover_stalled_or_failed_jobs(db, tentees)
        recuperees = sum(1 for j in tentees if j.turns_result)
        # `not j.turns_result` : exactement ce que `merge_segment_turns` ignorera.
        # `j.text.strip()` : une tranche sans matière n'est pas une perte (parité
        # avec le mode paramétré, plus haut).
        still_ko = [j for j in status["jobs"] if not j.turns_result and j.text.strip()]
        # `ignorer_manquantes` : l'utilisateur a VU le décompte et a explicitement
        # cliqué « Enregistrer quand même ». On passe outre — mais seulement sur
        # ce geste, jamais par défaut : la perte silencieuse est le défaut qu'on
        # corrige, pas le blocage.
        if still_ko and not ignorer_manquantes:
            # Le plafond n'attaque que `RECUP_TRANCHES_MAX` tranches par envoi,
            # mais le blocage regarde TOUTES les tranches : avec N tranches à
            # récupérer il faut donc ⌈N/RECUP_TRANCHES_MAX⌉ envois. Le message
            # doit dire où on en est, sinon l'utilisateur voit une page d'erreur
            # identique à chaque tentative et croit que rien n'avance — alors que
            # le progrès est bien commité d'un envoi à l'autre (revue
            # adversariale 2026-08-31). On distingue donc les deux situations :
            # ça progresse (relancer aboutira), ou ça ne progresse pas du tout.
            if recuperees:
                etat = (f"{recuperees} tranche(s) viennent d'être récupérées, il en "
                        f"reste {len(still_ko)} sur {status['total']}. Relance l'envoi : "
                        f"chaque envoi en reprend jusqu'à {RECUP_TRANCHES_MAX}, et ce qui "
                        "est déjà structuré est conservé.")
            else:
                job_error = next((j.error for j in still_ko if j.error), None)
                etat = ((job_error + " " if job_error else "")
                        + f"Aucune des {len(still_ko)} tranche(s) en échec n'a pu être "
                        f"récupérée sur cet envoi (sur {status['total']} au total). "
                        "Vérifie que le service d'IA répond, puis relance l'envoi.")
            return None, _libre_turns_error(
                request, mission, identity,
                "Leur contenu MANQUERAIT du tour de table, l'entretien n'a donc pas "
                "été enregistré. " + etat,
                tranches_manquantes=len(still_ko),
            )
        try:
            tail_result = None
            if segment_tail.strip():
                tail_result = extract_turns_from_text(segment_tail)
        except InterviewLibreExtractAIError as exc:
            return None, _libre_turns_error(request, mission, identity, str(exc))
        merged = merge_segment_turns(status["jobs"], tail_result)
        if not merged["turns"]:
            # B1 (revue adversariale 2026-07-22) : un job qui échoue (ex. timeout
            # Ollama) avale son exception dans `job.error` — sans ce resurfaçage,
            # l'utilisateur ne verrait que le message générique ci-dessous, trompeur
            # (« aucun tour détecté » alors qu'Ollama a timeouté), et le message
            # actionable (levier OLLAMA_TIMEOUT/OLLAMA_CHUNK_MAX_WORDS/SYNTHESE_MODEL,
            # patiemment construit dans ai_common) serait perdu. Le chemin synchrone
            # (status total==0) le remonte déjà via str(exc) — on tient la parité.
            job_error = next((j.error for j in status["jobs"] if j.error), None)
            return None, _libre_turns_error(
                request, mission, identity,
                job_error or "Aucun tour de parole détecté (tranches et reliquat vides).",
            )
        extracted = merged

    # Jobs consommés (leur seul rôle était d'alimenter l'écran suivant) : on nettoie.
    delete_segment_jobs(db, session_token)
    return extracted, None


def _identite_fusionnee(identity: dict, extracted: dict) -> dict:
    """Identité saisie par l'utilisateur complétée par celle relevée à l'oral
    (la saisie manuelle l'emporte, cf. `_merge_identity`), en reconduisant les
    champs annexes que l'IA ne produit jamais."""
    merged = _merge_identity(identity, extracted["identity"])
    merged["interview_date"] = identity.get("interview_date", "")
    merged["audio_backup_path"] = identity.get("audio_backup_path", "")
    merged["audio_segments"] = identity.get("audio_segments", "[]")
    return merged


def _finalize_libre_turns(
    db, request, mission, identity, transcript, session_token, segment_tail,
    ignorer_manquantes=False,
):
    """Produit les tours de parole puis rend l'écran de revue (étape 2 du
    wizard historique — plus atteignable depuis l'écran d'enregistrement
    depuis le 2026-07-29, cf. `record_libre_enregistrer`, mais conservée)."""
    extracted, erreur = _extraire_tours_libre(
        db, request, mission, identity, transcript, session_token, segment_tail,
        ignorer_manquantes,
    )
    if erreur is not None:
        return erreur

    merged_identity = _identite_fusionnee(identity, extracted)

    return templates.TemplateResponse(
        request,
        "interviews/libre_turns_review.html",
        {
            "mission": mission,
            "turns": extracted["turns"],
            "identity": merged_identity,
            "transcript": transcript,
        },
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
    audio_segments: str = Form("[]"),
    session_token: str = Form(""),
    segment_tail: str = Form(""),
    # Porte de sortie explicite (arbitrage utilisateur 2026-08-31) : posté
    # uniquement par le bouton « Enregistrer quand même » de la page d'erreur.
    ignorer_tranches_manquantes: str = Form(""),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    identity = {
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

    if not transcript.strip():
        return _libre_turns_error(request, mission, identity, "Aucun texte transcrit.")

    # Palier 2 : des tranches de 30min sont peut-être encore en traitement de
    # fond. Si oui, on attend sur un écran de statut (polling) plutôt que de
    # retraiter tout l'entretien en synchrone.
    status = segment_jobs_status(db, session_token)
    if status["total"] > 0 and not status["all_done"] and not status["any_failed"]:
        return _ecran_attente_tranches(
            request, mission, identity, transcript, session_token, segment_tail, status,
            f"/missions/{mission.id}/interviews/record-libre/from-jobs",
            "affichage des tours de parole",
        )

    return _finalize_libre_turns(
        db, request, mission, identity, transcript, session_token, segment_tail,
        bool(ignorer_tranches_manquantes),
    )


@router.post("/interviews/segment-jobs")
def create_segment_job(
    background_tasks: BackgroundTasks,
    session_token: str = Form(...),
    position: int = Form(0),
    text: str = Form(""),
    kind: str = Form("libre_turns"),
    mission_id: int = Form(0),
    db: Session = Depends(get_session),
):
    """Palier 2 : enregistre une tranche de texte et lance son extraction en
    tâche de fond, pendant que l'enregistrement continue. Appelé par la
    rotation JS 5 min de `record_libre.html` (kind="libre_turns", tours de
    parole) et, depuis 2026-07-25, de `record.html` (kind="answers",
    répartition sur la trame de `mission_id`). Fire-and-forget côté client
    (la progression est suivie via `segment_jobs_status_json`). Le texte est
    persisté sur le job (colonne `text`) — pas seulement passé en paramètre de
    la tâche de fond — pour survivre à un redémarrage serveur et permettre une
    récupération ciblée (`recover_stalled_or_failed_jobs`)."""
    if kind not in SEGMENT_JOB_KINDS:
        raise HTTPException(status_code=400, detail="Nature de job inconnue.")
    if kind == "answers":
        # La répartition a besoin de la trame — valider tout de suite plutôt
        # que de laisser chaque job échouer silencieusement en tâche de fond
        # (revue adversariale 2026-07-25 : l'existence de la mission seule ne
        # suffisait pas, une mission sans trame faisait échouer chaque job).
        mission = _get_mission(db, mission_id)
        if mission.trame is None or not _mission_questions(mission):
            raise HTTPException(
                status_code=400,
                detail="La mission n'a pas de trame avec des questions.",
            )
    # Auto-entretien : les jobs d'une session jamais finalisée (Recommencer,
    # wizard abandonné) portent du contenu d'entretien — balayés passé 7 jours.
    purge_stale_segment_jobs(db)
    job = InterviewSegmentJob(
        session_token=session_token[:64], position=position, status="pending",
        text=text, kind=kind, mission_id=mission_id or None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    background_tasks.add_task(run_segment_job, job.id)
    return JSONResponse(
        {"job_id": job.id, "position": position, "status": "pending"}
    )


@router.get("/interviews/segment-jobs/status")
def segment_jobs_status_json(
    session_token: str, db: Session = Depends(get_session)
):
    """État agrégé des jobs d'une session, interrogé en boucle par l'écran
    d'attente (`libre_segment_wait.html`)."""
    status = segment_jobs_status(db, session_token)
    return JSONResponse(
        {
            "total": status["total"],
            "done": status["done"],
            "failed": status["failed"],
            "all_done": status["all_done"],
            "any_failed": status["any_failed"],
        }
    )


@router.get("/interviews/segment-jobs/turns")
def segment_jobs_turns_json(
    session_token: str, db: Session = Depends(get_session)
):
    """Tours de parole DÉJÀ extraits (jobs terminés) d'une session — alimente
    l'aperçu live en lecture seule de l'onglet « Répartition » de
    `record_libre.html` (Palier A). Lecture seule stricte, AUCUN appel IA : ne
    fait que fusionner (par `position`) les `turns_result` déjà calculés en
    tâche de fond. Les tours du reliquat final (< 5 min) et de la synthèse
    n'apparaissent qu'à l'enregistrement, par le flux existant."""
    status = segment_jobs_status(db, session_token)
    merged = merge_segment_turns(status["jobs"], None)
    return JSONResponse(
        {
            "turns": merged["turns"],
            "done": status["done"],
            "total": status["total"],
        }
    )


@router.get("/interviews/segment-jobs/answers")
def segment_jobs_answers_json(
    session_token: str, db: Session = Depends(get_session)
):
    """Répartition Q/R DÉJÀ extraite (jobs `kind="answers"` terminés) d'une
    session — alimente l'aperçu live en lecture seule de l'onglet
    « Répartition (Q/R) » de `record.html`. Lecture seule stricte, AUCUN appel
    IA : ne fait que fusionner (première réponse non vide par question, ordre
    des tranches) les résultats déjà calculés en tâche de fond. Le reliquat
    final (< 5 min) n'apparaît qu'à la soumission, par le flux existant."""
    status = segment_jobs_status(db, session_token)
    merged = merge_segment_answers(status["jobs"], None)
    return JSONResponse(
        {
            # Clés str (JSON) — le JS les consomme telles quelles.
            "answers": {str(qid): ans for qid, ans in merged.items()},
            "done": status["done"],
            "total": status["total"],
        }
    )


@router.post("/missions/{mission_id}/interviews/record-libre/from-jobs")
def record_libre_from_jobs(
    mission_id: int,
    request: Request,
    transcript: str = Form(""),
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    audio_backup_path: str = Form(""),
    audio_segments: str = Form("[]"),
    session_token: str = Form(""),
    segment_tail: str = Form(""),
    # Porte de sortie explicite (arbitrage utilisateur 2026-08-31) : posté
    # uniquement par le bouton « Enregistrer quand même » de la page d'erreur.
    ignorer_tranches_manquantes: str = Form(""),
    db: Session = Depends(get_session),
):
    """Finalisation après l'écran d'attente : tous les jobs sont terminés (ou un
    a échoué), on fusionne/retombe sur le synchrone et on affiche la revue des
    tours. Même helper que `record_libre` sur le chemin sans attente."""
    mission = _get_mission(db, mission_id)
    identity = {
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
    return _finalize_libre_turns(
        db, request, mission, identity, transcript, session_token, segment_tail,
        bool(ignorer_tranches_manquantes),
    )


def _enregistrer_libre_direct(
    db, request, mission, identity, transcript, session_token, segment_tail,
    ignorer_manquantes=False,
):
    """Extrait les tours puis enregistre DÉFINITIVEMENT l'entretien, sans passer
    par les écrans de revue des tours ni de synthèse (désactivés de l'UI le
    2026-07-29 : la revue des tours doublonnait l'onglet « Répartition (Q/R) »
    de l'écran d'enregistrement, et la synthèse est une génération IA longue
    qui retenait l'entretien en otage). Résumé et répartition restent vides —
    ils se génèrent plus tard depuis l'aperçu (« Régénérer l'analyse »)."""
    extracted, erreur = _extraire_tours_libre(
        db, request, mission, identity, transcript, session_token, segment_tail,
        ignorer_manquantes,
    )
    if erreur is not None:
        return erreur

    interview = _creer_interview_libre(
        db,
        mission.id,
        _identite_fusionnee(identity, extracted),
        extracted["turns"],
        transcript,
        resume="",
        repartition=None,
    )
    return _redirection_apres_enregistrement(mission, interview)


@router.post("/missions/{mission_id}/interviews/record-libre/enregistrer")
def record_libre_enregistrer(
    mission_id: int,
    request: Request,
    transcript: str = Form(""),
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    audio_backup_path: str = Form(""),
    audio_segments: str = Form("[]"),
    session_token: str = Form(""),
    segment_tail: str = Form(""),
    # Porte de sortie explicite (arbitrage utilisateur 2026-08-31) : posté
    # uniquement par le bouton « Enregistrer quand même » de la page d'erreur.
    ignorer_tranches_manquantes: str = Form(""),
    db: Session = Depends(get_session),
):
    """Enregistrement direct depuis l'écran de transcription (demande utilisateur
    2026-07-29). Même préambule que `record_libre` — texte obligatoire, attente
    des tranches encore en traitement — mais la suite enregistre l'entretien au
    lieu d'ouvrir la revue des tours."""
    mission = _get_mission(db, mission_id)
    identity = {
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

    if not transcript.strip():
        return _libre_turns_error(request, mission, identity, "Aucun texte transcrit.")

    status = segment_jobs_status(db, session_token)
    if status["total"] > 0 and not status["all_done"] and not status["any_failed"]:
        return _ecran_attente_tranches(
            request, mission, identity, transcript, session_token, segment_tail, status,
            f"/missions/{mission.id}/interviews/record-libre/enregistrer/from-jobs",
            "enregistrement de l'entretien",
        )

    return _enregistrer_libre_direct(
        db, request, mission, identity, transcript, session_token, segment_tail,
        bool(ignorer_tranches_manquantes),
    )


@router.post("/missions/{mission_id}/interviews/record-libre/enregistrer/from-jobs")
def record_libre_enregistrer_from_jobs(
    mission_id: int,
    request: Request,
    transcript: str = Form(""),
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    audio_backup_path: str = Form(""),
    audio_segments: str = Form("[]"),
    session_token: str = Form(""),
    segment_tail: str = Form(""),
    # Porte de sortie explicite (arbitrage utilisateur 2026-08-31) : posté
    # uniquement par le bouton « Enregistrer quand même » de la page d'erreur.
    ignorer_tranches_manquantes: str = Form(""),
    db: Session = Depends(get_session),
):
    """Finalisation de l'enregistrement direct après l'écran d'attente — pendant
    synchrone de `record_libre_from_jobs` pour le chemin sans revue."""
    mission = _get_mission(db, mission_id)
    identity = {
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
    if not transcript.strip():
        return _libre_turns_error(request, mission, identity, "Aucun texte transcrit.")
    return _enregistrer_libre_direct(
        db, request, mission, identity, transcript, session_token, segment_tail,
        bool(ignorer_tranches_manquantes),
    )


@router.post("/missions/{mission_id}/interviews/record-libre/retour")
def record_libre_retour(
    mission_id: int,
    request: Request,
    transcript: str = Form(""),
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    audio_backup_path: str = Form(""),
    audio_segments: str = Form("[]"),
    db: Session = Depends(get_session),
):
    """Retour de l'étape 2 vers l'étape 1 SANS perdre le travail : la
    transcription (portée en champ caché depuis l'extraction) et l'identité
    re-préremplissent l'écran de transcription — avant ce bouton, « Annuler »
    renvoyait sur un formulaire vierge et détruisait tout (constat US9.12,
    TODO wiki). Aucun appel IA."""
    mission = _get_mission(db, mission_id)
    return templates.TemplateResponse(
        request,
        "interviews/record_libre.html",
        {
            "mission": mission,
            "recording_available": audio_transcribe.is_available(),
            "identity": {
                "interviewee_name": interviewee_name,
                "interviewee_role": interviewee_role,
                "interviewee_entity": interviewee_entity,
                "interview_date": interview_date,
                "audio_backup_path": audio_backup_path,
                "audio_segments": audio_segments,
                "transcript": transcript,
            },
        },
    )


@router.post("/missions/{mission_id}/interviews/record-libre/retour-tours")
def record_libre_retour_tours(
    mission_id: int,
    request: Request,
    transcript: str = Form(""),
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    audio_backup_path: str = Form(""),
    audio_segments: str = Form("[]"),
    turn_interlocuteur: list[str] = Form([]),
    turn_question: list[str] = Form([]),
    turn_remarque: list[str] = Form([]),
    turn_section_title: list[str] = Form([]),
    db: Session = Depends(get_session),
):
    """Retour de l'étape 3 vers l'étape 2 SANS perdre les tours de parole
    (portés en champs cachés par l'écran de synthèse) — même logique que
    `record_libre_retour`. Aucun appel IA."""
    mission = _get_mission(db, mission_id)
    return templates.TemplateResponse(
        request,
        "interviews/libre_turns_review.html",
        {
            "mission": mission,
            "turns": _parse_turns_from_form(
                turn_interlocuteur, turn_question, turn_remarque, turn_section_title
            ),
            "identity": {
                "interviewee_name": interviewee_name,
                "interviewee_role": interviewee_role,
                "interviewee_entity": interviewee_entity,
                "interview_date": interview_date,
                "audio_backup_path": audio_backup_path,
                "audio_segments": audio_segments,
            },
            "transcript": transcript,
        },
    )


def _parse_audio_segments(raw: str) -> list[dict]:
    """Décode la liste de tranches audio (champ caché JSON alimenté par la
    rotation JS de `backupRecorder`, cf. `record_libre.html`) — un JSON
    invalide ou absent (entretiens courts, anciens formulaires) redonne
    silencieusement une liste vide plutôt que de faire échouer l'enregistrement
    pour un champ annexe."""
    try:
        parsed = json.loads(raw) if raw else []
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _parse_turns_from_form(
    turn_interlocuteur: list[str],
    turn_question: list[str],
    turn_remarque: list[str],
    turn_section_title: list[str],
) -> list[dict]:
    """Reconstruit la liste de tours de parole depuis les champs de
    formulaire répétés (même filtrage que `extract_turns_from_text` : un tour
    sans AUCUN contenu n'est pas gardé ; un tour qui porte du contenu mais
    dont l'interlocuteur a été vidé est conservé sous « Intervenant » plutôt
    que jeté — filtrage relâché du 2026-07-22, étendu ici au formulaire après
    la revue adversariale 2026-07-27 : depuis que les tours consécutifs d'un
    même interlocuteur partagent un seul champ visible, vider ce champ vidait
    silencieusement TOUT le groupe)."""
    turns = []
    for interlocuteur, question, remarque, section_title in zip_longest(
        turn_interlocuteur, turn_question, turn_remarque, turn_section_title,
        fillvalue="",
    ):
        interlocuteur = interlocuteur.strip()
        question = question.strip() or None
        remarque = remarque.strip() or None
        section_title = section_title.strip() or None
        if question is None and remarque is None:
            continue
        interlocuteur = interlocuteur or "Intervenant"
        turns.append({
            "interlocuteur": interlocuteur,
            "question": question,
            "remarque": remarque,
            "section_title": section_title,
        })
    return turns


def _creer_interview_libre(
    db, mission_id: int, identity: dict, turns: list[dict], transcript: str,
    resume: str, repartition: dict | None,
) -> Interview:
    """Crée l'entretien libre et ses tours de parole, puis commit.

    Une seule implémentation pour les deux chemins d'enregistrement définitif :
    la confirmation du wizard (`record_libre_confirm`, avec résumé/répartition)
    et l'enregistrement direct depuis l'écran de transcription
    (`_enregistrer_libre_direct`, sans synthèse). L'appelant garantit
    `turns` non vide — un entretien sans tour serait compté dans la mission et
    injecté dans la synthèse globale sans porter aucun contenu."""
    try:
        parsed_date = (
            date.fromisoformat(identity["interview_date"])
            if identity.get("interview_date") else None
        )
    except ValueError:
        parsed_date = None

    interview = Interview(
        mission_id=mission_id,
        mode="libre",
        status="done",
        interviewee_name=identity.get("interviewee_name", "").strip() or "Sans nom",
        interviewee_role=identity.get("interviewee_role", "").strip() or None,
        interviewee_entity=identity.get("interviewee_entity", "").strip() or None,
        interview_date=parsed_date,
        audio_backup_path=identity.get("audio_backup_path") or None,
        audio_segments=_parse_audio_segments(identity.get("audio_segments", "[]")),
        resume=resume.strip() or None,
        repartition=repartition,
        # La transcription brute était postée par l'écran de revue mais jamais
        # lue ici : elle disparaissait à l'enregistrement. Anodin tant que la
        # synthèse aboutissait, grave depuis « Enregistrer sans la synthèse »,
        # dont le cas d'usage est justement l'échec IA à répétition — le texte
        # est alors l'artefact le plus précieux (revue adversariale 2026-07-27).
        raw_transcript=transcript.strip() or None,
    )
    db.add(interview)
    db.flush()  # attribue interview.id avant de créer les tours liés

    for position, turn in enumerate(turns):
        db.add(
            InterviewTurn(
                interview_id=interview.id,
                position=position,
                interlocuteur=turn["interlocuteur"],
                question=turn["question"],
                remarque=turn["remarque"],
                section_title=turn["section_title"],
            )
        )

    db.commit()
    return interview


def _redirection_apres_enregistrement(mission: Mission, interview: Interview):
    """Une mission brouillon reste à nommer/rattacher ; sinon on ouvre
    l'entretien tout juste enregistré."""
    if mission.is_draft:
        return RedirectResponse(f"/missions/{mission.id}/finaliser", status_code=303)
    return RedirectResponse(f"/interviews/{interview.id}", status_code=303)


@router.post("/missions/{mission_id}/interviews/record-libre/synthese")
def record_libre_synthese(
    mission_id: int,
    request: Request,
    transcript: str = Form(""),
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    audio_backup_path: str = Form(""),
    audio_segments: str = Form("[]"),
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
        "audio_segments": audio_segments,
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
                "transcript": transcript,
                "error": "Aucun tour de parole à synthétiser — corrige au moins un tour.",
            },
        )

    try:
        synth = generate_repartition_from_turns(turns, axes_of(db, mission))
    except InterviewLibreExtractAIError as exc:
        return templates.TemplateResponse(
            request,
            "interviews/libre_turns_review.html",
            {
                "mission": mission,
                "turns": turns,
                "identity": identity,
                "transcript": transcript,
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
            "transcript": transcript,
        },
    )


@router.post("/missions/{mission_id}/interviews/record-libre/confirm")
def record_libre_confirm(
    mission_id: int,
    request: Request,
    transcript: str = Form(""),
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    audio_backup_path: str = Form(""),
    audio_segments: str = Form("[]"),
    resume: str = Form(""),
    turn_interlocuteur: list[str] = Form([]),
    turn_question: list[str] = Form([]),
    turn_remarque: list[str] = Form([]),
    turn_section_title: list[str] = Form([]),
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
    mission = _get_mission(db, mission_id)

    turns_to_save = _parse_turns_from_form(
        turn_interlocuteur, turn_question, turn_remarque, turn_section_title
    )
    if not turns_to_save:
        # Même garde que `record_libre_synthese` : sans elle, « Enregistrer
        # sans la synthèse » sur un écran sans tour créait un entretien vide
        # « Sans nom », compté dans la mission et injecté dans la synthèse
        # globale (revue adversariale 2026-07-27).
        return templates.TemplateResponse(
            request,
            "interviews/libre_turns_review.html",
            {
                "mission": mission,
                "turns": [],
                "identity": {
                    "interviewee_name": interviewee_name,
                    "interviewee_role": interviewee_role,
                    "interviewee_entity": interviewee_entity,
                    "interview_date": interview_date,
                    "audio_backup_path": audio_backup_path,
                    "audio_segments": audio_segments,
                },
                "transcript": transcript,
                "error": "Aucun tour de parole à enregistrer — corrige au moins un tour.",
            },
        )
    # Répartition entièrement vide (enregistrement sans la synthèse) : la
    # laisser à None plutôt qu'un dict de 5 chaînes vides, qui reste `truthy`
    # et ferait injecter une matière sans contenu dans la synthèse globale de
    # mission (`synthese._libre_material`).
    repartition = _parse_repartition(
        repartition_json,
        (repartition_contexte, repartition_culture_adn, repartition_forces_succes,
         repartition_points_amelioration, repartition_aspirations),
    )
    if not any(repartition.values()):
        repartition = None

    # Même filtrage que l'écran de synthèse (`_parse_turns_from_form`) : une
    # seule implémentation, plus deux règles à garder en phase.
    interview = _creer_interview_libre(
        db,
        mission_id,
        {
            "interviewee_name": interviewee_name,
            "interviewee_role": interviewee_role,
            "interviewee_entity": interviewee_entity,
            "interview_date": interview_date,
            "audio_backup_path": audio_backup_path,
            "audio_segments": audio_segments,
        },
        turns_to_save,
        transcript,
        resume,
        repartition,
    )
    return _redirection_apres_enregistrement(mission, interview)


@router.post("/audio/transcribe-segment")
async def transcribe_segment(file: UploadFile = File(...)):
    """Transcrit un segment audio autonome (utilisé par la rotation de
    segments de record.html) — endpoint sans état, indépendant de toute
    mission/entretien. Même contrat d'erreur `{"error": ...}` que
    `transcribe_notes` : jamais de `{"detail": ...}` ni de 500 brute."""
    try:
        # Whisper est CPU-bound : hors de la boucle d'événements (finding perf audit
        # 2026-07-24 — un endpoint async qui transcrit en direct bloquait TOUTES les
        # autres requêtes pendant plusieurs minutes).
        contenu = await file.read()
        text = await asyncio.to_thread(audio_transcribe.transcribe_audio, contenu)
    except audio_transcribe.NoSpeechError as exc:
        # `code` structuré : l'écran d'enregistrement compte les segments
        # consécutifs sans parole pour alerter sur la source audio (entretien
        # à distance dont le micro n'entend pas le casque, mauvais périphérique)
        # — un matching sur le message français serait fragile.
        return JSONResponse({"error": str(exc), "code": "no_speech"}, status_code=422)
    except audio_transcribe.TranscriptionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception as exc:
        logger.exception("Échec inattendu de la transcription d'un segment")
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"text": text})


@router.post("/audio/transcribe-file")
async def transcribe_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session_token: str = Form(""),
    db: Session = Depends(get_session),
):
    """Importe un fichier audio déjà enregistré et lance sa transcription
    BLOC PAR BLOC en tâche de fond (2026-07-27).

    Remplace, pour ce cas, l'appel synchrone unique à `/audio/transcribe-segment`
    (qui ne rendait la main qu'une fois le fichier ENTIER transcrit — rien à
    l'écran pendant des dizaines de minutes, et aucune extraction IA démarrée
    avant la fin). Le client récupère les blocs au fil de l'eau
    (`transcribe_file_status`) et soumet, bloc par bloc, les mêmes jobs
    d'extraction que pendant un enregistrement micro : à partir du texte, un
    fichier importé se comporte exactement comme un direct."""
    try:
        suffix = "".join(c for c in (file.filename or "")[-16:] if c.isalnum() or c == ".")
        suffix = suffix[suffix.rfind("."):] if "." in suffix else ".audio"
        filename = f"import_{int(time.time())}_{uuid.uuid4().hex[:8]}{suffix}"
        # Streaming par blocs vers le disque (même raison que save_record_backup) :
        # un entretien de 1h30-3h ne doit pas passer entièrement en RAM.
        def _ecrire():
            with open(RECORDINGS_DIR / filename, "wb") as out:
                shutil.copyfileobj(file.file, out, length=1024 * 1024)
        await asyncio.to_thread(_ecrire)
    except Exception as exc:
        logger.exception("Échec de l'import du fichier audio à transcrire")
        return JSONResponse({"error": str(exc)}, status_code=500)

    if not session_token.strip():
        # Le jeton scope la lecture du statut (le seul endpoint qui renvoie du
        # contenu d'entretien) : sans lui, n'importe quel `job_id` — ils sont
        # séquentiels — rendrait la transcription d'autrui.
        (RECORDINGS_DIR / filename).unlink(missing_ok=True)
        return JSONResponse(
            {"error": "Session d'enregistrement absente."}, status_code=400
        )
    try:
        purge_stale_audio_file_jobs(db)
        job = AudioFileJob(
            session_token=session_token[:64],
            filename=filename,
            status="pending",
            block_seconds=audio_transcribe.FILE_BLOCK_S,
            blocks=[],
        )
        db.add(job)
        db.commit()
        db.refresh(job)
    except Exception as exc:
        # Fichier déjà écrit mais aucun job pour le référencer : la purge ne
        # le retrouverait jamais (elle part des lignes en base) — on le retire
        # tout de suite (revue adversariale 2026-07-27).
        (RECORDINGS_DIR / filename).unlink(missing_ok=True)
        logger.exception("Création du job de transcription de fichier impossible")
        return JSONResponse({"error": str(exc)}, status_code=500)
    background_tasks.add_task(run_audio_file_job, job.id)
    return JSONResponse(
        {"job_id": job.id, "status": "pending", "block_seconds": job.block_seconds}
    )


@router.post("/audio/transcribe-file/retry")
def transcribe_file_retry(
    background_tasks: BackgroundTasks,
    job_id: int = Form(...),
    session_token: str = Form(""),
    db: Session = Depends(get_session),
):
    """Relance un import échoué AU BLOC qui a échoué (2026-07-29).

    Un bloc peut échouer sans que le fichier soit en cause (worker de
    transcription tué par manque de mémoire, typiquement) : jusqu'ici la seule
    issue était de ré-importer le fichier et de re-transcrire depuis le début,
    en repayant les dizaines de minutes déjà passées. Les blocs déjà obtenus
    sont conservés et `run_audio_file_job` repart de `len(job.blocks)`.

    Même garde que `transcribe_file_status` : le jeton de session doit
    correspondre, sinon un `job_id` (séquentiel) relancerait l'import d'autrui.
    """
    job = db.get(AudioFileJob, job_id)
    if job is None or not session_token.strip() or job.session_token != session_token:
        return JSONResponse({"error": "Import introuvable."}, status_code=404)
    if job.status != "failed" and not is_audio_file_job_stale(job):
        # Rien à relancer : un job en cours finira, un job abouti n'a plus son
        # fichier. Le client ne propose le bouton que sur échec ; cette garde
        # couvre un double-clic ou un onglet resté ouvert.
        #
        # Un job PÉRIMÉ est relançable bien que son statut en base soit resté
        # `pending`/`running` (revue adversariale 2026-07-29) : c'est le cas
        # même du serveur redémarré en cours de transcription — `/status` le
        # rapporte `failed` et propose la reprise, que cette garde refusait
        # ensuite alors que le fichier est là et la reprise légitime.
        return JSONResponse(
            {"error": "Cet import n'est pas en échec.", "status": job.status},
            status_code=409,
        )
    if job.total_blocks and len(job.blocks or []) >= job.total_blocks:
        # Tous les blocs ont déjà été transcrits : l'échec ne vient pas d'un
        # bloc manquant (fichier sans parole, typiquement). Relancer ne
        # rejouerait rien — `iter_transcribe_blocks` n'a plus aucun bloc à
        # produire — et re-échouerait à l'identique, indéfiniment. On le dit et
        # on libère le fichier, que plus rien ne justifie de garder.
        release_audio_file(job)
        job.filename = ""
        db.commit()
        return JSONResponse(
            {
                "error": "Tout le fichier a déjà été transcrit : l'échec ne vient "
                "pas d'un bloc interrompu. Reprends depuis un autre fichier.",
                "status": "failed",
            },
            status_code=409,
        )
    if not (RECORDINGS_DIR / job.filename).is_file():
        return JSONResponse(
            {
                "error": "Le fichier audio importé n'est plus disponible — "
                "ré-importe-le pour reprendre la transcription."
            },
            status_code=410,
        )
    job.status = "pending"
    job.error = None
    # `created_at` sert d'horloge à `is_audio_file_job_stale` (et à la purge
    # des 7 jours) : sans ce réarmement, un job relancé plus de 3 h après
    # l'import initial serait déclaré « ne répond plus » au premier poll,
    # alors que la reprise vient de démarrer.
    job.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    background_tasks.add_task(run_audio_file_job, job.id)
    return JSONResponse(
        {"job_id": job.id, "status": "pending", "reprise_au_bloc": len(job.blocks or [])}
    )


@router.get("/audio/transcribe-file/status")
def transcribe_file_status(
    job_id: int,
    session_token: str = "",
    since: int = 0,
    db: Session = Depends(get_session),
):
    """Blocs transcrits d'un fichier importé depuis le curseur `since` —
    interrogé en boucle par l'écran d'enregistrement.

    `session_token` est EXIGÉ et vérifié : cet endpoint est le seul à renvoyer
    du contenu d'entretien, et les `job_id` sont séquentiels. `since` évite de
    re-transférer toute la transcription à chaque tick (3 s) : sur un entretien
    de 3 h le volume cumulé devenait quadratique. Un job resté bloqué
    (redémarrage serveur) est rapporté `failed` plutôt que de faire poller
    l'écran indéfiniment."""
    job = db.get(AudioFileJob, job_id)
    if job is None or not session_token or job.session_token != session_token:
        raise HTTPException(status_code=404, detail="Import introuvable.")
    blocks = list(job.blocks or [])
    status, error = job.status, job.error or ""
    if is_audio_file_job_stale(job):
        status = "failed"
        error = error or (
            "La transcription de ce fichier ne répond plus (serveur redémarré ?) "
            "— relance l'import."
        )
    payload = {
        "status": status,
        # Curseur : seuls les blocs non encore consommés par le client.
        "blocks": blocks[max(0, since):],
        "done": len(blocks),
        "total": job.total_blocks,
        "error": error,
    }
    if status == "done" and since >= len(blocks):
        # Job consommé : sa colonne `blocks` porte la transcription complète
        # d'un entretien. La purge périodique ne s'exécute qu'au prochain
        # import — sur un poste où l'on importe rarement, le texte serait resté
        # des mois en base (revue adversariale 2026-07-27). On le supprime dès
        # que le client a tout récupéré.
        #
        # Un job ÉCHOUÉ est au contraire CONSERVÉ (revue adversariale
        # 2026-07-29) : c'est lui qui porte les blocs déjà transcrits et le
        # fichier encore sur disque, donc la reprise au bloc échoué. Le
        # supprimer ici tuait la relance dans le cas NOMINAL — l'échec survient
        # en transcrivant le bloc suivant, donc sans nouveau bloc à livrer, donc
        # avec `since == len(blocks)`. `purge_stale_audio_file_jobs` (7 j)
        # reste le filet qui efface blocs ET fichier si la relance n'a pas lieu.
        db.delete(job)
        db.commit()
    return JSONResponse(payload)


@router.post("/missions/{mission_id}/interviews/record/backup")
async def save_record_backup(mission_id: int, file: UploadFile = File(...)):
    """Sauvegarde l'audio brut complet d'un entretien enregistré (filet de
    sécurité, cf. commentaire sur `Interview.audio_backup_path`) — écrit sur
    disque, hors base de données, en tâche de fond côté client."""
    try:
        # Suffixe aléatoire en plus de l'horodatage : deux tranches uploadées
        # dans la MÊME seconde (fin d'enregistrement + rotation, ou deux fetch
        # en vol) auraient sinon le même nom et l'une écraserait l'autre — d'où
        # plusieurs entrées `audio_segments` pointant sur un seul fichier
        # (« une seule tranche »). Le hex ne contient ni « / » ni « .. » : passe
        # le garde-fou de `get_record_backup`.
        filename = f"{mission_id}_{int(time.time())}_{uuid.uuid4().hex[:8]}.webm"
        # Streaming par blocs vers le disque (finding perf audit 2026-07-24) : un
        # enregistrement complet de 1h30-3h passait entièrement en RAM via
        # file.read(). copyfileobj lit/écrit en chunks ; dans un thread pour ne
        # pas bloquer la boucle sur l'I/O disque.
        def _ecrire():
            with open(RECORDINGS_DIR / filename, "wb") as out:
                shutil.copyfileobj(file.file, out, length=1024 * 1024)
        await asyncio.to_thread(_ecrire)
    except Exception as exc:
        logger.exception("Échec de la sauvegarde audio de secours")
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"path": filename})


@router.get("/missions/{mission_id}/interviews/record/backup/{filename}")
def get_record_backup(mission_id: int, filename: str, db: Session = Depends(get_session)):
    """Sert un enregistrement audio sauvegardé (écoute/téléchargement) — le
    fichier était déjà écrit sur disque (`save_record_backup`) mais jamais
    exposé par une route ; il n'y avait donc rien à lier depuis le
    formulaire d'enregistrement. Ajouté suite à un signalement utilisateur
    ("le lien pour réécouter/télécharger a disparu") — l'historique git ne
    montre aucune trace d'un tel lien ayant existé dans ce dépôt.

    Même garde d'appartenance que `delete_record_backup` (revue adversariale
    2026-07-29) : sans elle, l'id de mission de l'URL n'était qu'un décor et
    n'importe quel id servait n'importe quel enregistrement."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    mission = _get_mission(db, mission_id)
    if not mission_backups.appartient_a_mission(filename, mission_id, mission):
        raise HTTPException(
            status_code=404, detail="Enregistrement introuvable pour cette mission."
        )
    path = RECORDINGS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Enregistrement introuvable.")
    return FileResponse(path, media_type="audio/webm", filename=filename)


@router.post("/missions/{mission_id}/interviews/record/backup/{filename}/delete")
def delete_record_backup(
    mission_id: int,
    filename: str,
    db: Session = Depends(get_session),
):
    """Supprime un enregistrement audio de la mission (onglet « Backup ») —
    fichier sur disque ET référence en base, sinon l'écran garderait une ligne
    pointant vers un fichier disparu.

    Deux gardes, la seconde étant la vraie : le nom de fichier ne doit pas
    permettre de sortir de `data/recordings/` (même filtre que
    `get_record_backup`), et le fichier doit appartenir À CETTE mission —
    sans quoi l'id de mission de l'URL ne serait qu'un décor et n'importe
    quelle mission pourrait effacer les enregistrements des autres."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    mission = _get_mission(db, mission_id)
    if not mission_backups.appartient_a_mission(filename, mission_id, mission):
        raise HTTPException(
            status_code=404, detail="Enregistrement introuvable pour cette mission."
        )
    # Un fichier « orphelin » au regard de CETTE mission (préfixe seul) peut
    # être référencé par l'entretien d'une AUTRE mission — entretien réattaché
    # depuis une mission brouillon dont l'id a été réutilisé (revue
    # adversariale 2026-07-29). Le supprimer ici laisserait l'autre mission
    # avec des références pendantes sans aucun moyen de l'avoir empêché.
    ailleurs = [
        itw
        for itw in db.scalars(select(Interview).where(Interview.mission_id != mission_id))
        if itw.audio_backup_path == filename
        or any(seg.get("filename") == filename for seg in (itw.audio_segments or []))
    ]
    if ailleurs:
        raise HTTPException(
            status_code=409,
            detail="Cet enregistrement est référencé par un entretien d'une autre "
            "mission — supprime-le depuis cette mission-là.",
        )

    path = RECORDINGS_DIR / filename
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        # Fichier verrouillé (lecture en cours sous Windows, typiquement) : ne
        # pas retirer la référence, sinon l'octet resterait sur le disque sans
        # plus aucun écran pour le montrer.
        logger.warning("Suppression de %s impossible : %s", filename, exc)
        raise HTTPException(
            status_code=409,
            detail="Fichier momentanément verrouillé — réessaie dans un instant.",
        ) from exc

    for interview in mission.interviews:
        segments = [
            seg for seg in (interview.audio_segments or []) if seg.get("filename") != filename
        ]
        if len(segments) != len(interview.audio_segments or []):
            # Renumérotation : `position` est un RANG affiché (« Tranche 2/3 »
            # sur l'onglet Backup et `libre_detail.html`), pas une identité.
            # Sans elle, supprimer la tranche du milieu laissait les rangs 0 et
            # 2 sur un entretien qui n'a plus que 2 tranches, soit « Tranche
            # 3/2 » à l'écran (revue adversariale 2026-07-29).
            segments = [
                {**seg, "position": rang}
                for rang, seg in enumerate(
                    sorted(segments, key=lambda s: s.get("position") or 0)
                )
            ]
            # Réassignation (pas une mutation en place) : SQLAlchemy ne détecte
            # pas la modification d'une liste JSON modifiée sur place.
            interview.audio_segments = segments
        if interview.audio_backup_path == filename:
            # `audio_backup_path` désigne la DERNIÈRE tranche : on le fait
            # retomber sur celle qui reste, pas sur None si l'entretien a
            # encore de l'audio.
            interview.audio_backup_path = segments[-1]["filename"] if segments else None
    db.commit()
    return RedirectResponse(f"/missions/{mission_id}#backup", status_code=303)


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
        # Présent seulement pour le flux d'enregistrement audio
        # (record_interview()) — l'import .docx ne met jamais "transcript"
        # dans identity, l'utilisateur gardant déjà son fichier source.
        raw_transcript=(identity.get("transcript") or "").strip() or None,
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


# --------------------------------------------------------------------------- #
# Retranscription d'un entretien DÉJÀ enregistré, depuis ses tranches audio
# --------------------------------------------------------------------------- #
# Demande utilisateur du 2026-07-30, exigence (4) : les relances de transcription
# et de répartition Q/R doivent être disponibles EN CONSULTATION, pas seulement
# pendant l'enregistrement. Les blobs de 60 s du direct ne survivent pas à la
# fermeture de l'onglet — seules les tranches persistées (`audio_segments`,
# servies par l'onglet Backup) couvrent ce cas, d'où le choix de la piste (b) du
# TODO comme socle. Rien n'est écrasé sans revue : le résultat passe par un écran
# de confirmation, comme la régénération d'analyse ci-dessus.
# Récupération synchrone d'une tranche d'extraction non aboutie, dans la requête
# « Voir le résultat » : plafonnée, sinon un Ollama indisponible transforme ce
# POST en attente de plusieurs heures (cf. `retranscrire_appliquer`). Ce qui
# reste est signalé à l'écran et rattrapé par une relance.
RECUP_TRANCHES_MAX = 3


def _tranches_audio(interview: Interview) -> tuple[list[str], int]:
    """Fichiers de sauvegarde audio de l'entretien, dans l'ordre chronologique
    et effectivement présents sur le disque, plus le nombre de tranches
    RÉFÉRENCÉES MAIS INTROUVABLES. Reprend aussi les entretiens d'avant la
    segmentation (`audio_backup_path` seul).

    Le compte d'absents n'est pas cosmétique : une tranche supprimée depuis
    l'onglet Backup produit une transcription TROUÉE qui remplacerait ensuite
    une transcription complète, sans que rien ne signale le trou sur l'écran de
    revue (revue adversariale 2026-07-30)."""
    references = _noms_audio(interview)
    presents = [n for n in references if (RECORDINGS_DIR / n).is_file()]
    return presents, len(references) - len(presents)


def _rang_segment(seg: dict) -> int:
    """Clé de tri coercitive d'une tranche : `position` vient d'un champ caché
    client, une valeur non entière ne doit pas faire lever le tri (elle prend
    simplement le rang 0, donc l'ordre d'insertion — `sorted` est stable)."""
    try:
        return int(seg.get("position", 0))
    except (TypeError, ValueError):
        return 0


def _noms_audio(interview: Interview) -> list[str]:
    """Fichiers de sauvegarde RÉFÉRENCÉS par cet entretien, dans l'ordre
    chronologique — sans vérifier leur présence sur le disque (c'est le rôle de
    `_tranches_audio`, qui s'appuie sur cette liste)."""
    segments = interview.audio_segments or []
    # `isinstance` AVANT le tri : `sorted(..., key=lambda s: s.get(...))`
    # s'exécute d'abord et lèverait `AttributeError` (500) sur un
    # `audio_segments` contenant autre chose qu'un dict — ce champ vient d'un
    # champ caché client (`_parse_audio_segments` ne valide que « c'est une
    # liste »).
    # Le CONTENU est validé autant que le conteneur (revue adversariale
    # 2026-07-30) : `filename` non-`str` faisait lever le test de traversée plus
    # bas (`"/" not in 123`), et des `position` de types mixtes faisaient lever
    # le tri — 500 sur les 4 routes de retranscription, sans issue dans l'UI
    # pour l'entretien ainsi enregistré.
    dicts = [
        seg
        for seg in segments
        if isinstance(seg, dict) and isinstance(seg.get("filename"), str) and seg["filename"]
    ]
    noms = [seg["filename"] for seg in sorted(dicts, key=_rang_segment)]
    if not noms and interview.audio_backup_path:
        noms = [interview.audio_backup_path]
    # Déduplication en conservant l'ordre de première apparition : deux entrées
    # `audio_segments` peuvent pointer le MÊME fichier sur les entretiens
    # enregistrés avant le suffixe aléatoire de `save_record_backup` (deux
    # tranches uploadées dans la même seconde s'écrasaient). Sans elle, la même
    # demi-heure d'audio est transcrite deux fois et le tour de table proposé
    # porte un bloc entier en double.
    vus: set[str] = set()
    uniques = [n for n in noms if not (n in vus or vus.add(n))]
    # Même garde de traversée de chemin que `get_record_backup` /
    # `delete_record_backup` sur ce même champ : un nom qui s'en écarte n'est
    # pas un fichier de tranche, on ne le lit pas.
    return [n for n in uniques if "/" not in n and "\\" not in n and ".." not in n]


def _job_retranscription(db: Session, interview_id: int) -> AudioFileJob | None:
    """Job de retranscription courant de cet entretien (le plus récent)."""
    return db.scalars(
        select(AudioFileJob)
        .where(AudioFileJob.interview_id == interview_id)
        .order_by(AudioFileJob.id.desc())
    ).first()


def _job_retranscription_de_cet_entretien(
    db: Session, interview: Interview
) -> AudioFileJob | None:
    """Job de retranscription courant, à condition qu'il porte bien de l'audio
    de CET entretien.

    Sans cette vérification, un job pouvait proposer de remplacer le contenu
    d'un entretien par la transcription d'un AUTRE (constat 2026-07-30, trouvé
    en nettoyant les données de vérification) : la colonne `interview_id` est
    ajoutée par migration additive, donc **sans clause REFERENCES** sur les
    bases existantes (SQLite ne sait pas l'ajouter après coup) — aucun
    `ON DELETE CASCADE` n'emporte le job quand l'entretien est supprimé. Or
    SQLite **recycle** l'identifiant libéré (vérifié : supprimer la dernière
    ligne puis insérer rend le même `rowid`), donc le prochain entretien créé
    héritait du job orphelin comme « job courant » — sa transcription était
    alors proposée à l'application sur un entretien qui n'a jamais produit cet
    audio. `retranscrire_start` comparait déjà ses tranches ; les écrans de
    suivi, de statut et de revue, non."""
    job = _job_retranscription(db, interview.id)
    if job is None or not _job_porte_l_audio(job, interview):
        return None
    return job


def _job_porte_l_audio(job: AudioFileJob, interview: Interview) -> bool:
    """Ce job porte-t-il de l'audio de CET entretien ?

    Critère : une INTERSECTION non vide avec les fichiers RÉFÉRENCÉS par
    l'entretien — pas une inclusion, et pas une égalité.

    L'inclusion (première version de cette garde) était trop stricte : le seul
    chemin de suppression offert à l'utilisateur, l'onglet Backup, retire aussi
    la RÉFÉRENCE (`delete_record_backup` réécrit `audio_segments`). Supprimer
    une tranche pendant une retranscription rendait donc étranger un job
    parfaitement légitime — écran de suivi disparu sans un mot, statut en 404,
    bouton « Abandonner » injoignable, et le clic suivant détruisait des heures
    de calcul abouti (les deux chasseurs de la revue du 2026-07-30 l'ont
    reproduit indépendamment). L'égalité stricte, elle, avait le même défaut sur
    le chemin `retranscrire_start` dès qu'un fichier disparaissait du disque.

    Un job dont les tranches ont rétréci reste manifestement le sien ; seul un
    job DISJOINT est étranger — et c'est exactement ce que produit le recyclage
    d'identifiant décrit ci-dessus, les noms de fichiers étant préfixés par la
    mission et horodatés."""
    fichiers = set(job.filenames or [])
    return bool(fichiers and fichiers & set(_noms_audio(interview)))


def _oublier_job_retranscription(db: Session, job: AudioFileJob) -> None:
    """Retire un job de retranscription et les tranches d'extraction qu'il a
    créées. Le fichier audio, lui, appartient à l'entretien : jamais supprimé
    (cf. la garde de `audio_file_jobs._remove_audio`)."""
    delete_segment_jobs(db, job.session_token)
    db.delete(job)
    db.commit()


@router.post("/interviews/{interview_id}/retranscrire")
def retranscrire_start(
    interview_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Relance la transcription de l'entretien depuis ses tranches audio
    persistées, en tâche de fond (`run_audio_file_job`, bloc par bloc avec
    reprise au bloc échoué), puis l'extraction des tours tranche par tranche."""
    interview = _get_interview_libre(db, interview_id)
    tranches, _absentes = _tranches_audio(interview)
    if not tranches:
        context = _libre_analyse_context(interview)
        context["error"] = (
            "Aucun enregistrement audio disponible pour cet entretien : la "
            "transcription ne peut pas être relancée."
        )
        return templates.TemplateResponse(
            request, "interviews/libre_analyse.html", context
        )

    precedent = _job_retranscription(db, interview_id)
    if precedent is not None:
        perime = is_audio_file_job_stale(precedent)
        # Même critère de propriété que les écrans de consultation
        # (`_job_porte_l_audio`) et NON plus l'égalité stricte à la liste des
        # fichiers PRÉSENTS : une tranche disparue du disque — ou retirée depuis
        # l'onglet Backup — faisait tomber l'égalité, donc détruisait un résultat
        # abouti non revu (des heures de calcul) alors que le `confirm()` du
        # bouton ne parle que du contenu déjà enregistré.
        if precedent.status == "done" and _job_porte_l_audio(precedent, interview):
            # Résultat calculé et JAMAIS revu (l'utilisateur a quitté l'écran de
            # revue, ou re-clique sur le bouton) : le détruire pour repartir de
            # zéro jetterait potentiellement des heures de calcul sans le dire.
            # On le ramène sur son écran de suivi, d'où « Voir le résultat »
            # reste accessible — et « Abandonner ce résultat » permet de repartir
            # de zéro explicitement (revue adversariale 2026-07-30).
            return RedirectResponse(
                f"/interviews/{interview_id}/retranscrire", status_code=303
            )
        if precedent.status in ("pending", "running") and not perime:
            # Déjà en cours (double clic, onglet resté ouvert) : on renvoie sur
            # l'écran de suivi plutôt que de lancer un second passage IA.
            return RedirectResponse(
                f"/interviews/{interview_id}/retranscrire", status_code=303
            )
        if (precedent.status == "failed" or perime) and _job_porte_l_audio(
            precedent, interview
        ):
            # REPRISE au bloc/à la tranche interrompus : `run_audio_file_job`
            # repart de `files_done`/`len(blocks)` et l'extraction saute les
            # tranches déjà structurées. Recréer un job neuf ici jetterait tout
            # ce travail et repaierait les dizaines de minutes déjà passées —
            # exactement ce que la reprise de l'import évite depuis le
            # 2026-07-29. `created_at` est re-daté, sinon la reprise serait
            # déclarée « ne répond plus » dès le premier appel du statut.
            precedent.status = "pending"
            precedent.error = None
            precedent.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
            background_tasks.add_task(run_audio_file_job, precedent.id)
            return RedirectResponse(
                f"/interviews/{interview_id}/retranscrire", status_code=303
            )
        # Job abouti (résultat non appliqué) ou tranches audio différentes :
        # on repart de zéro plutôt que de mélanger deux passages.
        _oublier_job_retranscription(db, precedent)

    purge_stale_audio_file_jobs(db)
    job = AudioFileJob(
        session_token=uuid.uuid4().hex,
        filename="",
        filenames=tranches,
        interview_id=interview_id,
        status="pending",
        block_seconds=audio_transcribe.FILE_BLOCK_S,
        blocks=[],
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    background_tasks.add_task(run_audio_file_job, job.id)
    return RedirectResponse(
        f"/interviews/{interview_id}/retranscrire", status_code=303
    )


@router.get("/interviews/{interview_id}/retranscrire")
def retranscrire_ecran(
    interview_id: int, request: Request, db: Session = Depends(get_session)
):
    """Écran de suivi : la retranscription tourne en tâche de fond, cet écran
    interroge son avancement puis propose la revue du résultat."""
    interview = _get_interview_libre(db, interview_id)
    job = _job_retranscription_de_cet_entretien(db, interview)
    if job is None:
        return RedirectResponse(f"/interviews/{interview_id}", status_code=303)
    _, absentes = _tranches_audio(interview)
    return templates.TemplateResponse(
        request,
        "interviews/libre_retranscription.html",
        {
            "interview": interview,
            "mission": interview.mission,
            "job": job,
            "nb_tranches": len(job.filenames or []),
            "tranches_absentes": absentes,
        },
    )


@router.post("/interviews/{interview_id}/retranscrire/abandonner")
def retranscrire_abandonner(interview_id: int, db: Session = Depends(get_session)):
    """Jette le résultat d'une retranscription sans l'appliquer. Sortie explicite
    du cas « un résultat abouti attend d'être revu » : sans elle, le bouton
    « Retranscrire » renvoyant désormais sur ce résultat plutôt que de l'écraser,
    rien ne permettrait plus de relancer un passage neuf."""
    interview = _get_interview_libre(db, interview_id)
    job = _job_retranscription(db, interview.id)
    if job is not None:
        _oublier_job_retranscription(db, job)
    return RedirectResponse(f"/interviews/{interview_id}", status_code=303)


@router.get("/interviews/{interview_id}/retranscrire/statut")
def retranscrire_statut(interview_id: int, db: Session = Depends(get_session)):
    """Avancement des DEUX phases (transcription audio puis extraction des
    tours), interrogé en boucle par l'écran de suivi. Endpoint dédié plutôt que
    le `/audio/transcribe-file/status` de l'import : celui-ci ne connaît pas la
    phase d'extraction, et son jeton de session n'a pas à circuler dans une page
    de consultation (l'appartenance à l'entretien suffit ici)."""
    interview = _get_interview_libre(db, interview_id)
    job = _job_retranscription_de_cet_entretien(db, interview)
    if job is None:
        return JSONResponse({"error": "Aucune retranscription en cours."}, status_code=404)
    status = job.status
    erreur = job.error or ""
    if is_audio_file_job_stale(job):
        status = "failed"
        erreur = erreur or (
            "La retranscription ne répond plus (serveur redémarré ?) — relance-la."
        )
    ia = segment_jobs_status(db, job.session_token)
    # Total RÉEL des tranches d'extraction, pas le nombre de jobs déjà créés :
    # `_extraire_tours` les crée un par un juste avant de les exécuter, donc
    # `ia["total"]` valait toujours « ce qui est fait + 1 » — la barre affichait
    # « 2/2 » avec 18 tranches restantes (revue adversariale 2026-07-30).
    # …mais tant qu'AUCUNE tranche n'existe, on rend 0 : c'est ce zéro qui dit à
    # l'écran « phase 1 en cours ». Le calculer depuis les blocs déjà transcrits
    # ferait basculer l'affichage en « extraction » dès le premier bloc.
    ia_total = (
        max(len(tranches_extraction(list(job.blocks or []))), ia["total"])
        if ia["total"]
        else 0
    )
    return JSONResponse(
        {
            "status": status,
            "error": erreur,
            "blocs": len(job.blocks or []),
            "blocs_total": job.total_blocks,
            "tranches_faites": job.files_done or 0,
            "tranches_total": len(job.filenames or []),
            "ia_faites": ia["done"],
            "ia_total": ia_total,
        }
    )


@router.post("/interviews/{interview_id}/retranscrire/appliquer")
def retranscrire_appliquer(
    interview_id: int, request: Request, db: Session = Depends(get_session)
):
    """Assemble le résultat (transcription + tours de parole) et l'affiche pour
    revue — AUCUNE écriture ici : l'entretien n'est modifié qu'à la
    confirmation, comme la régénération d'analyse."""
    interview = _get_interview_libre(db, interview_id)
    # Garde de propriété OBLIGATOIRE ici : c'est la seule route qui construit la
    # proposition d'écrasement. La première version de ce correctif l'avait
    # oubliée — les deux chasseurs de la revue du 2026-07-30 ont prouvé qu'un
    # POST direct (ou un onglet de suivi resté ouvert, dont le poll s'arrête sur
    # `done`) suffisait alors à faire remplacer le contenu d'un entretien par
    # celui d'un autre, les deux autres routes ayant beau être gardées.
    job = _job_retranscription_de_cet_entretien(db, interview)
    if job is None:
        return RedirectResponse(f"/interviews/{interview_id}", status_code=303)
    transcript = "\n\n".join(b for b in (job.blocks or []) if (b or "").strip())
    if not transcript.strip():
        context = _libre_analyse_context(interview)
        context["error"] = job.error or (
            "La retranscription n'a produit aucun texte — l'enregistrement ne "
            "porte peut-être aucune parole audible."
        )
        return templates.TemplateResponse(
            request, "interviews/libre_analyse.html", context
        )

    # Tranche par tranche, jamais sur la transcription entière : une tranche
    # échouée ou bloquée est re-traitée seule (~5 min de matière), le coût reste
    # borné au nombre de tranches à récupérer (leçon du Palier 2, 2026-07-20).
    #
    # …mais BORNÉ AUSSI EN NOMBRE (revue adversariale 2026-07-30) : cette
    # récupération est SYNCHRONE, dans la requête HTTP. La borne « nombre de
    # tranches » du Palier 2 supposait des tranches de 30 min ; ici elles font
    # 5 min, donc un entretien d'1 h 40 en compte ~20 — Ollama indisponible, ce
    # sont 20 × (timeout + relance) dans un seul POST, soit des heures de
    # requête bloquée avant une page d'erreur. On en récupère quelques-unes, et
    # l'écran de revue signale explicitement ce qui manque encore.
    status = segment_jobs_status(db, job.session_token)
    a_recuperer = [j for j in status["jobs"] if j.turns_result is None]
    recover_stalled_or_failed_jobs(db, a_recuperer[:RECUP_TRANCHES_MAX])
    merged = merge_segment_turns(status["jobs"], None)

    # Ce qui MANQUE au résultat proposé, avant tout écrasement : une tranche
    # jamais aboutie (Ollama capricieux, serveur arrêté pendant l'extraction)
    # disparaissait EN SILENCE — `merge_segment_turns` ne retient que les jobs
    # porteurs d'un `turns_result` — et la route ne signalait que le cas 100 %
    # vide. L'utilisateur validait alors un tour de table amputé qui remplaçait
    # définitivement un tour de table complet (et corrigé à la main), sans
    # aucune trace. Le seul garde-fou était le compteur « N tours proposés
    # contre M actuellement », à repérer soi-même.
    attendues = len(tranches_extraction(list(job.blocks or [])))
    abouties = sum(1 for j in status["jobs"] if j.turns_result is not None)
    avertissements: list[str] = []
    if attendues > abouties:
        avertissements.append(
            f"{attendues - abouties} tranche(s) sur {attendues} n'ont pas pu être "
            "structurées en tours de parole : leur contenu MANQUE dans la "
            "proposition ci-dessous. Relance la retranscription pour les "
            "rattraper avant de remplacer le tour de table actuel."
        )
    if job.error:
        # Tranches audio ignorées pendant la transcription (fichier illisible,
        # supprimé) — consignées par `run_audio_file_job`.
        avertissements.append(f"Transcription incomplète : {job.error}.")
    _, absentes = _tranches_audio(interview)
    if absentes:
        avertissements.append(
            f"{absentes} tranche(s) audio référencée(s) par cet entretien sont "
            "introuvables sur le disque : la transcription relancée est trouée."
        )
    if not merged["turns"]:
        context = _libre_analyse_context(interview)
        job_error = next((j.error for j in status["jobs"] if j.error), None)
        context["error"] = job_error or (
            "Aucun tour de parole détecté dans la transcription relancée — "
            "la transcription elle-même reste téléchargeable ci-dessous."
        )
        context["retranscription_transcript"] = transcript
        return templates.TemplateResponse(
            request, "interviews/libre_analyse.html", context
        )

    ancien_transcript, ancien_reconstitue = transcript_of(interview)
    return templates.TemplateResponse(
        request,
        "interviews/libre_retranscription_review.html",
        {
            "interview": interview,
            "mission": interview.mission,
            "transcript": transcript,
            "turns": merged["turns"],
            "ancien_transcript": ancien_transcript,
            "ancien_reconstitue": ancien_reconstitue,
            "nb_tours_actuels": len(interview.turns),
            "avertissements": avertissements,
        },
    )


@router.post("/interviews/{interview_id}/retranscrire/confirmer")
def retranscrire_confirmer(
    interview_id: int,
    transcript: str = Form(""),
    turn_interlocuteur: list[str] = Form([]),
    turn_question: list[str] = Form([]),
    turn_remarque: list[str] = Form([]),
    turn_section_title: list[str] = Form([]),
    db: Session = Depends(get_session),
):
    """Écrit le résultat validé : transcription brute + tours de parole
    remplacés. Le résumé et la répartition ne sont PAS touchés (ils se
    régénèrent depuis les tours par « Régénérer l'analyse », qui a son propre
    écran de revue) — écraser en cascade sans revue serait justement le défaut
    que ces écrans évitent."""
    interview = _get_interview_libre(db, interview_id)
    turns = _parse_turns_from_form(
        turn_interlocuteur, turn_question, turn_remarque, turn_section_title
    )
    if not turns:
        # Rien à écrire plutôt qu'un entretien vidé de ses tours : le formulaire
        # de revue est arrivé sans aucune ligne (manipulation, session expirée).
        raise HTTPException(status_code=400, detail="Aucun tour de parole à enregistrer.")
    if not transcript.strip():
        # Même garde, côté transcription : l'écriture était ASYMÉTRIQUE — les
        # tours étaient TOUJOURS remplacés, `raw_transcript` seulement si le
        # champ arrivait rempli. Un champ caché vidé laissait donc un entretien
        # dont la transcription ne correspond plus à son tour de table, sans
        # aucun signal (revue adversariale 2026-07-30).
        raise HTTPException(
            status_code=400, detail="Transcription absente : rien n'a été remplacé."
        )

    for turn in list(interview.turns):
        db.delete(turn)
    db.flush()
    for position, turn in enumerate(turns):
        db.add(
            InterviewTurn(
                interview_id=interview.id,
                position=position,
                interlocuteur=turn["interlocuteur"],
                question=turn["question"],
                remarque=turn["remarque"],
                section_title=turn["section_title"],
            )
        )
    if transcript.strip():
        interview.raw_transcript = transcript.strip()
    db.commit()

    job = _job_retranscription(db, interview_id)
    if job is not None:
        _oublier_job_retranscription(db, job)
    return RedirectResponse(f"/interviews/{interview_id}", status_code=303)


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
        # Onglet Transcription : jamais vide dès que le tour de table est
        # renseigné (demande utilisateur 2026-07-27) — `transcript_of` rend la
        # transcription brute si elle a été conservée, sinon la reconstitue
        # depuis les tours, et dit laquelle des deux pour que l'écran l'annonce.
        transcript, transcript_reconstitue = transcript_of(interview)
        return templates.TemplateResponse(
            request,
            "interviews/libre_detail.html",
            {
                "interview": interview,
                "mission": interview.mission,
                "turns": interview.turns,
                "transcript": transcript,
                "transcript_reconstitue": transcript_reconstitue,
                # Onglet Aperçu (2026-07-27) : même rendu par sections que
                # l'écran /analyse, directement sur la fiche entretien.
                "sections": group_turns_into_sections(interview.turns),
            },
        )
    # Mission sans trame (entretien structuré créé avant la trame, ou trame
    # supprimée depuis) : l'écran a déjà son message « la trame est vide »,
    # mais `mission.trame.themes` levait une AttributeError -> 500 sur une
    # simple consultation (constat sur données réelles, 2026-07-27 — même
    # famille que les gardes « Mission sans trame » des écrans de synthèse).
    themes = interview.mission.trame.themes if interview.mission.trame else []
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
    interviewee_name: str = Form(""),
    interviewee_role: str = Form(""),
    interviewee_entity: str = Form(""),
    interview_date: str = Form(""),
    db: Session = Depends(get_session),
):
    """Édition d'un entretien libre déjà enregistré : identité, tours de parole
    et résumé, révisables après coup (ex. un ajustement suite à relecture).
    Ne touche jamais `mode` — verrou serveur (US9.1). Ne touche PAS non plus
    la `repartition` : c'est une matière de niveau *mission* (elle alimente
    la synthèse globale, cf. `_libre_material()`), plus éditée par entretien
    depuis 2026-07-20 (bloc retiré de la consultation) — elle reste révisable
    via « Régénérer l'analyse » (écran Aperçu) ou la synthèse globale."""
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

    interview.resume = resume.strip() or None

    # Identité : éditable ici depuis le 2026-07-27 (pavé repliable du tour de
    # table). Avant, un entretien enregistré « Sans nom » — identité non relevée
    # à l'oral et enregistrement sans passer par l'écran de synthèse — ne se
    # renommait NULLE PART. Le défaut « Sans nom » est conservé sur un nom vidé,
    # comme à la création : la mission liste ses entretiens par ce nom.
    interview.interviewee_name = interviewee_name.strip() or "Sans nom"
    interview.interviewee_role = interviewee_role.strip()
    interview.interviewee_entity = interviewee_entity.strip()
    try:
        interview.interview_date = date.fromisoformat(interview_date) if interview_date else None
    except ValueError:
        # Même tolérance que les autres routes portant ce champ : une date
        # illisible ne fait pas perdre la saisie des tours qui l'accompagne.
        pass
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
        contenu = await file.read()
        # CPU-bound hors de la boucle d'événements (même finding perf que
        # transcribe_segment) ; l'accès db reste dans le thread de la requête.
        transcript = await asyncio.to_thread(audio_transcribe.transcribe_audio, contenu)
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
    exemple de transcription éditée fourni par l'utilisateur).

    Un échec de mise en page ne doit jamais coûter sa matière au consultant :
    jusqu'au 2026-08-31 un verbatim plus haut qu'une page faisait lever
    reportlab et la route rendait un 500 nu (`text/plain`, corps
    « Internal Server Error »), sans autre issue que de retourner recopier le
    texte. La cause de fond est corrigée dans `interview_pdf_export.py`, mais
    la mise en page reste le maillon fragile : on retombe donc sur l'export de
    secours — même contenu, typographie minimale — plutôt que sur rien."""
    interview = _get_interview(db, interview_id)
    try:
        content = build_interview_pdf(interview)
    except Exception:
        logger.exception(
            "Mise en page PDF impossible pour l'entretien %s — repli sur l'export de secours",
            interview_id,
        )
        try:
            content = build_transcript_only_pdf(
                build_interview_markdown(interview),
                interview.interviewee_name,
                subtitle=(
                    "Export de secours — la mise en page complète a échoué sur cet "
                    "entretien ; son contenu est restitué ici en texte simple."
                ),
            )
        except Exception as exc:
            logger.exception("Export de secours PDF impossible pour l'entretien %s", interview_id)
            raise HTTPException(
                status_code=500,
                detail="Export PDF impossible pour cet entretien.",
            ) from exc
    filename = f"entretien_{slugify(interview.interviewee_name)}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/interviews/{interview_id}/export/transcription/pdf")
def export_interview_transcription_pdf(interview_id: int, db: Session = Depends(get_session)):
    """PDF de la seule TRANSCRIPTION d'un entretien — bouton de l'onglet
    « Transcription » de la consultation.

    Rendait jusqu'ici les tours de parole (`build_turns_only_pdf`), c'est-à-dire
    exactement le même document que le bouton « tour de table » de l'onglet
    voisin : deux boutons distincts, un seul contenu (constat utilisateur
    2026-07-27). Il rend désormais le texte que l'onglet affiche —
    `transcript_of()` tient la règle commune aux deux, transcription brute si
    elle a été conservée, sinon reconstitution depuis le tour de table."""
    interview = _get_interview(db, interview_id)
    transcript, reconstitue = transcript_of(interview)
    if not transcript.strip():
        # Comme les exports POST frères (turns/synthese) : sans transcription
        # brute NI tour de table, il n'y a rien à rendre — 400 plutôt qu'un PDF
        # au titre seul.
        raise HTTPException(
            status_code=400,
            detail="Cet entretien n'a ni transcription ni tour de parole à exporter.",
        )
    # Le sous-titre par défaut de ce builder annonce un export de SECOURS après
    # échec IA — faux ici, où rien n'a échoué : on dit d'où vient le texte.
    content = build_transcript_only_pdf(
        transcript,
        interview.interviewee_name,
        subtitle=(
            "Reconstituée à partir du tour de table — le mot-à-mot d'origine "
            "n'a pas été conservé pour cet entretien."
            if reconstitue else
            "Texte tel que transcrit à l'enregistrement, avant structuration par l'IA."
        ),
    )
    filename = f"transcription_{slugify(interview.interviewee_name)}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/interviews/transcript/export-pdf")
def export_transcript_only_pdf(
    transcript: str = Form(""),
    interviewee_name: str = Form(""),
):
    """Export PDF de secours d'une transcription pas encore enregistrée
    (aucun `Interview` en base) — bouton affiché sur les 3 écrans où
    l'extraction IA en aval peut échouer (`record.html`, `record_libre.html`,
    `libre_turns_review.html`) pour ne pas laisser le texte transcrit
    bloqué dans un formulaire sans autre issue que de le ressaisir."""
    if not transcript.strip():
        raise HTTPException(status_code=400, detail="Transcription vide — rien à exporter.")
    content = build_transcript_only_pdf(transcript, interviewee_name)
    slug = slugify(interviewee_name) if interviewee_name.strip() else "brute"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="transcription_{slug}.pdf"'},
    )


@router.post("/interviews/turns/export-pdf")
def export_turns_only_pdf(
    interviewee_name: str = Form(""),
    turn_interlocuteur: list[str] = Form([]),
    turn_question: list[str] = Form([]),
    turn_remarque: list[str] = Form([]),
    turn_section_title: list[str] = Form([]),
):
    """Export PDF des tours de parole pas encore enregistrés — bouton sur
    l'écran « Revue des questions/réponses » du wizard libre (2026-07-19),
    façon `01_Transcription_editee…docx`."""
    turns = _parse_turns_from_form(
        turn_interlocuteur, turn_question, turn_remarque, turn_section_title
    )
    if not turns:
        raise HTTPException(status_code=400, detail="Aucun tour de parole — rien à exporter.")
    content = build_turns_only_pdf(turns, interviewee_name)
    slug = slugify(interviewee_name) if interviewee_name.strip() else "brute"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="tours_{slug}.pdf"'},
    )


@router.post("/interviews/synthese/export-pdf")
def export_synthese_only_pdf(
    interviewee_name: str = Form(""),
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
):
    """Export PDF du résumé + de la répartition pas encore enregistrés —
    bouton sur l'écran « Synthèse avant enregistrement » du wizard libre
    (2026-07-19), façon `02_Synthese_session_3…docx`."""
    repartition = _parse_repartition(
        repartition_json,
        (repartition_contexte, repartition_culture_adn, repartition_forces_succes,
         repartition_points_amelioration, repartition_aspirations),
    )
    if not resume.strip() and not any(repartition.values()):
        raise HTTPException(status_code=400, detail="Synthèse vide — rien à exporter.")
    content = build_synthese_only_pdf(resume, repartition, interviewee_name)
    slug = slugify(interviewee_name) if interviewee_name.strip() else "brute"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="synthese_{slug}.pdf"'},
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
