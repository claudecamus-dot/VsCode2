"""Écriture en base des résultats de synthèse (globale, SWOT, résumé exécutif,
difficultés, recommandations) — partagée entre la génération IA
(`routers/synthese.py`), l'export/import d'analyse externe (`routers/export.py`)
et la tâche de fond de synthèse globale.

Extrait de `routers/synthese.py` le 2026-09-09 (finding superviseur
`VSCode2:synthese-service-deguise`, reconduit du cadrage flotte du 2026-09-04) :
`export.py` importait huit symboles PRIVÉS d'un autre router — un service
déguisé, impossible à réutiliser sans charger le router et ses dépendances de
templates. Le contrat des fonctions est inchangé ; seul le module et le
préfixe `_` ont bougé. `global_synthesis_job.py` appelait d'abord une COPIE locale de l'application
de synthèse globale, conservée pour ne pas changer une tâche de fond dans un
déplacement de code ; elle a été supprimée le 2026-09-10 au profit d'un appel
à `apply_global_synthesis_result` — une règle écrite à deux endroits est
exactement ce que cette extraction devait faire disparaître.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import (
    GlobalSynthesis,
    Mission,
    MissionDifficulty,
    MissionExecutiveSummary,
    MissionKpi,
    MissionMaturite,
    MissionRisk,
    MissionSwot,
    Recommendation,
    RecommendationAxis,
)
from .synthese_ai import _coerce_score03, coerce_niveau

SWOT_FIELDS = ("forces", "faiblesses", "opportunites", "menaces")
EXEC_SUMMARY_FIELDS = ("headline", "points", "key_message")


def _get_or_create_1_1[T](db: Session, mission: Mission, attr: str, modele: type[T]) -> T:
    """« Get or create » pour une relation 1-1 (`mission_id` UNIQUE, même forme
    sur les trois modèles de synthèse) — factorisé, les trois partageaient le
    même défaut.

    Course trouvée hors périmètre par l'audit-technique robustesse du
    2026-09-09 : deux requêtes concurrentes sur une mission SANS ligne
    lisaient toutes deux `getattr(mission, attr) is None`, créaient chacune la
    leur, et la seconde à committer heurtait la contrainte d'unicité en 500
    brut plutôt qu'en dégradation propre.

    INSERT ... ON CONFLICT DO NOTHING (upsert natif SQLite), PAS un
    `db.add()` + `IntegrityError` sous SAVEPOINT — première forme essayée
    (2026-09-09), écartée par la MÊME revue qui a trouvé la course (F3) :
    `Session.begin_nested()` n'isole un INSERT que si une transaction
    pysqlite est DÉJÀ ouverte ; sur la toute première écriture d'une session,
    il committait directement (mesuré). Le rendre sûr en toute circonstance
    aurait exigé de désactiver la gestion de transaction implicite de
    pysqlite (`isolation_level=None` + `BEGIN` explicite sur CHAQUE session
    de l'app, cf. `app/db.py`) — changement mesuré comme DANGEREUX : sous ce
    réglage, une session qui ne fait QUE LIRE garde une transaction ouverte
    jusqu'à son `commit()` explicite (l'app en a beaucoup, aucune ne commite
    ses lectures), ce qui a fait échouer par `database is locked` un test de
    course PRÉEXISTANT et sans rapport (`test_synthese_globale_concurrence.py`)
    — un effet de bord app-entière pour corriger un seul helper.

    L'upsert, lui, ne touche à rien d'autre : SQLite résout le conflit
    lui-même, en une seule instruction, sans jamais lever d'exception pour le
    cas courant (`ON CONFLICT DO NOTHING` = 0 ligne affectée, pas d'erreur) —
    et donc rien à catch, rien qui suppose que l'`IntegrityError` était
    forcément la course d'unicité (l'écueil que la même revue, F4, avait
    trouvé dans la version SAVEPOINT). Une vraie violation d'intégrité
    ailleurs (FK sur une mission supprimée entre-temps) continue de lever
    normalement, à l'INSERT lui-même — comportement inchangé par rapport à
    l'ancien `db.add()` nu.

    **Cette fonction COMMITE** (contrat changé le 2026-09-09, dit ici parce
    qu'il ne se devine pas) : l'INSERT doit être visible des autres sessions
    pour que la course se résolve, et le verrou d'écriture SQLite doit être
    relâché tout de suite plutôt que tenu jusqu'à la fin du handler appelant.
    Conséquences à connaître avant d'ajouter un appelant : un `db.rollback()`
    postérieur ne défait plus la création de la ligne, et trois routes GET
    écrivent donc durablement (dont le poll `…/synthese/globale/status`, dont
    la docstring dit « jamais de mutation ici » — elle parlait du statut de
    génération, pas de la ligne elle-même). Les 10 sites d'appel actuels
    appellent tous ce helper juste après `_get_mission`, sans écriture en
    attente : aucun ne voit donc un travail incomplet committé à sa place
    (vérifié un par un, revue du 2026-09-09).
    """
    existant = getattr(mission, attr)
    if existant is not None:
        return existant
    table = sa_inspect(modele).local_table
    db.execute(
        sqlite_insert(table)
        .values(mission_id=mission.id)
        .on_conflict_do_nothing(index_elements=["mission_id"])
    )
    db.commit()
    # Que ce soit NOTRE insertion ou celle d'un concurrent qui a gagné la
    # course, la ligne existe désormais en base — `expire` force la relecture
    # (l'identity map la fait remonter sans requête si un autre objet de
    # cette identité y est déjà chargé, sinon un SELECT la ramène).
    db.expire(mission, [attr])
    return getattr(mission, attr)


def get_or_create_global_synthesis(db: Session, mission: Mission) -> GlobalSynthesis:
    return _get_or_create_1_1(db, mission, "global_synthesis", GlobalSynthesis)


def get_or_create_swot(db: Session, mission: Mission) -> MissionSwot:
    return _get_or_create_1_1(db, mission, "swot", MissionSwot)


def _resultat_entierement_vide(result: dict) -> bool:
    """Un résultat IA qui ne porte AUCUN contenu exploitable.

    Règle retenue (2026-09-27) : vide = TOUS les champs blancs, jamais « au
    moins un champ blanc ». Une SWOT sans « menaces » ou un executive summary
    sans « key_message » sont des générations parfaitement légitimes ; refuser
    celles-là bloquerait le produit bien plus souvent que le défaut qu'on
    corrige. C'est exactement la règle des listes (`_generate_liste_view` :
    « aucun item »), transposée à un enregistrement à plusieurs champs — et
    déjà celle qu'appliquait l'import d'analyse externe (`any(... .strip())`
    dans `routers/export.py`), désormais écrite ICI une seule fois.
    """
    return not any(str(v or "").strip() for v in result.values())


def apply_swot_result(swot: MissionSwot, result: dict) -> bool:
    """Écrit les 4 quadrants, ou REFUSE d'écrire un résultat entièrement vide.

    Rend True si quelque chose a été écrit, False sinon. Ne rien écrire est
    la moitié du contrat ; l'autre moitié est de NE PAS toucher au `status` :
    le repasser à "generated" désarmerait la garde de régénération
    (`services/garde_edition.py`), qui lit `status == "edited"` — le consultant
    perdrait son texte ET la protection contre la perte suivante."""
    if _resultat_entierement_vide(result):
        return False
    for field in SWOT_FIELDS:
        setattr(swot, field, result[field])
    swot.status = "generated"
    swot.generated_at = datetime.now(UTC)
    return True


def get_or_create_executive_summary(
    db: Session, mission: Mission
) -> MissionExecutiveSummary:
    return _get_or_create_1_1(db, mission, "executive_summary", MissionExecutiveSummary)


def apply_executive_summary_result(
    es: MissionExecutiveSummary, result: dict
) -> bool:
    """Même contrat que `apply_swot_result` : rien d'écrit et `status` intact
    quand le résultat est entièrement vide. Rend True si écrit."""
    if _resultat_entierement_vide(result):
        return False
    for field in EXEC_SUMMARY_FIELDS:
        setattr(es, field, result[field])
    es.status = "generated"
    es.generated_at = datetime.now(UTC)
    return True

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


def apply_kpis_result(mission: Mission, kpis: list[dict]) -> None:
    """Remplace les indicateurs de suivi par la liste fournie (position = rang) —
    même contrat que `apply_difficulties_result` (affectation de la collection :
    delete-orphan sur les anciens, relation à jour en session)."""
    items = []
    for k in kpis:
        libelle = (k.get("libelle") or "").strip()
        if libelle:
            items.append(MissionKpi(
                position=len(items), libelle=libelle,
                cible=(k.get("cible") or "").strip(), axe=(k.get("axe") or "").strip(),
            ))
    mission.kpis = items


def apply_risks_result(mission: Mission, risks: list[dict]) -> None:
    """Remplace la matrice risques-contrôles par la liste fournie (position = rang)."""
    items = []
    for r in risks:
        risque = (r.get("risque") or "").strip()
        if risque:
            items.append(MissionRisk(
                position=len(items), risque=risque,
                # Même coercion bornée que la génération (0, « haut », 7… -> 1-3) :
                # un appelant hors IA (import, script) ne doit pas écrire hors échelle.
                gravite=coerce_niveau(r.get("gravite")),
                probabilite=coerce_niveau(r.get("probabilite")),
                controle=(r.get("controle") or "").strip(),
                controle_type=r.get("controle_type") or "propose",
            ))
    mission.risks = items


def apply_maturite_result(mission: Mission, lignes: list[dict]) -> None:
    """Remplace la grille de maturité (position = ordre de la trame)."""
    items = []
    for r in lignes:
        pilier = (r.get("pilier") or "").strip()
        if pilier:
            items.append(MissionMaturite(
                position=len(items), pilier=pilier,
                score=_coerce_score03(r.get("score")),
                justification=(r.get("justification") or "").strip(),
            ))
    mission.maturites = items

# --------------------------------------------------------------------------- #
# Application en base d'un résultat de synthèse globale / recommandations —
# partagée entre la génération IA et l'import d'une analyse externe (évol),
# qui produisent toutes deux exactement la même forme de résultat.
# --------------------------------------------------------------------------- #
def apply_global_synthesis_result(global_synthesis: GlobalSynthesis, result: dict) -> bool:
    """Même contrat que `apply_swot_result` : rien d'écrit et `status` intact
    quand le résultat est entièrement vide. Rend True si écrit.

    `_clean_global` rend TOUJOURS chaque clé d'axe, `""` comprise, donc un
    modèle muet produisait ici un dict complet de chaînes vides qui écrasait
    une synthèse écrite à la main — et la repassait à "generated", désarmant du
    même coup la garde de régénération.
    """
    if _resultat_entierement_vide(result):
        return False
    # `result` est deja borne aux cles d'axes par `_clean_global` ; on ecrit ce
    # qu'il porte, sans presumer des 5 rubriques historiques.
    for key, value in result.items():
        global_synthesis.set_contenu(key, value)
    global_synthesis.status = "generated"
    global_synthesis.generated_at = datetime.now(UTC)
    return True


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
            # `constats` (I2) n'est pas une colonne : des libellés à rattacher
            # aux constats DÉJÀ en base — l'import les écrit avant les recos.
            reco = dict(reco)
            libelles = reco.pop("constats", None)
            recommandation = Recommendation(axis_id=axis.id, position=rpos, **reco)
            if libelles:
                from .constats import lier_aux_constats

                lier_aux_constats(recommandation, libelles, mission)
            db.add(recommandation)
