"""Test « rendu de qualité » du deck de restitution (P6 refonte, 2026-07-22).

Consolide les contrôles qualité des slides au-delà du seul `verifier_geometrie`
(formes hors-cadre) : STRUCTURE narrative (couverture de marque, 4 intercalaires de
chapitre, slides de contenu titrées), CHARTE (police de marque Outfit réellement
appliquée au texte dessiné), et le garde-fou géométrie (build lève sinon). C'est le
filet automatisé complémentaire à `pptx-verify` (rendu visuel humain) : ce dernier
voit ce qu'un test ne voit pas (collisions, vides), ce test verrouille ce que l'œil
ne re-vérifie pas à chaque fois (structure/charte/géométrie).
"""
from __future__ import annotations

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import (
    GlobalSynthesis, Interview, Mission, MissionDifficulty, MissionExecutiveSummary,
    MissionSwot, Question, Recommendation, RecommendationAxis, Theme, Trame, Verbatim,
)
from app.services import pptx_deck as D
from app.services.pptx_export import build_presentation


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


def _mission_complete() -> int:
    """Mission couvrant les 4 chapitres (exec summary, synthèse, difficultés, SWOT,
    verbatims, axes+recos) — de quoi exercer toute la structure du deck."""
    db = SessionLocal()
    try:
        m = Mission(name="Audit qualité — Deck complet")
        db.add(m); db.flush()
        tr = Trame(mission_id=m.id); db.add(tr); db.flush()
        th = Theme(trame_id=tr.id, title="T", position=0); db.add(th); db.flush()
        q = Question(theme_id=th.id, label="Q", qtype="open", position=0); db.add(q); db.flush()
        iv = Interview(mission_id=m.id, interviewee_name="Témoin", status="done"); db.add(iv); db.flush()
        v = Verbatim(interview_id=iv.id, question_id=q.id, quote="Une citation représentative.")
        db.add(v); db.flush()
        m.restitution_verbatim_ids = [v.id]
        db.add(GlobalSynthesis(
            mission_id=m.id, status="generated", contexte="- C", culture_adn="- C",
            forces_succes="- F", points_amelioration="- Silos\n- Dette", aspirations="- A"))
        db.add(MissionExecutiveSummary(
            mission_id=m.id, status="generated", headline="Un titre-claim de synthèse",
            points="- Point un\n- Point deux", key_message="Le message à retenir."))
        m.difficulties = [MissionDifficulty(position=0, label="Silos entre équipes")]
        db.add(MissionSwot(
            mission_id=m.id, status="generated", forces="- F", faiblesses="- Fa",
            opportunites="- O", menaces="- Me"))
        ax = RecommendationAxis(mission_id=m.id, title="Axe 1", position=0); db.add(ax); db.flush()
        db.add(Recommendation(axis_id=ax.id, position=0, title="Reco 1", objectif="O",
                              acteurs="A", valeur=5, complexite=2, proposition_valeur="P",
                              plan_actions="- a", resultats_attendus="- r"))
        db.commit()
        return m.id
    finally:
        db.close()


def _layouts(prs) -> list[str]:
    return [(s.slide_layout.name or "").lower() for s in prs.slides]


def test_deck_structure_narrative() -> None:
    """Structure : couverture de marque, sommaire, 4 intercalaires de chapitre,
    chaque slide de contenu (« titre seul ») porte un titre non vide."""
    db = SessionLocal()
    try:
        prs = build_presentation(db.get(Mission, _mission_complete()))
    finally:
        db.close()
    layouts = _layouts(prs)
    assert any("couverture" in l for l in layouts), "couverture de marque OCTO manquante"
    assert sum(1 for l in layouts if "chapitre" in l) == 4, "4 intercalaires de chapitre attendus"
    # Chaque slide de contenu a du texte (titre) — pas de slide vide.
    for s in prs.slides:
        if "titre seul" in (s.slide_layout.name or "").lower():
            has_text = any(
                sh.has_text_frame and sh.text_frame.text.strip() for sh in s.shapes
            )
            assert has_text, "slide de contenu sans aucun texte"


def test_deck_charte_police_de_marque_appliquee() -> None:
    """Charte : la police de marque du template (Outfit) est détectée ET réellement
    appliquée au texte dessiné (add_text) — pas seulement présente sur les placeholders."""
    db = SessionLocal()
    try:
        prs = build_presentation(db.get(Mission, _mission_complete()))
    finally:
        db.close()
    police = D.police_marque(prs)
    assert police, "police de marque non détectée sur le template"
    fonts = {
        r.font.name
        for s in prs.slides for sh in s.shapes if sh.has_text_frame
        for p in sh.text_frame.paragraphs for r in p.runs
    }
    assert police in fonts, f"police de marque {police!r} non appliquée au texte dessiné"


def test_deck_charte_pas_d_ombre_sur_les_cartes() -> None:
    """Charte (règle dure OCTO : différenciation par bordure, jamais d'ombre) — les
    autoshapes dessinées (cartes/rectangles) ont l'héritage d'ombre coupé (_no_shadow)."""
    from pptx.oxml.ns import qn
    db = SessionLocal()
    try:
        prs = build_presentation(db.get(Mission, _mission_complete()))
    finally:
        db.close()
    # Aucune forme dessinée ne doit porter un effet d'ombre explicite (<a:effectLst>
    # avec une ombre) — _no_shadow coupe l'héritage du thème sur add_card/add_rect.
    for s in prs.slides:
        for sh in s.shapes:
            spPr = getattr(sh._element, "spPr", None)
            if spPr is None:
                continue
            effect = spPr.find(qn("a:effectLst"))
            if effect is not None:
                assert effect.find(qn("a:outerShdw")) is None, "ombre portée détectée (charte OCTO)"
