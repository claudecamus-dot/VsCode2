"""Transcription bloc par bloc d'un fichier audio importé (2026-07-27).

Pendant un enregistrement micro, la transcription arrive par segments d'~1 min
et l'extraction IA par tranches de 5 min (`interview_segment_jobs`) : le texte
et la répartition Q/R se remplissent au fil de l'eau. L'import d'un fichier
audio, lui, passait par un unique appel synchrone à `transcribe_audio()` —
écran figé jusqu'à la fin (des dizaines de minutes sur un entretien réel), puis
une seule extraction sur toute la transcription.

Ce module rejoue côté serveur ce que la rotation du micro fait côté navigateur :
`run_audio_file_job()` consomme `audio_transcribe.iter_transcribe_blocks()` et
persiste CHAQUE bloc dès qu'il est prêt. L'écran d'enregistrement récupère les
blocs par poll et soumet, bloc par bloc, les `InterviewSegmentJob` d'extraction
habituels — donc aucun nouveau chemin de fusion ni de revue : à partir du texte,
un fichier importé et un enregistrement micro sont indiscernables.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import RECORDINGS_DIR, SessionLocal
from ..models import AudioFileJob
from . import audio_transcribe

logger = logging.getLogger(__name__)


def _remove_audio(job: AudioFileJob) -> None:
    """Supprime le fichier importé du disque. L'audio d'un entretien ne doit
    pas s'entasser une fois son texte obtenu (même exigence que
    `purge_stale_segment_jobs` pour le texte des tranches).

    Uniquement sur SUCCÈS depuis 2026-07-29 : un job `failed` garde son fichier
    pour que l'utilisateur puisse relancer la transcription au bloc qui a
    échoué (route `/audio/transcribe-file/retry`) au lieu de tout ré-importer. La purge
    périodique (`purge_stale_audio_file_jobs`, 7 j) reste le filet qui finit
    par l'enlever si la relance n'a jamais lieu."""
    if not job.filename:
        return
    try:
        (RECORDINGS_DIR / job.filename).unlink(missing_ok=True)
    except OSError:
        logger.warning("Fichier audio importé non supprimé : %s", job.filename)


def release_audio_file(job: AudioFileJob) -> None:
    """Libère le fichier importé d'un job qui n'a plus de raison de le garder.

    Nom public de `_remove_audio` pour les appelants hors de ce module : depuis
    2026-07-29 le fichier survit à un échec pour rendre la reprise possible, et
    la route de relance doit pouvoir le libérer quand elle constate qu'aucune
    reprise n'est possible (tous les blocs déjà transcrits)."""
    _remove_audio(job)


def run_audio_file_job(job_id: int) -> None:
    """Tâche de fond : transcrit le fichier du job bloc par bloc, en
    commitant après chaque bloc pour que le poll de l'UI le voie tout de
    suite. Ouvre sa PROPRE session (celle de la requête est fermée dès la
    réponse renvoyée) et ne lève jamais — un échec est consigné en
    `status="failed"` avec un message destiné à l'UI.

    REPREND là où le job s'était arrêté : les blocs déjà persistés ne sont
    jamais re-transcrits (une relance après échec ne repaie pas les dizaines de
    minutes déjà passées, et n'aboutirait de toute façon pas au même découpage
    si on repartait de zéro)."""
    db = SessionLocal()
    try:
        job = db.get(AudioFileJob, job_id)
        if job is None:
            return
        path = RECORDINGS_DIR / job.filename
        depart = len(job.blocks or [])
        job.status = "running"
        job.error = None
        db.commit()
        try:
            content = path.read_bytes()
            for index, total, text in audio_transcribe.iter_transcribe_blocks(
                content, job.block_seconds, start_index=depart
            ):
                # Réassignation (pas .append) : SQLAlchemy ne détecte pas la
                # mutation en place d'une colonne JSON.
                job.total_blocks = total
                job.blocks = list(job.blocks or []) + [text]
                db.commit()
        except audio_transcribe.TranscriptionError as exc:
            job.status = "failed"
            job.error = str(exc)
            db.commit()
            return
        except OSError as exc:
            job.status = "failed"
            job.error = f"Fichier importé introuvable ou illisible : {exc}"
            db.commit()
            return
        if not any((b or "").strip() for b in (job.blocks or [])):
            # Parité avec `transcribe_audio()`, qui lève « Aucune parole
            # détectée » : sans ce contrôle, un fichier muet (ou entièrement
            # coupé par le VAD) finissait `done` avec des blocs vides et l'UI
            # annonçait « Fichier transcrit » avec un bouton qui reste
            # désactivé, sans rien expliquer (revue adversariale 2026-07-27).
            job.status = "failed"
            job.error = "Aucune parole détectée dans l'enregistrement."
            db.commit()
            return
        job.status = "done"
        db.commit()
    except Exception as exc:  # garde-fou : un job planté ne reste pas "running"
        logger.exception("Échec inattendu de la transcription d'un fichier importé")
        try:
            job = db.get(AudioFileJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                db.commit()
        except Exception:
            pass
    finally:
        try:
            job = db.get(AudioFileJob, job_id)
            # Sur ÉCHEC on garde le fichier : c'est lui qui rend la relance au
            # bloc échoué possible (cf. `_remove_audio`).
            if job is not None and job.status == "done":
                _remove_audio(job)
        except Exception:
            pass
        db.close()


def audio_file_job_stale_after_s() -> int:
    """Délai au-delà duquel un job `pending`/`running` est considéré mort
    (serveur redémarré, thread de fond tué) plutôt que lent — même dispositif
    que `interview_segment_jobs.segment_job_stale_after_s`, qui manquait ici :
    sans lui, l'écran d'enregistrement polle indéfiniment un job qui ne
    changera plus jamais d'état, bouton « Continuer » désactivé à vie (revue
    adversariale 2026-07-27). Défaut 3 h : la transcription d'un entretien de
    3 h est déjà mesurée à ~90-150 min sur ce matériel, la marge doit couvrir
    le pire cas légitime."""
    raw = os.environ.get("AUDIO_FILE_JOB_STALE_AFTER_S")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 3 * 60 * 60


def is_audio_file_job_stale(job: AudioFileJob) -> bool:
    if job.status not in ("pending", "running"):
        return False
    created = job.created_at
    if created.tzinfo is not None:
        created = created.astimezone(timezone.utc).replace(tzinfo=None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now - created) > timedelta(seconds=audio_file_job_stale_after_s())


def purge_stale_audio_file_jobs(db: Session, max_age_days: int = 7) -> None:
    """Balaie les jobs d'import anciens (onglet fermé en cours de route,
    session abandonnée) : leurs blocs portent du texte d'entretien et leur
    fichier peut être resté sur disque si le job n'a jamais abouti. Appelé à
    chaque import — auto-entretien, pas de tâche planifiée, comme
    `purge_stale_segment_jobs`."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=max_age_days
    )
    for job in db.scalars(select(AudioFileJob).where(AudioFileJob.created_at < cutoff)):
        _remove_audio(job)
        db.delete(job)
    db.commit()
