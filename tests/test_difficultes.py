"""Difficultés (planche §D.1, restitution) : modèle, génération (mockée), routes
generate / autosave label / liaison verbatim, et intégration à l'export PPT.
Calqué sur test_swot.py / test_executive_summary.py : DB jetable, IA mockée."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import (
    GlobalSynthesis,
    Interview,
    Mission,
    MissionDifficulty,
    Question,
    Theme,
    Trame,
    Verbatim,
)
from app.services import synthese_ai
from app.services.pptx_export import build_presentation


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


def _mission_with_gs(name: str = "Mission Diff") -> int:
    db = SessionLocal()
    try:
        m = Mission(name=name)
        db.add(m)
        db.flush()
        db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
        db.add(GlobalSynthesis(
            mission_id=m.id, status="generated",
            contexte="- Contexte", culture_adn="- Culture", forces_succes="- Force",
            points_amelioration="- Silos entre équipes\n- Dette technique",
            aspirations="- Autonomie",
        ))
        db.commit()
        return m.id
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Service — liste ordonnée dérivée de la synthèse (un appel, mocké).
# --------------------------------------------------------------------------- #
def test_generate_difficulties_returns_ordered_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        synthese_ai, "_call_claude",
        lambda *a, **k: {"difficultes": ["Silos entre équipes", "Dette technique"]},
    )
    result = synthese_ai.generate_difficulties(GlobalSynthesis(points_amelioration="x"))
    assert result == ["Silos entre équipes", "Dette technique"]


def test_clean_difficulties_flattens_ollama_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """Même défense Ollama que la SWOT (cf. feedback-ollama-json-type-coercion) :
    une difficulté renvoyée en dict/liste est APLATIE en une ligne, les vides et
    scalaires non textuels sont ignorés — jamais de crash."""
    monkeypatch.setattr(
        synthese_ai, "_call_claude",
        lambda *a, **k: {"difficultes": [{"label": "Silos"}, ["Dette", "technique"], "", 42]},
    )
    result = synthese_ai.generate_difficulties(GlobalSynthesis())
    assert result == ["Silos", "Dette technique"]  # dict+liste aplatis, '' et int -> droppés


def test_generate_demo_difficulties_reprojects_points() -> None:
    result = synthese_ai.generate_demo_difficulties(
        GlobalSynthesis(points_amelioration="- Silos\n- Dette")
    )
    assert result == ["Silos", "Dette"]


# --------------------------------------------------------------------------- #
# Routes — génération (dérive de la synthèse) + autosave label + liaison verbatim.
# --------------------------------------------------------------------------- #
def test_generate_difficulties_route_creates_ordered_rows(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mid = _mission_with_gs()
    monkeypatch.setattr("app.routers.export.is_configured", lambda: True)
    monkeypatch.setattr(
        "app.routers.export.generate_difficulties", lambda gs: ["Difficulté A", "Difficulté B"]
    )
    resp = client.post(f"/missions/{mid}/difficultes/generate")
    assert resp.status_code == 200
    assert "Difficulté A" in resp.text

    db = SessionLocal()
    try:
        diffs = db.scalars(
            select(MissionDifficulty)
            .where(MissionDifficulty.mission_id == mid)
            .order_by(MissionDifficulty.position)
        ).all()
        assert [d.label for d in diffs] == ["Difficulté A", "Difficulté B"]
        assert [d.position for d in diffs] == [0, 1]
    finally:
        db.close()


def test_generate_difficulties_replaces_previous(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Régénérer remplace la liste (les anciennes lignes ne s'accumulent pas)."""
    mid = _mission_with_gs("Diff regen")
    monkeypatch.setattr("app.routers.export.is_configured", lambda: True)
    monkeypatch.setattr("app.routers.export.generate_difficulties", lambda gs: ["A", "B", "C"])
    client.post(f"/missions/{mid}/difficultes/generate")
    monkeypatch.setattr("app.routers.export.generate_difficulties", lambda gs: ["X"])
    client.post(f"/missions/{mid}/difficultes/generate")
    db = SessionLocal()
    try:
        labels = [d.label for d in db.scalars(
            select(MissionDifficulty).where(MissionDifficulty.mission_id == mid)
        ).all()]
        assert labels == ["X"]
    finally:
        db.close()


def test_generate_difficulties_requires_global_synthesis(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.routers.export.is_configured", lambda: True)
    db = SessionLocal()
    try:
        m = Mission(name="Diff sans synthèse")
        db.add(m)
        db.flush()
        db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
        db.commit()
        mid = m.id
    finally:
        db.close()
    resp = client.post(f"/missions/{mid}/difficultes/generate")
    assert resp.status_code == 200
    assert "difficultés en" in resp.text  # « les difficultés en découlent »


def test_difficulty_label_autosave(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mid = _mission_with_gs("Diff autosave")
    monkeypatch.setattr("app.routers.export.is_configured", lambda: True)
    monkeypatch.setattr("app.routers.export.generate_difficulties", lambda gs: ["À corriger"])
    client.post(f"/missions/{mid}/difficultes/generate")
    db = SessionLocal()
    did = db.scalars(select(MissionDifficulty.id).where(MissionDifficulty.mission_id == mid)).first()
    db.close()

    resp = client.post(f"/difficultes/{did}/field", data={"value": "Difficulté corrigée"})
    assert resp.status_code == 200
    db = SessionLocal()
    try:
        assert db.get(MissionDifficulty, did).label == "Difficulté corrigée"
    finally:
        db.close()


def test_difficulty_field_unknown_404(client: TestClient) -> None:
    resp = client.post("/difficultes/999999/field", data={"value": "x"})
    assert resp.status_code == 404


def test_difficulty_verbatim_link_and_reject_foreign(client: TestClient) -> None:
    db = SessionLocal()
    try:
        m = Mission(name="Diff verbatim")
        db.add(m)
        db.flush()
        trame = Trame(mission_id=m.id)
        db.add(trame)
        db.flush()
        theme = Theme(trame_id=trame.id, title="Thème", position=0)
        db.add(theme)
        db.flush()
        q = Question(theme_id=theme.id, label="Q", qtype="open", position=0)
        db.add(q)
        db.flush()
        iv = Interview(mission_id=m.id, interviewee_name="Témoin", status="done")
        db.add(iv)
        db.flush()
        v = Verbatim(interview_id=iv.id, question_id=q.id, quote="Une citation représentative.")
        db.add(v)
        d = MissionDifficulty(mission_id=m.id, position=0, label="Difficulté")
        db.add(d)
        db.flush()
        vid, did = v.id, d.id
        db.commit()
    finally:
        db.close()

    resp = client.post(f"/difficultes/{did}/verbatim", data={"verbatim_id": str(vid)})
    assert resp.status_code == 200
    db = SessionLocal()
    try:
        assert db.get(MissionDifficulty, did).verbatim_id == vid
    finally:
        db.close()

    # Verbatim inexistant / d'une autre mission -> 400 (jamais lier un verbatim étranger).
    resp = client.post(f"/difficultes/{did}/verbatim", data={"verbatim_id": "999999"})
    assert resp.status_code == 400

    # Délier (vide) -> None.
    resp = client.post(f"/difficultes/{did}/verbatim", data={"verbatim_id": ""})
    assert resp.status_code == 200
    db = SessionLocal()
    try:
        assert db.get(MissionDifficulty, did).verbatim_id is None
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Export PPT — la planche est incluse par défaut, exclue par le toggle.
# --------------------------------------------------------------------------- #
def _all_slide_text(prs) -> str:
    return "\n".join(
        sh.text_frame.text for s in prs.slides for sh in s.shapes if sh.has_text_frame
    )


def test_build_presentation_includes_difficultes_slide() -> None:
    mid = _mission_with_gs("Diff export")
    db = SessionLocal()
    try:
        m = db.get(Mission, mid)
        m.difficulties.append(MissionDifficulty(mission_id=mid, position=0, label="Silos entre équipes"))
        db.commit()
        prs = build_presentation(db.get(Mission, mid))
    finally:
        db.close()
    txt = _all_slide_text(prs)
    assert "Difficultés identifiées" in txt
    assert "Silos entre équipes" in txt


def test_build_presentation_excludes_difficultes_when_toggled_off() -> None:
    mid = _mission_with_gs("Diff off")
    db = SessionLocal()
    try:
        m = db.get(Mission, mid)
        m.difficulties.append(MissionDifficulty(mission_id=mid, position=0, label="Silos"))
        db.commit()
        prs = build_presentation(db.get(Mission, mid), include_difficultes=False)
    finally:
        db.close()
    assert "Difficultés identifiées" not in _all_slide_text(prs)


def test_build_presentation_numbers_only_nonblank_contiguously() -> None:
    """Une difficulté à libellé vide est filtrée ; les restantes sont numérotées
    de façon CONTIGUË (1, 2), jamais avec un trou (1, 3) — c'est la référence de
    parité que l'aperçu doit suivre (cf. test template ci-dessous)."""
    mid = _mission_with_gs("Diff ppt num")
    db = SessionLocal()
    try:
        m = db.get(Mission, mid)
        m.difficulties = [
            MissionDifficulty(position=0, label="Alpha"),
            MissionDifficulty(position=1, label="   "),  # vide -> filtrée
            MissionDifficulty(position=2, label="Gamma"),
        ]
        db.commit()
        prs = build_presentation(db.get(Mission, mid))
    finally:
        db.close()
    txt = _all_slide_text(prs)
    assert "1.  Alpha" in txt
    assert "2.  Gamma" in txt  # Gamma = n°2 (la vide n'occupe pas de rang), pas n°3
    assert "3.  " not in txt


# --------------------------------------------------------------------------- #
# Correctifs revue bmad-code-review — parité aperçu/PPT + robustesse Ollama.
# --------------------------------------------------------------------------- #
def test_clean_difficulties_accepts_english_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repli clé anglaise : un modèle local qui répond {"difficulties": [...]} (EN)
    au lieu de {"difficultes": [...]} ne doit PAS donner une liste vide silencieuse."""
    monkeypatch.setattr(
        synthese_ai, "_call_claude",
        lambda *a, **k: {"difficulties": ["Silos", "Dette"]},
    )
    assert synthese_ai.generate_difficulties(GlobalSynthesis()) == ["Silos", "Dette"]


def test_generate_empty_result_keeps_existing_and_shows_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une régénération qui ne produit rien (modèle muet / clé inattendue) ne doit
    JAMAIS écraser une liste affinée + ses liens citation — on garde, on signale."""
    mid = _mission_with_gs("Diff no wipe")
    monkeypatch.setattr("app.routers.export.is_configured", lambda: True)
    monkeypatch.setattr("app.routers.export.generate_difficulties", lambda gs: ["A garder", "B garder"])
    client.post(f"/missions/{mid}/difficultes/generate")

    monkeypatch.setattr("app.routers.export.generate_difficulties", lambda gs: [])
    resp = client.post(f"/missions/{mid}/difficultes/generate")
    assert resp.status_code == 200
    assert "aucune difficulté" in resp.text.lower()  # message d'erreur affiché
    db = SessionLocal()
    try:
        labels = [d.label for d in db.scalars(
            select(MissionDifficulty)
            .where(MissionDifficulty.mission_id == mid)
            .order_by(MissionDifficulty.position)
        ).all()]
        assert labels == ["A garder", "B garder"]  # inchangé, pas effacé
    finally:
        db.close()


def test_apercu_blank_label_excluded_from_count_and_sommaire(client: TestClient) -> None:
    """Parité aperçu/PPT : une difficulté à libellé vide est comptée comme le PPT la
    rend (filtrée) — la case affiche (1), pas (2)."""
    mid = _mission_with_gs("Diff parity count")
    db = SessionLocal()
    try:
        m = db.get(Mission, mid)
        m.difficulties = [
            MissionDifficulty(position=0, label="Silos entre équipes"),
            MissionDifficulty(position=1, label="   "),  # vide -> non comptée
        ]
        db.commit()
    finally:
        db.close()
    resp = client.get(f"/missions/{mid}/synthese/apercu")
    assert resp.status_code == 200
    assert "Difficultés (1)" in resp.text
    assert "Difficultés (2)" not in resp.text
    assert "Silos entre équipes" in resp.text


def test_apercu_numbering_contiguous_with_blank_interleaved(client: TestClient) -> None:
    """Numérotation de l'aperçu = celle du PPT : une vide intercalée ne crée pas de
    trou (1, 2 et non 1, 3) — `loop.index` sur la liste filtrée, comme enumerate()."""
    mid = _mission_with_gs("Diff parity num")
    db = SessionLocal()
    try:
        m = db.get(Mission, mid)
        m.difficulties = [
            MissionDifficulty(position=0, label="Première difficulté"),
            MissionDifficulty(position=1, label=""),  # vide intercalée
            MissionDifficulty(position=2, label="Troisième difficulté"),
        ]
        db.commit()
    finally:
        db.close()
    resp = client.get(f"/missions/{mid}/synthese/apercu")
    assert resp.status_code == 200
    assert 'difficulty-card-label">1.&nbsp;' in resp.text
    assert 'difficulty-card-label">2.&nbsp;' in resp.text
    assert 'difficulty-card-label">3.&nbsp;' not in resp.text  # pas de trou de rang
