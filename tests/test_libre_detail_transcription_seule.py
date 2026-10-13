"""Page d'un entretien libre : seule la TRANSCRIPTION est montrée (2026-10-08).

TEMPORAIRE — marqueur `TOUR-DE-TABLE-COUPE-TEMPORAIRE` dans libre_detail.html. À supprimer
avec le marqueur quand le tour de table et l'aperçu seront réactivés.

Demande utilisateur : « dans l'entretien libre on ne conserve que le format transcription,
tour de table et aperçu est à mettre en commentaire pour l'instant ».

L'INVARIANT qui justifie ce fichier : `save_libre_detail` remet l'identité à « Sans nom » et
efface le résumé quand leurs champs sont ABSENTS du POST. Ils vivaient dans l'onglet Tour de
table ; le neutraliser sans les re-poster aurait effacé le nom, le rôle, l'entité, la date et
le résumé de l'entretien au premier « Enregistrer ». Le test rejoue ce scénario de bout en
bout : afficher la page, poster ses champs tels quels, relire la base.
"""
from __future__ import annotations

import re
from datetime import date
from html import unescape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview, InterviewTurn, Mission


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


def _entretien() -> int:
    db = SessionLocal()
    try:
        mission = Mission(name="Mission transcription seule", is_draft=True)
        db.add(mission)
        db.commit()
        iv = Interview(
            mission_id=mission.id, mode="libre", status="done",
            interviewee_name="Atelier BMAD", interviewee_role="Responsable",
            interviewee_entity="CACIB TIF", interview_date=date(2026, 10, 7),
            resume="Un message central à ne pas perdre.",
            raw_transcript="Bonjour, voici la transcription brute de l'entretien.",
            tranches_manquantes=4,
        )
        db.add(iv)
        db.commit()
        db.add(InterviewTurn(
            interview_id=iv.id, position=0, interlocuteur="Marc",
            question="Une question ?", remarque="Une réponse.", section_title="Ouverture",
        ))
        db.commit()
        return iv.id
    finally:
        db.close()


def _page(client: TestClient, iid: int) -> str:
    r = client.get(f"/interviews/{iid}")
    assert r.status_code == 200, r.text[:300]
    return r.text


def test_la_page_ne_montre_que_la_transcription(client):
    html = _page(client, _entretien())
    assert 'id="turntab-transcription"' in html
    assert "voici la transcription brute" in html
    # Tour de table et aperçu : ni onglets, ni panneaux, ni leur contenu.
    assert 'data-turntab="tours"' not in html and 'data-turntab="apercu"' not in html
    assert 'id="turntab-tours"' not in html and 'id="turntab-apercu"' not in html
    assert "Une question ?" not in html and "Une réponse." not in html
    # Le bandeau « N tranches n'ont pas pu être structurées » parle du tour de table.
    assert "pas pu être structurée" not in html
    # Plus aucun champ modifiable : pas de bouton Enregistrer qui ne ferait rien.
    assert "Enregistrer les modifications" not in html


def test_enregistrer_les_champs_de_la_page_n_efface_ni_identite_ni_resume(client):
    iid = _entretien()
    html = _page(client, iid)
    champs = {
        nom: unescape(valeur)
        for nom, valeur in re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', html)
    }
    # Les champs que `save_libre_detail` écraserait s'ils étaient absents.
    assert set(champs) >= {
        "interviewee_name", "interviewee_role", "interviewee_entity",
        "interview_date", "resume",
    }, champs

    r = client.post(f"/interviews/{iid}/libre", data=champs, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    try:
        iv = db.get(Interview, iid)
        assert iv.interviewee_name == "Atelier BMAD"
        assert iv.interviewee_role == "Responsable"
        assert iv.interviewee_entity == "CACIB TIF"
        assert iv.interview_date == date(2026, 10, 7)
        assert iv.resume == "Un message central à ne pas perdre."
        tours = db.scalars(select(InterviewTurn).where(InterviewTurn.interview_id == iid)).all()
        assert len(tours) == 1 and tours[0].remarque == "Une réponse."
    finally:
        db.close()


def test_le_scenario_dangereux_existe_bien_sans_les_champs_caches(client):
    """Témoin : un POST SANS ces champs efface bien l'identité — c'est le danger que les
    champs cachés écartent (si ce test passe au rouge, la route a changé : revoir tout)."""
    iid = _entretien()
    client.post(f"/interviews/{iid}/libre", data={}, follow_redirects=False)
    db = SessionLocal()
    try:
        iv = db.get(Interview, iid)
        assert iv.interviewee_name == "Sans nom"
        assert iv.resume is None
    finally:
        db.close()


def test_aucun_bout_de_commentaire_ne_fuit_a_l_ecran(client):
    """Un commentaire Jinja qui cite `{# #}` se ferme trop tôt et affiche sa fin à l'écran
    (vu au rendu le 2026-10-08, invisible aux autres assertions)."""
    html = _page(client, _entretien())
    assert "{#" not in html and "#}" not in html
    assert "ne peut pas contenir" not in html and "COUPE-TEMPORAIRE" not in html
