"""SessionStart / UserPromptSubmit hook: stops orphaned git.exe and hook Python processes.

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


def _limite_argv(argv):
    """Value of ``--limite-min N`` (minutes), or None: SessionStart is never limited."""
    try:
        i = argv.index("--limite-min")
        v = float(argv[i + 1])
        return v if v > 0 else None
    except (ValueError, IndexError):
        return None


def _trop_tot_leger(chemin, limite_min, maintenant):
    """Same rule as ``_faucheur_orphelins.trop_tot`` without importing that module
    (collections/json/re cost more than the 50 ms budget of the "too early" path)."""
    try:
        with open(chemin, encoding="utf-8") as fh:
            t = float(fh.read().strip())
    except Exception:  # noqa: BLE001
        return False
    return t <= maintenant and (maintenant - t) < limite_min * 60


def _travail(sortie, limite_min=None):
    here = os.path.dirname(os.path.abspath(__file__))
    if limite_min:
        import time
        chemin = os.environ.get("CLAUDE_FAUCHEUR_LIMITEUR") or os.path.join(
            here, "..", "supervision", "faucheur_dernier.json")
        now = time.time()
        if _trop_tot_leger(chemin, limite_min, now):
            return  # too early: no heavy import, no process listing
    sys.path.insert(0, here)
    import _faucheur_orphelins as f
    if limite_min:
        marquer = getattr(f, "marquer", None)  # a project with the old helper still reaps
        if marquer is not None:
            marquer(chemin, now)
    sortie.append(f.resume(f.faucher()))


def main(travail=_travail, delai=DELAI_S, limite_min=None):
    sortie = []
    if limite_min and travail is _travail:
        travail = lambda s: _travail(s, limite_min)  # noqa: E731

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
    code = main(limite_min=_limite_argv(sys.argv))
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    os._exit(code)  # never wait for a worker still running past the bound
