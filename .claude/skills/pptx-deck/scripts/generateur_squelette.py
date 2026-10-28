"""Squelette de generateur de deck : IMPORTE pptx_deck et pptx_deck_core, ne recopie rien.

Deux entrees :
  --depuis-zero GABARIT.pptx   deck neuf a partir d'un gabarit (layouts), sans deck precedent
  --depuis DECK.pptx           copie du deck precedent, puis delta
Options : --sortie OUT.pptx, --slides 3,12,24 (limite delta ET verification),
--remplacer ANCIEN=NOUVEAU (repetable), --retours COPIE.pptx (liste commentaires et
diff vs --depuis, sans rien ecrire), --dry-run (liste les slides touchees, n'ecrit rien).
Delta metier : passer `delta(prs, rangs)` a main() depuis le generateur du projet.
Sortie non nulle si la verification (geometrie + debordement de texte) trouve un defaut
sur les slides ciblees ; 2 = usage.
"""
import argparse
import os
import re
import sys

_ICI = os.path.dirname(os.path.abspath(__file__))
for _p in (_ICI, os.path.join(_ICI, "..", "..", "..", "dispositif", "canon")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)

import pptx_deck as D  # noqa: E402
try:
    import pptx_deck_core as C  # noqa: E402
except ModuleNotFoundError as _e:
    if _e.name != "pptx_deck_core":
        raise
    sys.exit("pptx_deck_core.py introuvable (cherche dans : " + _ICI + " puis ../../../dispositif/canon) : "
             "kit mal installe, copier pptx_deck_core.py a cote de pptx_deck.py")


def rangs_de(texte):
    """'3,12,24' -> [3, 12, 24] ; None -> None (toutes). Un element non numerique est un
    TITRE de slide, `id:256` un sldId : resolus par resoudre_slides (erreur si ambigu)."""
    if not texte:
        return None
    return [int(x) if x.strip().isdigit() else x.strip() for x in texte.split(",") if x.strip()]


def verifier(prs, rangs=None):
    """Defauts de geometrie et de debordement de texte, limites aux `rangs` (None = tous)."""
    problemes = list(D.verifier_geometrie(prs)) + list(D.verifier_debordements_texte(prs))
    if rangs is None:
        return problemes
    cibles = {str(r) for r in rangs}
    return [p for p in problemes
            if (m := re.match(r"\s*slide (\d+)", str(p))) is None or m.group(1) in cibles]


def _gardes():
    """Charge lint_charte, poids_deck et integrite_pptx (skill pptx-verify). Leur absence est
    une erreur (ValueError -> exit 2) : un deck ne sort jamais sans ses gardes."""
    d = os.path.join(_ICI, "..", "..", "pptx-verify", "scripts")
    if not os.path.isdir(d):
        raise ValueError("gardes introuvables : skill pptx-verify absente (cherche : " + d + ")")
    if d not in sys.path:
        sys.path.append(d)
    import integrite_pptx
    import lint_charte
    import poids_deck
    return lint_charte, poids_deck, integrite_pptx


GARDES_DEROGEABLES = ("poids", "charte", "sans-charte")
MOTIF_MIN = 15  # caracteres : « ok » ou « test » ne justifient rien


def lire_derogations(items):
    """['poids=motif', ...] -> {garde: motif}. ValueError si garde inconnue ou motif trop court."""
    sortie = {}
    for it in items or []:
        garde, _, motif = it.partition("=")
        garde, motif = garde.strip(), motif.strip()
        if garde not in GARDES_DEROGEABLES:
            raise ValueError(f"derogation inconnue {garde!r} ; possibles : {', '.join(GARDES_DEROGEABLES)}")
        if len(motif) < MOTIF_MIN:
            raise ValueError(f"derogation {garde} : motif de {MOTIF_MIN} caracteres minimum "
                             "(dire POURQUOI la garde ne s'applique pas)")
        sortie[garde] = motif
    return sortie


def controles_sortie(chemin, charte=None, tokens=None, derogations=None, demande=None):
    """Gardes STRICTES sur le deck ecrit : integrite OPC (jamais derogeable), poids (BLOCK =
    echec), charte (defaut = echec ; charte introuvable ou ambigue = erreur). Chaque garde ne
    se leve que par une derogation MOTIVEE (`derogations` {garde: motif}), affichee et ecrite
    dans `<deck>.derogations.json`. Renvoie la liste des echecs."""
    derog = derogations or {}
    if derog and len((demande or "").strip()) < MOTIF_MIN:
        raise ValueError("une derogation vient d'une demande EXPLICITE de l'utilisateur : joindre "
                         "--demande \"<ses mots, date>\" ; un agent ne deroge jamais de lui-meme")
    lint, poids, integ = _gardes()
    echecs, utilisees = [], {}
    echecs += [f"integrite : {x}" for x in integ.controler(chemin)["problemes"]]
    statut, msgs = poids.verdict(poids.mesurer(chemin))
    for m in msgs:
        print("POIDS " + m, file=sys.stderr if statut == "block" else sys.stdout)
    if statut == "block":
        if "poids" in derog:
            utilisees["poids"] = {"motif": derog["poids"], "constat": msgs[0]}
        else:
            echecs.append("poids : " + msgs[0] + " (--deroger poids=MOTIF pour justifier)")
    if "sans-charte" in derog:
        utilisees["sans-charte"] = {"motif": derog["sans-charte"], "constat": "charte non controlee"}
        print("CHARTE NON CONTROLEE (derogation : " + derog["sans-charte"] + ")")
    else:
        try:
            tk = lint.charger_tokens(tokens) if tokens else None
            defauts, _, _, nom = lint._controle(chemin, tk, False, None, charte)
        except (lint.CharteError, OSError, ValueError) as e:
            raise ValueError(f"charte non resolue : {e} (donner --charte NOM, --tokens FILE ou "
                             "--deroger sans-charte=MOTIF)") from e
        print(f"charte appliquee : {nom}")
        if defauts and "charte" in derog:
            utilisees["charte"] = {"motif": derog["charte"], "constat": defauts}
        else:
            echecs += [f"charte {x}" for x in defauts]
            if defauts:
                echecs.append("(--deroger charte=MOTIF pour justifier)")
    if utilisees:
        import json
        for g, d in utilisees.items():
            print(f"DEROGATION {g} : {d['motif']}")
        with open(chemin + ".derogations.json", "w", encoding="utf8") as f:
            json.dump({"demande_utilisateur": demande.strip(), "derogations": utilisees}, f,
                      ensure_ascii=False, indent=1)
    return echecs


def main(argv=None, delta=None):
    try:
        return _main(argv, delta)
    except ValueError as e:  # selecteur de slide absent ou ambigu
        print("ERREUR " + str(e), file=sys.stderr)
        return 2


def _main(argv, delta):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--depuis-zero", metavar="GABARIT")
    mode.add_argument("--depuis", metavar="DECK")
    ap.add_argument("--sortie")
    ap.add_argument("--slides")
    ap.add_argument("--remplacer", action="append", default=[], metavar="ANCIEN=NOUVEAU")
    ap.add_argument("--retours", metavar="COPIE")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--charte", metavar="NOM")
    ap.add_argument("--tokens", metavar="FICHIER")
    ap.add_argument("--demande", metavar="TEXTE",
                    help="mots de l'utilisateur qui demande la derogation (obligatoire avec --deroger)")
    ap.add_argument("--deroger", action="append", default=[], metavar="GARDE=MOTIF",
                    help="leve UNE garde (poids|charte|sans-charte) sur motif ecrit, trace dans <deck>.derogations.json")
    a = ap.parse_args(argv)
    derog = lire_derogations(a.deroger)
    from pptx import Presentation
    rangs = rangs_de(a.slides)
    source = a.depuis_zero or a.depuis
    if a.retours:
        if not a.depuis:
            ap.error("--retours exige --depuis")
        for c in C.extract_comments(a.retours):
            print(f"commentaire slide {c['slide']} ({c['auteur']}): {c['texte']}")
        for r, nat in C.diff_decks(a.depuis, a.retours, slides=rangs).items():
            print(f"slide {r} modifiee: {', '.join(nat)}")
        return 0
    prs = Presentation(source)
    if a.depuis_zero:
        D.clear_slides(prs)
    if rangs is not None:
        rangs = sorted(C.resoudre_slides(prs, rangs))
    touchees = set()
    for r in a.remplacer:
        ancien, _, nouveau = r.partition("=")
        cibles = [None] if rangs is None else rangs
        delta_r = [{"slide": c, "op": "remplacer_texte", "ancien": ancien, "nouveau": nouveau}
                   for c in cibles]
        touchees |= {x["slide"] for x in C.appliquer_delta(prs, delta_r, dry_run=a.dry_run)}
    if a.dry_run:
        print("slides touchees (dry-run): " + (",".join(map(str, sorted(touchees))) or "aucune"))
        return 0
    if delta is not None:
        delta(prs, rangs)
    problemes = verifier(prs, rangs)
    for p in problemes:
        print("DEFAUT " + str(p), file=sys.stderr)
    if a.sortie:
        prs.save(a.sortie)
        try:  # detecteur de formes complexes (dry-run, 1 ligne, ne modifie pas le deck)
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "..", "..", "deck-design-library", "scripts"))
            import detecter_formes
            print(detecter_formes.resume_ligne(a.sortie), file=sys.stderr)
        except Exception:
            pass
        echecs = controles_sortie(a.sortie, a.charte, a.tokens, derog, a.demande)
        for e in echecs:
            print("GARDE " + e, file=sys.stderr)
        problemes = list(problemes) + echecs
    return 1 if problemes else 0


if __name__ == "__main__":
    sys.exit(main())
