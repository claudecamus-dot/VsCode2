"""SWOT (Palier 1 restitution, 2026-07-21) : modèle, génération (mockée, aucun
appel réseau), routes generate/autosave, et intégration à l'export PPT (slide
2×2). Même conventions que le reste de la suite : DB jetable, IA monkeypatchée."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, init_db
from app.main import app
from app.models import GlobalSynthesis, Interview, Mission, MissionSwot
from app.services import synthese_ai
from app.services.pptx_export import build_presentation


def setup_module() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


_FAKE_SWOT = {
    "forces": "- Force de test",
    "faiblesses": "- Faiblesse de test",
    "opportunites": "- Opportunité externe de test",
    "menaces": "- Menace externe de test",
}


def _mission_with_global_synthesis(name: str = "Mission SWOT") -> int:
    """Crée une mission + un entretien (l'aperçu se ferme sur une mission sans
    entretien, cf. apercu.html) + une synthèse globale avec du contenu (la SWOT
    en découle) et renvoie l'id de la mission."""
    db = SessionLocal()
    try:
        mission = Mission(name=name)
        db.add(mission)
        db.flush()
        db.add(Interview(mission_id=mission.id, interviewee_name="Témoin", status="done"))
        db.add(
            GlobalSynthesis(
                mission_id=mission.id,
                status="generated",
                contexte="- Contexte",
                culture_adn="- Culture",
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
def test_generate_swot_returns_four_quadrants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(synthese_ai, "_call_claude", lambda *a, **k: dict(_FAKE_SWOT))
    gs = GlobalSynthesis(forces_succes="x", points_amelioration="y", aspirations="z")
    result = synthese_ai.generate_swot(gs)
    assert set(result) == {"forces", "faiblesses", "opportunites", "menaces"}
    assert result["opportunites"] == "- Opportunité externe de test"


def test_clean_swot_flattens_ollama_list_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Régression du bug du 2026-07-21 (trouvé par un passage RÉEL contre
    Ollama, jamais par les tests mockés) : `format: "json"` garantit du JSON
    valide, pas le type du schéma — llama3.1 renvoie une LISTE de petits objets
    `{"poids": "..."}` par quadrant là où une chaîne était attendue. L'ancienne
    garde stricte (str sinon "") ressortait les 4 quadrants VIDES malgré une
    génération pertinente ; on doit désormais aplatir en puces, pas jeter."""
    monkeypatch.setattr(
        synthese_ai, "_call_claude",
        lambda *a, **k: {
            "forces": [{"poids": "Équipes engagées"}, {"poids": "Socle cloud"}],
            "faiblesses": ["Silos métier/IT", "Dette technique"],
            "opportunites": [{"poids": "Croissance de l'IA"}],
            "menaces": [{"poids": "Concurrence accrue"}],
        },
    )
    result = synthese_ai.generate_swot(GlobalSynthesis())
    assert result["forces"] == "- Équipes engagées\n- Socle cloud"
    assert result["faiblesses"] == "- Silos métier/IT\n- Dette technique"
    assert result["opportunites"] == "- Croissance de l'IA"   # liste 1 élément
    assert result["menaces"] == "- Concurrence accrue"


def test_clean_swot_keeps_plain_string_and_drops_scalars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une chaîne déjà propre est conservée telle quelle (pas de re-puçage) ;
    un scalaire non textuel (int) reste traité comme absent, sans planter."""
    monkeypatch.setattr(
        synthese_ai, "_call_claude",
        lambda *a, **k: {"forces": 42, "faiblesses": "- ok\n- deux", "opportunites": "", "menaces": None},
    )
    result = synthese_ai.generate_swot(GlobalSynthesis())
    assert result["forces"] == ""              # int -> vide, pas de crash
    assert result["faiblesses"] == "- ok\n- deux"   # chaîne conservée à l'identique
    assert result["menaces"] == ""             # None -> vide


def test_generate_demo_swot_reprojects_synthesis() -> None:
    gs = GlobalSynthesis(
        forces_succes="- Collaboration",
        points_amelioration="- Silos",
        aspirations="- Autonomie",
    )
    result = synthese_ai.generate_demo_swot(gs)
    assert "Collaboration" in result["forces"]
    assert "Silos" in result["faiblesses"]
    assert set(result) == {"forces", "faiblesses", "opportunites", "menaces"}


# --------------------------------------------------------------------------- #
# Routes — génération (dérive de la synthèse globale) + autosave par quadrant.
# --------------------------------------------------------------------------- #
def test_generate_swot_route_saves_and_renders(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _mission_with_global_synthesis()
    monkeypatch.setattr("app.routers.export.is_configured", lambda: True)
    monkeypatch.setattr("app.routers.export.generate_swot", lambda gs: dict(_FAKE_SWOT))

    response = client.post(f"/missions/{mission_id}/swot/generate")
    assert response.status_code == 200
    assert "Force de test" in response.text

    db = SessionLocal()
    try:
        swot = db.scalars(
            select(MissionSwot).where(MissionSwot.mission_id == mission_id)
        ).one()
        assert swot.opportunites == "- Opportunité externe de test"
        assert swot.status == "generated"
    finally:
        db.close()


def test_generate_swot_requires_global_synthesis_first(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # IA disponible : la route vérifie is_configured() AVANT la pré-condition
    # synthèse globale — sans ce patch, on tomberait sur « Service IA
    # indisponible » et jamais sur le message testé ici.
    monkeypatch.setattr("app.routers.export.is_configured", lambda: True)
    db = SessionLocal()
    try:
        mission = Mission(name="SWOT sans synthèse")
        db.add(mission)
        db.flush()
        # Un entretien pour que l'aperçu rende son contenu (et donc la bannière
        # d'erreur) plutôt que l'état vide « aucun entretien ».
        db.add(Interview(mission_id=mission.id, interviewee_name="Témoin", status="done"))
        db.commit()
        mission_id = mission.id
    finally:
        db.close()

    response = client.post(f"/missions/{mission_id}/swot/generate")
    assert response.status_code == 200
    # Tail sans apostrophe : Jinja échappe « d'abord » en « d&#39;abord », un
    # substring avec apostrophe littérale ne matcherait pas le HTML rendu.
    assert "la SWOT en découle" in response.text


def test_swot_field_autosave_sets_edited(client: TestClient) -> None:
    mission_id = _mission_with_global_synthesis("Mission SWOT autosave")
    response = client.post(
        f"/swot/{mission_id}/field",
        data={"field": "menaces", "value": "- Concurrence agressive"},
    )
    assert response.status_code == 200

    db = SessionLocal()
    try:
        swot = db.scalars(
            select(MissionSwot).where(MissionSwot.mission_id == mission_id)
        ).one()
        assert swot.menaces == "- Concurrence agressive"
        assert swot.status == "edited"
    finally:
        db.close()


def test_swot_field_rejects_unknown_field(client: TestClient) -> None:
    mission_id = _mission_with_global_synthesis("Mission SWOT bad field")
    response = client.post(
        f"/swot/{mission_id}/field", data={"field": "inconnu", "value": "x"}
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Export PPT — la slide SWOT est incluse par défaut, exclue par le toggle.
# --------------------------------------------------------------------------- #
def _all_slide_text(prs) -> str:
    parts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
    return "\n".join(parts)


def _seed_swot(mission_id: int) -> None:
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        mission.swot = MissionSwot(mission_id=mission_id, status="generated", **_FAKE_SWOT)
        db.commit()
    finally:
        db.close()


def test_build_presentation_includes_swot_slide() -> None:
    mission_id = _mission_with_global_synthesis("Mission SWOT export")
    _seed_swot(mission_id)
    db = SessionLocal()
    try:
        prs = build_presentation(db.get(Mission, mission_id))
    finally:
        db.close()
    text = _all_slide_text(prs)
    assert "Matrice SWOT" in text
    assert "Force de test" in text and "Menace externe de test" in text


def test_build_presentation_excludes_swot_when_toggled_off() -> None:
    mission_id = _mission_with_global_synthesis("Mission SWOT off")
    _seed_swot(mission_id)
    db = SessionLocal()
    try:
        prs = build_presentation(db.get(Mission, mission_id), include_swot=False)
    finally:
        db.close()
    assert "Matrice SWOT" not in _all_slide_text(prs)
