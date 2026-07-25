"""Infra image de l'export PPT : photos Openverse CC0 avec repli procédural
offline, cache séparé par source, cadres OCTO (teardrop des intercalaires,
round2DiagRect des images de contenu). Extrait de pptx_export.py (découpage
du gros module, finding audit 2026-07-24) — code déplacé tel quel."""
from __future__ import annotations

import os
from pathlib import Path

from pptx.oxml.ns import qn
from pptx.util import Emu, Inches

# --- Cadres photo (têtes de chapitre, P3) : skill pptx-framed-image (greffé,
# présent dans .claude/skills/). Import gardé — si le skill/Pillow manque, les
# intercalaires retombent proprement sur leur version texte-seul (cf. _slide_chapitre).
_FRAMED_OK = False
try:  # pragma: no cover - dépend de la présence du skill + Pillow
    import sys as _sys
    _FRAMED_SCRIPTS = Path(__file__).resolve().parents[3] / ".claude" / "skills" / "pptx-framed-image" / "scripts"
    if str(_FRAMED_SCRIPTS) not in _sys.path:
        _sys.path.insert(0, str(_FRAMED_SCRIPTS))
    import nature_images as _nature_images  # type: ignore
    import stock_images as _stock_images  # type: ignore  # fetch Openverse (vraies photos CC0)
    from framed_image import cover_crop_to_aspect as _cover_crop_to_aspect  # type: ignore
    from framed_image import place_image_in_frame as _place_image_in_frame  # type: ignore
    _IMG_CACHE = Path(__file__).resolve().parents[3] / "data" / "pptx_chapitre_images"
    _FRAMED_OK = True
except Exception:  # skill absent, Pillow non installé, etc. -> repli texte-seul
    _FRAMED_OK = False

# Scène -> requête photo Openverse (vraies photos CC0, comme les decks OCTO réels /
# VSCode3-4). Repli sur la génération procédurale `nature_images` (nom = la scène).
_SCENE_REQUETE = {
    "sunset": "sunset sky",
    "mountains": "mountains landscape",
    "forest": "green forest sunlight",
    "ocean": "turquoise water",
}


def _find_teardrop_frame(shapes):
    """`(left, top, width, height, geom)` du cadre photo teardrop d'un layout
    (le layout « 50 - Chapitre » place son cadre en top-level, pas dans un groupe),
    ou None. Même principe que pptx-framed-image.frame_geometry, cas non groupé."""
    for sh in shapes:
        spPr = getattr(sh._element, "spPr", None)
        if spPr is None:
            continue
        g = spPr.find(qn("a:prstGeom"))
        if g is not None and g.get("prst") == "teardrop":
            return sh.left, sh.top, sh.width, sh.height, g
    return None


def _resoudre_image_cachee(base: str, scene: str, seed: int, aspect: float,
                           px_w: int, px_h: int, requete: str):
    """Résout l'image d'une zone : vraie photo Openverse CC0 si le fetch est permis
    et réussit, repli procédural sinon — avec un cache SÉPARÉ PAR SOURCE
    (`…_photo.png` / `…_proc.png`). Leçon 2026-07-22 (demande « ne pas générer des
    images ») : avec un seul slot de cache, un repli procédural écrit pendant un run
    de tests (PPTX_NO_PHOTO_FETCH=1, posé par conftest) restait servi À VIE par le
    serveur — le slot photo n'était jamais retenté. Ici un échec de fetch remplit le
    slot `_proc` sans jamais occuper `_photo` : le prochain run en ligne re-tente la
    vraie photo. Renvoie le Path de l'image utilisable."""
    import hashlib
    _IMG_CACHE.mkdir(parents=True, exist_ok=True)
    no_fetch = os.environ.get("PPTX_NO_PHOTO_FETCH") == "1"
    # Le slot photo dépend AUSSI de la requête : affiner une requête doit
    # re-déclencher un fetch, pas resservir l'ancien résultat mis en cache.
    qh = hashlib.md5(requete.encode("utf-8")).hexdigest()[:6]
    photo = _IMG_CACHE / f"{base}_{qh}_photo.png"
    proc = _IMG_CACHE / f"{base}_proc.png"
    if not no_fetch:
        if photo.exists():
            return photo
        # Échelle de retry (constat 2026-07-22, slides 7/10 restées procédurales) :
        # les échecs Openverse sont INTERMITTENTS (SSL sporadique) → 2 tentatives ;
        # et une requête précise peut n'avoir AUCUN résultat CC0 pour cet aspect →
        # repli sur la requête simplifiée « {scene} photography » avant d'abandonner.
        ar = "wide" if aspect > 1.15 else "tall" if aspect < 0.85 else "square"
        variantes = [requete]
        simple = f"{scene} photography"
        if simple != requete:
            variantes.append(simple)
        for req in variantes:
            for _tentative in range(2):
                try:
                    brut = _IMG_CACHE / f"_brut_{scene}_{seed}.jpg"
                    _stock_images.fetch_to(str(brut), req, seed=seed, aspect_ratio=ar)
                    _cover_crop_to_aspect(str(brut), str(photo), aspect)
                    return photo
                except Exception:
                    continue  # réseau/API KO : tentative/variante suivante
        # tout a échoué : repli procédural ci-dessous, slot photo intact
    if not proc.exists():
        _nature_images.generate_to(str(proc), scene, px_w, px_h, seed=seed)
    return proc


def _remplir_cadre_chapitre(slide, cadre, scene: str, seed: int = 0) -> None:
    """Remplit le cadre teardrop d'un intercalaire avec une image à l'aspect exact
    du cadre — vraie photo Openverse via _resoudre_image_cachee (repli procédural).
    Silencieux sur échec : l'intercalaire reste lisible sans image."""
    if not _FRAMED_OK or cadre is None:
        return
    try:
        left, top, width, height, geom = cadre
        aspect = Emu(width).inches / Emu(height).inches
        px_w = 900
        px_h = max(1, int(round(px_w / aspect)))
        path = _resoudre_image_cachee(
            f"{scene}_{seed}_{px_w}x{px_h}", scene, seed, aspect, px_w, px_h,
            requete=_SCENE_REQUETE.get(scene, scene),
        )
        _place_image_in_frame(slide, str(path), left, top, width, height, geom=geom)
    except Exception:
        pass  # repli : intercalaire sans image, jamais un export cassé


def _image_dans_zone(slide, left, top, width, height, scene: str, requete: str,
                     seed: int = 0) -> bool:
    """Pose une photo (Openverse CC0, repli procédural offline) cover-croppée à
    l'aspect de la zone, dans un rectangle — pattern « claim + visuel » (P3b, repéré
    sur les decks OCTO réels VSCode4). PPTX_NO_PHOTO_FETCH=1 force le procédural
    (tests). Renvoie True si posée, False sinon (silencieux — jamais un export cassé)."""
    if not _FRAMED_OK:
        return False
    try:
        aspect = width / height
        px_w = 900
        px_h = max(1, int(round(px_w / aspect)))
        path = _resoudre_image_cachee(
            f"zone_{scene}_{seed}_{px_w}x{px_h}", scene, seed, aspect, px_w, px_h,
            requete=requete,
        )
        pic = slide.shapes.add_picture(str(path), Inches(left), Inches(top),
                                       Inches(width), Inches(height))
        _clip_octo_frame(pic)  # cadre OCTO round2DiagRect (format image VSCode3)
        return True
    except Exception:
        return False


def _clip_octo_frame(pic) -> None:
    """Clippe une image au cadre OCTO « round2DiagRect » (rectangle à 2 coins diagonaux
    arrondis, 2 vifs) — format « image encadrée » des slides de contenu VSCode3, au lieu
    d'un rectangle plat. python-pptx n'expose pas prstGeom sur une image -> XML direct."""
    try:
        spPr = pic._element.spPr
        for tag in ("a:prstGeom", "a:custGeom"):
            for el in spPr.findall(qn(tag)):
                spPr.remove(el)
        geom = spPr.makeelement(qn("a:prstGeom"), {"prst": "round2DiagRect"})
        av = geom.makeelement(qn("a:avLst"), {})
        for name, val in (("adj1", "33000"), ("adj2", "0")):
            av.append(av.makeelement(qn("a:gd"), {"name": name, "fmla": f"val {val}"}))
        geom.append(av)
        spPr.append(geom)
    except Exception:
        pass  # image en rectangle plat si le clip échoue — jamais un export cassé
