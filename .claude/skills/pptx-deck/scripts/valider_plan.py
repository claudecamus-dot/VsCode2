"""Validateur de plan (outline) JSON d'un deck : 0 token, a lancer AVANT de dessiner.

Schema : {"slides": [{"titre": "<affirmation>", "recette": "<nom>", "data": {...}}, ...]}
Controles : nombre de slides (--min/--max), champs requis (titre, recette), titre = AFFIRMATION
(longueur --titre-min/--titre-max en caracteres, >= 4 mots, pas de « : » final), titres uniques,
recette connue (--recettes a,b ; defaut : celles de rendre_contenu).

CLI : py valider_plan.py PLAN.json [--min 1] [--max 60] [--titre-min 25] [--titre-max 110]
      [--recettes a,b]
Sortie : 0 plan valide ; 1 defauts (listes sur stdout) ; 2 fichier illisible / usage / schema racine.
"""
import argparse
import json
import os
import sys

RECETTES_DEFAUT = ("title_bullets", "two_column", "kpi_row", "table")
CLES_SLIDE = ("titre", "recette", "data")


def _validateur_data():
    """valider(nom, data) de rendre_contenu (meme controle que le rendu), ou None si indisponible."""
    ici = os.path.dirname(os.path.abspath(__file__))
    for p in (ici, os.path.join(ici, "..", "..", "..", "dispositif", "canon")):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
    try:
        import pptx_recettes as R
        import rendre_contenu as RC
    except ImportError:
        return None

    def valider_data(nom, data):
        (R.valider if nom == "kpi_row" else RC.valider)(nom, data)
    return valider_data


def avertissements(plan):
    """Cles inconnues par slide (jamais bloquant)."""
    out = []
    for i, s in enumerate(plan.get("slides", []), 1):
        if isinstance(s, dict):
            inconnues = sorted(set(s) - set(CLES_SLIDE))
            if inconnues:
                out.append(f"slide {i}: cles inconnues {inconnues}")
    return out


def valider(plan, mini=1, maxi=60, titre_min=25, titre_max=110, recettes=RECETTES_DEFAUT):
    """Liste de defauts (str) ; vide = plan valide. ValueError si la racine n'est pas {"slides": [...]}."""
    if not isinstance(plan, dict) or not isinstance(plan.get("slides"), list):
        raise ValueError('racine attendue : {"slides": [...]}')
    slides = plan["slides"]
    defauts = []
    if not mini <= len(slides) <= maxi:
        defauts.append(f"nombre de slides {len(slides)} hors de {mini}..{maxi}")
    vus = {}
    valider_data = _validateur_data() if recettes is RECETTES_DEFAUT else None
    for i, s in enumerate(slides, 1):
        if not isinstance(s, dict):
            defauts.append(f"slide {i}: objet attendu")
            continue
        for champ in ("titre", "recette"):
            if not isinstance(s.get(champ), str) or not s[champ].strip():
                defauts.append(f"slide {i}: champ requis absent ou vide : {champ}")
        titre = s.get("titre")
        if isinstance(titre, str) and titre.strip():
            t = titre.strip()
            if len(t) < titre_min:
                defauts.append(f"slide {i}: titre trop court ({len(t)} < {titre_min} car.) : une affirmation, pas une etiquette")
            if len(t) > titre_max:
                defauts.append(f"slide {i}: titre trop long ({len(t)} > {titre_max} car.)")
            if len(t.split()) < 4:
                defauts.append(f"slide {i}: titre de moins de 4 mots, pas une affirmation")
            if t.endswith(":"):
                defauts.append(f"slide {i}: titre se terminant par « : »")
            if t.lower() in vus:
                defauts.append(f"slide {i}: titre deja utilise en slide {vus[t.lower()]}")
            vus.setdefault(t.lower(), i)
        rec = s.get("recette")
        if isinstance(rec, str) and rec.strip() and recettes and rec not in recettes:
            defauts.append(f"slide {i}: recette inconnue {rec!r} (connues : {', '.join(recettes)})")
        if "data" in s and not isinstance(s["data"], dict):
            defauts.append(f"slide {i}: data doit etre un objet")
        elif valider_data and isinstance(rec, str) and rec in RECETTES_DEFAUT:
            try:
                valider_data(rec, s.get("data", {}))
            except ValueError as e:
                defauts.append(f"slide {i}: data: {e}")
    return defauts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan")
    ap.add_argument("--min", type=int, default=1)
    ap.add_argument("--max", type=int, default=60)
    ap.add_argument("--titre-min", type=int, default=25)
    ap.add_argument("--titre-max", type=int, default=110)
    ap.add_argument("--recettes")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    recettes = tuple(x for x in a.recettes.split(",") if x) if a.recettes else RECETTES_DEFAUT
    try:
        with open(a.plan, encoding="utf-8") as fh:
            plan = json.load(fh)
        defauts = valider(plan, a.min, a.max, a.titre_min, a.titre_max, recettes)
    except (OSError, ValueError, RecursionError) as e:  # JSONDecodeError herite de ValueError
        print(f"valider_plan : {e}", file=sys.stderr)
        return 2
    for w in avertissements(plan):
        print("PLAN AVERTISSEMENT", w)
    for d in defauts:
        print("PLAN DEFAUT", d)
    print(f"valider_plan : {len(plan['slides'])} slide(s), {len(defauts)} defaut(s)")
    return 1 if defauts else 0


if __name__ == "__main__":
    sys.exit(main())
