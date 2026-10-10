"""Bounded subprocess launch shared by the hub's hooks and the canon scan.

Why: on Windows, ``subprocess.run(cmd, capture_output=True, timeout=N)`` does not
honour N when a descendant keeps the pipes open: once the timeout expires, ``run()``
kills the direct child only and then calls ``communicate()`` again WITHOUT a bound,
waiting for a grandchild that still holds the write end of the pipe (repro
2026-10-09: timeout 3 s -> 40.9 s; file outputs + ``taskkill /T`` -> 5 s).

``lancer_borne`` is a drop-in for that call:
- outputs go to anonymous temporary files, never to pipes; stdin is DEVNULL (or
  ``entree`` through a temporary file); every temporary file is closed (hence
  deleted) in all cases;
- on expiry the whole process TREE dies (``taskkill /F /T`` by absolute path on
  Windows, ``os.killpg`` of the new session elsewhere), then
  ``subprocess.TimeoutExpired`` is raised, so existing ``except`` clauses still apply;
- stdout/stderr are BYTES; ``lancer_texte`` decodes them like ``text=True`` did;
- for git, ``GIT_PAGER=cat`` and ``GIT_TERMINAL_PROMPT=0`` are added to the env.
It never raises anything but ``subprocess.SubprocessError`` / ``OSError``.

Stdlib only, no project import: exported as a helper library next to the hooks
(same model as ``_stdin_borne.py``); callers import it with a fallback to their
previous ``subprocess.run`` call when it is absent.
"""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile

DELAI_TASKKILL_S = 10.0  # taskkill itself is bounded (its outputs go to DEVNULL)
DELAI_APRES_ARRET_S = 5.0  # wait for the killed child to be reaped


def _env_pour(cmd, env):
    """Environment for ``cmd``: unchanged unless the program is git."""
    try:
        prog = os.path.basename(str(cmd[0] if isinstance(cmd, (list, tuple)) else cmd).split()[0])
    except (IndexError, TypeError):
        return env
    if prog.lower() not in ("git", "git.exe"):
        return env
    base = dict(os.environ if env is None else env)
    base["GIT_PAGER"] = "cat"
    base["GIT_TERMINAL_PROMPT"] = "0"
    return base


def _taskkill():
    racine = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    return os.path.join(racine, "System32", "taskkill.exe")


def _tuer_arbre(proc):
    """Kill ``proc`` AND its descendants. Never raises."""
    if proc.poll() is not None:
        return  # already reaped: its PID may be recycled, never kill by PID now
    try:
        if os.name == "nt":
            subprocess.run([_taskkill(), "/F", "/T", "/PID", str(proc.pid)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=DELAI_TASKKILL_S)
        else:
            os.killpg(proc.pid, getattr(signal, "SIGKILL", 9))
    except Exception:  # noqa: BLE001 - last resort below
        pass
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _lire(fichier):
    try:
        fichier.seek(0)
        return fichier.read()
    except Exception:  # noqa: BLE001 - an unreadable output is an empty output
        return b""


def lancer_borne(cmd, *, delai, cwd=None, env=None, entree=None):
    """Run ``cmd`` with a REAL bound of ``delai`` seconds (+ the kill time).

    Returns ``subprocess.CompletedProcess`` (stdout/stderr as bytes). Raises
    ``subprocess.TimeoutExpired`` on expiry (the tree is killed first) and
    ``OSError`` when the program cannot be launched."""
    # A missing bound would wait forever (closing review 2026-10-10): refused with an
    # error every migrated site already catches, so the guard falls back as before.
    if delai is None or delai <= 0:
        raise subprocess.SubprocessError("delai must be a positive number of seconds")
    fichiers = []
    try:
        sortie = tempfile.TemporaryFile()
        fichiers.append(sortie)
        erreur = tempfile.TemporaryFile()
        fichiers.append(erreur)
        if entree is None:
            stdin = subprocess.DEVNULL
        else:
            stdin = tempfile.TemporaryFile()
            fichiers.append(stdin)
            stdin.write(entree if isinstance(entree, bytes) else str(entree).encode("utf-8"))
            stdin.flush()
            stdin.seek(0)
        options = {}
        if os.name != "nt":
            options["start_new_session"] = True
        proc = subprocess.Popen(cmd, cwd=cwd, env=_env_pour(cmd, env), stdin=stdin,
                                stdout=sortie, stderr=erreur, **options)
        try:
            code = proc.wait(timeout=delai)
        except subprocess.TimeoutExpired:
            _tuer_arbre(proc)
            try:
                proc.wait(timeout=DELAI_APRES_ARRET_S)
            except Exception:  # noqa: BLE001
                pass
            raise subprocess.TimeoutExpired(cmd, delai, output=_lire(sortie),
                                            stderr=_lire(erreur)) from None
        return subprocess.CompletedProcess(cmd, code, _lire(sortie), _lire(erreur))
    except (subprocess.SubprocessError, OSError):
        raise
    except Exception as exc:  # noqa: BLE001 - contract: only subprocess / OS errors
        raise subprocess.SubprocessError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        for f in fichiers:
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass


def en_texte(octets, encoding="utf-8", errors="strict"):
    """Decode like ``text=True`` did (universal newlines included)."""
    if octets is None:
        return None
    return octets.decode(encoding, errors).replace("\r\n", "\n").replace("\r", "\n")


def lancer_texte(cmd, *, delai, cwd=None, env=None, entree=None,
                 encoding="utf-8", errors="strict"):
    """``lancer_borne`` with stdout/stderr decoded as ``text=True`` would.

    A decoding failure (``errors="strict"``) raises ``subprocess.SubprocessError``."""
    r = lancer_borne(cmd, delai=delai, cwd=cwd, env=env, entree=entree)
    try:
        return subprocess.CompletedProcess(r.args, r.returncode,
                                           en_texte(r.stdout, encoding, errors),
                                           en_texte(r.stderr, encoding, errors))
    except (UnicodeDecodeError, LookupError) as exc:
        raise subprocess.SubprocessError(f"{type(exc).__name__}: {exc}") from exc
