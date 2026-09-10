"""Course (TOCTOU) sur la CRÉATION des lignes de synthèse — trouvée par la
revue du 2026-09-09 en marge du finding audit-technique robustesse (celui-là
portait sur le double LANCEMENT de `generate_global`, cf.
`test_synthese_globale_concurrence.py`, dont le `_creer_mission` évite
délibérément cette course-ci en pré-créant la ligne).

`get_or_create_global_synthesis`/`get_or_create_swot`/`get_or_create_executive_summary`
partageaient un lire-puis-écrire Python : sur une mission SANS ligne encore,
deux requêtes quasi simultanées lisent toutes deux `mission.<relation> is None`
avant qu'aucune n'ait committé, créent chacune la leur, et la seconde heurte la
contrainte d'unicité `uq_*_mission` — en 500 brut (`IntegrityError` non
attrapée), pas en dégradation propre.

Même technique que l'entrelacement déterministe de
`test_synthese_globale_concurrence.py` (suspendre la requête 1 APRÈS sa
lecture périmée, le temps qu'une requête 2 complète parte et committe) —
appliquée ici aux DEUX sessions du vrai couple concurrent, pas via une
callback interne, pour prouver ce que voit un second appelant qui n'a par
définition aucun crochet dans le premier.

Échec sur le code d'avant : la seconde session lève `sqlalchemy.exc.
IntegrityError` (`UNIQUE constraint failed`) à son `db.commit()`, remontée
telle quelle par `app/main.py` en 500 générique.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import GlobalSynthesis, Mission, MissionExecutiveSummary, MissionSwot
from app.services import synthese_ecriture


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
    db = SessionLocal()
    try:
        mission = Mission(name=nom)
        db.add(mission)
        db.commit()
        return mission.id
    finally:
        db.close()


@pytest.mark.parametrize(
    ("attr", "modele", "fonction"),
    [
        ("global_synthesis", GlobalSynthesis, synthese_ecriture.get_or_create_global_synthesis),
        ("swot", MissionSwot, synthese_ecriture.get_or_create_swot),
        ("executive_summary", MissionExecutiveSummary, synthese_ecriture.get_or_create_executive_summary),
    ],
    ids=["global_synthesis", "swot", "executive_summary"],
)
def test_deux_sessions_concurrentes_sur_une_mission_sans_ligne_n_en_creent_qu_une(
    attr: str, modele: type, fonction,
) -> None:
    mission_id = _creer_mission(f"Mission course {attr}")

    db_a = SessionLocal()
    db_b = SessionLocal()
    mission_a = db_a.get(Mission, mission_id)
    mission_b = db_b.get(Mission, mission_id)
    try:
        # Les deux lisent l'état AVANT que quiconque n'ait rien committé —
        # l'entrelacement exact qui rendait le défaut exploitable.
        assert getattr(mission_a, attr) is None
        assert getattr(mission_b, attr) is None

        gagnant = fonction(db_a, mission_a)
        db_a.commit()

        # La perdante ne doit ni lever, ni committer une seconde ligne : elle
        # doit rendre CELLE du gagnant.
        perdant = fonction(db_b, mission_b)
        db_b.commit()

        assert perdant.id == gagnant.id, (
            "la perdante a créé sa propre ligne au lieu de rendre celle du "
            "gagnant — la contrainte d'unicité aurait explosé son commit()"
        )
    finally:
        db_a.close()
        db_b.close()

    db = SessionLocal()
    try:
        assert db.query(modele).filter_by(mission_id=mission_id).count() == 1
    finally:
        db.close()


def test_le_chemin_solo_sans_course_reste_inchange() -> None:
    """Non-régression : sur une mission qui n'a encore aucune ligne et sans
    aucun concurrent, la création se fait normalement, en une seule session."""
    mission_id = _creer_mission("Mission solo")
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        gs = synthese_ecriture.get_or_create_global_synthesis(db, mission)
        db.commit()
        assert gs.id is not None
        assert gs.mission_id == mission_id
        # Rappelée dans la MÊME session : rend le même objet sans ré-écrire.
        assert synthese_ecriture.get_or_create_global_synthesis(db, mission) is gs
    finally:
        db.close()


def test_deux_appels_concurrents_a_la_route_ne_rendent_ni_500_ni_ligne_en_double(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test de FUMÉE sur la route réelle (`POST /missions/{id}/synthese/globale/
    generate`), sur une mission sans ligne `GlobalSynthesis` au départ : deux
    appels qui se chevauchent rendent 200 et laissent UNE seule ligne.

    Ce qu'il ne prouve PAS, et pourquoi il reste (second passage de revue,
    m5) : le point d'entrelacement disponible (`_total_answer_count`) est
    atteint APRÈS `get_or_create_global_synthesis`, donc la requête 1 a déjà
    créé et commité sa ligne quand la requête 2 part — le chemin `ON CONFLICT`
    n'est pas exercé ici. La vraie couverture de la course de création est le
    test paramétré à deux sessions ci-dessus, qui la reproduit sans passer par
    la route. Celui-ci garde son utilité propre : il prouve que le
    comportement de bout en bout reste sain (pas de 500, pas de doublon) quand
    deux requêtes se croisent sur ce point d'entrée."""
    from app.routers import synthese as synthese_router

    mission_id = _creer_mission("Mission course route")
    monkeypatch.setattr(synthese_router, "is_configured", lambda: True)
    monkeypatch.setattr(synthese_router, "run_global_synthesis_job", lambda mid: None)

    concurrent_lance = [False]

    def _avec_concurrent(material_by_theme):
        if not concurrent_lance[0]:
            concurrent_lance[0] = True
            concurrent = TestClient(app)
            reponse = concurrent.post(f"/missions/{mission_id}/synthese/globale/generate")
            assert reponse.status_code == 200, reponse.text
        return 1

    monkeypatch.setattr(synthese_router, "_total_answer_count", _avec_concurrent)

    reponse = client.post(f"/missions/{mission_id}/synthese/globale/generate")

    assert reponse.status_code == 200, reponse.text
    # Sans cette assertion, un court-circuit de la route AVANT
    # `_total_answer_count` (branche `error`/`running`, réorganisation future)
    # laisserait ce test vert sans que le second appel soit jamais parti.
    assert concurrent_lance[0], "le concurrent n'a jamais été déclenché"

    db = SessionLocal()
    try:
        assert db.query(GlobalSynthesis).filter_by(mission_id=mission_id).count() == 1
    finally:
        db.close()
