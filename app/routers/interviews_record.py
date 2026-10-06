"""Enregistrement direct (Q/R) et libre : formulaires, finalisation, extraction des tours.

Extrait mécaniquement de `interviews.py` (2026-09-23, constat d'audit
risque technique : 3385 lignes / 56 routes). Code déplacé tel quel ; URL,
ordre d'enregistrement des routes et nom de logger inchangés.
"""
from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    Form,
    Request,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import (
    InterviewSegmentJob,
)
from ..services import audio_transcribe
from ..services.interview_extract_ai import (
    InterviewExtractAIError,
    extract_answers_from_text,
)
from ..services.interview_segment_jobs import (
    delete_segment_jobs,
    merge_segment_answers,
    recover_stalled_or_failed_jobs,
    segment_jobs_status,
)
from ..services.structuration_libre import (  # noqa: F401  (ré-export)
    _extraire_tours_libre,
    _tours_vides,
)
from ..templating import templates
from .interviews_commun import (
    _fenetre_recuperation,
    _get_mission,
)
from .interviews_creation import (
    _build_identity,
    _build_review_context,
    _mission_questions,
)

router = APIRouter(tags=["interviews"])

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
    manquantes = 0
    detail = ""

    if status["total"] == 0:
        try:
            extracted = extract_answers_from_text(
                _mission_questions(mission), transcript
            )
        except InterviewExtractAIError as exc:
            extracted = {}
            manquantes = 1
            detail = str(exc)
    else:
        # Récupération PLAFONNÉE (revue R3-M1 du 2026-08-31) : même fenêtre que
        # le mode libre — sans plafond, un Ollama saturé sur un entretien de
        # 2 h enchaînait ~24 × (timeout + relance) synchrones dans ce seul
        # POST.
        tentees = _fenetre_recuperation(status["jobs"], lambda j: j.status == "done")
        recover_stalled_or_failed_jobs(db, tentees)
        still_ko = [
            j for j in status["jobs"] if j.status != "done" and j.text.strip()
        ]
        # Les tranches restées en échec ne retiennent PLUS l'entretien (demande
        # utilisateur 2026-09-04, parité avec le mode libre) : on passe à la
        # revue avec ce qui a été réparti, et on dit ce qui manque. La page de
        # refus renvoyait l'utilisateur relancer l'envoi indéfiniment sur un
        # poste trop lent, sans autre issue que perdre la séance.
        manquantes = len(still_ko)
        # Le levier actionnable (OLLAMA_TIMEOUT/CHUNK/MODEL) rencontré par une
        # tranche RÉELLEMENT retentée à cet envoi (revue N2 du 2026-09-01) :
        # l'erreur d'une tranche non retentée désigne une cause souvent révolue.
        detail = next((j.error for j in tentees if j.error), "") or ""
        try:
            tail_result = None
            if segment_tail.strip():
                tail_result = extract_answers_from_text(
                    _mission_questions(mission), segment_tail
                )
        except InterviewExtractAIError as exc:
            tail_result = None
            manquantes += 1
            detail = detail or str(exc)
        extracted = merge_segment_answers(status["jobs"], tail_result)

    if not extracted and not manquantes:
        # L'IA peut répondre sans lever d'exception mais sans répartir aucune
        # réponse (silence, transcription trop courte, jobs `done` au résultat
        # vide — non comptés par `still_ko`, qui ne filtre que sur le statut).
        # Sans ce garde, la revue s'ouvrait vide et SANS bandeau (zéro case à
        # cocher, zéro avertissement), et « Valider l'import » créait un
        # entretien à 0 réponse en silence. Parité avec le chemin libre
        # (`_extraire_tours_libre`, mêmes gardes sur ses deux branches).
        manquantes = 1

    if not manquantes:
        # Jobs consommés (leur seul rôle était d'alimenter cet écran) : on
        # nettoie — seulement si tout a abouti. Sur un échec partiel, les
        # garder permet à un nouvel essai (F5-repost, ou re-clic « Envoyer »
        # avant la revue) de ne retenter QUE les tranches encore KO, plafonné
        # par `RECUP_TRANCHES_MAX` — les supprimer inconditionnellement fait
        # retomber ce nouvel essai sur `status["total"] == 0`, donc sur la
        # transcription ENTIÈRE en synchrone (bmad-code-review 2026-09-04,
        # finding F3 : c'est exactement le mur que le plafond existe pour
        # éviter). Le texte lui-même n'est pas en jeu ici : il voyage déjà
        # dans `identity["transcript"]` jusqu'à `import_interview_confirm`.
        delete_segment_jobs(db, session_token)

    contexte = _build_review_context(mission, extracted, identity, manquantes)
    if manquantes:
        contexte["tranches_manquantes"] = manquantes
        contexte["tranches_manquantes_detail"] = detail
    return templates.TemplateResponse(
        request, "interviews/import_review.html", contexte
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
    # `transcript` préservé en cas de ré-affichage du formulaire (erreur
    # d'extraction) : un transcript peut représenter 1h-1h30 d'entretien, il
    # serait inacceptable de le perdre parce que l'appel IA a échoué.
    identity = _build_identity(
        interviewee_name=interviewee_name,
        interviewee_role=interviewee_role,
        interviewee_entity=interviewee_entity,
        interview_date=interview_date,
        audio_backup_path=audio_backup_path,
        transcript=transcript,
        session_token=session_token,
        segment_tail=segment_tail,
    )

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
    identity = _build_identity(
        interviewee_name=interviewee_name,
        interviewee_role=interviewee_role,
        interviewee_entity=interviewee_entity,
        interview_date=interview_date,
        audio_backup_path=audio_backup_path,
        transcript=transcript,
        session_token=session_token,
        segment_tail=segment_tail,
    )
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


def _libre_turns_error(request, mission, identity, message):
    """Rend l'écran d'enregistrement avec un message d'erreur, en conservant le
    travail déjà saisi (transcription, identité).

    Ne sert plus qu'aux refus qui précèdent toute extraction (transcription
    vide) : depuis le 2026-09-04, un échec de répartition Q/R n'y ramène plus —
    il enregistre l'entretien et signale les tranches manquantes sur sa fiche
    (`_extraire_tours_libre`)."""
    return templates.TemplateResponse(
        request,
        "interviews/record_libre.html",
        {
            "mission": mission,
            "recording_available": audio_transcribe.is_available(),
            "error": message,
            "identity": identity,
        },
    )


def _identite_fusionnee(identity: dict, extracted: dict) -> dict:
    """Identité saisie par l'utilisateur complétée par celle relevée à l'oral
    (la saisie manuelle l'emporte, cf. `_merge_identity`), en reconduisant les
    champs annexes que l'IA ne produit jamais."""
    merged = _merge_identity(identity, extracted["identity"])
    merged["interview_date"] = identity.get("interview_date", "")
    merged["audio_backup_path"] = identity.get("audio_backup_path", "")
    merged["audio_segments"] = identity.get("audio_segments", "[]")
    merged["session_token"] = identity.get("session_token", "")
    merged["segment_tail"] = identity.get("segment_tail", "")
    return merged


def _finalize_libre_turns(
    db, request, mission, identity, transcript, session_token, segment_tail,
):
    """Produit les tours de parole puis rend l'écran de revue (étape 2 du
    wizard historique — plus atteignable depuis l'écran d'enregistrement
    depuis le 2026-07-29, cf. `record_libre_enregistrer`, mais conservée)."""
    extracted = _extraire_tours_libre(db, transcript, session_token, segment_tail)
    merged_identity = _identite_fusionnee(identity, extracted)

    # `_extraire_tours_libre` rend un COMPTEUR (`tranches_manquantes`), plus
    # récent que le message texte que cet écran affiche (`{% if error %}`,
    # conservé avec son export PDF de secours) — sans cette traduction, un
    # échec de répartition rendait la revue SANS le dire (bmad-code-review
    # 2026-09-04, finding F5).
    manquantes = extracted.get("tranches_manquantes", 0)
    error = None
    if manquantes:
        pluriel = manquantes > 1
        error = (
            f"{manquantes} tranche{'s' if pluriel else ''} n'"
            f"{'ont' if pluriel else 'a'} pas pu être structurée{'s' if pluriel else ''} "
            "en tours de parole par l'IA. Le texte, lui, est conservé dans la "
            "transcription."
        )

    return templates.TemplateResponse(
        request,
        "interviews/libre_turns_review.html",
        {
            "mission": mission,
            "turns": extracted["turns"],
            "identity": merged_identity,
            "transcript": transcript,
            "error": error,
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
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    identity = _build_identity(
        interviewee_name=interviewee_name,
        interviewee_role=interviewee_role,
        interviewee_entity=interviewee_entity,
        interview_date=interview_date,
        audio_backup_path=audio_backup_path,
        audio_segments=audio_segments,
        transcript=transcript,
        session_token=session_token,
        segment_tail=segment_tail,
    )

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
    )


def _tranche_existante(db: Session, jeton: str, position: int, kind: str):
    """La tranche déjà enregistrée pour ce triplet, ou None. Le triplet est la
    clé d'unicité posée en base (`uq_segment_job_tranche`)."""
    return db.scalar(
        select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == jeton,
            InterviewSegmentJob.position == position,
            InterviewSegmentJob.kind == kind,
        )
    )
