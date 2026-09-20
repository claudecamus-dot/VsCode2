"""Le nombre de requetes SQL de la fiche mission ne croit pas avec le nombre
d'entretiens.

Constats audit-technique performance du 2026-09-13, reconduits le 2026-09-20 :
le gabarit evalue `iv.answers | selectattr(...) | length` et `iv.turns | length`
DANS la boucle `for iv in mission.interviews` (deux requetes paresseuses par
entretien, qui materialisent tous les `Answer.text` et toutes les questions de
tours pour n'afficher que deux entiers), et `mission_backups.fichiers_references`
re-parcourt `mission.interviews` en entites completes pour trois colonnes.

La mesure est un COMPTE DE REQUETES, pas une duree : une duree sur un poste
tiede est du bruit.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from starlette.testclient import TestClient

from app.db import SessionLocal, engine
from app.main import app
from app.models import (
    Answer,
    Interview,
    InterviewTurn,
    Mission,
    Question,
    Theme,
    Trame,
)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _mission_avec(nb_entretiens: int, nb_lignes: int) -> int:
    db = SessionLocal()
    try:
        mission = Mission(name="Compte-requetes")
        db.add(mission)
        db.flush()
        trame = Trame(mission_id=mission.id)
        db.add(trame)
        db.flush()
        theme = Theme(trame_id=trame.id, title="T")
        db.add(theme)
        db.flush()
        questions = [Question(theme_id=theme.id, label="q%d" % j) for j in range(nb_lignes)]
        db.add_all(questions)
        db.flush()
        for i in range(nb_entretiens):
            iv = Interview(mission_id=mission.id, interviewee_name="E%d" % i)
            db.add(iv)
            db.flush()
            for question in questions:
                db.add(
                    Answer(
                        interview_id=iv.id,
                        question_id=question.id,
                        status="answered",
                        text="x",
                    )
                )
                db.add(InterviewTurn(interview_id=iv.id, question="q"))
        db.commit()
        return mission.id
    finally:
        db.close()


def _compter_requetes(client, mission_id: int) -> int:
    compteur = {"n": 0}

    def _avant(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            compteur["n"] += 1

    event.listen(engine, "before_cursor_execute", _avant)
    try:
        reponse = client.get("/missions/%d" % mission_id)
        assert reponse.status_code == 200, reponse.status_code
    finally:
        event.remove(engine, "before_cursor_execute", _avant)
    return compteur["n"]


def test_le_cout_de_la_fiche_mission_ne_croit_pas_avec_les_entretiens(client):
    petite = _mission_avec(1, 3)
    grande = _mission_avec(6, 3)

    requetes_petite = _compter_requetes(client, petite)
    requetes_grande = _compter_requetes(client, grande)

    # Garde-fou du garde-fou : une page qui ne ferait AUCUN SELECT rendrait
    # l'ecart nul par construction.
    assert requetes_petite >= 2, requetes_petite
    # 5 entretiens de plus ne doivent pas coûter de requête supplémentaire.
    assert requetes_grande == requetes_petite, (
        "petite=%d grande=%d" % (requetes_petite, requetes_grande)
    )


def _colonnes_lues(client, mission_id: int) -> str:
    instructions = []

    def _avant(conn, cursor, statement, params, context, executemany):
        instructions.append(statement)

    event.listen(engine, "before_cursor_execute", _avant)
    try:
        assert client.get("/missions/%d" % mission_id).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", _avant)
    return "\n".join(instructions)


def test_la_fiche_mission_ne_ramene_pas_les_colonnes_lourdes(client):
    mission_id = _mission_avec(3, 2)
    sql = _colonnes_lues(client, mission_id)

    # Garde-fou du garde-fou : sans SELECT sur interviews, l'absence de colonne
    # lourde serait vraie par construction.
    assert "FROM interviews" in sql, sql[:500]
    lourdes = [c for c in ("raw_transcript", "free_notes", "resume") if c in sql]
    assert lourdes == [], lourdes


def test_la_fiche_mission_ne_ramene_pas_le_texte_des_reponses(client):
    mission_id = _mission_avec(3, 2)
    sql = _colonnes_lues(client, mission_id)
    assert "FROM answers" in sql, sql[:500]
    assert "answers.text" not in sql, sql
