"""Jobs de transcription/extraction par tranche pendant l'enregistrement.

Extrait mécaniquement de `interviews.py` (2026-09-23, constat d'audit
risque technique : 3385 lignes / 56 routes). Code déplacé tel quel ; URL,
ordre d'enregistrement des routes et nom de logger inchangés.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
)
from fastapi.responses import JSONResponse
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import (
    SEGMENT_JOB_KINDS,
    InterviewSegmentJob,
)
from ..services.interview_segment_jobs import (
    merge_segment_answers,
    merge_segment_turns,
    purge_stale_segment_jobs,
    run_segment_job,
    segment_jobs_status,
    segment_jobs_status_light,
)
from .interviews_commun import (
    _get_mission,
)
from .interviews_creation import (
    _mission_questions,
)
from .interviews_record import (
    _tranche_existante,
)

router = APIRouter(tags=["interviews"])

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
    jeton = session_token.strip()[:64]  # normalisé comme partout (F5)
    # Une tranche est identifiée par (session_token, position, kind) et la base
    # le garantit depuis ce lot (2026-09-10 ; le 2026-09-09 est la date du
    # CONSTAT d'audit, pas celle de la garantie). Cette route doit donc être IDEMPOTENTE,
    # et le devenir sans jamais perdre de texte : elle est appelée en
    # fire-and-forget par la rotation JS. Un 500 y est coûteux : la tranche
    # n'est pas enregistrée, et l'écran affiche un échec de transmission. (Le
    # texte lui-même n'est pas perdu pour autant — `record_libre.html` le garde
    # dans son reliquat `segment_tail` tant que la réponse n'est pas OK ; une
    # première rédaction disait « 5 minutes perdues », c'était surévalué,
    # relevé le 2026-09-10, 2e passe, N13.)
    #
    # Les vecteurs qui produisent réellement deux POST au même triplet : une
    # restauration de brouillon (le client remet `segmentJobPosition` à une
    # valeur déjà utilisée) et un rejeu HTTP hors du JS (proxy, navigateur).
    # PAS la relance après échec de la rotation : elle mine P+1, sa position
    # étant incrémentée avant l'envoi (vérifié dans `record_libre.html`, revue
    # du 2026-09-10).
    existant = _tranche_existante(db, jeton, position, kind)
    if existant is None:
        # `INSERT … ON CONFLICT DO NOTHING`, pas un SELECT-puis-INSERT : deux
        # POST concurrents (le proxy qui réémet, la relance utilisateur)
        # passent TOUS DEUX le SELECT ci-dessus, et le perdant lèverait
        # `IntegrityError` contre la contrainte fraîchement posée — donc un 500
        # et la perte de sa tranche, là où le code d'avant la contrainte
        # produisait un doublon, lui, récupérable. Le remède est celui que ce
        # dépôt applique déjà au même constat (`synthese_ecriture._get_or_create_1_1`,
        # 2026-09-09) : SQLite résout le conflit lui-même, sans exception.
        # Trouvé par la revue adversariale du 2026-09-10, finding bloquant n°2.
        table = sa_inspect(InterviewSegmentJob).local_table
        insere = db.execute(
            sqlite_insert(table)
            .values(
                session_token=jeton, position=position, kind=kind,
                status="pending", text=text, mission_id=mission_id or None,
            )
            .on_conflict_do_nothing(
                index_elements=["session_token", "position", "kind"]
            )
        )
        db.commit()
        nous_avons_cree = insere.rowcount == 1
        existant = _tranche_existante(db, jeton, position, kind)
        if existant is None:  # pragma: no cover - ni notre INSERT ni celui du concurrent
            raise HTTPException(status_code=500, detail="Tranche non enregistrée.")
        if nous_avons_cree:
            background_tasks.add_task(run_segment_job, existant.id)
        # Sinon le concurrent a gagné : c'est LUI qui a programmé la tâche.
        # En programmer une seconde ferait tourner deux extractions sur la même
        # ligne (double appel IA, écriture concurrente de `turns_result`).
        return JSONResponse(
            {"job_id": existant.id, "position": position, "status": existant.status}
        )

    # LA RÈGLE, pour une tranche déjà enregistrée : si le client en renvoie une
    # version PLUS LONGUE, c'est elle la vérité et on ré-extrait. Sinon, seule
    # une ligne en ÉCHEC bouge (elle est relancée sur son texte) ; `pending`,
    # `running` et `done` ne sont pas touchés. Les six chemins de la première version
    # fabriquaient un défaut à chaque revue — dont un 409 « soumettez le
    # complément à la suivante » que le client ne sait pas lire : il relançait
    # sur la position SUIVANTE avec le préfixe compris, et `merge_segment_turns`
    # concatène sans dédoublonner, donc les mêmes tours sortaient deux fois
    # (revue adversariale du 2026-09-10, 2e passe, finding bloquant N1).
    #
    # Ré-extraire REMPLACE `turns_result` à cette position : ce n'est jamais un
    # doublon, contrairement à une soumission à une position neuve. Le coût est
    # un appel IA repayé sur une tranche — infiniment moins cher que de la
    # parole d'entretien perdue.
    ancien = existant.text or ""
    if len(text) > len(ancien):
        existant.text = text
        # `mission_id` suit le texte : la requête vient d'être validée avec lui
        # (trame présente pour kind="answers"), et le laisser périmé ferait
        # ré-extraire sur l'ancienne trame (2e passe, N16).
        existant.mission_id = mission_id or None
        # `turns_result` N'EST PAS effacé ici : si la ré-extraction échoue (IA
        # indisponible — la panne la plus banale de ce projet), l'effacer aurait
        # jeté un résultat valide et déjà payé, et `merge_segment_turns`, qui
        # filtre sur `turns_result`, aurait fait DISPARAÎTRE la tranche de la
        # transcription fusionnée. Il est remplacé par `run_segment_job` au
        # moment où le nouveau résultat existe, jamais avant (revue du
        # 2026-09-10, 3e passe, M3).
        existant.status = "pending"
        existant.error = None
        # `created_at` remis à neuf : sinon `_is_stale` re-compte
        # immédiatement comme périmée une tranche qu'on vient de relancer, et
        # l'écran d'attente sort en erreur (2e passe, N14).
        existant.created_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
        # Programmation INCONDITIONNELLE, et c'est délibéré. La version
        # conditionnelle (`if ancien_statut != "pending"`) laissait une fenêtre
        # d'ordre milliseconde où PERSONNE ne possédait plus la ligne : si une
        # tâche basculait la ligne en `running` entre le SELECT et ce commit,
        # `ancien_statut` valait encore `pending`, rien n'était programmé, et
        # cette tâche jetait ensuite son résultat (à raison, le texte ayant
        # changé) — la ligne restait `running` 45 min avant que `_is_stale` ne
        # la libère, écran d'attente tournant à vide. Le doublon d'extraction
        # que la condition évitait est INOFFENSIF depuis que
        # `_ecrire_si_texte_inchange` filtre les écritures périmées : au pire un
        # appel IA payé deux fois sur le même texte, ce qui coûte moins qu'une
        # attente de 45 minutes (revue du 2026-09-10, 4e passe, P2).
        background_tasks.add_task(run_segment_job, existant.id)
        return JSONResponse(
            {"job_id": existant.id, "position": position, "status": "pending"}
        )
    if existant.status == "failed":
        # Rejeu d'une tranche en échec, sur le texte déjà persisté. UPDATE
        # CONDITIONNEL et non lire-puis-écrire : deux rejeux concurrents
        # liraient tous deux `failed` et programmeraient tous deux la tâche —
        # la double extraction que l'INSERT voisin prend soin d'éviter (2e
        # passe, N4). Seul celui qui a effectivement changé la ligne programme.
        pris = db.execute(
            update(InterviewSegmentJob)
            .where(
                InterviewSegmentJob.id == existant.id,
                InterviewSegmentJob.status == "failed",
            )
            .values(
                status="pending", error=None,
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        ).rowcount
        db.commit()
        if pris == 1:
            background_tasks.add_task(run_segment_job, existant.id)
        return JSONResponse(
            {"job_id": existant.id, "position": position, "status": "pending"}
        )
    # `pending`, `running` ou `done` sans texte neuf : rien à faire. On rend
    # l'état RÉEL de la ligne, jamais un statut de complaisance.
    return JSONResponse(
        {"job_id": existant.id, "position": position, "status": existant.status}
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
    session_token: str, since: int = 0, db: Session = Depends(get_session)
):
    """Tours de parole DÉJÀ extraits (jobs terminés) d'une session — alimente
    l'aperçu live en lecture seule de l'onglet « Répartition » de
    `record_libre.html` (Palier A). Lecture seule stricte, AUCUN appel IA : ne
    fait que fusionner (par `position`) les `turns_result` déjà calculés en
    tâche de fond. Les tours du reliquat final (< 5 min) et de la synthèse
    n'apparaissent qu'à l'enregistrement, par le flux existant.

    `since` (curseur de POSITION de tranche, optionnel, même esprit que le
    `since` de `transcribe_file_status`) : ne recharge/refusionne que les
    tranches terminées à partir de cette position — les tours étant produits
    dans l'ordre chronologique des tranches, ils s'ACCUMULENT, jamais réécrits.
    Un client qui ne le fournit pas (`since=0`, ancien frontend en cache) reçoit
    la fusion complète comme avant : compat inchangée. `record_libre.html`
    fournit désormais ce curseur et accumule les tours reçus."""
    status = segment_jobs_status_light(db, session_token, since_position=since)
    merged = merge_segment_turns(status["jobs"], None)
    next_since = max(
        [since] + [j.position + 1 for j in status["jobs"]]
    )
    return JSONResponse(
        {
            "turns": merged["turns"],
            "done": status["done"],
            "total": status["total"],
            "next_since": next_since,
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
    final (< 5 min) n'apparaît qu'à la soumission, par le flux existant.

    Contrairement à `segment_jobs_turns_json`, pas de curseur de position ici :
    une réponse commencée dans une tranche peut être COMPLÉTÉE par une tranche
    ultérieure (`_merge_answer_into`, continuations) — ignorer les tranches
    déjà vues perdrait ces compléments. `segment_jobs_status_light` évite
    quand même le coût dominant mesuré (chargement de la colonne `text`, tout
    le texte source, à chaque sondage 5s)."""
    status = segment_jobs_status_light(db, session_token)
    merged = merge_segment_answers(status["jobs"], None)
    return JSONResponse(
        {
            # Clés str (JSON) — le JS les consomme telles quelles.
            "answers": {str(qid): ans for qid, ans in merged.items()},
            "done": status["done"],
            "total": status["total"],
        }
    )
