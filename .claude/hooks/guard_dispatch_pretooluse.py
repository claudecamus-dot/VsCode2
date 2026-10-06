"""PreToolUse (Bash|PowerShell, Agent, Edit|Write): one process per matcher for its guards
(P2-b, 2026-09-30; Agent and Edit|Write since 2026-10-02, stdin bound 5 -> 15 s).

Why: six separate ``py`` processes per shell command (measured 2026-09-30:
1.9-6 s per start-up each, ``py -c pass`` alone ~4 s on this machine) were
followed by bursts of « stdin non recu en 5 s — refus prudent » (254 in one day).
This dispatcher starts ONE interpreter, reads stdin ONCE with the shared bounded
reader, then runs each guard's own ``main()`` in-process, in the same order as
the former settings.json entries, feeding it the payload through the
``_FLUX_STDIN`` hook every guard already honours. Guards stay untouched and
keep working standalone.

Output contract:
- no usable stdin, or not a JSON object -> refusal, exit 2 (fail-closed, as each guard);
- EVERY guard runs (as the former separate entries did); any guard that exits 2
  (its own prudent refusal) -> all stderr texts, exit 2;
- else any deny wins; its reason joins every deny and ask reason in guard
  order (review 2026-10-02: the first deny alone dropped the others' messages);
- else a guard answering ``ask`` is kept;
- reminders (systemMessage, additionalContext) are always merged, joined by a
  blank line; ``updatedInput`` cannot be merged -> refusal, exit 2;
- a guard that crashes INSIDE main() is skipped and journaled;
- a guard that cannot be LOADED (missing file, SyntaxError, ImportError, even a
  SystemExit at import) -> refusal, exit 2: an absent guard is not a guard that
  let the command through (standalone, a missing file was `py` exit 2 too);
- the whole chain runs under a time budget (BUDGET_S) below the harness timeout
  (130 s in settings.json): past it -> refusal, exit 2. A harness kill would
  let the command through (review 2026-10-01: 1 MB payload, ~25 s per guard).

Sévérité assumée (revue 2026-10-02) : seule, une garde qui plante à l'import ou
qui n'est pas chargeable sortait en code 1, non bloquant ; via ce dispatcher,
c'est un refus (exit 2). Le dispatcher est donc PLUS strict que les entrées
séparées qu'il remplace, et c'est voulu : une garde absente ne doit jamais
laisser passer un appel sans contrôle. Une garde qui plante DANS main() reste
ignorée (journalisée, motif ``crash``), comme avant — SAUF les gardes CRITIQUES
(``CRITIQUES``, aujourd'hui guard_destructive_git) : leur plantage est un refus, exit 2
(compromis D, 2026-10-06).

Every refusal line in the log carries ``gardes``: the guards this run dispatches.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import threading

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

# Same order as the former PreToolUse "Bash|PowerShell" entries. settings.json
# passes the list as arguments (so the wiring still names every guard file);
# this default only serves a bare `py guard_dispatch_pretooluse.py`.
GARDES = (
    "guard_destructive_git",
    "warn_verif_before_commit",
    "guard_export_genere",
    "guard_service",
    "guard_registres_commit",
    "warn_commit_sans_ref",
)
DELAI = 15.0  # < every settings.json PreToolUse timeout (30 s minimum)
BUDGET_S = 100.0  # < settings.json timeout 130 s, margin for interpreter start-up
_GARDES_ACTIVES = GARDES  # set by main(); logged with every refusal
# Guards whose crash INSIDE main() is a refusal (exit 2), not a pass (compromis D,
# 2026-10-06): the destructive-git guard is the last line before irreversible loss, a
# payload crafted to crash it must not be a bypass. Other guards stay fail-open (their
# crash is journaled and shown). Extra names via CLAUDE_DISPATCH_CRITIQUES (tests).
CRITIQUES = ("guard_destructive_git",)


def _critiques():
    extra = tuple(x for x in os.environ.get("CLAUDE_DISPATCH_CRITIQUES", "").split(",") if x)
    return CRITIQUES + extra


def _budget():
    """BUDGET_S, lowered (never raised) by CLAUDE_DISPATCH_BUDGET_S (tests)."""
    try:
        return min(BUDGET_S, float(os.environ.get("CLAUDE_DISPATCH_BUDGET_S", BUDGET_S)))
    except ValueError:
        return BUDGET_S


# --- bounded stdin read (anthropics/claude-code#87289) -------------------------
try:
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    from _stdin_borne import lire_stdin_borne as _lsb
except Exception:  # noqa: BLE001 - exported without the helper: still bounded
    def _lsb(delai=15.0, flux=None):
        import threading
        f = flux if flux is not None else sys.stdin
        boite = {}

        def _c():
            try:
                boite["v"] = f.read()
            except BaseException:  # noqa: BLE001
                boite["v"] = None
        t = threading.Thread(target=_c, daemon=True)
        t.start()
        t.join(delai)
        return None if t.is_alive() else boite.get("v")


def _ecrire_journal_stdin(champs, cap=1_000_000, suffixe="", rotation=True):
    """Shared writer of ``<hooks>/../supervision/refus_stdin<suffixe>.jsonl``
    (``CLAUDE_REFUS_STDIN_LOG`` redirects it, tests). Past ``cap`` the file
    ROTATES to ``<file>.1``: a hard stop silently dropped every line once the hub
    log reached 1 MB (review 2026-10-02). ``rotation=False`` keeps a hard stop
    (dispatcher crash lines). Never raises, never changes the exit."""
    try:
        _os = __import__("os")
        _dt = __import__("datetime")
        chemin = _os.environ.get("CLAUDE_REFUS_STDIN_LOG") or _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "..", "supervision", "refus_stdin.jsonl")
        if suffixe:
            chemin = _os.path.splitext(chemin)[0] + suffixe + ".jsonl"
        if _os.path.exists(chemin) and _os.path.getsize(chemin) > cap:
            if not rotation:
                return
            _os.replace(chemin, chemin + ".1")
        ligne = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                 "hook": _os.path.splitext(_os.path.basename(__file__))[0]}
        if globals().get("_GARDES_ACTIVES") is not None:
            ligne["gardes"] = list(globals()["_GARDES_ACTIVES"])
        ligne.update(champs)
        with open(chemin, "a", encoding="utf-8") as fh:
            fh.write(__import__("json").dumps(ligne) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _journal_attente(attente, delai):
    """A payload that arrived after > 2 s still passes, but leaves one line
    (motif ``attente``, ``issue: passe``) in its OWN file
    (``refus_stdin_attente.jsonl``, 500 KB then rotation): slow-but-served
    calls can never push the refusals out of the refusal journal."""
    _ecrire_journal_stdin({"motif": "attente", "issue": "passe", "delai_s": float(delai),
                           "attente_s": attente}, 500_000, "_attente")


def _stdin_borne(delai=15.0):
    """Bounded stdin read: the payload, or None on timeout/error — the hook decides
    (guard: fail-closed refusal; reminder: its existing fail-open path).
    A hook may set a module-level ``_FLUX_STDIN`` (e.g. a raw fd 0 reader).
    ``CLAUDE_STDIN_DELAI_S`` lowers the bound, never raises it (tests). The real
    wait is kept in ``_ATTENTE_S`` and journaled when a payload took > 2 s."""
    _tm = __import__("time")
    try:
        delai = min(delai, float(__import__("os").environ.get("CLAUDE_STDIN_DELAI_S", delai)))
    except ValueError:
        pass
    globals()["_DELAI_S"] = delai
    t0 = _tm.monotonic()
    v = _lsb(delai, globals().get("_FLUX_STDIN"))
    attente = round(_tm.monotonic() - t0, 3)
    globals()["_ATTENTE_S"] = attente
    if v is not None and attente > 2.0:
        globals()["_journal_attente"](attente, delai)
    return v


def _stdin_ou_refus(delai=15.0):
    """Guard hook: no stdin within the bound is a prudent refusal (fail-closed).

    Exit 2 blocks the tool call and shows stderr to Claude. ``os._exit`` skips
    interpreter shutdown, which can crash (0xC0000005) while the reader thread
    is still blocked — a crash code other than 2 would be a silent fail-open.
    """
    v = _stdin_borne(delai)
    delai = globals().get("_DELAI_S", delai)
    if v is None:
        _refus_prudent(f"stdin non recu en {delai:g} s", "delai", delai)
    return v

def _journal_refus(motif, delai):
    """One JSON line per refusal (review 4, Dana: measure before tuning the
    bound), with the measured wait ``attente_s``; 1 MB then rotation."""
    _ecrire_journal_stdin({"motif": motif, "delai_s": float(delai),
                           "attente_s": globals().get("_ATTENTE_S")})

def _refus_prudent(cause, motif, delai=15.0):
    _os = __import__("os")
    nom = _os.path.splitext(_os.path.basename(__file__))[0]
    try:  # UTF-8 bytes on fd 2: the harness reads UTF-8, a cp1252 dash is mojibake
        _journal_refus(motif, delai)
        _os.write(2, f"{nom}: {cause} — refus prudent, relancer la commande\n".encode())
    finally:
        _os._exit(2)

def _json_ou_refus(delai=15.0):
    """Guard hook: an empty, non-JSON or non-object payload is refused too
    (review 4, Vex) — only a VALID payload reaches the guard's own fail-open."""
    brut = _stdin_ou_refus(delai)
    delai = globals().get("_DELAI_S", delai)
    try:
        if isinstance(brut, bytes):
            brut = brut.decode("utf-8", "replace")
        data = __import__("json").loads(brut)
    except Exception:  # noqa: BLE001
        data = None
    if not isinstance(data, dict):
        _refus_prudent("entree illisible", "illisible", delai)
    return data


# --- the block above is shared byte-for-byte with every guard
# (tests/test_hooks_stdin_borne.py); its writer adds ``gardes`` because this
# module defines _GARDES_ACTIVES. Crash lines keep a hard stop (no rotation).
CAP_CRASH = 500_000
_PLANTEES = []  # guards that crashed during the current dispatch (visible message)


def _lire_payload():
    """Fail-closed like each guard (same helper, same messages), then hand the
    guards the payload re-serialised: they only ever json.loads it."""
    return json.dumps(_json_ou_refus(DELAI))


def _charger(nom):
    spec = importlib.util.spec_from_file_location(nom, os.path.join(ICI, nom + ".py"))
    if spec is None or spec.loader is None:
        raise ImportError(f"no loader for {nom}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _journal_crash(nom, exc, brut):
    """A guard's main() raised: the command still passes, but the crash leaves a
    line in the refusal journal (motif ``crash``, ``issue: passe``) with the
    guard, the exception type and a short sha256 of the command, never its
    text. Capped at CAP_CRASH (half the refusals' cap): a guard crashing on
    every call must not fill the shared journal and silence real refusals."""
    try:
        data = json.loads(brut)
        cmd = (data.get("tool_input") or {}).get("command")
        sha = (hashlib.sha256(cmd.encode("utf-8", "replace")).hexdigest()[:12]
               if isinstance(cmd, str) else None)
        outil = str(data.get("tool_name"))[:40]
    except Exception:  # noqa: BLE001
        sha, outil = None, None
    # the exception MESSAGE is deliberately not journaled: it may quote the command
    _ecrire_journal_stdin({"motif": "crash", "issue": "passe", "garde": nom,
                           "exc": type(exc).__name__, "cmd_sha": sha, "outil": outil},
                          CAP_CRASH, rotation=False)


def _forme_ou_refus(brut):
    """Refuse (exit 2, motif ``forme``) a payload whose types the guards cannot
    read: ``tool_input`` present but not an object, or ``command`` present but
    not a string. A guard crashing on such a payload would let it pass."""
    ti = json.loads(brut).get("tool_input")
    if ti is None:
        return
    if not isinstance(ti, dict):
        _refus_prudent(f"tool_input de type {type(ti).__name__}", "forme", DELAI)
    if "command" in ti and not isinstance(ti["command"], str):
        _refus_prudent(f"command de type {type(ti['command']).__name__}", "forme", DELAI)


def _executer(nom, brut):
    """Run one guard's main() in-process -> (exit_code, stdout, stderr).

    A load failure of any kind (SystemExit at import included) is a prudent
    refusal, exit 2 — never a skipped guard."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        # import-time prints go to a throwaway buffer: mixed into `out` they
        # would make the guard's JSON unparsable and silently drop a deny
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            mod = _charger(nom)
    except KeyboardInterrupt:
        raise
    except BaseException as e:  # noqa: BLE001 - fail-closed on an absent guard
        _refus_prudent(f"garde {nom} non chargeable ({type(e).__name__})",
                       "chargement", DELAI)
    try:
        mod._FLUX_STDIN = io.StringIO(brut)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                ret = mod.main([]) if nom == "warn_commit_sans_ref" else mod.main()
                code = ret if isinstance(ret, int) else 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception as e:  # noqa: BLE001 - a crashing guard never blocked
        err.write(f"{nom}: erreur interne ignoree ({type(e).__name__})"
                  " - commande NON controlee par cette garde\n")
        try:  # visibility must never turn into a second crash or a block
            _journal_crash(nom, e, brut)
            _PLANTEES.append(f"{nom} ({type(e).__name__})")
        except Exception:  # noqa: BLE001
            pass
        code = 1
        if nom in _critiques():
            err.write(f"{nom}: garde critique plantee ({type(e).__name__}) - "
                      "refus prudent, la commande n'a pas pu etre controlee. "
                      "Diagnostic : .claude/supervision/refus_stdin.jsonl (motif crash). "
                      "Sortie si le plantage se repete : retirer "
                      f"{nom}.py des arguments du dispatcher (PreToolUse) dans "
                      ".claude/settings.json, a la main, puis la corriger\n")
            code = 2
    return code, out.getvalue(), err.getvalue()


def _est_deny(obj):
    return isinstance(obj, dict) and (obj.get("hookSpecificOutput") or {}).get(
        "permissionDecision") == "deny"


def main() -> int:
    global _GARDES_ACTIVES
    gardes = tuple(a[:-3] if a.endswith(".py") else a for a in sys.argv[1:]) or GARDES
    _GARDES_ACTIVES = gardes
    brut = _lire_payload()
    budget = _budget()
    boite = {}

    def _chaine():
        try:
            boite["rc"] = _dispatcher(gardes, brut)
        except BaseException as e:  # noqa: BLE001 - fail-closed below
            boite["exc"] = e
    t = threading.Thread(target=_chaine, daemon=True)
    t.start()
    t.join(budget)
    if t.is_alive():
        _refus_prudent(f"gardes non terminees en {budget:g} s", "budget", budget)
    if "exc" in boite:
        _refus_prudent(f"erreur du dispatcher ({type(boite['exc']).__name__})",
                       "interne", DELAI)
    return boite["rc"]


def _err(texte):
    """UTF-8 bytes on fd 2 (the harness reads UTF-8; sys.stderr is cp1252 here)."""
    if texte:
        os.write(2, texte.encode("utf-8", "replace"))


def _dispatcher(gardes, brut) -> int:
    """Every guard runs, as the former separate settings.json entries did, so no
    guard's message is lost (review 2026-10-02). Verdict: any exit 2 -> exit 2
    (all refusal texts on stderr); else any deny -> deny whose reason joins every
    deny and ask reason in guard order; else the first ask; reminders
    (systemMessage, additionalContext) are always merged into the output."""
    messages, contextes, stderr_cumul = [], [], []
    raisons, deny, ask, refus = [], None, None, False
    _forme_ou_refus(brut)
    del _PLANTEES[:]
    for nom in gardes:
        code, out, err = _executer(nom, brut)
        if err:
            stderr_cumul.append(err)
        if code == 2:
            refus = True
            continue
        texte = out.strip()
        if not texte:
            continue
        try:
            obj = json.loads(texte)
        except Exception:  # noqa: BLE001 - plain text: debug-only, as before
            continue
        if not isinstance(obj, dict):
            continue
        hso = obj.get("hookSpecificOutput") or {}
        if "updatedInput" in hso:
            _err("".join(stderr_cumul) + f"{nom}: updatedInput non fusionnable entre gardes"
                 " — refus prudent, relancer la commande\n")
            return 2
        decision = hso.get("permissionDecision")
        if decision in ("deny", "ask"):
            if hso.get("permissionDecisionReason"):
                raisons.append(hso["permissionDecisionReason"])
            if decision == "deny" and deny is None:
                deny = obj
            if decision == "ask" and ask is None:
                ask = obj
        if obj.get("systemMessage"):
            messages.append(obj["systemMessage"])
        if hso.get("additionalContext"):
            contextes.append(hso["additionalContext"])
    if refus:
        _err("".join(stderr_cumul) + "".join(r + "\n" for r in raisons))
        return 2
    if _PLANTEES:  # same decision, but the user is told right now
        messages.append("Garde(s) plantee(s): " + ", ".join(_PLANTEES)
                        + " - la commande n'a PAS ete controlee par elle.")
    _err("".join(stderr_cumul))
    sortie = deny or ask
    if sortie is not None:
        h = sortie["hookSpecificOutput"]
        if len(raisons) > 1:
            h["permissionDecisionReason"] = "\n\n".join(raisons)
    elif messages or contextes:
        sortie = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    else:
        return 0
    if messages:
        sortie["systemMessage"] = "\n\n".join(messages)
    if contextes:
        sortie["hookSpecificOutput"]["additionalContext"] = "\n\n".join(contextes)
    sys.stdout.write(json.dumps(sortie) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
