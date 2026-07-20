"""Traitement asynchrone des tranches d'un entretien libre long (Palier 2).

Voir `docs/reflexions/enregistrement-segmente-30min.md` §4 et la note CLAUDE.md
« Enregistrement segmenté 30min — Palier 2 ». Pendant l'enregistrement, chaque
tranche de ~30min de texte transcrit est soumise à `run_segment_job()` en tâche
de fond (FastAPI `BackgroundTasks`) pendant que la tranche suivante s'enregistre
— au lieu de traiter tout l'entretien en une seule requête synchrone bloquée à
l'arrêt (le mur des ~2h30 pour 3h, cf. `extraction-longue-duree.md`).

La tranche de 30min est l'unité de *job*, pas l'unité d'*appel IA* :
`extract_turns_from_text()` la redécoupe elle-même en sous-tronçons de
`OLLAMA_CHUNK_MAX_WORDS` mots (map-reduce déjà en place). Un job qui échoue
(timeout persistant, etc.) fait retomber la finalisation sur le traitement
synchrone complet — la correction reste garantie, seul le gain de temps est
perdu sur ce cas.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import InterviewSegmentJob
from .interview_libre_extract_ai import (
    InterviewLibreExtractAIError,
    extract_turns_from_text,
)

_EMPTY_IDENTITY = {
    "interviewee_name": "",
    "interviewee_role": "",
    "interviewee_entity": "",
}


def run_segment_job(job_id: int, text: str) -> None:
    """Tâche de fond : extrait les tours d'UNE tranche de texte et écrit le
    résultat sur le job. Ouvre sa PROPRE session (la session de la requête est
    fermée dès la réponse renvoyée). Ne lève jamais — tout échec est consigné
    en `status="failed"` pour que la finalisation retombe sur le synchrone."""
    db = SessionLocal()
    try:
        job = db.get(InterviewSegmentJob, job_id)
        if job is None:
            return
        job.status = "running"
        db.commit()
        try:
            result = extract_turns_from_text(text)
        except InterviewLibreExtractAIError as exc:
            job.status = "failed"
            job.error = str(exc)
            db.commit()
            return
        job.turns_result = result
        job.status = "done"
        db.commit()
    except Exception as exc:  # garde-fou : un job planté ne doit pas rester "running"
        try:
            job = db.get(InterviewSegmentJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def segment_jobs_status(db: Session, session_token: str) -> dict:
    """État agrégé des jobs d'une session (pour l'écran de statut et la
    décision de finalisation)."""
    if not session_token:
        return {"jobs": [], "total": 0, "done": 0, "failed": 0,
                "all_done": False, "any_failed": False}
    jobs = list(
        db.scalars(
            select(InterviewSegmentJob)
            .where(InterviewSegmentJob.session_token == session_token)
            .order_by(InterviewSegmentJob.position)
        )
    )
    total = len(jobs)
    done = sum(1 for j in jobs if j.status == "done")
    failed = sum(1 for j in jobs if j.status == "failed")
    return {
        "jobs": jobs,
        "total": total,
        "done": done,
        "failed": failed,
        # all_done exige au moins un job ET aucun échec : sinon on retombe sur
        # le chemin synchrone (garantie de correction du Palier 2).
        "all_done": total > 0 and done == total,
        "any_failed": failed > 0,
    }


def merge_segment_turns(jobs: list[InterviewSegmentJob], tail_result: dict | None) -> dict:
    """Concatène les tours des jobs terminés (ordre de `position`) puis ceux du
    reliquat final. La conversation étant chronologique, simple concaténation
    sans appel de fusion — même principe qu'`extract_turns_from_text` entre ses
    propres tronçons. La première identité non vide rencontrée l'emporte
    (l'auto-présentation arrive presque toujours en tout début d'entretien)."""
    all_turns: list[dict] = []
    identity = dict(_EMPTY_IDENTITY)

    results = [j.turns_result for j in sorted(jobs, key=lambda j: j.position) if j.turns_result]
    if tail_result:
        results.append(tail_result)

    for res in results:
        all_turns.extend(res.get("turns", []))
        res_identity = res.get("identity") or {}
        if not any(identity.values()) and any(res_identity.values()):
            identity = {k: res_identity.get(k, "") for k in _EMPTY_IDENTITY}

    return {"turns": all_turns, "identity": identity}


def delete_segment_jobs(db: Session, session_token: str) -> None:
    """Supprime les jobs d'une session une fois consommés (à la finalisation) —
    ils ne servent qu'à alimenter l'écran de revue des tours, inutiles ensuite."""
    if not session_token:
        return
    for job in db.scalars(
        select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == session_token
        )
    ):
        db.delete(job)
    db.commit()
