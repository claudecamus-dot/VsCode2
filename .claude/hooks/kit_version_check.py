"""Hook SessionStart (generique, LECTURE SEULE) - le kit agentic a-t-il une version connue ?

Cote cible, ce hook ne connait PAS le manifeste du hub (il ne lit jamais le hub par
chemin machine). Il ne peut donc verifier que :
- `.claude/kit-version.json` absent ou illisible / sans `kit_id` : une ligne ;
- `.claude/kit-version.json` present ET `.claude/kit-reference.json` (optionnel, depose
  par le proprietaire du projet : `{"kit_id": "..."}`) avec un `kit_id` different : une ligne
  de retard.
Sans fichier de reference, un fichier de version valide -> silence. Fail-open integral :
toute erreur se tait, code de sortie 0. N'ecrit jamais rien.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
VERSION = os.path.join(".claude", "kit-version.json")
REFERENCE = os.path.join(".claude", "kit-reference.json")


def _kit_id(chemin):
    """kit_id str non vide, ou None (fichier absent/illisible/invalide)."""
    try:
        with open(chemin, encoding="utf-8-sig") as fh:
            kid = json.load(fh).get("kit_id")
        return kid if isinstance(kid, str) and kid else None
    except (OSError, ValueError, AttributeError):
        return None


def message(racine: str):
    """La ligne a afficher, ou None."""
    version = os.path.join(racine, VERSION)
    if not os.path.isfile(version):
        return ("[kit-version] .claude/kit-version.json absent : version du kit agentic "
                "inconnue (poser par la propagation du hub : propager_socle.py --appliquer).")
    kid = _kit_id(version)
    if kid is None:
        return "[kit-version] .claude/kit-version.json illisible ou sans kit_id : version du kit inconnue."
    ref = _kit_id(os.path.join(racine, REFERENCE))
    if ref is not None and ref != kid:
        return ("[kit-version] kit en retard : installe %s... , reference %s... "
                "(.claude/kit-reference.json)." % (kid[:8], ref[:8]))
    return None


def main() -> int:
    try:
        ligne = message(ROOT)
        if ligne:
            print(ligne)
    except Exception:  # noqa: BLE001 - fail-open
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
