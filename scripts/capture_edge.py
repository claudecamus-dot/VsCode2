"""Capture / session Edge ad hoc, bornee et nettoyee (stdlib seule).

Regle : toute capture ou session Edge ad hoc passe par ce helper. Le
2026-10-22 des Edge lances a la main (--screenshot, CDP aux ports 9333/9355)
sont restes vivants 9 a 21 h faute de timeout et de nettoyage.

Garanties : profil --user-data-dir NEUF supprime en fin, port CDP toujours 0,
timeout cote appelant, arbre du navigateur tue dans un finally.
Rien n'est journalise ailleurs que sur stderr.

Usage : python scripts/capture_edge.py URL sortie.png [largeur hauteur delai_s]
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_CANDIDATS_WINDOWS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
_CANDIDATS_PATH = ["microsoft-edge", "microsoft-edge-stable", "msedge"]

# Fabrique de Popen : point d'injection des tests.
_popen = subprocess.Popen


def trouver_edge() -> str:
    force = os.environ.get("E2E_NAVIGATEUR")
    if force and Path(force).exists():
        return force
    for chemin in _CANDIDATS_WINDOWS:
        if Path(chemin).exists():
            return chemin
    for nom in _CANDIDATS_PATH:
        trouve = shutil.which(nom)
        if trouve:
            return trouve
    raise RuntimeError("Edge introuvable")


def _tuer_arbre(pid: int) -> None:
    """Tue le navigateur ET ses enfants (renderers, GPU, crashpad)."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        return
    import signal

    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def construire_commande(exe: str, url: str, profil: str, *, sortie_png=None,
                        largeur: int = 1280, hauteur: int = 900,
                        delai_s: float = 30, cdp: bool = False) -> list[str]:
    cmd = [
        exe, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--disable-dev-shm-usage", f"--user-data-dir={profil}",
        f"--window-size={largeur},{hauteur}",
    ]
    if cdp:
        cmd.append("--remote-debugging-port=0")  # jamais de port fixe
    if sortie_png is not None:
        budget_ms = int(max(delai_s, 1) * 1000 / 2)
        cmd += [f"--virtual-time-budget={budget_ms}", f"--screenshot={sortie_png}"]
    cmd.append(url)
    return cmd


@contextlib.contextmanager
def _profil_neuf():
    profil = tempfile.mkdtemp(prefix="edge_")
    try:
        yield profil
    finally:
        shutil.rmtree(profil, ignore_errors=True)


def capturer(url: str, sortie_png, largeur: int = 1280, hauteur: int = 900,
             delai_s: float = 30) -> None:
    """Capture `url` en PNG. TimeoutError si Edge depasse `delai_s`."""
    sortie_png = str(sortie_png)
    with _profil_neuf() as profil:
        cmd = construire_commande(
            trouver_edge(), url, profil, sortie_png=sortie_png,
            largeur=largeur, hauteur=hauteur, delai_s=delai_s)
        proc = _popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            try:
                proc.wait(timeout=delai_s)
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(f"Edge > {delai_s} s") from exc
        finally:
            _tuer_arbre(proc.pid)


@contextlib.contextmanager
def session_cdp(url: str = "about:blank", largeur: int = 1280, hauteur: int = 900):
    """Edge headless avec CDP (port 0). Cede (processus, dossier du profil) ;
    DevToolsActivePort du profil donne le port reel. Arbre tue en sortie."""
    with _profil_neuf() as profil:
        cmd = construire_commande(trouver_edge(), url, profil, largeur=largeur,
                                  hauteur=hauteur, cdp=True)
        proc = _popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            yield proc, profil
        finally:
            _tuer_arbre(proc.pid)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    largeur = int(argv[2]) if len(argv) > 2 else 1280
    hauteur = int(argv[3]) if len(argv) > 3 else 900
    delai = float(argv[4]) if len(argv) > 4 else 30
    try:
        capturer(argv[0], argv[1], largeur, hauteur, delai)
    except (TimeoutError, RuntimeError, OSError) as exc:
        print(f"capture_edge: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
