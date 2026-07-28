"""Axes d'étude configurables d'une mission (2026-07-27, demande utilisateur).

Les 5 rubriques de la synthèse globale (contexte, culture & ADN, forces &
succès, points d'amélioration, aspirations) étaient figées jusque dans le
schéma : 5 colonnes de `GlobalSynthesis`, 5 clés du JSON demandé à l'IA, 5
rubriques du gabarit d'export, 5 champs de l'écran. Elles deviennent des lignes
(`MissionSynthesisAxis`) modifiables par mission.

Ce que ces tests verrouillent, au-delà du CRUD :

- le SEMIS des 5 défauts (une mission existante ne change pas de comportement) ;
- la STABILITÉ de la clé au renommage — c'est l'adresse du contenu déjà rédigé,
  la renommer l'orphelinerait en silence ;
- la PROPAGATION aux deux onglets que l'utilisateur a cités : le gabarit
  d'export manuel ET le prompt/schéma de l'IA intégrée. Un axe ajouté mais
  absent du gabarit serait demandé à personne, jamais rempli, jamais réimporté.
"""
from __future__ import annotations

from html import unescape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import GlobalSynthesis, Mission, MissionSynthesisAxis
from app.services import mission_axes
from app.services.analyse_import import parse_analysis_markdown
from app.services.mission_export import build_export_markdown
from app.services.synthese_ai import global_schema, global_system


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


def _mission(nom: str = "Mission axes") -> int:
    db = SessionLocal()
    try:
        mission = Mission(name=nom)
        db.add(mission)
        db.commit()
        return mission.id
    finally:
        db.close()


def _axes(mission_id: int) -> list[MissionSynthesisAxis]:
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        return list(mission_axes.axes_of(db, mission))
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Semis et invariants du service
# --------------------------------------------------------------------------- #
def test_une_mission_est_semee_avec_les_cinq_axes_historiques():
    """Semis paresseux : les missions déjà en base (dont celles d'avant cette
    fonctionnalité) héritent des 5 axes sans migration de données, avec les
    MÊMES clés que les colonnes historiques de `GlobalSynthesis`."""
    axes = _axes(_mission())
    assert [a.key for a in axes] == [
        "contexte", "culture_adn", "forces_succes", "points_amelioration", "aspirations",
    ]
    assert [a.position for a in axes] == [0, 1, 2, 3, 4]
    assert all(a.hint for a in axes)  # la consigne donnée à l'IA est renseignée


def test_le_semis_ne_rejoue_pas_a_chaque_lecture():
    mission_id = _mission()
    premiers = [a.id for a in _axes(mission_id)]
    assert [a.id for a in _axes(mission_id)] == premiers


def test_une_cle_est_unique_et_ne_recycle_pas_celle_d_un_axe_supprime():
    """Réutiliser la clé d'un axe supprimé ressusciterait son ancien contenu
    (stocké par clé) dans le nouvel axe, sans que personne l'ait demandé."""
    mission_id = _mission()
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        a1 = mission_axes.creer_axe(db, mission, "Outillage & données")
        assert a1.key == "outillage_donnees"
        a2 = mission_axes.creer_axe(db, mission, "Outillage & données")
        assert a2.key != a1.key

        # Le cas que ce test NOMMAIT sans l'exercer (trouvé en revue adversariale
        # 2026-07-28) : deux axes SIMULTANÉS ne prouvent rien du recyclage après
        # SUPPRESSION — et le recyclage avait bien lieu, ressuscitant le contenu.
        cle_supprimee = a1.key  # relu avant suppression : l'objet ORM expire ensuite
        gs = GlobalSynthesis(mission_id=mission.id)
        db.add(gs)
        gs.set_contenu(cle_supprimee, "Ancien contenu, propriété de l'axe supprimé.")
        db.commit()
        assert mission_axes.supprimer_axe(db, mission, a1) is True

        a3 = mission_axes.creer_axe(db, mission, "Outillage & données")
        assert a3.key != cle_supprimee, "la clé d'un axe supprimé a été recyclée"
        assert gs.contenu(a3.key) == "", "le nouvel axe hérite du contenu de l'ancien"
    finally:
        db.close()


def test_le_dernier_axe_n_est_pas_supprimable():
    """Sans axe, la mission n'a plus de synthèse — et `axes_of` la resèmerait
    aux 5 défauts à la lecture suivante : la suppression se serait annulée
    toute seule, en silence."""
    mission_id = _mission()
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        axes = mission_axes.axes_of(db, mission)
        for axe in axes[:-1]:
            assert mission_axes.supprimer_axe(db, mission, axe) is True
        assert mission_axes.supprimer_axe(db, mission, mission.synthesis_axes[0]) is False
        assert len(mission_axes.axes_of(db, mission)) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Stockage du contenu par clé
# --------------------------------------------------------------------------- #
def test_le_contenu_survit_a_un_renommage(client):
    """La clé ne bouge jamais au renommage — sinon le texte déjà rédigé
    deviendrait orphelin."""
    mission_id = _mission()
    axe = _axes(mission_id)[0]
    client.post(
        f"/syntheses/globale/{mission_id}/field",
        data={"field": axe.key, "value": "- Un contexte rédigé à la main"},
    )
    response = client.post(
        f"/missions/{mission_id}/axes/{axe.id}",
        data={"label": "Contexte & historique", "hint": "ce qui précède"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        axe_relu = mission.synthesis_axes[0]
        assert axe_relu.key == axe.key           # clé inchangée
        assert axe_relu.label == "Contexte & historique"
        assert mission.global_synthesis.contenu(axe.key) == "- Un contexte rédigé à la main"
    finally:
        db.close()


def test_le_contenu_d_un_axe_ajoute_est_stocke_et_relu(client):
    mission_id = _mission()
    client.post(f"/missions/{mission_id}/axes", data={"label": "Outillage & données"})
    axe = _axes(mission_id)[-1]
    client.post(
        f"/syntheses/globale/{mission_id}/field",
        data={"field": axe.key, "value": "- Des outils hétérogènes"},
    )
    db = SessionLocal()
    try:
        gs = db.get(Mission, mission_id).global_synthesis
        assert gs.contenu(axe.key) == "- Des outils hétérogènes"
        assert gs.has_content  # un axe AJOUTÉ suffit à rendre la synthèse non vide
    finally:
        db.close()


def test_un_champ_qui_n_est_pas_un_axe_est_refuse(client):
    mission_id = _mission()
    response = client.post(
        f"/syntheses/globale/{mission_id}/field",
        data={"field": "rubrique_inventee", "value": "x"},
    )
    assert response.status_code == 400


def test_le_contenu_historique_reste_lisible_sans_valeurs():
    """Base d'avant la migration : `valeurs` est vide, les 5 colonnes portent
    le texte — il doit rester lisible tel quel."""
    mission_id = _mission()
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        gs = GlobalSynthesis(mission_id=mission.id, contexte="- Ancien contexte")
        db.add(gs)
        db.commit()
        assert gs.contenu("contexte") == "- Ancien contexte"
        assert gs.has_content
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Propagation aux deux onglets cités par l'utilisateur
# --------------------------------------------------------------------------- #
def test_le_gabarit_d_export_manuel_suit_les_axes():
    """Onglet « Manuel (export / import) » : un axe ajouté doit être demandé à
    l'analyse externe, un axe supprimé ne doit plus l'être."""
    mission_id = _mission()
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        axes = mission_axes.axes_of(db, mission)
        mission_axes.creer_axe(db, mission, "Outillage & données", "les outils en place")
        mission_axes.supprimer_axe(db, mission, axes[1])  # culture_adn
        markdown = build_export_markdown(mission, mission_axes.axes_of(db, mission))
    finally:
        db.close()
    assert "### Outillage & données" in markdown
    assert "_les outils en place_" in markdown
    assert "### Culture & ADN" not in markdown
    assert "### Contexte" in markdown


def test_le_reimport_reconnait_un_axe_ajoute():
    """Le pendant du test précédent : sans cela, la rubrique demandée revenait
    remplie et était silencieusement ignorée au réimport."""
    mission_id = _mission()
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        mission_axes.axes_of(db, mission)
        mission_axes.creer_axe(db, mission, "Outillage & données")
        axes = mission_axes.axes_of(db, mission)
    finally:
        db.close()

    markdown = (
        "## SYNTHÈSE GLOBALE\n\n"
        "### Contexte\n- Un contexte\n\n"
        "### Outillage & données\n- Des outils hétérogènes\n"
    )
    parsed = parse_analysis_markdown(markdown, axes)["global_synthesis"]
    assert parsed["contexte"] == "- Un contexte"
    assert parsed["outillage_donnees"] == "- Des outils hétérogènes"


def test_le_reimport_ignore_une_rubrique_qui_n_est_plus_un_axe():
    mission_id = _mission()
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        axes = mission_axes.axes_of(db, mission)
        mission_axes.supprimer_axe(db, mission, axes[1])  # culture_adn
        axes = mission_axes.axes_of(db, mission)
    finally:
        db.close()
    parsed = parse_analysis_markdown(
        "## SYNTHÈSE GLOBALE\n\n### Culture & ADN\n- Une culture\n\n### Contexte\n- Un contexte\n",
        axes,
    )["global_synthesis"]
    assert "culture_adn" not in parsed
    assert parsed["contexte"] == "- Un contexte"


def test_le_prompt_et_le_schema_ia_suivent_les_axes():
    """Onglet « IA intégrée » : le modèle doit se voir demander les rubriques
    de LA mission, sinon il remplit celles qu'elle n'étudie plus et laisse
    vides celles qu'elle a ajoutées."""
    mission_id = _mission()
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        mission_axes.axes_of(db, mission)
        mission_axes.creer_axe(db, mission, "Outillage & données", "les outils en place")
        axes = mission_axes.axes_of(db, mission)
    finally:
        db.close()
    schema = global_schema(axes)
    assert "outillage_donnees" in schema["properties"]
    assert "outillage_donnees" in schema["required"]
    system = global_system(axes)
    # La clé JSON et le libellé sont explicitement appariés : la matière est
    # étiquetée par LIBELLÉ, la réponse attendue par CLÉ — et un axe renommé
    # (« Contexte & historique » ↔ `contexte`) rend cette correspondance
    # non devinable.
    assert "outillage_donnees (rubrique « Outillage & données ») : les outils en place" in system


# --------------------------------------------------------------------------- #
# Écran
# --------------------------------------------------------------------------- #
def test_l_ecran_analyse_ordonne_les_trois_onglets(client):
    """Ordre demandé : 1. Axes d'étude 2. Manuel (export/import) 3. IA intégrée."""
    mission_id = _mission()
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        mission.interviews  # l'écran exige de la matière pour afficher les onglets
    finally:
        db.close()
    from app.models import Interview

    db = SessionLocal()
    try:
        db.add(Interview(mission_id=mission_id, interviewee_name="Témoin", mode="libre"))
        db.commit()
    finally:
        db.close()

    html = unescape(client.get(f"/missions/{mission_id}/synthese/export-import").text)
    positions = [html.index(f'data-tab="{t}"') for t in ("axes-etude", "manuel", "ia")]
    assert positions == sorted(positions)
    # `unescape` : les libellés contiennent des « & » (Culture & ADN), échappés
    # à l'affichage.
    for axe in _axes(mission_id):
        assert axe.label in html
