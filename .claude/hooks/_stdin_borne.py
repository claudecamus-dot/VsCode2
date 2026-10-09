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


def armer_chien_de_garde(delai: float = 25.0, code: int = 0) -> None:
    """Max lifetime for a NON-guard hook (finding hooks:processus-git-orphelins-et-verrou-fige).

    Claude Code kills the ``py`` launcher at the hook timeout but not the ``python.exe``
    child, which then lives on as an orphan if anything blocks (file I/O, a child, a
    lock). A daemon timer ends the process with ``os._exit(code)`` after ``delai`` s,
    which must stay below the hook's settings.json timeout. Fail-open ``code`` 0 for
    reminders: NEVER use it in a guard (a guard's exit code is its decision)."""
    try:
        import os

        def _fin():
            try:
                sys.stderr.write(f"hook: duree maximale {delai:g} s atteinte -- fail-open\n")
                sys.stderr.flush()
            except Exception:  # noqa: BLE001
                pass
            os._exit(code)

        t = threading.Timer(delai, _fin)
        t.daemon = True
        t.start()
    except Exception:  # noqa: BLE001 - never raise from a safety net
        pass


def signaler_fail_open(hook: str, delai: float = DELAI_DEFAUT) -> None:
    """One stderr line so a timeout fail-open is never an invisible bypass."""
    try:
        sys.stderr.write(f"{hook}: stdin non recu en {delai:g} s — fail-open\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass


def lier_stdin(g, hook_file):
    """Bind the bounded-stdin wrapper and its journal to the calling hook.

    ``g`` is the hook's ``globals()`` (the hook may set ``_FLUX_STDIN`` and
    ``_GARDES_ACTIVES``; the wrapper stores ``_DELAI_S`` / ``_ATTENTE_S`` there for
    ``_refus_prudent``). Returns ``(_stdin_borne, _ecrire_journal_stdin,
    _journal_attente)``. Single source: the 17 hooks no longer carry a copy.
    """
    import datetime
    import json
    import os
    import time

    def _ecrire_journal_stdin(champs, cap=1_000_000, suffixe="", rotation=True):
        """Writer of ``<hooks>/../supervision/refus_stdin<suffixe>.jsonl``
        (``CLAUDE_REFUS_STDIN_LOG`` redirects it, tests). Past ``cap`` the file
        ROTATES to ``<file>.1`` (``rotation=False``: hard stop, dispatcher crash
        lines). Never raises, never changes the exit."""
        try:
            chemin = os.environ.get("CLAUDE_REFUS_STDIN_LOG") or os.path.join(
                os.path.dirname(os.path.abspath(hook_file)), "..", "supervision",
                "refus_stdin.jsonl")
            if suffixe:
                chemin = os.path.splitext(chemin)[0] + suffixe + ".jsonl"
            if os.path.exists(chemin) and os.path.getsize(chemin) > cap:
                if not rotation:
                    return
                os.replace(chemin, chemin + ".1")
            ligne = {"ts": datetime.datetime.now(datetime.UTC).isoformat(
                         timespec="seconds"),
                     "hook": os.path.splitext(os.path.basename(hook_file))[0]}
            if g.get("_GARDES_ACTIVES") is not None:
                ligne["gardes"] = list(g["_GARDES_ACTIVES"])
            ligne.update(champs)
            with open(chemin, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ligne) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def _journal_attente(attente, delai):
        """Payload that arrived after > 2 s: one line in ``refus_stdin_attente.jsonl``."""
        _ecrire_journal_stdin({"motif": "attente", "issue": "passe", "delai_s": float(delai),
                               "attente_s": attente}, 500_000, "_attente")

    def _stdin_borne(delai=15.0):
        """Bounded stdin read: the payload, or None on timeout/error. A hook may set
        ``_FLUX_STDIN``; ``CLAUDE_STDIN_DELAI_S`` lowers the bound, never raises it."""
        try:
            delai = min(delai, float(os.environ.get("CLAUDE_STDIN_DELAI_S", delai)))
        except ValueError:
            pass
        g["_DELAI_S"] = delai
        t0 = time.monotonic()
        v = lire_stdin_borne(delai, g.get("_FLUX_STDIN"))
        attente = round(time.monotonic() - t0, 3)
        g["_ATTENTE_S"] = attente
        if v is not None and attente > 2.0:
            # via the hook's global so a test may monkeypatch it, as before
            g.get("_journal_attente", _journal_attente)(attente, delai)
        return v

    return _stdin_borne, _ecrire_journal_stdin, _journal_attente
