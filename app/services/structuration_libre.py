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


def _attendre_les_tranches(db, jeton: str | None) -> None:
    if not jeton:
        return
    limite = time.monotonic() + ATTENTE_TRANCHES_S
    while True:
        status = segment_jobs_status(db, jeton)
        if status["total"] == 0 or status["all_done"] or status["any_failed"]:
            return
        if time.monotonic() >= limite:
            return
        db.expire_all()
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
        interview = db.get(Interview, interview_id)
        jeton = interview.segment_token or ""
        try:
            _attendre_les_tranches(db, jeton)
            extracted = _extraire_tours_libre(
                db,
                interview.raw_transcript or "",
                jeton,
                interview.segment_tail or "",
                supprimer_jobs=False,
            )
            manquantes = extracted["tranches_manquantes"]
            turns = extracted["turns"]
            statut = "echec" if (not turns and manquantes and jeton) else "fait"
            detectee = extracted.get("identity") or {}
            if interview.interviewee_name == "Sans nom" and (
                detectee.get("interviewee_name") or ""
            ).strip():
                interview.interviewee_name = detectee["interviewee_name"].strip()
            for champ in ("interviewee_role", "interviewee_entity"):
                if not getattr(interview, champ) and (detectee.get(champ) or "").strip():
                    setattr(interview, champ, detectee[champ].strip())
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
            interview.tranches_manquantes = max(0, manquantes)
            interview.structuration_status = statut
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Structuration de l'entretien %s en échec", interview_id)
            db.execute(
                update(Interview)
                .where(Interview.id == interview_id)
                .values(structuration_status="echec")
            )
            db.commit()
            return False
        if statut == "fait" and jeton:
            delete_segment_jobs(db, jeton)
        return statut == "fait"
    finally:
        db.close()

def reconcile_en_cours_on_startup() -> int:
    """Au démarrage : une structuration `en_cours` a été tuée par l'arrêt du
    serveur. Elle repasse en `echec` (relançable depuis la fiche) — JAMAIS
    relancée d'office (arbitrage utilisateur du 2026-10-06 : un redémarrage ne
    doit pas rallumer une génération de plusieurs minutes). Rend le compte."""
    db = _db.SessionLocal()
    try:
        n = db.execute(
            update(Interview)
            .where(Interview.structuration_status == "en_cours")
            .values(structuration_status="echec")
        ).rowcount
        db.commit()
        return n
    finally:
        db.close()