"""PostToolUse (Agent, Bash) + SubagentStop (TARGET variant, gmah-collecte-flotte): GMAH learning
collection. Exported by the kit and wired in settings.json, but it COLLECTS NOTHING unless the GMAH
mode is on AND verified in this project (gravite.actif_verifie(), re-read on every call). A project
that never typed `ADOPTE GMAH` writes nothing.

One JSON line per event in .claude/orchestration/gravite/collecte.jsonl, observed facts only:
ts, session_id, event, tool, mode, plafond_bras (read through gravite), subagent_type, description
signature (same rule as repetitions_actions.signature), bash_signature (program + at most 2 safe
tokens, never arguments that look like a path, URL, identifier or secret), bras_paralleles (null:
not derivable from one payload), duree_ms / statut / tokens (null when absent), bloc_fin_conforme
(closing STATUT/COMMIT lines present in the sub-agent answer, a boolean, never the text).
NEVER stored: prompts, descriptions, commands, outputs. Cap 1 MB, rotation to collecte.1.jsonl
(one generation). Fail-open: exit 0, nothing printed, whatever the payload. The payload fields of
this Claude Code version are NOT verified: every field is optional.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

ICI = Path(__file__).resolve().parent
ORCH = ICI.parent / "orchestration"
DOSSIER = ORCH / "gravite"
COLLECTE = DOSSIER / "collecte.jsonl"
ROTATION = DOSSIER / "collecte.1.jsonl"
TAILLE_MAX = 1024 * 1024

# Same normalisation as .claude/supervision/repetitions_actions.signature (kept in sync by a test).
_NUS = {"git", "ls", "cat", "grep", "dir", "echo", "head", "tail", "pwd"}
_REPRISE = re.compile(r"\b(retry|reprise|relance|rerun|nouvel essai|re-?essai)\b")
_BRUIT = re.compile(r"[a-z]:/\S*|\S*[/\\]\S*|\b[0-9a-f]{7,}\b|\d+")
_MOTS_VIDES = {"de", "du", "la", "le", "les", "des", "un", "une", "et", "pour", "a", "en"}

_SEPARATEURS = re.compile(r"&&|\|\||[;|\n]")
_PROGRAMME = re.compile(r"[a-z][a-z0-9_.+-]{0,24}")
_JETON_SUR = re.compile(r"-{0,2}[a-z][a-z0-9_-]{0,23}")
_SCRIPT = re.compile(r"[a-z][a-z0-9_-]{0,40}\.(py|ps1|sh|js|bat|cmd)")
_PREFIXES_SECRETS = ("sk-", "ghp_", "gho_", "github_pat", "xox", "akia", "asia", "glpat", "aiza", "eyj")
# Program names written as is; any other name is written `?` (a secret may sit in the name).
_PROGRAMMES_CONNUS = frozenset((
    "git py python python3 pytest node npm npx ruff gh ls cat grep rg cd echo curl ssh sed awk find "
    "mkdir rm cp mv sh bash powershell pwsh cmd docker pip uv make tar unzip head tail wc sort which "
    "test jq code claude dir pwd export").split())
_INTERPRETES = {"py", "python", "python3", "node"}
_MODULES_SURS = {"pytest", "py_compile"}
_DRAPEAUX_SURS = {"--check", "--help", "--solde", "--no-cov", "-q", "-x", "-v"}
_GIT = {c: frozenset() for c in ("status", "log", "diff", "commit", "add", "worktree", "merge",
                                 "cherry", "rev-list", "push", "pull", "fetch", "branch",
                                 "checkout", "switch", "show", "stash", "rebase", "tag")}
_SOUS_COMMANDES = {
    "git": _GIT, "pytest": {}, "ls": {}, "grep": {}, "rg": {}, "ruff": {"check": frozenset(), "format": frozenset()},
    "npm": {"test": frozenset(), "install": frozenset(), "ci": frozenset(),
            "run": frozenset({"build", "test", "lint", "dev", "start"})},
    "gh": {c: frozenset({"list", "view", "create", "status", "diff", "checks"})
           for c in ("pr", "issue", "run")} | {"api": frozenset()},
}
STDIN_MAX = 1024 * 1024
_BORNE = re.compile(r"[A-Za-z0-9_.:-]{1,40}")
_STATUT = re.compile(r"^\s*STATUT\s*:\s*\S", re.M)
_COMMIT = re.compile(r"^\s*COMMIT\s*:\s*\S", re.M)

try:
    sys.path.insert(0, str(ICI))
    from _stdin_borne import lire_stdin_borne as _lsb
except Exception:  # noqa: BLE001 - helper absent: no stdin, hook stays silent
    _lsb = None


class _FluxPlafonne:
    """Reads at most STDIN_MAX + 1 chars of a stream: an oversized payload is never read in full."""

    def __init__(self, flux):
        self._flux = flux

    def read(self):
        return self._flux.read(STDIN_MAX + 1)


def _ascii(t) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def signature_description(outil, genre, description):
    """repetitions_actions.signature, on fields only: `outil|genre|3 useful words` or None."""
    desc = _BRUIT.sub(" ", _ascii(str(description or "")))
    mots = [m for m in re.findall(r"[a-z_]+", desc) if m not in _MOTS_VIDES]
    if not mots or mots[0] in _NUS or _REPRISE.search(desc):
        return None
    return f"{outil}|{genre or ''}|{' '.join(mots[:3])}"


def _suspect(tok: str) -> bool:
    """Looks like an identifier or a secret: long hex run, 4+ digits, known key prefix."""
    return (bool(re.search(r"[0-9a-f]{7,}", tok) and re.search(r"\d", tok))
            or sum(c.isdigit() for c in tok) >= 4 or tok.startswith(_PREFIXES_SECRETS))


def signature_bash(commande):
    """Program + at most 2 safe tokens of the FIRST command segment, or None.
    Stops at the first token that could carry a path, URL, identifier or secret; a script path
    keeps only its file name (`py C:/x/scan.py` -> `py scan.py`)."""
    if not isinstance(commande, str):
        return None
    segment = next((s for s in _SEPARATEURS.split(commande) if s.strip()), "")
    brut = segment.split()
    while brut and "=" in brut[0]:
        valeur = brut.pop(0).split("=", 1)[1]  # VAR=value prefix: dropped, never logged
        if valeur[:1] in ("'", '"') and not (len(valeur) >= 2 and valeur.endswith(valeur[0])):
            return None  # quote opened and not closed in the token: a word of the value would leak
    jetons = [j.strip("'\"`") for j in brut]
    if not jetons:
        return None
    prog = re.split(r"[/\\]", jetons[0])[-1].lower()
    prog = prog[:-4] if prog.endswith(".exe") else prog
    if not _PROGRAMME.fullmatch(prog) or _suspect(prog):
        return None
    if prog not in _PROGRAMMES_CONNUS:
        return "?"
    garde = [prog]
    rest = jetons[1:]
    if not rest:
        return prog
    if prog in _INTERPRETES:
        nom = re.split(r"[/\\]", rest[0])[-1].lower()
        if prog != "node" and rest[0] == "-m":
            garde.append("-m")
            if len(rest) > 1 and rest[1] in _MODULES_SURS:
                garde.append(rest[1])
        elif ("://" not in rest[0] and ":" not in rest[0].replace(":\\", "").replace(":/", "")
                and _SCRIPT.fullmatch(nom) and not _suspect(nom)):
            garde.append(nom)
            if len(rest) > 1 and rest[1] in _DRAPEAUX_SURS:
                garde.append(rest[1])
        return " ".join(garde)
    sous = _SOUS_COMMANDES.get(prog)
    if sous is None:
        return prog  # unknown program: its name only, every argument may be a secret
    if prog == "pytest":
        if rest[0] in _DRAPEAUX_SURS:
            garde.append(rest[0])
        return " ".join(garde)
    if rest[0] in sous:
        garde.append(rest[0])
        if len(rest) > 1 and rest[1] in sous[rest[0]]:
            garde.append(rest[1])
    return " ".join(garde)


def _texte_reponse(rep):
    if isinstance(rep, str):
        return rep
    if isinstance(rep, dict):
        contenu = rep.get("content")
        if isinstance(contenu, str):
            return contenu
        if isinstance(contenu, list):
            return "\n".join(b.get("text", "") for b in contenu
                             if isinstance(b, dict) and isinstance(b.get("text"), str))
    return None


def _borne(v):
    """A short identifier-like string kept as is, anything else (free text) dropped."""
    if isinstance(v, str) and _BORNE.fullmatch(v) and not v.lower().startswith(_PREFIXES_SECRETS):
        return v  # a UUID stays (digits are fine here), a key-prefixed value does not
    return None


def _entier(*valeurs):
    for v in valeurs:
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            return v
    return None


def ligne(charge: dict, mode, plafond) -> dict | None:
    """The record for one payload, or None when the event is not collected."""
    event = charge.get("hook_event_name") if isinstance(charge.get("hook_event_name"), str) else None
    outil = charge.get("tool_name") if isinstance(charge.get("tool_name"), str) else None
    entree = charge.get("tool_input") if isinstance(charge.get("tool_input"), dict) else {}
    rep = charge.get("tool_response")
    repd = rep if isinstance(rep, dict) else {}
    sess = charge.get("session_id")
    rec = {"ts": datetime.now(UTC).isoformat(timespec="seconds"),
           "session_id": _borne(sess),
           "event": event, "tool": outil, "mode": mode, "plafond_bras": plafond,
           "subagent_type": None, "signature": None, "bash_signature": None,
           "bras_paralleles": None, "duree_ms": None, "statut": None, "tokens": None,
           "bloc_fin_conforme": None}
    if event == "SubagentStop" or (event is None and outil is None and "agent_type" in charge):
        genre = charge.get("agent_type") or charge.get("subagent_type")
        rec["subagent_type"] = _borne(genre)
        rec["duree_ms"] = _entier(charge.get("duration_ms"), charge.get("totalDurationMs"))
        rec["tokens"] = _entier(charge.get("total_tokens"), charge.get("totalTokens"))
        return rec
    if outil in ("Agent", "Task"):
        genre = entree.get("subagent_type")
        rec["subagent_type"] = _borne(genre)
        rec["signature"] = signature_description("Agent", rec["subagent_type"],
                                                 entree.get("description"))
        rec["duree_ms"] = _entier(repd.get("totalDurationMs"), repd.get("duration_ms"))
        rec["tokens"] = _entier(repd.get("totalTokens"), repd.get("total_tokens"))
        statut = repd.get("status")
        rec["statut"] = _borne(statut)
        texte = _texte_reponse(rep)
        if texte is not None:
            rec["bloc_fin_conforme"] = bool(_STATUT.search(texte) and _COMMIT.search(texte))
        return rec
    if outil == "Bash":
        rec["bash_signature"] = signature_bash(entree.get("command"))
        return rec
    return None


def _gravite():
    chemin = ORCH / "gravite.py"
    if not chemin.is_file():
        return None
    spec = importlib.util.spec_from_file_location("gravite_collecte", chemin)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ecrire(rec: dict) -> None:
    DOSSIER.mkdir(parents=True, exist_ok=True)
    try:
        if COLLECTE.stat().st_size >= TAILLE_MAX:
            os.replace(COLLECTE, ROTATION)  # one generation: the older one is overwritten
    except FileNotFoundError:
        pass
    with COLLECTE.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, ensure_ascii=True) + "\n")


def _executer() -> None:
    brut = _lsb(10.0, _FluxPlafonne(sys.stdin)) if _lsb else None
    if not brut or len(brut) > STDIN_MAX:
        return  # empty, timed out or over 1 MB: nothing written
    try:
        charge = json.loads(brut)
    except ValueError:
        return
    if not isinstance(charge, dict):
        return
    gr = _gravite()
    if gr is None or gr.actif_verifie() is not True:
        return  # GMAH off or unverified in this project: nothing collected
    rec = ligne(charge, gr.mode_effectif(), gr.plafond_courant())
    if rec is not None:
        ecrire(rec)


def main() -> int:
    sortie = io.StringIO()
    try:
        with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(sortie):
            _executer()
    except BaseException:  # noqa: BLE001 - fail-open: a collector never disturbs the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
