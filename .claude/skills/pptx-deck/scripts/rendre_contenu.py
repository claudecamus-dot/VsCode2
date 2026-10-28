"""Rendu deterministe d'un plan JSON : le LLM ecrit du CONTENU, ce script pose la geometrie.

Table de recettes (nom -> fonction) ; chacune valide ses donnees (longueurs max, nombre
d'elements), lit couleurs et police dans le THEME du deck, choisit une taille de police qui
tient (plancher TYPE['tiny']) et finit par verifier_geometrie + verifier_debordements_texte +
« toute forme reste dans la zone ». Un refus est ATOMIQUE (aucune forme laissee) et leve
ValueError (donnees) ou RecetteDefaut (geometrie).

  title_bullets {bullets: [2-6 textes <=110]}
  two_column    {left: {title<=30, points: [2-6 <=90]}, right: {...}}
  kpi_row       {kpis: [{value<=8, label<=40, note<=60 opt}]}  (delegue a pptx_recettes)
  table         {header: [2-6 textes <=24], rows: [1-8 lignes de meme largeur, cellules <=60]}
  Tailles : 24 pt au plus, 10,5 pt au plus bas ; avertissement « sous-remplissage » (stderr, exit 0)
  si le contenu occupe moins de 25 % de la hauteur utile.

Plan : {"slides": [{"titre": "...", "recette": "<nom>", "data": {...}}]} (voir valider_plan.py).
CLI : py rendre_contenu.py --gabarit GABARIT.pptx PLAN.json --sortie OUT.pptx
      (sans --gabarit : gabarit python-pptx par defaut). Sortie 0 ok ; 1 refus de contenu ou defaut
      de geometrie ; 2 usage / fichier illisible. Rien n'est ecrit si une slide est refusee.
"""
import argparse
import json
import os
import sys

_ICI = os.path.dirname(os.path.abspath(__file__))
for _p in (_ICI, os.path.join(_ICI, "..", "..", "..", "dispositif", "canon")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)

import pptx_deck as D  # noqa: E402
import pptx_recettes as R  # noqa: E402
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN  # noqa: E402
from pptx.util import Emu, Inches, Pt  # noqa: E402

RecetteDefaut = R.RecetteDefaut
TAILLE_MAX = 24.0    # corps lisible quand le contenu est court (pptx_recettes plafonne a 12)
TAILLE_MIN = 10.5    # plancher de lisibilite pptx-deck
SOUS_REMPLISSAGE = 0.25  # contenu < 25 % de la hauteur utile : avertissement

# nom -> (maxi d'elements de liste, limites) ; source unique de validation ET de lister()
LIMITES = {
    "title_bullets": {"bullets": (2, 6, 110)},
    "two_column": {"points": (2, 6, 90), "title": 30},
    "table": {"header": (2, 6, 24), "rows": (1, 8), "cellule": 60},
}


def _liste(valeur, mini, maxi, maxlen, chemin):
    if not isinstance(valeur, list) or not mini <= len(valeur) <= maxi:
        n = len(valeur) if isinstance(valeur, list) else type(valeur).__name__
        raise ValueError(f"{chemin}: attendu {mini} a {maxi} textes, recu {n}")
    for i, v in enumerate(valeur):
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{chemin}[{i}]: texte non vide attendu")
        if len(v) > maxlen:
            raise ValueError(f"{chemin}[{i}]: {len(v)} caracteres, max {maxlen}")


def _cles(data, attendues, nom):
    if not isinstance(data, dict):
        raise ValueError(f"{nom}: data doit etre un objet")
    inconnues = sorted(set(data) - set(attendues))
    if inconnues or set(attendues) - set(data):
        raise ValueError(f"{nom}: cles attendues {sorted(attendues)}, recu {sorted(data)}")


def valider(nom, data):
    """ValueError precise si `data` sort des limites de la recette `nom`."""
    if nom == "title_bullets":
        _cles(data, ("bullets",), nom)
        _liste(data["bullets"], *LIMITES[nom]["bullets"][:2], LIMITES[nom]["bullets"][2], f"{nom}.bullets")
    elif nom == "two_column":
        _cles(data, ("left", "right"), nom)
        mn, mx, ml = LIMITES[nom]["points"]
        for cote in ("left", "right"):
            _cles(data[cote], ("title", "points"), f"{nom}.{cote}")
            _liste([data[cote]["title"]], 1, 1, LIMITES[nom]["title"], f"{nom}.{cote}.title")
            _liste(data[cote]["points"], mn, mx, ml, f"{nom}.{cote}.points")
    elif nom == "table":
        _cles(data, ("header", "rows"), nom)
        mn, mx, ml = LIMITES[nom]["header"]
        _liste(data["header"], mn, mx, ml, f"{nom}.header")
        rows = data["rows"]
        rmn, rmx = LIMITES[nom]["rows"]
        if not isinstance(rows, list) or not rmn <= len(rows) <= rmx:
            raise ValueError(f"{nom}.rows: attendu {rmn} a {rmx} lignes")
        for i, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != len(data["header"]):
                raise ValueError(f"{nom}.rows[{i}]: {len(data['header'])} cellules attendues")
            _liste(row, len(row), len(row), LIMITES[nom]["cellule"], f"{nom}.rows[{i}]")


def title_bullets(slide, data, zone, ctx):
    x, y, w, h = zone
    textes = R._puces(data["bullets"])
    inner = w - 0.2
    t = R._fit(textes, inner, TAILLE_MAX, TAILLE_MIN, lambda s: sum(R._h(b, inner, s) for b in textes), h, "title_bullets")
    R._texte(slide, ctx, x + 0.1, y, inner, [R._para(ctx, b, t, ctx["ink"]) for b in textes])


def two_column(slide, data, zone, ctx):
    x, y, w, h = zone
    cw = (w - R.GAP) / 2
    inner = cw - 0.3
    cotes = [data["left"], data["right"]]
    pts = [R._puces(c["points"]) for c in cotes]
    h_titre = max(R._h(c["title"], inner, D.TYPE["h3"]) for c in cotes)

    def contenu(s):
        return max(sum(R._h(b, inner, s) for b in p) for p in pts) + h_titre + 0.15

    t = R._fit(sum(pts, []), inner, TAILLE_MAX, TAILLE_MIN, contenu, h - 0.5, "two_column")
    ch = contenu(t) + 0.5  # panneaux a la hauteur de LEUR contenu, pas de la zone
    for i, (c, p) in enumerate(zip(cotes, pts)):
        cx = x + i * (cw + R.GAP)
        R._rect(slide, cx, y, cw, ch, ctx["panel"], rounded=True)
        R._rect(slide, cx, y, cw, 0.07, ctx["accents"][i % len(ctx["accents"])])
        R._texte(slide, ctx, cx + 0.15, y + 0.2, inner, [R._para(ctx, c["title"], D.TYPE["h3"], ctx["ink"], bold=True)])
        R._texte(slide, ctx, cx + 0.15, y + 0.2 + R._h(c["title"], inner, D.TYPE["h3"]) + 0.15, inner,
                 [R._para(ctx, b, t, ctx["ink"]) for b in p])


def table(slide, data, zone, ctx):
    x, y, w, h = zone
    header, rows = data["header"], data["rows"]
    n = len(header)
    cw = w / n
    inner = cw - 0.2
    toutes = [c for r in [header] + rows for c in r]

    def haut(s):
        return sum(max(R._h(c, inner, s) for c in r) + 0.12 for r in [header] + rows)

    t = R._fit(toutes, inner, TAILLE_MAX, TAILLE_MIN, haut, h, "table")
    gf = slide.shapes.add_table(len(rows) + 1, n, Inches(x), Inches(y), Inches(w), Inches(h))
    tbl = gf.table
    for j in range(n):
        tbl.columns[j].width = Inches(cw)
    hauteurs = [max(R._h(c, inner, t) for c in r) + 0.12 for r in [header] + rows]
    for i, r in enumerate([header] + rows):
        tbl.rows[i].height = Inches(hauteurs[i])
        for j, c in enumerate(r):
            cell = tbl.cell(i, j)
            cell.fill.solid()
            cell.fill.fore_color.rgb = D.rgb(ctx["accents"][0] if i == 0 else (ctx["panel"] if i % 2 == 0 else ctx["paper"]))
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.LEFT
            run = p.add_run()
            run.text = c
            run.font.size = Pt(t)
            run.font.bold = i == 0
            run.font.name = ctx["font"]
            run.font.color.rgb = D.rgb(ctx["paper"] if i == 0 else ctx["ink"])
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    gf.height = Inches(sum(hauteurs))


RECETTES = {"title_bullets": title_bullets, "two_column": two_column, "table": table}
NOMS = tuple(RECETTES) + ("kpi_row",)


def lister():
    return "\n".join(f"{n}" for n in NOMS)


def _remplissage(slide, zone, avant):
    """Part de la hauteur utile occupee par les formes posees (0..1)."""
    nouvelles = [sh for sh in slide.shapes if sh.shape_id not in avant]
    if not nouvelles:
        return 0.0
    haut = min(Emu(sh.top).inches for sh in nouvelles)
    bas = max(Emu(sh.top + sh.height).inches for sh in nouvelles)
    return (bas - haut) / zone[3]


def rendre(slide, nom, data, zone=None):
    """Pose la recette `nom` sur `slide` depuis `data` seule (atomique). Renvoie la liste des
    avertissements (sous-remplissage) ; ValueError / RecetteDefaut en cas de refus."""
    if nom not in NOMS:
        raise ValueError(f"recette inconnue {nom!r} ; disponibles : {sorted(NOMS)}")
    zone_in = R._zone_in(slide, zone)
    avant = {sh.shape_id for sh in slide.shapes}
    if nom == "kpi_row":
        R.recette(slide, "kpi_row", data, zone)
    else:
        valider(nom, data)
        ctx = R._ctx(slide)
        try:
            RECETTES[nom](slide, data, zone_in, ctx)
            R._controler(slide, zone_in, avant)
        except Exception:
            for sh in list(slide.shapes):
                if sh.shape_id not in avant:
                    sh._element.getparent().remove(sh._element)
            raise
    part = _remplissage(slide, zone_in, avant)
    if part < SOUS_REMPLISSAGE:
        return [f"sous-remplissage : {nom} occupe {part:.0%} de la hauteur utile (< {SOUS_REMPLISSAGE:.0%}) : "
                "ajouter du contenu ou choisir une recette plus compacte"]
    return []


def _layout_titre_seul(prs):
    for lay in prs.slide_layouts:
        if lay.name.strip().lower() in ("title only", "titre seul", "titre uniquement"):
            return lay
    return prs.slide_layouts[5] if len(prs.slide_layouts) > 5 else prs.slide_layouts[-1]


def construire(prs, plan):
    """Ajoute une slide par entree du plan ; renvoie (slides, avertissements). ValueError/RecetteDefaut au 1er refus."""
    out, avert = [], []
    for i, s in enumerate(plan["slides"], 1):
        sl = prs.slides.add_slide(_layout_titre_seul(prs))
        if sl.shapes.title is not None:
            sl.shapes.title.text = s["titre"]
        try:
            avert += [f"slide {i}: {w}" for w in rendre(sl, s["recette"], s.get("data", {}))]
        except (ValueError, RecetteDefaut) as e:
            raise type(e)(f"slide {i} ({s['titre'][:40]}): {e}") from e
        out.append(sl)
    return out, avert


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan")
    ap.add_argument("--gabarit")
    ap.add_argument("--sortie", required=True)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    from pptx import Presentation
    try:
        with open(a.plan, encoding="utf-8") as fh:
            plan = json.load(fh)
        if not isinstance(plan, dict) or not isinstance(plan.get("slides"), list):
            raise ValueError('racine attendue : {"slides": [...]}')
        prs = Presentation(a.gabarit) if a.gabarit else Presentation()
    except (OSError, ValueError) as e:
        print(f"rendre_contenu : {e}", file=sys.stderr)
        return 2
    try:
        _, avert = construire(prs, plan)
    except (ValueError, RecetteDefaut, KeyError, TypeError) as e:
        print(f"rendre_contenu REFUS {e}", file=sys.stderr)
        return 1
    for w in avert:
        print("SOUS-REMPLISSAGE" if "sous-remplissage" in w else "AVERTISSEMENT", w, file=sys.stderr)
    defauts = list(D.verifier_geometrie(prs)) + list(D.verifier_debordements_texte(prs))
    if defauts:
        for d in defauts:
            print("GEOMETRIE DEFAUT", d, file=sys.stderr)
        return 1
    prs.save(a.sortie)
    print(f"rendre_contenu : {len(plan['slides'])} slide(s) -> {a.sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
