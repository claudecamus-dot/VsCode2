"""Tests de l'export PDF d'un entretien (`interview_pdf_export.py` +
`GET /interviews/{id}/export/pdf`) — jusqu'ici non couvert (constat de
l'audit du 2026-07-19). Le texte est extrait du PDF généré via PyMuPDF
(`fitz`, déjà présent dans l'environnement) pour vérifier le contenu réel
rendu, pas seulement l'absence d'exception ou un status 200.

Couvre aussi le fix du rendu multiligne (2026-07-19) : `Paragraph` reportlab
traite le texte comme du mini-HTML et collapse les `\\n` bruts en un seul
espace — une réponse/note/remarque saisie sur plusieurs lignes s'affichait
comme un bloc continu avant la conversion en `<br/>` (`_text()` dans
`interview_pdf_export.py`).
"""
from __future__ import annotations

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import Answer, Interview, InterviewTurn, Mission, Question, Theme, Trame, Verbatim
from app.services.interview_pdf_export import build_interview_pdf


def setup_module() -> None:
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


def _pdf_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


MULTILINE_ANSWER = "Ligne 1 de la réponse.\nLigne 2 de la réponse.\nLigne 3, dernière."


def _build_parametre_interview(multiline: bool = True) -> int:
    """Mission + trame + thème + question + entretien paramétré, avec
    réponse, verbatim et notes libres — retourne l'id de l'entretien."""
    session = SessionLocal()
    try:
        mission = Mission(name="Mission PDF", trame=Trame(name="Trame PDF"))
        session.add(mission)
        session.flush()
        theme = Theme(trame_id=mission.trame.id, title="Organisation", position=0)
        session.add(theme)
        session.flush()
        question = Question(theme_id=theme.id, label="Comment ça se passe ?", qtype="open", position=0)
        session.add(question)
        session.flush()
        interview = Interview(
            mission_id=mission.id, mode="parametre",
            interviewee_name="Jean Dupont", interviewee_role="DSI",
            free_notes="Note ligne A.\nNote ligne B." if multiline else "Note simple.",
        )
        session.add(interview)
        session.flush()
        session.add(Answer(
            interview_id=interview.id, question_id=question.id,
            text=MULTILINE_ANSWER if multiline else "Réponse sur une seule ligne.",
        ))
        session.add(Verbatim(
            interview_id=interview.id, question_id=question.id,
            quote="On adapte tout en continu.",
        ))
        session.commit()
        return interview.id
    finally:
        session.close()


def test_build_interview_pdf_parametre_content_and_multiline_preserved() -> None:
    interview_id = _build_parametre_interview(multiline=True)
    session = SessionLocal()
    try:
        interview = session.get(Interview, interview_id)
        pdf_bytes = build_interview_pdf(interview)
    finally:
        session.close()

    assert pdf_bytes[:4] == b"%PDF"
    text = _pdf_text(pdf_bytes)

    assert "Jean Dupont" in text
    assert "DSI" in text
    assert "Organisation" in text
    assert "Comment ça se passe ?" in text
    assert "On adapte tout en continu." in text

    # Rendu multiligne (2026-07-19) : chaque ligne de la réponse et des notes
    # apparaît comme une ligne distincte de texte extrait, pas fusionnée en un
    # seul bloc continu séparé par des espaces.
    for line in ("Ligne 1 de la réponse.", "Ligne 2 de la réponse.", "Ligne 3, dernière."):
        assert line in text.split("\n")
    assert "Note ligne A." in text.split("\n")
    assert "Note ligne B." in text.split("\n")
    # Preuve négative : si le bug de collapse réapparaît, les 3 lignes de la
    # réponse se retrouvent concaténées avec un simple espace sur une ligne.
    assert "Ligne 1 de la réponse. Ligne 2 de la réponse." not in text


def test_build_interview_pdf_parametre_sans_reponse_ni_trame_ne_plante_pas() -> None:
    session = SessionLocal()
    try:
        mission = Mission(name="Mission PDF Vide")
        session.add(mission)
        session.flush()
        interview = Interview(mission_id=mission.id, mode="parametre", interviewee_name="Sans Trame")
        session.add(interview)
        session.commit()
        interview_id = interview.id
    finally:
        session.close()

    session = SessionLocal()
    try:
        pdf_bytes = build_interview_pdf(session.get(Interview, interview_id))
    finally:
        session.close()
    assert pdf_bytes[:4] == b"%PDF"
    assert "Sans Trame" in _pdf_text(pdf_bytes)


def _build_libre_interview(multiline: bool = True) -> int:
    session = SessionLocal()
    try:
        mission = Mission(name="Mission PDF Libre")
        session.add(mission)
        session.flush()
        interview = Interview(
            mission_id=mission.id, mode="libre",
            interviewee_name="Claire Rousseau",
            resume="Message central de l'entretien.",
            repartition={
                "contexte": "- Contexte détaillé",
                "culture_adn": "",
                "forces_succes": "- Force clé",
                "points_amelioration": "",
                "aspirations": "",
            },
        )
        session.add(interview)
        session.flush()
        session.add(InterviewTurn(
            interview_id=interview.id, position=0,
            interlocuteur="Consultant·e", question="Comment ça se passe ?",
            section_title="Ouverture",
        ))
        session.add(InterviewTurn(
            interview_id=interview.id, position=1,
            interlocuteur="Claire Rousseau",
            remarque=(
                "Première ligne du témoignage.\nDeuxième ligne du témoignage."
                if multiline else "Témoignage sur une seule ligne."
            ),
        ))
        session.commit()
        return interview.id
    finally:
        session.close()


def test_build_interview_pdf_libre_content_and_multiline_preserved() -> None:
    interview_id = _build_libre_interview(multiline=True)
    session = SessionLocal()
    try:
        interview = session.get(Interview, interview_id)
        pdf_bytes = build_interview_pdf(interview)
    finally:
        session.close()

    text = _pdf_text(pdf_bytes)
    assert "Claire Rousseau" in text
    assert "Message central de l'entretien." in text
    assert "Ouverture" in text
    assert "Comment ça se passe ?" in text
    assert "Contexte détaillé" in text
    assert "Force clé" in text
    # Catégorie vide : le texte de substitution s'affiche, pas une case vide muette.
    assert "pas de matière sur cette catégorie" in text

    # Le préfixe interlocuteur reste sur la 1ère ligne du tour (« Claire
    # Rousseau : Première ligne… ») ; seule la suite du tour continue en
    # lignes nues — c'est cette continuation qui prouve le fix multiligne.
    assert "Claire Rousseau : Première ligne du témoignage." in text.split("\n")
    assert "Deuxième ligne du témoignage." in text.split("\n")
    assert "Première ligne du témoignage. Deuxième ligne du témoignage." not in text


def test_build_interview_pdf_libre_sans_tours_ne_plante_pas() -> None:
    session = SessionLocal()
    try:
        mission = Mission(name="Mission PDF Libre Vide")
        session.add(mission)
        session.flush()
        interview = Interview(mission_id=mission.id, mode="libre", interviewee_name="Sans Tours")
        session.add(interview)
        session.commit()
        interview_id = interview.id
    finally:
        session.close()

    session = SessionLocal()
    try:
        pdf_bytes = build_interview_pdf(session.get(Interview, interview_id))
    finally:
        session.close()
    text = _pdf_text(pdf_bytes)
    assert "Sans Tours" in text
    assert "Aucun tour de parole" in text


# --------------------------------------------------------------------------- #
# Route HTTP GET /interviews/{id}/export/pdf
# --------------------------------------------------------------------------- #
def test_export_pdf_route_parametre(client: TestClient) -> None:
    interview_id = _build_parametre_interview(multiline=True)
    response = client.get(f"/interviews/{interview_id}/export/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="entretien_jean_dupont.pdf"'
    assert response.content[:4] == b"%PDF"
    text = _pdf_text(response.content)
    assert "Jean Dupont" in text
    assert "Ligne 2 de la réponse." in text.split("\n")


def test_export_pdf_route_libre(client: TestClient) -> None:
    interview_id = _build_libre_interview(multiline=False)
    response = client.get(f"/interviews/{interview_id}/export/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    text = _pdf_text(response.content)
    assert "Claire Rousseau" in text
    assert "Témoignage sur une seule ligne." in text


def test_export_pdf_route_entretien_introuvable_404(client: TestClient) -> None:
    assert client.get("/interviews/999999/export/pdf").status_code == 404
