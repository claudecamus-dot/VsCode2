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

import logging

from ..db import SessionLocal
from ..models import GlobalSynthesis, Mission
from .constats import apply_constats_ia
from .mission_axes import axes_of
from .synthese_ai import SynthesisAIError, generate_constats, generate_global_synthesis
from .synthese_ecriture import apply_global_synthesis_result
from .synthese_material import all_theme_material, libre_material

logger = logging.getLogger(__name__)


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


def _generer_constats(db, mission, material_by_theme, material_libre) -> str | None:
    """I2 étape 1, APRÈS la synthèse déjà committée : un échec ici ne la
    défait jamais. Rend le message d'erreur à poser sur le job (seul canal vers
    l'écran), en disant que la synthèse, elle, est à jour ; None si tout va bien.
    Sortie vide = constats existants intacts (`apply_constats_ia`)."""
    # Arbitrage 2026-09-29 (option A) : l'IA ne propose des constats que si la
    # mission n'en a AUCUN. Des constats importés ou édités sont le travail du
    # consultant ; une régénération de la synthèse ne les remplace jamais.
    if mission.constats:
        return None
    try:
        result = generate_constats(
            mission, material_by_theme, material_libre, axes=axes_of(db, mission)
        )
    except SynthesisAIError as exc:
        return f"Synthèse globale mise à jour, mais constats non générés : {exc}"
    apply_constats_ia(db, mission, result["constats"])
    return None


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
        # Une SEULE écriture de la règle d'application (unification du
        # 2026-09-10, constat d'audit risque technique). La copie locale posée
        # lors de l'extraction du service — « à unifier lors d'un prochain
        # passage » — est ce que l'extraction devait justement supprimer : deux
        # endroits où corriger la même règle, dont un qu'on oublie.
        if not apply_global_synthesis_result(global_synthesis, result):
            # Résultat entièrement vide : rien d'écrit, `status` intact (la garde
            # de régénération reste armée). Le job n'a pas de réponse HTTP où
            # afficher quoi que ce soit — le SEUL canal vers l'écran est le
            # couple `generation_status="error"` / `generation_error`, que le
            # panneau (poll `/synthese/globale/status`, et rechargement de page)
            # rend déjà comme le message `error`. On l'emprunte donc ici : sans
            # lui, le consultant verrait la génération finir « idle » et son
            # texte inchangé, sans savoir si elle a tourné.
            global_synthesis.generation_status = "error"
            global_synthesis.generation_error = (
                "La génération n'a produit aucun contenu — synthèse globale "
                "inchangée. Réessayez, ou vérifiez les réponses des entretiens."
            )
            db.commit()
            return
        # Ces deux-là restent ICI : ils appartiennent au cycle de vie du JOB
        # (le suivi d'avancement interrogé par l'écran), pas à l'application
        # d'un résultat de synthèse — le chemin synchrone du router n'a pas de
        # statut de génération à remettre au repos.
        #
        # La synthèse est committée AVANT les constats (statut encore
        # `running` : le bouton reste bloqué, pas de 2e job concurrent) — un
        # échec des constats ne la perd donc jamais.
        db.commit()
        erreur_constats = _generer_constats(
            db, mission, material_by_theme, material_libre)
        global_synthesis.generation_status = "error" if erreur_constats else "idle"
        global_synthesis.generation_error = erreur_constats
        db.commit()
    except Exception as exc:  # garde-fou : un job planté ne doit pas rester "running"
        # Journal À L'ENTRÉE du handler, avant toute condition (audit-technique
        # robustesse du 2026-09-13). Le `logger.exception` vivait plus bas, DANS
        # le `if mission is not None and ...` : trois branches partaient donc
        # sans une seule ligne de journal — `db.rollback()` qui lève, mission
        # disparue, `global_synthesis` nul — et l'exception d'origine finissait
        # dans le `except Exception: pass` ci-dessous, job échoué sans que rien
        # ne permette de savoir pourquoi. Les deux tâches de fond sœurs au même
        # contrat (`audio_file_jobs`, `interview_segment_jobs`) journalisent,
        # elles, dès l'entrée : celle-ci était la seule des trois à ne pas le
        # faire. `logger.exception` hors d'un `except` perdrait la trace, d'où
        # sa place ici et non dans le `try` de secours.
        logger.exception(
            "Échec inattendu de la synthèse globale (mission %s)", mission_id
        )
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
                # Le TYPE seul, jamais le texte : `generation_error` est rendu
                # au navigateur (`routers/synthese.py`) et `str(exc)` porte
                # volontiers un chemin du poste. Détail complet au journal.
                mission.global_synthesis.generation_error = (
                    f"Échec inattendu ({type(exc).__name__})."
                )
                db.commit()
        except Exception:
            # Le secours lui-même a échoué (session cassée, base verrouillée) :
            # l'échec d'origine est DÉJÀ au journal, ligne ci-dessus. C'est
            # exactement ce que ce `pass` avalait avant.
            logger.exception(
                "Echec du secours apres un echec de synthese globale (mission %s)",
                mission_id,
            )
    finally:
        db.close()
