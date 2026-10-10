"""SessionStart hook: stops orphaned git.exe and hook Python processes.

Criteria, revalidation and journal: see ``_faucheur_orphelins.py``. Bounded to
``DELAI_S`` by a worker thread (the settings timeout is larger, so Claude Code
never has to kill this hook -- killing it would itself leave orphans). Fail-open:
any error, a missing helper or the bound is silent with exit 0. Prints one line
only when something was stopped; point_du_jour keeps the alert for the rest.
"""
from __future__ import annotations

import os
import sys
import threading

DELAI_S = 10.0


def _travail(sortie):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _faucheur_orphelins as f
    sortie.append(f.resume(f.faucher()))


def main(travail=_travail, delai=DELAI_S):
    sortie = []

    def _cible():
        try:
            travail(sortie)
        except BaseException:  # noqa: BLE001 - fail-open, silent
            pass

    t = threading.Thread(target=_cible, daemon=True)
    t.start()
    t.join(delai)
    try:
        if not t.is_alive() and sortie and sortie[0]:
            print(sortie[0])
    except Exception:  # noqa: BLE001
        pass
    return 0


def _armer_chien_de_garde():
    """Second net (< the 20 s settings timeout) in case the bounded join itself hangs."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from _stdin_borne import armer_chien_de_garde
        armer_chien_de_garde(15.0, 0)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    _armer_chien_de_garde()
    code = main()
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    os._exit(code)  # never wait for a worker still running past the bound
