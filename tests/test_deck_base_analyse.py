"""La slide « Base de l'analyse » du deck (incrément I1 de
`docs/reflexions/spec-restitution-defendable.md`).

Ce qu'elle porte, et pourquoi c'est testé serré : en restitution, la question
qui fait tomber un constat est « combien de personnes ont dit ça ? ». Cette
slide y répond par des chiffres CALCULÉS. Un chiffre faux y serait pire que pas
de chiffre — il donnerait à une affirmation l'apparence d'une mesure.

Piège de nommage figé ici : `Archetype.COUVERTURE` désigne la page de GARDE.
La couverture au sens « combien d'entretiens » est `Archetype.BASE_ANALYSE`.
"""
from __future__ import annotations

import pytest

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import (
    Answer, GlobalSynthesis, Interview, Mission, Question, Theme, Trame,
)
from app.services.pptx_export import build_presentation
from app.services.pptx_export.archetypes import Archetype


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


def _mission(nom: str, themes: tuple[str, ...], plan: dict[str, tuple[str, ...]],
             libres: int = 0) -> int:
    """`plan` : interviewé -> thèmes auxquels il répond."""
    db = SessionLocal()
    try:
        m = Mission(name=nom)
        db.add(m); db.flush()
        qs = {}
        if themes:
            tr = Trame(mission_id=m.id); db.add(tr); db.flush()
            for i, titre in enumerate(themes):
                th = Theme(trame_id=tr.id, title=titre, position=i); db.add(th); db.flush()
                q = Question(theme_id=th.id, label=f"{titre} ?", position=0)
                db.add(q); db.flush()
                qs[titre] = q.id
        for nom_iv, repond in plan.items():
            iv = Interview(mission_id=m.id, interviewee_name=nom_iv, status="done")
            db.add(iv); db.flush()
            for t in repond:
                db.add(Answer(interview_id=iv.id, question_id=qs[t], text="Réponse."))
            db.flush()
        for k in range(libres):
            db.add(Interview(mission_id=m.id, interviewee_name=f"Libre {k}", mode="libre",
                             status="done", repartition={"contexte": "Vu."}))
        db.commit()
        return m.id
    finally:
        db.close()


def _deck(mission_id: int, **kw):
    db = SessionLocal()
    try:
        return build_presentation(db.get(Mission, mission_id), **kw)
    finally:
        db.close()


def _slides_base(prs) -> list:
    out = []
    for s in prs.slides:
        t = s.shapes.title
        titre = (t.text_frame.text if t is not None and t.has_text_frame else "").strip()
        if titre.startswith("Base de l'analyse"):
            out.append(s)
    return out


def _textes(slide) -> str:
    return "\n".join(sh.text_frame.text for sh in slide.shapes if sh.has_text_frame)


def test_la_slide_porte_les_ratios_calcules() -> None:
    mission_id = _mission(
        "Base", ("Gouvernance", "Outillage"),
        {"Alix": ("Gouvernance", "Outillage"), "Bao": ("Gouvernance",), "Chris": ()},
        libres=1,
    )
    slides = _slides_base(_deck(mission_id))
    assert len(slides) == 1
    texte = _textes(slides[0])
    # 3 structurés (dont Chris muet) + 1 libre analysé = 4 entretiens, 3 nourrissent.
    assert "3/4" in texte
    assert "2/3" in texte   # Gouvernance : Alix + Bao sur 3 structurés
    assert "1/3" in texte   # Outillage : Alix seul


def test_un_theme_sans_question_rend_un_tiret_pas_un_zero() -> None:
    """« 0/3 » se lirait comme un thème mal couvert ; il n'y a rien à y couvrir."""
    mission_id = _mission("Thème vide", ("Répondu",), {"Alix": ("Répondu",)})
    db = SessionLocal()
    try:
        m = db.get(Mission, mission_id)
        db.add(Theme(trame_id=m.trame.id, title="Thème sans question", position=1))
        db.commit()
    finally:
        db.close()
    texte = _textes(_slides_base(_deck(mission_id))[0])
    assert "—" in texte
    # Le thème sans question ne produit AUCUN ratio : ni « 0/1 » ni « 0/0 ».
    # (Le ratio de mission vaut 1/1 ici, donc aucun « 0/ » légitime.)
    assert "0/" not in texte


def test_la_slide_disparait_sans_aucun_entretien() -> None:
    assert _slides_base(_deck(_mission("Vide", ("T",), {}))) == []


def test_la_case_decochee_retire_la_slide() -> None:
    mission_id = _mission("Décochée", ("T",), {"Alix": ("T",)})
    assert _slides_base(_deck(mission_id)) != []
    assert _slides_base(_deck(mission_id, include_base_analyse=False)) == []


def test_beaucoup_de_themes_paginent_sans_deborder() -> None:
    """15 thèmes ne tiennent pas sur une page : la slide se pagine plutôt que
    d'empiler les lignes hors de la carte (le vérificateur de géométrie le
    confirme)."""
    from app.services import pptx_deck as D

    # Libellés qui ne se contiennent PAS les uns les autres : « Thème 1 » est
    # une sous-chaîne de « Thème 10 », et le comptage d'occurrences mentirait.
    themes = tuple(f"Axe {i:02d}" for i in range(15))
    mission_id = _mission("Beaucoup", themes, {"Alix": themes})
    prs = _deck(mission_id)
    slides = _slides_base(prs)
    assert len(slides) > 1, "15 thèmes devraient occuper plusieurs pages"
    # Chaque thème apparaît une fois et une seule, toutes pages confondues.
    tout = "\n".join(_textes(s) for s in slides)
    for t in themes:
        assert tout.count(t) == 1, f"{t} apparaît {tout.count(t)} fois"
    assert D.verifier_geometrie(prs) == []
    assert D.verifier_debordements_texte(prs) == []


def test_l_archetype_de_la_slide_n_est_pas_celui_de_la_page_de_garde() -> None:
    """`COUVERTURE` = page de garde ; `BASE_ANALYSE` = couverture des entretiens.
    Les confondre ferait filtrer la page de garde par un plan qui demande la
    base de l'analyse, et réciproquement."""
    assert Archetype.BASE_ANALYSE != Archetype.COUVERTURE
    assert Archetype.BASE_ANALYSE.value == "base_analyse"


def test_un_plan_sans_la_base_de_l_analyse_la_retire() -> None:
    """Un deck d'exemple qui ne cite pas cet archétype ne doit pas le voir
    apparaître — le plan FILTRE, il ne crée jamais."""
    mission_id = _mission("Plan", ("T",), {"Alix": ("T",)})
    prs = _deck(mission_id, plan=[Archetype.SYNTHESE.value])
    assert _slides_base(prs) == []


def test_la_case_de_l_apercu_existe_sans_executive_summary() -> None:
    """Le bloquant de la revue du 2026-09-28 : la case `base_analyse` avait ete
    ecrite DANS le bloc `{% if executive_summary %}` de apercu.html. Une case
    absente n'est jamais soumise, et `base_analyse` vaut False par defaut cote
    route — la slide devenait donc INATTEIGNABLE pour toute mission sans
    executive summary, quel que soit le nombre d'entretiens.

    Aucun des tests de construction du deck ne pouvait le voir : ils appellent
    `build_presentation` en direct et ne rendent jamais le template. C'est le
    trou de test que ce cas ferme."""
    from fastapi.testclient import TestClient

    from app.main import app

    mission_id = _mission("Sans exec summary", ("T",), {"Alix": ("T",)})
    # L'ecran de configuration n'apparait qu'avec du contenu : on pose une
    # synthese globale, et surtout AUCUN executive summary — c'est le cas exact
    # que le bloquant rendait inatteignable.
    db = SessionLocal()
    try:
        db.add(GlobalSynthesis(mission_id=mission_id, status="generated",
                               contexte="- Un contexte"))
        db.commit()
    finally:
        db.close()
    page = TestClient(app).get(f"/missions/{mission_id}/synthese/apercu").text
    assert "Définition des slides" in page, "l'ecran de configuration doit etre rendu"
    assert 'name="base_analyse"' in page, (
        "la case doit exister des qu'il y a un entretien, sans dependre "
        "de l'executive summary"
    )
    # Et le garde-fou de la cause : la case ne depend pas du bloc voisin.
    assert 'name="executive_summary"' not in page


def test_la_case_disparait_sans_aucun_entretien() -> None:
    """Symetrique du test ci-dessus : la condition propre doit aussi RETIRER la
    case quand la slide ne se construirait pas (parite ecran / build)."""
    from fastapi.testclient import TestClient

    from app.main import app

    mission_id = _mission("Sans entretien", ("T",), {})
    page = TestClient(app).get(f"/missions/{mission_id}/synthese/apercu").text
    assert 'name="base_analyse"' not in page
