"""Release chain of a learned routing-weights version (story 14.6, FR69, AC14.6.2/3/4/7).

observe -> validate -> offline eval -> canary -> USER ARBITRATION -> promotion, with rollback.
Pure functions over an explicit state dict; the only writes are (a) the versions state file
`learning_versions*.json`, (b) the installed policy yaml `neural_policy*.yaml` under gravite/ or a tmp dir.
The user's acceptance is read ONLY from the real arbitrages.json (written solely by log_arbitrage.py, never
by these scripts); the only seam is the `lecteur` argument of the private `_promouvoir`, used by tests and
reachable from no CLI, no public signature and no path argument. These scripts NEVER write arbitrages.json, runs.jsonl, mode state or the real
neural_policy.yaml, never launch a process and never propagate (sharing only builds a package and checks the
per-target proof of propager-kit / verifier_propagation.py). Only a tmp dir under C:/tmp is usable for tests
(a tempfile 8.3 path such as C:/Users/CLAUDE~1 is refused by design).
Hostile input: exact type checks, bounded sizes, hazard paths and links refused, no exception escapes.
"""
import copy
import datetime
import importlib.util
import json
import os
import re
import sys
import unicodedata

ICI = os.path.dirname(os.path.abspath(__file__))
HUB = os.path.dirname(ICI)


def _charger(nom, chemin):
    spec = importlib.util.spec_from_file_location(nom, chemin)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _charger("validate_learning_ev", os.path.join(ICI, "eval_neural_gate.py"))
ns, npol = ev.ns, ev.npol
gate = ns.gate

ARBITRAGES_REEL = os.path.join(HUB, ".claude", "supervision", "arbitrages.json")
POLITIQUE_REELLE = os.path.join(HUB, ".claude", "orchestration", "neural_policy.yaml")
MAX_FICHIER = 8 * 1024 * 1024
MAX_VERSIONS = 200
# revocation stems looked for ONLY in the first segment of a decision (before the first ':', ' - ' or '(')
MOTS_REVOCATION = ("refus", "rejet", "annul", "revoqu", "retir", "abandon", "pas", "non")
RE_DEBUT_ACCEPTE = re.compile(r"accepte(?![a-z])")
RE_REVOCATION = re.compile(r"\b(" + "|".join(MOTS_REVOCATION) + r")")
CIBLES_DEFAUT = os.path.join(HUB, "projets.json")
CLES_PARTAGE = ("agregats", "versions_capacites", "tests", "cibles")
INTERDITS = ("transcript", "document_client", "client", "secret", "memoire", "memory", "password",
             "token_acces", "api_key", "credential")
RE_SECRET = re.compile(r"(sk-[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{12,}|Bearer\s+\S{8,}|BEGIN [A-Z ]*PRIVATE KEY|"
                       r"ghp_[A-Za-z0-9]{20,})")
RE_TEST = re.compile(r"^tests/[A-Za-z0-9_./-]{1,120}\.py$")
# closed set of verifier_propagation.py "identique" cells accepted as proof (user arbitration 2026-10-27):
# 'oui', 'oui (hors bandeau)' and 'oui (socle, chapitre local N l.)' with N a non-negative integer
IDENTIQUE_OK = ("oui", "oui (hors bandeau)")
RE_IDENTIQUE_SOCLE = re.compile(r"oui \(socle, chapitre local [0-9]{1,9} l\.\)")
RE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _racines():
    return [os.path.join(ns.ICI, "gravite")] + gate._racines_permises()[1:]


def _confine(chemin, prefixe, suffixe):
    """File `prefixe*suffixe` under gravite/ or a tmp dir (never elsewhere in the repo); no link/hazard."""
    try:
        if not ns._chemin_sur(chemin) or not ns._sans_lien(os.path.dirname(os.path.abspath(chemin))):
            return False
        if os.path.lexists(chemin) and not ns._fichier_regulier(chemin):
            return False
        reel = os.path.normcase(os.path.realpath(chemin))
        nom = os.path.basename(reel)
        if not (nom.startswith(prefixe) and nom.endswith(suffixe)):
            return False

        def sous(r):
            r = os.path.normcase(os.path.realpath(r))
            return os.path.commonpath([reel, r]) == r and reel != r
        rac = _racines()
        if sous(rac[0]):
            return True
        if sous(HUB):
            return False
        return any(sous(r) for r in rac[1:])
    except Exception:  # noqa: BLE001
        return False


def _est_reel(chemin, reel):
    try:
        return os.path.normcase(os.path.realpath(chemin)) == os.path.normcase(os.path.realpath(reel))
    except Exception:  # noqa: BLE001
        return True


def _ecrire_json(obj, chemin):
    tmp = f"{chemin}.{os.getpid()}.tmp"
    os.makedirs(os.path.dirname(os.path.abspath(chemin)), exist_ok=True)
    cree = False
    try:
        with open(tmp, "x", encoding="utf-8") as fh:
            cree = True
            fh.write(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1))
        os.replace(tmp, chemin)
    finally:
        if cree and os.path.lexists(tmp):
            os.remove(tmp)


def _lire_json(chemin):
    if not ns._fichier_regulier(chemin) or os.path.getsize(chemin) > MAX_FICHIER:
        return None
    with open(chemin, "rb") as fh:
        return json.loads(fh.read(MAX_FICHIER + 1).decode("utf-8"))


# ------------------------------------------------------------------ safety regression (AC14.6.7)
def regression_securite(policy):
    """Replays the safety suite on a policy: arm ceiling, approval always required, abstention on missing
    cost, quotas valid, and the gate never exceeds the ceiling nor drops the approval. Never raises."""
    echecs = []
    try:
        plafond = gate.PLAFOND_BRAS
        try:
            npol.validate(policy, plafond)
        except Exception:  # noqa: BLE001
            echecs.append("politique_invalide")
        sec = policy.get("securite") if type(policy) is dict else None
        if type(sec) is not dict or type(sec.get("plafond_bras")) is not int or not 1 <= sec["plafond_bras"] <= plafond:
            echecs.append("plafond_de_bras")
        if type(sec) is not dict or sec.get("requires_user_approval") is not True:
            echecs.append("approbation_toujours_requise")
        s = gate.decider({"run_id": "r", "etapes": 100, "independantes": True,
                          "cout_historique": 1.0, "qualite_historique": 0.9})
        pp = sec.get("plafond_bras") if type(sec) is dict else None
        # the gate's arm count (gravite cap) must not exceed THIS policy's own ceiling
        if not (s["decision"] == "C" and type(pp) is int and s["bras"] <= min(plafond, pp)
                and s["requires_user_approval"] is True):
            echecs.append("porte_hors_plafond")
        if not (npol.must_abstain(policy, False, True) and not npol.must_abstain(policy, True, True)):
            echecs.append("abstention_cout_manquant")
    except Exception:  # noqa: BLE001
        echecs.append("erreur_regression")
    return {"ok": not echecs, "echecs": echecs}


# ------------------------------------------------------------------ arbitration trace
def entree_arbitrage(version_id, decision, preuve=None):
    """An arbitrages.json entry (log_arbitrage.py format) for a learned version."""
    e = {"cible": f"apprentissage:{version_id}", "date": datetime.datetime.now().astimezone().strftime("%Y-%m-%d"),
         "titre": f"Promotion de la version apprise {version_id}", "categories": ["autre"], "decision": decision}
    if preuve:
        e["preuve_application"] = preuve
    return e


def _normaliser_decision(d):
    d = unicodedata.normalize("NFKD", d)
    return "".join(c for c in d if not unicodedata.combining(c)).casefold().strip()


def decision_acceptee(decision):
    """The decision (NFKD, accents and case ignored) STARTS with the token 'accepte' followed by a non-letter
    boundary, and its FIRST segment (up to the first ':', ' - ' or '(') holds no revocation word nor '?'.
    The justification after the segment may say anything (real decisions contain 'pas', 'non', quotes)."""
    try:
        if type(decision) is not str or len(decision) > 4000:
            return False
        n = _normaliser_decision(decision)
        if not RE_DEBUT_ACCEPTE.match(n):
            return False
        coupes = [i for i in (n.find(":"), n.find(" - "), n.find("(")) if i >= 0]
        seg = n[:min(coupes)] if coupes else n
        return "?" not in seg and not RE_REVOCATION.search(seg)
    except Exception:  # noqa: BLE001
        return False


def _norm_cible(c):
    """strip + NFKC + casefold, so 'Apprentissage:v2 ' and 'apprentissage:v2' are the same target."""
    try:
        return unicodedata.normalize("NFKC", c).strip().casefold() if type(c) is str else None
    except Exception:  # noqa: BLE001
        return None


def _lire_arbitrages_reels():
    return _lire_json(ARBITRAGES_REEL)


def arbitrage_accepte(data, version_id):
    """True iff the LAST entry for the target apprentissage:<version_id> in `data` (parsed arbitrages.json)
    is an acceptance: a later refusal or revocation cancels an earlier acceptance. Never raises."""
    try:
        if type(data) is not dict or type(data.get("arbitrages")) is not list \
                or type(version_id) is not str or not RE_ID.match(version_id):
            return False
        dernier = None
        for e in data["arbitrages"]:
            if type(e) is dict and _norm_cible(e.get("cible")) == _norm_cible(f"apprentissage:{version_id}"):
                dernier = e
        return dernier is not None and decision_acceptee(dernier.get("decision"))
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ versions, promotion, rollback
def etat_initial(policy):
    """Initial state from a valid policy, or None (refusal) when it is not one."""
    try:
        npol.validate(policy, gate.PLAFOND_BRAS)
        r = copy.deepcopy(policy["routage"])
    except Exception:  # noqa: BLE001
        return None
    return {"schema": 1, "actuelle": "v1", "versions": [{"id": "v1", "routage": r, "parent": None}],
            "journal": [{"evenement": "initial", "version": "v1"}]}


def _version(etat, vid):
    for v in etat["versions"]:
        if v["id"] == vid:
            return v
    return None


def politique_de(etat, base, vid):
    """The full policy of a version: `securite` always from the base, only `routage` from the version."""
    try:
        v = _version(etat, vid)
        if v is None:
            return None
        p = copy.deepcopy(base)
        p["routage"] = copy.deepcopy(v["routage"])
        return p
    except Exception:  # noqa: BLE001
        return None


JOURNAL_REEL = os.path.join(HUB, ".claude", "orchestration", "runs.jsonl")
TRACE_REELLE = None   # None: the gate trace path of gravite (ns.gate.chemin_trace()); tests patch these two names


def _retenus_reels():
    """(run, trace) pairs rebuilt by ev.preparer from the journal and gate-trace FILES, read here. None when a
    file is refused or unreadable."""
    porte, ip = ns.lire_fin(TRACE_REELLE or ns.gate.chemin_trace())
    runs, ir = ns.lire_fin(JOURNAL_REEL)
    if ip["refus"] or ir["refus"]:
        return None
    return ev.preparer(runs, porte)["retenus"]


def promouvoir(etat, base, proposition, projet_canari, vid):
    """Promote a proposal to version `vid`. The caller supplies NO evidence: the (run, trace) pairs are rebuilt
    HERE from the journal and gate-trace files (JOURNAL_REEL / TRACE_REELLE) through ev.preparer, then the
    offline evaluation and the canary are recomputed from them. ALL required: offline eval ok, canary ok, user
    acceptance in the REAL arbitrages.json, non-empty proposal, bounded step AND bounded cumulative drift vs
    v1, weights summing to 1 above the floor, valid policy, safety regression green. A journal that cannot be
    read gives no pairs, hence failing eval and canary. Returns {'ok', 'echecs', 'etat'} - the state is
    returned, never mutated, and nothing is written."""
    try:
        retenus = _retenus_reels()
    except Exception:  # noqa: BLE001
        retenus = None
    return _promouvoir(etat, base, proposition, retenus, projet_canari, vid, _lire_arbitrages_reels)


def _promouvoir(etat, base, proposition, retenus, projet_canari, vid, lecteur):
    """Implementation of promouvoir; `lecteur` (returns arbitrages.json content) is the test seam and is
    reachable only through this private function, never through the public signature. `retenus` is
    caller-supplied HERE (test seam only); the public promouvoir rebuilds it from the journal."""
    echecs = []
    try:
        if type(vid) is not str or not RE_ID.match(vid) or _version(etat, vid) is not None \
                or len(etat["versions"]) >= MAX_VERSIONS:
            return {"ok": False, "echecs": ["version_invalide"], "etat": etat}
        actuelle = politique_de(etat, base, etat["actuelle"])
        res = npol.propose_update(actuelle, proposition)
        if type(proposition) is not dict or not proposition:
            echecs.append("proposition_vide")
        retenus = retenus if type(retenus) is list else []
        sources, canari = ev.separer_canari(retenus, projet_canari)
        hl = ev.evaluer_hors_ligne(actuelle, {"ok": True, "politique": res["policy"]} if res["accepted"]
                                   else {"ok": False}, sources)
        if hl["ok"] is not True:
            echecs.append("evaluation_hors_ligne_manquante")
        if ev.evaluer_canari(sources, canari, projet_canari)["ok"] is not True:
            echecs.append("canari_manquant")
        if not arbitrage_accepte(lecteur(), vid):
            echecs.append("arbitrage_utilisateur_manquant")
        if not res["accepted"]:
            echecs.append("proposition_refusee")
        else:
            if res["policy"]["routage"] == actuelle["routage"] and "proposition_vide" not in echecs:
                echecs.append("proposition_vide")
            if not ev.amplitude_ok(actuelle, res["policy"]):
                echecs.append("amplitude_hors_borne")
            echecs.extend(ev.bornes_cumulees(politique_de(etat, base, "v1"), res["policy"]))
            if not regression_securite(res["policy"])["ok"]:
                echecs.append("regression_securite")
        if echecs:
            return {"ok": False, "echecs": echecs, "etat": etat}
        nv = copy.deepcopy(etat)
        nv["versions"].append({"id": vid, "routage": res["policy"]["routage"], "parent": etat["actuelle"]})
        nv["journal"].append({"evenement": "promotion", "version": vid, "de": etat["actuelle"]})
        nv["actuelle"] = vid
        return {"ok": True, "echecs": [], "etat": nv}
    except Exception:  # noqa: BLE001
        return {"ok": False, "echecs": ["erreur_promotion"], "etat": etat}


def retour_arriere(etat, base):
    """Restore the previous version; the trace says so; the safety suite is replayed ON the restored
    version (AC14.6.7). Refused if there is no previous version or the restored one fails."""
    try:
        cur = _version(etat, etat["actuelle"])
        if cur is None or cur["parent"] is None or _version(etat, cur["parent"]) is None:
            return {"ok": False, "echecs": ["pas_de_version_precedente"], "etat": etat}
        reg = regression_securite(politique_de(etat, base, cur["parent"]))
        if not reg["ok"]:
            return {"ok": False, "echecs": ["regression_securite_sur_precedente"] + reg["echecs"], "etat": etat}
        nv = copy.deepcopy(etat)
        nv["journal"].append({"evenement": "retour_arriere", "de": cur["id"], "vers": cur["parent"]})
        nv["actuelle"] = cur["parent"]
        return {"ok": True, "echecs": [], "etat": nv}
    except Exception:  # noqa: BLE001
        return {"ok": False, "echecs": ["erreur_retour_arriere"], "etat": etat}


def ecrire_etat(etat, chemin):
    try:
        if not _confine(chemin, "learning_versions", ".json"):
            return False
        _ecrire_json(etat, chemin)
        return True
    except Exception:  # noqa: BLE001
        return False


def _protege(chemin):
    """Real arbitrages.json, runs.jsonl, the real neural_policy.yaml, any *.jsonl or arbitrages*.json."""
    n = os.path.basename(str(chemin)).casefold()
    return (n.endswith(".jsonl") or n.startswith("arbitrages")
            or _est_reel(chemin, POLITIQUE_REELLE) or _est_reel(chemin, ARBITRAGES_REEL))


def installer(etat, base, chemin_yaml):
    """Write the current version as a policy yaml `neural_policy*.yaml` (gravite/ or tmp only), then reload it
    through load_policy and replay the safety suite (AC14.6.7). Protected files are refused outright."""
    try:
        import yaml
        if _protege(chemin_yaml):
            return {"ok": False, "echecs": ["fichier_protege"]}
        if not _confine(chemin_yaml, "neural_policy", ".yaml"):
            return {"ok": False, "echecs": ["chemin_refuse"]}
        pol = politique_de(etat, base, etat["actuelle"])
        if pol is None or not regression_securite(pol)["ok"]:
            return {"ok": False, "echecs": ["regression_securite"]}
        tmp = f"{chemin_yaml}.{os.getpid()}.tmp"
        with open(tmp, "x", encoding="utf-8") as fh:
            fh.write(yaml.safe_dump(pol, sort_keys=False))
        os.replace(tmp, chemin_yaml)
        rel = npol.load_policy(chemin_yaml)
        rg = regression_securite(rel["policy"]) if rel["ok"] else {"ok": False}
        return {"ok": bool(rel["ok"] and rg["ok"]),
                "echecs": [] if rg["ok"] else ["regression_securite_apres_installation"]}
    except Exception:  # noqa: BLE001
        return {"ok": False, "echecs": ["erreur_installation"]}


# ------------------------------------------------------------------ sharing between projects (AC14.6.4)
def _suspect(v, profondeur=0):
    """True if a key/value looks like a transcript, client document, secret or project memory."""
    if profondeur > 6:
        return True
    if type(v) is str:
        return (bool(RE_SECRET.search(v)) or len(v) > 200
                or any(m in v.casefold() for m in INTERDITS))
    if type(v) is dict:
        return any(_suspect(k, profondeur + 1) or any(m in str(k).casefold() for m in INTERDITS)
                   or _suspect(x, profondeur + 1) for k, x in v.items())
    if type(v) in (list, tuple):
        return any(_suspect(x, profondeur + 1) for x in v)
    return False


def _cibles_connues():
    try:
        d = _lire_json(CIBLES_DEFAUT)
        return {p["nom"] for p in d["projets"] if type(p) is dict and type(p.get("nom")) is str}
    except Exception:  # noqa: BLE001
        return set()


def preparer_partage(paquet, *, cibles_connues=None):
    """Only non-sensitive aggregates (finite numbers), capability versions and repo tests may be shared.
    Anything else is refused with the reason. Returns {'ok', 'echecs', 'paquet'}; never raises."""
    echecs = []
    try:
        if type(paquet) is not dict:
            return {"ok": False, "echecs": ["paquet_illisible"], "paquet": None}
        for k in paquet:
            if k not in CLES_PARTAGE:
                echecs.append(f"cle_interdite:{str(k)[:40]}")
        if _suspect({k: v for k, v in paquet.items() if k in CLES_PARTAGE}):
            echecs.append("contenu_sensible")
        ag = paquet.get("agregats", {})
        if type(ag) is not dict or not all(
                type(x) in (int, float) and ev.math.isfinite(x) and abs(x) < 1e12 for x in ag.values()):
            echecs.append("agregats_non_numeriques")
        vc = paquet.get("versions_capacites", [])
        if type(vc) is not list or not all(type(x) is str and RE_ID.match(x) for x in vc):
            echecs.append("versions_invalides")
        ts = paquet.get("tests", [])
        if type(ts) is not list or not all(type(x) is str and RE_TEST.match(x) and ".." not in x for x in ts):
            echecs.append("tests_invalides")
        cb = paquet.get("cibles", [])
        connues = _cibles_connues() if cibles_connues is None else set(cibles_connues)
        if type(cb) is not list or not cb or not all(type(x) is str and x in connues for x in cb):
            echecs.append("cibles_invalides")
        return {"ok": not echecs, "echecs": echecs, "paquet": copy.deepcopy(paquet) if not echecs else None}
    except Exception:  # noqa: BLE001
        return {"ok": False, "echecs": ["erreur_partage"], "paquet": None}


def _identique_ok(v):
    return type(v) is str and len(v) <= 80 and (v in IDENTIQUE_OK or bool(RE_IDENTIQUE_SOCLE.fullmatch(v)))


def partage_prouve(paquet, preuves, *, cibles_connues=None):
    """Propagation goes through propager-kit with ONE proof row per target (verifier_propagation.py table:
    present == 'oui' and identique starting with 'oui'). A target without proof blocks the sharing."""
    try:
        p = preparer_partage(paquet, cibles_connues=cibles_connues)
        if not p["ok"]:
            return {"ok": False, "echecs": p["echecs"]}
        manque = []
        for c in paquet["cibles"]:
            r = preuves.get(c) if type(preuves) is dict else None
            if not (type(r) is dict and r.get("present") == "oui" and _identique_ok(r.get("identique"))):
                manque.append(c)
        return {"ok": not manque, "echecs": [f"preuve_manquante:{c}" for c in manque],
                "commande": "py scripts/verifier_propagation.py <fichiers> --tests (skill propager-kit)"}
    except Exception:  # noqa: BLE001
        return {"ok": False, "echecs": ["erreur_preuve"]}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    base = npol.load_policy()
    if argv[:1] == ["--regression"]:
        r = regression_securite(base["policy"]) if base["ok"] else {"ok": False, "echecs": ["politique_illisible"]}
        print(json.dumps(r, sort_keys=True))
        return 0 if r["ok"] else 2
    print("usage: validate_learning_release.py --regression  (the rest is used as a library)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
