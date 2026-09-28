"""Couverture déterministe de la synthèse (I1 du cadrage « restitution
défendable », docs/reflexions/spec-restitution-defendable.md).

Ce que ces tests figent, et POURQUOI c'est le cœur du sujet : le chiffre
« N interviewés sur M » doit venir du CODE, jamais du modèle. C'est lui qui
permettra plus tard (I2) de contredire un constat qualifié « consensus » alors
que deux personnes seulement l'ont porté. Un comptage approximatif serait pire
qu'aucun comptage : il donnerait à une affirmation IA l'apparence d'une mesure.

Deux pièges couverts explicitement :
- comptage par ID d'entretien et non par nom (deux homonymes comptent deux) ;
- les entretiens libres, qui n'ont pas de questions, sont hors du dénominateur
  d'un thème — les y mettre ferait baisser une couverture sans qu'aucune
  réponse ne manque.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Answer, Interview, Mission, Question, Theme, Trame
from app.services.synthese_material import couverture_mission, couverture_par_theme


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


def _mission(nom: str) -> Mission:
    db = SessionLocal()
    mission = Mission(name=nom)
    db.add(mission)
    db.commit()
    db.refresh(mission)
    db.close()
    return mission


def _trame(mission_id: int, *titres: str) -> dict[str, tuple[int, int]]:
    """Crée une trame à un thème par titre, une question chacun.
    Rend `{titre: (theme_id, question_id)}`."""
    db = SessionLocal()
    try:
        trame = Trame(mission_id=mission_id)
        db.add(trame)
        db.commit()
        out = {}
        for titre in titres:
            theme = Theme(trame_id=trame.id, title=titre)
            db.add(theme)
            db.commit()
            question = Question(theme_id=theme.id, label=f"{titre} ?")
            db.add(question)
            db.commit()
            out[titre] = (theme.id, question.id)
        return out
    finally:
        db.close()


def _entretien(mission_id: int, nom: str, reponses: dict[int, str] | None = None,
               mode: str = "parametre", repartition=None) -> int:
    db = SessionLocal()
    try:
        iv = Interview(mission_id=mission_id, interviewee_name=nom, mode=mode,
                       status="done", repartition=repartition)
        db.add(iv)
        db.commit()
        for question_id, texte in (reponses or {}).items():
            db.add(Answer(interview_id=iv.id, question_id=question_id, text=texte))
        db.commit()
        return iv.id
    finally:
        db.close()


def _couv_themes(mission_id: int) -> dict[int, tuple[int, int]]:
    """Calcul DANS la session : la mission porte des relations chargées à la
    demande, un objet rendu hors session lèverait `DetachedInstanceError`."""
    db = SessionLocal()
    try:
        return couverture_par_theme(db.get(Mission, mission_id))
    finally:
        db.close()


def _couv_mission(mission_id: int) -> tuple[int, int]:
    db = SessionLocal()
    try:
        return couverture_mission(db.get(Mission, mission_id))
    finally:
        db.close()


def test_couverture_par_theme_compte_les_repondants_distincts() -> None:
    mission = _mission("Couverture")
    trame = _trame(mission.id, "Gouvernance", "Outillage")
    _, q_gouv = trame["Gouvernance"]
    _, q_outil = trame["Outillage"]
    _entretien(mission.id, "Alix", {q_gouv: "Oui.", q_outil: "Oui."})
    _entretien(mission.id, "Bao", {q_gouv: "Oui."})
    _entretien(mission.id, "Chris", {})  # entretien ouvert, aucune réponse

    couverture = _couv_themes(mission.id)
    assert couverture[trame["Gouvernance"][0]] == (2, 3)
    assert couverture[trame["Outillage"][0]] == (1, 3)


def test_une_reponse_vide_ne_compte_pas_comme_une_reponse() -> None:
    """`Answer` existe mais son texte est vide : la matière envoyée à la
    synthèse l'écarte (`theme_material`), le comptage doit l'écarter aussi —
    sinon le chiffre affirme une couverture que la synthèse n'a pas eue."""
    mission = _mission("Vides")
    trame = _trame(mission.id, "Thème")
    theme_id, question_id = trame["Thème"]
    _entretien(mission.id, "Alix", {question_id: "   "})

    assert _couv_themes(mission.id)[theme_id] == (0, 1)


def test_deux_homonymes_comptent_pour_deux() -> None:
    """Le comptage passe par l'ID d'entretien. Par nom, ces deux-là auraient
    fusionné en un seul répondant — et « 1 sur 2 » aurait remplacé « 2 sur 2 »."""
    mission = _mission("Homonymes")
    trame = _trame(mission.id, "Thème")
    theme_id, question_id = trame["Thème"]
    _entretien(mission.id, "Jean Martin", {question_id: "Un avis."})
    _entretien(mission.id, "Jean Martin", {question_id: "Un autre avis."})

    assert _couv_themes(mission.id)[theme_id] == (2, 2)


def test_un_entretien_libre_reste_hors_du_denominateur_d_un_theme() -> None:
    mission = _mission("Libre hors dénominateur")
    trame = _trame(mission.id, "Thème")
    theme_id, question_id = trame["Thème"]
    _entretien(mission.id, "Alix", {question_id: "Oui."})
    _entretien(mission.id, "Bao", mode="libre", repartition={"contexte": "Vu."})

    # 1 répondant sur 1 entretien STRUCTURÉ — le libre ne fait pas tomber à 1/2.
    assert _couv_themes(mission.id)[theme_id] == (1, 1)


def test_couverture_mission_compte_libres_et_structures() -> None:
    mission = _mission("Couverture mission")
    trame = _trame(mission.id, "Thème")
    _, question_id = trame["Thème"]
    _entretien(mission.id, "Alix", {question_id: "Oui."})
    _entretien(mission.id, "Bao", mode="libre", repartition={"contexte": "Vu."})
    _entretien(mission.id, "Chris", mode="libre", repartition=None)  # non analysé
    _entretien(mission.id, "Dany", {})  # aucune réponse

    assert _couv_mission(mission.id) == (2, 4)


def test_mission_sans_trame_ne_produit_aucune_couverture_de_theme() -> None:
    """Une mission née d'un entretien libre n'a pas de trame du tout."""
    mission = _mission("Sans trame")
    _entretien(mission.id, "Alix", mode="libre", repartition={"contexte": "Vu."})

    assert _couv_themes(mission.id) == {}
    assert _couv_mission(mission.id) == (1, 1)


def test_un_theme_sans_question_n_est_pas_un_trou_de_couverture() -> None:
    """(0, N) l'aurait affiche comme un thème mal couvert ; il n'y a
    simplement rien à y couvrir (revue 2026-09-28)."""
    mission = _mission("Thème vide")
    db = SessionLocal()
    try:
        trame = Trame(mission_id=mission.id)
        db.add(trame); db.commit()
        theme = Theme(trame_id=trame.id, title="Thème sans question")
        db.add(theme); db.commit()
        theme_id = theme.id
    finally:
        db.close()
    _entretien(mission.id, "Alix")

    assert _couv_themes(mission.id)[theme_id] == (0, 0)


def test_une_mission_sans_entretien_structure_n_affiche_pas_zero_sur_zero(
    client: TestClient,
) -> None:
    """Le piege que la revue a trouve : « 0/0 » rendu dans la couleur du
    complet se lit comme une couverture pleine. Rien a mesurer doit se voir
    comme rien a mesurer, pas comme un succes."""
    mission = _mission("Trame mais que des libres")
    trame = _trame(mission.id, "Gouvernance")
    theme_id, _ = trame["Gouvernance"]
    _entretien(mission.id, "Lea", mode="libre", repartition={"contexte": "Vu."})

    assert _couv_themes(mission.id)[theme_id] == (0, 0)
    page = client.get(f"/missions/{mission.id}/synthese/globale").text
    assert "couverture-neutre" in page
    assert "0/0" not in page


def test_l_ecran_affiche_la_couverture(client: TestClient) -> None:
    """Les deux chiffres de l'ecran sont DISTINCTS par construction : le ratio
    de thème (1/2, entretiens structures) ne doit pas pouvoir se confondre
    avec celui de mission (2/3, libre analyse compris) — sans quoi
    l'assertion passerait en mesurant le mauvais des deux."""
    mission = _mission("Écran couverture")
    trame = _trame(mission.id, "Gouvernance")
    _, question_id = trame["Gouvernance"]
    _entretien(mission.id, "Alix", {question_id: "Oui."})
    _entretien(mission.id, "Bao", {})
    _entretien(mission.id, "Lea", mode="libre", repartition={"contexte": "Vu."})

    page = client.get(f"/missions/{mission.id}/synthese/globale").text
    assert "Couverture par thème" in page
    assert "1/2" in page                                   # le thème
    assert "2/3 entretiens nourrissent la synthèse" in page  # la mission
    assert "couverture-partielle" in page
