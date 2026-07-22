"""Seed de démonstration (P5a-2) : la mission démo est complète, taguée is_demo,
et le seed est rejouable (idempotent). DB jetable (engine.dispose avant unlink —
verrou Windows, cf. feedback-pytest-db-unlink-needs-engine-dispose)."""
from __future__ import annotations

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import Mission
from app.services.pptx_export import build_presentation
from scripts.seed_demo import DEMO_NAME, seed


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


def test_seed_creates_complete_demo_mission() -> None:
    seed()
    db = SessionLocal()
    try:
        demo = db.query(Mission).filter(Mission.is_demo.is_(True)).all()
        assert len(demo) == 1
        m = demo[0]
        assert m.name == DEMO_NAME and m.is_demo is True and m.is_draft is False
        assert len(m.interviews) == 10
        assert len(m.all_verbatims) >= 10
        assert len(m.selected_verbatims) == 4
        assert m.global_synthesis is not None and m.global_synthesis.has_content
        assert m.swot is not None and m.swot.has_content
        assert m.executive_summary is not None and m.executive_summary.has_content
        assert len(m.difficulties) == 3
        assert len(m.recommendation_axes) == 2
        # Deck générable de bout en bout sur ce jeu (geometry check inclus).
        prs = build_presentation(m)
        assert sum(1 for _ in prs.slides) > 10
    finally:
        db.close()


def test_seed_is_idempotent_and_never_touches_real() -> None:
    db = SessionLocal()
    try:
        db.add(Mission(name="Vraie mission", is_demo=False))
        db.commit()
    finally:
        db.close()
    seed()
    seed()  # rejoué : ne duplique pas la mission démo
    db = SessionLocal()
    try:
        assert db.query(Mission).filter(Mission.is_demo.is_(True)).count() == 1
        # la vraie mission n'est jamais touchée par le seed
        assert db.query(Mission).filter(
            Mission.is_demo.is_(False), Mission.name == "Vraie mission"
        ).count() == 1
    finally:
        db.close()
