"""Le pilote CDP sans navigateur : ce qui se décide AVANT de lancer quoi que ce
soit doit être testé même sur un poste sans Chromium (revue du 2026-09-09, A5 —
554 lignes d'infrastructure qui n'étaient exécutées que par des modules sautés
sans navigateur)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from navigateur_cdp import _CHEMINS_NAVIGATEUR, Navigateur, trouver_navigateur


def test_e2e_navigateur_absent_est_une_erreur_pas_un_skip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Une faute de frappe dans la CI ne doit pas rendre la suite verte sans
    jamais jouer le parcours."""
    monkeypatch.setenv("E2E_NAVIGATEUR", str(tmp_path / "absent.exe"))
    with pytest.raises(RuntimeError, match="E2E_NAVIGATEUR"):
        trouver_navigateur()


def test_e2e_navigateur_present_est_retenu_tel_quel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    exe = tmp_path / "faux-chromium.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("E2E_NAVIGATEUR", str(exe))
    assert trouver_navigateur() == str(exe)


def test_un_profil_trop_long_est_refuse_avant_tout_lancement(tmp_path: Path) -> None:
    """Edge sortirait sans un mot : on refuse avec la raison, sans créer le
    dossier ni lancer d'exécutable (le chemin d'exécutable donné n'existe pas)."""
    profil = tmp_path / ("p" * 200)
    with pytest.raises(ValueError, match="trop long"):
        Navigateur(str(tmp_path / "inexistant.exe"), profil)
    assert not profil.exists()


def test_les_erreurs_ignorent_les_requetes_propres_au_navigateur() -> None:
    """`/favicon.ico` : demandé par tout navigateur, jamais servi par l'app —
    exclu des 4xx ET des échecs réseau, pas seulement des premiers."""
    nav = Navigateur.__new__(Navigateur)
    nav.reponses = [
        {"status": 404, "url": "http://127.0.0.1:1/favicon.ico?v=2", "type": "Other"},
        {"status": 404, "url": "http://127.0.0.1:1/missions/999", "type": "Document"},
        {"status": 200, "url": "http://127.0.0.1:1/missions", "type": "Document"},
    ]
    nav.echecs_reseau = [{"url": "http://127.0.0.1:1/static/app.css", "erreur": "net::ERR_FAILED", "type": "Stylesheet"}]
    assert [e["url"] for e in nav.erreurs()] == [
        "http://127.0.0.1:1/missions/999", "http://127.0.0.1:1/static/app.css",
    ]
    assert "/favicon.ico" in _CHEMINS_NAVIGATEUR


@pytest.mark.skipif(sys.platform != "win32", reason="chemins Windows")
def test_la_limite_de_profil_laisse_passer_les_dossiers_courts(tmp_path: Path) -> None:
    """Un profil court ne doit pas être refusé par la garde — le constructeur
    échoue alors plus loin, sur l'exécutable absent, et NETTOIE derrière lui."""
    profil = Path(r"C:\tmp") / "e2e-court" / "profil"
    with pytest.raises(FileNotFoundError):
        Navigateur(str(tmp_path / "inexistant.exe"), profil)
