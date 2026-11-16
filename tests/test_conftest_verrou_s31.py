"""Story 3.1, AC3.1.3 (finding D-2): a LIVE holder keeps its test-DB lock
whatever its age.

The lock was judged stale after 45 min even when its holder was alive and
identified (PID + start time): a second pytest stole it during a 2 h 35 full
suite (2026-10-20) and the WinError 32 cascade came back. Age is now only the
fallback when the holder's identity cannot be established.
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest


def test_un_detenteur_vivant_garde_son_verrou_au_dela_de_45_min(monkeypatch, tmp_path):
    import conftest

    verrou = tmp_path / "vieux.db.verrou"
    monkeypatch.setattr(conftest, "_VERROU", verrou)
    vivant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        debut = conftest._debut_processus(vivant.pid)
        if debut is None:
            pytest.skip("instant de demarrage indeterminable sur cette plateforme")
        il_y_a_2h = time.time() - 2 * 3600
        verrou.write_text(f"{vivant.pid} {il_y_a_2h} {debut}", encoding="utf-8")
        assert conftest._detenteur_actif(conftest._lire_le_verrou()) is True, (
            "le verrou d'une suite VIVANTE de 2 h est vole : cascade WinError 32"
        )
        # Fallback kept: identity UNKNOWN (old 2-field lock) -> age decides.
        verrou.write_text(f"{vivant.pid} {il_y_a_2h}", encoding="utf-8")
        assert conftest._detenteur_actif(conftest._lire_le_verrou()) is False
        verrou.write_text(f"{vivant.pid} {time.time()}", encoding="utf-8")
        assert conftest._detenteur_actif(conftest._lire_le_verrou()) is True
        # A recycled PID (different start) still frees the lock, old or fresh.
        verrou.write_text(f"{vivant.pid} {il_y_a_2h} {debut + 1}", encoding="utf-8")
        assert conftest._detenteur_actif(conftest._lire_le_verrou()) is False
    finally:
        vivant.kill()
        vivant.wait(timeout=30)
