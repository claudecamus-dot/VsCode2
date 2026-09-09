"""Un vrai uvicorn pour les parcours navigateur (`tests/test_e2e_*.py`) :
sous-processus sur un port libre, base SQLite temporaire, préchargements
coupés (`WHISPER_WARM_UP=0`, `OLLAMA_WARM_UP=0` — sinon le serveur attend
jusqu'à `OLLAMA_TIMEOUT` un Ollama absent AVANT sa première requête, revue du
2026-09-09 A2), journal capturé, et un diagnostic qui dit ce qui s'est
réellement passé : le code de sortie est relevé AVANT `kill()`, sans quoi la
branche « n'a pas répondu » était injoignable (A7).

    with serveur_uvicorn(dossier, WHISPER_MODEL="tiny") as base:
        ...  # base = "http://127.0.0.1:<port>"
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from navigateur_cdp import port_libre

_RACINE = Path(__file__).resolve().parents[1]
_DELAI_DEMARRAGE_S = 60.0


def _arreter(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass  # le journal se ferme quand même ; le processus est signalé


@contextlib.contextmanager
def serveur_uvicorn(dossier: Path, **env_supplementaire: str):
    port = port_libre()
    env = dict(
        os.environ, APP_DB_PATH=str(dossier / "e2e.db"), PYTHONUTF8="1",
        WHISPER_WARM_UP="0", OLLAMA_WARM_UP="0",
    )
    env.update(env_supplementaire)
    chemin_journal = dossier / "uvicorn.log"
    journal = open(chemin_journal, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=_RACINE, env=env, stdout=journal, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        statut: int | None = None
        limite = time.monotonic() + _DELAI_DEMARRAGE_S
        while time.monotonic() < limite and proc.poll() is None:
            try:
                with urllib.request.urlopen(base + "/missions", timeout=2) as r:
                    statut = r.status
                break
            except urllib.error.HTTPError as e:
                statut = e.code  # le serveur répond — mal : ce n'est pas « pas démarré »
                break
            except Exception:
                time.sleep(0.3)
        if statut != 200:
            code_avant = proc.poll()  # AVANT kill : lui seul dit si uvicorn est mort seul
            _arreter(proc)
            journal.close()
            if code_avant is not None:
                cause = f"uvicorn est sorti avec le code {code_avant} avant de répondre"
            elif statut is None:
                cause = f"uvicorn n'a pas répondu en {_DELAI_DEMARRAGE_S:.0f} s"
            else:
                cause = f"uvicorn a démarré mais GET /missions -> {statut}"
            raise RuntimeError(
                cause + " :\n" + chemin_journal.read_text(encoding="utf-8", errors="replace")
            )
        yield base
    finally:
        _arreter(proc)
        journal.close()
