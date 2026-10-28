"""UserPromptSubmit: one process for the three per-prompt hooks (story 7.10, FR48).

Why: three separate ``py`` processes per prompt (1.9-6 s start-up each on this
machine, see guard_dispatch_pretooluse.py). This dispatcher starts ONE interpreter,
reads stdin ONCE (bounded), then runs each hook's own ``main()`` in-process, in the
order given on the command line (settings.json: orchestrator_gate, faucher_orphelins,
rappel_rotation_session), feeding it the payload through the ``_FLUX_STDIN`` hook the
stdin helper already honours. The hooks stay untouched and work standalone.

In-process, not subprocess: a subprocess per hook would bring back the three
interpreter start-ups this story removes. The hooks' ``os._exit`` calls sit under
``__main__`` only, so importing them is safe.

Contract (fail-open, as the three separate entries were): the dispatcher ALWAYS exits 0
and never blocks the prompt. A hook that cannot be loaded, raises, or exits non-zero is
skipped (one stderr line) and the others still run. Stdout of each hook is kept, in
order. ``--limite-min N`` is forwarded to faucher_orphelins.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import math
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

HOOKS = ("orchestrator_gate", "faucher_orphelins", "rappel_rotation_session", "provenance_gmah")
DELAI = 10.0
WATCHDOG_S = 40.0  # < settings.json timeout


def _lire_stdin():
    try:  # shared wrapper (tests/test_hooks_stdin_borne.py): bounded read + wait journal
        from _stdin_borne import lier_stdin as _lier
        from _stdin_borne import lire_stdin_borne  # the bounded reader behind _stdin_borne
        _stdin_borne, _j, _a = _lier(globals(), __file__)
        try:
            return _stdin_borne(DELAI)
        except Exception:  # noqa: BLE001 - binding broken: same helper, unbound
            return lire_stdin_borne(DELAI)
    except Exception:  # noqa: BLE001 - helper absent: plain read
        try:
            return sys.stdin.read()
        except Exception:  # noqa: BLE001
            return None


def _noms(argv):
    noms, saute = [], False
    for a in argv:
        if saute:
            saute = False
        elif a == "--limite-min":
            saute = True
        elif not a.startswith("--"):
            nom = a[:-3] if a.endswith(".py") else a
            # whitelist: only the known hooks, never a path (no separator, no "..")
            if nom in HOOKS and not any(c in nom for c in ("/", "\\", "..")):
                noms.append(nom)
            else:
                _err(f"guard_dispatch_userpromptsubmit: {a!r} hors liste, ignore\n")
    return tuple(noms) or HOOKS


def _err(texte):
    try:
        os.write(2, texte.encode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        pass


def _out(texte):
    """Emit one hook's stdout NOW (UTF-8 bytes on fd 1): a later watchdog kill must
    not lose what earlier hooks printed, and a cp1252 console must not drop it."""
    if texte:
        try:
            os.write(1, texte.encode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            pass


def _watchdog_s():
    """WATCHDOG_S, lowered (never raised) by CLAUDE_DISPATCH_UPS_WATCHDOG_S (tests)."""
    try:
        v = float(os.environ.get("CLAUDE_DISPATCH_UPS_WATCHDOG_S", WATCHDOG_S))
    except ValueError:
        return WATCHDOG_S
    if not math.isfinite(v) or v <= 0:  # 0/-1/nan/inf: ignored, never an instant kill
        return WATCHDOG_S
    return min(WATCHDOG_S, v)


def _executer(nom, brut, argv):
    """Run one hook -> its stdout ('' on any failure)."""
    out, err = io.StringIO(), io.StringIO()
    try:
        spec = importlib.util.spec_from_file_location(nom, os.path.join(ICI, nom + ".py"))
        mod = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            spec.loader.exec_module(mod)
        mod._FLUX_STDIN = io.StringIO(brut)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            # --limite-min only reaches modules exposing _limite_argv (faucher_orphelins).
            # Unchanged behaviour: taskkill children started by the faucheur may outlive
            # the dispatcher (os._exit at the end does not wait for them).
            if hasattr(mod, "_limite_argv"):
                code = mod.main(limite_min=mod._limite_argv(["x", *argv]))
            else:
                code = mod.main()
        if isinstance(code, int) and code != 0:
            err.write(f"{nom}: code {code} ignore\n")
    except BaseException as e:  # noqa: BLE001 - fail-open: never block the prompt
        if isinstance(e, KeyboardInterrupt):
            raise
        if isinstance(e, SystemExit) and e.code in (None, 0):
            return out.getvalue(), err.getvalue()
        err.write(f"{nom}: {type(e).__name__} ignore (hook saute)\n")
    return out.getvalue(), err.getvalue()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        from _stdin_borne import armer_chien_de_garde
        armer_chien_de_garde(_watchdog_s(), 0)
    except Exception:  # noqa: BLE001
        pass
    brut = _lire_stdin()
    if isinstance(brut, bytes):
        brut = brut.decode("utf-8", "replace")
    brut = brut or ""
    for nom in _noms(argv):
        o, e = _executer(nom, brut, argv)
        _out(o)  # flushed per hook, in order
        _err(e)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:  # noqa: BLE001
        code = 0
    os._exit(code)  # never wait for a reader thread or a faucher worker still running
