"""Demande de modification -> delta verifie. Liste d'operations declaratives (JSON) appliquee
via pptx_deck_core.run_delta (garde hors-delta + integrite OPC), jamais sur la source : la
sortie est un NOUVEAU fichier vN+1, accompagne d'un journal JSON-lines rejouable.

Operations (cle `cible` : `id:<sldId>` stable ou titre exact, unique sinon ValueError) :
  remplacer_texte {cible, ancien, nouveau}   remplacer_slide {cible, depuis}
  inserer_apres {cible, depuis}              supprimer {cible}     deplacer {cible, position}
`depuis` = slide MODELE du meme deck (copiee avec toutes ses relations, SmartArt compris).

Registre des slides (--registre FICHIER.json) : {"<sldId>": {"etat": "finie"} |
{"etat": "commentee", "n": 3}}. Une slide `finie` ne se touche pas sans --forcer-finie ; `finie` protège le CONTENU, pas la
position (une insertion ou un déplacement ailleurs peut la décaler).

CLI : demande_vers_delta.py DECK.pptx OPS.json [--essai] [--registre R.json] [--forcer-finie]
      demande_vers_delta.py --annuler SORTIE.pptx      (affiche la version precedente)
      demande_vers_delta.py --marquer R.json id:256=finie | id:256=commentee:3
"""
import argparse
import json
import os
import re
import sys
import tempfile

_ICI = os.path.dirname(os.path.abspath(__file__))
for _p in (_ICI, os.path.join(_ICI, "..", "..", "..", "dispositif", "canon")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)
import pptx_deck_core as C  # noqa: E402

OPS = ("remplacer_texte", "remplacer_slide", "inserer_apres", "supprimer", "deplacer")
BORNE = 20


def charger_registre(chemin):
    if not chemin or not os.path.exists(chemin):
        return {}
    with open(chemin, encoding="utf8") as f:
        return {str(k).replace("id:", ""): v for k, v in json.load(f).items()}


def marquer(chemin, sld_id, etat, n=None):
    """Inscrit l'etat d'une slide (par sldId) dans le registre."""
    if etat not in ("finie", "commentee"):
        raise ValueError(f"etat inconnu : {etat!r} (finie | commentee)")
    reg = charger_registre(chemin)
    reg[str(sld_id).replace("id:", "")] = {"etat": etat} if n is None else {"etat": etat, "n": int(n)}
    with open(chemin, "w", encoding="utf8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)
    return reg


def _un(prs, sel):
    (rang,) = C.resoudre_slides(prs, [sel])
    return rang, prs.slides[rang - 1]


def _verifier_forme(op):
    if op.get("op") not in OPS:
        raise ValueError(f"op inconnue : {op.get('op')!r} (attendu : {', '.join(OPS)})")
    if op.get("cible") is None:
        raise ValueError(f"{op['op']} : cle `cible` exigee")
    manque = {"remplacer_texte": ("ancien", "nouveau"), "remplacer_slide": ("depuis",),
              "inserer_apres": ("depuis",), "deplacer": ("position",)}.get(op["op"], ())
    for k in manque:
        if k not in op:
            raise ValueError(f"{op['op']} : cle `{k}` exigee")


def _touchees(prs, op):
    """Slides MODIFIEES par l'op (celles que `finie` protege) : [(rang, slide)]."""
    if op["op"] == "inserer_apres":
        return []
    return [_un(prs, op["cible"])]


def _controle_finie(prs, op, registre, forcer):
    for rang, sl in _touchees(prs, op):
        if registre.get(str(sl.slide_id), {}).get("etat") == "finie" and not forcer:
            raise ValueError(f"slide {rang} (id:{sl.slide_id}) marquee `finie` : "
                             "refus, --forcer-finie pour passer outre")


def _delta(prs, op):
    """Op declarative -> liste d'ops du delta de pptx_deck_core (selecteurs `id:` stables)."""
    rang, cible = _un(prs, op["cible"])
    ref = C.id_ref(cible, prs)
    o = op["op"]
    if o == "remplacer_texte":
        return [{"slide": ref, "op": "remplacer_texte", "ancien": op["ancien"], "nouveau": op["nouveau"]}]
    if o == "supprimer":
        return [{"slide": ref, "op": "supprimer"}]
    if o == "deplacer":
        return [{"slide": ref, "op": "deplacer", "position": op["position"]}]
    _, modele = _un(prs, op["depuis"])
    mref = C.id_ref(modele, prs)
    if o == "inserer_apres":
        return [{"slide": mref, "op": "copier", "index": rang}]
    return [{"slide": mref, "op": "copier", "index": rang - 1}, {"slide": ref, "op": "supprimer"}]


def _version_suivante(src):
    base, ext = os.path.splitext(src)
    m = re.match(r"^(.*)_v(\d+)$", base)
    racine, n = (m.group(1), int(m.group(2)) + 1) if m else (base, 2)
    while os.path.exists(f"{racine}_v{n}{ext}"):
        n += 1
    return f"{racine}_v{n}{ext}"


def _titre(sl):
    return next((x.text_frame.text for x in sl.shapes if x.has_text_frame and x.text_frame.text), "")


def _executer(src, ops, registre, forcer_finie, tmp):
    """Chaine les ops (une run_delta chacune, memes gardes) dans `tmp`. Renvoie
    (chemin_final, journal, lignes)."""
    for op in ops:
        _verifier_forme(op)
    courant, journal, lignes = src, [], []
    for i, op in enumerate(ops):
        prs = C._ouvrir(courant)
        _controle_finie(prs, op, registre, forcer_finie)
        delta = _delta(prs, op)
        _, sl = _un(prs, op["cible"])
        fige = dict(op, cible=f"id:{sl.slide_id}")
        if "depuis" in op:
            fige["depuis"] = f"id:{_un(prs, op['depuis'])[1].slide_id}"
        lignes.append(f"{op['op']} id:{sl.slide_id} {_titre(sl)[:40]!r}")
        suivant = os.path.join(tmp, f"etape{i}.pptx")
        C.run_delta(courant, suivant, delta)
        journal.append(dict(fige, _source=os.path.abspath(src)))
        courant = suivant
    if registre and not forcer_finie:
        avant, apres = C.empreintes_slides(C._ouvrir(src)), C.empreintes_slides(C._ouvrir(courant))
        for sid, e in registre.items():
            if e.get("etat") == "finie" and int(sid) in avant and apres.get(int(sid)) != avant[int(sid)]:
                raise ValueError(f"slide finie id:{sid} modifiee : dst non ecrit")
    return courant, journal, lignes


def essai(src, ops, registre=None, forcer_finie=False):
    """Dry-run : applique la chaine COMPLETE (memes gardes, finie, integrite) dans un dossier
    temporaire supprime ensuite ; n'ecrit rien a cote de `src`. Renvoie
    {slides: [lignes bornees a 20], total, tronque}."""
    with tempfile.TemporaryDirectory() as tmp:
        _, _, lignes = _executer(src, ops, registre or {}, forcer_finie, tmp)
    return {"slides": lignes[:BORNE], "total": len(lignes), "tronque": len(lignes) > BORNE}


def appliquer_demande(src, ops, dst=None, registre=None, forcer_finie=False):
    """Applique `ops` sur `src` ; ecrit `dst` (defaut : vN+1, jamais src ni un fichier existant)
    + `dst`.journal.jsonl (jamais ecrase). Chaque op passe par run_delta (garde hors-delta)."""
    dst = dst or _version_suivante(src)
    if os.path.exists(dst):
        raise ValueError(f"{dst} existe deja : une version n'est jamais ecrasee")
    with tempfile.TemporaryDirectory() as tmp:
        courant, journal, _ = _executer(src, ops, registre or {}, forcer_finie, tmp)
        octets = open(courant, "rb").read()
    jr = dst + ".journal.jsonl"
    try:
        fj = open(jr, "x", encoding="utf8")
    except FileExistsError:
        raise ValueError(f"{jr} existe deja (journal d'une autre version) : refus") from None
    try:
        with open(dst, "xb") as fd:
            fd.write(octets)
    except FileExistsError:
        fj.close()
        os.remove(jr)
        raise ValueError(f"{dst} existe deja : une version n'est jamais ecrasee") from None
    with fj:
        for lg in journal:
            fj.write(json.dumps(dict(lg, _sortie=os.path.abspath(dst)), ensure_ascii=False) + "\n")
    return dst


def rejouer(journal, src, dst=None, registre=None, forcer_finie=False):
    """Rejoue un journal (ops figees en `id:`) sur `src` -> nouvelle version."""
    with open(journal, encoding="utf8") as f:
        lignes = [json.loads(lg) for lg in f if lg.strip()]
    ops = [{k: v for k, v in lg.items() if not k.startswith("_")}
           for lg in lignes if "annule" not in lg]
    return appliquer_demande(src, ops, dst, registre, forcer_finie)


def annuler(sortie):
    """Annulation = rouvrir la version precedente : la sortie n'est pas supprimee, l'annulation
    est tracee au journal ; renvoie le chemin de la version precedente."""
    jr = sortie + ".journal.jsonl"
    with open(jr, encoding="utf8") as f:
        lignes = [json.loads(lg) for lg in f if lg.strip()]
    prec = next((lg["_source"] for lg in lignes if "_source" in lg), None)
    if prec is None:
        raise ValueError(f"{jr} sans ligne `_source` : version precedente inconnue")
    with open(jr, "a", encoding="utf8") as f:
        f.write(json.dumps({"annule": True, "retour_a": prec}) + "\n")
    return prec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("deck", nargs="?")
    ap.add_argument("ops", nargs="?")
    ap.add_argument("--essai", action="store_true")
    ap.add_argument("--registre")
    ap.add_argument("--forcer-finie", action="store_true")
    ap.add_argument("--annuler")
    ap.add_argument("--marquer", nargs=2, metavar=("REGISTRE", "ID=ETAT[:N]"))
    a = ap.parse_args(argv)
    try:
        if a.annuler:
            print("version precedente :", annuler(a.annuler))
            return 0
        if a.marquer:
            ident, etat = a.marquer[1].split("=")
            nom, _, n = etat.partition(":")
            marquer(a.marquer[0], ident.replace("id:", ""), nom, n or None)
            return 0
        if not a.ops or not a.deck:
            raise ValueError("usage : DECK.pptx OPS.json (ou --annuler / --marquer)")
        with open(a.ops, encoding="utf8") as f:
            ops = json.load(f)
        reg = charger_registre(a.registre)
        if a.essai:
            r = essai(a.deck, ops, reg, a.forcer_finie)
            print("\n".join(r["slides"]))
            print(f"{r['total']} slide(s) touchee(s)" + (" (liste tronquee a 20)" if r["tronque"] else ""))
            return 0
        print(appliquer_demande(a.deck, ops, None, reg, a.forcer_finie))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as e:  # json.JSONDecodeError est une ValueError
        print("ERREUR :", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
