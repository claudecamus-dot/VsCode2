"""Garde géométrique d'un .pptx, déterministe (0 token) : sortie plafonnée (~20 lignes + total).

Bloquant (code 1) : HORS_SLIDE (forme qui sort du cadre, groupes aplatis dans le repère slide) et
DEBORDEMENT_TEXTE (texte estimé plus haut que sa boîte, sans autofit). Les lignes de dimension nulle sont ignorées.
Avertissements : CHEVAUCHEMENT (deux formes à texte qui se recouvrent, via pptx_deck.shapes_overlap),
CONTRASTE (ratio WCAG < 4,5 texte/fond ; couleur inconnue ou thème modifié = ignoré, pas échec),
MINI_POLICE (police explicite < N pt, bloquante avec --strict). Information : SLIDE_MASQUEE (show="0").
--baseline f.json : les constats dont la clé "slide|code|forme" y figure sont tolérés ; une entrée qui n'existe
plus est signalée BASELINE_PERIMEE (dette payée, à retirer) et bloque avec --strict.
Estimation de débordement = heuristique sans police (0,5 em par caractère) : elle trie, le rendu tranche.

Usage : py garde_rendu.py deck.pptx [--mini-police N] [--strict] [--baseline f.json] [--max-lignes 20]
Code : 0 propre, 1 défaut bloquant, 2 fichier illisible.
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BLOQUANTS = {"HORS_SLIDE", "DEBORDEMENT_TEXTE"}
MARGE_EMU = 9525
SEUIL_CHEVAUCHEMENT = 0.15
SCHEME = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}
_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _chevauche(b1, b2):
    try:
        sys.path.insert(0, os.path.join(HERE, "..", "..", "pptx-deck", "scripts"))
        from pptx_deck import shapes_overlap
        return shapes_overlap(b1, b2)
    except Exception:  # noqa: BLE001 - repli identique si pptx-deck est absent de la cible
        return b1[0] < b2[0] + b2[2] and b2[0] < b1[0] + b1[2] and b1[1] < b2[1] + b2[3] and b2[1] < b1[1] + b1[3]


def feuilles(shapes, dx=0, dy=0, sx=1.0, sy=1.0, sans_geo=None):
    """(forme, l, t, w, h) en EMU absolus slide ; groupes parcourus avec leur transformation enfant -> slide."""
    for sh in shapes:
        try:
            l, t, w, h = sh.left, sh.top, sh.width, sh.height
            if None in (l, t, w, h):
                if sans_geo is not None:
                    sans_geo.append(sh)
                continue
            al, at, aw, ah = dx + l * sx, dy + t * sy, w * sx, h * sy
        except Exception:  # noqa: BLE001
            continue
        if getattr(sh, "shape_type", None) is not None and "GROUP" in str(sh.shape_type):
            try:
                x = sh._element.grpSpPr.xfrm
                cw, ch = x.chExt.cx or w, x.chExt.cy or h
                nsx, nsy = (w / cw) * sx, (h / ch) * sy
                yield from feuilles(sh.shapes, al - x.chOff.x * nsx, at - x.chOff.y * nsy, nsx, nsy, sans_geo)
            except Exception:  # noqa: BLE001
                continue
        else:
            yield sh, al, at, aw, ah


def _theme(prs):
    try:
        import lxml.etree as et
        from pptx.opc.constants import RELATIONSHIP_TYPE as RT
        blob = prs.slide_master.part.part_related_by(RT.THEME).blob
        out = {}
        for el in et.fromstring(blob).iter(_NS + "clrScheme"):
            for c in el:
                for v in c:
                    out[c.tag.replace(_NS, "")] = v.get("val") or v.get("lastClr")
        return out
    except Exception:  # noqa: BLE001
        return {}


def _couleur(solid, theme):
    """RRGGBB d'un <a:solidFill>, None si inconnue (schéma modifié, absent, preset)."""
    if solid is None:
        return None
    for c in solid:
        nom = c.tag.replace(_NS, "")
        if nom == "srgbClr" and not len(c):
            return c.get("val")
        if nom == "schemeClr" and not len(c):
            v = theme.get(SCHEME.get(c.get("val"), c.get("val")))
            return v if v and len(v) == 6 else None
    return None


def _lum(hexa):
    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (f(int(hexa[i:i + 2], 16) / 255) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _txt(sh):
    try:
        return sh.text_frame.text if sh.has_text_frame else ""
    except Exception:  # noqa: BLE001
        return ""


def _paire_titre(a, b):
    """Titre + sous-titre du gabarit : emboîtés par construction, pas un défaut."""
    def nom(sh):
        try:
            return sh.placeholder_format.type.name if sh.is_placeholder else ""
        except Exception:  # noqa: BLE001
            return ""
    return {nom(a), nom(b)} <= {"TITLE", "CENTER_TITLE", "SUBTITLE"} and "" not in (nom(a), nom(b))


def _cadre_texte(sh):
    try:
        if sh.has_text_frame and sh.text_frame.text.strip():
            return sh.text_frame
    except Exception:  # noqa: BLE001
        pass
    return None


def _hauteur_estimee(tf, largeur_emu):
    """Hauteur estimée en EMU (0,5 em/caractère, interligne 1,2) ; None si une taille manque (pas de verdict)."""
    total = 0.0
    for p in tf.paragraphs:
        tailles = [r.font.size.pt for r in p.runs if r.font.size]
        if p.runs and not tailles:
            return None
        pt = max(tailles) if tailles else 12
        par_ligne = max(1, int(largeur_emu / 12700 / (pt * 0.5)))
        lignes = max(1, -(-len(p.text) // par_ligne))
        total += lignes * pt * 1.2
    return total * 12700


def _contraste(sh, tf, fond, theme):
    """Premier run dont le contraste est < 4,5 : (ratio, fg, bg) ; None sinon ou si une couleur est inconnue."""
    try:
        bg = _couleur(sh._element.spPr.find(_NS + "solidFill"), theme) or fond
        if not bg:
            return None
        for p in tf.paragraphs:
            for r in p.runs:
                rpr = r._r.rPr
                fg = _couleur(rpr.find(_NS + "solidFill"), theme) if rpr is not None else None
                if fg and r.text.strip() and ratio(fg, bg) < 4.5:
                    return ratio(fg, bg), fg, bg
    except Exception:  # noqa: BLE001
        pass
    return None


def analyser(prs, mini_police=None, theme=None, stats=None):
    """Constats {slide, code, forme, detail, bloquant}."""
    W, H = prs.slide_width, prs.slide_height
    theme = _theme(prs) if theme is None else theme
    out = []
    stats = {} if stats is None else stats
    stats.update(sans_geometrie=0, taille_non_resolue=0)

    def ajouter(i, code, forme, detail, texte=""):
        out.append({"slide": i, "code": code, "forme": forme, "detail": detail, "bloquant": code in BLOQUANTS,
                    "empreinte": hashlib.sha1(" ".join(texte.split())[:60].encode("utf-8")).hexdigest()[:6]})

    for i, slide in enumerate(prs.slides, 1):
        if slide._element.get("show") == "0":
            ajouter(i, "SLIDE_MASQUEE", "-", "slide masquée (show=0) : livrée mais invisible en projection")
        try:
            fond = _couleur(slide._element.cSld.bg.bgPr.find(_NS + "solidFill"), theme)
        except Exception:  # noqa: BLE001
            fond = None
        textuelles = []
        sans_geo = []
        for sh, l, t, w, h in feuilles(slide.shapes, sans_geo=sans_geo):
            if w == 0 or h == 0:
                continue
            if l < -MARGE_EMU or t < -MARGE_EMU or l + w > W + MARGE_EMU or t + h > H + MARGE_EMU:
                ajouter(i, "HORS_SLIDE", sh.name, "dépasse le cadre de la slide", _txt(sh))
            tf = _cadre_texte(sh)
            if tf is None:
                continue
            textuelles.append((sh, l, t, w, h))
            bp = tf._txBody.find(_NS + "bodyPr")
            auto = bp is not None and (bp.find(_NS + "normAutofit") is not None or bp.find(_NS + "spAutoFit") is not None)
            est = None if auto else _hauteur_estimee(tf, w)
            if est is None and not auto:
                stats["taille_non_resolue"] += 1
            if est is not None and est > h * 1.15 + 12700 * 4:
                ajouter(i, "DEBORDEMENT_TEXTE", sh.name, f"texte ~{est / 12700:.0f} pt > boîte {h / 12700:.0f} pt", tf.text)
            if mini_police:
                tailles = [r.font.size.pt for p in tf.paragraphs for r in p.runs if r.font.size and r.text.strip()]
                if tailles and min(tailles) < mini_police:
                    ajouter(i, "MINI_POLICE", sh.name, f"{min(tailles):g} pt < {mini_police:g} pt", tf.text)
            c = _contraste(sh, tf, fond, theme)
            if c:
                ajouter(i, "CONTRASTE", sh.name, f"ratio {c[0]:.1f} < 4,5 (#{c[1]} sur #{c[2]})", tf.text)
        stats["sans_geometrie"] += len(sans_geo)
        for a in range(len(textuelles)):
            for b in range(a + 1, len(textuelles)):
                (s1, l1, t1, w1, h1), (s2, l2, t2, w2, h2) = textuelles[a], textuelles[b]
                if _chevauche((l1, t1, w1, h1), (l2, t2, w2, h2)):
                    iw = min(l1 + w1, l2 + w2) - max(l1, l2)
                    ih = min(t1 + h1, t2 + h2) - max(t1, t2)
                    if iw * ih > SEUIL_CHEVAUCHEMENT * min(w1 * h1, w2 * h2) and not _paire_titre(s1, s2):
                        ajouter(i, "CHEVAUCHEMENT", f"{s1.name} / {s2.name}", "deux formes à texte se recouvrent", _txt(s1) + _txt(s2))
    return out


def cle(c):
    """Clé de baseline SANS numéro de slide (insérer une slide ne décale pas la dette) : code, forme, empreinte du texte."""
    return f"{c['code']}|{c['forme']}|{c['empreinte']}"


def appliquer_baseline(constats, baseline):
    """(nouveaux, perimees) : constats absents de la baseline, entrées de baseline sans constat."""
    connus = set(baseline)
    vus = {cle(c) for c in constats}
    return [c for c in constats if cle(c) not in connus], sorted(connus - vus)


def main(argv=None):
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("deck")
    ap.add_argument("--mini-police", type=float, default=None)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--max-lignes", type=int, default=20)
    a = ap.parse_args(argv)
    try:
        from pptx import Presentation
        prs = Presentation(a.deck)
    except Exception as exc:  # noqa: BLE001
        print(f"garde_rendu : fichier illisible ({a.deck} : {exc}).", file=sys.stderr)
        return 2
    stats = {}
    constats = analyser(prs, a.mini_police, stats=stats)
    perimees = []
    if a.baseline:
        try:
            with open(a.baseline, encoding="utf-8") as fh:
                base = json.load(fh)
            if not isinstance(base, list):
                raise ValueError("une liste de clés est attendue")
        except (OSError, ValueError) as exc:
            print(f"garde_rendu : baseline illisible ({a.baseline} : {exc}).", file=sys.stderr)
            return 2
        constats, perimees = appliquer_baseline(constats, base)
    constats.sort(key=lambda c: (not c["bloquant"], c["slide"]))
    lignes = [f"slide {c['slide']} | {c['code']} | {c['forme']} | {c['detail']}" for c in constats]
    lignes += [f"BASELINE_PERIMEE | {k} (dette payée : à retirer)" for k in perimees]
    for ligne in lignes[:a.max_lignes]:
        print(ligne)
    if len(lignes) > a.max_lignes:
        print(f"... {len(lignes) - a.max_lignes} ligne(s) de plus non affichées")
    bloq = [c for c in constats if c["bloquant"] or (a.strict and c["code"] != "SLIDE_MASQUEE")]
    print(f"TOTAL {len(constats)} constat(s), {len(bloq)} bloquant(s)"
          + (f", {len(perimees)} baseline périmée(s)" if perimees else "")
          + (f", {stats['sans_geometrie']} sans géométrie" if stats["sans_geometrie"] else "")
          + (f", {stats['taille_non_resolue']} taille non résolue" if stats["taille_non_resolue"] else ""))
    return 1 if bloq or (a.strict and perimees) else 0


if __name__ == "__main__":
    sys.exit(main())
