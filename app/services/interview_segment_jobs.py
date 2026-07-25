"""Traitement asynchrone des tranches d'un entretien libre long (Palier 2).

Voir `docs/reflexions/enregistrement-segmente-30min.md` §4 et la note CLAUDE.md
« Enregistrement segmenté 30min — Palier 2 ». Pendant l'enregistrement, chaque
tranche de ~30min de texte transcrit est soumise à `run_segment_job()` en tâche
de fond (FastAPI `BackgroundTasks`) pendant que la tranche suivante s'enregistre
— au lieu de traiter tout l'entretien en une seule requête synchrone bloquée à
l'arrêt (le mur des ~2h30 pour 3h, cf. `extraction-longue-duree.md`).

La tranche de 30min est l'unité de *job*, pas l'unité d'*appel IA* :
`extract_turns_from_text()` la redécoupe elle-même en sous-tronçons de
`OLLAMA_CHUNK_MAX_WORDS` mots (map-reduce déjà en place).

Garantie de correction, à coût BORNÉ (revue du 2026-07-20 — la première version
retombait sur `extract_turns_from_text(transcript_ENTIER)` en cas de job KO,
réintroduisant le mur multi-heures que le Palier 2 devait justement éviter) :
un job qui échoue OU qui reste bloqué trop longtemps (`_is_stale`, cf. plus bas)
est re-traité individuellement — seule SA tranche (~30min max), jamais la
transcription complète — par `recover_stalled_or_failed_jobs()`.

Généralisation 2026-07-25 : le même dispositif sert désormais aussi le mode
STRUCTURÉ (`record.html`) — `kind="answers"` répartit la tranche sur les
questions de la trame (`extract_answers_from_text`) au fil de l'enregistrement,
au lieu d'un unique appel synchrone sur la transcription complète à l'arrêt.
`_extract_for_job()` route selon `kind` ; `merge_segment_answers()` fusionne
(première réponse non vide par question, ordre chronologique des tranches).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import InterviewSegmentJob, Mission
from .interview_extract_ai import (
    InterviewExtractAIError,
    extract_answers_from_text,
)
from .interview_libre_extract_ai import (
    InterviewLibreExtractAIError,
    extract_turns_from_text,
)

_EMPTY_IDENTITY = {
    "interviewee_name": "",
    "interviewee_role": "",
    "interviewee_entity": "",
}

# Les deux erreurs fonctionnelles d'extraction (libre / structuré) — un job ne
# lève jamais, il consigne le message dans `error` pour l'UI.
_EXTRACT_ERRORS = (InterviewLibreExtractAIError, InterviewExtractAIError)


def _extract_for_job(db: Session, job: InterviewSegmentJob) -> dict:
    """Traite le texte d'UN job selon sa nature (`kind`) et retourne le
    payload à stocker dans `turns_result` :

    - "libre_turns" (historique) : tours de parole d'un entretien libre.
    - "answers" : répartition question/réponse sur la trame de la mission
      (`mission_id` requis — il faut les questions). Clés d'answers converties
      en str (JSON SQLite ne préserve pas les clés int) ; reconverties par
      `merge_segment_answers`.

    Lève une des `_EXTRACT_ERRORS` en cas d'échec — l'appelant décide (statut
    `failed` pour la tâche de fond, re-raise borné pour la récupération)."""
    if job.kind == "answers":
        mission = db.get(Mission, job.mission_id) if job.mission_id else None
        questions = (
            [q for t in mission.trame.themes for q in t.questions]
            if mission is not None and mission.trame is not None
            else []
        )
        if not questions:
            raise InterviewExtractAIError(
                "La mission de ce job n'a pas (ou plus) de trame avec des "
                "questions — impossible de répartir la tranche."
            )
        answers = extract_answers_from_text(questions, job.text)
        return {"answers": {str(qid): ans for qid, ans in answers.items()}}
    return extract_turns_from_text(job.text)


def segment_job_stale_after_s() -> int:
    """Délai (secondes) au-delà duquel un job encore `pending`/`running` est
    considéré bloqué (crash de tâche de fond, serveur redémarré, verrou DB
    jamais levé) plutôt que simplement lent. Défaut 45min — mesures réelles
    (`extraction-longue-duree.md`) montrent qu'une tranche de 30min prend au
    pire ~27-30min de traitement légitime (le temps dépend du volume de mots,
    pas de `OLLAMA_CHUNK_MAX_WORDS`) : 45min laisse une marge confortable sans
    faire attendre indéfiniment sur un job réellement mort."""
    raw = os.environ.get("SEGMENT_JOB_STALE_AFTER_S")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 45 * 60


def _is_stale(job: InterviewSegmentJob, now: datetime) -> bool:
    if job.status not in ("pending", "running"):
        return False
    created = job.created_at
    if created.tzinfo is not None:
        created = created.astimezone(timezone.utc).replace(tzinfo=None)
    return (now - created) > timedelta(seconds=segment_job_stale_after_s())


def run_segment_job(job_id: int) -> None:
    """Tâche de fond : extrait les tours d'UNE tranche de texte (lue depuis la
    colonne `text`, persistée à la création du job — jamais seulement portée
    par la closure de cette fonction, pour survivre à un redémarrage serveur)
    et écrit le résultat sur le job. Ouvre sa PROPRE session (la session de la
    requête est fermée dès la réponse renvoyée). Ne lève jamais — tout échec
    est consigné en `status="failed"` pour que la finalisation puisse relancer
    l'extraction sur cette seule tranche (`recover_stalled_or_failed_jobs`)."""
    db = SessionLocal()
    try:
        job = db.get(InterviewSegmentJob, job_id)
        if job is None:
            return
        job.status = "running"
        db.commit()
        try:
            result = _extract_for_job(db, job)
        except _EXTRACT_ERRORS as exc:
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
        return {"jobs": [], "total": 0, "done": 0, "failed": 0, "stale": 0,
                "all_done": False, "any_failed": False}
    jobs = list(
        db.scalars(
            select(InterviewSegmentJob)
            .where(InterviewSegmentJob.session_token == session_token)
            .order_by(InterviewSegmentJob.position)
        )
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    total = len(jobs)
    done = sum(1 for j in jobs if j.status == "done")
    failed = sum(1 for j in jobs if j.status == "failed")
    stale = sum(1 for j in jobs if _is_stale(j, now))
    return {
        "jobs": jobs,
        "total": total,
        "done": done,
        "failed": failed,
        "stale": stale,
        # all_done exige au moins un job ET aucun échec : sinon on relance la
        # récupération bornée (recover_stalled_or_failed_jobs), jamais un
        # retraitement de la transcription entière.
        "all_done": total > 0 and done == total,
        # any_failed déclenche la fin de l'attente (écran de statut) : un job
        # explicitement `failed`, OU bloqué depuis trop longtemps (stale) —
        # dans les deux cas la finalisation sait le re-traiter individuellement.
        "any_failed": failed > 0 or stale > 0,
    }


def recover_stalled_or_failed_jobs(db: Session, jobs: list[InterviewSegmentJob]) -> None:
    """Re-traite EN SYNCHRONE, un par un, TOUT job pas encore `done` — jamais la
    transcription entière. Appelé uniquement quand on a déjà décidé de
    finaliser MAINTENANT (soit tous les jobs sont `done`, soit un job `failed`/
    stale a fait sauter l'attente) : à ce stade, un job encore `pending`/
    `running` (même frais, non-stale) ne sera jamais rattrapé autrement — le
    laisser de côté perdrait silencieusement sa tranche de contenu (ni fusionné
    ni retraité). On le retraite donc lui aussi, comme un job `failed`.
    Conséquence acceptée : si CE job tourne réellement encore en tâche de fond
    en parallèle, il y a un calcul IA dupliqué (gaspillage, pas incorrection —
    le résultat final ne garde qu'UNE version du texte, pas les deux) ; cas
    rare (un job frère doit être `failed`/stale au même moment) et strictement
    préférable à une perte de contenu silencieuse. Coût borné au nombre de
    jobs non-`done` à cet instant, pas à la durée totale de l'entretien (c'est
    précisément ce qui manquait à la première version du Palier 2). Mute les
    objets `jobs` en place (turns_result/status/error) : `merge_segment_turns`
    appelé juste après voit le résultat à jour sans requête supplémentaire."""
    for job in jobs:
        if job.status == "done":
            continue
        if not job.text.strip():
            # Rien à traiter (job créé sans texte, cas dégénéré) : reste vide.
            job.status = "failed"
            job.error = job.error or "Aucun texte associé à ce job."
            continue
        try:
            job.turns_result = _extract_for_job(db, job)
            job.status = "done"
            job.error = None
        except _EXTRACT_ERRORS as exc:
            job.status = "failed"
            job.error = str(exc)
        db.commit()


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


def _merge_answer_into(merged: dict[int, dict], qid: int, answer: dict) -> None:
    """Fusionne la réponse d'UNE tranche pour une question. Contrairement au
    map-reduce par paragraphes d'`extract_answers_from_text` (first-wins),
    les tranches sont des fenêtres TEMPORELLES de 5 min : une réponse
    commencée à 4:30 et finie à 6:00 est vue par DEUX tranches — garder
    seulement la première amputait la suite (revue adversariale 2026-07-25).
    On concatène donc les continuations distinctes (une redite exacte est
    ignorée) : un éventuel excès de matière est visible et éditable sur
    l'écran de revue, une perte serait invisible."""
    text = (answer.get("text") or "").strip()
    verbatims = [v for v in answer.get("verbatims") or [] if v]
    if qid not in merged:
        merged[qid] = {"text": text, "verbatims": list(verbatims)}
        return
    existing = merged[qid]
    if text and text not in existing["text"]:
        existing["text"] = (existing["text"] + "\n" + text).strip()
    for v in verbatims:
        if v not in existing["verbatims"]:
            existing["verbatims"].append(v)


def merge_segment_answers(
    jobs: list[InterviewSegmentJob], tail_result: dict[int, dict] | None
) -> dict[int, dict]:
    """Fusionne les répartitions Q/R (`kind="answers"`) des jobs terminés,
    dans l'ordre chronologique (`position`), puis le reliquat final — sans
    appel IA (`_merge_answer_into` : concaténation des continuations, jamais
    de perte). Reconvertit les clés str du JSON stocké en int — le contrat
    d'`extract_answers_from_text` et de l'écran de revue."""
    merged: dict[int, dict] = {}
    for job in sorted(jobs, key=lambda j: j.position):
        result = job.turns_result or {}
        for qid_str, answer in (result.get("answers") or {}).items():
            try:
                qid = int(qid_str)
            except (TypeError, ValueError):
                continue
            _merge_answer_into(merged, qid, answer)
    for qid, answer in (tail_result or {}).items():
        _merge_answer_into(merged, int(qid), answer)
    return merged


def purge_stale_segment_jobs(db: Session, max_age_days: int = 7) -> None:
    """Balaie les jobs orphelins (« Recommencer », wizard abandonné, session
    jamais finalisée) : leur colonne `text` porte jusqu'à 5 min de propos
    d'entretien — pour une app qui promet que la voix ne quitte pas la
    machine, ils ne doivent pas s'entasser en base pour toujours. Appelé à
    chaque création de job (auto-entretien, pas de tâche planifiée) ; 7 jours
    laissent large pour reprendre une session après erreur."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=max_age_days
    )
    for job in db.scalars(
        select(InterviewSegmentJob).where(InterviewSegmentJob.created_at < cutoff)
    ):
        db.delete(job)
    db.commit()


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
