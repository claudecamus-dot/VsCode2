"""`_get_mission` (app/routers/export.py) : le coût de charger les verbatims
d'une mission ne doit pas suivre son nombre d'entretiens.

Constat d'audit du hub (performance, 2026-09-09), N+1 systémique : « zéro
`selectinload`/`joinedload`/`subqueryload`/`lazy=` dans tout `app/` ».
`Mission.all_verbatims` est une property nue (`models.py`) :

    return [v for iv in self.interviews for v in iv.verbatims]

Elle est évaluée dans `synthese/apercu.html` (`{% if mission.all_verbatims %}`
ligne 162, `{% for v in mission.all_verbatims %}` lignes 375 et 394, plus la
garde de `selected_verbatims` ligne 369), et chaque verbatim affiche
`v.interview.interviewee_name` (lignes 384, 396) — deux niveaux de N+1 :
UNE requête par entretien pour `iv.verbatims`, et (mesuré, pas supposé) UNE
requête de plus par entretien pour la remontée `Verbatim -> Interview` quand
elle n'est pas déjà dans l'identity map.

Portée du correctif (mandat : cibler, pas de refactor balayant) : `_get_mission`
dans `app/routers/export.py` seulement — c'est le point d'entrée commun de
TOUTES les routes qui rendent `apercu.html` (aperçu, génération SWOT/
difficultés/executive summary, import d'analyse).

`all_theme_material`, l'AUTRE N+1 nommé par le même audit et appelé par les
mêmes routes (`_synthese_context`), a déjà été mesuré et volontairement laissé
tel quel (`synthese_material.all_theme_material`, revue du 2026-09-10 : « 26
requêtes / 8 ms, négligeable devant l'appel IA qui suit ») — pas rouvert ici.

C'est pour ça que ce fichier mesure `_get_mission` + `all_verbatims` en ISOLANT
l'appel (pas le coût total de la route HTTP) : le coût de bout en bout de
`GET .../apercu` reste linéaire en nombre d'entretiens À CAUSE de
`all_theme_material`, une propriété DÉJÀ connue et acceptée — un test qui
mesurerait la route entière retomberait rouge pour une raison qui n'est pas
celle qu'il est censé garder rouge.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import (
    Interview,
    Mission,
    Question,
    RecommendationAxis,
    Theme,
    Trame,
    Verbatim,
)
from app.routers.export import _get_mission


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


def _creer_mission_avec_verbatims(nb_entretiens: int) -> int:
    """Une mission structurée, `nb_entretiens` entretiens, 2 verbatims chacun
    (le cas que la garde `{% if mission.all_verbatims %}` doit trouver plein)."""
    with SessionLocal() as db:
        mission = Mission(name=f"Mission {nb_entretiens} entretiens", is_draft=False)
        db.add(mission)
        db.flush()
        trame = Trame(mission_id=mission.id)
        db.add(trame)
        db.flush()
        theme = Theme(trame_id=trame.id, title="Theme unique")
        db.add(theme)
        db.flush()
        question = Question(theme_id=theme.id, label="Question unique")
        db.add(question)
        db.flush()
        # `apercu.html` masque tout le panneau d'onglets (verbatims compris)
        # derriere `has_content = (global_synthesis and .has_content) or axes` :
        # un axe suffit a l'ouvrir sans avoir a fabriquer une synthese globale.
        db.add(RecommendationAxis(mission_id=mission.id, title="Axe", position=0))
        for i in range(nb_entretiens):
            iv = Interview(
                mission_id=mission.id, interviewee_name=f"Interviewe {i}",
                mode="parametre", status="done",
            )
            db.add(iv)
            db.flush()
            db.add(Verbatim(interview_id=iv.id, question_id=question.id, quote=f"Citation {i}a"))
            db.add(Verbatim(interview_id=iv.id, question_id=question.id, quote=f"Citation {i}b"))
        db.commit()
        return mission.id


def _requetes_pour_charger_les_verbatims(mission_id: int) -> tuple[int, list]:
    """Reproduit EXACTEMENT ce que `apercu.html` fait de la mission rendue par
    `_get_mission` : lire `all_verbatims`, puis l'intervenant de chacun. Isolé
    de la route HTTP entière pour ne pas mélanger ce coût-là avec celui,
    distinct et déjà arbitré, d'`all_theme_material`."""
    compte = []
    ecouteur = lambda c, cur, s, p, ctx, m: compte.append(s)  # noqa: E731
    event.listen(engine, "before_cursor_execute", ecouteur)
    try:
        with SessionLocal() as db:
            mission = _get_mission(db, mission_id)
            verbatims = mission.all_verbatims
            qui = [v.interview.interviewee_name for v in verbatims]
    finally:
        event.remove(engine, "before_cursor_execute", ecouteur)
    return len(compte), qui


def test_le_nombre_de_requetes_ne_suit_pas_le_nombre_d_entretiens():
    petite_mission = _creer_mission_avec_verbatims(2)
    petit, qui_petit = _requetes_pour_charger_les_verbatims(petite_mission)
    assert len(qui_petit) == 4, "precondition : 2 entretiens x 2 verbatims"

    grande_mission = _creer_mission_avec_verbatims(15)
    grand, qui_grand = _requetes_pour_charger_les_verbatims(grande_mission)
    assert len(qui_grand) == 30, "precondition : 15 entretiens x 2 verbatims"

    assert grand <= petit, (
        f"{petit} requetes pour charger verbatims+intervenant de 2 entretiens, "
        f"{grand} pour 15 : le cout suit encore le nombre d'entretiens"
    )


def test_le_rendu_html_montre_bien_les_verbatims_de_la_grosse_mission():
    """Bout en bout, une fois : la route existe, répond 200, et affiche
    vraiment le contenu que le test ci-dessus mesure en isolation — sans
    seuil de requêtes ici, puisque le coût de la route entière reste
    linéaire à cause d'`all_theme_material` (mesuré et accepté séparément,
    cf. docstring du module)."""
    mission_id = _creer_mission_avec_verbatims(6)
    reponse = TestClient(app).get(f"/missions/{mission_id}/synthese/apercu")
    assert reponse.status_code == 200, reponse.text
    assert "Citation 0a" in reponse.text
    assert "Citation 5b" in reponse.text


def test_les_verbatims_de_la_mission_restent_corrects_apres_le_correctif():
    """Le correctif de performance ne doit rien changer au RESULTAT — mêmes
    verbatims, même attribution à leur intervenant. Sans ce test, un
    `selectinload` mal ciblé (mauvais chemin de relation) pourrait rendre une
    page vide ou incomplète tout en semblant plus rapide. Passe par
    `_get_mission`, la fonction réellement modifiée — pas une reconstruction
    ORM parallèle qui ne prouverait rien du correctif livré."""
    mission_id = _creer_mission_avec_verbatims(3)
    with SessionLocal() as db:
        mission = _get_mission(db, mission_id)
        citations = sorted(v.quote for v in mission.all_verbatims)
        qui = sorted(v.interview.interviewee_name for v in mission.all_verbatims)
    assert citations == [
        "Citation 0a", "Citation 0b", "Citation 1a", "Citation 1b",
        "Citation 2a", "Citation 2b",
    ]
    assert qui == [
        "Interviewe 0", "Interviewe 0", "Interviewe 1", "Interviewe 1",
        "Interviewe 2", "Interviewe 2",
    ]
