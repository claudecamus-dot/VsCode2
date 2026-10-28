"""Reviewer-feedback intake for a deck: detect dropped feedback files, dedupe comments, flag slides to redo.

Generic mechanism only; it never edits the deliverable (it only hashes it and reads slide titles).
State lives in a configurable directory (JSON registers).

  py feedbacks_deck.py --dossier DROP --etat STATE [--livrable-courant DECK] [--json]
      [--garder-extraits] [--marquer-finie N ...]

Feedback formats: .pptx (legacy comment parts), .docx/.xlsx (comment parts), .json
({"titres": [...], "titre_seul": [ids], "commentaires": [{"slide": 1, "auteur": "x", "texte": "..."}]}).

Registers (etat/): traites.json (content SHA-256 -> summary), ouverts.json (comment hash ->
slide, role placeholder; excerpt ONLY with --garder-extraits), finies.json (frozen slides).
Rules: a slide with >= 2 distinct comments -> "refaire entierement"; title-only slide ->
"nouvelle proposition". Identity is the SHA-256 of the CONTENT, never the file name.
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import zipfile

MAX_LIGNES = 20
EXT = (".pptx", ".docx", ".xlsx", ".json")
ROLE = "relecteur"


def sha(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(65536), b""):
            h.update(bloc)
    return h.hexdigest()


def norm(texte):
    return re.sub(r"[\W_]+", " ", texte.lower()).strip()


def cle_commentaire(texte, slide=None):
    """Dedupe key: slide + normalised text (the same remark on two slides is two comments)."""
    return hashlib.sha256((str(slide) + "|" + norm(texte)).encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ pure core
def rework_index(commentaires, titre_seul=(), finies=()):
    """Pure: anonymised comments [{"hash", "slide"}] -> {"slides": {id: {...}}, "total": n}.

    Duplicate hashes count once. Frozen slides are 'finie'; title-only -> 'nouvelle
    proposition'; >= 2 distinct comments -> 'refaire entierement'.
    """
    vus, par_slide = set(), {}
    for c in commentaires:
        if c["hash"] in vus:
            continue
        vus.add(c["hash"])
        par_slide.setdefault(c["slide"], []).append(c["hash"])
    slides = {}
    for s in sorted(set(par_slide) | set(titre_seul) | set(finies), key=lambda x: (x is None, x or 0)):
        n = len(par_slide.get(s, []))
        if s in finies:
            statut = "finie"
        elif s in titre_seul:
            statut = "nouvelle proposition"
        elif n >= 2:
            statut = "refaire entierement"
        elif n == 1:
            statut = "commentee 1 fois"
        else:
            continue
        slides["hors slide" if s is None else s] = {"commentaires": n, "statut": statut}
    return {"slides": slides, "total": len(vus)}


# ------------------------------------------------------------------ readers
def _textes(xml):
    """One entry per comment; a thread (root + replies) is ONE comment, the root text is kept."""
    xml = re.sub(r"<(?:\w+:)?replyLst\b.*?</(?:\w+:)?replyLst>", "", xml, flags=re.S)
    blocs = re.findall(r"<(?:\w+:)?(?:cm|comment)\b[^>]*>.*?</(?:\w+:)?(?:cm|comment)>", xml, flags=re.S) or [xml]
    out = []
    for b in blocs:
        found = re.findall(r"<(?:\w+:)?(?:text|t)(?:\s[^>]*)?>([^<]*)<", b)
        out.append(re.sub(r"\s+", " ", " ".join(found)).strip())
    return out


def lire_fichier(chemin):
    """-> {"titres": [...], "titre_seul": [...], "commentaires": [{"slide","auteur","texte"}]}"""
    if chemin.lower().endswith(".json"):
        with open(chemin, encoding="utf-8") as f:
            d = json.load(f)
        return {"titres": d.get("titres", []), "titre_seul": d.get("titre_seul", []),
                "commentaires": d.get("commentaires", [])}
    out = {"titres": [], "titre_seul": [], "commentaires": [], "avertissements": []}
    with zipfile.ZipFile(chemin) as z:
        noms = z.namelist()
        slide_de = {}
        for n in noms:
            m = re.match(r"ppt/slides/_rels/slide(\d+)\.xml\.rels$", n)
            if m:
                rels = z.read(n).decode("utf-8", "replace")
                for cible in re.findall(r'Target="[^"]*comments/([^"]+)"', rels):
                    slide_de["ppt/comments/" + cible] = int(m.group(1))
        for n in sorted(noms):
            if re.match(r"(ppt/comments/|word/comments|xl/comments)[^/]*\.xml$", n):
                for t in _textes(z.read(n).decode("utf-8", "replace")):
                    if t:
                        out["commentaires"].append({"slide": slide_de.get(n), "auteur": ROLE, "texte": t})
    if chemin.lower().endswith(".pptx"):
        try:
            from pptx import Presentation
            for i, s in enumerate(Presentation(chemin).slides, 1):
                t = s.shapes.title.text if s.shapes.title is not None else ""
                out["titres"].append(t)
                pleins = [sh for sh in s.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
                if t and len(pleins) == 1:
                    out["titre_seul"].append(i)
        except Exception:
            out["avertissements"].append("titres illisibles (python-pptx)")
    return out


def anonymiser(commentaires, garder_extraits=False):
    """Comments -> anonymised entries; the text is dropped unless garder_extraits."""
    res = []
    for c in commentaires:
        e = {"hash": cle_commentaire(c["texte"], c.get("slide")), "slide": c.get("slide"), "role": ROLE}
        if garder_extraits:
            e["extrait"] = norm(c["texte"])[:40]
        res.append(e)
    return res


# ------------------------------------------------------------------ registers
def _lire(etat, nom, defaut):
    try:
        with open(os.path.join(etat, nom), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defaut


def _ecrire(etat, nom, data):
    os.makedirs(etat, exist_ok=True)
    with open(os.path.join(etat, nom), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)


def candidats(dossier):
    """Feedback files (lock files and other types skipped), oldest first."""
    if not os.path.isdir(dossier):
        return []
    fs = [os.path.join(dossier, n) for n in os.listdir(dossier)
          if n.lower().endswith(EXT) and not n.startswith("~$")]
    return sorted(fs, key=lambda p: (os.path.getmtime(p), p))


def controles(chemin, titres, livrable):
    avert = []
    if livrable and os.path.isfile(livrable):
        if sha(chemin) == sha(livrable):
            avert.append("fichier IDENTIQUE au dernier livrable : rien a reprendre (retouches non enregistrees ?)")
        elif titres and livrable.lower().endswith(".pptx"):
            try:
                from pptx import Presentation
                base = [s.shapes.title.text if s.shapes.title is not None else ""
                        for s in Presentation(livrable).slides]
                r = difflib.SequenceMatcher(None, base, titres).ratio()
                if r < 0.8:
                    avert.append("base ELOIGNEE (ressemblance des titres %.0f %%) : lire les ecarts avec prudence" % (r * 100))
            except Exception:
                avert.append("livrable illisible : base non comparee")
    return avert


def traiter(dossier, etat, livrable=None, garder_extraits=False):
    traites = _lire(etat, "traites.json", {})
    ouverts = _lire(etat, "ouverts.json", {})
    finies = set(_lire(etat, "finies.json", []))
    nouveaux, ignores = [], []
    for p in candidats(dossier):
        h = sha(p)
        if h in traites or any(n[1] == h for n in nouveaux):
            continue
        try:
            nouveaux.append((p, h, lire_fichier(p)))
        except Exception:
            ignores.append(os.path.basename(p))
    # lineage: an older file sharing a comment hash with a newer one is superseded
    garde, avertissements = [], ["fichier illisible ignore : %s" % n for n in ignores]
    for i, (p, h, c) in enumerate(nouveaux):
        hs = {cle_commentaire(x["texte"], x.get("slide")) for x in c["commentaires"]}
        suivants = nouveaux[i + 1:]
        if hs and any(hs <= {cle_commentaire(x["texte"], x.get("slide")) for x in s[2]["commentaires"]} for s in suivants):
            traites[h] = {"remplace": True}
            avertissements.append("%s : remplace par une version plus recente (ses commentaires y sont inclus)" % h[:8])
            continue
        garde.append((p, h, c))
    anon, titre_seul = [], set()
    for p, h, c in garde:
        a = anonymiser(c["commentaires"], garder_extraits)
        anon += a
        titre_seul |= set(c["titre_seul"])
        for w in c.get("avertissements", []) + controles(p, c["titres"], livrable):
            avertissements.append("%s : %s" % (h[:8], w))
        traites[h] = {"commentaires": len(a)}
    for e in anon:
        if e["hash"] not in ouverts:
            ouverts[e["hash"]] = {k: v for k, v in e.items() if k != "hash"}
    idx = rework_index([dict(hash=k, slide=v["slide"]) for k, v in ouverts.items()], titre_seul, finies)
    _ecrire(etat, "traites.json", traites)
    _ecrire(etat, "ouverts.json", ouverts)
    return {"nouveaux_fichiers": len(garde), "avertissements": avertissements, "index": idx}


def rendre(res):
    lignes = ["fichiers nouveaux : %d" % res["nouveaux_fichiers"]]
    lignes += ["ATTENTION " + w for w in res["avertissements"]]
    for s, v in res["index"]["slides"].items():
        lignes.append("slide %s : %s (%d)" % (s, v["statut"], v["commentaires"]))
    if len(lignes) > MAX_LIGNES:
        lignes = lignes[:MAX_LIGNES] + ["... (+%d lignes)" % (len(lignes) - MAX_LIGNES)]
    return "\n".join(lignes)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dossier", required=True)
    ap.add_argument("--etat", required=True)
    ap.add_argument("--livrable-courant")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--garder-extraits", action="store_true")
    ap.add_argument("--marquer-finie", type=int, nargs="*", default=[])
    a = ap.parse_args(argv)
    if a.marquer_finie:
        f = set(_lire(a.etat, "finies.json", [])) | set(a.marquer_finie)
        _ecrire(a.etat, "finies.json", sorted(f))
    res = traiter(a.dossier, a.etat, a.livrable_courant, a.garder_extraits)
    print(json.dumps(res, ensure_ascii=False, indent=1) if a.json else rendre(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
