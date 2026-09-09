"""Écriture en base des résultats de synthèse (globale, SWOT, résumé exécutif,
difficultés, recommandations) — partagée entre la génération IA
(`routers/synthese.py`), l'export/import d'analyse externe (`routers/export.py`)
et la tâche de fond de synthèse globale.

Extrait de `routers/synthese.py` le 2026-09-09 (finding superviseur
`VSCode2:synthese-service-deguise`, reconduit du cadrage flotte du 2026-09-04) :
`export.py` importait huit symboles PRIVÉS d'un autre router — un service
déguisé, impossible à réutiliser sans charger le router et ses dépendances de
templates. Le contrat des fonctions est inchangé ; seul le module et le
préfixe `_` ont bougé. `global_synthesis_job.py` garde sa copie locale de
l'application de synthèse globale (même effet), pour ne pas changer une tâche
de fond dans un déplacement de code.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import (
    GlobalSynthesis,
    Mission,
    MissionDifficulty,
    MissionExecutiveSummary,
    MissionSwot,
    Recommendation,
    RecommendationAxis,
)

SWOT_FIELDS = ("forces", "faiblesses", "opportunites", "menaces")
EXEC_SUMMARY_FIELDS = ("headline", "points", "key_message")


def get_or_create_global_synthesis(db: Session, mission: Mission) -> GlobalSynthesis:
    if mission.global_synthesis is None:
        mission.global_synthesis = GlobalSynthesis(mission_id=mission.id)
        db.add(mission.global_synthesis)
    return mission.global_synthesis


def get_or_create_swot(db: Session, mission: Mission) -> MissionSwot:
    if mission.swot is None:
        mission.swot = MissionSwot(mission_id=mission.id)
        db.add(mission.swot)
    return mission.swot


def apply_swot_result(swot: MissionSwot, result: dict) -> None:
    for field in SWOT_FIELDS:
        setattr(swot, field, result[field])
    swot.status = "generated"
    swot.generated_at = datetime.now(timezone.utc)


def get_or_create_executive_summary(
    db: Session, mission: Mission
) -> MissionExecutiveSummary:
    if mission.executive_summary is None:
        mission.executive_summary = MissionExecutiveSummary(mission_id=mission.id)
        db.add(mission.executive_summary)
    return mission.executive_summary


def apply_executive_summary_result(
    es: MissionExecutiveSummary, result: dict
) -> None:
    for field in EXEC_SUMMARY_FIELDS:
        setattr(es, field, result[field])
    es.status = "generated"
    es.generated_at = datetime.now(timezone.utc)

def apply_difficulties_result(db: Session, mission: Mission, labels: list) -> None:
    """Remplace les difficultés de la mission par la liste ordonnée fournie
    (position = rang). AFFECTER la collection (plutôt qu'ajouter des lignes via
    mission_id) déclenche le delete-orphan sur les anciennes ET met à jour la
    relation EN SESSION — le ré-affichage voit la nouvelle liste sans dépendre
    d'un refresh post-commit. Les liens verbatim d'une génération précédente
    repartent à zéro : c'est une nouvelle liste de constats."""
    items = []
    for label in labels:
        text = (label or "").strip()
        if text:
            items.append(MissionDifficulty(position=len(items), label=text))
    mission.difficulties = items

# --------------------------------------------------------------------------- #
# Application en base d'un résultat de synthèse globale / recommandations —
# partagée entre la génération IA et l'import d'une analyse externe (évol),
# qui produisent toutes deux exactement la même forme de résultat.
# --------------------------------------------------------------------------- #
def apply_global_synthesis_result(global_synthesis: GlobalSynthesis, result: dict) -> None:
    # `result` est deja borne aux cles d'axes par `_clean_global` ; on ecrit ce
    # qu'il porte, sans presumer des 5 rubriques historiques.
    for key, value in result.items():
        global_synthesis.set_contenu(key, value)
    global_synthesis.status = "generated"
    global_synthesis.generated_at = datetime.now(timezone.utc)


def apply_recommendations_result(db: Session, mission: Mission, axes_data: list[dict]) -> None:
    # Remplace le jeu d'axes/recommandations précédent — même contrat que
    # "Régénérer" sur la synthèse par thème (un nouveau brouillon complet).
    for axis in list(mission.recommendation_axes):
        db.delete(axis)
    db.flush()
    for pos, axis_data in enumerate(axes_data):
        axis = RecommendationAxis(
            mission_id=mission.id, title=axis_data["title"], position=pos
        )
        db.add(axis)
        db.flush()
        for rpos, reco in enumerate(axis_data["recommendations"]):
            db.add(Recommendation(axis_id=axis.id, position=rpos, **reco))
