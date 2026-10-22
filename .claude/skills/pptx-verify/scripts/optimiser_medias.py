"""Reduit le poids des medias PNG/JPEG d'un .pptx, sur une COPIE (jamais en place).

Usage : py optimiser_medias.py in.pptx out.pptx [--dpi 192] [--dry-run]

Sous-echantillonne les images dont la densite (taille affichee) depasse --dpi, ignore les
images rognees (srcRect), en remplissage ou en groupe, garde le PNG si alpha, ne remplace un
media que si le resultat est plus petit. Les autres parties sont recopiees a l'identique.
"""
import argparse
import importlib.util
import io
import os
import sys
import zipfile

try:
    from PIL import Image
except ImportError:  # message clair, pas de trace
    Image = None

_ICI = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("poids_deck_mod", os.path.join(_ICI, "poids_deck.py"))
_pd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pd)


def _a_alpha(im):
    return im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)


def _reencoder(octets, nom, dpi_cible, dpi_actuel):
    """Retourne les nouveaux octets (ou les originaux si pas plus petit)."""
    ext = nom.lower().rsplit(".", 1)[-1]
    with Image.open(io.BytesIO(octets)) as im:
        im.load()
        if dpi_actuel is not None and dpi_actuel > dpi_cible:
            f = dpi_cible / dpi_actuel
            im = im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))), Image.LANCZOS)
        buf = io.BytesIO()
        if ext == "png":
            if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                im = im.convert("RGBA" if _a_alpha(im) else "RGB")
            im.save(buf, format="PNG", optimize=True)
        else:
            if _a_alpha(im) or im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(buf, format="JPEG", quality=85, optimize=True)
    nouveau = buf.getvalue()
    return nouveau if len(nouveau) < len(octets) else octets


def optimiser(chemin_in, chemin_out, dpi=192, dry_run=False):
    """Retourne la liste [(media, avant, apres)]. Ecrit chemin_out sauf dry_run."""
    if Image is None:
        raise RuntimeError("Pillow requis : py -m pip install pillow")
    src, dst = os.path.abspath(chemin_in), os.path.abspath(chemin_out)
    if os.path.normcase(src) == os.path.normcase(dst) or (os.path.exists(dst) and os.path.samefile(src, dst)):
        raise ValueError("refus : la sortie doit differer de l'entree (travail sur copie)")
    resultats = []
    with zipfile.ZipFile(src) as zin:
        usages = _pd.usages_images(zin)
        remplaces = {}
        for info in zin.infolist():
            n = info.filename
            if not n.startswith("ppt/media/") or not n.lower().endswith(_pd.EXT_IMG):
                continue
            orig = zin.read(n)
            dpi_actuel, ok = _pd.dpi_image(zin, n, usages.get(n))
            if not ok:
                if usages.get(n) is not None:
                    resultats.append((n, len(orig), len(orig)))  # non evalue : intact
                    continue
                dpi_actuel = None  # non reference : simple recompression seulement
            try:
                nouveau = _reencoder(orig, n, dpi, dpi_actuel)
            except Exception:
                nouveau = orig
            remplaces[n] = nouveau
            resultats.append((n, len(orig), len(nouveau)))
        if not dry_run:
            tmp = dst + ".tmp"
            with zipfile.ZipFile(tmp, "w") as zout:
                for info in zin.infolist():
                    data = remplaces.get(info.filename)
                    if data is None:
                        data = zin.read(info.filename)
                    zi = zipfile.ZipInfo(info.filename, info.date_time)
                    zi.compress_type = info.compress_type
                    zi.external_attr = info.external_attr
                    zout.writestr(zi, data)
            os.replace(tmp, dst)
    return resultats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("entree")
    ap.add_argument("sortie")
    ap.add_argument("--dpi", type=int, default=192)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        res = optimiser(a.entree, a.sortie, a.dpi, a.dry_run)
    except (RuntimeError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    for n, avant, apres in res:
        print(f"{n}: {avant} -> {apres} octets")
    av, ap_ = sum(r[1] for r in res), sum(r[2] for r in res)
    print(f"total medias: {av} -> {ap_} octets ({av - ap_} gagnes){' [dry-run]' if a.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
