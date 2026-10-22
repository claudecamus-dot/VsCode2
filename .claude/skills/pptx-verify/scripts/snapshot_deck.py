"""Instantané d'un deck .pptx indexé par identifiant de slide, et comparaison.

Pourquoi : une correction sur un sous-ensemble de slides ne doit pas toucher les
autres. L'identifiant `id` de `p:sldId` est stable à l'insertion/suppression,
contrairement au rang. Lecture seule ; le rapport n'affiche aucun contenu de slide
(numéros et natures d'écart seulement ; le JSON porte des empreintes et le titre).

  py snapshot_deck.py deck.pptx --out avant.json
  py snapshot_deck.py --comparer avant.json deck.pptx --perimetre 3,12 --figees 5
Périmètre/figées : identifiants, rangs ou titres (préfixes `id:` / `rang:` pour
lever l'ambiguïté ; un nombre nu est un rang s'il en existe un, sinon un id).
Exit 1 si une slide hors périmètre ou figée a changé.
"""
import argparse
import hashlib
import json
import os
import posixpath
import sys
import xml.etree.ElementTree as ET
import zipfile

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
ARRONDI = 9144  # 0,01 pouce en EMU
NATURES = (("texte", "texte"), ("formes", "forme"), ("position", "position"))


def _h(obj):
    return hashlib.sha1(json.dumps(obj, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def _rels(z, part):
    d, n = posixpath.split(part)
    path = posixpath.join(d, "_rels", n + ".rels")
    if path not in z.namelist():
        return {}
    return {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read(path))}


def _resolve(base, target):
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), target))


def _slide(xml):
    root = ET.fromstring(xml)
    tree = root.find(".//p:spTree", NS)
    formes, positions, textes, titre = [], [], [], ""
    if tree is None:
        return "", _h([]), _h([]), _h([]), 0
    for el in tree.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("sp", "pic", "graphicFrame", "grpSp", "cxnSp"):
            continue
        c = el.find(".//p:cNvPr", NS)
        nom = c.get("name", "") if c is not None else ""
        off = el.find(".//a:off", NS)
        ext = el.find(".//a:ext", NS)
        pos = [round(int(off.get(k, 0)) / ARRONDI) for k in ("x", "y")] if off is not None else None
        tai = [round(int(ext.get(k, 0)) / ARRONDI) for k in ("cx", "cy")] if ext is not None else None
        formes.append([nom, tag])
        positions.append([pos, tai])
        txt = "" if tag == "grpSp" else "".join(t.text or "" for t in el.iter("{{{}}}t".format(NS["a"])))
        textes.append(txt)
        ph = el.find(".//p:ph", NS)
        if not titre and txt and ph is not None and ph.get("type") in ("title", "ctrTitle"):
            titre = txt
    if not titre:
        titre = next((t for t in textes if t), "")
    return titre.strip()[:120], _h(textes), _h(formes), _h(positions), len(formes)


def instantane(pptx):
    """Retourne {id: {rang, titre, texte, formes, position, nb_formes}} (ordre du deck)."""
    out = {}
    with zipfile.ZipFile(pptx) as z:
        pres = ET.fromstring(z.read("ppt/presentation.xml"))
        rels = _rels(z, "ppt/presentation.xml")
        for rang, s in enumerate(pres.findall(".//p:sldIdLst/p:sldId", NS), 1):
            part = _resolve("ppt/presentation.xml", rels[s.get(R_ID)])
            titre, t, f, p, n = _slide(z.read(part))
            out[s.get("id")] = {"rang": rang, "titre": titre, "texte": t,
                                "formes": f, "position": p, "nb_formes": n}
    return out


def _resoudre(jetons, avant, apres):
    """Jetons (id, rang, titre) -> (ensemble d'identifiants, jetons inconnus)."""
    ids, inconnus = set(), []
    snaps = (avant, apres)
    for j in jetons or []:
        j = str(j).strip()
        if not j:
            continue
        mode, _, val = j.partition(":")
        if mode in ("id", "rang") and val:
            j = val
        else:
            mode = ""
        trouve = set()
        if j.isdigit():
            par_rang = {i for s in snaps for i, x in s.items() if x["rang"] == int(j)}
            par_id = {i for s in snaps for i in s if i == j}
            if mode == "rang" or (mode == "" and par_rang):
                trouve = par_rang
            else:
                trouve = par_id
        else:
            trouve = {i for s in snaps for i, x in s.items() if x["titre"] == j}
        if not trouve:
            inconnus.append(j)
        ids |= trouve
    return ids, inconnus


def comparer(avant, apres, perimetre=None, figees=None):
    """Rapport : modifiees (+natures), ajoutees, supprimees, violations, ok."""
    modif, nature = [], {}
    for i, a in avant.items():
        b = apres.get(i)
        if b is None:
            continue
        n = [lab for k, lab in NATURES if a[k] != b[k]]
        if n:
            modif.append(i)
            nature[i] = n
    ajoutees = [i for i in apres if i not in avant]
    supprimees = [i for i in avant if i not in apres]
    reordre = [i for i in avant if i in apres] != [i for i in apres if i in avant]
    rap = {"modifiees": modif, "natures": nature, "ajoutees": ajoutees,
           "supprimees": supprimees, "reordonnee": reordre, "violations": [],
           "inconnus": []}
    fig, inc1 = _resoudre(figees, avant, apres)
    rap["inconnus"] += inc1
    touchees = modif + ajoutees + supprimees
    motifs = {}
    for i in touchees:
        kind = "modifiee" if i in modif else "ajoutee" if i in ajoutees else "supprimee"
        if i in fig:
            motifs[i] = "figee, " + kind
    if perimetre:
        per, inc2 = _resoudre(perimetre, avant, apres)
        rap["inconnus"] += inc2
        for i in touchees:
            if i not in per and i not in motifs:
                kind = "modifiee" if i in modif else "ajoutee" if i in ajoutees else "supprimee"
                motifs[i] = "hors perimetre, " + kind
    rap["violations"] = [{"id": i, "motif": m} for i, m in motifs.items()]
    rap["ok"] = not rap["violations"]
    return rap


def avertissements(pptx, reference=None):
    w = []
    d, n = os.path.split(os.path.abspath(pptx))
    if os.path.exists(os.path.join(d, "~$" + n)):
        w.append(f"fichier verrou ~${n} present : deck ouvert, modifications peut-etre non enregistrees")
    if reference:
        a, b = instantane(reference), instantane(pptx)
        ta, tb = {s["titre"] for s in a.values()}, {s["titre"] for s in b.values()}
        commun = len(ta & tb) / max(1, len(ta | tb))
        if abs(len(a) - len(b)) > 0.2 * max(len(a), len(b)) or commun < 0.5:
            w.append(f"deck tres different de la reference : {len(b)} slides contre {len(a)}, "
                      f"{commun * 100:.0f} % de titres communs (base non derivee du dernier livrable ?)")
    return w


def formater(rap, avant, apres):
    lignes = []
    for i in rap["modifiees"]:
        lignes.append(f"modifiee : id {i} (rang {avant[i]['rang']} -> {apres[i]['rang']}) [{'+'.join(rap['natures'][i])}]")
    for i in rap["ajoutees"]:
        lignes.append(f"ajoutee : id {i} (rang {apres[i]['rang']})")
    for i in rap["supprimees"]:
        lignes.append(f"supprimee : id {i} (rang {avant[i]['rang']})")
    if rap["reordonnee"]:
        lignes.append("ordre relatif des slides communes modifie")
    for j in rap["inconnus"]:
        lignes.append(f"perimetre/figee inconnu : {j}")
    for v in rap["violations"]:
        lignes.append("ECHEC : id {} {}".format(v["id"], v["motif"]))
    lignes.append(f"RESULTAT : {'OK' if rap['ok'] else 'ECHEC'} ({len(rap['modifiees'])} modifiee(s), "
                   f"{len(rap['ajoutees'])} ajoutee(s), {len(rap['supprimees'])} supprimee(s))")
    return "\n".join(lignes)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("deck")
    ap.add_argument("--out")
    ap.add_argument("--comparer", metavar="AVANT_JSON")
    ap.add_argument("--perimetre")
    ap.add_argument("--figees")
    ap.add_argument("--reference")
    a = ap.parse_args(argv)
    for w in avertissements(a.deck, a.reference):
        print("AVERTISSEMENT : " + w, file=sys.stderr)
    snap = instantane(a.deck)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        print(f"instantane : {len(snap)} slides -> {a.out}")
    if a.comparer:
        with open(a.comparer, encoding="utf-8") as f:
            avant = json.load(f)

        def liste(s):
            return s.split(",") if s else None
        rap = comparer(avant, snap, liste(a.perimetre), liste(a.figees))
        print(formater(rap, avant, snap))
        return 0 if rap["ok"] else 1
    if not a.out:
        print("rien a faire : --out ou --comparer", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
