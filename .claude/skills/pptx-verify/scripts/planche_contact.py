"""Planche contact : N rendus PNG d'une meme revue -> UNE image (1 lecture au lieu de N).

Reutilise les PNG produits par rendu_slide.py (`sNNN.png`) ; aucun rendu ici, aucun COM.
Chaque vignette porte son numero de slide (lu dans le nom `sNNN.png`, sinon son rang d'entree).

CLI : planche_contact.py --sortie PLANCHE.png [--colonnes 3] [--largeur 480] PNG [PNG ...]
      planche_contact.py --sortie PLANCHE.png --dossier DIR [--rangs 3,5,8]
Sortie : 0 planche ecrite ; 1 un PNG demande manque ou est illisible (rien n'est ecrit) ;
2 usage. La planche sert a REGARDER : elle ne remplace pas un rendu pleine taille pour un detail.
"""
import argparse
import os
import re
import sys


def _numero(chemin, defaut):
    m = re.fullmatch(r"s(\d+)\.png", os.path.basename(chemin), re.I)
    return int(m.group(1)) if m else defaut


def planche(pngs, sortie, colonnes=3, largeur=480):
    """Ecrit la planche ; renvoie (largeur_px, hauteur_px). FileNotFoundError / OSError si un PNG manque."""
    from PIL import Image, ImageDraw
    if not pngs:
        raise ValueError("aucun PNG a assembler")
    if colonnes < 1 or largeur < 40:
        raise ValueError("colonnes >= 1 et largeur >= 40 attendues")
    vignettes = []
    for i, p in enumerate(pngs, 1):
        with Image.open(p) as im:
            im = im.convert("RGB")
            h = max(1, round(im.height * largeur / im.width))
            vignettes.append((_numero(p, i), im.resize((largeur, h))))
    cellule_h = max(v.height for _, v in vignettes)
    bandeau, marge = 18, 8
    lignes = -(-len(vignettes) // colonnes)
    W = colonnes * largeur + (colonnes + 1) * marge
    H = lignes * (cellule_h + bandeau) + (lignes + 1) * marge
    toile = Image.new("RGB", (W, H), (235, 235, 235))
    dessin = ImageDraw.Draw(toile)
    for k, (num, v) in enumerate(vignettes):
        x = marge + (k % colonnes) * (largeur + marge)
        y = marge + (k // colonnes) * (cellule_h + bandeau + marge)
        dessin.text((x, y + 2), f"slide {num}", fill=(20, 20, 20))
        toile.paste(v, (x, y + bandeau))
    os.makedirs(os.path.dirname(os.path.abspath(sortie)), exist_ok=True)
    toile.save(sortie)
    return W, H


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pngs", nargs="*")
    ap.add_argument("--sortie", required=True)
    ap.add_argument("--colonnes", type=int, default=3)
    ap.add_argument("--largeur", type=int, default=480)
    ap.add_argument("--dossier")
    ap.add_argument("--rangs")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    pngs = list(a.pngs)
    if a.dossier:
        try:
            rangs = [int(x) for x in a.rangs.split(",")] if a.rangs else None
        except ValueError:
            print("planche_contact : --rangs attend des entiers separes par des virgules", file=sys.stderr)
            return 2
        if rangs is None:
            rangs = sorted(int(m.group(1)) for f in os.listdir(a.dossier)
                           if (m := re.fullmatch(r"s(\d+)\.png", f, re.I)))
        pngs += [os.path.join(a.dossier, f"s{r:03d}.png") for r in rangs]
    if not pngs:
        print("planche_contact : aucun PNG (donner des fichiers ou --dossier)", file=sys.stderr)
        return 2
    manquants = [p for p in pngs if not os.path.isfile(p)]
    if manquants:
        print("planche_contact : PNG manquant(s) : " + ", ".join(manquants), file=sys.stderr)
        return 1
    try:
        W, H = planche(pngs, a.sortie, a.colonnes, a.largeur)
    except ValueError as e:
        print(f"planche_contact : {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"planche_contact : PNG illisible : {e}", file=sys.stderr)
        return 1
    print(f"planche_contact : {len(pngs)} vignette(s) -> {a.sortie} ({W}x{H})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
