"""Session libre : enregistrement direct, retours, synthèse et confirmation.

Extrait mécaniquement de `interviews.py` (2026-09-23, constat d'audit
risque technique : 3385 lignes / 56 routes). Code déplacé tel quel ; URL,
ordre d'enregistrement des routes et nom de logger inchangés.
"""
from __future__ import annotations

import json
from datetime import date
from itertools import zip_longest

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import RECORDINGS_DIR, get_session
from ..models import (
    Interview,
    InterviewTurn,
    Mission,
)
from ..services import audio_transcribe, mission_backups
from ..services.interview_libre_extract_ai import (
    InterviewLibreExtractAIError,
    generate_repartition_from_turns,
)
from ..services.mission_axes import axes_of
from ..services.structuration_libre import (
    peut_relancer,
    planifier_structuration,
    relancer,
)
from ..templating import templates
from .interviews_commun import (
    REPARTITION_KEYS,
    _get_mission,
    _parse_repartition,
)
from .interviews_creation import (
    _build_identity,
)
from .interviews_record import (
    _finalize_libre_turns,
    _libre_turns_error,
)

router = APIRouter(tags=["interviews"])

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
    db: Session = Depends(get_session),
):
    """Finalisation après l'écran d'attente : tous les jobs sont terminés (ou un
    a échoué), on fusionne/retombe sur le synchrone et on affiche la revue des
    tours. Même helper que `record_libre` sur le chemin sans attente."""
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
    if (refus := _refus_texte_vide(request, mission, identity, transcript)) is not None:
        return refus
    return _finalize_libre_turns(
        db, request, mission, identity, transcript, session_token, segment_tail,
    )




def _entretien_libre(db, interview_id: int) -> Interview:
    interview = db.get(Interview, interview_id)
    if interview is None or interview.mode != "libre":
        raise HTTPException(status_code=404, detail="Entretien libre introuvable.")
    return interview


@router.post("/interviews/{interview_id}/structurer")
def structurer_maintenant(
    interview_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """« Structurer maintenant » / « Relancer » depuis la fiche. Idempotent :
    `relancer` refuse ce qui est déjà en file/en cours, et `structurer_entretien`
    re-vérifie par UPDATE conditionnel (double clic : un seul appel IA).
    Relançable aussi (F4) : un `en_cours` orphelin, un `fait` à 0 tour. Un
    refus se DIT sur la fiche (`?structuration=refusee`), plus de 303 muet."""
    interview = _entretien_libre(db, interview_id)
    if not relancer(interview.id):
        return RedirectResponse(
            f"/interviews/{interview.id}?structuration=refusee", status_code=303
        )
    return RedirectResponse(f"/interviews/{interview.id}", status_code=303)


@router.get("/interviews/{interview_id}/structurer/statut")
def structurer_statut(
    interview_id: int,
    request: Request,
    suivi: int = 0,
    db: Session = Depends(get_session),
):
    """Fragment de statut (sondage HTMX). `suivi=1` : la page sondait un état
    non terminal — à l'arrivée sur un état terminal, `HX-Refresh` recharge la
    fiche pour afficher les tours produits."""
    interview = db.get(Interview, interview_id)
    if interview is None or interview.mode != "libre":
        # 286 : code HTMX « arrête de sonder » (F4) — un entretien supprimé
        # pendant le sondage ne doit pas faire boucler la page sur des 404.
        return Response(status_code=286)
    response = templates.TemplateResponse(
        request, "interviews/_structuration_statut.html",
        {"interview": interview, "structuration_relancable": peut_relancer(interview)},
    )
    if suivi and interview.structuration_status not in ("a_traiter", "en_cours"):
        response.headers["HX-Refresh"] = "true"
    return response


def _refus_texte_vide(request, mission, identity, transcript):
    """Garde UNIQUE « aucun texte » des routes de finalisation libre (revue
    2026-10-06, m4 : dupliquée sur deux routes, absente de `/from-jobs`, qui
    lançait alors l'extraction IA sur un texte vide). Rend l'écran d'erreur,
    ou None quand il y a du texte."""
    if not transcript.strip():
        return _libre_turns_error(request, mission, identity, "Aucun texte transcrit.")
    return None


def _enregistrer_libre_direct(
    db, request, mission, identity, transcript, session_token, segment_tail,
    background_tasks: BackgroundTasks,
):
    """Enregistre DÉFINITIVEMENT l'entretien — texte, audio, jeton des tranches —
    SANS appel IA, puis programme sa structuration en tâche de fond.

    Demande utilisateur du 2026-10-06 : « enregistrer l'entretien libre avec
    l'audio et la transcription dans un premier temps et après traiter le reste
    en asynchrone ». Avant, ce POST enchaînait l'extraction des tours en
    synchrone (des minutes, PC figé derrière « Traitement des tranches en
    cours… »). Les jobs de tranche ne sont PAS supprimés ici : la structuration
    (`structurer_entretien`) les consomme, et les garde si elle échoue.

    Double POST du même `session_token` (double clic, F5) : l'index unique
    partiel (mission, segment_token) refuse le second ; on redirige vers
    l'entretien déjà créé, sans seconde structuration (M1)."""
    jeton = session_token.strip() or None
    try:
        interview = _creer_interview_libre(
            db,
            mission.id,
            identity,
            [],
            transcript,
            resume="",
            repartition=None,
            segment_token=jeton,
            segment_tail=segment_tail,
            structuration_status="a_traiter",
        )
    except IntegrityError:
        db.rollback()
        existant = db.scalar(
            select(Interview).where(
                Interview.mission_id == mission.id, Interview.segment_token == jeton,
            )
        )
        if existant is None:
            raise
        return _redirection_apres_enregistrement(mission, existant)
    background_tasks.add_task(planifier_structuration, interview.id)
    return _redirection_apres_enregistrement(
        mission, interview, _date_illisible(identity.get("interview_date")),
    )


@router.post("/missions/{mission_id}/interviews/record-libre/enregistrer")
def record_libre_enregistrer(
    mission_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
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
    """Enregistrement direct depuis l'écran de transcription (demande utilisateur
    2026-07-29). Même préambule que `record_libre` — texte obligatoire, attente
    des tranches encore en traitement — mais la suite enregistre l'entretien au
    lieu d'ouvrir la revue des tours."""
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

    if (refus := _refus_texte_vide(request, mission, identity, transcript)) is not None:
        return refus

    # Plus d'écran d'attente des tranches sur ce chemin (2026-10-06) : la
    # structuration différée les attend elle-même, hors requête.
    return _enregistrer_libre_direct(
        db, request, mission, identity, transcript, session_token, segment_tail,
        background_tasks,
    )


@router.post("/missions/{mission_id}/interviews/record-libre/enregistrer/from-jobs")
def record_libre_enregistrer_from_jobs(
    mission_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
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
    """Finalisation de l'enregistrement direct après l'écran d'attente — pendant
    synchrone de `record_libre_from_jobs` pour le chemin sans revue."""
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
    if (refus := _refus_texte_vide(request, mission, identity, transcript)) is not None:
        return refus
    return _enregistrer_libre_direct(
        db, request, mission, identity, transcript, session_token, segment_tail,
        background_tasks,
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
    session_token: str = Form(""),
    segment_tail: str = Form(""),
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
            "identity": _build_identity(
                interviewee_name=interviewee_name,
                interviewee_role=interviewee_role,
                interviewee_entity=interviewee_entity,
                interview_date=interview_date,
                audio_backup_path=audio_backup_path,
                audio_segments=audio_segments,
                transcript=transcript,
                session_token=session_token,
                segment_tail=segment_tail,
            ),
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
            "identity": _build_identity(
                interviewee_name=interviewee_name,
                interviewee_role=interviewee_role,
                interviewee_entity=interviewee_entity,
                interview_date=interview_date,
                audio_backup_path=audio_backup_path,
                audio_segments=audio_segments,
            ),
            "transcript": transcript,
        },
    )


def _lire_proposition(proposed: str, keep: list[str]) -> tuple[dict, set[int]]:
    """Décode les deux champs de formulaire d'un écran de CONFIRMATION — la
    proposition JSON relue par l'utilisateur, et les identifiants cochés.

    Ni `json.loads(proposed)` ni `{int(k) for k in keep}` n'étaient protégés
    (audit-technique robustesse du 2026-09-13) : un champ tronqué, ré-encodé ou
    non numérique levait `JSONDecodeError`/`ValueError` AVANT le moindre
    `db.add`, donc une 500 nue — et l'utilisateur perdait la proposition qu'il
    venait justement de relire et de valider. Les deux autres lecteurs de JSON
    de formulaire de ce fichier (`_parse_repartition`, `_parse_audio_segments`)
    étaient protégés, eux : l'incohérence était interne au fichier.

    Le repli n'est PAS le leur : ces deux-là redonnent silencieusement une
    valeur vide parce qu'ils portent un champ annexe. Ici le champ EST le
    contenu à enregistrer — l'avaler enregistrerait un entretien vide en disant
    que tout s'est bien passé. On refuse donc explicitement, en 400 (la requête
    est mal formée) et non en 500 (le serveur n'est pas en panne), avec un
    message qui dit quoi faire.
    """
    try:
        data = json.loads(proposed)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="La proposition envoyée n'a pas pu être relue — "
            "recommence depuis l'écran précédent.",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=400,
            detail="La proposition envoyée n'a pas le format attendu — "
            "recommence depuis l'écran précédent.",
        )
    try:
        keep_ids = {int(k) for k in keep}
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="La sélection envoyée n'a pas pu être relue — "
            "recommence depuis l'écran précédent.",
        ) from exc
    return data, keep_ids


def _entier_positif(valeur, defaut: int = 0) -> int:
    """Entier d'un champ venu du JSON de formulaire, jamais une 500.

    `int(data.get("tranches_manquantes") or 0)` levait sur une valeur non
    numérique — même chemin, même conséquence que ci-dessus, pour un compteur
    d'affichage. Ici le repli silencieux est le bon : ce champ n'alimente qu'un
    bandeau d'information, le perdre ne perd aucun contenu d'entretien."""
    try:
        return max(defaut, int(valeur or defaut))
    except (TypeError, ValueError):
        return defaut


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
    resume: str, repartition: dict | None, tranches_manquantes: int = 0,
    segment_token: str | None = None, segment_tail: str | None = None,
    structuration_status: str = "fait",
) -> Interview:
    """Crée l'entretien libre et ses tours de parole, puis commit.

    Une seule implémentation pour les deux chemins d'enregistrement définitif :
    la confirmation du wizard (`record_libre_confirm`, avec résumé/répartition)
    et l'enregistrement direct depuis l'écran de transcription
    (`_enregistrer_libre_direct`, sans synthèse).

    `turns` peut être vide depuis le 2026-09-04 : l'échec d'extraction ne
    bloque plus l'enregistrement (`_extraire_tours_libre`), et un entretien à
    0 tour est alors créé, avec son texte dans `raw_transcript` et
    `tranches_manquantes` posé pour le signaler. `mission_export.py` filtre
    déjà les entretiens sans tour de la synthèse globale (aucune matière vide
    injectée) ; `_draft_vide`/`_interview_vide` (missions.py) le traitent
    comme un brouillon vide pour le nettoyage groupé (bmad-code-review
    2026-09-04, finding F8)."""
    try:
        parsed_date = (
            date.fromisoformat(identity["interview_date"])
            if identity.get("interview_date") else None
        )
    except ValueError:
        parsed_date = None

    audio_backup_path, audio_segments = mission_backups.references_audio_sures(
        mission_id,
        identity.get("audio_backup_path"),
        _parse_audio_segments(identity.get("audio_segments", "[]")),
        RECORDINGS_DIR,
    )
    interview = Interview(
        mission_id=mission_id,
        mode="libre",
        status="done",
        interviewee_name=identity.get("interviewee_name", "").strip() or "Sans nom",
        interviewee_role=identity.get("interviewee_role", "").strip() or None,
        interviewee_entity=identity.get("interviewee_entity", "").strip() or None,
        interview_date=parsed_date,
        audio_backup_path=audio_backup_path,
        audio_segments=audio_segments,
        resume=resume.strip() or None,
        repartition=repartition,
        # La transcription brute était postée par l'écran de revue mais jamais
        # lue ici : elle disparaissait à l'enregistrement. Anodin tant que la
        # synthèse aboutissait, grave depuis « Enregistrer sans la synthèse »,
        # dont le cas d'usage est justement l'échec IA à répétition — le texte
        # est alors l'artefact le plus précieux (revue adversariale 2026-07-27).
        raw_transcript=transcript.strip() or None,
        tranches_manquantes=max(0, tranches_manquantes),
        structuration_status=structuration_status,
        segment_token=segment_token,
        segment_tail=(segment_tail or "").strip() or None,
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


def _date_illisible(valeur) -> bool:
    """Une date a été saisie mais `date.fromisoformat` la refuse : elle sera
    écartée à l'enregistrement, et l'écran d'arrivée doit le dire (revue
    2026-10-06, m5 — elle disparaissait sans un mot)."""
    if not valeur:
        return False
    try:
        date.fromisoformat(valeur)
    except ValueError:
        return True
    return False


def _redirection_apres_enregistrement(
    mission: Mission, interview: Interview, date_invalide: bool = False,
):
    """Une mission brouillon reste à nommer/rattacher ; sinon on ouvre
    l'entretien tout juste enregistré.

    Le compteur `tranches_manquantes` (règle du 2026-09-04 — la répartition
    Q/R ne bloque plus l'enregistrement) est lu par la page d'arrivée depuis
    `Interview.tranches_manquantes`, pas depuis cette redirection : persisté
    en base (bmad-code-review 2026-09-04, finding F2), il survit à un F5 là où
    un paramètre d'URL se serait perdu au premier rechargement."""
    if mission.is_draft:
        return RedirectResponse(f"/missions/{mission.id}/finaliser", status_code=303)
    suffixe = "?date_invalide=1" if date_invalide else ""
    return RedirectResponse(f"/interviews/{interview.id}{suffixe}", status_code=303)


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
    identity = _build_identity(
        interviewee_name=interviewee_name,
        interviewee_role=interviewee_role,
        interviewee_entity=interviewee_entity,
        interview_date=interview_date,
        audio_backup_path=audio_backup_path,
        audio_segments=audio_segments,
    )
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
                "identity": _build_identity(
                    interviewee_name=interviewee_name,
                    interviewee_role=interviewee_role,
                    interviewee_entity=interviewee_entity,
                    interview_date=interview_date,
                    audio_backup_path=audio_backup_path,
                    audio_segments=audio_segments,
                ),
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
    return _redirection_apres_enregistrement(
        mission, interview, _date_illisible(interview_date),
    )
