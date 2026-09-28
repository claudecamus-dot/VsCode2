"""Le panneau de synthèse globale face aux entretiens libres et aux brouillons
— constats de l'analyse fonctionnelle du 2026-09-28 :

1. Une mission composée UNIQUEMENT d'entretiens libres analysés gardait le
   bouton « Générer (IA) » grisé : le gabarit ne comptait que `answer_count`
   (réponses structurées), alors que `generate_global` accepte les libres
   seuls (`answer_count == 0 and not material_libre` est son seul refus).
2. Un entretien libre SANS répartition est invisible de la synthèse
   (`libre_material` l'écarte) tout en s'affichant « Terminé » ailleurs : les
   missions réelles 16/19/22 montraient « rien à synthétiser » sans renvoi
   vers l'écran d'analyse manquant.
3. Les brouillons entrent dans la synthèse au même titre que les entretiens
   finis (`theme_material` ne filtre pas sur `status`) — comportement
   CONSERVÉ, mais désormais signalé (décision produit du 2026-09-28 :
   signaler plutôt qu'exclure en silence).

Seed en ORM direct (jamais en rejouant le wizard sur du HTML scrappé,
mémoire `feedback_html_scraping_double_escapes_entities`).
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Answer, Interview, Mission, Question, Theme, Trame


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


_REPARTITION = {"contexte": "Un contexte vu en entretien."}


def _mission_libre_analysee() -> int:
    """Mission sans trame : un seul entretien libre, répartition présente."""
    db = SessionLocal()
    try:
        mission = Mission(name="Libres analysés")
        db.add(mission)
        db.commit()
        db.add(Interview(
            mission_id=mission.id, interviewee_name="Alix", mode="libre",
            status="done", repartition=_REPARTITION,
        ))
        db.commit()
        return mission.id
    finally:
        db.close()


def _mission_libre_sans_analyse() -> tuple[int, int]:
    db = SessionLocal()
    try:
        mission = Mission(name="Libre sans analyse")
        db.add(mission)
        db.commit()
        iv = Interview(
            mission_id=mission.id, interviewee_name="Bao", mode="libre",
            status="done", repartition=None,
        )
        db.add(iv)
        db.commit()
        return mission.id, iv.id
    finally:
        db.close()


def _mission_brouillon_contributif() -> int:
    """Un entretien structuré resté en brouillon, avec une réponse remplie."""
    db = SessionLocal()
    try:
        mission = Mission(name="Brouillon contributif")
        db.add(mission)
        db.commit()
        trame = Trame(mission_id=mission.id)
        db.add(trame)
        db.commit()
        theme = Theme(trame_id=trame.id, title="Thème")
        db.add(theme)
        db.commit()
        question = Question(theme_id=theme.id, label="Question ?")
        db.add(question)
        db.commit()
        iv = Interview(
            mission_id=mission.id, interviewee_name="Camille", status="draft",
        )
        db.add(iv)
        db.commit()
        db.add(Answer(interview_id=iv.id, question_id=question.id, text="Réponse."))
        db.commit()
        return mission.id
    finally:
        db.close()


def _mission_libre_brouillon_avec_repartition() -> int:
    """Entretien libre ANALYSÉ mais resté `draft` — cas atteignable en usage
    normal : `interviews_analyse` écrit `repartition` sans jamais toucher
    `status`, qui vaut « draft » par défaut. Sa matière entre donc dans la
    synthèse alors que l'entretien n'a jamais été finalisé (revue 2026-09-28 :
    la branche `mode == "libre" and repartition` de `brouillons_contributifs`
    n'était exercée par aucun test)."""
    db = SessionLocal()
    try:
        mission = Mission(name="Libre analysé mais brouillon")
        db.add(mission)
        db.commit()
        db.add(Interview(
            mission_id=mission.id, interviewee_name="Dany", mode="libre",
            status="draft", repartition=_REPARTITION,
        ))
        db.commit()
        return mission.id
    finally:
        db.close()


def _mission_deux_libres_sans_analyse() -> int:
    """Deux entretiens, pour exercer l'accord pluriel des bandeaux — le cas à
    un seul élément ne voit ni le « nt » verbal ni la virgule de jonction."""
    db = SessionLocal()
    try:
        mission = Mission(name="Deux libres sans analyse")
        db.add(mission)
        db.commit()
        for nom in ("Eve", "Farid"):
            db.add(Interview(
                mission_id=mission.id, interviewee_name=nom, mode="libre",
                status="done", repartition=None,
            ))
        db.commit()
        return mission.id
    finally:
        db.close()


def test_libre_analyse_mais_brouillon_est_signale(client: TestClient) -> None:
    mission_id = _mission_libre_brouillon_avec_repartition()
    page = client.get(f"/missions/{mission_id}/synthese/globale").text
    assert "en brouillon" in page
    assert "Dany" in page
    # Et il compte bien comme matière : le bandeau « sans analyse » ne doit PAS
    # s'afficher, les deux conditions étant exclusives sur `repartition`.
    assert "sans analyse" not in page


def test_deux_libres_sans_analyse_accordent_le_bandeau_au_pluriel(
    client: TestClient,
) -> None:
    mission_id = _mission_deux_libres_sans_analyse()
    # Espaces normalisés : le gabarit répartit la phrase sur plusieurs lignes,
    # une assertion sur le texte brut testerait sa mise en forme Jinja.
    page = re.sub(r"\s+", " ", client.get(f"/missions/{mission_id}/synthese/globale").text)
    assert "2 entretiens libres sans analyse" in page
    assert "ils ne comptent pas" in page
    assert "Eve" in page and "Farid" in page


def test_generer_actif_sur_une_mission_de_libres_analyses(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `ai_ready` (SDK présent) n'est pas l'objet du test : on le tient vrai
    # pour isoler le facteur mesuré, le comptage des entretiens libres.
    monkeypatch.setattr("app.routers.synthese.is_configured", lambda: True)
    mission_id = _mission_libre_analysee()
    page = client.get(f"/missions/{mission_id}/synthese/globale").text
    bouton = page[page.index("/synthese/globale/generate"):]
    bouton = bouton[:bouton.index(">")]
    # « disabled title= » (l'attribut et sa raison), pas « disabled » nu :
    # `hx-disabled-elt="this"` du même bouton contient cette sous-chaîne.
    assert "disabled title=" not in bouton, bouton
    assert "rien à synthétiser" not in page
    assert "1 entretien libre analysé" in page


def test_libre_sans_analyse_est_signale_avec_le_lien_vers_l_analyse(
    client: TestClient,
) -> None:
    mission_id, interview_id = _mission_libre_sans_analyse()
    page = client.get(f"/missions/{mission_id}/synthese/globale").text
    assert "sans analyse" in page
    assert f'href="/interviews/{interview_id}/analyse"' in page
    # Et le bouton reste bien grisé : rien d'analysé, rien à générer.
    bouton = page[page.index("/synthese/globale/generate"):]
    bouton = bouton[:bouton.index(">")]
    assert "disabled title=" in bouton, bouton


def test_brouillon_avec_reponses_est_signale(client: TestClient) -> None:
    mission_id = _mission_brouillon_contributif()
    page = client.get(f"/missions/{mission_id}/synthese/globale").text
    assert "en brouillon" in page
    assert "Camille" in page


def test_fragment_htmx_porte_aussi_les_compteurs_libres(client: TestClient) -> None:
    """`_global_panel_context` (le fragment échangé par HTMX) doit fournir les
    mêmes compteurs que la page : sans `libre_count`, le bouton redevenait
    grisé au premier échange sur une mission 100 % libres."""
    from app.routers.synthese import _global_panel_context
    from app.services.synthese_ecriture import get_or_create_global_synthesis

    mission_id = _mission_libre_analysee()
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        gs = get_or_create_global_synthesis(db, mission)
        db.commit()
        contexte = _global_panel_context(None, db, mission, gs, error=None)
    finally:
        db.close()
    assert contexte["libre_count"] == 1
    assert contexte["libres_sans_analyse"] == []
    assert contexte["brouillons_contributifs"] == []
