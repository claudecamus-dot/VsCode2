"""Un vrai uvicorn pour les parcours navigateur (`tests/test_e2e_*.py`) :
sous-processus sur un port libre, base SQLite temporaire, préchargements
coupés (`WHISPER_WARM_UP=0`, `OLLAMA_WARM_UP=0` — les préchauffages sont des
fils d'arrière-plan depuis le 2026-11-05 et ne retardent plus le port, mais un
Ollama absent y garderait un fil jusqu'à `OLLAMA_TIMEOUT`, revue 2026-09-09 A2), journal capturé, et un diagnostic qui dit ce qui s'est
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
# Tunable (Story 3.1, D-4): a loaded machine can exceed 60 s; the default stays.
_DELAI_DEMARRAGE_S = float(os.environ.get("E2E_DELAI_DEMARRAGE_S", "60"))
# Lines of the server log attached to a red e2e test (D-4).
_LIGNES_JOURNAL = 80
# base URL -> log path of every server currently up (read by tests/conftest.py).
SERVEURS_ACTIFS: dict[str, Path] = {}


def fin_du_journal(chemin: Path, nb_lignes: int = _LIGNES_JOURNAL) -> str:
    """Last `nb_lignes` of the uvicorn log, or a placeholder if unreadable."""
    try:
        lignes = chemin.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"<journal illisible : {exc}>"
    return "\n".join(lignes[-nb_lignes:])


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
    # Password read from the env PASSED TO THE SERVER, before spawning it
    # (Story 3.1, D-5): reading `os.environ[...]` inside the probe's mute
    # `except` turned a missing variable into 60 s of retries then a false
    # "uvicorn did not answer" diagnosis.
    mot_de_passe = env.get("APP_AUTH_PASSWORD")
    if not mot_de_passe:
        raise RuntimeError(
            "APP_AUTH_PASSWORD absent de l'environnement du serveur e2e : "
            "toute route protegee rendrait 503 (tests/conftest.py le pose "
            "normalement ; ne pas le retirer avant serveur_uvicorn)."
        )
    chemin_journal = dossier / "uvicorn.log"
    journal = open(chemin_journal, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "info"],
        cwd=_RACINE, env=env, stdout=journal, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        statut: int | None = None
        limite = time.monotonic() + _DELAI_DEMARRAGE_S
        while time.monotonic() < limite and proc.poll() is None:
            try:
                # L'app exige une authentification depuis le 2026-09-19
                # (app/auth.py, defaut ferme) : la sonde de demarrage doit se
                # presenter, sinon elle lit le 401 de la garde et conclut a tort
                # que le serveur n'a pas demarre. Le mot de passe est celui que
                # tests/conftest.py a pose dans l'environnement, herite par le
                # processus uvicorn via `dict(os.environ)`.
                requete = urllib.request.Request(
                    base + "/missions",
                    headers={
                        "Authorization": "Bearer " + mot_de_passe
                    },
                )
                with urllib.request.urlopen(requete, timeout=2) as r:
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
        # Registry read by tests/conftest.py (pytest_runtest_makereport): a
        # MODULE-scoped server never sees the test failure in this generator.
        SERVEURS_ACTIFS[base] = chemin_journal
        try:
            yield base
        except GeneratorExit:
            raise
        except BaseException as exc:
            # A red e2e test carries the END of the server log (D-4): before,
            # the log was read only when STARTUP failed, never when a test
            # failed against a running server. BaseException: `pytest.fail`
            # raises `Failed`, which is not an `Exception`.
            exc.add_note(
                f"--- fin du journal uvicorn ({chemin_journal}) ---\n"
                + fin_du_journal(chemin_journal)
            )
            raise
    finally:
        SERVEURS_ACTIFS.pop(base, None)
        _arreter(proc)
        journal.close()
