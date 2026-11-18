"""Session libre : enregistrement direct, retours, synthèse et confirmation.

Extrait mécaniquement de `interviews.py` (2026-09-23, constat d'audit
risque technique : 3385 lignes / 56 routes). Code déplacé tel quel ; URL,
ordre d'enregistrement des routes et nom de logger inchangés.
"""
from __future__ import annotations

import json
import uuid
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
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..db import RECORDINGS_DIR, get_session
from ..models import (
    Interview,
    InterviewTurn,
    Mission,
)
from ..services import audio_transcribe, mission_backups
from ..services.ai_common import au_fond
from ..services.interview_libre_extract_ai import (
    InterviewLibreExtractAIError,
    generate_repartition_from_turns,
)
from ..services.interview_segment_jobs import delete_segment_jobs, normaliser_jeton
from ..services.mission_axes import axes_of
from ..services.structuration_libre import (
    peut_relancer,
    planifier_structuration,
    relancer,
    structurer_texte_disponible,
)
from ..services.tranches_echec import relancer_tranches_en_fond, reserver_tranches
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
    transcription_incomplete: str = Form(""),
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
        incomplete=bool(str(transcription_incomplete or "").strip()),
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
    texte_disponible: str = Form(""),
    db: Session = Depends(get_session),
):
    """« Structurer maintenant » / « Relancer » depuis la fiche. Idempotent :
    `relancer` refuse ce qui est déjà en file/en cours, et `structurer_entretien`
    re-vérifie par UPDATE conditionnel (double clic : un seul appel IA).
    Relançable aussi (F4) : un `en_cours` orphelin, un `fait` à 0 tour. Un
    refus se DIT sur la fiche (`?structuration=refusee`), plus de 303 muet."""
    interview = _entretien_libre(db, interview_id)
    # Story 3.4 (B-1) : « Structurer le texte disponible » (après confirm() à
    # l'écran) — seule sortie d'un `a_transcrire` qui porte du texte quand la
    # relance des tranches / la retranscription ne produit rien.
    if texte_disponible.strip():
        ok = structurer_texte_disponible(interview.id)
    else:
        ok = relancer(interview.id)
    if not ok:
        return RedirectResponse(
            f"/interviews/{interview.id}?structuration=refusee", status_code=303
        )
    return RedirectResponse(f"/interviews/{interview.id}", status_code=303)


def _worker_vivant(interview) -> bool:
    """Une structuration `en_cours` portée par un worker VIVANT fusionne les mêmes
    jobs : relancer des tranches en parallèle doublerait le travail IA et
    réécrirait les tours. (`en_cours` orphelin = `peut_relancer` vrai : autorisé.)"""
    return interview.structuration_status == "en_cours" and not peut_relancer(interview)


@router.post("/interviews/{interview_id}/tranches/relancer")
def relancer_toutes_les_tranches(
    interview_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_session),
):
    """Relance GLOBALE des tranches en échec, quel que soit le statut de
    structuration (y compris `fait` partiel, `a_transcrire`, `en_cours` orphelin)."""
    interview = _entretien_libre(db, interview_id)
    ids = [] if _worker_vivant(interview) else reserver_tranches(db, interview)
    if not ids:
        return RedirectResponse(f"/interviews/{interview.id}?tranche=indisponible", status_code=303)
    background_tasks.add_task(relancer_tranches_en_fond, interview.id, ids)
    return RedirectResponse(f"/interviews/{interview.id}", status_code=303)


@router.post("/interviews/{interview_id}/tranches/{job_id}/relancer")
def relancer_une_tranche(
    interview_id: int, job_id: int, background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Relance INDIVIDUELLE d'une tranche en échec (même fonction de récupération,
    en tâche de fond). 303 `refusee` si la tranche n'est pas (ou plus) à rattraper."""
    interview = _entretien_libre(db, interview_id)
    ids = [] if _worker_vivant(interview) else reserver_tranches(db, interview, job_id)
    if not ids:
        return RedirectResponse(f"/interviews/{interview.id}?tranche=indisponible", status_code=303)
    background_tasks.add_task(relancer_tranches_en_fond, interview.id, ids)
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


def _entier_tolerant(valeur, defaut: int = 0) -> int:
    """Entier d'un champ de formulaire texte : vide, non numérique ou négatif
    redonnent `defaut` (jamais un 422 brut sur un champ annexe)."""
    try:
        return max(0, int(str(valeur).strip()))
    except (TypeError, ValueError):
        return defaut


def _position_entiere(valeur):
    """Position d'une tranche en entier, ou None si illisible (jamais de TypeError :
    le JSON du client peut porter une chaîne)."""
    if isinstance(valeur, bool):
        return None
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None


def _base_occupee(exc: BaseException) -> bool:
    """Vrai si l'erreur SQLite est un verrou/occupation transitoire (détection par
    message, le driver n'exposant pas de code stable ici)."""
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _etat_audio(mission, identity, attendu: int = 0) -> tuple[bool, int]:
    """(a_audio, manquantes) pour l'audio de sauvegarde de CETTE mission, par les
    mêmes validateurs que la persistance (`references_audio_sures`).
    `manquantes` = positions 0..N-1 absentes (N = max(`attendu`, dernière
    position + 1)) ; 0 = audio complet. `a_audio` = au moins une référence VALIDE
    (`attendu` seul ne prouve rien) ; `attendu` est plafonné (champ client)."""
    chemin, segments = mission_backups.references_audio_sures(
        mission.id,
        identity.get("audio_backup_path"),
        _parse_audio_segments(identity.get("audio_segments", "[]")),
        RECORDINGS_DIR,
        mission=mission,  # Story 3.4 (B-11) : même garde que la persistance
    )
    attendu = min(max(0, attendu), 1000)  # plafond de bon sens (champ client)
    if not segments:
        if chemin:
            return True, 0 if attendu <= 1 else attendu - 1  # ancien format : un fichier
        return False, 0  # aucune référence audio valide : `attendu` seul ne prouve rien
    positions = {_position_entiere(seg.get("position")) for seg in segments}
    if None in positions:
        return True, max(0, attendu - len(segments))
    # Positions hors 0..999 (négatives incluses, champ client) écartées : elles
    # ne comblent aucun trou et ne doivent pas gonfler `range(n)`.
    positions = {p for p in positions if 0 <= p <= 999}
    if not positions:
        return True, max(1, attendu - len(segments))
    n = min(max(attendu, max(positions) + 1), 1000)
    return True, len(set(range(n)) - positions)


def _refus_texte_vide(request, mission, identity, transcript, *, audio_suffit=False, attendu=0,
                       incomplete=False):
    """Garde UNIQUE « aucun texte » des routes de finalisation libre. Rend l'écran
    d'erreur, ou None. Avec `audio_suffit` (« enregistrer sans attendre », 2026-10-19),
    seul « ni texte NI aucune référence audio » est refusé : un audio tronqué ne
    refuse plus (décision du 2026-10-19). L'état de l'entretien créé est décidé
    par `_enregistrer_libre_direct` (`a_transcrire` si texte vide OU partiel avec
    audio) ; la fiche explique ce blocage et propose ses sorties
    (`_structuration_statut.html`, Story 3.4 / B-1)."""
    vide = not transcript.strip()
    if not audio_suffit:
        return _libre_turns_error(request, mission, identity, "Aucun texte transcrit.") if vide else None
    if vide and not _etat_audio(mission, identity, attendu)[0]:
        return _libre_turns_error(request, mission, identity, "Aucun texte transcrit.")
    return None


def _enregistrer_libre_direct(
    db, request, mission, identity, transcript, session_token, segment_tail,
    background_tasks: BackgroundTasks, transcription_incomplete: bool = False,
    audio_attendu: int = 0,
):
    """Enregistre DÉFINITIVEMENT l'entretien — texte, audio, jeton des tranches —
    SANS appel IA, puis programme sa structuration en tâche de fond.

    Demande utilisateur du 2026-10-06 : enregistrer l'audio et la transcription
    d'abord, structurer ensuite en asynchrone. Les jobs de tranche ne sont PAS
    supprimés ici : la structuration (`structurer_entretien`) les consomme.

    Règle d'état : `a_transcrire` si texte vide OU (partiel ET audio présent) — la
    reprise repart de l'audio ; texte partiel SANS audio => structuré en `a_traiter`
    avec `tranches_manquantes` (sinon bloqué sans reprise possible).

    Double POST du même `session_token` : l'index unique partiel refuse le second ;
    on redirige vers l'entretien déjà créé. Il n'est COMPLÉTÉ que s'il est encore
    `a_transcrire` ET sans aucun tour ; sinon rien n'est écrit (jamais de
    réécriture d'un texte édité, fait ou en cours de structuration).

    Sans jeton client, un jeton `uuid4` est généré : il N'offre AUCUNE
    déduplication (chaque POST obtient un jeton neuf, donc crée un entretien) ; la
    déduplication n'existe que si le client envoie son `session_token`."""
    jeton = normaliser_jeton(session_token) or uuid.uuid4().hex
    texte = transcript.strip()
    a_audio, manquantes = _etat_audio(mission, identity, max(0, audio_attendu))
    sans_texte = not texte or (transcription_incomplete and a_audio)
    if sans_texte:
        tranches = manquantes
    else:
        tranches = 1 if transcription_incomplete else 0
    try:
        interview = _creer_interview_libre(
            db,
            mission.id,
            identity,
            [],
            transcript,
            resume="",
            repartition=None,
            tranches_manquantes=tranches,
            segment_token=jeton,
            segment_tail=segment_tail,
            structuration_status="a_transcrire" if sans_texte else "a_traiter",
        )
    except IntegrityError:
        db.rollback()
        existant = db.scalar(
            select(Interview).where(
                Interview.mission_id == mission.id, Interview.segment_token == jeton,
            )
        )
        if existant is None and db.get(Mission, mission.id) is None:
            # Mission supprimée entre la lecture et le commit : 404, pas un 409 trompeur.
            raise HTTPException(status_code=404, detail="Mission introuvable.") from None
        if existant is None:
            # Contrainte violée mais aucun entretien de ce jeton dans cette
            # mission (F6) : refus clair plutôt qu'une 500 nue.
            raise HTTPException(
                status_code=409,
                detail="Cet enregistrement n'a pas pu être enregistré (conflit) — "
                "recharge la mission pour vérifier s'il existe déjà.",
            ) from None
        if (
            texte
            and existant.structuration_status == "a_transcrire"
            and not existant.turns
            and len(texte) > len((existant.raw_transcript or "").strip())
        ):
            valeurs = {
                "raw_transcript": texte,
                "tranches_manquantes": tranches,  # retry incomplet : le compte suit le texte
            }
            # Story 3.4 (B-13) : le reliquat suit le texte complété — sinon la
            # structuration relancée repartait de l'ancien `segment_tail`.
            if (segment_tail or "").strip():
                valeurs["segment_tail"] = segment_tail
            relance = not sans_texte
            if relance:
                valeurs["structuration_status"] = "a_traiter"
            # Revue finale Story 3.4 (chemin voisin de F2) : la lecture
            # « a_transcrire sans tour » ci-dessus peut être périmée si un
            # `confirm` du même jeton vient de compléter l'entretien (statut
            # `fait` + tours validés). UPDATE CONDITIONNEL : on n'écrase ni le
            # statut ni le texte d'un entretien complété entre-temps, et la
            # structuration n'est relancée que si la transition a eu lieu.
            gagne = db.execute(
                update(Interview)
                .where(
                    Interview.id == existant.id,
                    Interview.structuration_status == "a_transcrire",
                    ~select(InterviewTurn.id)
                    .where(InterviewTurn.interview_id == Interview.id)
                    .exists(),
                )
                .values(**valeurs)
            ).rowcount == 1
            db.commit()
            if gagne and relance:
                background_tasks.add_task(au_fond(planifier_structuration), existant.id)
        return _redirection_apres_enregistrement(mission, existant)
    if not sans_texte:
        background_tasks.add_task(au_fond(planifier_structuration), interview.id)
    return _redirection_apres_enregistrement(
        mission, interview, _date_illisible(identity.get("interview_date")),
    )


def _enregistrer_direct_commun(
    db, request, background_tasks, mission_id, transcript, interviewee_name,
    interviewee_role, interviewee_entity, interview_date, audio_backup_path,
    audio_segments, session_token, segment_tail, audio_attendu, transcription_incomplete,
):
    """Corps UNIQUE des deux routes d'enregistrement direct (principale et
    `/from-jobs`) : mêmes gardes, mêmes règles d'état."""
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
    attendu = _entier_tolerant(audio_attendu)
    incomplete = bool(str(transcription_incomplete or "").strip())
    if (refus := _refus_texte_vide(
        request, mission, identity, transcript, audio_suffit=True,
        attendu=attendu, incomplete=incomplete,
    )) is not None:
        return refus
    try:
        return _enregistrer_libre_direct(
            db, request, mission, identity, transcript, session_token, segment_tail,
            background_tasks, transcription_incomplete=incomplete, audio_attendu=attendu,
        )
    except OperationalError as exc:
        db.rollback()
        if _base_occupee(exc):
            raise HTTPException(
                status_code=503,
                detail="Base occupée, réessaie dans un instant (rien n'a été perdu : "
                "le texte reste dans l'écran).",
                headers={"Retry-After": "5"},
            ) from None
        raise


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
    audio_attendu: str = Form("0"),
    transcription_incomplete: str = Form(""),
    db: Session = Depends(get_session),
):
    """Enregistrement direct depuis l'écran de transcription (demande utilisateur
    2026-07-29) : enregistre l'entretien au lieu d'ouvrir la revue des tours."""
    return _enregistrer_direct_commun(
        db, request, background_tasks, mission_id, transcript, interviewee_name,
        interviewee_role, interviewee_entity, interview_date, audio_backup_path,
        audio_segments, session_token, segment_tail, audio_attendu, transcription_incomplete,
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
    audio_attendu: str = Form("0"),
    transcription_incomplete: str = Form(""),
    db: Session = Depends(get_session),
):
    """Finalisation de l'enregistrement direct après l'écran d'attente — mêmes
    gardes et mêmes règles d'état que la route sœur (`_enregistrer_direct_commun`)."""
    return _enregistrer_direct_commun(
        db, request, background_tasks, mission_id, transcript, interviewee_name,
        interviewee_role, interviewee_entity, interview_date, audio_backup_path,
        audio_segments, session_token, segment_tail, audio_attendu, transcription_incomplete,
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
    session_token: str = Form(""),
    segment_tail: str = Form(""),
    tranches_manquantes: str = Form(""),
    db: Session = Depends(get_session),
):
    """Retour de l'étape 3 vers l'étape 2 SANS perdre les tours de parole
    (portés en champs cachés par l'écran de synthèse) — même logique que
    `record_libre_retour`. Aucun appel IA.

    Le jeton de session et le reliquat sont REPOSTÉS (revue finale Story 3.4,
    F1) : sans eux, `libre_turns_review.html` rendait `session_token=""`, et
    synthèse -> Retour -> synthèse -> double confirmation créait deux entretiens
    (l'index unique ne dédoublonne qu'un jeton non vide)."""
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
                session_token=session_token,
                segment_tail=segment_tail,
            ),
            "transcript": transcript,
            "tranches_manquantes": _entier_positif(tranches_manquantes),
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
        # Story 3.4 (B-11) : garde anti-réutilisation d'id SQLite.
        mission=db.get(Mission, mission_id),
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
    session_token: str = Form(""),
    tranches_manquantes: str = Form(""),
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
        # Story 3.4 (B-9) : transmis à l'écran de synthèse, qui le reposte à
        # `confirm` — l'index unique y dédoublonne un double POST.
        session_token=session_token,
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
                "tranches_manquantes": _entier_positif(tranches_manquantes),
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
                "tranches_manquantes": _entier_positif(tranches_manquantes),
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
            "tranches_manquantes": _entier_positif(tranches_manquantes),
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
    session_token: str = Form(""),
    segment_tail: str = Form(""),
    tranches_manquantes: str = Form(""),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    manquantes = _entier_positif(tranches_manquantes)

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
                    # Revue finale Story 3.4 (F1) : le jeton survit au réaffichage.
                    session_token=session_token,
                    segment_tail=segment_tail,
                ),
                "transcript": transcript,
                "tranches_manquantes": manquantes,
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
    # Story 3.4 (B-9) : le jeton de session (posté par `libre_turns_review.html`)
    # est enregistré sur l'entretien — l'index unique partiel (mission, jeton)
    # refuse alors un double POST, qui redirige vers l'entretien déjà créé au
    # lieu d'en créer un second. Sans jeton : comportement inchangé.
    jeton = normaliser_jeton(session_token) or None
    try:
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
            tranches_manquantes=manquantes,
            segment_token=jeton,
        )
    except IntegrityError:
        db.rollback()
        existant = db.scalar(
            select(Interview).where(
                Interview.mission_id == mission.id, Interview.segment_token == jeton,
            )
        ) if jeton else None
        if existant is None:
            raise HTTPException(
                status_code=409,
                detail="Cet entretien n'a pas pu être enregistré (conflit) — "
                "recharge la mission pour vérifier s'il existe déjà.",
            ) from None
        return _confirm_sur_jeton_existant(db, mission, existant, turns_to_save, transcript,
                                           resume, repartition)
    # Les jobs de tranche de la session sont CONSOMMÉS : les tours viennent d'être
    # validés à la main. Gardés, un job en échec rendait l'entretien « relançable »
    # (`peut_relancer`) et la relance aurait réécrit ces tours (Story 3.4, B-9).
    if jeton:
        delete_segment_jobs(db, jeton)
    return _redirection_apres_enregistrement(
        mission, interview, _date_illisible(interview_date),
    )


def _memes_tours(interview: Interview, turns: list[dict]) -> bool:
    """Les tours en base sont-ils exactement ceux postés (double POST) ?"""
    existants = sorted(interview.turns, key=lambda t: t.position or 0)
    cles = ("interlocuteur", "question", "remarque", "section_title")
    return len(existants) == len(turns) and all(
        all((getattr(e, k) or None) == (t.get(k) or None) for k in cles)
        for e, t in zip(existants, turns, strict=True)  # longueurs déjà égales
    )


def _confirm_sur_jeton_existant(db, mission, existant, turns, transcript, resume, repartition):
    """Story 3.4 (B-9, revue) : `confirm` tombe sur un entretien qui porte déjà
    le jeton de la session. Jamais de perte silencieuse du contenu validé :
    - double POST (mêmes tours) : redirection vers l'entretien déjà créé ;
    - entretien `a_transcrire` SANS tour (« enregistrer sans attendre » du même
      jeton) : COMPLÉTÉ avec les tours, le résumé et la répartition validés ;
    - sinon : 409 explicite."""
    if existant.turns and _memes_tours(existant, turns):
        return _redirection_apres_enregistrement(mission, existant)
    if existant.structuration_status == "a_transcrire" and not existant.turns:
        # Revue finale Story 3.4 (F2) : deux POST concurrents lisaient tous deux
        # « a_transcrire sans tour » et inséraient chacun les tours (doublons).
        # La transition est désormais un UPDATE CONDITIONNEL : seul le POST qui
        # la gagne (rowcount == 1) insère ; le perdant relit et se comporte
        # comme un double POST (redirection si mêmes tours, sinon 409).
        gagne = db.execute(
            update(Interview)
            .where(Interview.id == existant.id,
                   Interview.structuration_status == "a_transcrire")
            .values(structuration_status="fait")
        ).rowcount == 1
        if not gagne:
            db.rollback()
            db.expire(existant)  # relit statut ET tours écrits par le gagnant
            if existant.turns and _memes_tours(existant, turns):
                return _redirection_apres_enregistrement(mission, existant)
            raise HTTPException(
                status_code=409,
                detail="Un entretien de cette session existe déjà avec un autre contenu — "
                "recharge la mission pour le consulter.",
            )
        for position, turn in enumerate(turns):
            db.add(InterviewTurn(
                interview_id=existant.id, position=position,
                interlocuteur=turn["interlocuteur"], question=turn["question"],
                remarque=turn["remarque"], section_title=turn["section_title"],
            ))
        texte = (transcript or "").strip()
        if len(texte) > len((existant.raw_transcript or "").strip()):
            existant.raw_transcript = texte
        existant.resume = (resume or "").strip() or existant.resume
        if repartition:
            existant.repartition = repartition
        existant.structuration_status = "fait"
        # `tranches_manquantes` n'est PLUS remis à 0 (F2, option conservatrice) :
        # rien ne prouve que les tours postés couvrent les tranches que l'aperçu
        # tronqué n'avait pas — le signal reste jusqu'à une retranscription
        # complète (`interviews_retranscription`), seule à pouvoir le prouver.
        db.commit()
        delete_segment_jobs(db, existant.segment_token)
        return _redirection_apres_enregistrement(mission, existant)
    raise HTTPException(
        status_code=409,
        detail="Un entretien de cette session existe déjà avec un autre contenu — "
        "ouvre-le depuis la mission ; les tours validés ici n'ont PAS été enregistrés.",
    )
