"""Coalition planner (story 14.4, FR67): turn steps + written files into a task graph.

Usage: py .claude/orchestration/coalition_planner.py <etapes.json> [--plafond N]
Input: a list of {"id": str, "ecrit": [paths], "depend": [ids]}.
Guarantees (pure function, writes nothing, launches nothing):
  - one writer per file scope: steps whose scopes overlap (same path, or one is a directory
    prefix of the other) share ONE writer lane and are never in the same wave;
  - dependent steps are never in the same wave (waves are topological layers); a cycle is refused;
  - arms = min(widest wave, cap); a reduction below the requested arms is traced in `reduction`;
  - the result is a PROPOSAL: `requires_user_approval` is always true, `lance` is always false.
Cap: gravite.plafond_courant() (4 standard, 10 augmented; read only), a smaller --plafond may lower it, never raise it.
Exit codes: 0 plan printed; 2 refused (cycle, invalid input).
"""
import importlib.util
import json
import os
import posixpath
import sys
import unicodedata

ICI = os.path.dirname(os.path.abspath(__file__))
MAX_ETAPES = 200
MAX_CHEMINS = 100


class PlanRefuse(ValueError):
    pass


def _plafond_gravite() -> int:
    spec = importlib.util.spec_from_file_location("gravite_planner", os.path.join(ICI, "gravite.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    v = mod.plafond_courant() if hasattr(mod, "plafond_courant") else mod.PLAFOND_BRAS
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise PlanRefuse("gravite PLAFOND_BRAS must be an int >= 1")
    return v


MAX_CHEMIN_LEN = 260
RESERVES = {"con", "prn", "aux", "nul", "conin$", "conout$"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
RESERVES |= {f"{d}{s}" for d in ("com", "lpt") for s in "¹²³"}
INTERDITS = set('*?<>"|:~')


def _norm(p):
    if type(p) is not str:
        raise PlanRefuse("path must be a string")
    if len(p) > MAX_CHEMIN_LEN:
        raise PlanRefuse(f"path longer than {MAX_CHEMIN_LEN} characters refused")
    if any(ord(c) < 32 or ord(c) == 127 for c in p):
        raise PlanRefuse(f"control character in path refused: {p!r}")
    if any(c in INTERDITS for c in p):
        raise PlanRefuse(f"forbidden character (* ? < > \" | : ~) in path refused: {p!r}")
    q = unicodedata.normalize("NFC", p.replace("\\", "/"))
    if q.startswith("/"):
        raise PlanRefuse(f"absolute path refused: {p!r}")
    n = posixpath.normpath(q)
    if n in ("", ".") or n == ".." or n.startswith("../"):
        raise PlanRefuse(f"empty or escaping path refused: {p!r}")
    segs = []
    for seg in n.split("/"):
        seg = seg.rstrip(". ")
        if not seg:
            raise PlanRefuse(f"empty segment after trimming refused: {p!r}")
        if seg.split(".")[0].rstrip(" ").casefold() in RESERVES:
            raise PlanRefuse(f"reserved device name refused: {p!r}")
        segs.append(seg)
    return "/".join(segs).casefold()


def _valider(etapes):
    if type(etapes) is not list or not etapes:
        raise PlanRefuse("etapes must be a non-empty list")
    if len(etapes) > MAX_ETAPES:
        raise PlanRefuse(f"too many steps (max {MAX_ETAPES})")
    ids = set()
    for e in etapes:
        if type(e) is not dict or type(e.get("id")) is not str or not e["id"] or e["id"] in ids:
            raise PlanRefuse("each step needs a unique non-empty string id")
        ids.add(e["id"])
        for champ in ("ecrit", "depend"):
            v = e.get(champ, [])
            if type(v) is not list or not all(type(x) is str and x for x in v):
                raise PlanRefuse(f"{champ} must be a list of non-empty strings")
            if len(v) > MAX_CHEMINS:
                raise PlanRefuse(f"too many entries in {champ} (max {MAX_CHEMINS})")
    for e in etapes:
        for d in e.get("depend", []):
            if d not in ids:
                raise PlanRefuse(f"unknown dependency {d!r}")


def planifier(etapes, plafond=None, bras_demandes=None) -> dict:
    _valider(etapes)
    cap = _plafond_gravite()
    if plafond is not None:
        if type(plafond) is not int or plafond < 1:
            raise PlanRefuse("plafond must be an int >= 1")
        cap = min(cap, plafond)
    idx = {e["id"]: i for i, e in enumerate(etapes)}
    scopes = [[_norm(p) for p in e.get("ecrit", [])] for e in etapes]
    parent = list(range(len(etapes)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # overlap = same path or one is a directory prefix of the other; owner index, O(n*m*depth)
    owner = {}
    for i, sc in enumerate(scopes):
        for p in sc:
            parent[find(i)] = find(owner.setdefault(p, i))
    for i, sc in enumerate(scopes):
        for p in sc:
            segs = p.split("/")
            for k in range(1, len(segs)):
                o = owner.get("/".join(segs[:k]))
                if o is not None:
                    parent[find(i)] = find(o)
    # Kahn topological order, ties by declaration order; leftover = cycle
    restant = {e["id"]: set(e.get("depend", [])) for e in etapes}
    ordre = []
    while restant:
        pret = sorted((i for i in restant if not restant[i]), key=idx.get)
        if not pret:
            raise PlanRefuse("dependency cycle among: " + ", ".join(sorted(restant, key=idx.get)))
        n = pret[0]
        ordre.append(n)
        del restant[n]
        for r in restant.values():
            r.discard(n)
    vague, derniere_lane = {}, {}
    for n in ordre:
        e = etapes[idx[n]]
        w = 1 + max([vague[d] for d in e.get("depend", [])], default=0)
        lane = find(idx[n])
        w = max(w, derniere_lane.get(lane, 0) + 1)
        vague[n] = derniere_lane[lane] = w
    largeur = {}
    for n in ordre:
        largeur.setdefault(vague[n], []).append(n)
    demandes = max(len(v) for v in largeur.values())
    if bras_demandes is not None:
        if type(bras_demandes) is not int or bras_demandes < 1:
            raise PlanRefuse("bras_demandes must be an int >= 1")
        demandes = bras_demandes
    bras = min(demandes, cap)
    vagues = []
    for w in sorted(largeur):
        membres = largeur[w]
        for k in range(0, len(membres), bras):
            vagues.append(membres[k:k + bras])
    lanes = {}
    for i, e in enumerate(etapes):
        lanes.setdefault(find(i), []).append(e["id"])
    redacteurs = [{"redacteur": f"R{k + 1}", "etapes": v,
                   "perimetre": sorted({p for i in range(len(etapes)) if find(i) == r for p in scopes[i]})}
                  for k, (r, v) in enumerate(sorted(lanes.items()))]
    reduction = None
    if demandes > bras:
        reduction = {"demandes": demandes, "accordes": bras, "plafond": cap, "motif": "plafond_bras"}
    elif max(len(v) for v in largeur.values()) > bras:
        reduction = {"demandes": max(len(v) for v in largeur.values()), "accordes": bras, "plafond": cap,
                     "motif": "bras_demandes_inferieur_a_la_vague"}
    return {"statut": "proposition", "requires_user_approval": True, "lance": False,
            "plafond": cap, "bras": bras, "reduction": reduction,
            "vagues": vagues, "redacteurs": redacteurs}


def main(argv) -> int:
    if len(argv) < 2:
        print("usage: coalition_planner.py <etapes.json> [--plafond N]", file=sys.stderr)
        return 2
    try:
        plafond = int(argv[argv.index("--plafond") + 1]) if "--plafond" in argv else None
        with open(argv[1], encoding="utf-8") as f:
            plan = planifier(json.load(f), plafond=plafond)
    except (PlanRefuse, OSError, ValueError, IndexError, RecursionError) as exc:
        print(f"refus: {exc}", file=sys.stderr)
        return 2
    plan["requires_user_approval"] = True
    print(json.dumps(plan, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
