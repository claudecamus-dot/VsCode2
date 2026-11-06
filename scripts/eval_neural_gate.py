"""Offline evaluation of a bounded learning update of the routing weights (story 14.6, FR69).

Usage: py scripts/eval_neural_gate.py --trace trace_porte.jsonl [--journal runs.jsonl]
           [--projet-canari ID] [--sortie shadow_report*.json]

It reads ALREADY JOURNALED data only (tail of the gate trace and of the run journal) and never
launches, alternates or randomises a real run (AC14.6.5). Chain: filter learning candidates
(AC14.6.1, AC14.6.6) -> bounded update proposal -> offline evaluation -> canary on a project that
is NOT a source of the proposal. Promotion, arbitration and rollback live in
validate_learning_release.py. The update formula of study GMAH 3.5 is NOT used: the rule here is
a bounded nudge of at most PAS_MAX per weight (a choice to arbitrate).

The offline evaluation is an ADMISSIBILITY check (valid policy, bounded step, enough data, quality
not collapsing), not a counterfactual claim: every result carries OMBRE.
Hostile input: exact type checks, finite numbers only, bounded sizes, public functions never raise.
Exit codes: 0 report printed; 2 unreadable or refused input; 3 report path refused.
"""
import argparse
import importlib.util
import json
import math
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
HUB = os.path.dirname(ICI)
ORCH = os.path.join(HUB, ".claude", "orchestration")


def _charger(nom, chemin):
    spec = importlib.util.spec_from_file_location(nom, chemin)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ns = _charger("eval_neural_gate_shadow", os.path.join(ORCH, "neural_shadow.py"))
npol = _charger("eval_neural_gate_policy", os.path.join(ORCH, "neural_policy.py"))
optimiseur = ns.optimiseur

OMBRE = ns.OMBRE
N_MIN = ns.N_MIN               # same floor as the shadow comparator (20 decisions)
PAS_MAX = 0.05                 # max absolute move of one weight per release (choice, to arbitrate)
MARGE = 0.05                   # quality non-regression margin, fixed BEFORE the trial (choice)
DERIVE_MAX = 0.15              # max cumulative |weight - reference| vs the reference version v1 (choice)
PLANCHER = 0.05                # floor of each weight: no criterion can be extinguished (choice)
MAX_CANDIDATS = 100_000
MOTIFS = ("preuve_qualite_manquante", "cout_partiel", "provenance_douteuse", "version_agent_inconnue",
          "probabilite_ou_version_politique_absente")
VERSIONS_INCONNUES = ("", "inconnue", "unknown", "?", "none", "null")
REFUS_EXECUTION = ("executer_reel", "alterner", "randomiser")


def _ident(v, maxi=128):
    try:
        if type(v) is not str:
            return None
        v.encode("utf-8")
        v = v.strip()
        return v if 0 < len(v) <= maxi and all(c.isprintable() for c in v) else None
    except Exception:  # noqa: BLE001
        return None


def _norm_proj(p):
    p = _ident(p)
    return p.casefold() if p is not None else None


def _projet(run):
    return _norm_proj(run.get("project_id", "hub"))


def juger_candidat(run, trace):
    """(True, None) or (False, motif) - one motif per discard case (AC14.6.1, AC14.6.6). Never raises."""
    try:
        if type(run) is not dict or type(trace) is not dict:
            return False, "provenance_douteuse"
        # quality proof: a failure is its own evidence; a success needs the 8.20 acceptance proof
        res = run.get("resultat")
        if not (res in ("echec", "partiel") or (res == "succes" and optimiseur.est_succes(run))):
            return False, "preuve_qualite_manquante"
        if run.get("cout_couverture") != "complet" or ns._entier(run.get("tokens")) is None:
            return False, "cout_partiel"
        rid = _ident(run.get("run_id"))
        if (rid is None or trace.get("run_id") != rid or _projet(run) is None
                or type(run.get("ts")) is not str or not run["ts"]):
            return False, "provenance_douteuse"
        v = _ident(run.get("agent_version"), 64)
        if v is None or v.casefold() in VERSIONS_INCONNUES:
            return False, "version_agent_inconnue"
        p = trace.get("chosen_action_prob")
        try:
            ok_p = (type(p) in (int, float) and math.isfinite(p) and 0 < p <= 1)
        except OverflowError:     # huge int: not a usable probability
            ok_p = False
        if not ok_p or _ident(trace.get("policy_version"), 64) is None:
            return False, "probabilite_ou_version_politique_absente"
        return True, None
    except Exception:  # noqa: BLE001
        return False, "provenance_douteuse"


def preparer(runs, lignes_porte):
    """Join journaled runs and gate-trace lines by run_id (last line wins) and filter them.
    Returns {'retenus': [(run, trace)], 'ecartes': {motif: n}, 'n_vus'}."""
    out = {"retenus": [], "ecartes": dict.fromkeys(MOTIFS, 0), "n_vus": 0}
    try:
        par_run = {}
        for d in lignes_porte if type(lignes_porte) is list else []:
            if type(d) is dict and type(d.get("run_id")) is str:
                par_run[d["run_id"]] = d
        for r in (runs if type(runs) is list else [])[:MAX_CANDIDATS]:
            if type(r) is not dict or not optimiseur.est_resolu(r):
                continue
            out["n_vus"] += 1
            t = par_run.get(r.get("run_id")) if type(r.get("run_id")) is str else None
            ok, motif = juger_candidat(r, t if t is not None else {})
            if ok:
                out["retenus"].append((r, t))
            else:
                out["ecartes"][motif] += 1
    except Exception:  # noqa: BLE001
        out["erreur"] = True
    return out


def _mediane(v):
    s = sorted(v)
    return s[len(s) // 2] if s else None


def signaux(retenus):
    """Observed (never counterfactual) pain signals in [0, 1]: failure rate, share of runs above
    the median cost, share above the median latency."""
    runs = [r for r, _ in retenus]
    n = len(runs)
    if n == 0:
        return None
    echec = sum(1 for r in runs if not optimiseur.est_succes(r)) / n
    couts = [ns._entier(r.get("tokens")) for r in runs]
    lats = [ns._reel(r.get("duree_s")) for r in runs]
    mc, ml = _mediane([c for c in couts if c is not None]), _mediane([x for x in lats if x is not None])
    hc = sum(1 for c in couts if c is not None and mc is not None and c > mc) / n
    hl = sum(1 for x in lats if x is not None and ml is not None and x > ml) / n
    return {"qualite": echec, "cout": hc, "delai": hl}


def taux_succes(retenus):
    n = len(retenus)
    return sum(1 for r, _ in retenus if optimiseur.est_succes(r)) / n if n else None


def proposer(policy, retenus):
    """Bounded update proposal: each weight moves toward its pain share by at most PAS_MAX.
    Only `routage.poids` is touched (securite is never learnable). Returns a dict, never raises."""
    try:
        if len(retenus) < N_MIN:
            return {"ok": False, "motif": ns.INSUFFISANT, "n": len(retenus)}
        sig = signaux(retenus)
        poids = policy["routage"]["poids"]
        total = sum(sig.values())
        if total <= 0:
            return {"ok": False, "motif": "aucun_signal", "n": len(retenus)}
        # sum-zero step: weights keep summing to 1 and each moves by at most PAS_MAX
        d = {k: sig[k] / total - v for k, v in poids.items()}
        moy = sum(d.values()) / len(d)
        d = {k: x - moy for k, x in d.items()}
        m = max(abs(x) for x in d.values())
        if m > PAS_MAX:
            d = {k: x * PAS_MAX / m for k, x in d.items()}
        nouveau = {k: round(v + d[k], 6) for k, v in poids.items()}
        bornes = bornes_cumulees(policy, {"routage": {"poids": nouveau, "seuils": policy["routage"]["seuils"]}},
                                 policy)
        if bornes:
            return {"ok": False, "motif": ",".join(bornes), "n": len(retenus)}
        res = npol.propose_update(policy, {"routage": {"poids": nouveau}})
        if not res["accepted"]:
            return {"ok": False, "motif": res["reason"], "n": len(retenus)}
        return {"ok": True, "proposition": {"routage": {"poids": nouveau}}, "politique": res["policy"],
                "n": len(retenus), "signaux": sig, "mention": OMBRE}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "motif": f"erreur:{type(exc).__name__}", "n": 0}


def _tous_finis(*dicts):
    return all(type(v) in (int, float) and math.isfinite(v) for d in dicts for v in d.values())


def bornes_cumulees(reference, nouvelle, courante=None):
    """Failing checks of a candidate policy vs the REFERENCE version (v1): weights sum to 1, each weight
    >= PLANCHER, cumulative drift <= DERIVE_MAX. `courante` is unused (kept for call symmetry)."""
    echecs = []
    try:
        pn, pr = nouvelle["routage"]["poids"], reference["routage"]["poids"]
        sn, sr = nouvelle["routage"]["seuils"], reference["routage"]["seuils"]
        if set(pn) != set(pr) or set(sn) != set(sr) or not _tous_finis(pn, sn):
            return ["cles_ou_valeurs_invalides"]
        if abs(sum(pn.values()) - 1.0) > 1e-6:
            echecs.append("poids_non_normalises")
        if any(v < PLANCHER - 1e-9 for v in pn.values()):
            echecs.append("plancher_poids")
        if any(abs(pn[k] - pr[k]) > DERIVE_MAX + 1e-9 for k in pr) or any(
                abs(sn[k] - sr[k]) > DERIVE_MAX + 1e-9 for k in sr):
            echecs.append("derive_cumulee")
    except Exception:  # noqa: BLE001
        echecs.append("bornes_illisibles")
    return echecs


def amplitude_ok(avant, apres):
    """True iff every learnable number moved by at most PAS_MAX (+ float slack) and securite is identical."""
    try:
        if avant["securite"] != apres["securite"] or avant["version"] != apres["version"]:
            return False
        for sec in ("poids", "seuils"):
            if set(avant["routage"][sec]) != set(apres["routage"][sec]) \
                    or not _tous_finis(avant["routage"][sec], apres["routage"][sec]):
                return False
            for k, v in avant["routage"][sec].items():
                if abs(apres["routage"][sec][k] - v) > PAS_MAX + 1e-9:
                    return False
        return True
    except Exception:  # noqa: BLE001
        return False


def refuser_execution_reelle(plan):
    """AC14.6.5: learning never launches/alternates/randomises a real run. A truthy flag is a refusal."""
    try:
        if type(plan) is not dict:
            return {"ok": False, "echecs": ["plan_illisible"]}
        bad = [k for k in REFUS_EXECUTION if plan.get(k)]
        if bad:
            return {"ok": False, "echecs": bad,
                    "motif": "arbitrage ecrit de l'utilisateur exige, politique par politique, hors story 14.6"}
        return {"ok": True, "echecs": []}
    except Exception:  # noqa: BLE001
        return {"ok": False, "echecs": ["plan_illisible"]}


def evaluer_hors_ligne(politique_actuelle, resultat_proposition, retenus, plan=None):
    """Offline evaluation on journaled data only. Fails (names every failing check) on: execution
    requested, proposal refused, no data, step too large, quality collapsing. Never raises."""
    echecs = []
    try:
        if not refuser_execution_reelle(plan or {})["ok"]:
            echecs.append("execution_reelle_demandee")
        if type(resultat_proposition) is not dict or resultat_proposition.get("ok") is not True:
            echecs.append("proposition_refusee")
        elif not amplitude_ok(politique_actuelle, resultat_proposition["politique"]):
            echecs.append("amplitude_hors_borne")
        if len(retenus) < N_MIN:
            echecs.append("donnees_insuffisantes")
        else:
            t = taux_succes(retenus)
            if t is None or t < 0.5 - MARGE:
                echecs.append("qualite_observee_effondree")
    except Exception:  # noqa: BLE001
        echecs.append("erreur_evaluation")
    return {"ok": not echecs, "echecs": echecs, "mention": OMBRE, "n": len(retenus) if type(retenus) is list else 0}


def separer_canari(retenus, projet_canari):
    """(sources, canari): the canary project's rows never feed the proposal."""
    pc = _norm_proj(projet_canari)
    if pc is None:
        return list(retenus), []
    src = [x for x in retenus if _projet(x[0]) != pc]
    return src, [x for x in retenus if _projet(x[0]) == pc]


def evaluer_canari(sources, canari, projet_canari):
    """Canary on a NON-source project, from journaled rows only: its observed success rate must not fall
    below the sources' rate by more than MARGE. Names every failing check. Never raises."""
    echecs = []
    try:
        pc = _norm_proj(projet_canari)
        if pc is None:
            echecs.append("projet_canari_invalide")
        elif any(_projet(r) == pc for r, _ in sources):
            echecs.append("canari_est_source")
        if not sources:
            echecs.append("aucune_source")
        if len(canari) < N_MIN:
            echecs.append("donnees_canari_insuffisantes")
        elif sources and taux_succes(canari) < taux_succes(sources) - MARGE:
            echecs.append("regression_sur_canari")
    except Exception:  # noqa: BLE001
        echecs.append("erreur_canari")
    return {"ok": not echecs, "echecs": echecs, "mention": OMBRE, "projet_canari": projet_canari}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trace", default=ns.gate.chemin_trace())
    ap.add_argument("--journal", default=os.path.join(ORCH, "runs.jsonl"))
    ap.add_argument("--projet-canari", default=None)
    ap.add_argument("--sortie", default=None)
    a = ap.parse_args(argv)
    porte, ip = ns.lire_fin(a.trace)
    runs, ir = ns.lire_fin(a.journal)
    if ip["refus"] or ir["refus"]:
        print("eval_neural_gate : entree refusee.", file=sys.stderr)
        return 2
    prep = preparer(runs, porte)
    pol = npol.load_policy()
    rap = {"mention": OMBRE, "ecartes": prep["ecartes"], "n_vus": prep["n_vus"], "n_retenus": len(prep["retenus"])}
    if pol["ok"]:
        src, can = separer_canari(prep["retenus"], a.projet_canari)
        prop = proposer(pol["policy"], src)
        rap["proposition"] = prop.get("proposition")
        rap["hors_ligne"] = evaluer_hors_ligne(pol["policy"], prop, src)
        if a.projet_canari:
            rap["canari"] = evaluer_canari(src, can, a.projet_canari)
    if a.sortie is not None and not ns.ecrire_rapport(rap, a.sortie):
        print("eval_neural_gate : chemin de rapport refuse.", file=sys.stderr)
        return 3
    print(json.dumps(rap, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
