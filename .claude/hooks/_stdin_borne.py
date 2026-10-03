"""Bounded stdin read shared by the hub's Claude Code hooks.

Why: Claude Code does not enforce a hook's timeout while the hook is blocked
reading stdin (anthropics/claude-code#87289). A plain ``sys.stdin.read()`` on a
pipe that is never closed hangs forever and leaves an orphan python.exe.

``lire_stdin_borne`` reads the given stream in a daemon thread and gives up
after ``delai`` seconds. It never raises: ``None`` means "no usable stdin"
(timeout or error): a guard hook refuses (fail-closed, exit 2), a reminder
hook takes its existing fail-open path (revue n3, 2026-09-28).
"""
from __future__ import annotations

import sys
import threading

DELAI_DEFAUT = 15.0  # 5 s refused bursts under load (254/day, 2026-09-30)


def _lire_borne(lecture, delai):
    boite = {}

    def _cible():
        try:
            boite["v"] = lecture()
        except BaseException:  # noqa: BLE001 - never raise from the reader
            boite["v"] = None

    fil = threading.Thread(target=_cible, daemon=True)
    fil.start()
    fil.join(delai)
    if fil.is_alive():
        return None
    return boite.get("v")


def lire_stdin_borne(delai: float = DELAI_DEFAUT, flux=None):
    """Return the text of ``flux`` (default: ``sys.stdin`` at call time), or None."""
    try:
        f = flux if flux is not None else sys.stdin
        if f is None:
            return None
        return _lire_borne(f.read, delai)
    except Exception:  # noqa: BLE001
        return None


def lire_stdin_octets_borne(delai: float = DELAI_DEFAUT, flux=None):
    """Bytes variant, or None. Default reads raw fd 0 with ``os.read``: a daemon
    thread blocked in ``sys.stdin.buffer.read`` crashes the interpreter at
    shutdown (exit 0xC0000005, measured 2026-09-27)."""
    try:
        if flux is not None:
            return _lire_borne(flux.read, delai)
        import os

        def _fd0():
            morceaux = []
            while True:
                b = os.read(0, 65536)
                if not b:
                    return b"".join(morceaux)
                morceaux.append(b)
        return _lire_borne(_fd0, delai)
    except Exception:  # noqa: BLE001
        return None


def signaler_fail_open(hook: str, delai: float = DELAI_DEFAUT) -> None:
    """One stderr line so a timeout fail-open is never an invisible bypass."""
    try:
        sys.stderr.write(f"{hook}: stdin non recu en {delai:g} s — fail-open\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
