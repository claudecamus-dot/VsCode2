"""GMAH provenance key storage (story 14.11): DPAPI-protected key file, stdlib only (ctypes/crypt32).

File format on Windows: MAGIE + DPAPI blob (CryptProtectData, user scope). Legacy format: 32 raw bytes.
Honest limits: DPAPI (user scope) is bound to the Windows account; any process of the SAME account in
the SAME session can still call CryptUnprotectData. The real boundary stays the permission layer and
human approval. Cross-account read was NOT tested in CI (only corrupted blob / other entropy).
Non-Windows: plaintext file with mode 0o600 (no DPAPI available)."""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

MAGIE = b"GMAHDPAPI1\n"
WINDOWS = sys.platform == "win32"


def _blob(data: bytes):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data) or 1)
    return BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _appel(nom: str, data: bytes, entropie: bytes) -> bytes:
    if not WINDOWS:
        raise OSError("DPAPI unavailable on this platform")
    import ctypes
    entree, k1 = _blob(data)
    ent, k2 = _blob(entropie)
    sortie = type(entree)()
    f = getattr(ctypes.windll.crypt32, nom)
    ok = f(ctypes.byref(entree), None, ctypes.byref(ent) if entropie else None, None, None, 0x1,
           ctypes.byref(sortie))
    if not ok:
        raise OSError(f"{nom} failed (winerror {ctypes.GetLastError()})")
    try:
        return ctypes.string_at(sortie.pbData, sortie.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(sortie.pbData)


def proteger(cle: bytes, entropie: bytes = b"") -> bytes:
    return MAGIE + _appel("CryptProtectData", cle, entropie)


def deproteger(contenu: bytes, entropie: bytes = b"") -> bytes:
    if not contenu.startswith(MAGIE):
        raise OSError("not a DPAPI key file")
    cle = _appel("CryptUnprotectData", contenu[len(MAGIE):], entropie)
    if not cle:
        raise OSError("empty key")
    return cle


def lire(p: Path, entropie: bytes = b"") -> bytes:
    """Key bytes. Raises FileNotFoundError if absent, OSError (reason in message) if unusable.
    A legacy plaintext key is still read so the existing signed state keeps working until
    `gravite.py migrer-cle` wraps it (same bytes)."""
    brut = p.read_bytes()
    if brut.startswith(MAGIE):
        return deproteger(brut, entropie)
    if not brut:
        raise OSError("empty key file")
    return brut


def est_clair(p: Path) -> bool:
    """True if the key file exists as legacy plaintext on Windows (migration pending)."""
    try:
        return WINDOWS and not p.read_bytes().startswith(MAGIE)
    except OSError:
        return False


def creer(p: Path, entropie: bytes = b"") -> bytes:
    """Create the key (O_EXCL) already protected on Windows; returns the key. FileExistsError if present."""
    cle = secrets.token_bytes(32)
    _ecrire_excl(p, proteger(cle, entropie) if WINDOWS else cle)
    return cle


def _ecrire_excl(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    ok = False
    try:
        if os.write(fd, data) != len(data):
            raise OSError("short write")
        os.fsync(fd)
        ok = True
    finally:
        os.close(fd)
        if not ok:  # a partial file must not lock the key out
            p.unlink(missing_ok=True)


def migrer(p: Path, entropie: bytes = b"") -> str:
    """Wrap the legacy plaintext key in DPAPI WITHOUT changing its bytes. New file written next to
    the original, re-read and compared, then atomically replaces it. Original intact on any failure.
    Returns a status message; raises OSError on failure."""
    brut = p.read_bytes()
    if brut.startswith(MAGIE):
        return "already protected"
    if not brut:
        raise OSError("empty key file")
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{secrets.token_hex(4)}.dpapi.tmp")
    tmp.unlink(missing_ok=True)
    try:
        _ecrire_excl(tmp, proteger(brut, entropie))
        if deproteger(tmp.read_bytes(), entropie) != brut:
            raise OSError("re-read mismatch")
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return "migrated"
