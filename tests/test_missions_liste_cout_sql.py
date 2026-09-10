"""Le compteur de brouillons vides : juste, et à coût constant.

Constat d'audit du hub (performance, 2026-09-09) : « zéro `selectinload` dans
tout `app/` » — et `list_missions` est la page la plus visitée du produit.
Chaque mission affichée coûtait une requête pour ses entretiens, plus une par
entretien pour ses tours ou ses réponses (`_draft_vide`).

Mesuré le 2026-09-10 sur une copie de l'installation réelle (15 missions) :
15 requêtes / 35,6 ms avec le prédicat Python, 2 requêtes / 4,6 ms avec le
comptage SQL, résultat identique (7 des deux côtés).

Un `selectinload` avait d'abord été posé (6 requêtes / 9,7 ms) : écarté par
revue adversariale, parce qu'il MATÉRIALISAIT tours et réponses de tout le
corpus — colonnes `Text` comprises — pour produire un entier. Le nombre de
requêtes devenait constant, le volume transféré devenait proportionnel à la
base. Les `EXISTS` corrélés sont constants EN REQUÊTES ET EN MÉMOIRE.

Deux propriétés sont vérifiées ici, et la première compte plus que la seconde :
le compte SQL doit être JUSTE (identique au prédicat Python, qui reste la
définition de référence et sert au chemin de suppression), et son coût ne doit
pas suivre la taille de la liste.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.db import DB_PATH, SessionLocal, engine, init_db
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
from app.routers.missions import _compter_brouillons_vides, _draft_vide


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


def _semer_les_cas_limites() -> None:
    """Un exemplaire de CHAQUE branche du prédicat.

    La première version de ce test ne semait que des brouillons dont le premier
    entretien portait une réponse : `all(...)` court-circuitait au premier
    élément, et deux tiers du correctif pouvaient être supprimés en laissant le
    test vert (revue adversariale du 2026-09-10, F4). On sème donc aussi le
    brouillon SANS entretien, celui à trame remplie, et le mode libre.
    """
    with SessionLocal() as db:
        # 1. Brouillon totalement vide -> COMPTE.
        db.add(Mission(name="Vide sans rien", is_draft=True))

        # 2. Brouillon sans entretien mais AVEC trame remplie -> ne compte pas.
        #    (Seule branche qui exerce `trame -> themes`.)
        m2 = Mission(name="Vide mais trame remplie", is_draft=True)
        db.add(m2); db.flush()
        t2 = Trame(mission_id=m2.id); db.add(t2); db.flush()
        db.add(Theme(trame_id=t2.id, title="Un theme"))

        # 3. Brouillon avec un entretien STRUCTURÉ porteur d'une réponse -> ne compte pas.
        m3 = Mission(name="Avec reponse", is_draft=True)
        db.add(m3); db.flush()
        t3 = Trame(mission_id=m3.id); db.add(t3); db.flush()
        th3 = Theme(trame_id=t3.id, title="T"); db.add(th3); db.flush()
        q3 = Question(theme_id=th3.id, label="Q"); db.add(q3); db.flush()
        iv3 = Interview(mission_id=m3.id, interviewee_name="P", mode="parametre")
        db.add(iv3); db.flush()
        db.add(Answer(interview_id=iv3.id, question_id=q3.id, text="une reponse"))

        # 4. Brouillon avec un entretien LIBRE porteur de tours -> ne compte pas.
        #    (Seule branche qui exerce `turns`.)
        m4 = Mission(name="Avec tours", is_draft=True)
        db.add(m4); db.flush()
        iv4 = Interview(mission_id=m4.id, interviewee_name="L", mode="libre")
        db.add(iv4); db.flush()
        db.add(InterviewTurn(interview_id=iv4.id, position=0, remarque="un tour"))

        # 5. Brouillon avec un entretien VIDE -> COMPTE (c'est le cas F8 du
        #    2026-09-04 : l'extraction IA en échec n'empêche plus d'enregistrer).
        m5 = Mission(name="Entretien vide", is_draft=True)
        db.add(m5); db.flush()
        db.add(Interview(mission_id=m5.id, interviewee_name="V", mode="libre"))

        # 6. Mission NON brouillon, vide -> ne compte jamais.
        db.add(Mission(name="Pas un brouillon", is_draft=False))
        db.commit()


def _semer_du_volume(nb: int) -> None:
    with SessionLocal() as db:
        for i in range(nb):
            m = Mission(name=f"Volume {i}", is_draft=True)
            db.add(m); db.flush()
            iv = Interview(mission_id=m.id, interviewee_name=f"P{i}", mode="libre")
            db.add(iv); db.flush()
            db.add(InterviewTurn(interview_id=iv.id, position=0, remarque="tour"))
        db.commit()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_le_compte_sql_est_identique_au_predicat_python():
    """LA propriété qui compte. `_draft_vide` reste la définition de référence
    (le chemin de suppression s'en sert) : si le SQL en diverge, l'écran
    annonce un nombre de brouillons que le bouton ne supprimera pas — ou
    l'inverse, bien pire."""
    _semer_les_cas_limites()
    with SessionLocal() as db:
        missions = db.scalars(select(Mission).where(Mission.is_demo.is_(False))).all()
        attendu = sum(1 for m in missions if _draft_vide(m))
        obtenu = _compter_brouillons_vides(db, demo=False)
    assert obtenu == attendu, (
        f"SQL={obtenu}, Python={attendu} : le compteur affiche autre chose que "
        "ce que le nettoyage supprimera"
    )
    assert attendu == 2, (
        f"precondition du semis : 2 brouillons vides attendus, {attendu} trouves"
    )


def _requetes_pour_la_liste(client: TestClient) -> int:
    compte = []
    ecouteur = lambda c, cur, s, p, ctx, m: compte.append(s)  # noqa: E731
    event.listen(engine, "before_cursor_execute", ecouteur)
    try:
        reponse = client.get("/missions")
        assert reponse.status_code == 200, reponse.text
        # Le gabarit doit vraiment avoir rendu la liste : sans cette assertion,
        # une page d'erreur silencieuse laisserait le compte de requetes bas et
        # le test vert.
        assert "Volume 3" in reponse.text or "Vide sans rien" in reponse.text
    finally:
        event.remove(engine, "before_cursor_execute", ecouteur)
    return len(compte)


def test_le_nombre_de_requetes_ne_suit_pas_le_nombre_de_missions(client):
    """x3 missions, PAS x3 requêtes. Sur le code d'avant (`lazy=select` et un
    `sum()` Python), chaque mission coûtait au moins une requête."""
    _semer_du_volume(5)
    petit = _requetes_pour_la_liste(client)

    _semer_du_volume(10)  # 15 de volume, plus les cas limites
    grand = _requetes_pour_la_liste(client)

    assert grand <= petit + 1, (
        f"{petit} requetes pour 5 missions, {grand} pour 15 : le cout suit la "
        "taille de la liste (N+1)"
    )
