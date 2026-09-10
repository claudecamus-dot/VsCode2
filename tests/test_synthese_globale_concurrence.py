"""Course (TOCTOU) sur le lancement de la synthèse globale — finding
audit-technique robustesse du 2026-09-09 (`.claude/audits/VSCode2.json` côté
hub) : « la garde anti-double-lancement est un lire-puis-écrire Python, pas un
UPDATE conditionnel ni un verrou. Deux requêtes quasi simultanées lisent toutes
deux `generation_status != running` avant qu'aucune n'ait commité et lancent
DEUX `run_global_synthesis_job` sur la même mission ».

L'enjeu n'est pas cosmétique : le map-reduce peut dépasser 100 min mesurées, et
les deux jobs écrivent le MÊME `GlobalSynthesis`. Le seul rempart d'avant était
déclaré dans le code : « le bouton est désactivé pendant l'exécution » — une
garde côté JS, contournée par un double-clic, un second onglet ou un appel
direct de la route.

La course est reproduite DE FAÇON DÉTERMINISTE, sans threads ni sleeps : on
force l'entrelacement exact qui rendait le défaut exploitable. La requête 1 est
suspendue APRÈS avoir lu `generation_status` (donc sur une valeur périmée), le
temps qu'une requête 2 complète parte, prenne le jeton et commite ; la requête 1
reprend alors sur sa lecture périmée. Un test à deux threads prouverait la même
chose au prix d'un ordonnancement non reproductible.

Échec sur le code d'avant : `generation_status = "running"` puis `db.commit()`
en Python, donc DEUX `background_tasks.add_task(run_global_synthesis_job, ...)`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import GlobalSynthesis, Mission
from app.routers import synthese as synthese_router


def setup_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def teardown_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _creer_mission(nom: str) -> int:
    """Mission AVEC sa ligne `GlobalSynthesis` déjà en base.

    `get_or_create_global_synthesis` se contente d'un `db.add()` sans commit :
    deux requêtes concurrentes sur une mission qui n'en a pas encore en
    créeraient chacune une, et la seconde s'arrêterait sur la contrainte
    d'unicité `uq_global_synthesis_mission` — un 500, pas un double lancement.
    Ce n'est pas la course qu'on teste ici : on la met donc hors du chemin pour
    isoler celle du champ `generation_status`."""
    db = SessionLocal()
    try:
        mission = Mission(name=nom)
        db.add(mission)
        db.commit()
        db.add(GlobalSynthesis(mission_id=mission.id))
        db.commit()
        return mission.id
    finally:
        db.close()


def test_deux_lancements_concurrents_ne_lancent_qu_un_seul_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _creer_mission("Mission course au lancement")
    lancements: list[int] = []

    monkeypatch.setattr(synthese_router, "is_configured", lambda: True)
    # Le job réel n'a pas à tourner : ce qui se compte ici, c'est le nombre de
    # LANCEMENTS. Le laisser tourner remettrait de surcroît le statut à `idle`
    # et effacerait l'état que le second appel doit rencontrer.
    monkeypatch.setattr(
        synthese_router, "run_global_synthesis_job", lambda mid: lancements.append(mid)
    )

    reel = synthese_router._total_answer_count
    concurrent_lance = [False]

    def _total_answer_count_avec_concurrent(material_by_theme):
        """Point d'entrelacement : appelé DANS `generate_global`, après la
        lecture de `generation_status` et avant la prise du jeton. Une seule
        fois (la route le rappelle ensuite pour le rendu du panneau)."""
        if not concurrent_lance[0]:
            concurrent_lance[0] = True
            concurrent = TestClient(app)
            reponse = concurrent.post(
                f"/missions/{mission_id}/synthese/globale/generate"
            )
            assert reponse.status_code == 200, reponse.text
        # Non nul : sans quoi la route s'arrête sur « aucune réponse saisie »
        # et n'atteint jamais la garde qu'on teste.
        return 1

    monkeypatch.setattr(
        synthese_router, "_total_answer_count", _total_answer_count_avec_concurrent
    )

    reponse = client.post(f"/missions/{mission_id}/synthese/globale/generate")

    assert reponse.status_code == 200, reponse.text
    assert lancements == [mission_id], (
        "double lancement : deux map-reduce de plusieurs dizaines de minutes "
        "sur la meme mission, ecrivant le meme GlobalSynthesis"
    )
    assert reel is not synthese_router._total_answer_count  # garde du montage

    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        assert mission.global_synthesis.generation_status == "running", (
            "le perdant de la course doit rendre l'etat reel, pas 'idle'"
        )
    finally:
        db.close()


def test_la_toute_premiere_generation_demarre_bien(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contrepartie de la garde atomique sur une mission qui n'a PAS encore de
    ligne `GlobalSynthesis` : `get_or_create_global_synthesis` se contente d'un
    `db.add()`, l'id vaut donc None tant que rien n'a flushé. Un WHERE composé
    sur cet id vide ne matche rien et empêcherait toute première génération —
    le cas le plus courant du produit. Les autres tests de ce fichier créent la
    ligne d'avance (pour isoler la course), ils ne le couvriraient pas."""
    db = SessionLocal()
    try:
        mission = Mission(name="Mission sans synthese prealable")
        db.add(mission)
        db.commit()
        mission_id = mission.id
        assert mission.global_synthesis is None
    finally:
        db.close()
    lancements: list[int] = []

    monkeypatch.setattr(synthese_router, "is_configured", lambda: True)
    monkeypatch.setattr(
        synthese_router, "run_global_synthesis_job", lambda mid: lancements.append(mid)
    )
    monkeypatch.setattr(synthese_router, "_total_answer_count", lambda m: 1)

    reponse = client.post(f"/missions/{mission_id}/synthese/globale/generate")

    assert reponse.status_code == 200, reponse.text
    assert lancements == [mission_id]


def test_un_clic_sur_une_synthese_deja_en_cours_ne_relance_rien(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contrepartie non concurrente : la garde de base tient toujours. Sans
    elle, l'UPDATE conditionnel serait le seul rempart et un appel sur une
    génération en cours rendrait un panneau incohérent."""
    mission_id = _creer_mission("Mission deja en cours")
    lancements: list[int] = []

    monkeypatch.setattr(synthese_router, "is_configured", lambda: True)
    monkeypatch.setattr(
        synthese_router, "run_global_synthesis_job", lambda mid: lancements.append(mid)
    )
    monkeypatch.setattr(synthese_router, "_total_answer_count", lambda m: 1)

    premier = client.post(f"/missions/{mission_id}/synthese/globale/generate")
    second = client.post(f"/missions/{mission_id}/synthese/globale/generate")

    assert premier.status_code == 200
    assert second.status_code == 200
    assert lancements == [mission_id]


# --------------------------------------------------------------------------- #
# Course sur l'ACQUITTEMENT d'une erreur — trouvée par la revue adversariale
# du 2026-09-09 sur le correctif ci-dessus (F2), DANS le correctif lui-même.
#
# Avant F2, un clic sur une synthèse en statut `error` commençait par un
# acquittement INCONDITIONNEL — écriture ORM de `idle`, puis `db.commit()` —
# AVANT l'UPDATE conditionnel qui prend le jeton. Un tel `commit()` réécrit
# EN AVEUGLE les colonnes chargées par leur valeur PYTHON locale (aucune
# clause WHERE, aucun verrou optimiste). Sur un objet chargé par la session B
# AVANT que la session A n'ait pris le jeton, l'acquittement de B — qui
# s'exécute APRÈS — écrase le `running` que A vient de poser, avec la valeur
# périmée `idle`/`error` que B avait en mémoire. Le `WHERE generation_status
# != "running"` de B, exécuté juste après, relit alors ce `idle` qu'il vient
# lui-même de poser et prend AUSSI le jeton : deux jobs lancés sur la même
# mission, chacun croyant être seul.
#
# Même technique d'entrelacement déterministe que la course de lancement
# ci-dessus, avec un point de suspension différent : la requête 1 est
# suspendue APRÈS avoir chargé `global_synthesis` en statut `error` (donc
# avant tout acquittement), le temps qu'une requête 2 complète parte,
# acquitte, prenne le jeton et committe ; la requête 1 reprend alors sur son
# objet PÉRIMÉ et tente son propre acquittement.
#
# Échec sur le code d'avant F2 : deux `background_tasks.add_task(
# run_global_synthesis_job, ...)`.
# --------------------------------------------------------------------------- #
def test_l_acquittement_d_une_erreur_n_ecrase_pas_un_lancement_concurrent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `_creer_mission` (ci-dessus dans ce fichier) crée DÉJÀ la ligne
    # `GlobalSynthesis` — sa propre docstring l'explique : c'est pour mettre
    # la course de CRÉATION hors du chemin et isoler celle de
    # `generation_status`. On la met donc à jour, on n'en insère pas une
    # seconde (heurterait `uq_global_synthesis_mission`).
    mission_id = _creer_mission("Mission acquittement concurrent")
    with SessionLocal() as db:
        mission = db.get(Mission, mission_id)
        mission.global_synthesis.generation_status = "error"
        mission.global_synthesis.generation_error = "échec précédent"
        db.commit()

    lancements: list[int] = []
    monkeypatch.setattr(synthese_router, "is_configured", lambda: True)
    monkeypatch.setattr(
        synthese_router, "run_global_synthesis_job", lambda mid: lancements.append(mid)
    )

    concurrent_lance = [False]

    def _avec_concurrent(material_by_theme):
        # Appelé APRÈS le chargement de `global_synthesis` (statut `error`
        # encore en mémoire côté requête 1), AVANT tout acquittement — c'est
        # le point d'entrelacement.
        if not concurrent_lance[0]:
            concurrent_lance[0] = True
            concurrent = TestClient(app)
            reponse = concurrent.post(f"/missions/{mission_id}/synthese/globale/generate")
            assert reponse.status_code == 200, reponse.text
        return 1

    monkeypatch.setattr(synthese_router, "_total_answer_count", _avec_concurrent)

    reponse = client.post(f"/missions/{mission_id}/synthese/globale/generate")

    assert reponse.status_code == 200, reponse.text
    assert concurrent_lance[0], "le concurrent n'a jamais été déclenché"
    assert lancements == [mission_id], (
        "double lancement : l'acquittement de la perdante a écrasé le "
        "'running' de la gagnante avec sa propre valeur périmée"
    )

    with SessionLocal() as db:
        mission = db.get(Mission, mission_id)
        assert mission.global_synthesis.generation_status == "running", (
            "le perdant de la course doit rendre l'etat reel, pas 'idle'/'error'"
        )
