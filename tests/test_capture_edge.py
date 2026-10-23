"""scripts/capture_edge.py sans Edge reel : faux lanceur injecte."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "capture_edge", Path(__file__).resolve().parent.parent / "scripts" / "capture_edge.py")
ce = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ce)


class FauxPopen:
    instances: list = []
    mode = "ok"

    def __init__(self, cmd, **kw):
        self.cmd, self.kw, self.pid = cmd, kw, 4242
        self.timeout = None
        self.mode = FauxPopen.mode
        FauxPopen.instances.append(self)

    def wait(self, timeout=None):
        self.timeout = timeout
        if self.mode == "timeout":
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        if self.mode == "boom":
            raise KeyboardInterrupt
        return 0


@pytest.fixture
def faux(monkeypatch):
    FauxPopen.instances = []
    FauxPopen.mode = "ok"
    tues = []
    monkeypatch.setattr(ce, "_popen", FauxPopen)
    monkeypatch.setattr(ce, "_tuer_arbre", tues.append)
    monkeypatch.setattr(ce, "trouver_edge", lambda: "edge.exe")
    return tues


def _profil(p):
    return next(a.split("=", 1)[1] for a in p.cmd if a.startswith("--user-data-dir="))


def test_arbre_tue_sur_timeout(faux):
    FauxPopen.mode = "timeout"
    with pytest.raises(TimeoutError):
        ce.capturer("http://x", "o.png", delai_s=3)
    assert faux == [4242]


def test_arbre_tue_sur_exception(faux):
    FauxPopen.mode = "boom"
    with pytest.raises(KeyboardInterrupt):
        ce.capturer("http://x", "o.png")
    assert faux == [4242]


def test_profil_neuf_distinct_et_supprime(faux):
    ce.capturer("http://x", "o.png")
    ce.capturer("http://x", "o.png")
    p1, p2 = (_profil(p) for p in FauxPopen.instances)
    assert p1 != p2
    assert not Path(p1).exists() and not Path(p2).exists()


def test_profil_supprime_sur_exception(faux):
    FauxPopen.mode = "timeout"
    with pytest.raises(TimeoutError):
        ce.capturer("http://x", "o.png")
    assert not Path(_profil(FauxPopen.instances[0])).exists()


def test_cdp_port_zero_jamais_fixe(faux):
    with ce.session_cdp() as (proc, profil):
        ports = [a for a in proc.cmd if a.startswith("--remote-debugging-port")]
    assert ports == ["--remote-debugging-port=0"]
    assert faux == [4242] and not Path(profil).exists()


def test_capture_sans_cdp_pas_de_port(faux):
    ce.capturer("http://x", "o.png")
    assert not [a for a in FauxPopen.instances[0].cmd if "remote-debugging" in a]


def test_timeout_transmis_et_budget_virtuel(faux):
    ce.capturer("http://x", "o.png", delai_s=7)
    p = FauxPopen.instances[0]
    assert p.timeout == 7
    assert any(a.startswith("--virtual-time-budget=") for a in p.cmd)
    assert "--screenshot=o.png" in p.cmd
