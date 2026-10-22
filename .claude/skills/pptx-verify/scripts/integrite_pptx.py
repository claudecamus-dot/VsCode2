"""Contrôle d'intégrité d'un .pptx (zip OPC), en LECTURE SEULE.

python-pptx est tolérant : un fichier qu'il rouvre peut être refusé par PowerPoint.
Vérifie : cibles de relations existantes, parties orphelines, `[Content_Types].xml`
(couvre toutes les parties, ne cite aucune partie absente), noms de parties
dupliqués, XML bien formé. Exit 1 si problème.

  py integrite_pptx.py deck.pptx
"""
import posixpath
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter

CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def controler(pptx):
    """Retourne {"comptes": {...}, "problemes": [str]}."""
    pb = []
    with zipfile.ZipFile(pptx) as z:
        noms = [i.filename for i in z.infolist()]
        for n, c in Counter(noms).items():
            if c > 1:
                pb.append(f"partname duplique : {n} (x{c})")
        parties = {n for n in noms if not n.endswith("/")}
        casse = Counter(n.lower() for n in parties)
        for n in sorted(parties):
            if casse[n.lower()] > 1:
                pb.append(f"partname duplique (casse) : {n}")
            if n.endswith((".xml", ".rels")):
                try:
                    ET.fromstring(z.read(n))
                except ET.ParseError as e:
                    pb.append(f"XML mal forme : {n} ({e})")
        defaults, overrides = set(), set()
        if "[Content_Types].xml" not in parties:
            pb.append("[Content_Types].xml absent")
        else:
            try:
                root = ET.fromstring(z.read("[Content_Types].xml"))
                defaults = {d.get("Extension", "").lower() for d in root.findall(CT + "Default")}
                overrides = {o.get("PartName", "").lstrip("/") for o in root.findall(CT + "Override")}
            except ET.ParseError:
                pass
        for o in sorted(overrides):
            if o not in parties:
                pb.append(f"Content_Types cite une partie absente : {o}")
        for n in sorted(parties):
            if n == "[Content_Types].xml":
                continue
            ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
            if n not in overrides and ext not in defaults:
                pb.append(f"partie non couverte par Content_Types : {n}")
        atteintes, nrel = set(), 0
        for r in sorted(n for n in parties if n.endswith(".rels")):
            base_dir = posixpath.dirname(posixpath.dirname(r))
            try:
                rels = ET.fromstring(z.read(r))
            except ET.ParseError:
                continue
            for rel in rels.findall(PR + "Relationship"):
                nrel += 1
                if rel.get("TargetMode") == "External":
                    continue
                t = rel.get("Target", "")
                cible = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(base_dir, t))
                if cible not in parties:
                    pb.append("relation vers une partie absente : {} -> {} ({})".format(
                        r, cible, rel.get("Type", "").rsplit("/", 1)[-1]))
                else:
                    atteintes.add(cible)
        for n in sorted(parties):
            if n == "[Content_Types].xml" or n.endswith(".rels") or n in atteintes:
                continue
            pb.append(f"partie orpheline : {n}")
    comptes = {"parties": len(parties), "relations": nrel,
               "slides": sum(1 for n in parties if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
               "problemes": len(pb)}
    return {"comptes": comptes, "problemes": pb}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage : integrite_pptx.py deck.pptx", file=sys.stderr)
        return 2
    try:
        r = controler(argv[0])
    except zipfile.BadZipFile:
        print("PROBLEME : pas un zip valide")
        return 1
    c = r["comptes"]
    print(f"parties={c['parties']} relations={c['relations']} slides={c['slides']} problemes={c['problemes']}")
    for p in r["problemes"]:
        print("PROBLEME : " + p)
    return 1 if r["problemes"] else 0


if __name__ == "__main__":
    sys.exit(main())
