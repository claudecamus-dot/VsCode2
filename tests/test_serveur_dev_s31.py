"""Story 3.1, AC3.1.5 — scripts/serveur-dev.ps1 (findings D-6, D-8, D-9).

Functions only (SERVEUR_DEV_FONCTIONS_SEULES=1, dot-sourcing), fake port 8099,
fake history file: nothing is purged, nothing is launched.
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

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None,
    reason="script PowerShell 5.1 propre au poste Windows",
)


def _ps(expression: str, env_sup: dict | None = None, timeout: int = 120) -> str:
    env = dict(os.environ, SERVEUR_DEV_FONCTIONS_SEULES="1", APP_AUTH_PASSWORD="mdp-de-test",
               SERVEUR_DEV_PYTHON=sys.executable, **(env_sup or {}))
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         f". '{SCRIPT}' -Port 8099; {expression}"],
        cwd=RACINE, env=env, capture_output=True, timeout=timeout)
    sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
    assert res.returncode == 0, sortie
    return sortie.strip()


def test_descendants_bornes_ne_prennent_pas_le_worker_d_un_autre_serveur() -> None:
    """D-6: parent_pid=<racine>9 is ANOTHER server's worker, never a descendant."""
    racine = 2147480001  # never a live PID: only the spawn marker can link
    vrai = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                             "multiprocessing", f"parent_pid={racine}"])
    faux = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                             "multiprocessing", f"parent_pid={racine}9"])
    try:
        sortie = _ps(f"(@(Get-DescendantsProcessus -Racines @({racine})) | "
                     "ForEach-Object { [int]$_.ProcessId }) -join ','")
        ids = sortie.splitlines()[-1].split(",") if sortie else []
        assert str(vrai.pid) in ids, sortie
        assert str(faux.pid) not in ids, "le worker d'un AUTRE serveur serait purge"
    finally:
        for p in (vrai, faux):
            p.kill()
            p.wait(timeout=30)


def test_python_borne_hors_delai_marque_l_etat_indetermine() -> None:
    """D-8 (1/2): a timeout sets the « indéterminé » flag, an answer does not."""
    sortie = _ps("$script:pythonIndetermine = $false; "
                 "$null = Invoke-PythonBorne -Code 'import time; time.sleep(30)' -DelaiS 3; "
                 "$a = $script:pythonIndetermine; $script:pythonIndetermine = $false; "
                 "$null = Invoke-PythonBorne -Code 'print(1)' -DelaiS 60; "
                 "\"$a $($script:pythonIndetermine)\"")
    assert sortie.splitlines()[-1] == "True False", sortie


def test_preuve_python_indeterminee_ne_purge_pas_un_serveur_qui_repond() -> None:
    """D-8 (2/2): -KeepIfFresh on a responding, old server whose python freshness
    proof timed out decides « indetermine » (nothing purged), not « purger »."""
    faux = ("function Test-PortRepond { param([int]$NumPort) $true }; "
            "function Test-ContenuFrais { param([int]$NumPort) $true }; "
            "function Get-CodeRouteProtegee { param([int]$NumPort, [string]$Mdp) 200 }; "
            "function Get-AgeServeurS { param([int]$NumPort) 5000 }; "
            "function Get-AgeModifAppS { 9999 }; "
            "function Get-CpuServeurS { param([int]$NumPort) 1 }; "
            "function Get-OctetsHttp { param($Url, $Bearer) "
            "[System.Text.Encoding]::UTF8.GetBytes('{\"empreinte\":\"abc\"}') }; ")
    # Stand-in for a TIMEOUT of the real Invoke-PythonBorne (its flag is proven above).
    hors_delai = "function Invoke-PythonBorne { param($Code, $DelaiS) $script:pythonIndetermine = $true; $null }; "
    rend_autre = "function Invoke-PythonBorne { param($Code, $DelaiS) 'autre-empreinte' }; "
    assert _ps(faux + hors_delai + "Resolve-DecisionKeepIfFresh -NumPort 8099").splitlines()[-1] \
        == "indetermine"
    # Contrepart: a real « stale » answer still purges.
    assert _ps(faux + rend_autre + "Resolve-DecisionKeepIfFresh -NumPort 8099").splitlines()[-1] \
        == "purger"


def test_historique_horodate_et_fin_du_err(tmp_path) -> None:
    """D-9: launches/deaths are traced with a timestamp; a dead server's .err tail is readable."""
    hist = tmp_path / "hist.log"
    err = tmp_path / "u.log.err"
    err.write_text("\n".join(f"ligne {i}" for i in range(30)) + "\nSchemaEnRetard: migrer\n",
                   encoding="utf-8")
    sortie = _ps(f"Write-Historique 'mort port=8099 code=3'; Get-FinJournalErr -Chemin '{err}' -NbLignes 3",
                 {"SERVEUR_DEV_HISTORIQUE": str(hist)})
    assert "SchemaEnRetard: migrer" in sortie and "ligne 10" not in sortie, sortie
    ligne = hist.read_text(encoding="utf-8").strip()
    import re
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d pid=\d+ mort port=8099 code=3$", ligne), ligne


def test_un_serveur_mort_au_demarrage_n_est_pas_dit_lent() -> None:
    """D-9: the health-check has an ExitCode branch BEFORE the « démarré lentement » one."""
    texte = SCRIPT.read_text(encoding="utf-8-sig")
    i_mort = texte.index("if (-not $pret -and $proc.HasExited)")
    i_lent = texte.index("PEUT-ÊTRE simplement démarré lentement")
    assert i_mort < i_lent
    assert "$proc.ExitCode" in texte[i_mort:i_lent]
