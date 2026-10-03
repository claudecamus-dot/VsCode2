"""Pre-launch partition check for atelier-dev: every file has exactly one owner.

Usage: py .claude/orchestration/verifier_partition.py <plan.json>
Plan: {"voice": ["path", ...], ...}. Exit 0 if no path is claimed twice, 1 otherwise
(conflicts listed), 2 on an unreadable or invalid plan (empty plan, non-string or empty
entry, absolute path, ".." component).
A directory entry ("src/") conflicts with any file under it ("src/f.py").
"""
import json
import os
import posixpath
import sys


def normaliser(chemin, insensible=None):
    """Component tuple of a plan path; ValueError if absolute, escaping or empty."""
    if not isinstance(chemin, str) or not chemin.strip():
        raise ValueError(f"invalid entry: {chemin!r}")
    brut = chemin.strip().replace("\\", "/")
    if brut.startswith("/") or (len(brut) > 1 and brut[1] == ":"):
        raise ValueError(f"absolute path refused: {chemin!r}")
    if ".." in brut.split("/"):
        raise ValueError(f"'..' component refused: {chemin!r}")
    if brut.startswith("./"):
        brut = brut[2:]
    norme = posixpath.normpath(brut)
    if norme in (".", ""):
        raise ValueError(f"empty path: {chemin!r}")
    if insensible is None:
        insensible = os.name == "nt"
    if insensible:
        norme = norme.lower()
    return tuple(norme.split("/"))


def conflits(plan, insensible=None):
    """[(path_a, path_b, owners)] where one path equals or contains the other."""
    entrees = []
    for voix, fichiers in plan.items():
        for f in fichiers:
            entrees.append((normaliser(f, insensible), voix))
    trouves = {}
    for i, (a, va) in enumerate(entrees):
        for b, vb in entrees[i + 1:]:
            n = min(len(a), len(b))
            if a[:n] == b[:n] and va != vb:
                cle = "/".join(a if len(a) <= len(b) else b)
                trouves.setdefault(cle, set()).update((va, vb))
    return {c: sorted(v) for c, v in trouves.items()}


def doublons(plan, insensible=None):
    return conflits(plan, insensible)


def _charger(chemin):
    with open(chemin, encoding="utf-8-sig") as fh:
        plan = json.load(fh)
    if not isinstance(plan, dict) or not plan:
        raise ValueError("plan must be a non-empty object {voice: [files]}")
    for voix, fichiers in plan.items():
        if not isinstance(fichiers, list):
            raise ValueError(f"voice {voix!r}: list of files expected")
    return plan


def main(argv):
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except AttributeError:
        pass
    try:
        plan = _charger(argv[1])
        d = conflits(plan)
    except (IndexError, OSError, ValueError) as e:
        print(f"invalid plan: {e}" if not isinstance(e, IndexError)
              else "usage: verifier_partition.py <plan.json> ({voice: [files]})")
        return 2
    for f, voix in d.items():
        print(f"DOUBLON {f} : {', '.join(voix)}")
    if not d:
        print("partition exclusive")
    return 1 if d else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
