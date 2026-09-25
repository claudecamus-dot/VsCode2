"""Grille de maturité par pilier (incr.10 palier 3) : aplatissement des réponses
IA (types Ollama inattendus), génération (mockée), persistance, autosave, écran
d'édition et slide du deck (présente, paginée, absente si vide). Même patron que
test_kpis_risques.py : DB jetable, IA monkeypatchée."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import (
    MATURITE_NIVEAUX,
    GlobalSynthesis,
    Interview,
    Mission,
    MissionMaturite,
    Theme,
    Trame,
    score_maturite,
)
from app.services import pptx_deck as D
from app.services import synthese_ai
from app.services.pptx_export import build_presentation, field_fit_hint

PILIERS = ["Gouvernance de la donnée", "Organisation & collaboration", "Culture"]


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


def _mission(name: str = "Mission maturité", piliers=PILIERS) -> int:
    db = SessionLocal()
    try:
        m = Mission(name=name)
        db.add(m)
        db.flush()
        tr = Trame(mission_id=m.id)
        db.add(tr)
        db.flush()
        for i, t in enumerate(piliers):
            db.add(Theme(trame_id=tr.id, title=t, position=i))
        db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
        db.add(GlobalSynthesis(mission_id=m.id, status="generated", points_amelioration="- Silos"))
        db.commit()
        return m.id
    finally:
        db.close()


def _titres(prs) -> list[str]:
    return [(s.shapes.title.text_frame.text if s.shapes.title is not None else "")
            for s in prs.slides]


# --------------------------------------------------------------------------- #
# IA — aplatissement, bornage 0-3, piliers ramenés aux thèmes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("valeur,attendu", [
    (2, 2), (0, 0), (3.4, 3), (-1, 0), (7, 3), ("2", 2), ("2/3", 2), ("4/5", 2),
    ("5/5", 3), ("-1", 0), ("-2.5", 0), ("structuré", 2), ("Maîtrisé", 3), ("émergent", 1), ("absent", 0),
    ([1], 1), ({"score": "3"}, 3), (None, 0), ("?", 0), (True, 0),
])
def test_coerce_score03(valeur, attendu) -> None:
    assert synthese_ai._coerce_score03(valeur) == attendu


def test_coerce_score03_negative_string_is_clamped_not_defaulted() -> None:
    # defaut NON nul : distingue « parsé puis borné à 0 » de « illisible → defaut ».
    assert synthese_ai._coerce_score03("-1", defaut=2) == 0
    assert synthese_ai._coerce_score03("-2.5", defaut=2) == 0


def test_clean_maturite_flattens_snaps_orders_and_drops_unknown() -> None:
    data = {"maturity": [  # clé anglaise
        {"pillar": "culture", "level": "3/3", "rationale": ["Forte", "appétence"]},
        {"pilier": "Gouvernance de la donnée", "score": {"v": 1}, "justification": "Informelle"},
        {"pilier": "Pilier inventé", "score": 2, "justification": "x"},   # hors trame
        {"pilier": "Gouvernance de la donnée", "score": 3, "justification": "doublon"},
        "chaîne nue",                                                      # sans pilier sûr
    ]}
    assert synthese_ai._clean_maturite(data, PILIERS) == [
        {"pilier": "Gouvernance de la donnée", "score": 1, "justification": "Informelle"},
        {"pilier": "Culture", "score": 3, "justification": "Forte appétence"},
    ]


def test_generate_maturite_prompt_lists_piliers(monkeypatch) -> None:
    vus = []

    def fake(system, prompt, schema, hint, **k):
        vus.append(prompt)
        return {"maturite": [{"pilier": "Culture", "score": 2, "justification": "ok"}]}

    monkeypatch.setattr(synthese_ai, "_call_claude", fake)
    out = synthese_ai.generate_maturite(GlobalSynthesis(points_amelioration="- S"), None, PILIERS)
    assert out == [{"pilier": "Culture", "score": 2, "justification": "ok"}]
    assert all(f"- {p}" in vus[0] for p in PILIERS)


def test_score_maturite_and_scale() -> None:
    assert list(MATURITE_NIVEAUX) == [0, 1, 2, 3]
    assert (score_maturite("x"), score_maturite(9), score_maturite(-2)) == (0, 3, 0)
    assert MissionMaturite(score=2).niveau_label == "Structuré"


# --------------------------------------------------------------------------- #
# Routes — génération (persistance) et autosave
# --------------------------------------------------------------------------- #
def _ia_prete(monkeypatch) -> None:
    monkeypatch.setattr("app.services.synthese_ai.is_configured", lambda: True)
    monkeypatch.setattr("app.routers.export.is_configured", lambda: True)


def test_generate_route_persists_in_trame_order(client, monkeypatch) -> None:
    mid = _mission()
    _ia_prete(monkeypatch)
    vus = {}

    def fake(gs, axes, piliers):
        vus["piliers"] = piliers
        return [{"pilier": "Culture", "score": 2, "justification": "B"},
                {"pilier": "Gouvernance de la donnée", "score": 0, "justification": "A"}]

    monkeypatch.setattr("app.routers.export.generate_maturite", fake)
    rep = client.post(f"/missions/{mid}/maturite/generate")
    assert rep.status_code == 200
    assert vus["piliers"] == PILIERS
    db = SessionLocal()
    try:
        assert [(x.position, x.pilier, x.score) for x in db.get(Mission, mid).maturites] == [
            (0, "Culture", 2), (1, "Gouvernance de la donnée", 0)]
    finally:
        db.close()


def test_generate_empty_keeps_existing(client, monkeypatch) -> None:
    mid = _mission()
    _ia_prete(monkeypatch)
    monkeypatch.setattr("app.routers.export.generate_maturite",
                        lambda *a: [{"pilier": "Culture", "score": 3, "justification": "garder"}])
    client.post(f"/missions/{mid}/maturite/generate")
    monkeypatch.setattr("app.routers.export.generate_maturite", lambda *a: [])
    rep = client.post(f"/missions/{mid}/maturite/generate")
    assert "grille" in rep.text and "inchangée" in rep.text
    db = SessionLocal()
    try:
        assert [x.justification for x in db.get(Mission, mid).maturites] == ["garder"]
    finally:
        db.close()


def test_generate_without_themes_explains(client, monkeypatch) -> None:
    mid = _mission("Sans thèmes", piliers=[])
    _ia_prete(monkeypatch)
    rep = client.post(f"/missions/{mid}/maturite/generate")
    assert rep.status_code == 200 and "aucun thème" in rep.text


def _ligne(mid: int) -> int:
    db = SessionLocal()
    try:
        m = db.get(Mission, mid)
        m.maturites = [MissionMaturite(position=0, pilier="Culture", score=1, justification="j")]
        db.commit()
        return m.maturites[0].id
    finally:
        db.close()


def test_autosave_score_and_justification(client) -> None:
    lid = _ligne(_mission())
    assert client.post(f"/maturites/{lid}/field", data={"field": "score", "value": "3"}).status_code == 200
    rep = client.post(f"/maturites/{lid}/field", data={"field": "justification", "value": "Piloté"})
    assert rep.status_code == 200 and "fit-hint-mat-" in rep.text
    db = SessionLocal()
    try:
        x = db.get(MissionMaturite, lid)
        assert (x.score, x.justification) == (3, "Piloté")
    finally:
        db.close()


@pytest.mark.parametrize("data,code", [
    ({"field": "score", "value": "4"}, 400),
    ({"field": "score", "value": "-1"}, 400),
    ({"field": "score", "value": "haut"}, 400),
    ({"field": "pilier", "value": "x"}, 400),
    ({"field": "mission_id", "value": "1"}, 400),
])
def test_autosave_rejects_bad_input(client, data, code) -> None:
    lid = _ligne(_mission())
    assert client.post(f"/maturites/{lid}/field", data=data).status_code == code


def test_autosave_unknown_row_404(client) -> None:
    assert client.post("/maturites/999999/field", data={"field": "score", "value": "1"}).status_code == 404


def test_apercu_tab_scale_checkbox_and_sommaire(client) -> None:
    mid = _mission()
    _ligne(mid)
    html = client.get(f"/missions/{mid}/synthese/apercu").text
    assert 'data-tab="maturite"' in html and 'name="maturite"' in html
    assert "Maturité par pilier" in html
    for lib in MATURITE_NIVEAUX.values():  # échelle nommée affichée à l'écran
        assert lib in html
    assert 'hx-post="/maturites/' in html


# --------------------------------------------------------------------------- #
# Deck
# --------------------------------------------------------------------------- #
def _deck(n: int, long: bool = False):
    mid = _mission(f"Deck maturité {n} {long}")
    txt = ("Des pratiques existent localement mais ne sont ni partagées ni pilotées, "
           "faute de responsable et d'instance dédiée à l'échelle du groupe " if long else "")
    db = SessionLocal()
    m = db.get(Mission, mid)
    m.maturites = [MissionMaturite(position=i, pilier=f"{txt[:60]}Pilier {i + 1}",
                                   score=i % 4, justification=f"{txt}justif {i + 1}")
                   for i in range(n)]
    db.commit()
    return db, m


def test_deck_maturite_in_diagnostic_with_scale_legend() -> None:
    db, m = _deck(3)
    try:
        prs = build_presentation(m)
    finally:
        db.close()
    titres = _titres(prs)
    i = titres.index("Grille de maturité par pilier")
    textes = " ".join(sh.text_frame.text for sh in prs.slides[i].shapes if sh.has_text_frame)
    for lib in ("0 Absent", "1 Émergent", "2 Structuré", "3 Maîtrisé", "Pilier 3", "justif 2"):
        assert lib in textes


def test_deck_without_maturite_or_toggled_off() -> None:
    db, m = _deck(0)
    try:
        m.maturites = [MissionMaturite(position=0, pilier="  ", score=2)]
        db.commit()
        assert not any("Grille de maturité" in t for t in _titres(build_presentation(m)))
    finally:
        db.close()
    db, m = _deck(2)
    try:
        assert not any("Grille de maturité" in t for t in _titres(build_presentation(m, include_maturite=False)))
    finally:
        db.close()


def test_deck_paginates_15_long_piliers_without_losing_any() -> None:
    db, m = _deck(15, long=True)
    try:
        prs = build_presentation(m)  # lève si géométrie / plancher / chrome KO
    finally:
        db.close()
    pages = [s for s, t in zip(prs.slides, _titres(prs), strict=True)
             if t.startswith("Grille de maturité par pilier")]
    assert len(pages) > 1
    textes = " ".join(sh.text_frame.text for s in pages for sh in s.shapes if sh.has_text_frame)
    # chaque pilier = une ligne : autant de libellés « n · Niveau » que de piliers
    assert sum(textes.count(f"{s} · {lib}") for s, lib in MATURITE_NIVEAUX.items()) == 15
    assert D.verifier_debordements_texte(prs) == []


def test_fit_hint_known() -> None:
    assert "ligne" in field_fit_hint("maturite_justification", "Pratiques isolées")
    assert "tronqué" in field_fit_hint("maturite_justification", "mot " * 200)


# --------------------------------------------------------------------------- #
# Correctifs de revue (points 1-6)
# --------------------------------------------------------------------------- #
def test_clean_maturite_keeps_two_themes_with_same_title() -> None:
    piliers = ["Culture", "Outils", "Culture"]
    data = {"maturite": [
        {"pilier": "Culture", "score": 1, "justification": "site A"},
        {"pilier": "Outils", "score": 2, "justification": "o"},
        {"pilier": "Culture", "score": 3, "justification": "site B"},
    ]}
    out = synthese_ai._clean_maturite(data, piliers)
    assert [(x["pilier"], x["justification"]) for x in out] == [
        ("Culture", "site A"), ("Outils", "o"), ("Culture", "site B")]


def _slide_mat(prs):
    return next(s for s in prs.slides
                if s.shapes.title is not None
                and s.shapes.title.text_frame.text.startswith("Grille de maturité"))


def test_score_zero_gauge_outlined_like_legend() -> None:
    from app.services.pptx_export.slides_diagnostic import couleur_maturite
    db, m = _deck(1)  # un seul pilier, score 0
    try:
        s = _slide_mat(build_presentation(m))
    finally:
        db.close()
    rouge = couleur_maturite(0).lstrip("#").upper()
    cercles = [sh for sh in s.shapes
               if not sh.has_text_frame or not sh.text_frame.text.strip()
               if getattr(sh, "line", None) is not None and sh.line.fill.type == 1
               and str(sh.line.color.rgb).upper() == rouge]
    assert len(cercles) == 3, "score 0 : les 3 segments vides doivent être cerclés du rouge de la légende"


def test_justification_three_lines_when_few_rows_two_when_full() -> None:
    longue = ("Des pratiques existent localement mais ne sont ni partagées ni pilotées, "
              "faute de responsable nommé et d'instance dédiée ; les arbitrages remontent "
              "au COMEX et les règles varient d'une direction à l'autre sans justification")
    for n, lignes_max in ((2, 3), (15, 2)):
        mid = _mission(f"Lignes {n}")
        db = SessionLocal()
        try:
            m = db.get(Mission, mid)
            m.maturites = [MissionMaturite(position=i, pilier=f"P{i}", score=2, justification=longue)
                           for i in range(n)]
            db.commit()
            prs = build_presentation(m)
        finally:
            db.close()
        s = _slide_mat(prs)
        j = next(sh.text_frame.text for sh in s.shapes
                 if sh.has_text_frame and sh.text_frame.text.startswith("Des pratiques"))
        from app.services.pptx_export.slides_diagnostic import _dims  # noqa: F401
        w_j = 10.0 - 0.6 - (0.6 + 2.5 + 0.2 + 2.1 + 0.2)
        assert D.estimer_lignes(j, w_j, D.TYPE["small"], cpi_ref=12.5) == lignes_max, (n, j)
        assert D.verifier_debordements_texte(prs) == []


def test_first_row_too_tall_is_truncated_not_overflowing() -> None:
    """Gabarit au titre très bas / slide courte : la 1re ligne d'une page ne tient
    pas dans la bande — elle est ramenée à UNE ligne, rien ne descend sous `bas`."""
    from pptx import Presentation
    from pptx.util import Inches

    from app.services.pptx_export.slides_diagnostic import _slide_maturite
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(10), Inches(2.9)
    prs._i2d_synthetic = True
    D.set_police(None)
    long = "Pilier au libellé démesuré " * 12
    lignes = [MissionMaturite(position=0, pilier=long, score=1, justification=long)]
    _slide_maturite(prs, lignes)
    bas = 2.9 - 0.60 - 0.30
    for s in prs.slides:
        for sh in s.shapes:
            if sh.has_text_frame and sh.text_frame.text.startswith("Pilier au"):
                assert (sh.top + sh.height) / 914400 <= bas + 0.01
                assert D.estimer_lignes(sh.text_frame.text, sh.width / 914400, D.TYPE["small"], cpi_ref=12.5) == 1
    assert D.verifier_geometrie(prs) == []
