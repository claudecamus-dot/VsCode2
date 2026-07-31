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
from ..models import AudioFileJob, InterviewSegmentJob
from . import audio_transcribe
from .interview_segment_jobs import delete_segment_jobs, run_segment_job

logger = logging.getLogger(__name__)

# Retranscription d'un entretien enregistré : nombre de blocs (~1 min chacun)
# regroupés en UNE tranche d'extraction IA. 5 blocs ≈ 5 min de parole, la même
# cadence que la rotation JS des écrans d'enregistrement — assez pour que
# l'extraction ait du contexte (un tour de parole coupé en deux se recolle mal),
# assez court pour qu'un échec ne coûte que 5 min de matière à re-traiter.
BLOCS_PAR_TRANCHE_IA = 5


def _remove_audio(job: AudioFileJob) -> None:
    """Supprime le fichier importé du disque. L'audio d'un entretien ne doit
    pas s'entasser une fois son texte obtenu (même exigence que
    `purge_stale_segment_jobs` pour le texte des tranches).

    Uniquement sur SUCCÈS depuis 2026-07-29 : un job `failed` garde son fichier
    pour que l'utilisateur puisse relancer la transcription au bloc qui a
    échoué (route `/audio/transcribe-file/retry`) au lieu de tout ré-importer. La purge
    périodique (`purge_stale_audio_file_jobs`, 7 j) reste le filet qui finit
    par l'enlever si la relance n'a jamais lieu."""
    if job.filenames:
        # RETRANSCRIPTION d'un entretien enregistré : ces tranches sont les
        # sauvegardes audio de l'utilisateur (`Interview.audio_segments`,
        # servies par l'onglet Backup de la mission), pas un fichier importé
        # temporaire. Les supprimer à la fin du job détruirait l'enregistrement
        # de l'entretien — et pas seulement la copie de travail.
        return
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


def _fichiers_a_traiter(job: AudioFileJob) -> list[str]:
    """Fichiers restant à transcrire pour ce job, dans l'ordre.

    Un import classique n'en a qu'un (`filename`) ; une retranscription
    d'entretien enchaîne ses tranches (`filenames`) en reprenant après celles
    déjà entièrement transcrites."""
    if job.filenames:
        return list(job.filenames)[job.files_done or 0:]
    return [job.filename] if job.filename else []


def tranches_extraction(blocs: list[str]) -> list[str]:
    """Regroupe les blocs transcrits en tranches d'extraction IA (texte prêt à
    poser sur un `InterviewSegmentJob`). Découpage déterministe : la tranche
    d'indice `i` porte toujours les mêmes blocs, ce qui rend la reprise après
    échec calculable sans colonne d'avancement supplémentaire."""
    groupes = [
        blocs[i : i + BLOCS_PAR_TRANCHE_IA]
        for i in range(0, len(blocs), BLOCS_PAR_TRANCHE_IA)
    ]
    return ["\n\n".join(b for b in g if (b or "").strip()) for g in groupes]


def _battement(db, job: AudioFileJob) -> None:
    """Signale que le job PROGRESSE encore. `is_audio_file_job_stale` compare à
    `created_at` : sans ce battement, un traitement plus long que le seuil se
    déclare « ne répond plus » alors qu'il travaille, et l'UI propose une
    relance qui doublonne la tâche de fond en cours (revue adversariale
    2026-07-30). L'extraction IA en a autant besoin que la transcription : elle
    peut représenter la MAJEURE partie du temps total (~5 min par tranche)."""
    job.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()


def _extraire_tours(db, job: AudioFileJob) -> None:
    """Enchaîne l'extraction des tours de parole sur les blocs transcrits, pour
    un job de RETRANSCRIPTION uniquement (2026-07-30).

    Pourquoi ici et pas côté navigateur (comme le fait l'import de fichier des
    écrans d'enregistrement) : l'entretien est déjà enregistré, rien ne court
    contre le temps réel — faire l'extraction dans la même tâche de fond évite
    d'ajouter un troisième dispositif de concurrence côté client (soumissions en
    vol, curseur de couverture, garde de génération) pour aucun gain, Ollama
    sérialisant de toute façon ses appels. Chaque tranche passe par un
    `InterviewSegmentJob` : on hérite de la persistance par tranche, de la
    récupération ciblée (`recover_stalled_or_failed_jobs`) et de la fusion déjà
    éprouvées, sans nouveau chemin de fusion.

    REPRISE : seules les tranches ABOUTIES (`turns_result` non nul) sont sautées
    — un job relancé après un arrêt en cours d'extraction ne repaie pas les
    appels IA déjà faits, MAIS re-joue les tranches restées en échec. Sauter sur
    la seule présence de la `position` (version initiale) rendait une tranche
    échouée définitivement irrécupérable par la relance : sa position existait,
    donc la reprise l'ignorait, et son contenu ne pouvait plus être rattrapé que
    par la récupération synchrone de l'écran de revue (revue adversariale
    2026-07-30)."""
    tranches = tranches_extraction(list(job.blocks or []))
    existants = {
        j.position: j
        for j in db.scalars(
            select(InterviewSegmentJob).where(
                InterviewSegmentJob.session_token == job.session_token
            )
        )
    }
    for position, texte in enumerate(tranches):
        if not texte.strip():
            continue
        existant = existants.get(position)
        if existant is not None:
            if existant.turns_result is not None:
                continue  # déjà abouti : ne pas repayer l'appel IA
            # Tranche connue mais non aboutie (échec, ou serveur arrêté avant
            # son exécution) : on la rejoue TELLE QUELLE plutôt que d'en créer
            # une seconde à la même position, ce qui dupliquerait ses tours à la
            # fusion.
            run_segment_job(existant.id)
            _battement(db, job)
            continue
        segment = InterviewSegmentJob(
            session_token=job.session_token,
            position=position,
            status="pending",
            text=texte,
            kind="libre_turns",
            interview_id=job.interview_id,
        )
        db.add(segment)
        db.commit()
        db.refresh(segment)
        # Séquentiel et dans CE thread : `run_segment_job` ouvre sa propre
        # session, ne lève jamais (un échec est consigné dans `job.error`) et
        # sera récupéré tranche par tranche à l'application du résultat.
        run_segment_job(segment.id)
        _battement(db, job)


def run_audio_file_job(job_id: int) -> None:
    """Tâche de fond : transcrit le fichier du job bloc par bloc, en
    commitant après chaque bloc pour que le poll de l'UI le voie tout de
    suite. Ouvre sa PROPRE session (celle de la requête est fermée dès la
    réponse renvoyée) et ne lève jamais — un échec est consigné en
    `status="failed"` avec un message destiné à l'UI.

    REPREND là où le job s'était arrêté : les blocs déjà persistés ne sont
    jamais re-transcrits (une relance après échec ne repaie pas les dizaines de
    minutes déjà passées, et n'aboutirait de toute façon pas au même découpage
    si on repartait de zéro).

    Un job de RETRANSCRIPTION (`filenames`, 2026-07-30) enchaîne PLUSIEURS
    tranches persistées : les blocs s'accumulent dans le même `blocks`, et la
    reprise repart de la tranche en cours (`files_done`) au bloc en cours
    (`len(blocks) - blocks_before_file`) — les tranches déjà transcrites ne sont
    jamais re-décodées."""
    db = SessionLocal()
    try:
        job = db.get(AudioFileJob, job_id)
        if job is None:
            return
        job.status = "running"
        job.error = None
        db.commit()
        incidents: list[str] = []
        try:
            rang = job.files_done or 0
            for fichier in _fichiers_a_traiter(job):
                rang += 1
                try:
                    content = (RECORDINGS_DIR / fichier).read_bytes()
                    depart = len(job.blocks or []) - (job.blocks_before_file or 0)
                    for index, total, text in audio_transcribe.iter_transcribe_blocks(
                        content, job.block_seconds, start_index=max(0, depart)
                    ):
                        # Réassignation (pas .append) : SQLAlchemy ne détecte pas
                        # la mutation en place d'une colonne JSON.
                        job.total_blocks = (job.blocks_before_file or 0) + total
                        job.blocks = list(job.blocks or []) + [text]
                        # Battement de cœur : cf. `_battement` — sans lui, un
                        # traitement plus long que le seuil de péremption se
                        # déclare « ne répond plus » alors qu'il progresse, et
                        # la relance proposée par l'écran doublonne la tâche de
                        # fond en cours (deux sessions écrivant `job.blocks`).
                        job.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
                        db.commit()
                except (audio_transcribe.TranscriptionError, OSError) as exc:
                    if not job.filenames:
                        # Import d'un fichier UNIQUE : l'abandon du job est le
                        # comportement voulu — c'est lui qui porte la reprise au
                        # bloc échoué (2026-07-29). Traité par les `except` ci-dessous.
                        raise
                    # RETRANSCRIPTION (revue adversariale 2026-07-30) : une seule
                    # tranche illisible (0 octet, `.webm` tronqué par un crash
                    # navigateur — précisément ce que la segmentation existe pour
                    # amortir) condamnait le job entier ET toutes les tranches
                    # SUIVANTES : `files_done` n'étant pas incrémenté, chaque
                    # relance rejouait la même tranche fautive, sans aucune issue
                    # dans l'UI. On l'ignore, on le consigne, et on continue —
                    # perdre 30 min d'un entretien vaut mieux que le perdre entier.
                    incidents.append(f"tranche {rang} ignorée ({exc})")
                if job.filenames:
                    # Tranche terminée (ou ignorée) : la suivante repart de son
                    # bloc 0, et une reprise après échec ne la re-décodera pas.
                    job.files_done = (job.files_done or 0) + 1
                    job.blocks_before_file = len(job.blocks or [])
                    db.commit()
        except audio_transcribe.TranscriptionError as exc:
            job.status = "failed"
            job.error = str(exc)
            db.commit()
            return
        except OSError as exc:
            job.status = "failed"
            job.error = f"Fichier introuvable ou illisible : {exc}"
            db.commit()
            return
        if not any((b or "").strip() for b in (job.blocks or [])):
            # Parité avec `transcribe_audio()`, qui lève « Aucune parole
            # détectée » : sans ce contrôle, un fichier muet (ou entièrement
            # coupé par le VAD) finissait `done` avec des blocs vides et l'UI
            # annonçait « Fichier transcrit » avec un bouton qui reste
            # désactivé, sans rien expliquer (revue adversariale 2026-07-27).
            job.status = "failed"
            # Si TOUTES les tranches ont été ignorées, le dire : « aucune parole
            # détectée » enverrait l'utilisateur chercher un problème de micro
            # alors que ses fichiers sont illisibles.
            job.error = (
                "Aucune tranche exploitable — " + " ; ".join(incidents)
                if incidents
                else "Aucune parole détectée dans l'enregistrement."
            )
            db.commit()
            return
        if job.interview_id:
            # Retranscription d'un entretien enregistré : la transcription ne
            # suffit pas, l'écran de revue attend aussi les tours de parole.
            _extraire_tours(db, job)
        job.status = "done"
        # Job ABOUTI mais partiel : `error` porte l'avertissement que l'écran de
        # revue affiche avant tout écrasement (`status` reste « done », c'est
        # bien un résultat exploitable — simplement incomplet, et l'utilisateur
        # doit le savoir AVANT de remplacer un tour de table complet).
        job.error = " ; ".join(incidents) if incidents else None
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
    # Liste matérialisée : `delete_segment_jobs` commite, et commiter pendant
    # l'itération d'un résultat encore ouvert expire les objets restants.
    perimes = list(
        db.scalars(select(AudioFileJob).where(AudioFileJob.created_at < cutoff))
    )
    for job in perimes:
        _remove_audio(job)
        # Les tranches d'extraction d'une retranscription (`_extraire_tours`)
        # sont rattachées au job par le seul `session_token` — aucune FK ne les
        # emporte. Sans cette ligne, la purge effaçait le job mais laissait en
        # base ses tranches, chacune porteuse de ~5 min de propos d'entretien :
        # exactement ce que la purge existe pour éviter (revue adversariale
        # 2026-07-30).
        delete_segment_jobs(db, job.session_token)
        db.delete(job)
    db.commit()
