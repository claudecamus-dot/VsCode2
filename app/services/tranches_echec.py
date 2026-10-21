"""Relance des tranches de structuration en échec depuis la fiche d'entretien.

Exigence utilisateur (2026-10-19) : tant qu'une tranche est en échec, la fiche
offre la relance INDIVIDUELLE (par tranche) et GLOBALE, quel que soit le statut
de structuration. La relance réutilise `recover_stalled_or_failed_jobs` (la même
fonction que la récupération de la finalisation) et tourne en tâche de fond
(`au_fond`) : aucun appel IA dans la requête.

Garde-fous (revue pré-commit) : une tranche est RÉSERVÉE dans la requête par un
UPDATE conditionnel atomique (double clic = un seul traitement) ; les tranches
`pending`/`running` FRAÎCHES (traitement normal) ne sont jamais proposées ;
`tranches_manquantes` est RECALCULÉ depuis les jobs, jamais décrémenté.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, object_session

from .. import db as _db
from ..models import Interview, InterviewSegmentJob
from .ai_common import au_fond
from .interview_segment_jobs import (
    _is_stale,
    recover_stalled_or_failed_jobs,
    segment_job_stale_after_s,
    segment_jobs_status,
)

logger = logging.getLogger(__name__)

# Attente (bornée) de la fin d'une structuration en cours avant de rejouer la fusion.
ATTENTE_REJEU_S = 30 * 60
ATTENTE_REJEU_PAS_S = 2.0


def _attendre_fin_structuration(interview_id: int) -> str | None:
    """Statut de l'entretien une fois la structuration en file/en cours terminée,
    ou None à l'échéance. Sessions courtes : rien n'est tenu pendant l'attente."""
    limite = time.monotonic() + ATTENTE_REJEU_S
    while True:
        with _db.SessionLocal() as db:
            interview = db.get(Interview, interview_id)
            if interview is None:
                return None
            statut = interview.structuration_status
        if statut not in ("en_cours", "a_traiter"):
            return statut
        if time.monotonic() >= limite:
            return None
        time.sleep(ATTENTE_REJEU_PAS_S)


def _maintenant() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def tranches_en_echec(interview: Interview) -> list[dict]:
    """Tranches relançables : `failed`, ou `pending`/`running` PÉRIMÉS (orphelins),
    avec du texte. Une tranche fraîche en cours de traitement n'en fait pas partie.
    [{id, position, error}]. Vide sans jeton ni session."""
    jeton = interview.segment_token
    db = object_session(interview)
    if not jeton or db is None:
        return []
    now = _maintenant()
    jobs = [
        j for j in segment_jobs_status(db, jeton)["jobs"]
        if j.text.strip() and (j.status == "failed" or _is_stale(j, now))
    ]
    return [
        {"id": j.id, "position": j.position, "error": (j.error or "")[:160]}
        for j in sorted(jobs, key=lambda j: j.position)
    ]


def reserver_tranches(db: Session, interview: Interview, job_id: int | None = None) -> list[int]:
    """Réserve ATOMIQUEMENT les tranches relançables (`failed`/périmées -> `running`,
    horodatage rafraîchi) et rend les ids réellement obtenus. Un second clic sur la
    même tranche n'obtient rien (elle est `running` et fraîche)."""
    candidats = [t["id"] for t in tranches_en_echec(interview)]
    if job_id is not None:
        candidats = [job_id] if job_id in candidats else []
    now = _maintenant()
    limite = now - timedelta(seconds=segment_job_stale_after_s())
    obtenus = []
    for jid in candidats:
        n = db.execute(
            update(InterviewSegmentJob)
            .where(
                InterviewSegmentJob.id == jid,
                or_(
                    InterviewSegmentJob.status == "failed",
                    and_(InterviewSegmentJob.status.in_(("pending", "running")),
                         InterviewSegmentJob.created_at < limite),
                ),
            )
            .values(status="running", created_at=now)
            .execution_options(synchronize_session=False)
        ).rowcount
        if n:
            obtenus.append(jid)
    db.commit()
    return obtenus


@au_fond
def relancer_tranches_en_fond(interview_id: int, job_ids: list[int]) -> None:
    """Re-traite les tranches RÉSERVÉES (fonction de récupération existante), puis
    recalcule `tranches_manquantes` depuis les jobs et, quand plus rien n'est à
    rattraper, remet l'entretien en file pour fusionner les tours."""
    from .structuration_libre import planifier_structuration

    refaire = False
    attendre = False
    with _db.SessionLocal() as db:
        jobs = list(db.scalars(
            select(InterviewSegmentJob).where(InterviewSegmentJob.id.in_(job_ids))
        ))
        try:
            recover_stalled_or_failed_jobs(db, jobs)
            db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Échec de la relance de tranches de l'entretien %s", interview_id)
            db.rollback()
            # Jamais laisser une tranche `running` orpheline : elle redevient relançable.
            for j in db.scalars(select(InterviewSegmentJob).where(
                    InterviewSegmentJob.id.in_(job_ids),
                    InterviewSegmentJob.status != "done")):
                j.status = "failed"
                j.error = "Échec inattendu de la relance."
            db.commit()
            return
        interview = db.get(Interview, interview_id)
        if interview is None or not interview.segment_token:
            return
        restants = [
            j for j in segment_jobs_status(db, interview.segment_token)["jobs"]
            if j.status != "done" and j.text.strip()
        ]
        statut = interview.structuration_status
        if statut != "a_transcrire":  # a_transcrire : le compteur dit l'audio manquant
            interview.tranches_manquantes = len(restants)
            db.commit()
        rattrapees = not restants and any(j.status == "done" for j in jobs)
        attendre = rattrapees and statut in ("en_cours", "a_traiter")
        if rattrapees and not attendre:
            if statut in ("fait", "echec") or (
                statut == "a_transcrire" and (interview.raw_transcript or "").strip()
            ):
                refaire = bool(db.execute(
                    update(Interview)
                    .where(Interview.id == interview_id, Interview.structuration_status == statut)
                    .values(
                        structuration_status="a_traiter",
                        # Badge recalculé depuis les jobs (a_transcrire : il disait
                        # l'audio manquant, périmé une fois le texte structurable).
                        **({"tranches_manquantes": len(restants)} if statut == "a_transcrire" else {}),
                    )
                ).rowcount)
                db.commit()
    if attendre:
        # Une structuration était en file/en cours : elle a pu lire les tranches
        # AVANT leur récupération. On attend sa fin puis on rejoue la fusion
        # (même bascule conditionnelle que ci-dessus : jamais depuis `fait` sans
        # tranche rattrapée, jamais en doublon).
        fin = _attendre_fin_structuration(interview_id)
        if fin in ("fait", "echec"):
            with _db.SessionLocal() as db:
                refaire = bool(db.execute(
                    update(Interview)
                    .where(Interview.id == interview_id, Interview.structuration_status == fin)
                    .values(structuration_status="a_traiter")
                ).rowcount)
                db.commit()
    if refaire:
        planifier_structuration(interview_id)
