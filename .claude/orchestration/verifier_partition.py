"""Pre-launch partition check for atelier-dev: every file has exactly one owner.

Usage: py .claude/orchestration/verifier_partition.py <plan.json>
Plan: {"voice": ["path", ...], ...}. Exit 0 if no path is claimed twice, 1 otherwise
(conflicts listed), 2 on an unreadable or invalid plan (empty plan, non-string or empty
entry, absolute path, ".." component).
A directory entry ("src/") conflicts with any file under it ("src/f.py").

Second mode, after the writers ran: check the REAL diff against the partition.
Usage: py .claude/orchestration/verifier_partition.py --diff-reel <plan.json>
           --owner <voice>=<worktree path> [--owner ...] [--base REF]
One --owner per voice of the plan; each path is a git worktree (or checkout) of that
writer. Changed files = committed since the merge-base with REF (default "main") plus
uncommitted tracked and untracked changes (read-only git: diff --name-only, status
--porcelain). Prints one line per violation: HORS-PERIMETRE <voice> : <file> (changed
outside its declared perimeter) and DOUBLE-ECRITURE <file> : <voices> (changed by two
owners). Exit 0 if clean, 1 on any violation, 2 on invalid plan/arguments or git error.
"""
import json
import os
import posixpath
import subprocess
import sys

NUL = chr(0)


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


def _git(wt, *args):
    r = subprocess.run(["git", "-C", wt, *args], capture_output=True)
    if r.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed in {wt}: "
                         f"{r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8", "replace")


def fichiers_modifies(wt, base="main"):
    """Set of repo-relative paths really changed in worktree wt (read-only git)."""
    out = set(x for x in _git(wt, "diff", "--name-only", "-z", f"{base}...HEAD")
              .split(NUL) if x)
    champs = _git(wt, "status", "--porcelain", "-z", "-uall").split(NUL)
    i = 0
    while i < len(champs):
        e = champs[i]
        i += 1
        if len(e) < 4:
            continue
        out.add(e[3:])
        if e[0] in "RC":  # rename/copy: the next field is the old path
            if i < len(champs) and champs[i]:
                out.add(champs[i])
            i += 1
    return out


def violations(plan, reels, insensible=None):
    """(hors, doubles): hors = [(voice, file)], doubles = {file: [voices]}."""
    perim = {v: [normaliser(f, insensible) for f in fs] for v, fs in plan.items()}
    hors, vus = [], {}
    for voix, fichiers in reels.items():
        for f in sorted(fichiers):
            n = normaliser(f, insensible)
            vus.setdefault(n, (f, set()))[1].add(voix)
            if not any(n[:len(p)] == p for p in perim.get(voix, [])):
                hors.append((voix, f))
    doubles = {f: sorted(v) for f, v in vus.values() if len(v) > 1}
    return hors, doubles


def _main_diff(argv):
    args = argv[2:]
    owners, base, plan_path, i = {}, "main", None, 0
    while i < len(args):
        a = args[i]
        if a in ("--owner", "--base"):
            if i + 1 >= len(args):
                raise ValueError(f"{a} needs a value")
            v = args[i + 1]
            i += 2
            if a == "--base":
                base = v
            else:
                voix, sep, chemin = v.partition("=")
                if not sep or not voix or not chemin:
                    raise ValueError(f"--owner expects <voice>=<path>: {v!r}")
                owners[voix] = chemin
        elif plan_path is None and not a.startswith("--"):
            plan_path, i = a, i + 1
        else:
            raise ValueError(f"unexpected argument: {a!r}")
    if plan_path is None:
        raise ValueError("plan.json missing")
    plan = _charger(plan_path)
    for voix in plan:
        for f in plan[voix]:
            normaliser(f)
    if set(owners) != set(plan):
        raise ValueError(f"--owner must cover exactly the plan voices {sorted(plan)}, "
                         f"got {sorted(owners)}")
    reels = {v: fichiers_modifies(c, base) for v, c in owners.items()}
    hors, doubles = violations(plan, reels)
    for voix, f in hors:
        print(f"HORS-PERIMETRE {voix} : {f}")
    for f, voix in sorted(doubles.items()):
        print(f"DOUBLE-ECRITURE {f} : {', '.join(voix)}")
    if not hors and not doubles:
        print("diff reel conforme a la partition")
    return 1 if hors or doubles else 0


def main(argv):
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except AttributeError:
        pass
    if len(argv) > 1 and argv[1] == "--diff-reel":
        try:
            return _main_diff(argv)
        except (OSError, ValueError) as e:
            print(f"invalid: {e}")
            return 2
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
