import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import Question, Theme
from app.services.openhub_agents import invoke_skill
from sqlalchemy import select


def setup_module() -> None:
    # Ensure test DB is isolated and fresh.
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


def test_create_mission_and_view_trame(client: TestClient) -> None:
    response = client.post(
        "/missions", data={
            "name": "Mission Test",
            "client": "Client Test",
            "description": "Description de test",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/missions/" in response.headers["location"]

    mission_url = response.headers["location"]
    mission_id = mission_url.rsplit("/", 1)[-1]
    assert mission_id.isdigit()

    response = client.get(f"/missions/{mission_id}/trame")
    assert response.status_code == 200
    assert "Trame d'entretien" in response.text
    assert "Mission Test" in response.text
    assert "/missions/{mission_id}/trame/themes" in response.text or "Ajouter un thème" in response.text


def test_add_theme_and_question(client: TestClient) -> None:
    response = client.post(
        "/missions", data={
            "name": "Mission Test 2",
            "client": "Client Test 2",
            "description": "Description de test 2",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    mission_id = response.headers["location"].rsplit("/", 1)[-1]

    response = client.post(
        f"/missions/{mission_id}/trame/themes",
        data={"title": "Thème Test"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    session = SessionLocal()
    try:
        theme = session.scalars(
            select(Theme).where(Theme.title == "Thème Test")
        ).one()
    finally:
        session.close()

    response = client.post(
        f"/missions/{mission_id}/trame/themes/{theme.id}/questions",
        data={
            "label": "Question de test ?",
            "qtype": "open",
            "scale_min": "1",
            "scale_max": "5",
            "choices": "",
            "help_text": "Texte d'aide",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = client.get(f"/missions/{mission_id}/trame")
    assert response.status_code == 200
    assert "Thème Test" in response.text
    assert "Question de test ?" in response.text
    assert "Texte d'aide" in response.text


def test_import_docx_creates_theme_and_questions(client: TestClient) -> None:
    response = client.post(
        "/missions",
        data={
            "name": "Mission DOCX",
            "client": "Client DOCX",
            "description": "Description DOCX",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    mission_id = response.headers["location"].rsplit("/", 1)[-1]

    # Build a simple .docx file containing one theme and two questions.
    from docx import Document
    import io

    document = Document()
    document.add_heading("Objectifs et principes", level=1)
    document.add_paragraph("Introduction DOCX")
    document.add_heading("Thème importé", level=1)
    document.add_paragraph("Question A ?")
    document.add_paragraph("Question B ?")

    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)

    response = client.post(
        f"/missions/{mission_id}/trame/import",
        files={"file": ("import.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/missions/{mission_id}/trame/preview")

    response = client.get(f"/missions/{mission_id}/trame")
    assert response.status_code == 200
    assert "Thème importé" in response.text
    assert "Question A ?" in response.text
    assert "Question B ?" in response.text


def test_agents_page_lists_available_skills(client: TestClient) -> None:
    response = client.post(
        "/missions",
        data={
            "name": "Mission Skills",
            "client": "Client Skills",
            "description": "Description Skills",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    mission_id = response.headers["location"].rsplit("/", 1)[-1]

    response = client.get(f"/missions/{mission_id}/agents")
    assert response.status_code == 200
    assert "Skills disponibles" in response.text
    assert "beads-dev" in response.text or "Workflow exécuteur Beads" in response.text


def test_skill_invocation_from_agents_page(client: TestClient) -> None:
    response = client.post(
        "/missions",
        data={
            "name": "Mission Skill Invoke",
            "client": "Client Skill Invoke",
            "description": "Description Skill Invoke",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    mission_id = response.headers["location"].rsplit("/", 1)[-1]

    response = client.post(
        f"/missions/{mission_id}/skills/beads-dev/invoke",
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Skill OpenHub" in response.text
    assert "beads-dev" in response.text


def test_dynamic_skill_execution_uses_opencode_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    python_path = tmp_path / "opencode.py"
    python_path.write_text(
        "import sys\n"
        "print('skill-ok')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    wrapper_path = tmp_path / "opencode.cmd"
    wrapper_path.write_text(
        "@echo off\n"
        f"python \"{python_path}\" %*\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")

    mission = type("Mission", (), {"name": "Mission", "client": "Client", "description": "Desc", "trame": None, "interviews": []})()
    result = invoke_skill("beads-dev", mission)

    assert result["runtime_available"] is True
    assert "Skill OpenHub" in result["output"]
    assert "skill-ok" in result["output"]


def test_interview_capture_and_save_answer(client: TestClient) -> None:
    response = client.post(
        "/missions",
        data={
            "name": "Mission Interview",
            "client": "Client Interview",
            "description": "Description Interview",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    mission_id = response.headers["location"].rsplit("/", 1)[-1]

    response = client.post(
        f"/missions/{mission_id}/trame/themes",
        data={"title": "Interview Thème"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    session = SessionLocal()
    try:
        theme = session.scalars(
            select(Theme).where(Theme.title == "Interview Thème")
        ).one()
    finally:
        session.close()

    response = client.post(
        f"/missions/{mission_id}/trame/themes/{theme.id}/questions",
        data={
            "label": "Question interview ?",
            "qtype": "open",
            "scale_min": "1",
            "scale_max": "5",
            "choices": "",
            "help_text": "Note interview",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    # Créer un entretien et vérifier la saisie d'une réponse.
    response = client.post(
        f"/missions/{mission_id}/interviews",
        data={
            "interviewee_name": "Jean Dupont",
            "interviewee_role": "Responsable",
            "interviewee_entity": "Produit",
            "interview_date": "2026-06-30",
            "reference_text": "Référence d'entretien",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    interview_id = response.headers["location"].rsplit("/", 1)[-1]

    session = SessionLocal()
    try:
        question = session.scalars(
            select(Question).where(
                Question.label == "Question interview ?",
                Question.theme_id == theme.id,
            )
        ).one()
    finally:
        session.close()

    response = client.post(
        f"/interviews/{interview_id}/answers/{question.id}",
        data={"text": "Réponse test"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Répondue" in response.text or "enr." in response.text

    response = client.get(f"/interviews/{interview_id}")
    assert response.status_code == 200
    assert "Question interview ?" in response.text
    assert "Réponse test" in response.text
