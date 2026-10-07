"""Structuration d'un entretien libre en tours de parole (extraction IA).

Déplacé tel quel depuis `app/routers/interviews_record.py`
(`_tours_vides`, `_extraire_tours_libre`) et `app/routers/interviews_commun.py`
(`RECUP_TRANCHES_MAX`, `_fenetre_recuperation`) le 2026-10-06, pour que le
traitement asynchrone de l'entretien libre vive dans un SERVICE (un service
n'importe pas un routeur). Les deux modules d'origine ré-exportent ces noms.

Seul écart au déplacement : `_tours_vides` construisait son identité vide par
`_build_identity()` (routeur) — la même valeur est écrite ici en littéral.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

from sqlalchemy import delete, update

from .. import db as _db
from ..models import Interview, InterviewTurn
from .interview_libre_extract_ai import (
    InterviewLibreExtractAIError,
    extract_turns_from_text,
)
from .interview_segment_jobs import (
    delete_segment_jobs,
    merge_segment_turns,
    recover_stalled_or_failed_jobs,
    segment_jobs_status,
)


# Récupération synchrone d'une tranche d'extraction non aboutie, dans la requête
# « Voir le résultat » : plafonnée, sinon un Ollama indisponible transforme ce
# POST en attente de plusieurs heures (cf. `retranscrire_appliquer`). Ce qui
# reste est signalé à l'écran et rattrapé par une relance.
RECUP_TRANCHES_MAX = 3


def _fenetre_recuperation(jobs, deja_abouti):
    """Fenêtre de récupération synchrone : au plus ``RECUP_TRANCHES_MAX``
    tranches par envoi, choisies pour ne pas affamer (revue R3-M1/M3 du
    2026-08-31, partagée par les TROIS appelants de
    ``recover_stalled_or_failed_jobs`` — la version précédente du plafond
    n'existait que sur le chemin libre, et en préfixe fixe).

    - même filtre de matière que le décompte de perte (``still_ko``) : une
      tranche sans texte n'est ni récupérable ni une perte — sans ce filtre
      elle consommait un créneau de récupération à CHAQUE envoi, éternellement ;
    - les tranches jamais tombées en erreur passent AVANT celles qui portent
      déjà un ``error`` : trois échecs déterministes en tête de liste
      monopolisaient sinon le préfixe ``[:RECUP_TRANCHES_MAX]`` et les
      suivantes n'étaient JAMAIS tentées, pendant que le message promettait
      « relance l'envoi » à l'infini.

    Limite assumée : quand TOUTES les tranches restantes portent une erreur,
    la fenêtre redevient un préfixe stable (aucun compteur de tentatives en
    base) — le bandeau `tranches_manquantes` de l'écran d'arrivée couvre ce
    cas depuis le 2026-09-04 (l'enregistrement n'est plus bloqué dessus).
    """
    candidats = [j for j in jobs if not deja_abouti(j) and j.text.strip()]
    candidats.sort(key=lambda j: (j.error is not None, j.position))
    return candidats[:RECUP_TRANCHES_MAX]


def _tours_vides() -> dict:
    return {
        "turns": [],
        "identity": {
            "interviewee_name": "",
            "interviewee_role": "",
            "interviewee_entity": "",
            "interview_date": "",
            "audio_backup_path": "",
            "audio_segments": "[]",
            "transcript": "",
            "session_token": "",
            "segment_tail": "",
        },
    }


def _extraire_tours_libre(db, transcript, session_token, segment_tail, supprimer_jobs=True):
    """Produit les tours de parole d'un entretien libre — et n'échoue JAMAIS.

    Rend `{"turns", "identity", "tranches_manquantes"}`. La répartition Q/R est
    un CONFORT (elle structure un texte qu'on a déjà) : depuis la demande
    utilisateur du 2026-09-04, son échec ne peut plus retenir l'entretien.
    Avant, une seule tranche qu'Ollama ne digérait pas rendait l'entretien non
    enregistrable — l'utilisateur devait relancer l'envoi, régler des variables
    d'environnement, ou cliquer une porte de sortie ; sur un poste lent, il
    perdait la séance. Le texte, lui, est intégralement conservé dans
    `raw_transcript` (`_creer_interview_libre`) : ce qui manque d'une tranche
    non structurée manque du TOUR DE TABLE, jamais de la transcription.

    `tranches_manquantes` porte ce qui n'a pas abouti, pour que l'écran
    d'arrivée le dise — l'ancien blocage protégeait la matière contre une perte
    SILENCIEUSE, et c'est ce silence-là qu'il faut continuer d'empêcher, pas
    l'enregistrement.

    Palier 2 (revue du 2026-07-20 : la 1ère version retombait sur
    `extract_turns_from_text(transcript_ENTIER)` dès qu'un job n'était pas
    `done`, réintroduisant le mur synchrone multi-heures que le Palier 2
    devait précisément éviter — corrigé ici). Si aucun job n'existe (entretien
    < 30min), chemin synchrone historique inchangé. Sinon : chaque job `failed`
    ou bloqué (`recover_stalled_or_failed_jobs`) est re-traité INDIVIDUELLEMENT
    sur sa seule tranche (~30min max), jamais sur la transcription complète —
    puis fusion de tous les tours (jobs + reliquat final). Coût borné au nombre
    de tranches à récupérer, pas à la durée totale de l'entretien."""
    status = segment_jobs_status(db, session_token)

    manquantes = 0

    if status["total"] == 0:
        try:
            extracted = extract_turns_from_text(transcript)
        except InterviewLibreExtractAIError:
            extracted = _tours_vides()
        if not extracted["turns"]:
            # L'IA peut répondre sans lever d'exception et sans détecter aucun
            # tour (silence, transcription trop courte, échec silencieux malgré
            # les relances internes de `extract_turns_from_text`). L'entretien
            # part quand même — avec sa transcription, qui est la matière
            # précieuse — mais le compteur fait dire à l'écran d'arrivée que le
            # tour de table est vide (revue adversariale 2026-07-29 : ce cas
            # créait un entretien `status="done"` sans contenu NI message).
            manquantes = 1
    else:
        # Récupération PLAFONNÉE et perte partielle SIGNALÉE — les deux garde-fous
        # posés le 2026-07-31 sur `retranscrire_appliquer` (d36aef6) manquaient ici,
        # c'est-à-dire sur le chemin NOMINAL du mode libre (revue du 2026-08-31).
        # Sans plafond, un Ollama saturé sur un entretien de 2 h faisait enchaîner
        # 24 × (timeout + relance) dans un seul POST. Sans détection, les tranches
        # restées en échec disparaissaient du tour de table SANS un mot, et le
        # `delete_segment_jobs` de la fin détruisait le texte qui les portait :
        # l'entretien était créé `status="done"`, amputé, sans trace.
        # Fenêtre partagée `_fenetre_recuperation` (revue R3-M3) : même filtre
        # de matière que `still_ko` ci-dessous (une tranche sans texte ne
        # consomme plus un créneau à chaque envoi), et les tranches jamais
        # tentées passent avant les échecs déjà constatés (plus de préfixe
        # fixe qui affamait les tranches 4..N).
        # `j.status == "done"` et NON `bool(j.turns_result)` — alignement sur le
        # mode paramétré (plus haut), qui l'a toujours fait. L'équivalence
        # « porte un résultat » = « a abouti sur son texte COURANT » était vraie
        # jusqu'au 2026-09-10 ; elle ne l'est plus depuis qu'une tranche
        # re-soumise avec un texte plus long conserve son ancien résultat en
        # attendant la ré-extraction (correctif M3, pour ne pas jeter un appel
        # IA déjà payé si la ré-extraction échoue). Sur `turns_result`, une
        # telle tranche était prise pour aboutie : ni relancée, ni comptée
        # manquante — le deck partait avec la tranche TRONQUÉE et sans un mot à
        # l'utilisateur (revue adversariale du 2026-09-10, 4e passe, P1).
        tentees = _fenetre_recuperation(
            status["jobs"], lambda j: j.status == "done"
        )
        recover_stalled_or_failed_jobs(db, tentees)
        # `j.text.strip()` : une tranche sans matière n'est pas une perte (parité
        # avec le mode paramétré, plus haut).
        still_ko = [j for j in status["jobs"] if j.status != "done" and j.text.strip()]
        manquantes = len(still_ko)
        try:
            tail_result = None
            if segment_tail.strip():
                tail_result = extract_turns_from_text(segment_tail)
        except InterviewLibreExtractAIError:
            # Le reliquat (≤ 5 min de parole) compte comme une tranche perdue du
            # tour de table : son texte est dans la transcription, pas dans les
            # tours.
            tail_result = None
            manquantes += 1
        extracted = merge_segment_turns(status["jobs"], tail_result)
        if not extracted["turns"] and not manquantes:
            # Ni tour, ni tranche identifiée comme perdue : tranches vides de
            # matière. On le signale quand même plutôt que de rendre une fiche
            # muette (parité avec le chemin synchrone ci-dessus).
            manquantes = 1

    # Jobs consommés (leur seul rôle était d'alimenter l'écran suivant) : on
    # nettoie. Leur texte est déjà dans la transcription postée par l'écran,
    # que `_creer_interview_libre` enregistre en entier — y compris celui des
    # tranches non structurées.
    # `supprimer_jobs=False` (structuration différée, 2026-10-06) : c'est
    # `structurer_entretien` qui décide, selon l'issue, de les garder pour une
    # relance.
    if supprimer_jobs:
        delete_segment_jobs(db, session_token)
    extracted["tranches_manquantes"] = manquantes
    return extracted


# --------------------------------------------------------------------------- #
# Structuration DIFFÉRÉE (2026-10-06, demande utilisateur : « enregistrer
# l'entretien libre avec l'audio et la transcription dans un premier temps et
# après traiter le reste en asynchrone »). L'enregistrement crée l'entretien
# tout de suite (`structuration_status="a_traiter"`, aucun appel IA) ; cette
# fonction, lancée en tâche de fond ou relancée depuis la fiche, produit les
# tours de parole.
# --------------------------------------------------------------------------- #
logger = logging.getLogger("app.services.structuration_libre")

# Attente des tranches encore en traitement au fil de l'eau (l'écran d'attente
# de l'enregistrement a disparu de ce chemin) : bornée. Au-delà, l'extraction
# part quand même — une tranche toujours en vol y est comptée manquante.
ATTENTE_TRANCHES_S = 30 * 60
ATTENTE_PAS_S = 2.0

_STATUTS_LANCABLES = ("a_traiter", "echec")


def _tranches_en_vol(jeton: str) -> int:
    """Jobs encore `pending`/`running` du jeton — lus dans une session COURTE
    (aucune session tenue pendant l'attente)."""
    with _db.SessionLocal() as db:
        jobs = segment_jobs_status(db, jeton)["jobs"]
        return sum(1 for j in jobs if j.status in ("pending", "running"))


def _attendre_les_tranches(jeton: str | None) -> None:
    """Attend que PLUS AUCUNE tranche ne soit en vol, ou l'échéance.

    Revue 2026-10-06 (F2) : s'arrêter au premier job `failed`/stale
    (`any_failed`) abandonnait les tranches voisines encore en traitement,
    comptées alors manquantes. Le drapeau stale n'est PAS consulté : son
    horloge part de `created_at` (`_is_stale`) et ne se met pas en pause
    pendant qu'une tranche attend le verrou Ollama — une tranche simplement
    retardée y passerait pour morte. Seule l'échéance borne l'attente."""
    if not jeton:
        return
    limite = time.monotonic() + ATTENTE_TRANCHES_S
    while _tranches_en_vol(jeton) and time.monotonic() < limite:
        time.sleep(ATTENTE_PAS_S)


def structurer_entretien(interview_id: int) -> bool:
    """Structure un entretien libre en tours de parole, hors requête HTTP.

    Idempotent : la bascule `a_traiter|echec -> en_cours` est un UPDATE
    CONDITIONNEL ; si une autre exécution l'a déjà prise (double clic, tâche
    auto + relance), rowcount vaut 0 et rien n'est fait — un seul appel IA.
    Tours + `tranches_manquantes` + `fait` sont écrits dans UNE transaction.
    Toute exception, ou une extraction sans aucun tour alors que des tranches
    ont échoué, laisse `echec` (relançable) et garde les jobs de tranche ;
    `fait` les supprime (comme le faisait l'enregistrement synchrone).

    Rend True si cette exécution a structuré l'entretien."""
    db = _db.SessionLocal()
    try:
        pris = db.execute(
            update(Interview)
            .where(
                Interview.id == interview_id,
                Interview.structuration_status.in_(_STATUTS_LANCABLES),
            )
            .values(structuration_status="en_cours")
        ).rowcount
        db.commit()
        if not pris:
            return False
        prise = db.get(Interview, interview_id)
        if prise is None:  # supprimé entre l'UPDATE et la lecture (G3)
            return False
        jeton = prise.segment_token or ""
        # Aucune session tenue pendant l'attente des tranches (F1) : on la
        # ferme, on attend (sessions courtes), puis on en rouvre une.
        db.close()
        try:
            _attendre_les_tranches(jeton)
            db = _db.SessionLocal()
            interview = db.get(Interview, interview_id)
            # Supprimé ou ré-étiqueté PENDANT l'attente (G3) : on sort AVANT
            # tout appel IA — pas d'extraction gâchée sous le verrou Ollama.
            if interview is None or interview.structuration_status != "en_cours":
                return False
            extracted = _extraire_tours_libre(
                db,
                interview.raw_transcript or "",
                jeton,
                interview.segment_tail or "",
                supprimer_jobs=False,
            )
            manquantes = extracted["tranches_manquantes"]
            turns = extracted["turns"]
            # Aucun tour ET une perte signalée : rien de structuré, relançable.
            # Sans condition sur le jeton : un entretien court n'a pas de jobs
            # de tranche, et sa relance repart de la transcription entière.
            statut = "echec" if (not turns and manquantes) else "fait"
            detectee = extracted.get("identity") or {}
            valeurs = {
                "structuration_status": statut,
                "tranches_manquantes": max(0, manquantes),
            }
            if interview.interviewee_name == "Sans nom" and (
                detectee.get("interviewee_name") or ""
            ).strip():
                valeurs["interviewee_name"] = detectee["interviewee_name"].strip()
            for champ in ("interviewee_role", "interviewee_entity"):
                if not getattr(interview, champ) and (detectee.get(champ) or "").strip():
                    valeurs[champ] = detectee[champ].strip()
            # Écriture GARDÉE (revue 2026-10-06, F3) : seulement si l'entretien
            # existe encore ET est toujours `en_cours` pour CETTE exécution.
            # Supprimé ou ré-étiqueté entre-temps (réconciliation, relance) :
            # on n'écrit rien — aucun tour orphelin, aucun « fait » abusif.
            db.expire_all()
            ecrit = db.execute(
                update(Interview)
                .where(
                    Interview.id == interview_id,
                    Interview.structuration_status == "en_cours",
                )
                .values(**valeurs)
                .execution_options(synchronize_session=False)
            ).rowcount
            if not ecrit:
                db.rollback()
                logger.warning(
                    "Structuration de l'entretien %s abandonnée : supprimé ou "
                    "statut changé pendant le traitement", interview_id,
                )
                return False
            db.execute(delete(InterviewTurn).where(InterviewTurn.interview_id == interview_id))
            for position, turn in enumerate(turns):
                db.add(
                    InterviewTurn(
                        interview_id=interview_id,
                        position=position,
                        interlocuteur=turn["interlocuteur"],
                        question=turn["question"],
                        remarque=turn["remarque"],
                        section_title=turn["section_title"],
                    )
                )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Structuration de l'entretien %s en échec", interview_id)
            db.execute(
                update(Interview)
                .where(
                    Interview.id == interview_id,
                    Interview.structuration_status == "en_cours",
                )
                .values(structuration_status="echec")
            )
            db.commit()
            return False
        if statut == "fait" and jeton:
            # Ménage seulement (F6) : l'entretien est déjà écrit « fait ». Un
            # échec ici ne doit ni lever ni le faire passer pour raté — la
            # purge des 7 jours rattrapera les jobs.
            try:
                delete_segment_jobs(db, jeton)
            except Exception:
                db.rollback()
                logger.exception("Jobs de tranche %s non supprimés", jeton)
        return statut == "fait"
    finally:
        db.close()

# --------------------------------------------------------------------------- #
# Worker DÉDIÉ (revue 2026-10-06, F1). Une BackgroundTask synchrone tourne dans
# le pool de threads partagé d'anyio : y dormir jusqu'à 30 min (attente des
# tranches) puis enchaîner des appels IA y bloquait un thread de requêtes. Un
# seul fil démon, une file, une structuration à la fois, dédoublonnée par
# entretien : une relance déjà en file ou en cours est un no-op.
# --------------------------------------------------------------------------- #
# Tests : exécution immédiate dans l'appelant (posé par tests/conftest.py).
EXECUTION_SYNCHRONE = False

_file: queue.Queue[int] = queue.Queue()
_en_vol: dict[int, float] = {}  # id -> instant (monotonic) de mise en file
_verrou_registre = threading.Lock()
_fil: threading.Thread | None = None


def est_en_vol(interview_id: int) -> bool:
    """En file ou en cours dans CE processus (registre en mémoire)."""
    with _verrou_registre:
        return interview_id in _en_vol


def _liberer(interview_id: int) -> None:
    with _verrou_registre:
        _en_vol.pop(interview_id, None)


def _boucle() -> None:
    while True:
        interview_id = _file.get()
        try:
            structurer_entretien(interview_id)
        except Exception:
            logger.exception("Worker de structuration : entretien %s", interview_id)
        finally:
            _liberer(interview_id)
            _file.task_done()


def _demarrer_worker() -> None:
    global _fil
    with _verrou_registre:
        if _fil is None or not _fil.is_alive():
            _fil = threading.Thread(
                target=_boucle, name="structuration-libre", daemon=True
            )
            _fil.start()


def planifier_structuration(interview_id: int) -> bool:
    """Met l'entretien en file de structuration. False si déjà en file ou en
    cours (dédoublonnage). Ne bloque jamais l'appelant."""
    with _verrou_registre:
        if interview_id in _en_vol:
            return False
        _en_vol[interview_id] = time.monotonic()
    if EXECUTION_SYNCHRONE:
        try:
            structurer_entretien(interview_id)
        finally:
            _liberer(interview_id)
        return True
    _demarrer_worker()
    _file.put(interview_id)
    return True


def peut_relancer(interview: Interview) -> bool:
    """Le bouton « Structurer / Relancer » a-t-il un sens (F4 : aucun état
    figé) ? `a_traiter`/`echec` hors file ; `en_cours` que le worker de CE
    processus ne porte pas (orphelin : le statut n'a pas d'horodatage, le
    registre en mémoire fait foi) ; `fait` sans aucun tour."""
    if est_en_vol(interview.id):
        return False
    statut = interview.structuration_status or "fait"
    if statut in ("a_traiter", "echec", "en_cours"):
        return True
    return statut == "fait" and not interview.turns


def relancer(interview_id: int) -> bool:
    """Remet l'entretien `a_traiter` (UPDATE conditionnel sur le statut lu) et
    le met en file. False si la relance est refusée (déjà en file, statut
    changé, ou rien à relancer)."""
    with _db.SessionLocal() as db:
        interview = db.get(Interview, interview_id)
        if interview is None or not peut_relancer(interview):
            return False
        statut = interview.structuration_status
        if statut not in _STATUTS_LANCABLES:
            ok = db.execute(
                update(Interview)
                .where(Interview.id == interview_id,
                       Interview.structuration_status == statut)
                .values(structuration_status="a_traiter")
            ).rowcount
            db.commit()
            if not ok:
                return False
    return planifier_structuration(interview_id)


def reconcile_en_cours_on_startup() -> int:
    """Au démarrage : une structuration `en_cours` a été tuée par l'arrêt du
    serveur. Elle repasse en `echec` (relançable depuis la fiche) — JAMAIS
    relancée d'office (arbitrage utilisateur du 2026-10-06 : un redémarrage ne
    doit pas rallumer une génération de plusieurs minutes). Rend le compte."""
    db = _db.SessionLocal()
    try:
        # `a_traiter` aussi (F4) : au démarrage, la file du worker est vide —
        # un `a_traiter` n'a plus personne pour le prendre et resterait figé.
        n = db.execute(
            update(Interview)
            .where(Interview.structuration_status.in_(("en_cours", "a_traiter")))
            .values(structuration_status="echec")
        ).rowcount
        db.commit()
        return n
    finally:
        db.close()