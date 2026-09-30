"""scripts/serveur-dev.ps1 ne déclare plus « OK » un site qui rend 503.

Diagnostic superviseur du 2026-09-29 : le contrôle de santé n'exerçait que les
routes publiques (/static/, et un code HTTP quelconque sur /). Un serveur sans
APP_AUTH_PASSWORD était déclaré sain pendant que l'utilisateur voyait KO au
clic sur Connexion, trois fois. Ces tests figent les deux gardes qui se jouent
SANS lancer de serveur : refus sans mot de passe, et -CheckOnly qui ne lance
rien. Port 8099 : jamais celui de l'utilisateur (8020).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
SCRIPT = RACINE / "scripts" / "serveur-dev.ps1"
PORT_LIBRE = "8099"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None,
    reason="script PowerShell 5.1 propre au poste Windows",
)


def _lancer(*args: str, mot_de_passe: str) -> subprocess.CompletedProcess:
    # Variable POSÉE (même vide) : python-dotenv n'écrase jamais une variable
    # existante, donc le .env du poste ne peut pas fausser le test.
    env = dict(os.environ, APP_AUTH_PASSWORD=mot_de_passe)
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(SCRIPT), "-Port", PORT_LIBRE, *args],
        cwd=RACINE, env=env, capture_output=True, timeout=180,
    )


def test_sans_mot_de_passe_le_script_refuse_avant_toute_purge() -> None:
    res = _lancer(mot_de_passe="")
    sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
    assert res.returncode == 2, sortie
    assert "APP_AUTH_PASSWORD" in sortie
    assert "Purge" not in sortie  # aucun serveur tué pour rien


def test_check_only_ne_lance_rien_et_dit_ko_sur_un_port_muet() -> None:
    res = _lancer("-CheckOnly", mot_de_passe="mdp-de-test")
    sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
    assert res.returncode == 1, sortie
    assert "uvicorn lancé" not in sortie
    assert "Purge" not in sortie


def test_stop_purge_le_worker_muet_d_un_reloader_mort_lu_dans_le_journal(tmp_path) -> None:
    """2026-09-29 : reloader mort pendant un --reload, worker respawné vivant
    mais MUET (aucun socket) qui verrouillait le journal — Chrome en
    ERR_CONNECTION_REFUSED et relance impossible. Le PID du reloader n'est lu
    que dans le journal .err du lancement précédent."""
    mort = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    pid_mort = int(mort.stdout.strip())
    (tmp_path / f"uvicorn_dev_{PORT_LIBRE}.log.err").write_text(
        f"INFO:     Started reloader process [{pid_mort}] using WatchFiles\n", encoding="utf-8")
    # Même forme de ligne de commande qu'un worker multiprocessing.spawn.
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                               "multiprocessing", f"parent_pid={pid_mort}"])
    # Faux ami : parent_pid=<pid_mort>9 est le worker d'un AUTRE serveur — il doit
    # survivre (le motif avait un joker final, revue 2026-09-29).
    voisin = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                               "multiprocessing", f"parent_pid={pid_mort}9"])
    try:
        env = dict(os.environ, APP_AUTH_PASSWORD="mdp-de-test", TEMP=str(tmp_path), TMP=str(tmp_path))
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(SCRIPT), "-Port", PORT_LIBRE, "-StopOnly"],
            cwd=RACINE, env=env, capture_output=True, timeout=180)
        sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
        assert res.returncode == 0, sortie
        worker.wait(timeout=10)  # tué par la purge, sinon TimeoutExpired
        assert voisin.poll() is None, "le worker d'un autre serveur a été tué"
    finally:
        for proc in (worker, voisin):
            if proc.poll() is None:
                proc.kill()


def test_stop_relit_aussi_le_journal_horodate_d_un_lancement_verrouille(tmp_path) -> None:
    """Revue 2026-09-29 : un lancement dont le journal était verrouillé écrit
    dans uvicorn_dev_<port>_<horodatage>.log ; le lancement suivant ne relisait
    que le journal par défaut et laissait vivre le worker muet de celui-là."""
    mort = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    pid_mort = int(mort.stdout.strip())
    (tmp_path / f"uvicorn_dev_{PORT_LIBRE}_20260929_120000.log.err").write_text(
        f"INFO:     Started reloader process [{pid_mort}] using WatchFiles\n", encoding="utf-8")
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                               "multiprocessing", f"parent_pid={pid_mort}"])
    try:
        env = dict(os.environ, APP_AUTH_PASSWORD="mdp-de-test", TEMP=str(tmp_path), TMP=str(tmp_path))
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(SCRIPT), "-Port", PORT_LIBRE, "-StopOnly"],
            cwd=RACINE, env=env, capture_output=True, timeout=180)
        assert res.returncode == 0, (res.stdout + res.stderr).decode("utf-8", errors="replace")
        worker.wait(timeout=10)  # tué par la purge, sinon TimeoutExpired
    finally:
        if worker.poll() is None:
            worker.kill()


def test_stop_ne_garde_que_les_trois_journaux_horodates_les_plus_recents(tmp_path) -> None:
    """Revue 2026-09-30 : les journaux de repli s'accumulaient sans fin."""
    import time
    for i in range(5):
        for suffixe in (".log", ".log.err"):
            f = tmp_path / f"uvicorn_dev_{PORT_LIBRE}_2026093{i}_120000{suffixe}"
            f.write_text("x", encoding="utf-8")
            os.utime(f, (time.time() - (5 - i) * 60,) * 2)
    env = dict(os.environ, APP_AUTH_PASSWORD="mdp-de-test", TEMP=str(tmp_path), TMP=str(tmp_path))
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(SCRIPT), "-Port", PORT_LIBRE, "-StopOnly"],
        cwd=RACINE, env=env, capture_output=True, timeout=180)
    assert res.returncode == 0, (res.stdout + res.stderr).decode("utf-8", errors="replace")
    restants = sorted(p.name for p in tmp_path.glob(f"uvicorn_dev_{PORT_LIBRE}_*"))
    assert restants == sorted(f"uvicorn_dev_{PORT_LIBRE}_2026093{i}_120000{s}"
                              for i in (2, 3, 4) for s in (".log", ".log.err"))
