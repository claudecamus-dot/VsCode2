"""Story 3.1, AC3.1.3 (finding D-2): a LIVE holder keeps its test-DB lock
whatever its age.

The lock was judged stale after 45 min even when its holder was alive and
identified (PID + start time): a second pytest stole it during a 2 h 35 full
suite (2026-10-20) and the WinError 32 cascade came back. Age is now only the
fallback when the holder's identity cannot be established.
"""
from __future__ import annotations

import os
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


def test_un_detenteur_vivant_mais_bloque_depuis_13h_est_repris_et_nomme(monkeypatch, tmp_path):
    """A hung pytest must not block every run forever: 12 h ceiling, and the
    refusal names the PID to kill."""
    import conftest

    verrou = tmp_path / "pendu.db.verrou"
    monkeypatch.setattr(conftest, "_VERROU", verrou)
    vivant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        debut = conftest._debut_processus(vivant.pid)
        if debut is None:
            pytest.skip("instant de demarrage indeterminable sur cette plateforme")
        verrou.write_text(f"{vivant.pid} {time.time() - 13 * 3600} {debut}", encoding="utf-8")
        with pytest.warns(UserWarning, match=rf"PID {vivant.pid}"):
            assert conftest._detenteur_actif(conftest._lire_le_verrou()) is False
        with pytest.raises(RuntimeError, match=rf"taskkill /F /T /PID {vivant.pid}"):
            conftest._refuser(vivant.pid, 60.0)
    finally:
        vivant.kill()
        vivant.wait(timeout=30)


# --------------------------------------------------------------------------- #
# Story 3.1 final review — F6, through the real path (_prendre_le_verrou)
# --------------------------------------------------------------------------- #
@pytest.fixture
def detenteur_vivant(monkeypatch, tmp_path):
    import conftest

    verrou = tmp_path / "base.db.verrou"
    monkeypatch.setattr(conftest, "_VERROU", verrou)
    monkeypatch.delenv("PYTEST_SANS_VERROU", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    vivant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        debut = conftest._debut_processus(vivant.pid)
        if debut is None:
            pytest.skip("instant de demarrage indeterminable sur cette plateforme")
        yield conftest, verrou, vivant.pid, debut
    finally:
        vivant.kill()
        vivant.wait(timeout=30)


def test_identite_illisible_d_un_pid_vivant_ne_vole_pas_le_verrou(detenteur_vivant, monkeypatch):
    """F6a: OpenProcess denied (elevated holder, non-elevated runner) makes the
    start time unreadable for a LIVE pid; the 45 min fallback then stole the
    lock of a 2 h suite (D-2 again)."""
    conftest, verrou, pid, debut = detenteur_vivant
    reel = conftest._debut_processus
    monkeypatch.setattr(conftest, "_debut_processus",
                        lambda p: None if p == pid else reel(p))
    verrou.write_text(f"{pid} {time.time() - 2 * 3600} {debut}", encoding="utf-8")
    with pytest.raises(RuntimeError, match=rf"PID {pid}"):
        conftest._prendre_le_verrou()
    assert verrou.read_text(encoding="utf-8").split()[0] == str(pid), "verrou vole"


def test_reprise_au_dela_du_plafond_est_signalee_avec_le_pid(detenteur_vivant):
    """F6b: beyond the 12 h ceiling the takeover happens, but loudly."""
    conftest, verrou, pid, debut = detenteur_vivant
    verrou.write_text(f"{pid} {time.time() - 13 * 3600} {debut}", encoding="utf-8")
    with pytest.warns(UserWarning, match=rf"PID {pid}.*taskkill /F /T /PID {pid}"):
        conftest._prendre_le_verrou()
    assert verrou.read_text(encoding="utf-8").split()[0] == str(os.getpid())
