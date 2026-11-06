"""Gravitational score: activable orchestrator mode, OFF by default (lot G1, GMAH 3).

score = w_attr * attraction - sum(w_i * penalty_i)
attraction = base / (kappa + base), base = priorite*fit*preuve*reuse / (eps+distance)**rho
Hard eligibility filters run FIRST and are never compensable.
Score fields accept only int/float (not bool): Decimal, Fraction, bytes, str, np.float32 and
np.int64 (not int/float subclasses) are excluded as entree_non_finie; a finite value below 0 is
excluded as entree_hors_plage; a value above 1 stays clipped to 1 by _clip (story 8.8).

CLI: py .claude/orchestration/gravite.py on --arbitrage <key> | off | status | comparer <file.json>
`comparer` (story 8.6) is read-only: it prints classic / gravitational / linear orders as JSON and
never touches etat.json or trace.jsonl (plafond gap vs classer(): see comparer.__doc__).
Activation is locked behind a traced arbitration (G1-verrou): `on` needs the CONTENT KEY
`<date>|<cible>|<first 12 hex of sha256(decision)>` of an entry of arbitrages.json whose cible is
exactly `hub:gmah`, whose date is an ISO date not after today (UTC; a future date is refused as
`date_future`, an unparsable one as `date_invalide`) and whose decision is, EXACTLY (fullmatch), one of
the closed whitelist DECISIONS_ACTIVATION (story 8.16): `ACCEPTE : activer-gravitationnel`, the same
without the colon or its spaces, or `ACCEPTE : activer-gravitationnel — ADOPTE GMAH tapé par
l'utilisateur le YYYY-MM-DD` (the form logged by modes.md). Any suffix (`— REFUSE`, `pour le hub`) fails. The key is stored in etat.json as the proof and re-validated against
arbitrages.json on EVERY read of actif_verifie() (fail closed): a bare flag opens nothing.
Weight bounds (engineering safety bounds, NOT from the Imports, set 2026-10-25): w_attr in [0, 100]
(0 allowed: the Imports only require w_A >= 0), other penalty weights in [0, 100] with cout and risque
>= 0.01 (never switched off), kappa in [1e-9, 1e6], eps >= 1e-9, rho in [0, 10]; all inclusive.
Each flag change is appended to gravite/trace.jsonl (dedicated append-only log, not runs.jsonl).
`off` and `status` stay free, even when arbitrages.json is missing or unreadable.
Story 8.22: any later entry of the same cible whose decision is not a neutral
verdict (accepte/reporte) nor an exact DECISIONS_ACTIVATION form revokes the mode (etat_verifie() -> (False, "revoque"); scope and
fail-closed rule in _revoquee.__doc__); `off` first writes a stop
marker next to etat.json (arret.flag), so a read-only etat.json still leaves the mode off; the date
inside the dated decision form is checked like the `date` field; arbitrages.json larger than
TAILLE_MAX_ARBITRAGES or holding a duplicate JSON key is refused (no last-key-wins).
Two-file design limits (accepted): deleting arret.flag while etat.json is still active re-enables
the mode (needs write access to gravite/); conversely anyone able to write arbitrages.json can
switch the mode off by appending a revocation (fail-safe direction).
Provenance token (story 8.21, FR54): `on` also consumes a token that only the UserPromptSubmit hook
provenance_gmah.py issues when the typed prompt is exactly `ADOPTE GMAH` (gravite/jetons.jsonl,
next to etat.json). The token must be fresh (JETON_TTL_S) and unused, and is
bound to the key at consumption; it is HMAC-signed with a key outside the repo
(%APPDATA%/claude-gmah/jeton.key). A forged arbitration entry alone (even dated 2099) opens nothing.
Residual risk: an agent with unrestricted Bash under the same Windows account can read the key or
edit the hook; the boundary is Claude Code's protection of .claude/** plus human approval of Bash
touching gravite/.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import os
import posixpath
import re
import sys
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gmah_cle as _cle  # noqa: E402 - DPAPI key storage shared with the hook (story 14.11)

ETAT = Path(__file__).resolve().parent / "gravite" / "etat.json"
TRACE = Path(__file__).resolve().parent / "gravite" / "trace.jsonl"
ARBITRAGES = Path(__file__).resolve().parents[1] / "supervision" / "arbitrages.json"
CIBLE_ARBITRAGE = "hub:gmah"
MARQUEUR = "activer-gravitationnel"
DECISIONS_ACTIVATION = (  # closed whitelist, matched with fullmatch (story 8.16)
    re.compile(r"ACCEPTE : activer-gravitationnel"),
    re.compile(r"ACCEPTE activer-gravitationnel"),
    re.compile(r"ACCEPTE:activer-gravitationnel"),
    re.compile("ACCEPTE : activer-gravitationnel — ADOPTE GMAH tapé par l'utilisateur le "
               r"[0-9]{4}-[0-9]{2}-[0-9]{2}"),
    re.compile("ACCEPTE : activer-gravitationnel — ADOPTE GMAH AUGMENTE tapé par l'utilisateur le "
               r"[0-9]{4}-[0-9]{2}-[0-9]{2}"),
)
VERDICTS_NEUTRES = re.compile(r"(accepte|reporte)\b")  # closed allowlist on _texte; Unicode \b (8.22)
TAILLE_MAX_ARBITRAGES = 2 * 1024 * 1024  # bytes; refused before json.loads (story 8.22)
PLAFOND_BRAS = 4  # standard cap (kept: other code reads this name)
PLAFOND_STANDARD = PLAFOND_BRAS
PLAFOND_AUGMENTE = 10  # only after the typed phrase ADOPTE GMAH AUGMENTE (story 14.16)
RHO_MAX = 10.0
DEFAUT_ETAT = {"actif": False, "depuis": None, "par": None, "plafond_bras": PLAFOND_BRAS,
               "arbitrage": None, "mode": "standard"}
POIDS_DEFAUT = {"w_attr": 1.0, "kappa": 1.0, "eps": 0.05, "rho": 1.0,
                "cout": 0.15, "tokens": 0.15, "latence": 0.1, "charge": 0.2,
                "risque": 0.25, "contexte": 0.1}
PENALITES = ("cout", "tokens", "latence", "charge", "risque", "contexte")


PLANCHER_SCORE = 0.0  # abstention: a candidate scoring below this is never returned as a choice
EPS_MIN = 1e-9  # floor of eps: (eps + distance) ** rho must not blow up
W_ATTR_MIN, W_ATTR_MAX = 0.0, 100.0  # engineering safety bound (2026-10-25); w_attr = 0 is allowed
POIDS_MAX = 100.0  # upper bound of every other weight
KAPPA_MIN, KAPPA_MAX = 1e-9, 1e6
POIDS_PLANCHER = {"cout": 0.01, "risque": 0.01}  # cost and risk penalties can never be switched off
CHAMPS_SCORE = ("priorite", "fit", "preuve", "reuse", "distance") + PENALITES
_CHEMIN_ABSOLU_RE = re.compile(r"^(/|[a-z]:)")
_MODELE_RE = re.compile(r"[a-z0-9._-]+")


def _clip(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(x):
        return 0.0
    return min(1.0, max(0.0, x))


def _fini(x):
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _texte(x) -> str:
    """NFKC + format characters (category Cf, e.g. zero-width space) removed + casefold + strip."""
    s = unicodedata.normalize("NFKC", str(x))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    return s.casefold().strip()


def _collection_de_str(brut, nom: str) -> list:
    if not brut and not isinstance(brut, (int, float)):
        return []  # None, '', (), [], {} : nothing declared
    if isinstance(brut, (str, bytes)):
        raise ValueError(f"{nom} must be a collection, not a bare string")
    try:
        items = list(iter(brut))
    except TypeError:
        raise ValueError(f"{nom} must be a collection") from None
    for m in items:
        if not isinstance(m, str):
            raise ValueError(f"{nom} entries must be str, got {m!r}")
    return items


def _interdits(contexte: dict) -> set:
    return {_texte(m) for m in _collection_de_str(contexte.get("modeles_interdits"),
                                                  "modeles_interdits")}


def _norm_perimetre(x) -> str:
    """Lowercased, backslashes as slashes, posixpath.normpath; '' if empty."""
    s = _texte(x).replace("\\", "/")
    return posixpath.normpath(s) if s else ""


_TILDE_RE = re.compile(r"~\d")  # Windows 8.3 short names (PROGRA~1)


def _segment_suspect(seg: str) -> bool:
    """Windows aliasing of a path segment: trailing dot/space, 8.3 name, ':' (stream, drive),
    control character (NUL, tab, ...). Such a segment may name another directory than its text."""
    if seg in (".", "..", ""):
        return False
    return (seg[-1] in ". " or seg[0] == " " or ":" in seg or bool(_TILDE_RE.search(seg))
            or any(unicodedata.category(ch) in ("Cc", "Cf") for ch in seg))


def _hors_perimetre(n: str) -> bool:
    """Absolute path, residual '..' or suspect segment after normalisation: counts as TAKEN
    (fail closed)."""
    return (bool(_CHEMIN_ABSOLU_RE.match(n)) or n == ".." or n.startswith("../")
            or any(_segment_suspect(seg) for seg in n.split("/")))


def _chevauche(a: str, b: str) -> bool:
    """Equality or path-segment inclusion either way ('x/sub' under 'x', not 'xy').
    '.' is the root and overlaps everything; absolute or '..' paths overlap everything."""
    if a == "." or b == "." or _hors_perimetre(a) or _hors_perimetre(b):
        return True
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _valider_contexte(contexte) -> dict:
    if not contexte:
        return {}
    if not isinstance(contexte, dict):
        raise ValueError(f"contexte must be a dict, got {type(contexte).__name__}")
    return contexte


def _entree_non_finie(candidat: dict) -> bool:
    """A present score field that is not a finite real number: NaN, inf, 10**400, a bool, or a
    string (numeric or not: '0.9' is refused, the type must be int or float)."""
    for k in CHAMPS_SCORE:
        v = candidat.get(k)
        if v is None:
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool) or _fini(v) is None:
            return True  # bytes, bytearray, str, bool, NaN, inf, 10**400 ...
    return False


def _distance_negative(candidat: dict) -> bool:
    v = _fini(candidat.get("distance"))
    return v is not None and math.copysign(1.0, v) < 0


def _champ_negatif(candidat: dict) -> bool:
    """A finite score field below 0 (cout=-1.0, risque=-1, ...): out of range, not clipped to 0."""
    return any((v := _fini(candidat.get(k))) is not None and v < 0 for k in CHAMPS_SCORE)


def filtre_dur(candidat, contexte: dict | None, cap_mode: int = PLAFOND_STANDARD) -> str | None:
    """Typed exclusion reason, or None if eligible. Fails closed: unknown or malformed
    candidate fields are ineligible; a malformed context raises ValueError."""
    contexte = _valider_contexte(contexte)
    interdits = _interdits(contexte)
    pris = [n for n in (_norm_perimetre(q) for q in
                        _collection_de_str(contexte.get("perimetres_pris"), "perimetres_pris")) if n]
    if not isinstance(candidat, dict):
        return "candidat_invalide"
    modele = candidat.get("modele")
    if modele is None or modele == "":
        return "modele_absent"
    if not isinstance(modele, str):
        return "modele_invalide"
    modele = _texte(modele)
    if not modele:
        return "modele_absent"
    if not modele.isascii():
        return "modele_non_ascii"
    if not _MODELE_RE.fullmatch(modele) or modele.endswith("."):
        return "modele_invalide"
    if modele in interdits:
        return "modele_interdit"
    perim = candidat.get("perimetre")
    if perim is None or perim == "":
        return "perimetre_absent"
    if not isinstance(perim, str):
        return "perimetre_invalide"
    perim = _norm_perimetre(perim)
    if not perim:
        return "perimetre_absent"
    if _hors_perimetre(perim) or any(_chevauche(perim, q) for q in pris):
        return "perimetre_pris"
    brut_bras = contexte.get("bras_actifs")
    bras = None if isinstance(brut_bras, bool) else _fini(brut_bras)
    if bras is not None and (bras < 0 or bras != int(bras)):  # a fractional count is not a count
        return "plafond_bras"
    plafond = _fini(contexte.get("plafond_bras", cap_mode))
    if bras is None or plafond is None or bras >= min(plafond, cap_mode):
        return "plafond_bras"
    arb = candidat.get("requiert_arbitrage")
    if arb is not False:
        # only an explicit False is eligible; 0, '', None, absent are unknown, not 'no'
        return "arbitrage_r4" if arb else "requiert_arbitrage_absent"
    if _entree_non_finie(candidat):
        return "entree_non_finie"
    if _distance_negative(candidat) or _champ_negatif(candidat):
        return "entree_hors_plage"
    return None


def valider_poids(poids: dict | None) -> dict:
    if poids and not isinstance(poids, dict):
        raise ValueError(f"poids must be a dict, got {type(poids).__name__}")
    poids = poids or {}
    inconnus = sorted(str(k) for k in poids if k not in POIDS_DEFAUT)
    if inconnus:
        raise ValueError(f"unknown weight key(s): {inconnus}")
    p = {**POIDS_DEFAUT, **poids}
    for k, v in p.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"weight {k!r} must be a real number (not bool/str), got {v!r}")
        f = _fini(v)
        if f is None or f < 0:
            raise ValueError(f"weight {k!r} must be finite and >= 0, got {v!r}")
        p[k] = f
    if not KAPPA_MIN <= p["kappa"] <= KAPPA_MAX or p["eps"] < EPS_MIN:
        raise ValueError(f"kappa and eps out of bounds: kappa in [{KAPPA_MIN}, {KAPPA_MAX}], "
                         f"eps >= {EPS_MIN}")
    if not W_ATTR_MIN <= p["w_attr"] <= W_ATTR_MAX:
        raise ValueError(f"weight 'w_attr' must be in [{W_ATTR_MIN}, {W_ATTR_MAX}], got {p['w_attr']!r}")
    for k in PENALITES:
        bas = POIDS_PLANCHER.get(k, 0.0)
        if not bas <= p[k] <= POIDS_MAX:
            raise ValueError(f"weight {k!r} must be in [{bas}, {POIDS_MAX}], got {p[k]!r}")
    if not 0 <= p["rho"] <= RHO_MAX:
        raise ValueError(f"rho must be in [0, {RHO_MAX}], got {p['rho']!r}")
    return p


def score(candidat, contexte: dict | None = None, poids: dict | None = None,
          cap_mode: int = PLAFOND_STANDARD) -> dict:
    contexte = _valider_contexte(contexte)
    p = valider_poids(poids)
    # the 3rd argument only when the mode raised the cap (keeps the standard call shape unchanged)
    raison = (filtre_dur(candidat, contexte) if cap_mode == PLAFOND_STANDARD
              else filtre_dur(candidat, contexte, cap_mode))
    if raison:
        return {"eligible": False, "raison": raison, "total": None, "termes": {}}
    base = 1.0
    for k in ("priorite", "fit", "preuve", "reuse"):
        base *= _clip(candidat.get(k, 0))
    base /= (p["eps"] + _clip(candidat.get("distance", 1))) ** p["rho"]
    attraction = base / (p["kappa"] + base)
    termes = {"attraction": attraction}
    for k in PENALITES:
        v = _clip(candidat.get(k, 0))
        termes[k] = v * v if k == "charge" else v
    total = p["w_attr"] * attraction - sum(p[k] * termes[k] for k in PENALITES)
    if not math.isfinite(total):
        return {"eligible": False, "raison": "entree_non_finie", "total": None, "termes": {}}
    return {"eligible": True, "raison": None, "total": total, "termes": termes}


def plafond(mode) -> int:
    """Arm cap of a mode: 10 only for exactly `augmente`; 4 for `standard` and anything else (fail closed)."""
    return PLAFOND_AUGMENTE if mode == "augmente" else PLAFOND_STANDARD


def etat() -> dict:
    try:
        d = json.loads(ETAT.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return dict(DEFAUT_ETAT)
    if not isinstance(d, dict):
        return dict(DEFAUT_ETAT)
    e = {**DEFAUT_ETAT, **d}
    e["actif"] = e.get("actif") is True
    return e


def cle_arbitrage(entree) -> str | None:
    """Content key of an arbitrages.json entry (None if date/cible/decision are not all str)."""
    if not isinstance(entree, dict):
        return None
    date, cible, decision = entree.get("date"), entree.get("cible"), entree.get("decision")
    if not all(isinstance(x, str) for x in (date, cible, decision)):
        return None
    return f"{date}|{cible}|{hashlib.sha256(decision.encode('utf-8')).hexdigest()[:12]}"


def _decision_active(decision: str) -> bool:
    """Closed whitelist only (story 8.16): the decision must FULLY match one DECISIONS_ACTIVATION form."""
    return isinstance(decision, str) and any(r.fullmatch(decision) for r in DECISIONS_ACTIVATION)


def _date_refusee(date: str) -> str | None:
    """None if `date` is an ISO date (YYYY-MM-DD) not after today (UTC); else a typed reason."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
        return "date_invalide"
    try:
        jour = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return "date_invalide"
    return "date_future" if jour > datetime.now(UTC).date() else None


class _CleEnDouble(ValueError):
    pass


def _sans_doublon(paires):
    d = {}
    for k, v in paires:
        if k in d:
            raise _CleEnDouble(k)
        d[k] = v
    return d


def _jour(date):
    """Parsed ISO date (YYYY-MM-DD) or None; the future is allowed here (ordering only)."""
    if not isinstance(date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
        return None
    try:
        return datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return None


def _cible_norm(cible) -> str:
    """_texte + every whitespace removed + '-' and '_' mapped to ':' (story 8.22, round 2)."""
    t = "".join(_texte(cible).split())
    return t.replace("-", ":").replace("_", ":")


def _revoquee(liste, entree) -> bool:
    """Story 8.22 (review round 2): is the activation `entree` revoked by a LATER entry of `liste`?

    Later = a later parsed date, or the same date further down the list. An entry is in scope when
    its cible normalises (_cible_norm) to the active cible; a non-str cible counts as matching.
    FAIL CLOSED: an in-scope later entry revokes UNLESS its decision, after _texte, starts with a
    closed pure-ASCII allowlist of neutral/positive verdicts (VERDICTS_NEUTRES: accepte, reporte;
    a non-ASCII char in the first word, e.g. a homoglyph, means no match) or is an exact
    DECISIONS_ACTIVATION form. An in-scope entry with a missing or non-ISO date revokes. Hence ANY
    later refusal on hub:gmah, even on another subject, switches the mode off and requires a new
    ADOPTE GMAH: this is intended.
    """
    debut = _jour(entree["date"])
    cible = _cible_norm(entree["cible"])
    vue = False
    for autre in liste:
        if autre is entree:
            vue = True
            continue
        if not isinstance(autre, dict):
            continue
        c = autre.get("cible")
        if isinstance(c, str) and _cible_norm(c) != cible:
            continue
        jour = _jour(autre.get("date"))
        if jour is not None and debut is not None and (jour < debut or (jour == debut and not vue)):
            continue  # earlier entry: never revokes
        decision = autre.get("decision")
        if isinstance(decision, str) and (_decision_active(decision)
                                          or VERDICTS_NEUTRES.match(_texte(decision))):
            continue
        return True
    return False


def verifier_arbitrage(cle) -> tuple[bool, str]:
    """(ok, reason): is `cle` the exact content key of a valid activation arbitration? Fail closed."""
    if not isinstance(cle, str) or not cle:
        return False, "no arbitration key"
    try:
        with ARBITRAGES.open("rb") as f:
            octets = f.read(TAILLE_MAX_ARBITRAGES + 1)
        if len(octets) > TAILLE_MAX_ARBITRAGES:
            return False, f"arbitrages.json too large (> {TAILLE_MAX_ARBITRAGES} bytes)"
        brut = json.loads(octets.decode("utf-8"), object_pairs_hook=_sans_doublon)
    except _CleEnDouble as exc:
        return False, f"arbitrages.json has a duplicate key ({exc})"
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        return False, f"arbitrages.json unreadable ({type(exc).__name__})"
    liste = brut.get("arbitrages") if isinstance(brut, dict) else brut
    if not isinstance(liste, list):
        return False, "arbitrages.json has no list of entries"
    for entree in liste:
        if cle_arbitrage(entree) != cle:  # exact, case-sensitive, no prefix match
            continue
        if entree["cible"] != CIBLE_ARBITRAGE:
            return False, f"entry cible is not {CIBLE_ARBITRAGE}"
        raison_date = _date_refusee(entree["date"])
        if raison_date:
            return False, raison_date
        if not _decision_active(entree["decision"]):
            return False, f"entry decision is not an ACCEPTE of {MARQUEUR}"
        jour = re.search(r"le ([0-9]{4}-[0-9]{2}-[0-9]{2})$", entree["decision"])
        if jour and (raison_jour := _date_refusee(jour.group(1))):
            return False, raison_jour
        if _revoquee(liste, entree):
            return False, "revoque"
        return True, "ok"
    return False, "no matching arbitration entry"


def _arret() -> Path:
    """Stop marker written by `off` beside etat.json (story 8.22): its presence means off."""
    return ETAT.with_name("arret.flag")


def etat_verifie() -> tuple[bool, str]:
    """(active, reason): flag True, no stop marker, and the stored proof still validates."""
    try:
        if _arret().exists():
            return False, "arret"
    except OSError:
        return False, "arret illisible"
    e = etat()
    if e["actif"] is not True:
        return False, "off"
    ok, raison = verifier_arbitrage(e.get("arbitrage"))
    if not ok:
        return ok, raison
    emp = e.get("cle_empreinte")
    if isinstance(emp, str) and emp:  # key frozen at activation: missing or replaced -> off
        cle = _cle_lue()
        if cle is None:
            return False, "cle absente"
        if empreinte_cle(cle) != emp:
            return False, "cle remplacee"
    if not _etat_signe(e):
        return False, "etat non signe"  # a hand-written / edited etat.json opens nothing
    return True, raison


def mode_effectif() -> str:
    """Mode of the VERIFIED active state (the signature covers `mode`); `standard` otherwise."""
    ok, _ = etat_verifie()
    m = etat().get("mode")
    return m if ok and isinstance(m, str) else "standard"


def plafond_courant() -> int:
    """Arm cap in force now: plafond(mode_effectif()); 4 when off, unsigned or unreadable."""
    return plafond(mode_effectif())


def actif_verifie() -> bool:
    """True only if the flag is True AND its stored proof still validates (re-read every call)."""
    return etat_verifie()[0] is True


class Classement(list):
    """Chosen candidates (descending score, name as tie-break) + `.exclusions`: one
    {"nom", "raison"} per excluded or abstained candidate, so an empty result is explained.
    `.exclusions` is lost by slicing, sorted(), list() and `+` (they return plain lists)."""

    def __init__(self, choix=(), exclusions=()):
        super().__init__(choix)
        self.exclusions = list(exclusions)


def _nom(c) -> str:
    return str(c.get("nom", "")) if isinstance(c, dict) else ""


def _cle_stable(c) -> str:
    try:
        return json.dumps(c, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(c)


def classer(candidats: list, contexte: dict | None = None, poids: dict | None = None) -> list:
    """Mode off: input order unchanged. Mode on: eligible candidates at or above PLANCHER_SCORE by
    descending score, ties by name (independent of input order); the rest in `.exclusions`."""
    e = etat()
    if not actif_verifie():
        return Classement(candidats)
    ctx = dict(_valider_contexte(contexte))
    cap_malforme = "plafond_bras" in ctx and _fini(ctx["plafond_bras"]) is None
    valider_poids(poids)
    cap_mode = plafond_courant()  # state-bound cap (comparer never reads the state: it stays at 4)
    caps = [c for c in (_fini(e.get("plafond_bras")), _fini(ctx.get("plafond_bras", cap_mode))) if c is not None]
    ctx["plafond_bras"] = min(caps) if caps else None
    if cap_malforme:
        ctx["plafond_bras"] = None  # a malformed cap in the context blocks (fail closed)
    gardes, exclus = [], []
    for c in candidats:
        s = score(c, ctx, poids, cap_mode)
        if not s["eligible"]:
            exclus.append({"nom": _nom(c), "raison": s["raison"]})
        elif s["total"] < PLANCHER_SCORE:
            exclus.append({"nom": _nom(c), "raison": "sous_plancher"})
        else:
            gardes.append((c, s["total"]))
    gardes.sort(key=lambda cs: (-cs[1], _nom(cs[0]), _cle_stable(cs[0])))
    return Classement((c for c, _ in gardes), exclus)


def score_lineaire(candidat, contexte: dict | None = None, poids: dict | None = None) -> dict:
    """Linear witness (story 8.6): same hard filter (filtre_dur, reused), same POIDS_DEFAUT and
    same attraction as score(), but every penalty is linear (charge=0.9 counts 0.9, not 0.81)."""
    contexte = _valider_contexte(contexte)
    p = valider_poids(poids)
    raison = filtre_dur(candidat, contexte)
    if raison:
        return {"eligible": False, "raison": raison, "total": None, "termes": {}}
    termes = {"attraction": score(candidat, contexte, p)["termes"]["attraction"]}
    for k in PENALITES:
        termes[k] = _clip(candidat.get(k, 0))
    total = p["w_attr"] * termes["attraction"] - sum(p[k] * termes[k] for k in PENALITES)
    if not math.isfinite(total):
        return {"eligible": False, "raison": "entree_non_finie", "total": None, "termes": {}}
    return {"eligible": True, "raison": None, "total": total, "termes": termes}


def _ordre(candidats: list, contexte: dict, poids, scoreur) -> tuple[list, list]:
    gardes, sous = [], []
    for i, c in enumerate(candidats):
        s = scoreur(c, contexte, poids)
        if not s["eligible"]:
            continue
        if s["total"] < PLANCHER_SCORE:
            sous.append(_nom(c))
        else:
            gardes.append((-s["total"], _nom(c), i))
    gardes.sort()
    return [n for _, n, _ in gardes], sous


def comparer(candidats, contexte: dict | None = None, poids: dict | None = None) -> dict:
    """Read-only comparison (story 8.6): classic (input) order, gravitational order (score) and
    linear order (score_lineaire), plus `exclus` as [nom, raison] pairs from filtre_dur.
    Never reads nor writes etat.json / trace.jsonl: the output is the same mode on or off.
    PLAFOND GAP (documented, tested): the gravitational order here reads plafond_bras from the
    CONTEXT alone (default PLAFOND_BRAS), whereas classer() also applies the cap stored in the
    etat (min of both); a state cap lower than the context's is therefore ignored here.
    Malformed context, weights or a non-list `candidats` raise ValueError (typed, nothing written).
    Duplicates keep both entries (tie-break by name then input index); inputs are not modified."""
    if not isinstance(candidats, list):
        raise ValueError(f"candidats must be a list, got {type(candidats).__name__}")
    candidats = copy.deepcopy(candidats)
    ctx = copy.deepcopy(_valider_contexte(contexte))
    valider_poids(poids)
    exclus = [[_nom(c), r] for c in candidats if (r := filtre_dur(c, ctx))]
    grav, sous_g = _ordre(candidats, ctx, poids, score)
    lin, sous_l = _ordre(candidats, ctx, poids, score_lineaire)
    return {"classique": [_nom(c) for c in candidats], "gravitationnel": grav, "lineaire": lin,
            "exclus": exclus, "sous_plancher": {"gravitationnel": sous_g, "lineaire": sous_l},
            "plafond_source": "contexte"}


def _comparer_cli(args: list) -> int:
    if len(args) != 1:
        return _usage("usage: gravite.py comparer <file.json>")
    try:
        data = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        print(f"refused: cannot read {args[0]} ({type(exc).__name__})", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("refused: expected an object {candidats, contexte, poids}", file=sys.stderr)
        return 1
    try:
        r = comparer(data.get("candidats"), data.get("contexte"), data.get("poids"))
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(r, ensure_ascii=True, sort_keys=True))
    return 0


def _tracer(action: str, arbitrage) -> None:
    ligne = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "action": action,
             "arbitrage": arbitrage, "par": "cli"}
    TRACE.parent.mkdir(parents=True, exist_ok=True)
    with TRACE.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(ligne, ensure_ascii=False) + "\n")


def _ecrire(actif: bool, arbitrage=None, nonce: str = "", mode: str = "standard") -> None:
    """Atomic write (temp file in the same directory + os.replace).

    An ACTIVE state is HMAC-signed (key outside the repo, same key as the provenance tokens) over
    actif|arbitrage|depuis|plafond_bras|jeton (+ `mode` when not standard), `jeton` being the nonce of the consumed token: no
    key -> OSError (fail closed), so `on` is refused rather than writing an unverifiable state."""
    d = etat()
    d.update(actif=actif, depuis=datetime.now(UTC).isoformat(timespec="seconds"),
             par="cli", arbitrage=arbitrage if actif else None,
             jeton=(nonce or "") if actif else None, sig_etat=None, cle_empreinte=None,
             mode=mode if actif else "standard", plafond_bras=plafond(mode) if actif else PLAFOND_STANDARD)
    if actif:
        cle = _cle_lue()
        if cle is None:
            raise OSError("no HMAC key available (state cannot be signed)")
        d["cle_empreinte"] = empreinte_cle(cle)  # key freeze: fingerprint only, signed with the state
        d["sig_etat"] = signature_etat(cle, d)
    ETAT.parent.mkdir(parents=True, exist_ok=True)
    tmp = ETAT.with_name(ETAT.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, ETAT)
    finally:
        if tmp.exists():
            tmp.unlink()


def _restaurer(avant) -> bool:
    """Put the state file back as it was (None = it did not exist). True iff it worked.

    On failure the stop marker is written (fail safe: the mode is off whatever etat.json says)
    and False is returned so the caller never claims a rollback that did not happen."""
    tmp = ETAT.with_name(ETAT.name + f".{os.getpid()}.rb")
    try:
        if avant is None:
            ETAT.unlink(missing_ok=True)
        else:
            tmp.write_bytes(avant)
            os.replace(tmp, ETAT)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            _arret().write_text("off" + chr(10), encoding="utf-8")
        except OSError:
            pass
        return False


JETON_TTL_S = 15 * 60  # a provenance token is fresh for 15 minutes after the typed prompt


def _jetons_chemin() -> Path:
    """Token log next to the state file (follows ETAT, so a redirected ETAT redirects it too)."""
    return ETAT.parent / "jetons.jsonl"


def _lire_jetons() -> list:
    try:
        lignes = _jetons_chemin().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ligne in lignes:
        try:
            d = json.loads(ligne)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


VERROU_JETONS_PERIME_S = 30.0
VERROU_JETONS_ATTENTE_S = 10.0


def sous_dossier_cle() -> str:
    """Key folder name: `claude-gmah` at the hub (story 8.21), `claude-gmah-<project slug>` in a
    target (story 14.12), so a target never shares the hub's key. The hub is recognised by the
    token-issuer hook `.claude/hooks/provenance_gmah.py`, which is never exported."""
    claude = next((a for a in Path(__file__).resolve().parents if a.name == ".claude"), None)
    if claude is None or (claude / "hooks" / "provenance_gmah.py").is_file():
        return "claude-gmah"
    slug = "".join(c if c.isalnum() else "-" for c in claude.parent.name.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return "claude-gmah-" + (slug[:40] or "projet")


def cle_hmac_chemin() -> Path | None:
    """HMAC key OUTSIDE the repo: <APPDATA>/claude-gmah/jeton.key (created by the hook).

    No implicit fallback (no home dir): base = env GMAH_KEY_DIR (tests/CI) if set, else APPDATA.
    Fail closed (None): base unset/empty, not absolute (a relative one would land under the current
    directory) or inside the protected `.claude` tree. KEEP IN SYNC with hooks/provenance_gmah.py.

    Honest limit: a process of the same Windows user can still read the key file; this raises the
    bar and makes tampering detectable (key fingerprint frozen in the signed state). On Windows the file is a DPAPI blob
    (story 14.11) but DPAPI does not stop a process of the same account/session."""
    try:
        brut = os.environ.get("GMAH_KEY_DIR") or os.environ.get("APPDATA")
        if not brut:
            return None
        base = Path(brut)
        if not base.is_absolute():
            return None
        p = (base / sous_dossier_cle() / "jeton.key").resolve()
        protege = next((a for a in Path(__file__).resolve().parents if a.name == ".claude"), None)
        if protege is not None and protege in p.parents:
            return None
        return p
    except (RuntimeError, OSError, ValueError, KeyError):
        return None


def empreinte_cle(cle: bytes) -> str:
    """SHA-256 fingerprint of the key (the key itself is never stored)."""
    return hashlib.sha256(cle).hexdigest()


def _cle_lue():
    """The HMAC key bytes, or None (never created here: only the hook mints it)."""
    p = cle_hmac_chemin()
    if p is None:
        return None
    try:
        return _cle.lire(p) or None
    except OSError:  # absent, corrupted blob, other account, legacy plaintext on Windows: fail closed
        return None


def _cle_raison() -> str | None:
    """Why the key is unusable (None if it is usable); used by `on`/`status` to say it (story 14.11)."""
    p = cle_hmac_chemin()
    if p is None:
        return None  # unsafe location: the later checks refuse with their own message
    try:
        _cle.lire(p)
        return None
    except FileNotFoundError:
        return None  # absent key: the token check below refuses with its own message
    except OSError as exc:
        return f"key unusable ({exc})"


def signature_etat(cle_hmac: bytes, e: dict) -> str:
    msg = "|".join(["etat"] + [str(e.get(k)) for k in ("actif", "arbitrage", "depuis", "plafond_bras")]
                   + [str(e.get("jeton") or ""), str(e.get("cle_empreinte") or "")])
    if e.get("mode", "standard") != "standard":  # a state signed before `mode` existed stays valid (= standard)
        msg += "|mode=" + str(e.get("mode"))
    return hmac.new(cle_hmac, msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _etat_signe(e: dict) -> bool:
    """Signed AND bound to the freshest consumed token (anti-replay, review 2026-10-28).

    The nonce must be non-empty and equal the nonce _nonce_lie() returns now for the stored
    arbitrage. `off` appends a {"revoque": ...} record to jetons.jsonl and _nonce_lie() stops at
    it, so after any `off` a copy of an older signed etat.json no longer validates: only a new
    typed `ADOPTE GMAH` (a new consumed nonce, recorded after the revocation) can reactivate."""
    cle, sig = _cle_lue(), e.get("sig_etat")
    nonce = e.get("jeton")
    emp = e.get("cle_empreinte")
    return (cle is not None and isinstance(sig, str) and isinstance(nonce, str) and nonce != ""
            and isinstance(emp, str) and emp != "" and emp == empreinte_cle(cle)
            and nonce == _nonce_lie(str(e.get("arbitrage")))
            and hmac.compare_digest(sig, signature_etat(cle, e)))


def _nonce_lie(cle: str) -> str:
    """Nonce of the freshest token consumed (bound) for `cle`; '' if none is recorded or if a
    revocation (written by `off`) is more recent than the last consumption. The consomme/revoque
    lines are unsigned on purpose: forging one can only turn the mode off or burn a token (the
    state signature binds the real nonce), never switch it on; pinned by a test."""
    for d in reversed(_lire_jetons()):
        if d.get("revoque") is True:
            return ""
        if d.get("cle") == cle and isinstance(d.get("consomme"), str):
            return d["consomme"]
    return ""


def signature_jeton(cle_hmac: bytes, d: dict) -> str:
    """HMAC-SHA256 over ts|type|nonce|session (same formula as hooks/provenance_gmah.py)."""
    msg = "|".join(str(d.get(k, "")) for k in ("ts", "type", "nonce", "session"))
    return hmac.new(cle_hmac, msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _signe(cle_hmac, d: dict) -> bool:
    sig = d.get("sig")
    return (cle_hmac is not None and isinstance(sig, str)
            and hmac.compare_digest(sig, signature_jeton(cle_hmac, d)))


class _VerrouJetons:
    """Exclusive O_CREAT|O_EXCL lock around read+append of jetons.jsonl (plan_bras.py pattern,
    simplified: a lock older than VERROU_JETONS_PERIME_S is stale and removed)."""

    def __init__(self, chemin: Path):
        self.chemin = Path(str(chemin) + ".lock")
        self.jeton = f"{os.getpid()}-{os.urandom(8).hex()}"

    def __enter__(self):
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        fin = time.monotonic() + VERROU_JETONS_ATTENTE_S
        while True:
            try:
                fd = os.open(self.chemin, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    if time.time() - self.chemin.stat().st_mtime > VERROU_JETONS_PERIME_S:
                        self.chemin.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() > fin:
                    raise OSError("token lock busy") from None
                time.sleep(0.02)
                continue
            try:
                os.write(fd, self.jeton.encode("ascii"))
            finally:
                os.close(fd)
            return self

    def __exit__(self, *exc):
        try:
            if self.chemin.read_text(encoding="ascii") == self.jeton:
                self.chemin.unlink()
        except OSError:
            pass
        return False


def consommer_jeton(cle: str, maintenant: datetime | None = None, type_jeton: str = "adopte") -> tuple[bool, str]:
    """(ok, reason): consume the freshest unused, HMAC-signed ADOPTE token for `cle` (fail closed).

    Refused: no token, unsigned or badly signed line (ignored), token older than JETON_TTL_S or
    dated in the future, token already consumed for this key, or bound (consumed) to another key.
    Binding happens at consumption: the typed prompt cannot carry the key. Read + append run under
    an exclusive lock: two concurrent consumers of one token, exactly one wins."""
    try:
        with _VerrouJetons(_jetons_chemin()):
            return _consommer_sous_verrou(cle, maintenant or datetime.now(UTC), type_jeton)
    except OSError as exc:
        return False, f"cannot consume provenance token ({exc})"


def _consommer_sous_verrou(cle: str, maintenant: datetime, type_jeton: str = "adopte") -> tuple[bool, str]:
    cle_hmac = _cle_lue()
    lignes = _lire_jetons()
    liees = {d["consomme"]: d.get("cle") for d in lignes if isinstance(d.get("consomme"), str)}
    raison = "no provenance token (type ADOPTE GMAH first)"
    for d in reversed(lignes):
        nonce, ts = d.get("nonce"), d.get("ts")
        if d.get("type") != type_jeton or not isinstance(nonce, str) or not isinstance(ts, str):
            continue
        if not _signe(cle_hmac, d):
            raison = "provenance token unsigned or bad signature"
            continue
        if nonce in liees:
            raison = ("provenance token already consumed" if liees[nonce] == cle
                      else "provenance token bound to another key")
            continue
        try:
            emis = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if emis.tzinfo is None:
            continue
        age = (maintenant - emis).total_seconds()
        if age < 0 or age > JETON_TTL_S:
            raison = "provenance token expired"
            continue
        ligne = {"consomme": nonce, "cle": cle, "ts": maintenant.isoformat(timespec="seconds")}
        try:
            with _jetons_chemin().open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        except OSError as exc:
            return False, f"cannot consume provenance token ({exc})"
        return True, "ok"
    return False, raison


def _usage(msg: str = "usage: gravite.py on --arbitrage <key> | off | status") -> int:
    print(msg, file=sys.stderr)
    return 2


def _cle_demandee(args: list):
    """Exactly one `--arbitrage KEY` or `--arbitrage=KEY`, non-empty; anything else -> None."""
    if len(args) == 2 and args[0] == "--arbitrage":
        cle = args[1]
    elif len(args) == 1 and args[0].startswith("--arbitrage="):
        cle = args[0][len("--arbitrage="):]
    else:
        return None
    return cle or None


def _extraire_mode(args: list):
    """(mode, remaining args) for an optional trailing `--mode standard|augmente`; None if malformed."""
    if "--mode" not in args and not any(a.startswith("--mode=") for a in args):
        return "standard", args
    if len(args) >= 2 and args[-2] == "--mode":
        mode, reste = args[-1], args[:-2]
    elif args and args[-1].startswith("--mode="):
        mode, reste = args[-1][len("--mode="):], args[:-1]
    else:
        return None
    return (mode, reste) if mode in ("standard", "augmente") else None


def _revoquer() -> None:
    """Append the revocation record (called by `off` under the state lock): from now on no
    earlier nonce validates a signed state (replay protection, see _etat_signe)."""
    ligne = {"revoque": True, "ts": datetime.now(UTC).isoformat(timespec="seconds")}
    _jetons_chemin().parent.mkdir(parents=True, exist_ok=True)
    with _jetons_chemin().open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(ligne) + "\n")


def _activer(cle: str, mode: str = "standard") -> int:
    """Critical section of `on` (called under the state lock): 0 = activated, 1 = refused."""
    try:
        _arret().unlink(missing_ok=True)
    except OSError as exc:
        print(f"refused: cannot remove the stop marker ({exc})", file=sys.stderr)
        return 1
    nonce = _nonce_lie(cle)
    if not nonce:
        print("refused: no consumed provenance token (empty nonce)", file=sys.stderr)
        return 1
    try:
        avant = ETAT.read_bytes() if ETAT.exists() else None
    except OSError:
        avant = None
    try:
        _ecrire(True, cle, nonce, mode)
    except OSError as exc:
        print(f"refused: cannot persist activation ({exc})", file=sys.stderr)
        return 1
    try:
        _tracer("on", cle)  # state first, then the trace: the journal never says 'on' for nothing
    except OSError as exc:
        if _restaurer(avant):  # no activation without its trace
            print(f"refused: trace not written, activation rolled back ({exc})", file=sys.stderr)
        else:
            print(f"refused: trace not written and rollback FAILED ({exc}); stop marker written, "
                  "mode is off", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    args = list(argv[1:])
    if cmd == "comparer":
        return _comparer_cli(args)
    if cmd == "migrer-cle":  # story 14.11: wrap the plaintext key in DPAPI, same bytes
        p = cle_hmac_chemin()
        if args or p is None:
            return _usage("usage: gravite.py migrer-cle (no argument; needs a safe key location)")
        try:
            print(f"migrer-cle: {_cle.migrer(p)}")
        except OSError as exc:
            print(f"refused: migration failed, original untouched ({exc})", file=sys.stderr)
            return 1
        return 0
    if cmd == "on":
        extrait = _extraire_mode(args)
        cle = _cle_demandee(extrait[1]) if extrait else None
        if cle is None:
            return _usage("usage: gravite.py on --arbitrage <key> (exactly one non-empty key)")
        mode = extrait[0]
        raison_cle = _cle_raison()
        if raison_cle:
            print(f"refused: {raison_cle}", file=sys.stderr)
            return 1
        ok, raison = verifier_arbitrage(cle)
        if ok:
            # story 8.21: typed-prompt provenance; `augmente` needs the token of the typed AUGMENTE phrase
            ok, raison = (consommer_jeton(cle) if mode == "standard"
                          else consommer_jeton(cle, type_jeton="adopte-augmente"))
        if not ok:
            print(f"refused: {raison}", file=sys.stderr)
            return 1
        try:
            with _VerrouJetons(ETAT.with_name("etat.json")):  # on/off serialised (no interleaving)
                code = _activer(cle, mode)
        except OSError as exc:
            print(f"refused: state lock unavailable ({exc})", file=sys.stderr)
            return 1
        if code:
            return code
    elif cmd == "off":
        if args:
            return _usage("usage: gravite.py off (no argument)")
        try:
            _arret().parent.mkdir(parents=True, exist_ok=True)
            _arret().write_text("off" + chr(10), encoding="utf-8")
            marqueur = "stop marker written, mode is off"
        except OSError as exc:
            marqueur = f"stop marker NOT written ({exc}), mode may still be on"
        try:
            with _VerrouJetons(ETAT.with_name("etat.json")):
                _revoquer()
                _ecrire(False)
                try:
                    _tracer("off", None)
                except OSError as exc:
                    print(f"warning: trace not written ({exc})", file=sys.stderr)
        except OSError as exc:  # the stop marker (written above, unlocked) keeps the mode off
            print(f"error: cannot persist deactivation ({exc}); {marqueur}", file=sys.stderr)
            return 1
    elif cmd == "status":
        if args:
            return _usage("usage: gravite.py status (no argument)")
        raison_cle = _cle_raison()
        if raison_cle:
            print(f"gravite: warning: {raison_cle}", file=sys.stderr)
        else:
            pc = cle_hmac_chemin()
            if pc is not None and _cle.est_clair(pc):
                print("gravite: warning: cle en clair non migree: lancer gravite.py migrer-cle",
                      file=sys.stderr)
    else:
        return _usage()
    e = etat()
    if e["actif"] and not actif_verifie():
        print(f"gravite: off (flag on, proof invalid) (plafond_bras={e['plafond_bras']}, depuis={e['depuis']})")
        return 0
    print(f"gravite: {'on' if e['actif'] else 'off'} (plafond_bras={e['plafond_bras']}, depuis={e['depuis']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
