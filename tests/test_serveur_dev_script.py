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
