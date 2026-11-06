"""Neural Gate, SHADOW mode only (story 8.24, FR63).

Usage: py .claude/orchestration/neural_gate.py --shadow <demande.json> [--trace PATH] [--no-trace]

Reads a request description and returns what the gate WOULD choose, without changing anything:
  D        deterministic script alone (`deterministe: true`)
  S        one agent (sequential task, or a single step)
  C        coalition of 1..PLAFOND_BRAS arms (independent steps), arms = min(etapes, cap)
  ABSTAIN  insufficient evidence (reason NO_EVIDENCE) or invalid input (INVALID_INPUT)

Request fields: run_id, tache_id (optional, traced), etapes (int >= 0), independantes (bool),
cout_historique (finite number >= 0), qualite_historique (finite number in [0, 1]),
deterministe (bool, optional). Missing/invalid cost or quality -> ABSTAIN NO_EVIDENCE, never a
default coalition. A sequential task (independantes false) never yields C.

Shadow guarantees: `requires_user_approval` is ALWAYS true (set after every other field, no
input can override it); the gate never activates anything and writes nothing but ONE appended
line in its trace (default .claude/orchestration/gravite/trace_porte.jsonl, overridable by
--trace or the NEURAL_GATE_TRACE environment variable). gravite.py is read only (arm cap).
Exit codes: 0 decision printed (trace write failure: warning on stderr, still 0);
2 unreadable request file; 3 trace path refused (must be trace_porte*.jsonl under gravite/ or a tmp dir).
"""
import argparse
import datetime
import importlib.util
import json
import math
import os
import sys
import tempfile

ICI = os.path.dirname(os.path.abspath(__file__))
TRACE_DEFAUT = os.path.join(ICI, "gravite", "trace_porte.jsonl")
POLICY = "neural-gate-shadow-v0.1"


def _plafond() -> int:
    spec = importlib.util.spec_from_file_location("gravite_porte", os.path.join(ICI, "gravite.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return int(mod.PLAFOND_BRAS)


def _plafond_courant() -> int:
    """Cap of the CURRENT mode (4 standard, 10 augmented), read at each decision, never frozen at import."""
    spec = importlib.util.spec_from_file_location("gravite_porte", os.path.join(ICI, "gravite.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return int(mod.plafond_courant())


PLAFOND_BRAS = _plafond()  # standard cap (reference for policy validation)


def _nombre(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        return float(x) if math.isfinite(x) else None
    except OverflowError:  # huge int: not a usable number
        return None


def _sortie(decision, raison, valeurs, bras=None):
    out = {"decision": decision, "bras": bras, "raison": raison, "valeurs": valeurs,
           "policy_version": POLICY, "mode": "shadow"}
    out["requires_user_approval"] = True  # AC8.24.4: last write wins, never false
    return out


def decider(demande) -> dict:
    """Shadow decision for a request dict. Pure: no I/O."""
    if not isinstance(demande, dict):
        return _sortie("ABSTAIN", "INVALID_INPUT", {})
    etapes, indep = demande.get("etapes"), demande.get("independantes")
    cap = _plafond_courant()
    cout = _nombre(demande.get("cout_historique"))
    qual = _nombre(demande.get("qualite_historique"))
    valeurs = {"etapes": etapes if isinstance(etapes, int) and not isinstance(etapes, bool)
               else None, "independantes": indep if isinstance(indep, bool) else None,
               "cout_historique": cout, "qualite_historique": qual,
               "deterministe": demande.get("deterministe") is True, "plafond_bras": cap}
    if cout is None or cout < 0 or qual is None or not 0.0 <= qual <= 1.0:
        return _sortie("ABSTAIN", "NO_EVIDENCE", valeurs)
    if valeurs["etapes"] is None or etapes < 0 or valeurs["independantes"] is None:
        return _sortie("ABSTAIN", "INVALID_INPUT", valeurs)
    if valeurs["deterministe"]:
        return _sortie("D", "DETERMINISTIC_SCRIPT", valeurs)
    if not indep or etapes <= 1:
        return _sortie("S", "SEQUENTIAL" if not indep else "SINGLE_STEP", valeurs)
    return _sortie("C", "INDEPENDENT_STEPS", valeurs, bras=min(etapes, cap))


def chemin_trace(arg=None) -> str:
    return arg or os.environ.get("NEURAL_GATE_TRACE") or TRACE_DEFAUT


def _racines_permises() -> list:
    return [os.path.join(ICI, "gravite"), tempfile.gettempdir(),
            os.path.abspath(os.sep + "tmp")]


def trace_permise(chemin) -> bool:
    """AC8.24.4 confinement: a *.jsonl file under gravite/ or under a tmp directory."""
    try:
        reel = os.path.normcase(os.path.realpath(chemin))
        nom = os.path.basename(reel)
        if ":" in os.path.basename(chemin) or not (nom.startswith("trace_porte") and nom.endswith(".jsonl")):
            return False
        def sous(racine):
            r = os.path.normcase(os.path.realpath(racine))
            return os.path.commonpath([reel, r]) == r and reel != r
        racines = _racines_permises()
        if sous(racines[0]):
            return True
        if sous(os.path.dirname(os.path.dirname(ICI))):  # inside the repo: gravite/ only
            return False
        return any(sous(r) for r in racines[1:])
    except (OSError, ValueError, TypeError):
        return False
    return False


def tracer(demande, sortie, chemin) -> None:
    """Append ONE timestamped line (append only)."""
    ligne = dict(sortie)
    if isinstance(demande, dict):
        ligne["run_id"], ligne["tache_id"] = demande.get("run_id"), demande.get("tache_id")
    ligne["ts"] = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(os.path.abspath(chemin)), exist_ok=True)
    with open(chemin, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(ligne, ensure_ascii=True, sort_keys=True) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shadow", required=True, help="request JSON file")
    ap.add_argument("--trace", default=None)
    ap.add_argument("--no-trace", action="store_true")
    a = ap.parse_args(argv)
    try:
        with open(a.shadow, encoding="utf-8") as fh:
            demande = json.load(fh)
    except (OSError, ValueError, RecursionError) as exc:
        print(f"neural_gate : demande illisible ({str(exc)[:100]}).", file=sys.stderr)
        return 2
    sortie = decider(demande)
    if not a.no_trace:
        chemin = chemin_trace(a.trace)
        if not trace_permise(chemin):
            print("neural_gate : trace refusee (un trace_porte*.jsonl sous gravite/ ou un dossier tmp).",
                  file=sys.stderr)
            return 3
        try:
            tracer(demande, sortie, chemin)
        except (OSError, ValueError) as exc:  # shadow: a trace failure never blocks the orchestration
            print(f"neural_gate : avertissement, trace non ecrite ({str(exc)[:100]}).",
                  file=sys.stderr)
    print(json.dumps(sortie, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
