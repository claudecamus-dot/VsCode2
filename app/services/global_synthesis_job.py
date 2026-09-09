"""Génération de la synthèse globale en tâche de fond (FastAPI
`BackgroundTasks`) — finding audit-technique performance:critique du
2026-09-04 : le map-reduce (`synthese_ai.generate_global_synthesis`) peut
dépasser 100 min mesurées sur une mission volumineuse ; l'exécuter dans le
thread de la requête HTTP bloquait la réponse bien au-delà de tout timeout
raisonnable (navigateur, proxy), pour un usage qui n'avait jusque-là aucun
retour tant que ça tournait.

Pendant du pattern déjà en place pour la transcription/répartition Q/R
(`interview_segment_jobs.run_segment_job`, `audio_file_jobs.run_audio_file_job`) :
ouvre sa PROPRE session (celle de la requête est fermée dès la réponse
renvoyée), ne lève jamais — tout échec est consigné sur
`GlobalSynthesis.generation_status`/`generation_error` pour que l'écran (poll)
le resurface, plutôt qu'un job qui reste indéfiniment "running"."""
from __future__ import annotations

from datetime import datetime, timezone

from ..db import SessionLocal
from ..models import GlobalSynthesis, Mission
from .mission_axes import axes_of
from .synthese_ai import SynthesisAIError, generate_global_synthesis
from .synthese_material import all_theme_material, libre_material


def reconcile_running_on_startup() -> int:
    """Repasse à `error` toute synthèse restée `running` d'une séance
    précédente — un crash ou un `--reload` (mode dev documenté) pendant les
    ~100 min d'un job ne laisse alors JAMAIS une mission bloquée sans recours
    (bouton désactivé, poll qui tourne à vide, aucun moyen de relancer sans
    éditer la base à la main). À appeler UNE fois, au `lifespan` du serveur —
    symétrique du filet déjà en place dans `run_global_synthesis_job` pour un
    échec DANS le process, celui-ci couvre le process qui n'existe plus.
    Revue adversariale 2026-09-07."""
    db = SessionLocal()
    try:
        bloquees = db.query(GlobalSynthesis).filter(
            GlobalSynthesis.generation_status == "running"
        ).all()
        for gs in bloquees:
            gs.generation_status = "error"
            gs.generation_error = (
                "Génération interrompue par un redémarrage du serveur — relancez."
            )
        if bloquees:
            db.commit()
        return len(bloquees)
    finally:
        db.close()


def run_global_synthesis_job(mission_id: int) -> None:
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        global_synthesis = mission.global_synthesis if mission else None
        if global_synthesis is None:
            return
        try:
            material_by_theme = all_theme_material(mission)
            material_libre = libre_material(mission)
            result = generate_global_synthesis(
                mission, material_by_theme, material_libre, axes=axes_of(db, mission)
            )
        except SynthesisAIError as exc:
            global_synthesis.generation_status = "error"
            global_synthesis.generation_error = str(exc)
            db.commit()
            return
        # Même application qu'`apply_global_synthesis_result`
        # (services/synthese_ecriture.py, extrait du router le 2026-09-09) :
        # copie locale conservée pour ne pas changer une tâche de fond dans un
        # déplacement de code — à unifier lors d'un prochain passage sur ce job.
        for key, value in result.items():
            global_synthesis.set_contenu(key, value)
        global_synthesis.status = "generated"
        global_synthesis.generated_at = datetime.now(timezone.utc)
        global_synthesis.generation_status = "idle"
        global_synthesis.generation_error = None
        db.commit()
    except Exception as exc:  # garde-fou : un job planté ne doit pas rester "running"
        try:
            # Si l'exception vient d'un commit() raté (ex. verrou SQLite), la
            # session reste en transaction cassée (PendingRollback) : le
            # db.get() de secours ci-dessous lèverait à son tour et serait
            # avalé par le except englobant, laissant le statut bloqué à
            # "running" par un 2e chemin (revue adversariale 2026-09-07).
            db.rollback()
            mission = db.get(Mission, mission_id)
            if mission is not None and mission.global_synthesis is not None:
                mission.global_synthesis.generation_status = "error"
                mission.global_synthesis.generation_error = f"{type(exc).__name__}: {exc}"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()
