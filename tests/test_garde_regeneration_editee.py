"""Garde serveur contre l'écrasement des lignes éditées à la main (constat G de
la salle atelier-dev du 2026-09-26, arbitré par l'utilisateur ; ADR
`docs/adr/0001-garde-regeneration-lignes-editees.md`).

Le `confirm()` JavaScript ne protégeait rien côté serveur. Trois listes sont
concernées — indicateurs, risques, grille de maturité — et la garde vit dans
leur patron commun `_generate_liste_view` : une ligne `edite` + pas de
`confirmer=1` ⇒ 400, rien de généré.

Les deux garanties sont testées SÉPARÉMENT, exprès : « sans confirmer, la ligne
éditée survit » et « avec confirmer, elle est bien remplacée » sont deux choses
différentes, et un test unique qui les mélangerait resterait vert sur une garde
qui refuse tout, y compris la demande confirmée.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import (
    GlobalSynthesis,
    Interview,
    Mission,
    MissionDifficulty,
    MissionExecutiveSummary,
    MissionKpi,
    MissionMaturite,
    MissionRisk,
    MissionSwot,
    Question,
    Recommendation,
    RecommendationAxis,
    Theme,
    Trame,
    Verbatim,
)
from app.routers import export as export_routes
from app.routers import synthese as synthese_routes
from app.services import garde_edition
from app.services.synthese_ecriture import apply_kpis_result

RACINE = Path(__file__).resolve().parents[1]


def setup_module() -> None:
    # engine.dispose() AVANT l'unlink : le pool du fichier de test précédent
    # verrouille la base sous Windows (leçon du 2026-09-1x, mémoire projet).
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


def _mission_avec_les_trois_listes(nom: str) -> tuple[int, int, int, int]:
    """Mission prête à régénérer : synthèse globale, un thème de trame (pilier de
    la grille de maturité) et une ligne dans chacune des trois listes."""
    db = SessionLocal()
    try:
        m = Mission(name=nom)
        db.add(m)
        db.flush()
        tr = Trame(mission_id=m.id)
        db.add(tr)
        db.flush()
        db.add(Theme(trame_id=tr.id, title="Gouvernance", position=0))
        db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
        db.add(GlobalSynthesis(
            mission_id=m.id, status="generated", contexte="- C", culture_adn="- C",
            forces_succes="- F", points_amelioration="- Silos", aspirations="- A"))
        m.kpis = [MissionKpi(position=0, libelle="KPI IA", cible="cible IA")]
        m.risks = [MissionRisk(position=0, risque="Risque IA", gravite=2, probabilite=2)]
        m.maturites = [MissionMaturite(position=0, pilier="Gouvernance", score=1,
                                       justification="justif IA")]
        db.commit()
        return m.id, m.kpis[0].id, m.risks[0].id, m.maturites[0].id
    finally:
        db.close()


# (route de génération, route d'autosave, champ édité, valeur écrite,
#  nom du générateur à patcher, résultat qu'il rend)
_CAS = {
    "kpis": ("kpis/generate", "kpis", "cible", "90 % à la main", "generate_kpis",
             [{"libelle": "KPI régénéré", "cible": "neuf", "axe": ""}]),
    "risques": ("risques/generate", "risques", "controle", "Revue à la main",
                "generate_risks",
                [{"risque": "Risque régénéré", "gravite": 2, "probabilite": 2,
                  "controle": "neuf", "controle_type": "propose"}]),
    "maturite": ("maturite/generate", "maturites", "justification", "Justif à la main",
                 "generate_maturite",
                 [{"pilier": "Gouvernance", "score": 3, "justification": "neuf"}]),
}


def _editer(client: TestClient, liste: str, mid: int, ligne_id: int,
            champ: str, valeur: str) -> None:
    """Édition à la main PAR LA ROUTE d'autosave : c'est elle qui pose `edite`,
    pas le test — sinon on testerait le drapeau et non le chemin réel."""
    r = client.post(f"/{liste}/{ligne_id}/field",
                    data={"mission_id": mid, "field": champ, "value": valeur})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("cas", list(_CAS), ids=list(_CAS))
def test_regeneration_sans_confirmer_refuse_et_garde_la_ligne_editee(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, cas: str
) -> None:
    """Sans `confirmer`, la régénération est REFUSÉE en 400 : la ligne éditée est
    intacte en base, l'écran annonce le nombre de lignes en jeu et porte le bouton
    qui repose la demande confirmée. Le générateur ne doit même pas être appelé."""
    generate_route, liste, champ, valeur, nom_generateur, items = _CAS[cas]
    mid, kid, rid, maid = _mission_avec_les_trois_listes(f"G sans confirmer {cas}")
    ligne_id = {"kpis": kid, "risques": rid, "maturites": maid}[liste]
    _editer(client, liste, mid, ligne_id, champ, valeur)

    appels = []
    # Patché sur le ROUTEUR : export.py importe les générateurs par leur nom au
    # chargement, patcher le module de service ne changerait rien.
    monkeypatch.setattr(export_routes, nom_generateur,
                        lambda *a, **k: appels.append(1) or items)
    r = client.post(f"/missions/{mid}/{generate_route}")

    assert r.status_code == 400, r.status_code
    assert "1 ligne éditée à la main sera remplacée" in r.text
    assert 'name="confirmer" value="1"' in r.text
    assert appels == [], "le générateur a été appelé alors que rien ne devait l'être"
    db = SessionLocal()
    try:
        ligne = db.get({"kpis": MissionKpi, "risques": MissionRisk,
                        "maturites": MissionMaturite}[liste], ligne_id)
        assert ligne is not None, "la ligne éditée a été supprimée malgré le refus"
        assert getattr(ligne, champ) == valeur
        assert ligne.edite is True
    finally:
        db.close()


@pytest.mark.parametrize("cas", list(_CAS), ids=list(_CAS))
def test_regeneration_avec_confirmer_remplace_bien_la_ligne_editee(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, cas: str
) -> None:
    """Avec `confirmer=1`, la garde s'écarte : la génération a lieu et la ligne
    éditée est REMPLACÉE. Garantie distincte de la précédente — une garde qu'on ne
    peut jamais franchir est aussi cassée qu'une garde qui ne se déclenche pas."""
    generate_route, liste, champ, valeur, nom_generateur, items = _CAS[cas]
    mid, kid, rid, maid = _mission_avec_les_trois_listes(f"G avec confirmer {cas}")
    ligne_id = {"kpis": kid, "risques": rid, "maturites": maid}[liste]
    _editer(client, liste, mid, ligne_id, champ, valeur)

    monkeypatch.setattr(export_routes, nom_generateur, lambda *a, **k: items)
    r = client.post(f"/missions/{mid}/{generate_route}", data={"confirmer": "1"})

    assert r.status_code == 200, r.status_code
    assert "sera remplacée" not in r.text
    modele = {"kpis": MissionKpi, "risques": MissionRisk,
              "maturites": MissionMaturite}[liste]
    db = SessionLocal()
    try:
        assert db.get(modele, ligne_id) is None, "l'ancienne ligne survit à la confirmation"
        lignes = getattr(db.get(Mission, mid), {"kpis": "kpis", "risques": "risks",
                                               "maturites": "maturites"}[liste])
        assert [getattr(x, champ) for x in lignes] == ["neuf"]
    finally:
        db.close()


@pytest.mark.parametrize("cas", list(_CAS), ids=list(_CAS))
def test_un_autosave_sans_changement_ne_marque_pas_la_ligne(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, cas: str
) -> None:
    """Le drapeau ne se pose QUE sur un vrai changement (reprise de revue).

    Les autosaves se déclenchent aussi sur `blur` — le modificateur `changed` de
    `hx-trigger` ne porte que sur `keyup` — donc parcourir l'écran au clavier
    reposte les valeurs inchangées. Marquer là-dessus annonçait « 8 lignes éditées
    seront remplacées » à qui n'avait rien touché, et une alerte qui crie pour rien
    n'est plus lue le jour où elle a raison. Tous les autres tests postent un
    changement réel : c'est pourquoi aucun ne voyait ce défaut."""
    generate_route, liste, champ, _valeur, nom_generateur, items = _CAS[cas]
    mid, kid, rid, maid = _mission_avec_les_trois_listes(f"G sans changement {cas}")
    ligne_id = {"kpis": kid, "risques": rid, "maturites": maid}[liste]
    modele = {"kpis": MissionKpi, "risques": MissionRisk, "maturites": MissionMaturite}[liste]
    db = SessionLocal()
    try:
        deja = getattr(db.get(modele, ligne_id), champ)
    finally:
        db.close()

    # Repost à l'IDENTIQUE de ce que la ligne porte déjà.
    _editer(client, liste, mid, ligne_id, champ, str(deja))

    db = SessionLocal()
    try:
        assert db.get(modele, ligne_id).edite is False
    finally:
        db.close()
    # Conséquence visible : la régénération ne demande rien.
    monkeypatch.setattr(export_routes, nom_generateur, lambda *a, **k: items)
    r = client.post(f"/missions/{mid}/{generate_route}")
    assert r.status_code == 200
    assert "sera remplacée" not in r.text


def test_la_garde_ne_fait_pas_confirmer_une_generation_impossible(
    client: TestClient
) -> None:
    """La garde vient APRÈS la précondition IA (reprise de revue) : sans synthèse
    globale, la génération est de toute façon refusée — faire confirmer le
    remplacement d'une ligne éditée pour répondre ensuite « générez d'abord la
    synthèse globale » ferait valider une action impossible."""
    db = SessionLocal()
    try:
        m = Mission(name="G sans synthese globale")
        db.add(m)
        db.flush()
        db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
        m.kpis = [MissionKpi(position=0, libelle="KPI IA", cible="cible IA")]
        db.commit()
        mid, kid = m.id, m.kpis[0].id
    finally:
        db.close()
    _editer(client, "kpis", mid, kid, "cible", "90 % à la main")

    r = client.post(f"/missions/{mid}/kpis/generate")

    # Aucune demande de confirmation : la garde ne s'est pas interposée. (Le
    # message de précondition n'atteint pas cet écran-là — sans matière, l'aperçu
    # rend son état vide et n'affiche pas le bloc d'erreur ; ce qui se vérifie ici
    # est l'ABSENCE de la garde, et que rien n'a été généré.)
    assert "sera remplacée" not in r.text
    assert 'name="confirmer"' not in r.text
    db = SessionLocal()
    try:
        ligne = db.get(MissionKpi, kid)
        assert (ligne.cible, ligne.edite) == ("90 % à la main", True)
    finally:
        db.close()


def test_le_patron_de_generation_n_a_pas_de_mode_sans_garde() -> None:
    """Contrat de couture (reprise de revue) : `_generate_liste_view` EXIGE une
    surface déclarée gardée. Le paramètre optionnel d'avant tombait en marche —
    une quatrième liste branchée dessus n'aurait eu aucune garde, en silence.

    Depuis l'extension du 2026-09-27 le registre couvre les HUIT surfaces
    régénérables et vit dans `app/services/garde_edition.py` ; `surface` y est un
    mot-clé obligatoire, là aussi sans défaut."""
    from inspect import Parameter, signature

    params = signature(export_routes._generate_liste_view).parameters
    assert params["surface"].default is params["surface"].empty
    assert set(garde_edition.SURFACES) == {
        "kpis", "risks", "maturites", "difficulties", "recommendations",
        "swot", "executive_summary", "global_synthesis",
    }
    with pytest.raises(KeyError):
        garde_edition.SURFACES["une_neuvieme_surface"]

    p_surface = signature(garde_edition.garde_regeneration).parameters["surface"]
    assert p_surface.kind is Parameter.KEYWORD_ONLY
    assert p_surface.default is p_surface.empty
    with pytest.raises(KeyError):
        garde_edition.garde_regeneration(object(), surface="inconnue", confirmer=False)


# --------------------------------------------------------------------------- #
# Extension aux CINQ autres surfaces régénérables (arbitrage utilisateur du
# 2026-09-27, `VSCode2:garde-regeneration-limitee-a-3-surfaces-sur-8`).
# Trois formes de marqueur, testées chacune par le chemin réel (autosave HTTP,
# puis route de génération) : drapeau par ligne (difficultés), drapeau sur
# l'arbre axes/recommandations, et `status == "edited"` pour les enregistrements
# uniques (SWOT, executive summary) — la synthèse globale, dont la génération est
# une tâche de fond rendue en fragment HTMX, a ses deux tests dédiés plus bas.
# --------------------------------------------------------------------------- #
def _mission_complete(nom: str) -> dict:
    """Mission prête à régénérer N'IMPORTE LAQUELLE des surfaces : synthèse
    globale avec contenu, une difficulté, un axe portant une recommandation, une
    SWOT et un executive summary déjà générés."""
    db = SessionLocal()
    try:
        m = Mission(name=nom)
        db.add(m)
        db.flush()
        tr = Trame(mission_id=m.id)
        db.add(tr)
        db.flush()
        db.add(Theme(trame_id=tr.id, title="Gouvernance", position=0))
        db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
        db.add(GlobalSynthesis(
            mission_id=m.id, status="generated", contexte="- C", culture_adn="- C",
            forces_succes="- F", points_amelioration="- Silos", aspirations="- A"))
        m.difficulties = [MissionDifficulty(position=0, label="Difficulté IA")]
        axe = RecommendationAxis(mission_id=m.id, title="Axe IA", position=0)
        db.add(axe)
        db.flush()
        reco = Recommendation(axis_id=axe.id, position=0, title="Reco IA",
                              objectif="objectif IA")
        db.add(reco)
        db.add(MissionSwot(mission_id=m.id, status="generated", forces="- F IA",
                           faiblesses="- f", opportunites="- o", menaces="- m"))
        db.add(MissionExecutiveSummary(mission_id=m.id, status="generated",
                                       headline="Constat IA", points="- p",
                                       key_message="message IA"))
        db.commit()
        return {"mid": m.id, "difficulte": m.difficulties[0].id, "axe": axe.id,
                "reco": reco.id}
    finally:
        db.close()


_RECO_GENEREE = [{"title": "Axe régénéré",
                  "recommendations": [{"title": "Reco régénérée",
                                       "objectif": "objectif neuf"}]}]

# Les CINQ surfaces ajoutées, décrites par leur chemin RÉEL :
#  module à patcher, nom du générateur, ce qu'il rend, route de génération,
#  requête d'autosave (url, données), sonde qui relit l'état édité en base.
_NOUVELLES = {
    "difficultes": {
        "module": lambda: export_routes,
        "generateur": "generate_difficulties",
        "resultat": ["Difficulté régénérée"],
        "route": "difficultes/generate",
        "autosave": lambda ids: (f"/difficultes/{ids['difficulte']}/field",
                                 {"mission_id": ids["mid"], "value": "Difficulté à la main"}),
        "editee": lambda db, ids: db.get(MissionDifficulty, ids["difficulte"]).label,
        "attendu": "Difficulté à la main",
        "message": "1 ligne éditée à la main sera remplacée",
        "apres": lambda db, ids: [d.label for d in db.get(Mission, ids["mid"]).difficulties],
        "regenere": ["Difficulté régénérée"],
    },
    "recommandations": {
        "module": lambda: synthese_routes,
        "generateur": "generate_recommendations",
        "resultat": _RECO_GENEREE,
        "route": "recommandations/generate",
        "autosave": lambda ids: (f"/recommandations/{ids['reco']}/field",
                                 {"field": "title", "value": "Reco à la main"}),
        "editee": lambda db, ids: db.get(Recommendation, ids["reco"]).title,
        "attendu": "Reco à la main",
        "message": "1 élément édité à la main sera remplacé",
        "apres": lambda db, ids: [r.title for a in db.get(Mission, ids["mid"]).recommendation_axes
                                  for r in a.recommendations],
        "regenere": ["Reco régénérée"],
    },
    "swot": {
        "module": lambda: export_routes,
        "generateur": "generate_swot",
        "resultat": {"forces": "- F neuf", "faiblesses": "- f neuf",
                     "opportunites": "- o neuf", "menaces": "- m neuf"},
        "route": "swot/generate",
        "autosave": lambda ids: (f"/swot/{ids['mid']}/field",
                                 {"field": "forces", "value": "- F à la main"}),
        "editee": lambda db, ids: db.get(Mission, ids["mid"]).swot.forces,
        "attendu": "- F à la main",
        "message": "La SWOT porte des modifications faites à la main",
        "apres": lambda db, ids: [db.get(Mission, ids["mid"]).swot.forces],
        "regenere": ["- F neuf"],
    },
    "executive-summary": {
        "module": lambda: export_routes,
        "generateur": "generate_executive_summary",
        "resultat": {"headline": "Constat neuf", "points": "- p neuf",
                     "key_message": "message neuf"},
        "route": "executive-summary/generate",
        "autosave": lambda ids: (f"/executive-summary/{ids['mid']}/field",
                                 {"field": "headline", "value": "Constat à la main"}),
        "editee": lambda db, ids: db.get(Mission, ids["mid"]).executive_summary.headline,
        "attendu": "Constat à la main",
        "message": "executive summary porte des modifications faites à la main",
        "apres": lambda db, ids: [db.get(Mission, ids["mid"]).executive_summary.headline],
        "regenere": ["Constat neuf"],
    },
}


def _autosave(client: TestClient, cas: dict, ids: dict) -> None:
    url, data = cas["autosave"](ids)
    r = client.post(url, data=data)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("nom", list(_NOUVELLES), ids=list(_NOUVELLES))
def test_nouvelle_surface_sans_confirmer_refuse_et_garde_l_edition(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, nom: str
) -> None:
    """Sans `confirmer`, la régénération est REFUSÉE en 400 : le contenu édité à la
    main est intact en base, l'écran porte le bouton de confirmation, et le
    générateur n'est même pas appelé."""
    cas = _NOUVELLES[nom]
    ids = _mission_complete(f"garde sans confirmer {nom}")
    _autosave(client, cas, ids)

    appels = []
    monkeypatch.setattr(cas["module"](), cas["generateur"],
                        lambda *a, **k: appels.append(1) or cas["resultat"])
    r = client.post(f"/missions/{ids['mid']}/{cas['route']}")

    assert r.status_code == 400, r.status_code
    assert cas["message"] in r.text
    assert 'name="confirmer" value="1"' in r.text
    assert appels == [], "le générateur a été appelé alors que rien ne devait l'être"
    db = SessionLocal()
    try:
        assert cas["editee"](db, ids) == cas["attendu"]
    finally:
        db.close()


@pytest.mark.parametrize("nom", list(_NOUVELLES), ids=list(_NOUVELLES))
def test_nouvelle_surface_avec_confirmer_remplace_bien_l_edition(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, nom: str
) -> None:
    """Avec `confirmer=1`, la garde s'écarte et la génération REMPLACE le contenu
    édité. Garantie distincte de la précédente : une garde qu'on ne peut jamais
    franchir est aussi cassée qu'une garde qui ne se déclenche pas."""
    cas = _NOUVELLES[nom]
    ids = _mission_complete(f"garde avec confirmer {nom}")
    _autosave(client, cas, ids)

    monkeypatch.setattr(cas["module"](), cas["generateur"], lambda *a, **k: cas["resultat"])
    r = client.post(f"/missions/{ids['mid']}/{cas['route']}", data={"confirmer": "1"})

    assert r.status_code == 200, r.status_code
    assert 'name="confirmer" value="1"' not in r.text
    db = SessionLocal()
    try:
        assert cas["apres"](db, ids) == cas["regenere"]
    finally:
        db.close()


@pytest.mark.parametrize("nom", list(_NOUVELLES), ids=list(_NOUVELLES))
def test_nouvelle_surface_autosave_sans_changement_ne_marque_rien(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, nom: str
) -> None:
    """Même règle que les trois listes d'origine : le marqueur ne se pose que sur un
    VRAI changement. Les autosaves partent aussi sur `blur`, donc parcourir l'écran
    au clavier reposte des valeurs identiques — la garde ne doit pas crier pour
    rien, drapeau `edite` comme `status == "edited"`."""
    cas = _NOUVELLES[nom]
    ids = _mission_complete(f"garde sans changement {nom}")
    url, data = cas["autosave"](ids)
    db = SessionLocal()
    try:
        deja = cas["editee"](db, ids)
    finally:
        db.close()

    # Repost à l'IDENTIQUE de ce que la surface porte déjà.
    r = client.post(url, data={**data, "value": deja})
    assert r.status_code == 200, r.text

    monkeypatch.setattr(cas["module"](), cas["generateur"], lambda *a, **k: cas["resultat"])
    r = client.post(f"/missions/{ids['mid']}/{cas['route']}")
    assert r.status_code == 200, r.status_code
    assert 'name="confirmer" value="1"' not in r.text


def test_axe_de_recommandation_edite_seul_declenche_la_garde(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'arbre des recommandations porte DEUX autosaves distincts : l'intitulé d'un
    axe et les champs d'une recommandation. Marquer la seule recommandation aurait
    laissé un axe renommé à la main se faire écraser en silence — l'axe compte."""
    ids = _mission_complete("garde axe reco seul")
    r = client.post(f"/recommandations/axes/{ids['axe']}/field",
                    data={"field": "title", "value": "Axe à la main"})
    assert r.status_code == 200, r.text

    appels = []
    monkeypatch.setattr(synthese_routes, "generate_recommendations",
                        lambda *a, **k: appels.append(1) or _RECO_GENEREE)
    r = client.post(f"/missions/{ids['mid']}/recommandations/generate")

    assert r.status_code == 400
    assert "1 élément édité à la main sera remplacé" in r.text
    assert appels == []
    db = SessionLocal()
    try:
        assert db.get(RecommendationAxis, ids["axe"]).title == "Axe à la main"
    finally:
        db.close()


def test_verbatim_lie_a_la_main_declenche_la_garde(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lier un verbatim à une difficulté est un travail à la main que la
    régénération perd (les difficultés neuves naissent sans lien) : il marque la
    ligne au même titre qu'un libellé retouché."""
    ids = _mission_complete("garde verbatim lie")
    r = client.post(f"/difficultes/{ids['difficulte']}/verbatim",
                    data={"mission_id": ids["mid"], "verbatim_id": ""})
    assert r.status_code == 200, r.text
    # Délier un lien DÉJÀ absent ne change rien : rien ne doit être marqué.
    db = SessionLocal()
    try:
        assert db.get(MissionDifficulty, ids["difficulte"]).edite is False
    finally:
        db.close()

    vid = _un_verbatim(ids["mid"])
    r = client.post(f"/difficultes/{ids['difficulte']}/verbatim",
                    data={"mission_id": ids["mid"], "verbatim_id": str(vid)})
    assert r.status_code == 200, r.text

    monkeypatch.setattr(export_routes, "generate_difficulties",
                        lambda *a, **k: ["Difficulté régénérée"])
    r = client.post(f"/missions/{ids['mid']}/difficultes/generate")
    assert r.status_code == 400
    assert "1 ligne éditée à la main sera remplacée" in r.text


def _un_verbatim(mission_id: int) -> int:
    """Un verbatim réel de la mission (le lien est validé contre eux)."""
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        interview = mission.interviews[0]
        theme = mission.trame.themes[0]
        q = Question(theme_id=theme.id, label="Comment décidez-vous ?", position=0)
        db.add(q)
        db.flush()
        v = Verbatim(interview_id=interview.id, question_id=q.id,
                     quote="On manque de visibilité.")
        db.add(v)
        db.commit()
        return v.id
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Synthèse globale : même garde, mais génération en TÂCHE DE FOND rendue dans un
# fragment HTMX — d'où un 200 (et non un 400) documenté dans la route et l'ADR.
# --------------------------------------------------------------------------- #
def _globale_prete(monkeypatch: pytest.MonkeyPatch) -> list:
    """Préconditions de `generate_global` satisfaites, et la tâche de fond REMPLACÉE
    par un mouchard : aucun map-reduce IA ne doit partir d'un test."""
    lancees = []
    monkeypatch.setattr(synthese_routes, "is_configured", lambda: True)
    monkeypatch.setattr(synthese_routes, "_total_answer_count", lambda _m: 3)
    monkeypatch.setattr(synthese_routes, "run_global_synthesis_job",
                        lambda mid: lancees.append(mid))
    return lancees


def test_synthese_globale_sans_confirmer_ne_lance_rien(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthèse éditée à la main (`status == "edited"`) et pas de `confirmer` :
    AUCUNE tâche de fond lancée, aucun jeton pris (`generation_status` intact), le
    texte édité intact — et le panneau porte la demande de confirmation.

    La réponse est un 200 : ce panneau est un fragment HTMX (`hx-swap`), et htmx
    2.0.3 n'échange RIEN sur un 4xx — un 400 ici rendrait le refus INVISIBLE."""
    ids = _mission_complete("garde globale sans confirmer")
    r = client.post(f"/syntheses/globale/{ids['mid']}/field",
                    data={"field": "contexte", "value": "- Contexte à la main"})
    assert r.status_code == 200, r.text
    lancees = _globale_prete(monkeypatch)

    r = client.post(f"/missions/{ids['mid']}/synthese/globale/generate")

    assert r.status_code == 200, r.status_code
    assert "La synthèse globale porte des modifications faites à la main" in r.text
    assert '"confirmer": "1"' in r.text
    # Le bouton repose sur CETTE route (`request.url.path`, comme les deux autres
    # dicts `confirmation`) : assertion vraie avant comme après ce refactor, c'est
    # justement ce qui établit que le chemin rendu est identique.
    assert f'hx-post="/missions/{ids["mid"]}/synthese/globale/generate"' in r.text
    assert lancees == [], "une génération a été lancée alors que rien ne devait l'être"
    db = SessionLocal()
    try:
        gs = db.get(Mission, ids["mid"]).global_synthesis
        assert gs.contenu("contexte") == "- Contexte à la main"
        assert gs.status == "edited"
        assert gs.generation_status == "idle"
    finally:
        db.close()


def test_synthese_globale_avec_confirmer_lance_bien_la_generation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Avec `confirmer=1`, la garde s'écarte : le jeton est pris (`running`) et la
    tâche de fond est bien lancée. Garantie distincte du refus."""
    ids = _mission_complete("garde globale avec confirmer")
    r = client.post(f"/syntheses/globale/{ids['mid']}/field",
                    data={"field": "contexte", "value": "- Contexte à la main"})
    assert r.status_code == 200, r.text
    lancees = _globale_prete(monkeypatch)

    r = client.post(f"/missions/{ids['mid']}/synthese/globale/generate",
                    data={"confirmer": "1"})

    assert r.status_code == 200, r.status_code
    assert "porte des modifications faites à la main" not in r.text
    assert lancees == [ids["mid"]]
    db = SessionLocal()
    try:
        assert db.get(Mission, ids["mid"]).global_synthesis.generation_status == "running"
    finally:
        db.close()


def test_synthese_globale_seulement_generee_ne_demande_rien(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une synthèse `generated` jamais retouchée se régénère sans confirmation : la
    garde ne protège que le travail à la main (le `hx-confirm` du bouton couvre,
    lui, le cas d'un contenu généré non édité)."""
    ids = _mission_complete("garde globale non editee")
    lancees = _globale_prete(monkeypatch)

    r = client.post(f"/missions/{ids['mid']}/synthese/globale/generate")

    assert r.status_code == 200
    assert "porte des modifications faites à la main" not in r.text
    assert lancees == [ids["mid"]]


def test_regeneration_remet_le_drapeau_edite_a_faux_sans_code(client: TestClient) -> None:
    """Fait PORTEUR de la décision (plan de la salle, G) : le drapeau retombe à la
    recréation delete-orphan des `apply_*_result`, SANS une ligne de code dans
    `synthese_ecriture.py` — les objets neufs naissent au défaut du modèle. Si ce
    test rougit, c'est cette hypothèse qui tombe, pas la garde."""
    mid, kid, _rid, _maid = _mission_avec_les_trois_listes("G drapeau remis a zero")
    _editer(client, "kpis", mid, kid, "cible", "90 % à la main")
    db = SessionLocal()
    try:
        assert db.get(MissionKpi, kid).edite is True
        mission = db.get(Mission, mid)
        apply_kpis_result(mission, [{"libelle": "KPI régénéré", "cible": "neuf"}])
        db.commit()
        assert [x.edite for x in mission.kpis] == [False]
    finally:
        db.close()


_SONDE_BASE_ANCIENNE = r"""
from app.db import SessionLocal, _add_missing_columns, engine, init_db
from app.models import Mission

init_db()
db = SessionLocal()
m = Mission(name="Ancienne")
db.add(m); db.commit()
mid = m.id
db.close()
engine.dispose()

# Base ANCIENNE : la colonne n'existait pas quand la ligne a ete editee.
with engine.begin() as conn:
    conn.exec_driver_sql("ALTER TABLE mission_kpis DROP COLUMN edite")
    conn.exec_driver_sql(
        "INSERT INTO mission_kpis (mission_id, position, libelle, cible, axe)"
        " VALUES (%d, 0, 'KPI ancien', 'cible editee a la main', '')" % mid)
    sans = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(mission_kpis)")]
assert "edite" not in sans, sans

# La migration additive rattrape la colonne, sans reecrire la table.
_add_missing_columns()
with engine.begin() as conn:
    apres = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(mission_kpis)")]
    brut = list(conn.exec_driver_sql("SELECT cible, edite FROM mission_kpis"))
print(repr(("edite" in apres, brut)))
"""


def test_migration_base_ancienne_relit_les_lignes_comme_non_editees(tmp_path) -> None:
    """Migration ADDITIVE sur une base d'avant le drapeau : la colonne est ajoutée
    au démarrage, la ligne éditée de longue date survit, et elle se relit
    `edite = 0` — la LIMITE assumée de l'ADR, testée pour qu'elle reste un choix
    écrit et non une surprise. Sous-processus avec son propre `APP_DB_PATH` : la
    base réelle n'est jamais migrée par un test."""
    import os
    base = tmp_path / "ancienne.db"
    env = dict(os.environ, APP_DB_PATH=str(base), PYTHONUTF8="1")
    res = subprocess.run([sys.executable, "-c", _SONDE_BASE_ANCIENNE], cwd=RACINE,
                         env=env, capture_output=True, text=True, timeout=120,
                         encoding="utf-8")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().splitlines()[-1] == repr(
        (True, [("cible editee a la main", 0)]))


_SONDE_NOUVELLES_COLONNES = r"""
from app.db import SessionLocal, _add_missing_columns, engine, init_db
from app.models import Mission, RecommendationAxis

init_db()
db = SessionLocal()
m = Mission(name="Ancienne")
db.add(m); db.commit()
a = RecommendationAxis(mission_id=m.id, title="Axe ancien", position=0)
db.add(a); db.commit()
mid, aid = m.id, a.id
db.close()
engine.dispose()

TABLES = ("mission_difficulties", "recommendation_axes", "recommendations")

# Base ANCIENNE : les 3 colonnes n'existaient pas quand les lignes ont ete editees.
with engine.begin() as conn:
    for t in TABLES:
        conn.exec_driver_sql("ALTER TABLE %s DROP COLUMN edite" % t)
    conn.exec_driver_sql(
        "INSERT INTO mission_difficulties (mission_id, position, label)"
        " VALUES (%d, 0, 'difficulte editee a la main')" % mid)
    conn.exec_driver_sql(
        "INSERT INTO recommendations (axis_id, position, title, objectif,"
        " acteurs, proposition_valeur, plan_actions, resultats_attendus,"
        " valeur, complexite) VALUES (%d, 0, 'reco editee a la main',"
        " '', '', '', '', '', 3, 3)" % aid)
    sans = {t: [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(%s)" % t)]
            for t in TABLES}
assert all("edite" not in cols for cols in sans.values()), sans

# La migration additive rattrape les 3 colonnes, sans reecrire les tables.
_add_missing_columns()
with engine.begin() as conn:
    apres = all("edite" in [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(%s)" % t)]
                for t in TABLES)
    brut = (list(conn.exec_driver_sql("SELECT label, edite FROM mission_difficulties"))
            + list(conn.exec_driver_sql("SELECT title, edite FROM recommendation_axes"))
            + list(conn.exec_driver_sql("SELECT title, edite FROM recommendations")))
print(repr((apres, brut)))
"""


def test_migration_ajoute_les_colonnes_des_nouvelles_surfaces(tmp_path) -> None:
    """Mêmes garanties que pour les trois listes d'origine, sur les trois colonnes
    ajoutées le 2026-09-27 (difficultés, axes, recommandations) : ajout ADDITIF au
    démarrage, lignes anciennes conservées, relues `edite = 0` (limite assumée de
    l'ADR). Sous-processus avec son propre `APP_DB_PATH` : la base réelle n'est
    jamais migrée par un test."""
    import os
    base = tmp_path / "ancienne_nouvelles.db"
    env = dict(os.environ, APP_DB_PATH=str(base), PYTHONUTF8="1")
    res = subprocess.run([sys.executable, "-c", _SONDE_NOUVELLES_COLONNES], cwd=RACINE,
                         env=env, capture_output=True, text=True, timeout=120,
                         encoding="utf-8")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().splitlines()[-1] == repr((True, [
        ("difficulte editee a la main", 0),
        ("Axe ancien", 0),
        ("reco editee a la main", 0),
    ]))


def _mission_sans_entretien_avec_kpis_edites(nom: str) -> tuple[int, int]:
    """Mission SANS aucun entretien, mais qui porte des indicateurs édités.

    Cas réel : les indicateurs viennent d'un import ou d'une génération faite
    quand la mission portait encore des entretiens, retirés depuis. L'écran
    d'aperçu bascule alors sur sa case « Aucun entretien pour l'instant » —
    mais les routes de génération, elles, répondent toujours.
    """
    db = SessionLocal()
    try:
        m = Mission(name=nom)
        db.add(m)
        db.flush()
        db.add(GlobalSynthesis(
            mission_id=m.id, status="generated", contexte="- C", culture_adn="- C",
            forces_succes="- F", points_amelioration="- Silos", aspirations="- A"))
        m.kpis = [MissionKpi(position=0, libelle="KPI IA", cible="cible IA", edite=True)]
        db.commit()
        return m.id, m.kpis[0].id
    finally:
        db.close()


def test_refus_de_garde_visible_meme_sans_entretien(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le refus de la garde doit RESTER LISIBLE sur une mission sans entretien.

    `apercu.html` enfermait `{% if error %}` et `{% if confirmation %}` dans la
    branche `else` de sa case « Aucun entretien pour l'instant ». Le 400 de la
    garde rendait donc un écran qui ne disait NI pourquoi il refusait, NI comment
    passer outre : une impasse, sur le scénario même (requête directe, double
    envoi, onglet périmé) que la garde prétend couvrir. Les deux blocs sont
    désormais hors de la case, donc rendus dans les deux branches.
    """
    mid, kid = _mission_sans_entretien_avec_kpis_edites("Garde sans entretien")
    appels = []
    monkeypatch.setattr(export_routes, "generate_kpis",
                        lambda *a, **k: appels.append(1) or [])

    r = client.post(f"/missions/{mid}/kpis/generate")

    assert r.status_code == 400, r.status_code
    # La case « aucun entretien » est bien celle qui est rendue...
    assert "Aucun entretien pour l'instant" in r.text
    # ...et pourtant le refus est lisible ET franchissable.
    assert "1 ligne éditée à la main sera remplacée" in r.text
    assert 'name="confirmer" value="1"' in r.text
    assert appels == []

    # Et le bouton de confirmation rendu là fonctionne vraiment.
    monkeypatch.setattr(export_routes, "generate_kpis",
                        lambda *a, **k: [{"libelle": "KPI régénéré", "cible": "neuf",
                                          "axe": ""}])
    r2 = client.post(f"/missions/{mid}/kpis/generate", data={"confirmer": "1"})
    assert r2.status_code == 200, r2.status_code
    db = SessionLocal()
    try:
        assert db.get(MissionKpi, kid) is None
        assert [k.cible for k in db.get(Mission, mid).kpis] == ["neuf"]
    finally:
        db.close()


@pytest.mark.parametrize("valeur", ["2", "oui", "", "0", "true"])
def test_confirmer_non_explicite_rend_l_ecran_de_la_garde_pas_un_json_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, valeur: str
) -> None:
    """`confirmer: bool = Form(False)` faisait répondre à FastAPI un 422 JSON brut
    (`bool_parsing`) sur `confirmer=2` ou `confirmer=oui` — une page JSON montrée à
    un consultant. Seul `confirmer=1` (la valeur que pose le bouton) vaut
    confirmation : toute autre valeur est un refus ordinaire, rendu par la garde,
    et rien n'est généré. `true` compris : pydantic l'acceptait, la garde non —
    la direction sûre, une confirmation qui ne ressemble pas au bouton n'en est pas une."""
    mid, _kid = _mission_sans_entretien_avec_kpis_edites(f"Garde confirmer={valeur!r}")
    appels = []
    monkeypatch.setattr(export_routes, "generate_kpis",
                        lambda *a, **k: appels.append(1) or [])

    r = client.post(f"/missions/{mid}/kpis/generate", data={"confirmer": valeur})

    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert "text/html" in r.headers["content-type"], r.headers["content-type"]
    assert "1 ligne éditée à la main sera remplacée" in r.text
    assert appels == []


def test_confirmer_non_explicite_sur_les_recommandations(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Même règle sur la surface servie par `synthese.py` : un seul parseur pour les
    huit routes, pas huit copies."""
    ids = _mission_complete("garde reco confirmer=2")
    r = client.post(f"/recommandations/axes/{ids['axe']}/field",
                    data={"field": "title", "value": "Axe à la main"})
    assert r.status_code == 200, r.text
    appels = []
    monkeypatch.setattr(synthese_routes, "generate_recommendations",
                        lambda *a, **k: appels.append(1) or _RECO_GENEREE)

    r = client.post(f"/missions/{ids['mid']}/recommandations/generate",
                    data={"confirmer": "2"})
    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert "1 élément édité à la main sera remplacé" in r.text
    assert appels == []

    r = client.post(f"/missions/{ids['mid']}/recommandations/generate",
                    data={"confirmer": "1"})
    assert r.status_code == 200, r.status_code
    assert appels == [1]


# --------------------------------------------------------------------------- #
# Un résultat IA ENTIÈREMENT VIDE n'écrase rien (2026-09-27, ajout arbitré).
#
# Les listes appliquaient déjà cette règle (`_generate_liste_view` : « aucun
# item -> liste inchangée »). Les trois surfaces de TEXTE, elles, écrivaient
# sans condition et reposaient `status = "generated"` : un modèle muet effaçait
# le texte écrit à la main ET désarmait la garde pour la fois suivante —
# `_clean_global` / `_clean_swot` rendent toujours toutes les clés, `""`
# comprise, donc rien en amont ne filtrait.
#
# Règle retenue : vide = TOUS les champs blancs. « Au moins un champ blanc »
# bloquerait des générations légitimes (une SWOT sans menaces, un executive
# summary sans key_message). Chaque surface est donc testée DEUX fois : le
# résultat vide ne touche à rien, et le résultat non vide remplace toujours.
# --------------------------------------------------------------------------- #

_VIDE = {
    "swot": ("swot/generate", "generate_swot",
             {"forces": "", "faiblesses": "", "opportunites": "", "menaces": ""},
             {"forces": "- Force neuve", "faiblesses": "- f", "opportunites": "- o",
              "menaces": "- m"},
             "SWOT inchangée"),
    "executive_summary": ("executive-summary/generate", "generate_executive_summary",
                          {"headline": "", "points": "", "key_message": "   "},
                          {"headline": "Constat neuf", "points": "- p",
                           "key_message": "message"},
                          "executive summary inchangé"),
}


def _texte_edite(db, mid: int, surface: str) -> tuple[str, str]:
    m = db.get(Mission, mid)
    obj = m.swot if surface == "swot" else m.executive_summary
    champ = "forces" if surface == "swot" else "headline"
    return getattr(obj, champ), obj.status


@pytest.mark.parametrize("surface", list(_VIDE), ids=list(_VIDE))
def test_resultat_vide_n_ecrase_pas_le_texte_ecrit_a_la_main(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    """Résultat IA entièrement vide : le texte écrit à la main SURVIT, `status`
    reste `edited` (la garde reste armée), et l'écran le DIT."""
    route, generateur, vide, _plein, message = _VIDE[surface]
    ids = _mission_complete(f"vide {surface}")
    champ, valeur = (("forces", "- Force écrite à la main") if surface == "swot"
                     else ("headline", "Constat écrit à la main"))
    prefixe = "swot" if surface == "swot" else "executive-summary"
    r = client.post(f"/{prefixe}/{ids['mid']}/field",
                    data={"field": champ, "value": valeur})
    assert r.status_code == 200, r.text

    monkeypatch.setattr(export_routes, generateur, lambda *a, **k: vide)
    # `confirmer=1` : c'est bien APRÈS la garde qu'on se place, sur le chemin
    # « le consultant a accepté de perdre son texte, et il le perd pour rien ».
    r = client.post(f"/missions/{ids['mid']}/{route}", data={"confirmer": "1"})

    assert r.status_code == 200, r.status_code
    assert message in r.text, r.text[:400]
    db = SessionLocal()
    try:
        assert _texte_edite(db, ids["mid"], surface) == (valeur, "edited")
    finally:
        db.close()


@pytest.mark.parametrize("surface", list(_VIDE), ids=list(_VIDE))
def test_resultat_non_vide_remplace_toujours_normalement(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    """L'autre moitié, celle qu'on oublie : une génération légitime écrit bien.
    Une règle qui bloquerait les vraies générations serait pire que le défaut."""
    route, generateur, _vide, plein, message = _VIDE[surface]
    ids = _mission_complete(f"plein {surface}")
    champ, valeur = (("forces", "- Force écrite à la main") if surface == "swot"
                     else ("headline", "Constat écrit à la main"))
    prefixe = "swot" if surface == "swot" else "executive-summary"
    client.post(f"/{prefixe}/{ids['mid']}/field", data={"field": champ, "value": valeur})

    monkeypatch.setattr(export_routes, generateur, lambda *a, **k: plein)
    r = client.post(f"/missions/{ids['mid']}/{route}", data={"confirmer": "1"})

    assert r.status_code == 200, r.status_code
    assert message not in r.text
    attendu = plein["forces"] if surface == "swot" else plein["headline"]
    db = SessionLocal()
    try:
        assert _texte_edite(db, ids["mid"], surface) == (attendu, "generated")
    finally:
        db.close()


def test_synthese_globale_vide_ne_l_ecrase_pas_et_le_dit_par_le_statut(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthèse globale : le job de fond n'a aucune réponse HTTP où écrire un
    message — le seul canal vers l'écran est `generation_status="error"` /
    `generation_error`, que le panneau rend déjà. Sur un résultat vide, le texte
    écrit à la main survit, `status` reste `edited`, et l'erreur est posée."""
    from app.services import global_synthesis_job as job

    ids = _mission_complete("globale vide")
    db = SessionLocal()
    try:
        gs = db.get(Mission, ids["mid"]).global_synthesis
        gs.contexte = "- Contexte écrit à la main"
        gs.status = "edited"
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(job, "generate_global_synthesis",
                        lambda *a, **k: {"contexte": "", "culture_adn": "",
                                         "forces_succes": "", "points_amelioration": "",
                                         "aspirations": ""})
    job.run_global_synthesis_job(ids["mid"])

    db = SessionLocal()
    try:
        gs = db.get(Mission, ids["mid"]).global_synthesis
        assert gs.contenu("contexte") == "- Contexte écrit à la main"
        assert gs.status == "edited", "la garde s'est désarmée toute seule"
        assert gs.generation_status == "error"
        assert "aucun contenu" in (gs.generation_error or "")
    finally:
        db.close()


def test_synthese_globale_non_vide_est_bien_ecrite_par_le_job(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'autre moitié : une génération légitime écrit et repasse `generated`."""
    from app.services import global_synthesis_job as job

    ids = _mission_complete("globale pleine")
    db = SessionLocal()
    try:
        gs = db.get(Mission, ids["mid"]).global_synthesis
        gs.contexte = "- Contexte écrit à la main"
        gs.status = "edited"
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(job, "generate_global_synthesis",
                        lambda *a, **k: {"contexte": "- Contexte régénéré",
                                         "culture_adn": "", "forces_succes": "",
                                         "points_amelioration": "", "aspirations": ""})
    job.run_global_synthesis_job(ids["mid"])

    db = SessionLocal()
    try:
        gs = db.get(Mission, ids["mid"]).global_synthesis
        assert gs.contenu("contexte") == "- Contexte régénéré"
        assert gs.status == "generated"
        assert gs.generation_status == "idle"
        assert gs.generation_error is None
    finally:
        db.close()


def _verifier_onglets(apercu: str, onglets_de_garde: dict) -> list[str]:
    """Écarts entre les trois copies du nom d'onglet : `data-tab` des onglets,
    fragment `…/generate#X` des formulaires, valeurs de `_ONGLET_APERCU`.
    Rend la liste des défauts (vide = cohérent)."""
    import re

    onglets = set(re.findall(r'data-tab="([^"{]+)"', apercu))
    defauts = [
        f"_ONGLET_APERCU[{surface!r}] = {onglet!r} : aucun data-tab de ce nom"
        for surface, onglet in onglets_de_garde.items() if onglet not in onglets
    ]
    fragments = re.findall(r'action="[^"]*/generate#([^"]+)"', apercu)
    defauts += [
        f"formulaire …/generate#{frag} : aucun data-tab de ce nom"
        for frag in fragments if frag not in onglets
    ]
    if not fragments:
        defauts.append("aucun formulaire …/generate#onglet trouvé : regex périmée ?")
    return defauts


def test_nom_d_onglet_identique_dans_ses_trois_copies() -> None:
    """Le nom d'onglet est écrit trois fois (data-tab, fragment de l'action des
    formulaires de génération, `_ONGLET_APERCU` pour l'action de confirmation).
    Renommer un onglet en oubliant une copie ramène le consultant, en silence,
    sur le premier onglet ; seul l'onglet Risques a un e2e. Ce test lie les
    trois copies (revue du 2026-09-28)."""
    apercu = (Path(__file__).resolve().parents[1] / "app" / "templates"
              / "synthese" / "apercu.html").read_text(encoding="utf-8")
    defauts = _verifier_onglets(apercu, export_routes._ONGLET_APERCU)
    assert not defauts, "\n".join(defauts)
    # Le contrôle discrimine : une valeur pointée vers un onglet inexistant, ou
    # un fragment renommé, sont signalés par leur nom.
    faux = dict(export_routes._ONGLET_APERCU, risks="risques-renomme")
    assert any("risques-renomme" in d for d in _verifier_onglets(apercu, faux))
    assert any("swot-x" in d for d in _verifier_onglets(
        apercu.replace("/generate#swot", "/generate#swot-x"),
        export_routes._ONGLET_APERCU))
