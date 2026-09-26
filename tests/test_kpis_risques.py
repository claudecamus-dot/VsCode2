"""Indicateurs de suivi (US9.27 b) et matrice risques-contrôles (US9.27 c) :
aplatissement des réponses IA (types Ollama inattendus), génération (mockée,
aucun appel réseau), persistance, autosave, écran d'édition et slides du deck
(présentes, dessinées, paginées, absentes si vides). Calqué sur
test_executive_summary.py / test_difficultes.py : DB jetable, IA monkeypatchée."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import (
    GlobalSynthesis,
    Interview,
    Mission,
    MissionKpi,
    MissionRisk,
    RecommendationAxis,
)
from app.services import pptx_deck as D
from app.services import synthese_ai
from app.services.pptx_export import build_presentation, field_fit_hint


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


def _mission(name: str = "Mission suivi", *, axes: bool = True) -> int:
    db = SessionLocal()
    try:
        m = Mission(name=name)
        db.add(m)
        db.flush()
        db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
        db.add(GlobalSynthesis(
            mission_id=m.id, status="generated", contexte="- C", culture_adn="- C",
            forces_succes="- F", points_amelioration="- Silos", aspirations="- A"))
        if axes:
            db.add(RecommendationAxis(mission_id=m.id, title="Gouvernance data", position=0))
        db.commit()
        return m.id
    finally:
        db.close()


def _titres(prs) -> list[str]:
    return [(s.shapes.title.text_frame.text if s.shapes.title is not None else "")
            for s in prs.slides]


# --------------------------------------------------------------------------- #
# IA — aplatissement des types inattendus (jamais jeter, aplatir)
# --------------------------------------------------------------------------- #
def test_clean_kpis_flattens_ollama_types_and_snaps_axis() -> None:
    data = {"indicators": [  # clé anglaise
        {"label": ["Taux de", "couverture"], "target": {"v": "100 %"}, "axis": "gouvernance DATA"},
        "Délai d'arbitrage",                        # item chaîne nue -> libellé seul
        {"libelle": "", "cible": "x"},              # libellé vide -> ignoré
        {"libelle": 42},                            # scalaire non texte -> ignoré
    ]}
    out = synthese_ai._clean_kpis(data, ["Gouvernance data"])
    assert out == [
        {"libelle": "Taux de couverture", "cible": "100 %", "axe": "Gouvernance data"},
        {"libelle": "Délai d'arbitrage", "cible": "", "axe": ""},
    ]


def test_clean_kpis_accepts_bare_list_and_unknown_single_key() -> None:
    assert synthese_ai._clean_kpis([{"libelle": "A"}])[0]["libelle"] == "A"
    assert synthese_ai._clean_kpis({"resultat": [{"libelle": "B"}]})[0]["libelle"] == "B"
    assert synthese_ai._clean_kpis("pas un objet") == []


@pytest.mark.parametrize("valeur,attendu", [
    (3, 3), (1, 1), (2.6, 3), ("2", 2), ("3/3", 3), ("élevée", 3), ("High", 3),
    ("faible", 1), ("Moyenne", 2), (5, 3), ([1], 1), ({"niveau": "3"}, 3),
    (None, 2), ("n/a", 2), (0, 2), (True, 2), ({}, 2),
])
def test_coerce_niveau(valeur, attendu) -> None:
    assert synthese_ai._coerce_niveau(valeur) == attendu


def test_clean_risks_flattens_and_bounds() -> None:
    data = {"risks": [
        {"risk": ["Départ", "des experts"], "severity": "high", "likelihood": "4",
         "mitigation": ["Plan de", "rétention"], "type": "existing"},
        {"risque": "RGPD", "gravite": 1, "probabilite": 1, "controle": "", "controle_type": "?"},
        "Dette technique",
        {"risque": None},
    ]}
    out = synthese_ai._clean_risks(data)
    assert out[0] == {"risque": "Départ des experts", "gravite": 3, "probabilite": 3,
                      "controle": "Plan de rétention", "controle_type": "existant"}
    assert out[1]["controle_type"] == "propose" and out[1]["gravite"] == 1
    assert out[2] == {"risque": "Dette technique", "gravite": 2, "probabilite": 2,
                      "controle": "", "controle_type": "propose"}
    assert len(out) == 3


def test_generate_kpis_and_risks_prompt_includes_reco_axes(monkeypatch) -> None:
    vus = []

    def fake(system, prompt, schema, hint, **k):
        vus.append(prompt)
        return {"kpis": [{"libelle": "K", "cible": "c", "axe": "Axe A"}],
                "risques": [{"risque": "R", "gravite": 3, "probabilite": 2,
                             "controle": "c", "controle_type": "propose"}]}

    monkeypatch.setattr(synthese_ai, "_call_claude", fake)

    class _R:
        title = "Nommer des data owners"

    class _A:
        title = "Axe A"
        recommendations = [_R()]

    gs = GlobalSynthesis(points_amelioration="- Silos")
    assert synthese_ai.generate_kpis(gs, None, [_A()])[0]["axe"] == "Axe A"
    assert synthese_ai.generate_risks(gs, None, [_A()])[0]["gravite"] == 3
    assert all("Axe « Axe A »" in p and "Nommer des data owners" in p for p in vus)


# --------------------------------------------------------------------------- #
# Routes — génération (persistance) et autosave
# --------------------------------------------------------------------------- #
def _ia_prete(monkeypatch) -> None:
    monkeypatch.setattr("app.services.synthese_ai.is_configured", lambda: True)
    monkeypatch.setattr("app.routers.export.is_configured", lambda: True)


def test_generate_kpis_route_persists_ordered_rows(client, monkeypatch) -> None:
    mid = _mission()
    _ia_prete(monkeypatch)
    monkeypatch.setattr("app.routers.export.generate_kpis", lambda gs, a=None, r=None: [
        {"libelle": "Premier", "cible": "100 %", "axe": "Gouvernance data"},
        {"libelle": "Second", "cible": "", "axe": ""}])
    rep = client.post(f"/missions/{mid}/kpis/generate")
    assert rep.status_code == 200 and "Premier" in rep.text
    db = SessionLocal()
    try:
        ks = db.get(Mission, mid).kpis
        assert [(k.position, k.libelle, k.axe) for k in ks] == [
            (0, "Premier", "Gouvernance data"), (1, "Second", "")]
    finally:
        db.close()


def test_generate_empty_keeps_existing_and_shows_error(client, monkeypatch) -> None:
    mid = _mission()
    _ia_prete(monkeypatch)
    monkeypatch.setattr("app.routers.export.generate_risks", lambda *a: [
        {"risque": "À garder", "gravite": 3, "probabilite": 3, "controle": "", "controle_type": "propose"}])
    client.post(f"/missions/{mid}/risques/generate")
    monkeypatch.setattr("app.routers.export.generate_risks", lambda *a: [])
    rep = client.post(f"/missions/{mid}/risques/generate")
    assert "aucun risque" in rep.text
    db = SessionLocal()
    try:
        assert [r.risque for r in db.get(Mission, mid).risks] == ["À garder"]
    finally:
        db.close()


def test_generate_requires_global_synthesis(client, monkeypatch) -> None:
    _ia_prete(monkeypatch)
    db = SessionLocal()
    try:
        m = Mission(name="Sans synthèse")
        db.add(m)
        db.flush()
        db.add(Interview(mission_id=m.id, interviewee_name="T", status="done"))
        db.commit()
        mid = m.id
    finally:
        db.close()
    rep = client.post(f"/missions/{mid}/kpis/generate")
    assert "les indicateurs en découlent" in rep.text


def _ligne_kpi_et_risque(mid: int) -> tuple[int, int]:
    db = SessionLocal()
    try:
        m = db.get(Mission, mid)
        m.kpis = [MissionKpi(position=0, libelle="K", cible="c")]
        m.risks = [MissionRisk(position=0, risque="R", gravite=2, probabilite=2)]
        db.commit()
        return m.kpis[0].id, m.risks[0].id
    finally:
        db.close()


def test_autosave_kpi_and_risk_fields(client) -> None:
    mid = _mission()
    kid, rid = _ligne_kpi_et_risque(mid)
    assert client.post(f"/kpis/{kid}/field", data={"mission_id": mid, "field": "cible", "value": "90 %"}).status_code == 200
    assert client.post(f"/kpis/{kid}/field", data={"mission_id": mid, "field": "axe", "value": "Gouvernance data"}).status_code == 200
    rep = client.post(f"/risques/{rid}/field", data={"mission_id": mid, "field": "controle", "value": "Revue"})
    assert "fit-hint-risk-" in rep.text
    for f, v in (("gravite", "3"), ("probabilite", "1"), ("controle_type", "existant")):
        assert client.post(f"/risques/{rid}/field", data={"mission_id": mid, "field": f, "value": v}).status_code == 200
    db = SessionLocal()
    try:
        k, r = db.get(MissionKpi, kid), db.get(MissionRisk, rid)
        assert (k.cible, k.axe) == ("90 %", "Gouvernance data")
        assert (r.controle, r.gravite, r.probabilite, r.controle_type) == ("Revue", 3, 1, "existant")
    finally:
        db.close()


@pytest.mark.parametrize("url_tpl,data,code", [
    ("/kpis/{k}/field", {"field": "position", "value": "9"}, 400),
    ("/risques/{r}/field", {"field": "gravite", "value": "7"}, 400),
    ("/risques/{r}/field", {"field": "gravite", "value": "haut"}, 400),
    ("/risques/{r}/field", {"field": "controle_type", "value": "autre"}, 400),
    ("/risques/{r}/field", {"field": "mission_id", "value": "1"}, 400),
    ("/kpis/999999/field", {"field": "libelle", "value": "x"}, 404),
    ("/risques/999999/field", {"field": "risque", "value": "x"}, 404),
])
def test_autosave_rejects_bad_input(client, url_tpl, data, code) -> None:
    mid = _mission()
    kid, rid = _ligne_kpi_et_risque(mid)
    data = {"mission_id": mid, **data}
    assert client.post(url_tpl.format(k=kid, r=rid), data=data).status_code == code


def test_autosave_axe_tolere_casse_et_espaces(client) -> None:
    # Après une régénération des axes, l'écran peut poster une variante de casse
    # ou d'espaces internes de l'intitulé réel : rapprochée via _snap_axe, jamais
    # un 400 ni un lien perdu (salle code-review-crew, BOUNDARY-2).
    mid = _mission("Axe tolérant")
    kid, _ = _ligne_kpi_et_risque(mid)
    ok = client.post(f"/kpis/{kid}/field",
                     data={"mission_id": mid, "field": "axe", "value": "gouvernance  DATA"})
    assert ok.status_code == 200
    db = SessionLocal()
    try:
        assert db.get(MissionKpi, kid).axe == "Gouvernance data"  # intitulé EXACT
    finally:
        db.close()
    refus = client.post(f"/kpis/{kid}/field",
                        data={"mission_id": mid, "field": "axe", "value": "Axe fantôme"})
    assert refus.status_code == 400


def test_autosave_axe_perime_resoumis_reste_fixe(client) -> None:
    # Axe périmé (« Data », inclus dans l'axe réel « Gouvernance data ») : la
    # resoumission à l'identique s'accepte SANS rapprochement — jamais snappée
    # en silence vers un autre axe réel (contre-revue du correctif BOUNDARY-2).
    mid = _mission("Axe périmé")
    kid, _ = _ligne_kpi_et_risque(mid)
    db = SessionLocal()
    try:
        db.get(MissionKpi, kid).axe = "Data"
        db.commit()
    finally:
        db.close()
    ok = client.post(f"/kpis/{kid}/field",
                     data={"mission_id": mid, "field": "axe", "value": "Data"})
    assert ok.status_code == 200
    db = SessionLocal()
    try:
        assert db.get(MissionKpi, kid).axe == "Data"  # inchangé, pas « Gouvernance data »
    finally:
        db.close()


def test_autosave_refuses_a_row_of_another_mission(client) -> None:
    # Onglet périmé : la page de la mission B poste l'id d'un KPI / risque de A.
    # La ligne de A ne doit jamais être modifiée (salle code-review-crew, VEX-1).
    mid_a, mid_b = _mission("Mission A"), _mission("Mission B")
    kid, rid = _ligne_kpi_et_risque(mid_a)
    for url, field in ((f"/kpis/{kid}/field", "libelle"), (f"/risques/{rid}/field", "risque")):
        refus = client.post(url, data={"mission_id": mid_b, "field": field, "value": "écrasé"})
        assert refus.status_code == 404
    db = SessionLocal()
    try:
        assert db.get(MissionKpi, kid).libelle == "K"
        assert db.get(MissionRisk, rid).risque != "écrasé"
    finally:
        db.close()
    ok = client.post(f"/kpis/{kid}/field", data={"mission_id": mid_a, "field": "libelle", "value": "K2"})
    assert ok.status_code == 200


def test_apercu_shows_tabs_checkboxes_and_sommaire(client) -> None:
    mid = _mission()
    _ligne_kpi_et_risque(mid)
    html = client.get(f"/missions/{mid}/synthese/apercu").text
    assert 'data-tab="kpis"' in html and 'data-tab="risques"' in html
    assert 'name="kpis"' in html and 'name="risques"' in html
    assert "Indicateurs de suivi" in html and "Risques et contrôles" in html
    assert 'hx-post="/risques/' in html and 'hx-post="/kpis/' in html


def test_apercu_blank_rows_not_counted_for_export(client) -> None:
    mid = _mission()
    db = SessionLocal()
    try:
        m = db.get(Mission, mid)
        m.kpis = [MissionKpi(position=0, libelle="  ")]
        db.commit()
    finally:
        db.close()
    html = client.get(f"/missions/{mid}/synthese/apercu").text
    assert 'name="kpis"' not in html  # parité build_presentation : pas de case


# --------------------------------------------------------------------------- #
# Deck — slides présentes, dessinées, paginées ; absentes si vides
# --------------------------------------------------------------------------- #
def _mission_deck(n_kpis: int, n_risks: int, long: bool = False):
    mid = _mission(f"Deck {n_kpis}/{n_risks}/{long}")
    txt = ("Départ non anticipé des profils data rares et expérimentés avant que la "
           "montée en compétence des équipes internes ne soit effective " if long else "")
    db = SessionLocal()
    m = db.get(Mission, mid)
    m.kpis = [MissionKpi(position=i, libelle=f"{txt}KPI {i}", cible=f"{txt}cible {i}",
                         axe="Gouvernance data" if i % 2 else "Axe disparu")
              for i in range(n_kpis)]
    m.risks = [MissionRisk(position=i, risque=f"{txt}Risque {i}", controle=f"{txt}ctrl",
                           gravite=(i % 3) + 1, probabilite=3 - (i % 3),
                           controle_type="existant" if i % 2 else "propose")
               for i in range(n_risks)]
    db.commit()
    return db, m


def test_deck_includes_kpis_and_risk_matrix_in_trajectory() -> None:
    db, m = _mission_deck(3, 4)
    try:
        prs = build_presentation(m)
    finally:
        db.close()
    titres = _titres(prs)
    i_kpi = titres.index("Indicateurs de suivi")
    i_risk = titres.index("Matrice des risques et contrôles")
    assert i_kpi < i_risk
    matrice = prs.slides[i_risk]
    textes = " ".join(sh.text_frame.text for sh in matrice.shapes if sh.has_text_frame)
    for lbl in ("R1", "R4", "Probabilité", "Élevée", "Faible", "Mesure proposée", "Contrôle existant"):
        assert lbl in textes


def test_deck_without_kpis_or_risks_has_no_such_slide() -> None:
    db, m = _mission_deck(0, 0)
    try:
        m.risks = [MissionRisk(position=0, risque="   ")]
        db.commit()
        prs = build_presentation(m)
    finally:
        db.close()
    assert not any("Indicateurs" in t or "risques" in t for t in _titres(prs))


def test_deck_toggles_off() -> None:
    db, m = _mission_deck(2, 2)
    try:
        prs = build_presentation(m, include_kpis=False, include_risques=False)
    finally:
        db.close()
    assert not any("Indicateurs" in t or "risques" in t for t in _titres(prs))


def test_deck_paginates_many_long_items_without_losing_any() -> None:
    db, m = _mission_deck(8, 14, long=True)
    try:
        prs = build_presentation(m)  # lève si géométrie / plancher / chrome KO
    finally:
        db.close()
    titres = _titres(prs)
    assert sum(t.startswith("Indicateurs de suivi (") for t in titres) == 2
    pages_r = [s for s, t in zip(prs.slides, titres, strict=True)
               if t.startswith("Matrice des risques et contrôles")]
    assert len(pages_r) > 1
    textes = " ".join(sh.text_frame.text for s in pages_r for sh in s.shapes if sh.has_text_frame)
    import re
    for n in range(1, 15):  # chaque risque : repère (bulle) ET entrée de registre
        assert len(re.findall(rf"\bR{n}\b", textes)) >= 2, f"R{n} perdu"
    assert D.verifier_debordements_texte(prs) == []


def test_fit_hints_known_for_new_fields() -> None:
    assert "ligne" in field_fit_hint("kpi_libelle", "Taux de couverture")
    assert "tronqué" in field_fit_hint("risk_risque", "mot " * 200)


# --------------------------------------------------------------------------- #
# Correctifs de la revue adversariale (points 3-7)
# --------------------------------------------------------------------------- #
def test_snap_axe_exact_then_longest_never_short_terms() -> None:
    titres = ["Data", "Gouvernance des données Data", "Gouvernance des données"]
    assert synthese_ai._snap_axe("data", titres) == "Data"  # égalité d'abord
    # inclusion : la plus longue correspondance, pas la première de la liste
    assert synthese_ai._snap_axe("Axe Gouvernance des données Data (2026)", titres) \
        == "Gouvernance des données Data"
    # terme trop court : « SI » inclus dans « Rapprocher SI et métiers » -> pas de snap
    assert synthese_ai._snap_axe("SI", ["Rapprocher SI et métiers"]) == "SI"


def test_apply_risks_result_bounds_levels() -> None:
    from app.services.synthese_ecriture import apply_risks_result
    m = Mission(name="bornes")
    apply_risks_result(m, [
        {"risque": "A", "gravite": 0, "probabilite": "haut", "controle": "", "controle_type": "propose"},
        {"risque": "B", "gravite": 7, "probabilite": "1", "controle": "", "controle_type": "propose"},
    ])
    assert [(r.gravite, r.probabilite) for r in m.risks] == [(2, 3), (3, 1)]


def test_criticite_single_source() -> None:
    from app.models import criticite_risque
    from app.services.pptx_export import slides_trajectoire as T
    assert MissionRisk(gravite=3, probabilite=2).criticite == criticite_risque(3, 2) == 6
    assert MissionRisk(gravite=9, probabilite=None).criticite == 6  # bornée
    assert T._couleur_criticite(3, 2) == D.WARN and T._couleur_criticite(1, 3) == D.GOLD


def test_risk_prefix_never_eats_the_body() -> None:
    from app.services.pptx_export import slides_trajectoire as T
    long = "Départ non anticipé des profils data rares et expérimentés " * 4
    for num in (1, 10, 128):
        prefixe, corps, _c = T._risk_entry(num, MissionRisk(risque=long), 2.0, D.TYPE["tiny"])
        assert prefixe == f"R{num}   " and len(corps.strip(" …")) >= 10, (num, corps)


def test_deck_ten_plus_long_risks_each_keeps_its_text() -> None:
    db, m = _mission_deck(0, 12, long=True)
    try:
        prs = build_presentation(m)
    finally:
        db.close()
    textes = " ".join(sh.text_frame.text for s in prs.slides for sh in s.shapes if sh.has_text_frame)
    for n in (10, 11, 12):
        assert f"R{n}   Départ" in textes, f"R{n} : corps du risque mangé par la troncature"


def test_autosave_kpi_axe_validated_against_mission_axes(client) -> None:
    mid = _mission()
    kid, _rid = _ligne_kpi_et_risque(mid)
    assert client.post(f"/kpis/{kid}/field", data={"mission_id": mid, "field": "axe", "value": "Axe fantôme"}).status_code == 400
    assert client.post(f"/kpis/{kid}/field", data={"mission_id": mid, "field": "axe", "value": "Gouvernance data"}).status_code == 200
    assert client.post(f"/kpis/{kid}/field", data={"mission_id": mid, "field": "axe", "value": ""}).status_code == 200


def test_autosave_kpi_axe_hors_axes_actuels_resoumis_ok_autre_refuse(client) -> None:
    """3e branche de la validation : un KPI porte un axe qui n'existe plus (axe
    renommé) — l'écran le propose en « hors axes actuels » ; re-soumettre CETTE
    valeur passe (200), toute autre valeur hors axes est refusée (400)."""
    mid = _mission()
    kid, _rid = _ligne_kpi_et_risque(mid)
    assert client.post(f"/kpis/{kid}/field", data={"mission_id": mid, "field": "axe", "value": "Gouvernance data"}).status_code == 200
    db = SessionLocal()
    try:
        db.get(Mission, mid).recommendation_axes[0].title = "Gouvernance renommée"
        db.commit()
    finally:
        db.close()
    assert client.post(f"/kpis/{kid}/field", data={"mission_id": mid, "field": "axe", "value": "Gouvernance data"}).status_code == 200
    assert client.post(f"/kpis/{kid}/field", data={"mission_id": mid, "field": "axe", "value": "Autre axe"}).status_code == 400
    assert client.post(f"/kpis/{kid}/field", data={"mission_id": mid, "field": "axe", "value": "Gouvernance renommée"}).status_code == 200
