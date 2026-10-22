"""Derive charte/<nom>/tokens.json d'un deck de reference (autorise_* seulement, tolere_* vides).
Usage : py deriver_tokens_charte.py REF.pptx --charte acn [--racine .] [--force]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lint_charte  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("reference")
    ap.add_argument("--charte", required=True, help="acn | octo | client:<nom>")
    ap.add_argument("--racine", default=".", help="dossier du projet contenant charte/")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    sortie = os.path.join(a.racine, "charte", a.charte.replace(":", "_"), "tokens.json")
    if os.path.exists(sortie) and not a.force:
        print(f"refus : {sortie} existe deja (--force pour ecraser)", file=sys.stderr)
        return 1
    t = lint_charte.deriver_tokens(a.reference, a.charte)
    os.makedirs(os.path.dirname(os.path.abspath(sortie)), exist_ok=True)
    with open(sortie, "w", encoding="utf8") as f:
        json.dump(t, f, indent=1, ensure_ascii=False)
    print(f"{sortie} : {len(t['autorise_hex'])} hex, {len(t['autorise_polices'])} polices")
    return 0


if __name__ == "__main__":
    sys.exit(main())
