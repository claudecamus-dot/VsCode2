"""Helpers partagés par les routeurs d'entretien (mission, entretien, réponses, verbatims).

Extrait mécaniquement de `interviews.py` (2026-09-23, constat d'audit
risque technique : 3385 lignes / 56 routes). Code déplacé tel quel ; URL,
ordre d'enregistrement des routes et nom de logger inchangés.
"""
from __future__ import annotations

import json

from fastapi import (
    HTTPException,
    Request,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Answer,
    Interview,
    Mission,
    Question,
    Verbatim,
)
from ..templating import templates




def _parse_repartition(repartition_json: str, valeurs_nommees: tuple) -> dict:
    """Répartition postée par le wizard libre.

    Depuis que les axes d'étude sont configurables (2026-07-27), elle voyage
    entre écrans dans UN champ JSON (`repartition_json`) : ses clés ne sont plus
    connues d'avance. Repli sur les 5 champs nommés historiques quand le JSON
    est absent ou illisible — un formulaire déjà ouvert dans un onglet, ou un
    appelant qui poste l'ancien format, ne doit pas perdre sa répartition ni
    faire échouer un enregistrement d'entretien pour ce champ annexe.
    """
    if repartition_json.strip():
        try:
            data = json.loads(repartition_json)
            if isinstance(data, dict):
                return {
                    str(key): (value or "").strip() if isinstance(value, str) else ""
                    for key, value in data.items()
                }
        except (ValueError, TypeError):
            pass
    # `strict=False` ASSUMÉ, pas oublié : ce repli existe pour qu'un formulaire
    # ouvert dans un vieil onglet, ou un appelant à l'ancien format, ne fasse
    # PAS échouer l'enregistrement d'un entretien pour ce champ annexe (cf.
    # docstring). Y lever sur un uplet plus court casserait précisément ce que
    # le repli protège. Les 3 appelants passent aujourd'hui les 5 valeurs.
    return {
        key: value.strip()
        for key, value in zip(REPARTITION_KEYS, valeurs_nommees, strict=False)
    }


REPARTITION_KEYS = (
    "contexte", "culture_adn", "forces_succes", "points_amelioration", "aspirations",
)




# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _get_mission(db: Session, mission_id: int) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable.")
    return mission


def _get_interview(db: Session, interview_id: int) -> Interview:
    interview = db.get(Interview, interview_id)
    if interview is None:
        raise HTTPException(status_code=404, detail="Entretien introuvable.")
    return interview


def _get_or_create_answer(db: Session, interview: Interview, question_id: int) -> Answer:
    answer = db.scalar(
        select(Answer).where(
            Answer.interview_id == interview.id,
            Answer.question_id == question_id,
        )
    )
    if answer is None:
        answer = Answer(interview_id=interview.id, question_id=question_id)
        db.add(answer)
    return answer


def _all_questions(interview: Interview) -> list[Question]:
    trame = interview.mission.trame
    if trame is None:
        return []  # mission sans trame : aucune question, pas une erreur
    return [q for t in trame.themes for q in t.questions]


def _coverage(interview: Interview) -> tuple[int, int]:
    answers = {a.question_id: a for a in interview.answers}
    questions = _all_questions(interview)
    answered = sum(
        1 for q in questions
        if (a := answers.get(q.id)) is not None and a.status == "answered"
    )
    return answered, len(questions)


def _saved_response(request: Request, interview: Interview, answer: Answer):
    answered, total = _coverage(interview)
    return templates.TemplateResponse(
        request,
        "interviews/_saved.html",
        {"answer": answer, "answered": answered, "total": total},
    )


def _verbatims_for(db: Session, interview_id: int, question_id: int) -> list[Verbatim]:
    return list(
        db.scalars(
            select(Verbatim)
            .where(
                Verbatim.interview_id == interview_id,
                Verbatim.question_id == question_id,
            )
            .order_by(Verbatim.created_at)
        )
    )


def _verbatims_response(request: Request, verbatims: list[Verbatim]):
    return templates.TemplateResponse(
        request, "interviews/_verbatims.html", {"verbatims": verbatims}
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
