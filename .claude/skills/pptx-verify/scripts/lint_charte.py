"""Lint de charte generique : srgbClr et polices (a:latin/ea/cs) des slides, mises en page et masques
contre les tokens d'une charte nommee (`charte/<nom>/tokens.json`) ou d'un deck de REFERENCE (`--reference`).

Chartes : acn | octo | client:<nom>. Un tokens.json porte "charte", "reference", "version".
Legacy : un `charte/tokens.json` plat (Vscode8) est lu comme la charte `acn`.
Selection : `--charte <nom>` ; sans option, la charte unique du dossier charte/ du projet, sinon exit 2.
Autorise : couleurs du theme du deck, `autorise_hex`, `autorise_polices`, references de theme (+mn-, +mj-).
Tolere (avertissement) : `tolere_*` ; avec `--strict` ce sont des defauts. Autre valeur : defaut.
Exit 1 si defaut, 0 sinon, 2 si charte introuvable / ambigue / illisible. La charte appliquee est toujours imprimee.
Usage : py lint_charte.py deck.pptx [--charte NOM | --tokens FILE | --reference REF.pptx] [--strict]
"""
import argparse
import collections
import json
import os
import re
import sys
import zipfile

PARTIES = re.compile(r"ppt/(slides|slideLayouts|slideMasters)/[^/]+\.xml")
THEME = re.compile(r"ppt/theme/theme\d+\.xml")
_RE_THEME = re.compile(r'<a:(?:dk1|lt1|dk2|lt2|accent\d|hlink|folHlink)>\s*<a:(?:srgbClr val|sysClr val="[^"]*" lastClr)="([0-9A-Fa-f]{6})"')
_RE_HEX = re.compile(r'srgbClr val="([0-9A-Fa-f]{6})"')
_RE_FONT = re.compile(r'<a:(?:latin|ea|cs) typeface="([^"]*)"')
_RE_THEME_FONT = re.compile(r'<a:(?:major|minor)Font>\s*<a:latin typeface="([^"]*)"')
DEFAUT_TOKENS = {"autorise_hex": ["FFFFFF", "000000"], "tolere_hex": [], "autorise_polices": [],
                 "tolere_polices": [], "police_theme_prefixes": ["+mn-", "+mj-"], "taille_mini_pt": 8}


class CharteError(Exception):
    """Charte introuvable, inconnue ou ambigue (exit 2)."""


def _dossier_charte(chemin):
    d = os.path.dirname(os.path.abspath(chemin))
    while True:
        c = os.path.join(d, "charte")
        if os.path.isdir(c):
            return c
        p = os.path.dirname(d)
        if p == d:
            return None
        d = p


def trouver_charte(chemin, nom=None):
    """(nom, chemin tokens.json) ; leve CharteError. Le tokens.json plat est la charte `acn` (legacy)."""
    base = _dossier_charte(chemin)
    if base is None:
        raise CharteError("dossier charte/ introuvable en remontant depuis le deck (utiliser --tokens ou --reference)")
    dispo = {}
    for e in sorted(os.listdir(base)):
        f = os.path.join(base, e, "tokens.json")
        if os.path.isfile(f):
            dispo[e] = f
    plat = os.path.join(base, "tokens.json")
    if os.path.isfile(plat) and "acn" not in dispo:
        dispo["acn"] = plat
    if nom:
        if nom not in dispo:
            raise CharteError(f"charte '{nom}' inconnue ; disponibles : {', '.join(sorted(dispo)) or 'aucune'}")
        return nom, dispo[nom]
    if len(dispo) == 1:
        return next(iter(dispo.items()))
    raise CharteError(f"{len(dispo)} chartes disponibles ({', '.join(sorted(dispo)) or 'aucune'}) : preciser --charte")


def charger_tokens(chemin):
    with open(chemin, encoding="utf8") as f:
        return json.load(f)


def balayer(chemin):
    """(hex Counter, polices Counter, couleurs theme, polices theme, lieux {valeur: set(n slide)}, parties par hex)."""
    hexa, polices, theme, pol_theme = collections.Counter(), collections.Counter(), set(), set()
    lieux, parties_hex = collections.defaultdict(set), collections.defaultdict(set)
    with zipfile.ZipFile(chemin) as z:
        for n in z.namelist():
            if THEME.fullmatch(n):
                d = z.read(n).decode("utf8")
                theme |= {v.upper() for v in _RE_THEME.findall(d)}
                pol_theme |= set(_RE_THEME_FONT.findall(d))
            elif PARTIES.fullmatch(n):
                d = z.read(n).decode("utf8")
                m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", n)
                num = int(m.group(1)) if m else None
                for v in _RE_HEX.findall(d):
                    v = v.upper()
                    hexa[v] += 1
                    parties_hex[v].add(n)
                    if num:
                        lieux[v].add(num)
                for p in _RE_FONT.findall(d):
                    polices[p] += 1
                    if num:
                        lieux[p].add(num)
    return hexa, polices, theme, pol_theme, lieux, parties_hex


def mesurer(chemin):
    """(hex Counter, polices Counter, couleurs du theme)."""
    return balayer(chemin)[:3]


def deriver_tokens(chemin_ref, charte="acn"):
    """Tokens depuis un deck de reference : theme + srgbClr sur >= 2 parties + polices utilisees. tolere_* vides."""
    hexa, polices, theme, pol_theme, _, parties = balayer(chemin_ref)
    return {
        "charte": charte,
        "reference": os.path.basename(chemin_ref),
        "version": "derive-1",
        "_note": "Derive du deck de reference ; a arbitrer.",
        "autorise_hex": sorted(theme | {h for h, p in parties.items() if len(p) >= 2}),
        "tolere_hex": [],
        "autorise_polices": sorted({p for p in polices if p and not p.startswith(("+mn-", "+mj-"))} | pol_theme),
        "tolere_polices": [],
        "police_theme_prefixes": ["+mn-", "+mj-"],
        "taille_mini_pt": 8,
    }


def _controle(chemin, tokens=None, strict=False, reference=None, charte=None):
    if reference:
        t, nom = deriver_tokens(reference), f"reference:{os.path.basename(reference)}"
    elif tokens is not None:
        t, nom = tokens, tokens.get("charte", "tokens-fournis")
    else:
        nom, f = trouver_charte(chemin, charte)
        t = charger_tokens(f)
        nom = t.get("charte", nom)
    t = {**DEFAUT_TOKENS, **t}
    hexa, polices, theme, _, lieux, _p = balayer(chemin)
    ok_hex = theme | {h.upper() for h in t["autorise_hex"]}
    tol_hex = {h.upper() for h in t["tolere_hex"]}
    defauts, avert = [], []
    for h, n in sorted(hexa.items()):
        if h in ok_hex:
            continue
        if h in tol_hex:
            (defauts if strict else avert).append(f"couleur {h} x{n}" + (" : tolere, refuse en --strict" if strict else ""))
        else:
            defauts.append(f"couleur {h} x{n} : hors charte")
    for p, n in sorted(polices.items()):
        if p in t["autorise_polices"] or p.startswith(tuple(t["police_theme_prefixes"])) or p == "":
            continue
        if p in t["tolere_polices"]:
            (defauts if strict else avert).append(f"police '{p}' x{n}" + (" : tolere, refuse en --strict" if strict else ""))
        else:
            defauts.append(f"police '{p}' x{n} : hors charte")
    return defauts, avert, lieux, nom


def controler(chemin, tokens=None, strict=False, reference=None, charte=None):
    """(defauts, avertissements) : listes de str. Sans effet de bord. Leve CharteError si charte introuvable."""
    return _controle(chemin, tokens, strict, reference, charte)[:2]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("deck")
    ap.add_argument("--charte")
    ap.add_argument("--tokens")
    ap.add_argument("--reference")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)
    try:
        tk = charger_tokens(a.tokens) if a.tokens else None
        d, w, _, nom = _controle(a.deck, tk, a.strict, a.reference, a.charte)
    except (CharteError, json.JSONDecodeError, OSError) as e:
        print(f"lint_charte : erreur charte/tokens/reference : {e}", file=sys.stderr)
        return 2
    print(f"charte appliquee : {nom}")
    for x in d:
        print("CHARTE DEFAUT", x, file=sys.stderr)
    for x in w:
        print("charte tolere [A VALIDER]", x)
    print(f"lint_charte : {len(d)} defaut(s), {len(w)} valeur(s) tolerees")
    return 1 if d else 0


if __name__ == "__main__":
    sys.exit(main())
