"""Executive summary (piste F restitution, 2026-07-21) : modèle, génération
(mockée, aucun appel réseau), routes generate/autosave, et intégration à l'export
PPT (slide d'ouverture « so what » + bande key message). Calqué sur test_swot.py :
DB jetable, IA monkeypatchée."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import GlobalSynthesis, Interview, Mission, MissionExecutiveSummary
from app.services import synthese_ai
from app.services.pptx_export import build_presentation


def setup_module() -> None:
    # engine.dispose() AVANT unlink : libère le pool de connexions du fichier de
    # test précédent, sinon DB_PATH.unlink() échoue sur un verrou Windows selon
    # l'ordre de collecte (cf. feedback-pytest-db-unlink-needs-engine-dispose).
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def teardown_module() -> None:
    # Libère le pool AVANT de rendre la main : sans ça, le fichier de test suivant
    # (ordre de collecte) échoue à unlink la DB sur un verrou Windows si son propre
    # setup ne dispose pas (cf. feedback-pytest-db-unlink-needs-engine-dispose).
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


_FAKE_ES = {
    "headline": "La situation est bloquée : il faut changer le cadrage.",
    "points": "- Peu de leviers d'amélioration disponibles\n- Cadre contractuel rigide",
    "key_message": "Une décision business ET technique est à prendre à moyen terme.",
}


def _mission_with_global_synthesis(name: str = "Mission ES") -> int:
    db = SessionLocal()
    try:
        mission = Mission(name=name)
        db.add(mission)
        db.flush()
        db.add(Interview(mission_id=mission.id, interviewee_name="Témoin", status="done"))
        db.add(
            GlobalSynthesis(
                mission_id=mission.id, status="generated",
                contexte="- Contexte", culture_adn="- Culture",
                forces_succes="- Bonne collaboration",
                points_amelioration="- Silos entre équipes",
                aspirations="- Plus d'autonomie",
            )
        )
        db.commit()
        return mission.id
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Service — génération dérivée de la synthèse globale (un seul appel, mocké).
# --------------------------------------------------------------------------- #
def test_generate_executive_summary_returns_three_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(synthese_ai, "_call_claude", lambda *a, **k: dict(_FAKE_ES))
    gs = GlobalSynthesis(forces_succes="x", points_amelioration="y", aspirations="z")
    result = synthese_ai.generate_executive_summary(gs)
    assert set(result) == {"headline", "points", "key_message"}
    assert result["headline"] == "La situation est bloquée : il faut changer le cadrage."


def test_clean_executive_summary_flattens_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Même défense que la SWOT contre les types inattendus d'Ollama (cf.
    feedback-ollama-json-type-coercion-flatten-not-drop) : `headline`/`key_message`
    renvoyés en LISTE sont aplatis en UNE ligne (sans marqueur de puce), `points`
    reste des puces — jamais jeter."""
    monkeypatch.setattr(
        synthese_ai, "_call_claude",
        lambda *a, **k: {
            "headline": ["Constat", "en deux morceaux"],
            "points": [{"poids": "Point A"}, "Point B"],
            "key_message": ["Décider vite"],
        },
    )
    result = synthese_ai.generate_executive_summary(GlobalSynthesis())
    assert result["headline"] == "Constat en deux morceaux"   # liste -> une ligne
    assert result["points"] == "- Point A\n- Point B"          # dict+str -> puces
    assert result["key_message"] == "Décider vite"             # liste 1 élément, sans « - »


def test_clean_executive_summary_drops_scalars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un scalaire non textuel reste traité comme absent, sans planter (comme la SWOT)."""
    monkeypatch.setattr(
        synthese_ai, "_call_claude",
        lambda *a, **k: {"headline": 42, "points": "", "key_message": None},
    )
    result = synthese_ai.generate_executive_summary(GlobalSynthesis())
    assert result["headline"] == ""
    assert result["key_message"] == ""


def test_generate_demo_executive_summary_reprojects_synthesis() -> None:
    gs = GlobalSynthesis(
        forces_succes="- Collaboration", points_amelioration="- Silos", aspirations="- Autonomie",
    )
    result = synthese_ai.generate_demo_executive_summary(gs)
    assert set(result) == {"headline", "points", "key_message"}
    assert "Collaboration" in result["points"] and "Silos" in result["points"]


# --------------------------------------------------------------------------- #
# Routes — génération (dérive de la synthèse globale) + autosave par champ.
# --------------------------------------------------------------------------- #
def test_generate_es_route_saves_and_renders(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _mission_with_global_synthesis()
    monkeypatch.setattr("app.routers.export.is_configured", lambda: True)
    monkeypatch.setattr("app.routers.export.generate_executive_summary", lambda gs: dict(_FAKE_ES))

    response = client.post(f"/missions/{mission_id}/executive-summary/generate")
    assert response.status_code == 200
    assert "changer le cadrage" in response.text

    db = SessionLocal()
    try:
        es = db.scalars(
            select(MissionExecutiveSummary).where(MissionExecutiveSummary.mission_id == mission_id)
        ).one()
        assert es.status == "generated"
        assert "bloquée" in es.headline
    finally:
        db.close()


def test_generate_es_requires_global_synthesis_first(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.routers.export.is_configured", lambda: True)
    db = SessionLocal()
    try:
        mission = Mission(name="ES sans synthèse")
        db.add(mission)
        db.flush()
        db.add(Interview(mission_id=mission.id, interviewee_name="Témoin", status="done"))
        db.commit()
        mission_id = mission.id
    finally:
        db.close()

    response = client.post(f"/missions/{mission_id}/executive-summary/generate")
    assert response.status_code == 200
    # Sans apostrophe : Jinja échappe « l'executive » en « l&#39;executive ».
    assert "executive summary en" in response.text


def test_es_field_autosave_sets_edited(client: TestClient) -> None:
    mission_id = _mission_with_global_synthesis("Mission ES autosave")
    response = client.post(
        f"/executive-summary/{mission_id}/field",
        data={"field": "key_message", "value": "- Décider vite"},
    )
    assert response.status_code == 200

    db = SessionLocal()
    try:
        es = db.scalars(
            select(MissionExecutiveSummary).where(MissionExecutiveSummary.mission_id == mission_id)
        ).one()
        assert es.key_message == "- Décider vite"
        assert es.status == "edited"
    finally:
        db.close()


def test_es_field_rejects_unknown_field(client: TestClient) -> None:
    mission_id = _mission_with_global_synthesis("Mission ES bad field")
    response = client.post(
        f"/executive-summary/{mission_id}/field", data={"field": "inconnu", "value": "x"}
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Export PPT — la slide d'ouverture est incluse par défaut, exclue par le toggle.
# --------------------------------------------------------------------------- #
def _all_slide_text(prs) -> str:
    return "\n".join(
        sh.text_frame.text
        for slide in prs.slides
        for sh in slide.shapes
        if sh.has_text_frame
    )


def _seed_es(mission_id: int) -> None:
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        mission.executive_summary = MissionExecutiveSummary(
            mission_id=mission_id, status="generated", **_FAKE_ES
        )
        db.commit()
    finally:
        db.close()


def test_build_presentation_includes_executive_summary_slide() -> None:
    mission_id = _mission_with_global_synthesis("Mission ES export")
    _seed_es(mission_id)
    db = SessionLocal()
    try:
        prs = build_presentation(db.get(Mission, mission_id))
    finally:
        db.close()
    text = _all_slide_text(prs)
    assert "Executive Summary" in text
    assert "changer le cadrage" in text


def test_build_presentation_excludes_executive_summary_when_toggled_off() -> None:
    mission_id = _mission_with_global_synthesis("Mission ES off")
    _seed_es(mission_id)
    db = SessionLocal()
    try:
        prs = build_presentation(
            db.get(Mission, mission_id), include_executive_summary=False
        )
    finally:
        db.close()
    assert "changer le cadrage" not in _all_slide_text(prs)
