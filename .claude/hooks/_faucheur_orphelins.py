"""Orphan reaper: finds and stops orphaned ``git.exe`` and hook Python processes.

Why (2026-10-10, third occurrence): 252 ``git.exe`` and 3 hook ``python.exe``
accumulated with a dead parent, saturating the workstation. A subprocess timeout
on Windows kills the direct child only, and Claude Code cancels a hook by killing
the ``py.exe`` launcher: the ``python.exe`` child and its ``git.exe`` survive.

Candidates (all conditions required):
- name ``git.exe``; or ``python.exe``/``py.exe``/``pythonw.exe`` whose command
  line targets ``.claude/hooks/`` or ``.claude/supervision/``;
- orphan: the parent PID no longer exists, OR the process holding that PID was
  created AFTER the child (PID reuse) -- a parent that exists and is older is
  ALWAYS treated as alive, the process is never touched;
- older than ``seuil_min`` (30 min by default);
- not a known detached launcher (``WHITELIST_CMD`` in the command line, or a PID
  declared in ``.claude/supervision/popen_detaches.jsonl``): those live up to
  14 min without a parent ON PURPOSE.
Two more families (2026-10-22, test debris that saturated the workstation):
- ``navigateur-test``: ``msedge.exe``/``chrome.exe`` ROOT (no ``--type=``) whose command line
  has ``--headless`` AND (``--screenshot`` OR ``--remote-debugging-port``) AND a
  ``--user-data-dir=``/``--screenshot=`` path under ``c:/tmp/`` or a scratchpad
  ``.../Temp/claude/`` folder; older than ``SEUIL_NAVIGATEUR_MIN`` (10 min); the parent may
  still exist (a headless test browser is never the user's browser);
- ``python-adhoc``: ``python.exe`` whose command line is exactly ``-`` (stdin) or starts with
  ``-c``, orphan (same rule), older than ``seuil_min``.
Guards for both: never a process with a live ancestor whose command line contains
``pytest``, never anything whose own or ancestor command line cites the 8020 site
(``--port 8020`` / ``serveur-dev`` / ``8020``). If ``taskkill`` fails on a single-thread
process the journal says ``bloque-noyau`` (one attempt, no retry loop).
Never by name alone. Before every stop the candidate is revalidated on a fresh
snapshot (same pid, same creation time, same name, still orphan). Stop =
``taskkill /F /T /PID`` through ``_lancement_borne.lancer_borne`` (never pipes).
Journal: ``.claude/supervision/orphelins_arretes.jsonl`` (ts, nom, age_min, pid,
resultat -- NEVER the command line), rotated to ``.1`` past 1 MB.

Stdlib only (``ctypes``); outside Windows every function returns "nothing".
Listing: Toolhelp32 snapshot + GetProcessTimes (~0.05 s for 1 000 processes, CIM
takes 5-7 s). The command line is read for Python, git and browser candidates only (name filter first), through
``NtQueryInformationProcess(ProcessCommandLineInformation)``.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import time
from collections import namedtuple

SEUIL_MIN = 30
NOMS_GIT = frozenset({"git.exe"})
# Long-lived git processes that legitimately outlive their parent (never reaped). Matched as
# whole words: the bare substring " gc" also matched `-c gc.auto=0`, carried by every git the
# editor launches, so 246 orphans escaped the reaper (2026-10-12).
GIT_DAEMONS_LEGITIMES = re.compile(r"fsmonitor--daemon|(?:^|\s)(?:gc|maintenance)(?:\s|$)")
NOMS_PYTHON = frozenset({"python.exe", "py.exe", "pythonw.exe"})
NOMS_NAVIGATEUR = frozenset({"msedge.exe", "chrome.exe"})
SEUIL_NAVIGATEUR_MIN = 10
# Never touch a tree with such a live ancestor (full test run / the 8020 dev site).
MARQUEURS_PROTEGES = ("pytest", "--port 8020", "serveur-dev", "8020")
NOMS_ANCETRES_LUS = NOMS_PYTHON | {"pytest.exe", "powershell.exe", "pwsh.exe", "cmd.exe",
                                   "uvicorn.exe"}
_CHEMIN_NAV = re.compile(r'--(?:user-data-dir|screenshot)=(?:"([^"]*)"|(\S+))')
_ADHOC = re.compile(r"^-(?:c(?:\s|$)|$)")
CIBLES_CMD = ("/.claude/hooks/", "/.claude/supervision/")
# Detached launchers that live without a parent on purpose (refresh_wiki_apres_action
# -> _refresh_wiki_lanceur, max 840 s). Matched on the normalised command line.
WHITELIST_CMD = ("refresh_wiki_apres_action", "_refresh_wiki_lanceur")
CAP_JOURNAL = 1_000_000
DELAI_TASKKILL_S = 10.0

# debut: creation, epoch seconds or None; threads: thread count or None (unknown)
Proc = namedtuple("Proc", "pid ppid nom debut threads", defaults=(None,))

_ICI = os.path.dirname(os.path.abspath(__file__))
_SUPERVISION = os.path.join(_ICI, "..", "supervision")


# --------------------------------------------------------------------- listing (Windows)
def lister_processus():
    """``{pid: Proc}`` for every process, or None outside Windows / on any error."""
    if os.name != "nt":
        return None
    try:
        return _lister_nt()
    except Exception:  # noqa: BLE001 - fail-open
        return None


def _lister_nt():
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return None
    brut = []
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            brut.append((int(e.th32ProcessID), int(e.th32ParentProcessID), e.szExeFile.lower(),
                         int(e.cntThreads)))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    # Creation times are read only where a criterion needs them (watched names and
    # their parents): one OpenProcess per process would cost seconds under load.
    utiles = set()
    for pid, ppid, nom, _t in brut:
        if nom in NOMS_GIT or nom in NOMS_PYTHON or nom in NOMS_NAVIGATEUR:
            utiles.update((pid, ppid))
    return {pid: Proc(pid, ppid, nom, debut_processus(pid) if pid in utiles else None, thr)
            for pid, ppid, nom, thr in brut}


def debut_processus(pid):
    """Creation time of ``pid`` (epoch seconds), or None when unreadable."""
    if os.name != "nt" or not pid:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            ft = [wintypes.FILETIME() for _ in range(4)]
            if not k32.GetProcessTimes(h, *[ctypes.byref(f) for f in ft]):
                return None
            v = (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime
            return (v - 116444736000000000) / 1e7 if v else None
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return None


def ligne_commande(pid):
    """Command line of ``pid`` (same user only), or None. Never logged."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                    wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
        h = k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return None
        try:
            taille = wintypes.ULONG(0)
            buf = ctypes.create_string_buffer(65536)
            st = ntdll.NtQueryInformationProcess(h, 60, buf, len(buf), ctypes.byref(taille))
            if st != 0:  # 60 = ProcessCommandLineInformation (Windows 8.1+)
                return None

            class UNICODE_STRING(ctypes.Structure):
                _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                            ("Buffer", ctypes.c_void_p)]
            us = UNICODE_STRING.from_buffer(buf)
            if not us.Buffer or not us.Length:
                return ""
            return ctypes.wstring_at(us.Buffer, us.Length // 2)
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------- pure criteria
def est_orphelin(p, procs):
    """True when ``p``'s parent is gone, or the PID now belongs to a younger process.

    A parent that exists and is not younger than ``p`` (or whose creation time is
    unknown) is ALIVE: never an orphan."""
    parent = procs.get(p.ppid)
    if parent is None:
        return True
    if parent.debut is None or p.debut is None:
        return False
    return parent.debut > p.debut


def age_min(p, maintenant):
    return (maintenant - p.debut) / 60.0 if p.debut is not None else None


def vise_hooks(cmd):
    c = (cmd or "").replace("\\", "/").lower()
    return any(m in c for m in CIBLES_CMD)


def _norm(cmd):
    return (cmd or "").replace("\\", "/").lower()


def est_navigateur_test(cmd):
    """Headless test browser ROOT: --headless AND (--screenshot OR --remote-debugging-port)
    AND a --user-data-dir=/--screenshot= path under c:/tmp/ or a scratchpad Temp/claude/."""
    c = _norm(cmd)
    if not c or "--type=" in c or "--headless" not in c:
        return False
    if not (re.search(r"--screenshot(?:=|\s|$)", c) or "--remote-debugging-port" in c):
        return False
    temp = _norm(os.environ.get("TEMP") or os.environ.get("TMP") or "").rstrip("/")
    for m in _CHEMIN_NAV.finditer(c):
        chemin = (m.group(1) or m.group(2) or "").strip()
        if (chemin.startswith("c:/tmp/") or "/temp/claude/" in chemin
                or (temp and chemin.startswith(temp + "/claude/"))):
            return True
    return False


def est_python_adhoc(cmd):
    """``python.exe -`` (stdin) or ``python.exe -c ...``: the argument after the executable."""
    c = (cmd or "").strip()
    if c.startswith('"'):
        fin = c.find('"', 1)
        reste = c[fin + 1:].strip() if fin > 0 else ""
    else:
        morceaux = c.split(None, 1)
        reste = morceaux[1].strip() if len(morceaux) == 2 else ("-" if c == "-" else "")
    return bool(_ADHOC.match(reste))


def cite_protege(cmd):
    c = _norm(cmd)
    return any(m in c for m in MARQUEURS_PROTEGES)


def protege(p, procs, lire_cmd, cmd=None):
    """True when ``p``'s own line or a LIVE ancestor's line cites pytest / the 8020 site."""
    if cite_protege(lire_cmd(p.pid) if cmd is None else cmd):
        return True
    vus, cur = {p.pid}, p
    for _ in range(64):
        a = procs.get(cur.ppid)
        if a is None or a.pid in vus:
            return False
        if a.debut is not None and cur.debut is not None and a.debut > cur.debut:
            return False  # PID reused: not a real ancestor
        vus.add(a.pid)
        if (a.nom in NOMS_ANCETRES_LUS or "pytest" in a.nom) and cite_protege(lire_cmd(a.pid)):
            return True
        cur = a
    return False


def en_liste_blanche(p, cmd, pids_detaches):
    c = (cmd or "").replace("\\", "/").lower()
    return p.pid in pids_detaches or any(w in c for w in WHITELIST_CMD)


def pids_detaches(chemin=None):
    """PIDs declared by detached launchers in ``popen_detaches.jsonl`` (last 256 KB)."""
    chemin = chemin or os.environ.get("CLAUDE_POPEN_DETACHES_LOG") or os.path.join(
        _SUPERVISION, "popen_detaches.jsonl")
    out = set()
    try:
        with open(chemin, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 262144))
            for ligne in fh.read().decode("utf-8", "replace").splitlines():
                try:
                    pid = json.loads(ligne).get("pid")
                except Exception:  # noqa: BLE001 - truncated first line
                    continue
                if isinstance(pid, int):
                    out.add(pid)
    except Exception:  # noqa: BLE001
        pass
    return out


def candidats(procs=None, *, maintenant=None, seuil_min=SEUIL_MIN, lire_cmd=ligne_commande,
              detaches=None):
    """Orphans to stop: list of dicts ``{pid, nom, debut, age_min, famille}``."""
    procs = lister_processus() if procs is None else procs
    if not procs:
        return []
    maintenant = time.time() if maintenant is None else maintenant
    detaches = pids_detaches() if detaches is None else detaches
    moi = os.getpid()
    out = []
    for p in procs.values():
        if p.pid == moi or (p.nom not in NOMS_GIT and p.nom not in NOMS_PYTHON
                            and p.nom not in NOMS_NAVIGATEUR):
            continue
        age = age_min(p, maintenant)
        if age is None:
            continue
        if p.nom in NOMS_NAVIGATEUR:
            # Root of a headless test browser: the parent may be alive, no orphan rule.
            if age < SEUIL_NAVIGATEUR_MIN:
                continue
            cmd = lire_cmd(p.pid)
            if not est_navigateur_test(cmd) or protege(p, procs, lire_cmd, cmd):
                continue
            famille = "navigateur-test"
        elif age < seuil_min or not est_orphelin(p, procs):
            continue
        elif p.nom in NOMS_PYTHON:
            cmd = lire_cmd(p.pid)
            if en_liste_blanche(p, cmd, detaches):
                continue
            if vise_hooks(cmd):
                famille = "hook"
            elif est_python_adhoc(cmd) and not protege(p, procs, lire_cmd, cmd):
                famille = "python-adhoc"
            else:
                continue
        else:
            cmd = lire_cmd(p.pid)
            if GIT_DAEMONS_LEGITIMES.search((cmd or "").lower()):
                continue
            famille = "git"
        out.append({"pid": p.pid, "nom": p.nom, "debut": p.debut, "age_min": round(age),
                    "famille": famille})
    return out


def revalider(c, procs, lire_cmd=None):
    """Same pid, same creation time, same name, still orphan on a FRESH snapshot.

    ``navigateur-test`` has no orphan rule; both new families are also re-checked against the
    pytest / 8020 guards on the fresh snapshot when ``lire_cmd`` is given."""
    p = (procs or {}).get(c["pid"])
    if not (p is not None and p.debut is not None and p.debut == c["debut"]
            and p.nom == c["nom"]):
        return False
    if c.get("famille") != "navigateur-test" and not est_orphelin(p, procs):
        return False
    nouvelle = c.get("famille") in ("navigateur-test", "python-adhoc")
    return not (nouvelle and lire_cmd is not None and protege(p, procs, lire_cmd))


# --------------------------------------------------------------------- stop + journal
def _taskkill_exe():
    racine = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    return os.path.join(racine, "System32", "taskkill.exe")


def tuer_arbre(pid):
    """``taskkill /F /T /PID`` bounded, outputs to files (never pipes). True on success."""
    cmd = [_taskkill_exe(), "/F", "/T", "/PID", str(int(pid))]
    try:
        if _ICI not in sys.path:
            sys.path.insert(0, _ICI)
        from _lancement_borne import lancer_borne
        return lancer_borne(cmd, delai=DELAI_TASKKILL_S).returncode == 0
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - timeout / OS error = failure
        return False
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=DELAI_TASKKILL_S).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def chemin_journal():
    return os.environ.get("CLAUDE_ORPHELINS_LOG") or os.path.join(
        _SUPERVISION, "orphelins_arretes.jsonl")


def journaliser(c, resultat, chemin=None, cap=CAP_JOURNAL):
    """One line per stop attempt: ts, nom, age_min, pid, resultat. Never raises."""
    try:
        chemin = chemin or chemin_journal()
        if os.path.exists(chemin) and os.path.getsize(chemin) > cap:
            os.replace(chemin, chemin + ".1")
        ligne = {"ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
                 "nom": c["nom"], "age_min": c["age_min"], "pid": c["pid"], "resultat": resultat}
        with open(chemin, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ligne) + "\n")
    except Exception:  # noqa: BLE001
        pass


def arreter(liste, *, relister=lister_processus, tuer=tuer_arbre, chemin=None,
            lire_cmd=ligne_commande):
    """Stop each candidate after revalidation. Returns the stopped candidates."""
    if not liste:
        return []
    frais = relister()
    arretes = []
    for c in liste:
        if not revalider(c, frais, lire_cmd):
            continue  # gone, PID reused, parent back, or protected: never stop
        ok = tuer(c["pid"])
        if ok:
            resultat = "arrete"
        elif frais[c["pid"]].threads == 1:
            resultat = "bloque-noyau"  # single thread stuck in the kernel; ONE attempt, no retry
        else:
            resultat = "echec"
        journaliser(c, resultat, chemin)
        if ok:
            arretes.append(c)
    return arretes


def resume(arretes):
    """``"N orphelins arretes : X git.exe, Y hooks"`` or "" when nothing was stopped."""
    if not arretes:
        return ""
    n = {fam: sum(1 for c in arretes if c["famille"] == fam)
         for fam in ("git", "navigateur-test", "python-adhoc")}
    h = len(arretes) - sum(n.values())
    extra = "".join(f", {n[fam]} {lib}" for fam, lib in
                    (("navigateur-test", "navigateurs-test"), ("python-adhoc", "python ad hoc"))
                    if n[fam])
    return f"{len(arretes)} orphelins arretes : {n['git']} git.exe, {h} hooks{extra}"


def faucher(seuil_min=SEUIL_MIN):
    """Automatic entry point: list, revalidate, stop. Returns the stopped candidates."""
    if os.name != "nt":
        return []
    if os.environ.get("CLAUDE_FAUCHEUR_A_BLANC"):  # tests: full path, nothing stopped
        candidats(seuil_min=seuil_min)
        return []
    return arreter(candidats(seuil_min=seuil_min))


# ---------------------------------------------------------------- periodic limiter
def chemin_limiteur():
    return os.environ.get("CLAUDE_FAUCHEUR_LIMITEUR") or os.path.join(
        _SUPERVISION, "faucheur_dernier.json")


def trop_tot(chemin, limite_min, maintenant):
    """True only when the last real sweep is less than ``limite_min`` minutes old.

    Fail-open: a missing or unreadable file, or a stamp in the future (clock set
    back), means "sweep".
    """
    try:
        with open(chemin, encoding="utf-8") as fh:
            t = float(fh.read().strip())
    except Exception:  # noqa: BLE001
        return False
    if t > maintenant:
        return False
    return (maintenant - t) < limite_min * 60


def marquer(chemin, maintenant):
    """Atomically stamp the time of the sweep (temp file + replace). Never raises."""
    tmp = f"{chemin}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(repr(float(maintenant)))
        os.replace(tmp, chemin)
    except Exception:  # noqa: BLE001
        try:
            os.remove(tmp)
        except OSError:
            pass
