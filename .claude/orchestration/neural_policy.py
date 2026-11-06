"""Loader/validator of neural_policy.yaml (story 14.1).

Safe position: any absent/unreadable/oversized/unknown-version/invalid file gives
ABSTAIN (no action); load_policy never raises and never returns a permissive default.
PLAFOND_BRAS / PLAFOND_AUGMENTE are read by parsing gravite.py text (no import: no side effects);
the current mode (standard unless the signed state says augmente; read through gravite.mode_effectif,
standard on any failure) only picks which one applies.

Vocabulary note (two spellings, both intentional and pinned by a test):
`measured_cost_coverage` uses the masculine `inconnu`, while `uncertainty_status`
uses the feminine `inconnue`. Emitters (14.2 / 8.24 neural_gate) must use each form as is.
Hard ceilings (MAX_MESSAGES_PAR_RUN = 60, MAX_TOKENS_PAR_RUN = 600_000) are code constants set by
the user (2026-10-26); the yaml cannot raise them.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

try:
    import yaml  # PyYAML is pinned in requirements-dev.txt (CI installs it)
except ImportError:  # pragma: no cover - no yaml => safe position
    yaml = None

HERE = Path(__file__).resolve().parent
POLICY_PATH = HERE / "neural_policy.yaml"
GRAVITE_PATH = HERE / "gravite.py"
MAX_BYTES = 1024 * 1024
KNOWN_VERSIONS = (1,)
MAX_MESSAGES_PAR_RUN = 60  # user decision 2026-10-26 (initial quota 24)
MAX_TOKENS_PAR_RUN = 600_000  # user decision 2026-10-26 (initial quota 240000)
MIN_CONFIANCE_MIN = 0.5  # floor of the confidence barrier; to be arbitrated by the user
MAX_GAIN_MIN = 1.0  # upper bound of gain_min; to be arbitrated by the user
ABSTAIN = "ABSTAIN"
# Closed vocabularies of the optional gate-trace fields (AC14.1.4)
TRACE_VOCAB = {
    "measured_cost_coverage": ("complet", "partiel", "inconnu"),
    "uncertainty_status": ("mesuree", "estimee", "inconnue"),
    "truncated": (True, False),
}

_SCHEMA = {
    "version": int,
    "securite": {
        "plafond_bras": int,
        "requires_user_approval": bool,
        "on_missing_cost_or_capacity": str,
        "quotas_max": {"agents_actifs": int, "messages_par_run": int, "tokens_par_run": int},
    },
    "routage": {
        "seuils": {"gain_min": float, "confiance_min": float},
        "poids": {"cout": float, "qualite": float, "delai": float},
    },
}


class PolicyError(ValueError):
    pass


def safe_position(reason):
    return {"ok": False, "decision": ABSTAIN, "action": None, "reason": reason, "policy": None}


def read_plafond_bras(path=None):
    m = re.search(r"^PLAFOND_BRAS\s*=\s*(\d+)\s*(?:#.*)?$",
                  Path(path or GRAVITE_PATH).read_text(encoding="utf-8"), re.M)
    if not m:
        raise PolicyError("PLAFOND_BRAS not found in gravite.py")
    return int(m.group(1))


def read_plafond_augmente(path=None):
    m = re.search(r"^PLAFOND_AUGMENTE\s*=\s*(\d+)\s*(?:#.*)?$",
                  Path(path or GRAVITE_PATH).read_text(encoding="utf-8"), re.M)
    if not m:
        raise PolicyError("PLAFOND_AUGMENTE not found in gravite.py")
    return int(m.group(1))


def _mode_courant(path=None):
    """Verified mode of the state (via gravite.mode_effectif); standard on any failure."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("gravite_policy_mode", Path(path or GRAVITE_PATH))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.mode_effectif()
    except Exception:  # noqa: BLE001 - unreadable state is standard (fail closed)
        return "standard"


def plafond_courant(path=None, mode=None):
    """Cap of the current mode: PLAFOND_AUGMENTE only for mode `augmente`, PLAFOND_BRAS otherwise."""
    mode = _mode_courant(path) if mode is None else mode
    return read_plafond_augmente(path) if mode == "augmente" else read_plafond_bras(path)


def _loader():
    class Strict(yaml.SafeLoader):
        pass

    def construct(loader, node, deep=False):
        seen = set()
        for k, _ in node.value:
            key = loader.construct_object(k, deep=True)
            if key in seen:
                raise PolicyError(f"duplicate key: {key!r}")
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep)

    Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct)
    return Strict


def _check(node, schema, path):
    if not isinstance(node, dict):
        raise PolicyError(f"{path or 'root'}: mapping expected")
    unknown = set(node) - set(schema)
    if unknown:
        raise PolicyError(f"{path or 'root'}: unknown key {sorted(map(str, unknown))}")
    missing = set(schema) - set(node)
    if missing:
        raise PolicyError(f"{path or 'root'}: missing key {sorted(missing)}")
    for k, t in schema.items():
        v, p = node[k], f"{path}.{k}" if path else k
        if isinstance(t, dict):
            _check(v, t, p)
        elif t is bool:
            if not isinstance(v, bool):
                raise PolicyError(f"{p}: bool expected")
        elif t is str:
            if not isinstance(v, str):
                raise PolicyError(f"{p}: string expected")
        elif t is int:
            if isinstance(v, bool) or not isinstance(v, int):
                raise PolicyError(f"{p}: int expected")
        elif t is float:
            if (isinstance(v, bool) or not isinstance(v, (int, float)) or v != v
                    or v in (float("inf"), float("-inf"))):
                raise PolicyError(f"{p}: finite number expected")


def validate(data, plafond=None):
    """Raise PolicyError if data is not a valid policy."""
    _check(data, _SCHEMA, "")
    if data["version"] not in KNOWN_VERSIONS:
        raise PolicyError(f"unknown version {data['version']}")
    plafond = plafond_courant() if plafond is None else plafond
    s = data["securite"]
    if s["requires_user_approval"] is not True:
        raise PolicyError("securite.requires_user_approval must be true")
    if not 1 <= s["plafond_bras"] <= plafond:
        raise PolicyError(f"securite.plafond_bras must be in 1..{plafond}")
    if s["on_missing_cost_or_capacity"] != "abstain":
        raise PolicyError("securite.on_missing_cost_or_capacity must be 'abstain'")
    q = s["quotas_max"]
    if any(v < 1 for v in q.values()):
        raise PolicyError("securite.quotas_max must be >= 1")
    if q["messages_par_run"] > MAX_MESSAGES_PAR_RUN:
        raise PolicyError(f"quotas_max.messages_par_run exceeds ceiling {MAX_MESSAGES_PAR_RUN}")
    if q["tokens_par_run"] > MAX_TOKENS_PAR_RUN:
        raise PolicyError(f"quotas_max.tokens_par_run exceeds ceiling {MAX_TOKENS_PAR_RUN}")
    if q["agents_actifs"] > s["plafond_bras"]:
        raise PolicyError("quotas_max.agents_actifs exceeds plafond_bras")
    if any(v < 0 for v in data["routage"]["poids"].values()):
        raise PolicyError("routage.poids must be >= 0")
    if any(v > 1 for v in data["routage"]["poids"].values()):
        raise PolicyError("routage.poids must be <= 1")
    if not any(v > 0 for v in data["routage"]["poids"].values()):
        raise PolicyError("routage.poids must contain at least one weight > 0")
    t = data["routage"]["seuils"]
    if not MIN_CONFIANCE_MIN <= t["confiance_min"] <= 1:
        raise PolicyError(f"routage.seuils.confiance_min must be in {MIN_CONFIANCE_MIN}..1")
    if not 0 <= t["gain_min"] <= MAX_GAIN_MIN:
        raise PolicyError(f"routage.seuils.gain_min must be in 0..{MAX_GAIN_MIN}")


def load_policy(path=None):
    """Never raises. Returns {'ok': True, 'policy': dict, ...} or the safe position."""
    try:
        if yaml is None:
            return safe_position("PyYAML unavailable")
        p = Path(path or POLICY_PATH)
        if not p.is_file():
            return safe_position("policy file absent")
        if p.stat().st_size > MAX_BYTES:
            return safe_position("policy file oversized")
        raw = p.read_bytes()
        if len(raw) > MAX_BYTES:
            return safe_position("policy file oversized")
        data = yaml.load(raw.decode("utf-8"), Loader=_loader())  # noqa: S506 - SafeLoader subclass
        validate(data)
        return {"ok": True, "decision": None, "action": None, "reason": "ok", "policy": data}
    except Exception as exc:  # noqa: BLE001 - contract: never raise to the caller
        return safe_position(f"policy invalid or unreadable: {type(exc).__name__}: {exc}")


def propose_update(policy, proposal):
    """Learning update proposal (partial nested dict). Refused if it touches `securite`
    (or `version`), or if the merged result is not a valid policy."""
    try:
        if not isinstance(proposal, dict) or not isinstance(policy, dict):
            return {"accepted": False, "reason": "proposal must be a mapping", "policy": None}
        for k in proposal:
            if str(k).startswith("securite") or k == "version":
                return {"accepted": False,
                        "reason": f"learning may not touch {k!r} (securite is not learnable)",
                        "policy": None}
        if set(proposal) - {"routage"}:
            return {"accepted": False, "reason": "only the routage section is updatable", "policy": None}
        merged = copy.deepcopy(policy)

        def merge(dst, src, path):
            if not isinstance(src, dict):
                raise PolicyError(f"{path}: mapping expected")
            for k, v in src.items():
                if k not in dst:
                    raise PolicyError(f"{path}.{k}: unknown key")
                if isinstance(dst[k], dict):
                    merge(dst[k], v, f"{path}.{k}")
                else:
                    dst[k] = v

        merge(merged, proposal, "")
        validate(merged)
        return {"accepted": True, "reason": "ok", "policy": merged}
    except Exception as exc:  # noqa: BLE001
        return {"accepted": False, "reason": f"{type(exc).__name__}: {exc}", "policy": None}


def validate_trace(trace):
    """Optional trace fields must use the closed vocabulary; unknown keys refused.
    Returns (ok, reason)."""
    if not isinstance(trace, dict):
        return False, "trace must be a mapping"
    for k, v in trace.items():
        if k not in TRACE_VOCAB:
            return False, f"unknown trace field {k!r}"
        if not any(v is a or (type(v) is type(a) and v == a) for a in TRACE_VOCAB[k]):
            return False, f"{k}: value {v!r} outside vocabulary"
    return True, "ok"


def must_abstain(policy, cost_known, capacity_known):
    """Policy imposes abstention when cost or capacity is missing (or the policy is not usable)."""
    if not isinstance(policy, dict):
        return True
    sec = policy.get("securite")
    if not isinstance(sec, dict) or sec.get("on_missing_cost_or_capacity") != "abstain":
        return True
    return not (cost_known is True and capacity_known is True)
