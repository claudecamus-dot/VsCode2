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
    _taille_max_pt,
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
    # « Base de l'analyse » ouvre le diagnostic : sur quelle matiere il repose,
    # avant ce qu'il dit (incr. I1, 2026-09-28).
    A.BASE_ANALYSE,
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
    # `include_base_analyse=False` : la slide « Base de l'analyse » (incr. I1,
    # 2026-09-28) n'existait pas dans le code qui a PRODUIT cette fixture. La
    # desactiver ici garde la fixture doree intacte — la reecrire a la main lui
    # ferait perdre ce qui fait sa valeur (elle ne serait plus le temoin d'un
    # code anterieur, mais une liste alignee sur le code courant).
    sans = _build(include_base_analyse=False)
    assert len(sans.slides) == dore["nb_slides"]
    assert [[str(classer_slide(s)), t] for s, t in zip(sans.slides, _titres(sans))] == dore["slides"]
    assert [classer_slide(s) for s in sans.slides] == [
        a for a in CLASSEMENT_DEMO if a is not A.BASE_ANALYSE
    ]


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

    # Le plan de l'exemple n'a ni maturité, ni KPIs, ni risques, ni base de
    # l'analyse : filtrés. Un plan FILTRE, il ne crée jamais — un archétype
    # ajouté au produit après le deck d'exemple reste donc absent.
    r = client.get(f"/missions/{mid}/export/pptx")
    assert r.status_code == 200
    got = [classer_slide(s) for s in Presentation(io.BytesIO(r.content)).slides]
    assert got == [a for a in CLASSEMENT_DEMO
                   if a not in (A.MATURITE, A.KPIS, A.RISQUES, A.BASE_ANALYSE)]

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


# Formulation VRAIE dans les DEUX cas de `_plan_exemple` : un deck peut s'ouvrir
# parfaitement et ne livrer aucun plan reconnaissable. « illisible » était faux la
# moitié du temps à l'écran (le journal serveur, lui, distingue les deux causes et
# garde le mot pour le seul cas où il est exact).
_PHRASE_PLAN_NON_RECONNU = ("Plan du deck d'exemple non reconnu : toutes les slides "
                            "ci-dessous sont proposées dans l'ordre par défaut")
# L'encart « Deck d'exemple » dit la même chose autrement (deux emplacements, deux
# phrases distinctes) : lui aussi ne doit plus affirmer que le fichier est illisible.
_PHRASE_ENCART_NON_EXPLOITE = "Deck d'exemple non exploité — aucun plan de slides n'en a été reconnu"


def _deck_structure_seule() -> bytes:
    """Deck LISIBLE mais fait de seules slides de structure : `plan_applicable`
    le refuse, donc `_plan_exemple` rend None pour l'autre raison."""
    src = Presentation(str(EXEMPLE))
    for i in reversed(range(len(src.slides))):
        if i not in (0, 1, 2):
            rid = src.slides._sldIdLst[i].rId
            src.part.drop_rel(rid)
            del src.slides._sldIdLst[i]
    return _flux(src).getvalue()


def test_apercu_annonce_un_deck_exemple_sans_plan_et_logs_des_deux_causes(
        client: TestClient, caplog) -> None:
    """Deck d'exemple posé mais sans plan : « Définition des slides » DIT que la
    liste n'est ni filtrée ni réordonnée, avec l'ancre vers l'encart du deck. La
    ligne est absente quand le plan existe, et les deux causes (fichier illisible
    vs deck lisible sans slide de contenu) se distinguent au journal.

    Les DEUX phrases d'écran doivent rester vraies dans les deux cas : un deck qui
    s'ouvre sans problème et ne livre simplement aucun plan ne doit pas s'entendre
    dire qu'il est illisible (le consultant en conclut que l'app est cassée)."""
    mid = seed()
    client.post(f"/missions/{mid}/pptx-exemple",
                files={"file": ("exemple.pptx", EXEMPLE.read_bytes(), "application/octet-stream")})
    page = client.get(f"/missions/{mid}/synthese/apercu").text
    assert 'id="deck-exemple"' in page  # l'ancre visée existe bien sur la page
    assert _PHRASE_PLAN_NON_RECONNU not in page  # plan présent : rien à annoncer
    assert _PHRASE_ENCART_NON_EXPLOITE not in page

    # Cause 1 : fichier devenu illisible.
    (PPTX_EXEMPLES_DIR / f"{mid}.pptx").write_bytes(b"corrompu depuis")
    with caplog.at_level(logging.WARNING, logger="app.routers.export"):
        page = client.get(f"/missions/{mid}/synthese/apercu").text
    assert _PHRASE_PLAN_NON_RECONNU in page and 'href="#deck-exemple"' in page
    assert _PHRASE_ENCART_NON_EXPLOITE in page
    assert any("Deck d'exemple illisible" in r.getMessage() for r in caplog.records)
    assert not any("sans plan de contenu reconnu" in r.getMessage() for r in caplog.records)

    # Cause 2 : deck lisible, mais aucune slide de contenu reconnue. L'écran dit la
    # même chose que pour la cause 1 — et n'affirme PAS que le fichier est illisible.
    caplog.clear()
    (PPTX_EXEMPLES_DIR / f"{mid}.pptx").write_bytes(_deck_structure_seule())
    with caplog.at_level(logging.WARNING, logger="app.routers.export"):
        page = client.get(f"/missions/{mid}/synthese/apercu").text
    assert _PHRASE_PLAN_NON_RECONNU in page
    assert _PHRASE_ENCART_NON_EXPLOITE in page
    assert "Deck d'exemple illisible —" not in page
    assert "Deck d'exemple illisible :" not in page
    assert any("sans plan de contenu reconnu" in r.getMessage() for r in caplog.records)
    assert not any("Deck d'exemple illisible" in r.getMessage() for r in caplog.records)
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


def _slide_deux_textes(prs, *paires) -> object:
    """Slide « NN » + intitulé, chaque texte donné en (texte, [tailles pt]) — une
    taille None laisse le run HÉRITER du layout, cas normal d'un gabarit client."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for texte, tailles in paires:
        tf = slide.shapes.add_textbox(0, 0, 100, 100).text_frame
        morceaux = texte if isinstance(texte, tuple) else (texte,)
        tf.text = morceaux[0]
        for m in morceaux[1:]:
            tf.paragraphs[0].add_run().text = m
        for run, pt in zip(tf.paragraphs[0].runs, tailles):
            if pt is not None:
                run.font.size = Pt(pt)
    return slide


def test_chapitre_discrimine_sur_le_rapport_des_tailles() -> None:
    """Repli de forme CHAPITRE : « NN » + intitulé se discrimine sur le RAPPORT
    intitulé/nombre (intercalaire dessiné 20/44 ≈ 0.45 ; carte chiffre-clé
    10.5/44 ≈ 0.24), et sur rien d'autre.

    Remplace `test_chapitre_pas_de_faux_positif_sur_une_carte_chiffre_cle`, dont
    les assertions encodaient la MAUVAISE règle : elles épinglaient des
    heuristiques de capitalisation / longueur / ponctuation qui rejettent un
    intercalaire client légitime (« vers une gouvernance partagée : »), et ses
    deux cas fixaient des tailles explicites — donc elles ne voyaient pas que la
    règle d'avant classait CHAPITRE une carte chiffre-clé à tailles héritées."""
    prs = Presentation()
    # Carte chiffre-clé, tailles EXPLICITES : légende en petit corps → pas un
    # intercalaire (rapport 10.5/44 ≈ 0.24).
    carte = _slide_deux_textes(prs, ("12", [44]), ("Risques identifiés", [10.5]))
    assert classer_slide(carte) is None
    # Carte chiffre-clé aux tailles HÉRITÉES du layout : le cas que le test
    # d'avant manquait. La règle d'avant court-circuitait à vrai (taille None) et
    # classait CHAPITRE — une slide de contenu perdue à l'export.
    heritee = _slide_deux_textes(prs, ("42", [None]), ("Entretiens menés", [None]))
    assert classer_slide(heritee) is None
    # Intercalaire client légitime : intitulé en minuscules, long, finissant par
    # « : » — mais composé à l'échelle d'un titre. C'est un CHAPITRE.
    client = _slide_deux_textes(
        prs, ("03", [44]),
        ("vers une gouvernance partagée des données et des usages métiers :", [20]))
    assert classer_slide(client) is A.CHAPITRE
    # Texte DUPLIQUÉ (deux formes portent le même intitulé, l'une à 8pt) : la
    # mesure porte sur TOUTES les formes (max), pas sur la première rencontrée —
    # sinon l'intercalaire est mesuré à 8pt et rejeté. Mesuré sur l'aide
    # elle-même : côté `classer_slide`, une 3e forme fait déjà sortir la slide de
    # la règle « exactement 2 textes ».
    dup = _slide_deux_textes(prs, ("Le diagnostic", [8]), ("Le diagnostic", [20]))
    assert _taille_max_pt(dup, "Le diagnostic") == 20
    # ...et une seule forme non mesurable suffit à rendre la mesure inconnue.
    mixte = _slide_deux_textes(prs, ("Le diagnostic", [8]), ("Le diagnostic", [None]))
    assert _taille_max_pt(mixte, "Le diagnostic") is None
    # Héritage PARTIEL (1er run hérité, 2e explicite à 8pt) : taille inconnue,
    # on ne conclut pas — surtout pas « 8pt donc une carte ».
    partiel = _slide_deux_textes(prs, ("05", [44]), (("Le diag", "nostic"), [None, 8]))
    assert classer_slide(partiel) is None
    # Un vrai intercalaire DESSINÉ reste classé CHAPITRE.
    _slide_chapitre(prs, 2, "Le diagnostic", "#0E2356")
    assert classer_slide(prs.slides[-1]) is A.CHAPITRE


_NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _poser_sz_layout(layout, idx_ph: int, sz: int) -> None:
    """Pose `lvl1pPr/defRPr@sz` dans le lstStyle du placeholder `idx_ph` du
    LAYOUT — la taille vient du gabarit, pas de la slide."""
    from lxml import etree
    lst = layout.placeholders[idx_ph]._element.txBody.find(_NS_A + "lstStyle")
    for vieux in list(lst):
        lst.remove(vieux)
    lvl = etree.SubElement(lst, _NS_A + "lvl1pPr")
    etree.SubElement(lvl, _NS_A + "defRPr").set("sz", str(sz))


def _slide_placeholders(prs, numero: str, libelle: str, layout: int = 1):
    """« NN » dans le titre, intitulé dans le corps, runs NUS (aucune taille sur
    la slide) : tout vient du layout ou du master."""
    slide = prs.slides.add_slide(prs.slide_layouts[layout])
    slide.shapes.title.text_frame.text = numero
    slide.placeholders[1].text_frame.text = libelle
    for sh in slide.placeholders:
        for r in (r for p in sh.text_frame.paragraphs for r in p.runs):
            assert r.font.size is None
    return slide


def test_chapitre_tailles_heritees_du_gabarit_resolues() -> None:
    """Intercalaire d'un gabarit client : tailles HÉRITÉES du LAYOUT, runs nus.
    La règle d'avant refusait de conclure (None) et l'export perdait
    l'intercalaire ; la taille portée par le layout est désormais résolue. Le
    master, générique au gabarit, n'est PAS consulté (cf. `_sz_pt`)."""
    # Tailles portées par les placeholders du LAYOUT : 20 / 44 ≈ 0.45.
    prs = Presentation()
    _poser_sz_layout(prs.slide_layouts[1], 0, 4400)
    _poser_sz_layout(prs.slide_layouts[1], 1, 2000)
    s = _slide_placeholders(prs, "01", "Le diagnostic")
    assert _taille_max_pt(s, "01") == 44
    assert _taille_max_pt(s, "Le diagnostic") == 20
    assert classer_slide(s) is A.CHAPITRE
    # Layout muet : les seules tailles sont celles du MASTER (titre 44, corps
    # 32), valables pour toute paire titre + corps du gabarit — elles ne disent
    # rien de CETTE slide. Non résolu → refus de conclure.
    prs2 = Presentation()
    s2 = _slide_placeholders(prs2, "02", "Les recommandations")
    assert _taille_max_pt(s2, "Les recommandations") is None
    assert classer_slide(s2) is None


@pytest.mark.parametrize("layout", [1, 4, 0], ids=[
    "title_and_content", "comparison", "title_slide"])
def test_carte_chiffre_cle_placeholders_layout_muet_pas_chapitre(layout) -> None:
    """Régression : carte chiffre-clé « 12 » + légende dans les placeholders
    titre + corps d'un layout qui ne déclare AUCUNE taille. Remonter au master
    (titleStyle 44 / bodyStyle 32, rapport 0.73) classait CHAPITRE et sortait la
    slide du plan de contenu, donc de l'export."""
    prs = Presentation()
    s = _slide_placeholders(prs, "12", "risques identifies", layout=layout)
    assert classer_slide(s) is None


@pytest.mark.parametrize("attr,valeur", [
    ("sz", "12.5"), ("sz", "abc"), ("lvl", "x")])
def test_valeur_xml_illisible_ne_leve_pas(attr, valeur) -> None:
    """Deck d'exemple fourni par l'utilisateur : une valeur XML illisible vaut
    « inconnue » (None), jamais une exception."""
    from lxml import etree
    prs = Presentation()
    s = _slide_placeholders(prs, "01", "Le diagnostic")
    for sh in s.placeholders:
        p = sh.text_frame.paragraphs[0]._p
        if attr == "lvl":
            p.get_or_add_pPr().set("lvl", valeur)
        else:
            r_pr = p.find(_NS_A + "r").find(_NS_A + "rPr")
            if r_pr is None:
                r_pr = etree.SubElement(p.find(_NS_A + "r"), _NS_A + "rPr")
                p.find(_NS_A + "r").insert(0, r_pr)
            r_pr.set("sz", valeur)
    assert classer_slide(s) is None


def test_carte_chiffre_cle_legende_heritee_petite_pas_chapitre() -> None:
    """Symétrique : la légende hérite d'un PETIT corps du layout (10.5 contre 44)
    — carte chiffre-clé, jamais CHAPITRE. Le layout prime sur le 32pt du master."""
    prs = Presentation()
    _poser_sz_layout(prs.slide_layouts[1], 0, 4400)
    _poser_sz_layout(prs.slide_layouts[1], 1, 1050)
    s = _slide_placeholders(prs, "12", "risques identifiés")
    assert _taille_max_pt(s, "risques identifiés") == 10.5
    assert classer_slide(s) is None


def test_taille_irresoluble_reste_none() -> None:
    """Aucun niveau de la chaîne ne porte de taille : None, jamais un défaut
    inventé (qui rouvrirait le faux positif)."""
    prs = Presentation()
    for style in prs.slide_masters[0]._element.iter(_NS_A + "defRPr"):
        style.attrib.pop("sz", None)
    s = _slide_placeholders(prs, "01", "Le diagnostic")
    assert _taille_max_pt(s, "01") is None
    assert classer_slide(s) is None


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
    # Le plan de l'exemple n'a ni maturité, ni KPIs, ni risques, ni base de
    # l'analyse (archétype ajouté au produit après ce deck d'exemple).
    assert etat == {
        "sommaire": "checked", "executive_summary": "checked",
        "base_analyse": "disabled", "synthese": "checked",
        "difficultes": "checked", "swot": "checked", "maturite": "disabled",
        "verbatims": "checked", "axes_overview": "checked", "matrix": "checked",
        "kpis": "disabled", "risques": "disabled",
    }
    assert form.count("hors du plan du deck d'exemple") == 4
    assert "La couverture est toujours gardée" in page
    client.post(f"/missions/{mid}/pptx-exemple/retirer")
