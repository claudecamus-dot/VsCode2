"""Autocorrection MECANIQUE des slides denses : reduire puis scinder, sans LLM, avec rapport.

Mesure : densite_deck.mesurer (caracteres, % de texte sous `petit_pt`), seuil absolu `--max-car`
(densite_deck n'a qu'un plafond de non-regression, pas de norme : la valeur est a regler par
projet). Pour chaque slide :
  1. REDUIRE — seulement si le filet verifier_debordements_texte la signale : toutes les tailles
     explicites des zones non-placeholder baissent de 0,5 pt jusqu'a tenir, sans jamais passer
     sous `--plancher` (10,5 pt, plancher de lisibilite pptx-deck). Les tailles heritees sont
     ignorees (limite de densite_deck).
  2. SCINDER — uniquement une slide de TEXTE PUR (placeholders + zones de texte) ; une slide a
     composition (cadre, fleche, image, groupe, SmartArt, graphique) n'est JAMAIS scindee : elle est
     signalee RESTE_DENSE « slide à composition, scission manuelle ». Si la slide depasse encore `--max-car` ou deborde : la plus grosse zone de texte
     (>= 2 paragraphes) est coupee en deux ; la seconde moitie passe sur une copie inseree juste
     apres (pptx_deck_core.copier_slide), titre suffixe « (suite) », autres zones de texte retirees
     de la copie. Une seule passe : ce qui reste dense est SIGNALE, pas boucle.
Le deck source n'est jamais modifie : sortie dans un NOUVEAU fichier. Rapport JSON = ce qui a change.

CLI : autocorriger_dense.py DECK.pptx --sortie OUT.pptx [--max-car 700] [--plancher 10.5]
      [--rapport R.json] [--essai]
Sortie : 0 rien de dense ou tout corrige ; 1 slide(s) encore dense(s) apres correction ; 2 usage.
"""
import argparse
import json
import math
import os
import re
import sys

_ICI = os.path.dirname(os.path.abspath(__file__))
for _p in (_ICI,
           os.path.join(_ICI, "..", "..", "pptx-deck", "scripts"),
           os.path.join(_ICI, "..", "..", "..", "dispositif", "canon")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)

import densite_deck as DD  # noqa: E402
import pptx_deck as D  # noqa: E402
import pptx_deck_core as C  # noqa: E402

PAS = 0.5
MAX_CAR_DEFAUT = 700
PLANCHER_DEFAUT = 10.5


def _zones_texte(slide):
    return [sh for sh in slide.shapes
            if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip() and not sh.is_placeholder]


def _debordements(prs, rang):
    marque = re.compile(rf"\s*slide {rang}:")
    return [p for p in D.verifier_debordements_texte(prs) if marque.match(str(p))]


def _runs(zones):
    return [r for sh in zones for p in sh.text_frame.paragraphs for r in p.runs]


def reduire(prs, rang, plancher=PLANCHER_DEFAUT):
    """Baisse de PAS les tailles explicites tant que la slide deborde ; renvoie (de, a) en pt ou None."""
    sl = prs.slides[rang - 1]
    zones = _zones_texte(sl)
    tailles = [DD._taille_pt(r) for r in _runs(zones) if DD._taille_pt(r) is not None]
    if not tailles or not _debordements(prs, rang):
        return None
    depart = max(tailles)
    for _ in range(40):
        if not _debordements(prs, rang):
            break
        bouge = False
        for r in _runs(zones):
            t = DD._taille_pt(r)
            if t is not None and t - PAS >= plancher - 1e-9:
                from pptx.util import Pt
                r.font.size = Pt(t - PAS)
                bouge = True
        if not bouge:
            break
    fin = max(DD._taille_pt(r) for r in _runs(zones) if DD._taille_pt(r) is not None)
    return (depart, fin) if fin < depart else None


def texte_pur(slide):
    """Vrai si la slide n'a QUE du texte : placeholders et zones de texte non vides. Cadres, connecteurs,
    images, groupes, SmartArt, graphiques = composition : une copie « suite » en dupliquerait le decor vide."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    for sh in slide.shapes:
        if sh.is_placeholder:
            continue
        if sh.shape_type == MSO_SHAPE_TYPE.TEXT_BOX and sh.text_frame.text.strip():
            continue
        return False
    return True


def scinder(prs, rang):
    """Coupe la plus grosse zone en deux ; renvoie {"zone", "paragraphes", "copie"} ou None si impossible
    (une seule zone multi-paragraphes absente, ou slide a composition : jamais de scission)."""
    sl = prs.slides[rang - 1]
    if not texte_pur(sl):
        return None
    cand = [sh for sh in _zones_texte(sl) if len(sh.text_frame.paragraphs) >= 2]
    if not cand:
        return None
    cible = max(cand, key=lambda sh: len(sh.text_frame.text))
    n = len(cible.text_frame.paragraphs)
    k = math.ceil(n / 2)
    copie = C.copier_slide(prs, sl, index=rang)  # juste apres l'original
    for p in list(cible.text_frame.paragraphs)[k:]:
        p._p.getparent().remove(p._p)
    for sh in list(copie.shapes):
        if sh.shape_id == cible.shape_id:
            for p in list(sh.text_frame.paragraphs)[:k]:
                p._p.getparent().remove(p._p)
        elif getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip() and not (
                sh.is_placeholder and sh == copie.shapes.title):
            sh._element.getparent().remove(sh._element)
    titre = copie.shapes.title
    if titre is not None and titre.has_text_frame:
        runs = [r for p in titre.text_frame.paragraphs for r in p.runs]
        if runs:
            runs[-1].text += " (suite)"
    return {"zone": cible.name, "paragraphes": [k, n - k], "copie": rang + 1}


def corriger(prs, max_car=MAX_CAR_DEFAUT, plancher=PLANCHER_DEFAUT):
    """Applique reduire puis scinder ; renvoie (rapport, restes). `restes` = rangs (AVANT) encore denses."""
    mesures = DD.mesurer(prs)
    rapport, restes = [], []
    for rang in sorted(mesures):
        r = reduire(prs, rang, plancher)
        if r:
            rapport.append({"slide": rang, "action": "reduire", "de_pt": r[0], "a_pt": r[1]})
    for rang in sorted(mesures, reverse=True):  # de la fin : les rangs a traiter ne bougent pas
        car = DD.mesurer(prs)[rang]["car"]
        if car > max_car or _debordements(prs, rang):
            s = scinder(prs, rang)
            if s:
                rapport.append({"slide": rang, "action": "scinder", "car_avant": car, **s})
            else:
                restes.append(rang)
                raison = ("slide à composition, scission manuelle" if not texte_pur(prs.slides[rang - 1])
                          else "une seule zone de texte ou un seul paragraphe")
                rapport.append({"slide": rang, "action": "reste_dense", "car": car, "raison": raison})
    rapport.sort(key=lambda e: (e["slide"], e["action"]))
    return rapport, sorted(restes)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("deck")
    ap.add_argument("--sortie")
    ap.add_argument("--max-car", type=int, default=MAX_CAR_DEFAUT)
    ap.add_argument("--plancher", type=float, default=PLANCHER_DEFAUT)
    ap.add_argument("--rapport")
    ap.add_argument("--essai", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if not a.essai and not a.sortie:
        print("autocorriger_dense : --sortie requis (ou --essai)", file=sys.stderr)
        return 2
    if a.sortie and os.path.abspath(a.sortie) == os.path.abspath(a.deck):
        print("autocorriger_dense : la sortie ne peut pas etre le deck source", file=sys.stderr)
        return 2
    from pptx import Presentation
    try:
        prs = Presentation(a.deck)
    except Exception as e:  # noqa: BLE001 - fichier absent ou non OPC
        print(f"autocorriger_dense : deck illisible : {e}", file=sys.stderr)
        return 2
    rapport, restes = corriger(prs, a.max_car, a.plancher)
    sortie = {"changements": rapport, "restes_denses": restes, "essai": a.essai}
    if a.rapport:
        with open(a.rapport, "w", encoding="utf-8") as fh:
            json.dump(sortie, fh, ensure_ascii=False, indent=1)
    if not a.essai:
        prs.save(a.sortie)
    for e in rapport:
        if e["action"] != "reste_dense":
            print("AUTOCORRECTION", json.dumps(e, ensure_ascii=False))
    for e in rapport:
        if e["action"] == "reste_dense":
            print(f"RESTE_DENSE slide {e['slide']} : {e['raison']}")
    print(f"autocorriger_dense : {len(rapport)} changement(s), {len(restes)} reste(s)")
    return 1 if restes else 0


if __name__ == "__main__":
    sys.exit(main())
