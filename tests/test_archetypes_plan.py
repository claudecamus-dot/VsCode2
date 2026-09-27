"""US5.3 (archétypes de slide, classement déterministe) et US5.2 (plan extrait
d'un deck d'exemple, qui réordonne/filtre l'export sans créer de slide)."""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Pt

from app.db import PPTX_EXEMPLES_DIR, SessionLocal, init_db
from app.main import app
from app.models import Mission
from app.services.mission_axes import axes_of
from app.services.pptx_export import build_presentation
from app.services.pptx_export.archetypes import Archetype as A
from app.services.pptx_export.archetypes import (
    classer_slide,
    extraire_plan,
    extraire_plan_fichier,
)
from app.services.pptx_export.slides_cadre import _slide_chapitre
from scripts.seed_demo import seed

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXEMPLE = Path(__file__).resolve().parent.parent / "docs" / "exemples" / "deck-restitution-exemple.pptx"

# Suite attendue du deck d'exemple (sortie du générateur, 20 slides).
CLASSEMENT_EXEMPLE = [
    A.COUVERTURE, A.SOMMAIRE, A.CHAPITRE, A.EXECUTIVE_SUMMARY, A.CHAPITRE,
    *[A.SYNTHESE] * 5, A.DIFFICULTES, A.SWOT, A.CHAPITRE, A.VERBATIMS, A.CHAPITRE,
    A.AXES, A.MATRICE_PRIORISATION, *[A.FICHE_RECO] * 3,
]
PLAN_EXEMPLE = [
    A.COUVERTURE, A.SOMMAIRE, A.CHAPITRE, A.EXECUTIVE_SUMMARY, A.SYNTHESE,
    A.DIFFICULTES, A.SWOT, A.VERBATIMS, A.AXES, A.MATRICE_PRIORISATION, A.FICHE_RECO,
]
# Deck de la mission démo aujourd'hui (24 slides), figé : un build sans plan doit
# le reproduire à l'identique.
CLASSEMENT_DEMO = [
    A.COUVERTURE, A.SOMMAIRE, A.CHAPITRE, A.EXECUTIVE_SUMMARY, A.CHAPITRE,
    *[A.SYNTHESE] * 5, A.DIFFICULTES, A.SWOT, A.MATURITE, A.CHAPITRE, A.VERBATIMS,
    A.CHAPITRE, A.AXES, A.MATRICE_PRIORISATION, *[A.FICHE_RECO] * 4, A.KPIS, A.RISQUES,
]


def setup_module() -> None:
    init_db()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _build(plan=None, **kw):
    mid = seed()
    db = SessionLocal()
    try:
        m = db.get(Mission, mid)
        return build_presentation(m, axes_etude=axes_of(db, m), plan=plan, **kw)
    finally:
        db.close()


def _flux(prs) -> io.BytesIO:
    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf


def _titres(prs) -> list[str]:
    return [s.shapes.title.text_frame.text if s.shapes.title is not None else "" for s in prs.slides]


def test_classer_chaque_slide_du_deck_exemple() -> None:
    slides = Presentation(str(EXEMPLE)).slides
    assert [classer_slide(s) for s in slides] == CLASSEMENT_EXEMPLE


def test_extraire_plan_ordre_attendu() -> None:
    assert extraire_plan(EXEMPLE) == PLAN_EXEMPLE
    # Flux binaire accepté (chemin de l'upload).
    assert extraire_plan(io.BytesIO(EXEMPLE.read_bytes())) == PLAN_EXEMPLE


def test_deck_regenere_chaque_slide_retrouve_son_archetype() -> None:
    prs = _build()
    assert [classer_slide(s) for s in prs.slides] == CLASSEMENT_DEMO


def test_build_sans_plan_identique_au_build_actuel() -> None:
    """Fixture DORÉE : produite par le code d'avant US5.2 (47aaed7, extrait par
    `git archive`, jamais de checkout) — pas une liste écrite à la main."""
    dore = json.loads((FIXTURES / "deck_sans_plan_demo.json").read_text(encoding="utf-8"))
    sans = _build()
    assert len(sans.slides) == dore["nb_slides"]
    assert [[str(classer_slide(s)), t] for s, t in zip(sans.slides, _titres(sans))] == dore["slides"]
    assert [classer_slide(s) for s in sans.slides] == CLASSEMENT_DEMO


def test_fiche_reco_prime_sur_un_mot_cle_du_titre() -> None:
    """Le titre libre d'une reco peut contenir « maturité » ou « risques »."""
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "2.3 — Mesurer la maturité et les risques et contrôles"
    assert classer_slide(s) is A.FICHE_RECO
    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    s2.shapes.title.text = "Indicateurs de suivi (2/3)"
    assert classer_slide(s2) is A.KPIS
    s3 = prs.slides.add_slide(prs.slide_layouts[5])
    s3.shapes.title.text = "Une slide libre du client"
    assert classer_slide(s3) is None


def test_build_avec_plan_reordonne_et_filtre() -> None:
    plan = [A.COUVERTURE, A.SOMMAIRE, A.CHAPITRE, A.FICHE_RECO, A.AXES, A.SWOT, A.VERBATIMS]
    prs = _build(plan=plan)
    assert [classer_slide(s) for s in prs.slides] == [
        A.COUVERTURE, A.SOMMAIRE, A.CHAPITRE, *[A.FICHE_RECO] * 4, A.AXES,
        A.CHAPITRE, A.SWOT, A.CHAPITRE, A.VERBATIMS,
    ]
    # Intercalaires numérotés dans l'ordre RÉEL, et sommaire qui le suit.
    chapitres = [s for s in prs.slides if classer_slide(s) is A.CHAPITRE]
    assert [c.shapes.title.text_frame.paragraphs[0].text for c in chapitres] == [
        "La trajectoire proposée", "Le diagnostic", "La parole des équipes"]
    numeros = [next(ph.text_frame.text for ph in c.placeholders if ph.placeholder_format.idx == 1)
               for c in chapitres]
    assert numeros == ["01", "02", "03"]
    texte_sommaire = " ".join(sh.text_frame.text for sh in prs.slides[1].shapes if sh.has_text_frame)
    pos = [texte_sommaire.index(t) for t in
           ("01", "La trajectoire proposée", "02", "Le diagnostic", "03", "La parole des équipes")]
    assert pos == sorted(pos)
    assert "Ce qu'il faut retenir" not in texte_sommaire
    assert "Matrice SWOT" in texte_sommaire and "Difficultés" not in texte_sommaire


def test_plan_sans_sommaire_ni_intercalaires() -> None:
    prs = _build(plan=[A.SWOT, A.EXECUTIVE_SUMMARY])
    assert [classer_slide(s) for s in prs.slides] == [A.COUVERTURE, A.SWOT, A.EXECUTIVE_SUMMARY]


def test_archetype_inconnu_ignore_et_journalise(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        prs = _build(plan=["inconnu", A.SWOT])
    assert [classer_slide(s) for s in prs.slides] == [A.COUVERTURE, A.SWOT]
    assert "inconnu" in caplog.text
    # Plan sans aucun archétype de contenu connu : ignoré, deck par défaut.
    with caplog.at_level(logging.WARNING):
        prs = _build(plan=["n_importe_quoi"])
    assert [classer_slide(s) for s in prs.slides] == CLASSEMENT_DEMO


# --------------------------------------------------------------------------- #
# Écran : téléverser / remplacer / retirer le deck d'exemple
# --------------------------------------------------------------------------- #
def test_upload_deck_exemple_refus_propres(client: TestClient) -> None:
    mid = seed()
    r = client.post(f"/missions/{mid}/pptx-exemple",
                    files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400 and ".pptx est attendu" in r.text
    r = client.post(f"/missions/{mid}/pptx-exemple",
                    files={"file": ("faux.pptx", b"not a real pptx", "application/octet-stream")})
    assert r.status_code == 400 and "invalide ou corrompu" in r.text
    buf = io.BytesIO()
    Presentation().save(buf)  # .pptx valide mais sans aucune slide reconnue
    r = client.post(f"/missions/{mid}/pptx-exemple",
                    files={"file": ("vide.pptx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 400 and "aucune slide de contenu reconnue" in r.text
    # Deck fait de seules slides de STRUCTURE (couverture, sommaire, intercalaire) :
    # même règle que l'export (plan_applicable) — refusé, jamais « actif » en silence.
    src = Presentation(str(EXEMPLE))
    for i in reversed(range(len(src.slides))):
        if i not in (0, 1, 2):
            rid = src.slides._sldIdLst[i].rId
            src.part.drop_rel(rid)
            del src.slides._sldIdLst[i]
    assert extraire_plan(_flux(src)) == [A.COUVERTURE, A.SOMMAIRE, A.CHAPITRE]
    r = client.post(f"/missions/{mid}/pptx-exemple",
                    files={"file": ("structure.pptx", _flux(src).getvalue(), "application/octet-stream")})
    assert r.status_code == 400 and "aucune slide de contenu reconnue dans ce deck" in r.text
    db = SessionLocal()
    try:
        assert db.get(Mission, mid).pptx_exemple_path is None
    finally:
        db.close()


def test_upload_plan_affiche_export_reordonne_puis_retrait(client: TestClient) -> None:
    mid = seed()
    r = client.post(f"/missions/{mid}/pptx-exemple", follow_redirects=False,
                    files={"file": ("exemple.pptx", EXEMPLE.read_bytes(), "application/octet-stream")})
    assert r.status_code == 303
    assert (PPTX_EXEMPLES_DIR / f"{mid}.pptx").exists()

    page = client.get(f"/missions/{mid}/synthese/apercu").text
    assert "Deck d'exemple actif" in page and "Remplacer le deck d'exemple" in page
    assert page.index("Executive Summary</li>") < page.index("Fiches recommandation</li>")

    # Le plan de l'exemple n'a ni maturité, ni KPIs, ni risques : filtrés.
    r = client.get(f"/missions/{mid}/export/pptx")
    assert r.status_code == 200
    got = [classer_slide(s) for s in Presentation(io.BytesIO(r.content)).slides]
    assert got == [a for a in CLASSEMENT_DEMO if a not in (A.MATURITE, A.KPIS, A.RISQUES)]

    r = client.post(f"/missions/{mid}/pptx-exemple/retirer", follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    try:
        assert db.get(Mission, mid).pptx_exemple_path is None
    finally:
        db.close()
    assert "Aucun deck d'exemple" in client.get(f"/missions/{mid}/synthese/apercu").text
    r = client.get(f"/missions/{mid}/export/pptx")
    assert [classer_slide(s) for s in Presentation(io.BytesIO(r.content)).slides] == CLASSEMENT_DEMO


def test_deck_exemple_disparu_ne_casse_pas_l_export(client: TestClient) -> None:
    mid = seed()
    client.post(f"/missions/{mid}/pptx-exemple",
                files={"file": ("exemple.pptx", EXEMPLE.read_bytes(), "application/octet-stream")})
    (PPTX_EXEMPLES_DIR / f"{mid}.pptx").write_bytes(b"corrompu depuis")
    assert client.get(f"/missions/{mid}/synthese/apercu").status_code == 200
    r = client.get(f"/missions/{mid}/export/pptx")
    assert r.status_code == 200
    assert [classer_slide(s) for s in Presentation(io.BytesIO(r.content)).slides] == CLASSEMENT_DEMO
    client.post(f"/missions/{mid}/pptx-exemple/retirer")


def test_chapitre_pas_de_faux_positif_sur_un_chiffre_court() -> None:
    """Repli de forme CHAPITRE : une slide de contenu portant « 12 » n'est pas un
    intercalaire ; l'intercalaire DESSINÉ (deck sans layout de marque) l'est."""
    prs = Presentation()
    blanc = prs.slide_layouts[6]
    s = prs.slides.add_slide(blanc)
    for texte in ("Chiffres clés", "12"):
        s.shapes.add_textbox(0, 0, 100, 100).text_frame.text = texte
    assert classer_slide(s) is None
    s2 = prs.slides.add_slide(blanc)
    for texte in ("12", "entretiens menés", "sur 3 sites"):
        s2.shapes.add_textbox(0, 0, 100, 100).text_frame.text = texte
    assert classer_slide(s2) is None
    _slide_chapitre(prs, 2, "Le diagnostic", "#0E2356")
    assert classer_slide(prs.slides[-1]) is A.CHAPITRE


def test_chapitre_pas_de_faux_positif_sur_une_carte_chiffre_cle() -> None:
    """Carte chiffre-clé d'un deck client — « 12 » puis sa légende, dans cet ordre :
    la règle « 2 textes dont le 1er est NN » ne suffit pas, le 2e texte doit
    ressembler à un intitulé de chapitre. Sinon la carte est classée intercalaire et
    sort du plan de contenu, ce qui fait disparaître des slides de l'export."""
    prs = Presentation()
    blanc = prs.slide_layouts[6]
    carte = prs.slides.add_slide(blanc)
    for texte in ("12", "risques identifiés"):
        carte.shapes.add_textbox(0, 0, 100, 100).text_frame.text = texte
    assert classer_slide(carte) is None
    # Même légende capitalisée : composée en petit corps, ce n'est pas un titre.
    carte2 = prs.slides.add_slide(blanc)
    for texte, pt in (("12", 44), ("Risques identifiés", 10.5)):
        tf = carte2.shapes.add_textbox(0, 0, 100, 100).text_frame
        tf.text = texte
        tf.paragraphs[0].runs[0].font.size = Pt(pt)
    assert classer_slide(carte2) is None
    # Un vrai intercalaire DESSINÉ reste classé CHAPITRE.
    _slide_chapitre(prs, 2, "Le diagnostic", "#0E2356")
    assert classer_slide(prs.slides[-1]) is A.CHAPITRE


def test_extraire_plan_fichier_memoise_par_mtime(tmp_path, monkeypatch) -> None:
    import app.services.pptx_export.archetypes as mod
    chemin = tmp_path / "ex.pptx"
    chemin.write_bytes(EXEMPLE.read_bytes())
    appels = []
    vrai = mod.extraire_plan
    monkeypatch.setattr(mod, "extraire_plan", lambda p: appels.append(p) or vrai(p))
    assert extraire_plan_fichier(chemin) == PLAN_EXEMPLE
    assert extraire_plan_fichier(chemin) == PLAN_EXEMPLE
    assert len(appels) == 1
    import os
    st = chemin.stat()
    os.utime(chemin, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))  # nouvel envoi
    extraire_plan_fichier(chemin)
    assert len(appels) == 2


def test_cases_hors_plan_decochees_et_desactivees(client: TestClient) -> None:
    import re
    mid = seed()
    page = client.get(f"/missions/{mid}/synthese/apercu").text
    assert "hors du plan du deck d'exemple" not in page
    assert " disabled" not in re.search(r'id="pptx-config-form".*?</form>', page, re.S).group(0)
    client.post(f"/missions/{mid}/pptx-exemple",
                files={"file": ("exemple.pptx", EXEMPLE.read_bytes(), "application/octet-stream")})
    page = client.get(f"/missions/{mid}/synthese/apercu").text
    form = re.search(r'id="pptx-config-form".*?</form>', page, re.S).group(0)
    etat = {m.group(1): m.group(2).strip() for m in
            re.finditer(r'<input type="checkbox" name="(\w+)" value="true"\s*(checked|disabled)>', form)}
    # Le plan de l'exemple n'a ni maturité, ni KPIs, ni risques.
    assert etat == {
        "sommaire": "checked", "executive_summary": "checked", "synthese": "checked",
        "difficultes": "checked", "swot": "checked", "maturite": "disabled",
        "verbatims": "checked", "axes_overview": "checked", "matrix": "checked",
        "kpis": "disabled", "risques": "disabled",
    }
    assert form.count("hors du plan du deck d'exemple") == 3
    assert "La couverture est toujours gardée" in page
    client.post(f"/missions/{mid}/pptx-exemple/retirer")
