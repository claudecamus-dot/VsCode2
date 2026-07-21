"""Verbatims restitués — planche « Paroles d'acteurs » (Palier 2 restitution,
2026-07-21). Approche légère : sélection d'ids de `Verbatim` déjà en base sur
`Mission.restitution_verbatim_ids`, rendue en slide _slide_verbatims. Mêmes
conventions que le reste de la suite (DB jetable, aucun appel réseau)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview, Mission, Question, Theme, Trame, Verbatim
from app.services.pptx_export import build_presentation


def setup_module() -> None:
    # Libère les connexions du pool avant l'unlink : la suite partage un seul
    # engine, et le fichier de test précédent (ex. test_swot, trié juste avant)
    # laisse des connexions ouvertes qui verrouillent DB_PATH sous Windows —
    # sans ce dispose, DB_PATH.unlink() lève PermissionError (8 tests en erreur).
    engine.dispose()
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def teardown_module() -> None:
    # Convention de la suite : disposer l'engine puis nettoyer, pour que le
    # fichier suivant puisse unlink à son tour (cf. test_interview_segment_jobs).
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _mission_with_verbatims(quotes: list[tuple[str, str]], name: str = "Mission Verbatims") -> tuple[int, list[int]]:
    """Crée une mission avec trame/thème/question + un entretien par
    interlocuteur distinct, et un `Verbatim` par (interlocuteur, citation).
    Renvoie (mission_id, [verbatim_ids] dans l'ordre fourni)."""
    db = SessionLocal()
    try:
        mission = Mission(name=name)
        db.add(mission)
        db.flush()
        trame = Trame(mission_id=mission.id)
        db.add(trame)
        db.flush()
        theme = Theme(trame_id=trame.id, title="Thème", position=0)
        db.add(theme)
        db.flush()
        question = Question(theme_id=theme.id, label="Q", qtype="open", position=0)
        db.add(question)
        db.flush()
        interviews: dict[str, int] = {}
        vids: list[int] = []
        for who, quote in quotes:
            if who not in interviews:
                iv = Interview(mission_id=mission.id, interviewee_name=who, status="done")
                db.add(iv)
                db.flush()
                interviews[who] = iv.id
            v = Verbatim(interview_id=interviews[who], question_id=question.id, quote=quote)
            db.add(v)
            db.flush()
            vids.append(v.id)
        db.commit()
        return mission.id, vids
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Modèle — all_verbatims / selected_verbatims (ordre, ids périmés).
# --------------------------------------------------------------------------- #
def test_all_verbatims_gathers_across_interviews() -> None:
    mid, vids = _mission_with_verbatims(
        [("Alice", "citation A"), ("Bob", "citation B"), ("Alice", "citation C")]
    )
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        assert {v.id for v in mission.all_verbatims} == set(vids)
        assert len(mission.all_verbatims) == 3  # 2 entretiens, 3 verbatims
    finally:
        db.close()


def test_selected_verbatims_follows_order_and_ignores_stale_ids() -> None:
    mid, vids = _mission_with_verbatims([("Alice", "A"), ("Bob", "B"), ("Alice", "C")])
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        # Ordre de sélection inversé + un id périmé (999) qui doit être ignoré.
        mission.restitution_verbatim_ids = [vids[2], vids[0], 999]
        db.commit()
        selected = mission.selected_verbatims
        assert [v.id for v in selected] == [vids[2], vids[0]]  # 999 ignoré, ordre gardé
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Route — toggle de sélection (ajout / retrait / id inconnu).
# --------------------------------------------------------------------------- #
def test_toggle_adds_then_removes_verbatim(client: TestClient) -> None:
    mid, vids = _mission_with_verbatims([("Alice", "A"), ("Bob", "B")], "Mission Toggle")

    r = client.post(f"/missions/{mid}/verbatims/toggle", data={"verbatim_id": vids[0], "selected": "true"})
    assert r.status_code == 200
    r = client.post(f"/missions/{mid}/verbatims/toggle", data={"verbatim_id": vids[1], "selected": "true"})
    assert r.status_code == 200

    db = SessionLocal()
    try:
        assert (db.get(Mission, mid).restitution_verbatim_ids or []) == [vids[0], vids[1]]
    finally:
        db.close()

    # Décoche le premier.
    client.post(f"/missions/{mid}/verbatims/toggle", data={"verbatim_id": vids[0], "selected": "false"})
    db = SessionLocal()
    try:
        assert (db.get(Mission, mid).restitution_verbatim_ids or []) == [vids[1]]
    finally:
        db.close()


def test_toggle_ignores_unknown_verbatim_id(client: TestClient) -> None:
    mid, _vids = _mission_with_verbatims([("Alice", "A")], "Mission Toggle Bad")
    r = client.post(f"/missions/{mid}/verbatims/toggle", data={"verbatim_id": 999999, "selected": "true"})
    assert r.status_code == 200  # pas d'erreur
    db = SessionLocal()
    try:
        assert (db.get(Mission, mid).restitution_verbatim_ids or []) == []  # rien ajouté
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Export PPT — planche incluse si des verbatims sont retenus, exclue au toggle.
# --------------------------------------------------------------------------- #
def _all_slide_text(prs) -> str:
    parts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
    return "\n".join(parts)


def test_build_presentation_includes_verbatims_board() -> None:
    mid, vids = _mission_with_verbatims(
        [("Alice", "Un frein réel sur le terrain"), ("Bob", "Une vraie fierté partagée")], "Mission Verb Export"
    )
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        mission.restitution_verbatim_ids = vids
        db.commit()
        prs = build_presentation(db.get(Mission, mid))
    finally:
        db.close()
    text = _all_slide_text(prs)
    assert "Paroles d'acteurs" in text
    assert "Un frein réel sur le terrain" in text and "Alice" in text


def test_build_presentation_excludes_verbatims_when_none_selected() -> None:
    mid, _vids = _mission_with_verbatims([("Alice", "A")], "Mission Verb None")
    db = SessionLocal()
    try:
        prs = build_presentation(db.get(Mission, mid))  # aucune sélection
    finally:
        db.close()
    assert "Paroles d'acteurs" not in _all_slide_text(prs)


def test_build_presentation_excludes_verbatims_when_toggled_off() -> None:
    mid, vids = _mission_with_verbatims([("Alice", "A")], "Mission Verb Off")
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        mission.restitution_verbatim_ids = vids
        db.commit()
        prs = build_presentation(db.get(Mission, mid), include_verbatims=False)
    finally:
        db.close()
    assert "Paroles d'acteurs" not in _all_slide_text(prs)


def test_build_presentation_long_quotes_do_not_overflow() -> None:
    """Beaucoup de citations longues : la planche cape/tronque et build ne lève
    pas (verifier_geometrie en fin de build_presentation garderait sinon)."""
    quotes = [("Témoin %d" % i, "Une citation particulièrement longue " * 8) for i in range(8)]
    mid, vids = _mission_with_verbatims(quotes, "Mission Verb Long")
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        mission.restitution_verbatim_ids = vids
        db.commit()
        prs = build_presentation(db.get(Mission, mid))  # ne doit pas lever
    finally:
        db.close()
    assert "Paroles d'acteurs" in _all_slide_text(prs)
