"""Tests for the pptx-framed-image skill.

Run standalone:  python tests/test_framed_image.py
(also discoverable by pytest). No network, no template needed — a blank
Presentation is built in-memory.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from pptx import Presentation
from pptx.util import Emu
from pptx.oxml.ns import qn

import framed_image as FI
import nature_images as NI

FRAME = (6270019, 304800, 2593200, 3705000)  # a real OCTO frame's bounds (EMU)
ADJ1, ADJ2 = 50000, 0


def _tmp_img(w=180, h=257, kind="sunset"):
    p = os.path.join(tempfile.gettempdir(), f"_nat_{kind}_{w}x{h}.png")
    NI.generate_to(p, kind, w, h, seed=1)
    return p


def test_round2diag_geom_values():
    g = FI.round2diag_geom(ADJ1, ADJ2)
    assert g.get("prst") == "round2DiagRect"
    gds = g.findall(qn("a:avLst") + "/" + qn("a:gd"))
    vals = {gd.get("name"): gd.get("fmla") for gd in g.iter(qn("a:gd"))}
    assert vals == {"adj1": f"val {ADJ1}", "adj2": f"val {ADJ2}"}, vals
    print("ok  round2diag_geom builds correct preset + adjustments")


def test_picture_gets_frame_geometry_and_bounds():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    img = _tmp_img()
    pic = FI.place_image_in_frame(slide, img, *FRAME, geom=FI.round2diag_geom(ADJ1, ADJ2))

    # geometry cloned onto the picture (not a plain rect, not a custom path)
    spPr = pic._element.spPr
    prst = spPr.find(qn("a:prstGeom"))
    assert prst is not None and prst.get("prst") == "round2DiagRect"
    assert spPr.find(qn("a:custGeom")) is None
    # exactly one geometry child
    assert len(spPr.findall(qn("a:prstGeom"))) == 1

    # placed exactly at the frame bounds
    assert (pic.left, pic.top, pic.width, pic.height) == tuple(Emu(v) for v in FRAME)

    # geometry sits right after xfrm (schema order) so PowerPoint accepts it
    kids = [c.tag for c in spPr]
    assert kids.index(qn("a:xfrm")) < kids.index(qn("a:prstGeom"))
    print("ok  picture is clipped to the frame preset, at the frame bounds, schema-ordered")


def test_picture_within_slide_bounds():
    prs = Presentation()
    sw, sh = prs.slide_width, prs.slide_height
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    pic = FI.place_image_in_frame(slide, _tmp_img(), *FRAME, geom=FI.round2diag_geom())
    assert pic.left >= 0 and pic.top >= 0
    assert pic.left + pic.width <= sw, "picture runs off the right edge"
    assert pic.top + pic.height <= sh, "picture runs off the bottom edge"
    print("ok  picture stays inside the slide")


def test_none_geom_leaves_plain_rect():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    pic = FI.place_image_in_frame(slide, _tmp_img(), *FRAME, geom=None)
    g = pic._element.spPr.find(qn("a:prstGeom"))
    # default is a plain rectangle (or no explicit geometry) — never the frame preset
    assert g is None or g.get("prst") == "rect"
    print("ok  geom=None leaves the picture a plain rectangle")


def test_nature_scenes_size_and_mode():
    for kind in NI.SCENES:
        im = NI.generate(kind, 240, 343, seed=3)
        assert im.size == (240, 343), (kind, im.size)
        assert im.mode == "RGB", (kind, im.mode)
    print(f"ok  {len(NI.SCENES)} nature scenes render at the requested size")


def test_saved_image_reopens_at_frame_aspect():
    # aspect the caller must feed (frame w/h) — generator honours exact px size
    w, h = 700, int(700 / (FRAME[2] / FRAME[3]))
    p = _tmp_img(w, h, "mountains")
    from PIL import Image
    assert Image.open(p).size == (w, h)
    print("ok  generated image matches the requested frame-aspect size")


def test_cover_crop_matches_frame_aspect_without_stretch():
    from PIL import Image
    frame_aspect = FRAME[2] / FRAME[3]  # ~0.700
    src = _tmp_img(500, 683, "forest")  # ~0.732, wrong for the frame
    assert abs(Image.open(src).size[0] / Image.open(src).size[1] - frame_aspect) > 0.02
    dst = os.path.join(tempfile.gettempdir(), "_cropped_forest.png")
    FI.cover_crop_to_aspect(src, dst, frame_aspect)
    w, h = Image.open(dst).size
    assert abs(w / h - frame_aspect) < 0.005, (w, h, w / h, frame_aspect)
    # cover-crop trims one axis only; height is preserved when the source is too wide
    assert h == 683 and w < 500
    print("ok  cover_crop_to_aspect matches the frame aspect (no stretch)")


def _inject_layout_shape(slide, xml):
    """Append a raw shape to the slide's LAYOUT spTree (for the audit tests)."""
    from pptx.oxml import parse_xml
    spTree = slide.slide_layout._element.find(
        qn("p:cSld") + "/" + qn("p:spTree"))
    spTree.append(parse_xml(xml))


def test_frame_obstructions_flags_edge_line_and_clean_is_empty():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout
    fl, ft, fw, fh = FRAME
    # clean to start
    assert FI.frame_obstructions(slide, *FRAME) == []
    # a stray dark vertical line sitting exactly on the frame's right edge
    x_edge = fl + fw
    line = (
        f'<p:cxnSp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        f'xmlns:a="{FI._A}"><p:nvCxnSpPr>'
        f'<p:cNvPr id="909" name="StrayLine"/><p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{x_edge}" y="{ft + 100000}"/>'
        f'<a:ext cx="0" cy="1500000"/></a:xfrm>'
        f'<a:prstGeom prst="straightConnector1"><a:avLst/></a:prstGeom>'
        f'<a:ln w="9525"><a:solidFill><a:srgbClr val="0E2356"/></a:solidFill></a:ln>'
        f'</p:spPr></p:cxnSp>'
    )
    _inject_layout_shape(slide, line)
    found = FI.frame_obstructions(slide, *FRAME)
    ids = {f["id"] for f in found}
    assert "909" in ids, found
    assert any(f["stroke"] is True for f in found)
    print("ok  frame_obstructions flags a stray edge line and is empty when clean")


def test_frame_obstructions_ignores_covered_inner_shape():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    fl, ft, fw, fh = FRAME
    # a stroked rect well inside the frame -> fully covered by the picture -> ignore
    inner = (
        f'<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        f'xmlns:a="{FI._A}"><p:nvSpPr>'
        f'<p:cNvPr id="910" name="InnerCovered"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{fl + fw // 3}" y="{ft + fh // 3}"/>'
        f'<a:ext cx="{fw // 4}" cy="{fh // 4}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f'<a:ln w="9525"><a:solidFill><a:srgbClr val="0E2356"/></a:solidFill></a:ln>'
        f'</p:spPr></p:sp>'
    )
    _inject_layout_shape(slide, inner)
    ids = {f["id"] for f in FI.frame_obstructions(slide, *FRAME)}
    assert "910" not in ids, "inner covered shape should not be flagged"
    print("ok  frame_obstructions ignores a shape fully covered by the frame")


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED")


if __name__ == "__main__":
    main()


# --- Garde de fetch_to : l'URL vient d'un agrégateur tiers -------------------
# Trouvé par la session VSCode3 le 2026-09-01, en instruisant son finding
# `ppt-toolkit.md` : sa copie locale portait une garde que les 6 autres copies de la
# flotte n'avaient pas — dont celle-ci, la SOURCE du kit. La doctrine de resynchro
# écrite chez elle aurait donc SUPPRIMÉ un correctif de sécurité.
#
# Openverse agrège Wikimedia, Flickr, StockSnap… : `img_url` est une donnée qu'on ne
# contrôle pas. Or `urllib.request.urlopen` suit le schéma `file://` par défaut —
# vérifié en une ligne : il lit un fichier local. Une entrée dont l'`url` ne serait
# pas http(s) faisait donc recopier un fichier arbitraire du poste dans le cache
# d'images du deck. Et `r.read()` sans plafond charge toute la réponse en mémoire.

import importlib.util as _iu
import os as _os

_SPEC = _iu.spec_from_file_location(
    "stock_images_test",
    _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                  "scripts", "stock_images.py"))
_stock = _iu.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_stock)


class TestFetchToRefuseCeQuIlNAPasDemande:
    def test_une_url_file_est_refusee(self, tmp_path, monkeypatch):
        """Le cas prouvé : sans garde, le deck embarque un fichier du poste."""
        secret = tmp_path / "secret.txt"
        secret.write_text("CONTENU_LOCAL", encoding="utf-8")
        monkeypatch.setattr(_stock, "search_photo",
                            lambda *a, **k: (secret.as_uri(), "qui", "ou"))
        cible = tmp_path / "image.jpg"
        try:
            _stock.fetch_to(str(cible), "peu importe")
        except ValueError:
            return                      # refus explicite : c'est le comportement voulu
        assert "CONTENU_LOCAL" not in cible.read_text(encoding="utf-8", errors="replace"), (
            "fetch_to a recopie un fichier LOCAL dans le cache d'images du deck")

    def test_une_url_http_normale_passe_toujours(self, tmp_path, monkeypatch):
        """La garde ne doit pas casser le cas nominal — sinon on a remplacé une
        faille par une panne."""
        class _Reponse:
            def __init__(self):
                self._restant = [b"octets-image"]
            def read(self, n=None):
                return self._restant.pop(0) if self._restant else b""
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        monkeypatch.setattr(_stock, "search_photo",
                            lambda *a, **k: ("https://exemple.test/p.jpg", "qui", "ou"))
        monkeypatch.setattr(_stock.urllib.request, "urlopen", lambda *a, **k: _Reponse())
        cible = tmp_path / "ok.jpg"
        _stock.fetch_to(str(cible), "peu importe")
        assert cible.read_bytes() == b"octets-image"

    def test_une_reponse_demesuree_est_interrompue(self, tmp_path, monkeypatch):
        """`r.read()` sans plafond charge toute la réponse en mémoire : un serveur
        tiers décide donc de la mémoire de la machine."""
        class _Fleuve:
            def read(self, n=None):
                return b"x" * (n or 65536)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        monkeypatch.setattr(_stock, "search_photo",
                            lambda *a, **k: ("https://exemple.test/gros.jpg", "q", "o"))
        monkeypatch.setattr(_stock.urllib.request, "urlopen", lambda *a, **k: _Fleuve())
        cible = tmp_path / "gros.jpg"
        # `pytest.raises` et non un try/except tolérant : sans plafond, `r.read()` sans
        # argument rend un seul bloc et le fichier reste petit — le test passerait sans
        # rien prouver. C'est le REFUS qui doit être exigé.
        import pytest as _pytest
        with _pytest.raises(ValueError):
            _stock.fetch_to(str(cible), "peu importe")

    def test_le_fichier_partiel_est_efface_quand_le_plafond_saute(self, tmp_path,
                                                                  monkeypatch):
        """Le plafond arrête bien le téléchargement — mais laissait sur disque les
        25 Mo déjà écrits (mesuré : 26 214 400 octets), sous le nom de l'image
        attendue. Le deck aurait embarqué un fichier tronqué, ou le cache aurait
        grossi d'un fichier que personne ne réclame. Trouvé par la re-cotation
        d'audit du 2026-09-01, quelques heures après le correctif lui-même."""
        class _Fleuve:
            def read(self, n=None):
                return b"x" * (n or 65536)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        monkeypatch.setattr(_stock, "search_photo",
                            lambda *a, **k: ("https://exemple.test/gros.jpg", "q", "o"))
        monkeypatch.setattr(_stock.urllib.request, "urlopen", lambda *a, **k: _Fleuve())
        cible = tmp_path / "gros.jpg"
        import pytest as _pytest
        with _pytest.raises(ValueError):
            _stock.fetch_to(str(cible), "peu importe")
        assert not cible.exists(), (
            f"fichier partiel laisse sur disque "
            f"({cible.stat().st_size if cible.exists() else 0} octets)")
