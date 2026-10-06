"""Écran de saisie : réponses, notes, identité, verbatims.

Extrait mécaniquement de `interviews.py` (2026-09-23, constat d'audit
risque technique : 3385 lignes / 56 routes). Code déplacé tel quel ; URL,
ordre d'enregistrement des routes et nom de logger inchangés.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from itertools import zip_longest

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import (
    Interview,
    Verbatim,
)
from ..services import audio_transcribe
from ..services.structuration_libre import peut_relancer
from ..services.interview_export import (
    group_turns_into_sections,
    transcript_of,
)
from ..services.interview_extract_ai import (
    InterviewExtractAIError,
    extract_answers_from_text,
)
from ..templating import templates
from ..uploads import (
    UploadTropVolumineux,
    lire_upload_audio_borne,
)
from .interviews_commun import (
    _all_questions,
    _coverage,
    _get_interview,
    _get_or_create_answer,
    _saved_response,
    _verbatims_for,
    _verbatims_response,
)
from .interviews_libre import (
    _lire_proposition,
)

# Même nom de logger qu'avant le découpage : filtres et tests s'y accrochent.
logger = logging.getLogger("app.routers.interviews")

router = APIRouter(tags=["interviews"])

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
    # Persisté sur `Interview.tranches_manquantes` (2026-09-04, bmad-code-review
    # finding F2) — plus lu depuis la query string : un F5 sur cette fiche
    # perdait sinon le signal, alors même que le manque persistait en base.
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
                "tranches_manquantes": interview.tranches_manquantes,
                # Date saisie illisible, écartée à l'enregistrement : la fiche
                # le dit au lieu de l'avaler (revue 2026-10-06, m5).
                "ident_date_invalide": request.query_params.get("date_invalide") == "1",
                # Structuration différée (F4) : bouton là où une relance a un
                # sens, et refus d'une relance DIT plutôt qu'avalé.
                "structuration_relancable": peut_relancer(interview),
                "structuration_refusee": (
                    request.query_params.get("structuration") == "refusee"
                ),
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
    date_invalide = False
    try:
        interview.interview_date = date.fromisoformat(interview_date) if interview_date else None
    except ValueError:
        # Même tolérance que les autres routes portant ce champ : une date
        # illisible ne fait pas perdre la saisie des tours qui l'accompagne.
        # Mais elle se DIT (revue 2026-10-06, m5) : drapeau lu par la fiche.
        date_invalide = True
    db.commit()
    suffixe = "?date_invalide=1" if date_invalide else ""
    return RedirectResponse(f"/interviews/{interview.id}{suffixe}", status_code=303)


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
        # Borne mémoire AVANT tout décodage, même finding securite du
        # 2026-09-09 et même plafond audio que `transcribe_segment` : cette
        # dictée-ci n'a AUCUNE rotation côté navigateur (capture.html
        # enregistre d'un seul tenant), c'est donc le chemin le plus exposé
        # des deux.
        contenu = await lire_upload_audio_borne(file)
        # CPU-bound hors de la boucle d'événements (même finding perf que
        # transcribe_segment) ; l'accès db reste dans le thread de la requête.
        transcript = await asyncio.to_thread(audio_transcribe.transcribe_audio, contenu)
        interview.free_notes = (
            f"{interview.free_notes.strip()}\n\n{transcript}"
            if (interview.free_notes or "").strip()
            else transcript
        )
        db.commit()
    except UploadTropVolumineux as exc:
        # 413 et `{"error": ...}` : capture.html n'affiche que ce champ.
        return JSONResponse({"error": str(exc)}, status_code=413)
    except audio_transcribe.TranscriptionBusyError as exc:
        # Avant `TranscriptionError` (dont elle hérite) : cette route partage
        # `audio_transcribe.transcribe_audio()` avec `/audio/transcribe-segment`
        # (interviews_audio.py) — même verrou `_MODEL_LOCK`, même contrat de
        # refus « occupé » (revue adversariale 2026-09-15, code-review du lot 1
        # atelier-dev : ce site était resté sur le 422 générique, faisant
        # perdre une dictée le temps que le modèle se libère plutôt que de
        # signaler un état rejouable).
        retry_after = max(1, round(exc.retry_after_s))
        return JSONResponse(
            {"error": str(exc), "code": "busy", "retry_after_s": retry_after},
            status_code=503,
            headers={"Retry-After": str(retry_after)},
        )
    except audio_transcribe.TranscriptionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception:
        logger.exception("Échec inattendu de la transcription des notes libres")
        return JSONResponse(
            {"error": "Échec inattendu de la transcription des notes."},
            status_code=500,
        )

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
    data, keep_ids = _lire_proposition(proposed, keep)

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
