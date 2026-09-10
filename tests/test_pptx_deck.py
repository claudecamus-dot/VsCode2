"""Tests de `paginer_items` (pptx_deck.py) — bin-packing pur, sans
dépendance à python-pptx/DB/HTTP, utilisé par la pagination auto de
l'export PPT (voir pptx_export.py).

Complété (arbitrage 2026-09-03) par les tests des « helpers durcis deck
binaire » remontés depuis VSCode4 (clear_slides, purger_rels_slides_orphelines,
sans_puce) et des formes (add_forme, definir_geometrie, configurer_text_frame,
trouver_cadre_layout) — même esprit que le reste du fichier : aucune dépendance
au domaine métier/DB de ce projet, la bibliothèque doit rester réutilisable
telle quelle par la flotte.

Les tests de trouver_slide_par_titre, supprimer_slide, _normaliser,
definir_paragraphes et add_text_runs sont partis avec leurs fonctions le
2026-09-10 (arbitrage utilisateur) : cinq helpers que ce dépôt n'a jamais
appelés. Ils restent dans l'historique git et chez VSCode4."""
from __future__ import annotations

import pytest
from pptx import Presentation
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches

from app.services.pptx_deck import (
    add_forme,
    add_rect,
    clear_slides,
    configurer_text_frame,
    definir_geometrie,
    paginer_items,
    purger_rels_slides_orphelines,
    sans_puce,
    trouver_cadre_layout,
)


def test_paginer_items_splits_when_capacity_exceeded() -> None:
    assert paginer_items([1, 1, 1, 1], lambda x: x, 2) == [[1, 1], [1, 1]]


def test_paginer_items_single_page_when_everything_fits() -> None:
    assert paginer_items([1, 1, 1], lambda x: x, 10) == [[1, 1, 1]]


def test_paginer_items_oversized_item_alone_on_its_page() -> None:
    assert paginer_items([3], lambda x: x, 2) == [[3]]
    assert paginer_items([1, 3, 1], lambda x: x, 2) == [[1], [3], [1]]


def test_paginer_items_empty_input_returns_one_empty_page() -> None:
    assert paginer_items([], lambda x: x, 2) == [[]]


def test_paginer_items_preserves_order_and_drops_nothing() -> None:
    items = list(range(10))
    pages = paginer_items(items, lambda x: 1, 3)
    assert [x for page in pages for x in page] == items


# --------------------------------------------------------------------------- #
# Helpers durcis deck binaire (clear_slides, purger_rels_slides_orphelines) —
# priorité de test demandée : ce sont ceux qui manipulent des relations
# OOXML/slides, et une relation non lâchée rend le .pptx inouvrable.
# --------------------------------------------------------------------------- #


def _slide_rid(prs, slide) -> str:
    """rId de `slide` dans sldIdLst — pour vérifier qu'une relation est bien
    lâchée (ou au contraire orpheline) après manipulation."""
    for sld_id in prs.slides._sldIdLst:
        if int(sld_id.get("id")) == slide.slide_id:
            return sld_id.get(qn("r:id"))
    raise AssertionError("slide absente de sldIdLst")


def _slide_reltypes(prs) -> list[str]:
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    return [rid for rid, rel in prs.part.rels.items() if rel.reltype == RT.SLIDE]


def _new_prs_with_slides(n: int, titres=None):
    prs = Presentation()
    layout = prs.slide_layouts[6]  # layout vierge
    slides = []
    for i in range(n):
        slide = prs.slides.add_slide(layout)
        texte = (titres[i] if titres else f"Slide {i}")
        box = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(3), Inches(1))
        box.text_frame.text = texte
        slides.append(slide)
    return prs, slides


def test_clear_slides_retire_toutes_les_slides_et_lache_les_rels() -> None:
    prs, _ = _new_prs_with_slides(3)
    assert len(prs.slides) == 3
    assert len(_slide_reltypes(prs)) == 3
    clear_slides(prs)
    assert len(prs.slides) == 0
    assert list(prs.slides._sldIdLst) == []
    # Le point qui compte : plus AUCUNE relation de type slide ne traîne
    # (sinon PowerPoint refuse d'ouvrir le fichier réserialisé).
    assert _slide_reltypes(prs) == []


def test_purger_rels_slides_orphelines_nettoie_sans_toucher_les_relations_actives() -> None:
    prs, (s0, s1) = _new_prs_with_slides(2)
    rid_orphelin = _slide_rid(prs, s1)
    # Reproduit la corruption historique : retirer l'entrée sldIdLst SANS
    # lâcher la relation (l'erreur que clear_slides évite).
    for sld_id in list(prs.slides._sldIdLst):
        if sld_id.get(qn("r:id")) == rid_orphelin:
            prs.slides._sldIdLst.remove(sld_id)
    assert rid_orphelin in prs.part.rels, "précondition : la relation orpheline existe encore"
    purges = purger_rels_slides_orphelines(prs)
    assert purges == 1
    assert rid_orphelin not in prs.part.rels
    # La relation de la slide restante (toujours référencée) n'est pas touchée.
    assert _slide_rid(prs, s0) in prs.part.rels
    assert purger_rels_slides_orphelines(prs) == 0, "un deck sain ne doit plus rien purger"


# --------------------------------------------------------------------------- #
# sans_puce
# --------------------------------------------------------------------------- #


def test_sans_puce_retire_indentation_et_force_buNone() -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1))
    p = box.text_frame.paragraphs[0]
    p.text = "01"
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", str(int(Inches(0.5))))
    pPr.set("indent", str(int(Inches(-0.25))))
    pPr.append(pPr.makeelement(qn("a:buChar"), {"char": "•"}))

    sans_puce(p)

    pPr = p._p.get_or_add_pPr()
    assert pPr.get("marL") == "0"
    assert pPr.get("indent") == "0"
    assert pPr.findall(qn("a:buChar")) == []
    assert pPr.findall(qn("a:buAutoNum")) == []
    assert pPr.findall(qn("a:buNone")) != [], "buNone absent — la puce peut survivre"


# --------------------------------------------------------------------------- #
# Formes : add_forme, definir_geometrie, configurer_text_frame,
# trouver_cadre_layout.
# --------------------------------------------------------------------------- #


def test_add_forme_applique_preset_adjustments_et_alpha() -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    shp = add_forme(slide, "roundRect", 1, 1, 2, 1, fill="#2c5cc5", adj=[0.2],
                     fill_alpha=50)
    spPr = shp._element.spPr
    geom = spPr.find(qn("a:prstGeom"))
    assert geom.get("prst") == "roundRect"
    gd = geom.find(qn("a:avLst")).find(qn("a:gd"))
    assert gd.get("fmla") == "val 20000"  # adj[0]=0.2 -> échelle OOXML 100000
    srgb = spPr.find(qn("a:solidFill")).find(qn("a:srgbClr"))
    alpha = srgb.find(qn("a:alpha"))
    assert alpha is not None and alpha.get("val") == "50000"  # 50 * 1000


def test_add_forme_prst_inconnu_leve_key_error() -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    with pytest.raises(KeyError):
        add_forme(slide, "prst_inexistant", 1, 1, 1, 1)


def test_definir_geometrie_pose_position_et_taille() -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    shp = add_rect(slide, 0, 0, 1, 1)
    definir_geometrie(shp, 2.5, 1.25, 4.0, 0.5)
    assert shp.left == Inches(2.5)
    assert shp.top == Inches(1.25)
    assert shp.width == Inches(4.0)
    assert shp.height == Inches(0.5)


def test_configurer_text_frame_ne_touche_que_les_champs_fournis() -> None:
    from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1))
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = Inches(0.3)

    configurer_text_frame(tf, anchor=MSO_ANCHOR.MIDDLE)  # wrap/autosize/margins omis

    assert tf.vertical_anchor == MSO_ANCHOR.MIDDLE
    assert tf.word_wrap is True, "word_wrap non fourni : ne doit pas être écrasé"
    assert tf.auto_size == MSO_AUTO_SIZE.NONE
    assert tf.margin_left == Inches(0.3), "marge non fournie : ne doit pas être écrasée"


def test_trouver_cadre_layout_desambigue_par_largeur_minimale() -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_forme(slide, "round2DiagRect", 0.5, 0.5, 1.0, 1.0)  # petit cadre, à ignorer
    grand = add_forme(slide, "round2DiagRect", 2.0, 2.0, 3.0, 2.0)  # grand cadre, attendu

    trouve = trouver_cadre_layout(slide.shapes, "round2DiagRect", largeur_min_in=2.0)
    assert trouve is not None
    left, top, width, height, geom, flip = trouve
    assert Emu(left).inches == pytest.approx(2.0)
    assert Emu(width).inches == pytest.approx(3.0)
    assert geom.get("prst") == "round2DiagRect"
    assert flip == (False, False)
    assert grand.left == left


def test_trouver_cadre_layout_renvoie_none_si_aucun_preset_ne_matche() -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_forme(slide, "ellipse", 0, 0, 1, 1)
    assert trouver_cadre_layout(slide.shapes, "round2DiagRect") is None
