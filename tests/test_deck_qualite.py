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


def test_deck_charte_police_du_theme_appliquee_pas_outfit() -> None:
    """Charte / rendu : le deck applique au texte la police du THÈME (Arial sur OCTO,
    une police système donc rendue telle quelle), et surtout PAS l'Outfit des
    placeholders — qui, n'étant pas installée, serait rendue en substitution (c'était
    la cause du « la police ne matche pas la référence » ; cf. police_theme vs
    police_marque dans pptx_deck). Ce test est le garde-fou anti-régression du fix."""
    db = SessionLocal()
    try:
        prs = build_presentation(db.get(Mission, _mission_complete()))
    finally:
        db.close()
    theme = D.police_theme(prs)
    assert theme, "police du thème non détectée sur le template"
    fonts = {
        r.font.name
        for s in prs.slides for sh in s.shapes if sh.has_text_frame
        for p in sh.text_frame.paragraphs for r in p.runs
        if r.font.name  # ignorer les runs sans police explicite (héritage)
    }
    assert theme in fonts, f"police du thème {theme!r} non appliquée au texte dessiné"
    # Garde-fou dur : l'Outfit des placeholders (non installée) ne doit JAMAIS être
    # forcée sur le texte — sinon rendu en substitution, le bug d'origine.
    placeholder = D.police_marque(prs)  # 'Outfit' sur le template OCTO
    if placeholder and placeholder != theme:
        assert placeholder not in fonts, (
            f"la police des placeholders {placeholder!r} (non installée) est forcée sur "
            "le texte — elle serait rendue en substitution ; utiliser la police du thème"
        )


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


# ---- Revue de design automatisée (demande 2026-07-22, skill deck-design-review) ----
# Chaque invariant ci-dessous verrouille un défaut TROUVÉ par une revue visuelle
# réelle — le test empêche sa réapparition, le skill (rendu + œil) trouve les suivants.


def _prs_complete():
    db = SessionLocal()
    try:
        return build_presentation(db.get(Mission, _mission_complete()))
    finally:
        db.close()


def _slide_titre(s) -> str:
    t = s.shapes.title
    return (t.text_frame.text if t is not None and t.has_text_frame else "").strip()


def test_design_matrice_dessinee_pas_de_scatter_excel() -> None:
    """La matrice de priorisation est DESSINÉE (skill priority-matrix) : plus aucun
    graphique scatter natif (marqueurs Excel gris + légende ◆■▲ illisibles), et la
    slide porte ses invariants — 4 libellés de quadrant + une bulle par reco."""
    from pptx.oxml.ns import qn
    prs = _prs_complete()
    matrice = None
    for s in prs.slides:
        if "Matrice de priorisation" in _slide_titre(s):
            matrice = s
        for gf in s.shapes:
            if gf.has_chart:
                xml = gf.chart._chartSpace.xml
                assert "scatterChart" not in xml, "scatter Excel réintroduit (priority-matrix)"
    assert matrice is not None, "slide matrice de priorisation absente"
    textes = " ".join(sh.text_frame.text for sh in matrice.shapes if sh.has_text_frame)
    for lbl in ("QUICK WINS", "CHANTIERS STRUCTURANTS", "OPPORTUNISTES", "À DIFFÉRER"):
        assert lbl in textes, f"libellé de quadrant manquant : {lbl}"
    assert "1.1" in textes, "bulle de reco absente de la matrice"


def test_design_swot_matrice_axes_explicites() -> None:
    """La SWOT reste une MATRICE (skill swot-matrix) : axes Interne/Externe ×
    Favorable/Défavorable présents — pas quatre cartes flottantes."""
    prs = _prs_complete()
    swot = next((s for s in prs.slides if "SWOT" in _slide_titre(s)), None)
    assert swot is not None
    textes = " ".join(sh.text_frame.text for sh in swot.shapes if sh.has_text_frame)
    for lbl in ("FAVORABLE", "DÉFAVORABLE", "INTERNE", "EXTERNE"):
        assert lbl in textes, f"axe de la matrice SWOT manquant : {lbl}"


def test_design_fiche_reco_encarts_arrondis() -> None:
    """Les fiches reco posent leur contenu dans des ENCARTS ARRONDIS format OCTO
    (demande 2026-07-22) : cartes roundRect + l'encart gris « proposition »."""
    prs = _prs_complete()
    fiche = next((s for s in prs.slides if _slide_titre(s).startswith("1.1")), None)
    assert fiche is not None, "fiche 1.1 absente"
    xml = fiche._element.xml
    assert xml.count('prst="roundRect"') >= 3, "cartes arrondies manquantes sur la fiche"
    assert D.ENCART_BG.lstrip("#").upper() in xml.upper(), "encart gris proposition absent"


def test_design_titres_a_l_echelle() -> None:
    """Tous les titres de slides de contenu sont à D.TYPE['title'] (20pt, aligné
    référence) — pas de taille de titre ad hoc réintroduite."""
    from pptx.util import Pt
    prs = _prs_complete()
    for s in prs.slides:
        lay = (s.slide_layout.name or "").lower()
        if "titre seul" not in lay:
            continue  # couverture/chapitres : tailles du template de marque
        t = s.shapes.title
        if t is None or not t.has_text_frame:
            continue
        for p in t.text_frame.paragraphs:
            for r in p.runs:
                if r.font.size is not None:
                    assert r.font.size == Pt(D.TYPE["title"]), (
                        f"titre hors échelle sur « {_slide_titre(s)} » : {r.font.size}"
                    )


def test_design_cache_image_proc_ne_bloque_pas_la_photo(tmp_path, monkeypatch) -> None:
    """Le repli procédural (tests/offline) n'occupe JAMAIS le slot photo : un run
    en ligne ultérieur doit retenter le vrai fetch (cause racine des images
    « générées » servies à vie sur les slides synthèse, 2026-07-22)."""
    import app.services.pptx_export as px
    if not px._FRAMED_OK:  # infra image absente : rien à vérifier ici
        return
    monkeypatch.setattr(px, "_IMG_CACHE", tmp_path)
    monkeypatch.setenv("PPTX_NO_PHOTO_FETCH", "1")
    p = px._resoudre_image_cachee("t_1_10x10", "mountains", 1, 1.0, 10, 10, requete="x")
    assert p.name.endswith("_proc.png") and p.exists()
    assert not list(tmp_path.glob("*_photo.png")), (
        "le repli procédural a écrit dans le slot photo — il le monopoliserait à vie"
    )
