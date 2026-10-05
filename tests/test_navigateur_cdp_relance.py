"""Une seule relance du lancement + connexion CDP (décision du 2026-10-05).

Finding VSCode2:test-e2e-enregistrement-libre-instable-ci : le handshake
websocket expire par intermittence en CI. Aucun navigateur réel ici : Popen,
l'attente DevTools et la plomberie CDP sont remplacés par des faux.
"""
from __future__ import annotations

import navigateur_cdp as nav
import pytest


class _FauxProc:
    def __init__(self, journal):
        self.journal = journal

    def poll(self):
        return None

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def faux(monkeypatch, tmp_path):
    etat = {"lancements": 0, "tues": 0, "echecs": [], "sleeps": []}

    def popen(cmd, stdout=None, stderr=None):
        etat["lancements"] += 1
        stdout.write(f"journal-lancement-{etat['lancements']}\n")
        stdout.flush()
        return _FauxProc(stdout)

    def attendre(self):
        n = etat["lancements"]
        if n in etat["echecs"]:
            raise TimeoutError(f"timed out during opening handshake (essai {n})")
        return "ws://127.0.0.1:1/x", 1

    monkeypatch.setattr(nav.subprocess, "Popen", popen)
    monkeypatch.setattr(nav.Navigateur, "_attendre_devtools", attendre)
    monkeypatch.setattr(nav.Navigateur, "_run", lambda self, c: {"processInfo": []})
    monkeypatch.setattr(nav.Navigateur, "_cmd", lambda self, *a, **k: None)
    monkeypatch.setattr(nav.Navigateur, "_cmd_sur", lambda self, *a, **k: None)
    monkeypatch.setattr(nav.Navigateur, "_cible_page", lambda self, port: "ws://x")
    monkeypatch.setattr(nav, "_attendre_fin_processus", lambda pid, d: True)
    monkeypatch.setattr(
        nav, "_tuer_par_profil", lambda profil: etat.__setitem__("tues", etat["tues"] + 1)
    )
    monkeypatch.setattr(nav.time, "sleep", lambda s: etat["sleeps"].append(s))
    etat["profil"] = tmp_path / "p"
    return etat


def test_premier_lancement_en_echec_declenche_une_seconde_tentative(faux):
    faux["echecs"] = [1]
    b = nav.Navigateur("x", faux["profil"])
    assert faux["lancements"] == 2
    assert faux["tues"] >= 1, "le premier navigateur doit etre tue avant la relance"
    assert all(s <= 2 for s in faux["sleeps"])
    b.fermer()


def test_deux_echecs_portent_le_journal_du_premier(faux):
    faux["echecs"] = [1, 2]
    with pytest.raises(RuntimeError) as exc:
        nav.Navigateur("x", faux["profil"])
    msg = str(exc.value)
    assert faux["lancements"] == 2, "une seule relance, pas de boucle"
    assert "essai 1" in msg and "journal-lancement-1" in msg
    assert "essai 2" in msg


def test_premier_succes_ne_relance_pas(faux):
    b = nav.Navigateur("x", faux["profil"])
    assert faux["lancements"] == 1
    assert faux["sleeps"] == []
    b.fermer()
