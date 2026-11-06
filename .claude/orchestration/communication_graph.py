"""Story 14.2: bounded need/offer message graph between agents of one run.

Proposal-only: nothing is executed; every decision names the rule in cause.
Quotas come from neural_policy.yaml (story 14.1) via neural_policy.load_policy.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import unicodedata
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_cg_neural_policy", _HERE / "neural_policy.py")
_np = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_np)

REQUIRED = ("project_id", "run_id", "task_id", "emitter", "recipients", "intent", "need",
            "offer", "artifact_refs", "classification", "expires_at", "response_budget")
CLASSIFICATIONS = ("publique", "interne", "sensible")
INSUFFICIENT = "données insuffisantes"
RULES = ("doublon", "destinataire_sans_besoin", "politique_contredite", "preuve_deja_validee",
         "hausse_sans_fait_nouveau", "dependance_non_terminee", "quota_depasse")


def _exact_int(v, minimum):
    """Exact int only (bool and int subclasses are refused: a subclass can lie in comparisons)."""
    return type(v) is int and v >= minimum


def _strs(v):
    return type(v) is list and all(type(x) is str for x in v)


def _snapshot(msg):
    """Plain-dict copy of an exact dict (a dict subclass could lie on every access)."""
    if type(msg) is not dict:
        return None
    return {k: v for k, v in dict.items(msg) if type(k) is str}


def _validate(m, project_id):
    for k in REQUIRED:
        if k not in m or m[k] is None:
            return False, f"champ_manquant:{k}"
        if type(m[k]) in (str, list) and len(m[k]) == 0 and k != "artifact_refs":
            return False, f"champ_manquant:{k}"
    for k in ("project_id", "emitter", "intent", "need", "offer", "task_id", "run_id"):
        if type(m[k]) is not str:
            return False, f"type_invalide:{k}"
    if type(project_id) is not str or m["project_id"] != project_id:
        return False, "autre_projet"
    rcp = m["recipients"]
    if not _strs(rcp) or not all(rcp):
        return False, "destinataires_invalides"
    if len(set(rcp)) != len(rcp):
        return False, "destinataires_dupliques"
    if m["emitter"] in rcp:
        return False, "emetteur_destinataire"
    if not _strs(m["artifact_refs"]):
        return False, "references_invalides"
    if "depends_on" in m and not _strs(m["depends_on"]):
        return False, "dependances_invalides"
    if type(m["classification"]) is not str or m["classification"] not in CLASSIFICATIONS:
        return False, "classification_invalide"
    ea = m["expires_at"]
    if type(ea) not in (int, float) or not math.isfinite(ea):
        return False, "expiration_invalide"
    if not _exact_int(m["response_budget"], 1):
        return False, "budget_invalide"
    return True, "ok"


def validate_message(msg, project_id):
    """Return (ok, reason). Never raises: any failure while validating is a refusal."""
    try:
        m = _snapshot(msg)
        if m is None:
            return False, "message_invalide"
        return _validate(m, project_id)
    except Exception:  # noqa: BLE001 - hostile value (raising __eq__, ...) => refusal
        return False, "message_invalide"


def normalize_intent(text):
    """NFKC, drop format (Cf, e.g. zero-width) characters, strip, casefold."""
    if type(text) is not str:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = "".join(c for c in t if unicodedata.category(c) != "Cf")
    return t.strip().casefold()


def fingerprint(msg):
    body = {k: msg[k] for k in ("emitter", "intent", "need", "offer")}
    body["recipients"] = sorted(msg["recipients"])
    body["artifact_refs"] = sorted(msg["artifact_refs"])
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def _out(decision, rule):
    return {"decision": decision, "rule": rule}


def _real_int(v):
    return _exact_int(v, 1)


def _checked_policy(pol):
    """Never trust an injected policy: quotas must be real ints >= 1, capped by the hard ceilings."""
    try:
        if type(pol) is not dict or pol.get("ok") is not True:
            return None
        sec = pol["policy"]["securite"]
        q = sec["quotas_max"]
        if not (_real_int(q["messages_par_run"]) and _real_int(q["tokens_par_run"])
                and _real_int(sec["plafond_bras"])):
            return None
        return {"plafond_bras": sec["plafond_bras"],
                "max_msgs": min(int(q["messages_par_run"]), _np.MAX_MESSAGES_PAR_RUN),
                "max_tokens": min(int(q["tokens_par_run"]), _np.MAX_TOKENS_PAR_RUN)}
    except Exception:  # noqa: BLE001 - malformed policy => safe refusal
        return None


class Graph:
    def __init__(self, project_id, run_id, policy=None):
        self.project_id, self.run_id = project_id, run_id
        pol = policy if policy is not None else _np.load_policy()
        self.policy = _checked_policy(pol)
        self.needs = {}          # agent -> set of needs
        self.validated = set()   # artifact refs already validated
        self.seen_refs = set()
        self.done_tasks = set()
        self.prints = set()
        self.edges = []          # accepted messages
        self.tokens = 0

    def declare_need(self, agent, need):
        """Returns True when recorded; non-str input is refused (False), never raises."""
        if type(agent) is not str or type(need) is not str or not agent or not need:
            return False
        self.needs.setdefault(agent, set()).add(need)
        return True

    def mark_validated(self, ref):
        if type(ref) is not str:
            return False
        self.validated.add(ref)
        return True

    def mark_done(self, task):
        if type(task) is not str:
            return False
        self.done_tasks.add(task)
        return True

    def _quota(self):
        return self.policy["max_msgs"], self.policy["max_tokens"]

    def request_link(self, msg, now=0):
        """Decide on a link. Never raises. Returns {'decision': accept|refuse|delay, 'rule': str|None}."""
        try:
            return self._decide(msg, now)
        except Exception:  # noqa: BLE001 - contract: a hostile message is a refusal
            return _out("refuse", "erreur_validation")

    def _decide(self, msg, now):
        m = _snapshot(msg)
        if m is None:
            return _out("refuse", "message_invalide")
        ok, reason = _validate(m, self.project_id)
        if not ok:
            return _out("refuse", reason)
        if type(now) not in (int, float) or not math.isfinite(now):
            return _out("refuse", "horloge_invalide")
        if m["run_id"] != self.run_id:
            return _out("refuse", "autre_run")
        if self.policy is None:
            return _out("refuse", "politique_indisponible")
        fp = fingerprint(m)
        if fp in self.prints:
            return _out("refuse", "doublon")
        if any(m["need"] not in self.needs.get(r, set()) for r in m["recipients"]):
            return _out("refuse", "destinataire_sans_besoin")
        if len(m["recipients"]) > self.policy["plafond_bras"] or m["expires_at"] <= now:
            return _out("refuse", "politique_contredite")
        refs = m["artifact_refs"]
        if refs and all(r in self.validated for r in refs):
            return _out("refuse", "preuve_deja_validee")
        if normalize_intent(m["intent"]) == "hausse" and not any(r not in self.seen_refs for r in refs):
            return _out("refuse", "hausse_sans_fait_nouveau")
        if any(d not in self.done_tasks for d in m.get("depends_on", [])):
            return _out("delay", "dependance_non_terminee")
        max_msgs, max_tokens = self._quota()
        if len(self.edges) + 1 > max_msgs or self.tokens + m["response_budget"] > max_tokens:
            return _out("refuse", "quota_depasse")
        self.prints.add(fp)
        self.seen_refs.update(refs)
        self.tokens += m["response_budget"]
        self.edges.append(m)
        return _out("accept", None)


def _tok(v):
    return _exact_int(v, 0)


def compute_metrics(run):
    """run: {'messages': [{'tokens', 'useful': bool|None}], 'accepted_outputs': int,
    'tokens_total': int, 'edges': [(a, b)]}. Each metric: {'value', 'population'} or INSUFFICIENT.
    Never raises: malformed input is ignored or gives INSUFFICIENT."""
    try:
        return _metrics(run)
    except Exception:  # noqa: BLE001
        return {k: INSUFFICIENT for k in ("messages_per_accepted", "tokens_coordination_share",
                                          "edge_precision", "graph_degree")}


def _metrics(run):
    if type(run) is not dict:
        run = {}
    msgs = run.get("messages") if type(run.get("messages")) is list else []
    acc = run["accepted_outputs"] if _tok(run.get("accepted_outputs")) else 0
    total = run["tokens_total"] if _tok(run.get("tokens_total")) else 0
    raw_edges = run.get("edges") if type(run.get("edges")) is list else []
    edges = [e for e in raw_edges if type(e) in (tuple, list) and len(e) == 2
             and type(e[0]) is str and type(e[1]) is str]
    out = {}
    out["messages_per_accepted"] = ({"value": len(msgs) / acc, "population": {"messages": len(msgs), "accepted": acc}}
                                    if acc > 0 else INSUFFICIENT)
    coord = sum(m["tokens"] for m in msgs if type(m) is dict and _tok(m.get("tokens")))
    out["tokens_coordination_share"] = ({"value": coord / total, "population": {"coordination_tokens": coord, "total_tokens": total}}
                                        if total > 0 and msgs else INSUFFICIENT)
    judged = [m for m in msgs if type(m) is dict and type(m.get("useful")) is bool]
    useful = sum(1 for m in judged if m["useful"])
    out["edge_precision"] = ({"value": useful / len(judged), "population": {"judged": len(judged), "useful": useful}}
                             if judged else INSUFFICIENT)
    nodes = {n for e in edges for n in e if type(n) is str}
    out["graph_degree"] = ({"value": 2 * len(edges) / len(nodes), "population": {"nodes": len(nodes), "edges": len(edges)}}
                           if nodes else INSUFFICIENT)
    return out


def _describe(exc):
    """Exception description that cannot raise, even if __str__/__repr__ do."""
    try:
        name = type(exc).__name__
    except Exception:  # noqa: BLE001
        name = "Exception"
    try:
        return f"{name}: {exc}"
    except Exception:  # noqa: BLE001
        return f"{name}: <description illisible>"


def adversarial_review(question, contradictors, conclude):
    """Each contradictor sees ONLY the question. The conclusion is computed afterwards and the
    objections are kept in the synthesis. A contradictor that raises or returns a non-dict, or a
    raising conclude, is recorded as an objection with a clear error; nothing escapes."""
    analyses, errors = [], []
    for i, c in enumerate(contradictors if type(contradictors) in (list, tuple) else []):
        try:
            a = c(question)
            if type(a) is not dict:
                raise TypeError(f"contradicteur {i}: analyse non-dict ({type(a).__name__})")
        except Exception as exc:  # noqa: BLE001
            msg = f"erreur contradicteur {i}: {_describe(exc)}"
            errors.append(msg)
            a = {"verdict": None, "objection": msg, "error": msg}
        analyses.append(a)
    try:
        conclusion = conclude(question, analyses)
    except Exception as exc:  # noqa: BLE001
        conclusion = None
        errors.append(f"erreur conclusion: {_describe(exc)}")
    objections = [a["objection"] for a in analyses if type(a.get("objection")) is str and a["objection"]]
    objections += [e for e in errors if e not in objections]
    return {"conclusion": conclusion, "analyses": analyses, "objections": objections, "errors": errors}
