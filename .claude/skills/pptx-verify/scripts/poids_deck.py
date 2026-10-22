"""Mesure le poids d'un .pptx : total, repartition media/xml/polices, plus grosses parties,
images trop lourdes ou trop denses. Sortie 1 si > BLOCK_MB, avertissements si WARN.

Usage : py poids_deck.py deck.pptx [--json]
"""
import argparse
import io
import json
import posixpath
import sys
import zipfile
import xml.etree.ElementTree as ET

# --- Politique (modifiable) -------------------------------------------------
WARN_MB = 12.0
WARN_KB_PAR_SLIDE = 200.0
BLOCK_MB = 25.0
IMAGE_MAX_KB = 800.0
DPI_MAX = 192

EMU_PAR_INCH = 914400
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EXT_IMG = (".png", ".jpg", ".jpeg")


def _rels(zf, partie):
    """Retourne {rId: cible absolue} pour une partie."""
    dossier, nom = posixpath.split(partie)
    chemin = posixpath.join(dossier, "_rels", nom + ".rels")
    if chemin not in zf.namelist():
        return {}
    out = {}
    for rel in ET.fromstring(zf.read(chemin)):
        cible = rel.get("Target", "")
        if rel.get("TargetMode") == "External":
            continue
        out[rel.get("Id")] = posixpath.normpath(posixpath.join(dossier, cible)) if not cible.startswith("/") else cible.lstrip("/")
    return out


def _taille_px(octets):
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(octets)) as im:
            return im.size
    except Exception:
        return None


def usages_images(zf):
    """Pour chaque media : liste d'usages {'dpi_largeur': .., 'evaluable': bool}.

    Evaluable = <p:pic> hors groupe, sans srcRect, avec xfrm/ext. Tout autre usage
    (rogne, remplissage, groupe) est marque non evaluable.
    """
    usages = {}
    for nom in zf.namelist():
        if not (nom.startswith("ppt/") and nom.endswith(".xml")) or "/_rels/" in nom:
            continue
        rels = _rels(zf, nom)
        if not rels:
            continue
        try:
            racine = ET.fromstring(zf.read(nom))
        except ET.ParseError:
            continue
        vus_pic = set()

        def parcourir(el, en_groupe):
            if el.tag == f"{{{NS_P}}}pic":
                bf = el.find(f"{{{NS_P}}}blipFill")
                blip = bf.find(f"{{{NS_A}}}blip") if bf is not None else None
                if blip is not None:
                    rid = blip.get(f"{{{NS_R}}}embed")
                    ext = el.find(f"{{{NS_P}}}spPr/{{{NS_A}}}xfrm/{{{NS_A}}}ext")
                    src = bf.find(f"{{{NS_A}}}srcRect")
                    rogne = src is not None and any(v not in ("0", "") for v in src.attrib.values())
                    cible = rels.get(rid)
                    if cible and ext is not None and not rogne and not en_groupe:
                        cx = int(ext.get("cx", "0"))
                        cy = int(ext.get("cy", "0"))
                        if cx > 0 and cy > 0:
                            usages.setdefault(cible, []).append({"w_in": cx / EMU_PAR_INCH, "h_in": cy / EMU_PAR_INCH, "evaluable": True})
                            vus_pic.add(id(blip))
            grp = en_groupe or el.tag == f"{{{NS_P}}}grpSp"
            for enfant in el:
                parcourir(enfant, grp)

        parcourir(racine, False)
        evalues_ids = vus_pic
        for el in racine.iter():
            rid = el.get(f"{{{NS_R}}}embed")
            if rid and id(el) not in evalues_ids:
                cible = rels.get(rid)
                if cible:
                    usages.setdefault(cible, []).append({"evaluable": False})
    return usages


def dpi_image(zf, media, liste_usages):
    """(dpi_max ou None, evaluable). None si PIL absent ou usage non evaluable."""
    if not liste_usages or any(not u["evaluable"] for u in liste_usages):
        return None, False
    taille = _taille_px(zf.read(media))
    if taille is None:
        return None, False
    pw, ph = taille
    dpi = max(max(pw / u["w_in"], ph / u["h_in"]) for u in liste_usages)
    return dpi, True


def mesurer(chemin):
    """Retourne un dict de mesures ; aucun effet de bord."""
    with zipfile.ZipFile(chemin) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        octets = {i.filename: i.file_size for i in infos}
        total = sum(octets.values())
        taille_fichier = _taille_disque(chemin)
        media = {n: s for n, s in octets.items() if n.startswith("ppt/media/")}
        polices = {n: s for n, s in octets.items() if n.startswith("ppt/fonts/") or n.lower().endswith((".fntdata", ".ttf", ".otf"))}
        n_slides = sum(1 for n in octets if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        usages = usages_images(zf)
        n_dense = 0
        n_non_evalue = 0
        for m in media:
            if not m.lower().endswith(EXT_IMG):
                continue
            dpi, ok = dpi_image(zf, m, usages.get(m))
            if not ok:
                n_non_evalue += 1
            elif dpi > DPI_MAX:
                n_dense += 1
    s_media = sum(media.values())
    s_pol = sum(polices.values())
    top = sorted(octets.items(), key=lambda kv: -kv[1])[:5]
    return {
        "fichier": str(chemin),
        "total_mb": taille_fichier / 1e6,
        "decompresse_mb": total / 1e6,
        "slides": n_slides,
        "kb_par_slide": (taille_fichier / 1000 / n_slides) if n_slides else None,
        "media_octets": s_media,
        "xml_octets": total - s_media - s_pol,
        "polices_octets": s_pol,
        "top5": [{"partie": n, "octets": s} for n, s in top],
        "media_lourds": sum(1 for s in media.values() if s > IMAGE_MAX_KB * 1000),
        "images_dpi_excessif": n_dense,
        "images_non_evaluees": n_non_evalue,
        "dpi_max_policy": DPI_MAX,
    }


def _taille_disque(chemin):
    import os
    return os.path.getsize(chemin)


def verdict(m):
    """(statut, [messages]) ; statut in ok|warn|block."""
    msgs = []
    if m["total_mb"] > BLOCK_MB:
        return "block", [f"BLOCK : {m['total_mb']:.1f} MB > {BLOCK_MB} MB"]
    if m["total_mb"] > WARN_MB:
        msgs.append(f"WARN : {m['total_mb']:.1f} MB > {WARN_MB} MB")
    if m["kb_par_slide"] is not None and m["kb_par_slide"] > WARN_KB_PAR_SLIDE:
        msgs.append(f"WARN : {m['kb_par_slide']:.0f} KB/slide > {WARN_KB_PAR_SLIDE:.0f}")
    return ("warn" if msgs else "ok"), msgs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("deck")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    m = mesurer(a.deck)
    statut, msgs = verdict(m)
    m["statut"] = statut
    m["messages"] = msgs
    if a.json:
        print(json.dumps(m, indent=2, ensure_ascii=False))
    else:
        kb = m["kb_par_slide"]
        print(f"{m['fichier']}: {m['total_mb']:.2f} MB, {m['slides']} slides, "
              f"{'n/a' if kb is None else f'{kb:.0f}'} KB/slide")
        print(f"decompresse: media {m['media_octets']/1e6:.2f} MB, xml {m['xml_octets']/1e6:.2f} MB, "
              f"polices {m['polices_octets']/1e6:.2f} MB (total {m['decompresse_mb']:.2f} MB)")
        for p in m["top5"]:
            print(f"  {p['octets']/1000:10.1f} KB  {p['partie']}")
        print(f"media > {IMAGE_MAX_KB:.0f} KB : {m['media_lourds']}")
        print(f"images > {DPI_MAX} dpi : {m['images_dpi_excessif']} (non evalue : {m['images_non_evaluees']})")
        for msg in msgs:
            print(msg)
    return 1 if statut == "block" else 0


if __name__ == "__main__":
    sys.exit(main())
