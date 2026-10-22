"""Garde-fous de `vider_recordings_de_test` (tests/conftest.py).

Liste blanche stricte : la fonction SUPPRIME des fichiers ; elle ne doit jamais
toucher un dossier de donnees reel (revues adversariales du 2026-10-19)."""
from pathlib import Path

import pytest

import conftest
from conftest import vider_recordings_de_test


def _fabriquer(base: Path) -> Path:
    (base / "recordings").mkdir(parents=True)
    f = base / "recordings" / "1_x.webm"
    f.write_bytes(b"x")
    return f


def test_vide_le_dossier_de_la_base_de_test_du_conftest(tmp_path, monkeypatch):
    f = _fabriquer(tmp_path / "base")
    base_test = tmp_path / "base" / "interview_to_deck_test.db"
    monkeypatch.setattr(conftest, "_TEST_DB", str(base_test))
    monkeypatch.setenv("APP_DB_PATH", str(base_test))
    monkeypatch.delenv("APP_TEST_CLEAN_RECORDINGS", raising=False)
    vider_recordings_de_test()
    assert not f.exists()


def test_vide_la_base_de_test_du_conftest_meme_avec_des_bases_voisines(tmp_path, monkeypatch):
    # Le temporaire systeme est partage : d'autres outils y posent des `*.db`.
    # Cela ne doit pas empecher de vider le `recordings/` de LA base de test.
    f = _fabriquer(tmp_path / "base")
    (tmp_path / "base" / "autre_outil.db").write_bytes(b"sqlite")
    base_test = tmp_path / "base" / "interview_to_deck_test.db"
    monkeypatch.setattr(conftest, "_TEST_DB", str(base_test))
    monkeypatch.setenv("APP_DB_PATH", str(base_test))
    monkeypatch.delenv("APP_TEST_CLEAN_RECORDINGS", raising=False)
    vider_recordings_de_test()
    assert not f.exists()


def test_refuse_la_base_de_test_si_un_app_db_est_a_cote(tmp_path, monkeypatch):
    # TMP/TEMP pose sur un dossier de donnees : `_TEST_DB` y atterrit. Un `app.db`
    # voisin dit que ce n'est pas un temporaire partage -> on ne vide rien.
    f = _fabriquer(tmp_path / "donnees")
    (tmp_path / "donnees" / "app.db").write_bytes(b"sqlite")
    base_test = tmp_path / "donnees" / "interview_to_deck_test.db"
    monkeypatch.setattr(conftest, "_TEST_DB", str(base_test))
    monkeypatch.setenv("APP_DB_PATH", str(base_test))
    monkeypatch.delenv("APP_TEST_CLEAN_RECORDINGS", raising=False)
    vider_recordings_de_test()
    assert f.exists()


def test_refuse_un_dossier_recordings_qui_est_un_lien(tmp_path, monkeypatch):
    # Un lien/une jonction `recordings` peut pointer vers l'audio d'un AUTRE depot.
    cible = tmp_path / "ailleurs"
    cible.mkdir()
    f = cible / "1_x.webm"
    f.write_bytes(b"x")
    base = tmp_path / "base"
    base.mkdir()
    try:
        (base / "recordings").symlink_to(cible, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Windows sans privilege : une jonction se cree sans droit particulier.
        import subprocess
        import sys

        if sys.platform != "win32":
            pytest.skip("liens symboliques non autorises sur ce poste")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(base / "recordings"), str(cible)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip(f"ni lien ni jonction possibles : {r.stderr.strip()}")
    base_test = base / "interview_to_deck_test.db"
    monkeypatch.setattr(conftest, "_TEST_DB", str(base_test))
    monkeypatch.setenv("APP_DB_PATH", str(base_test))
    vider_recordings_de_test()
    assert f.exists()


def test_vide_un_dossier_explicitement_autorise(tmp_path, monkeypatch):
    f = _fabriquer(tmp_path / "base")
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "base" / "t.db"))
    monkeypatch.setenv("APP_TEST_CLEAN_RECORDINGS", "1")
    vider_recordings_de_test()
    assert not f.exists()


def test_ne_supprime_rien_sans_autorisation_ni_base_de_test(tmp_path, monkeypatch):
    # Meme sous le temporaire systeme : une base qui n'est pas CELLE du conftest
    # (copie de sauvegarde posee dans %TEMP%) ne doit jamais voir son audio effacé.
    f = _fabriquer(tmp_path / "copie")
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "copie" / "copie.db"))
    monkeypatch.delenv("APP_TEST_CLEAN_RECORDINGS", raising=False)
    vider_recordings_de_test()
    assert f.exists()


def test_refuse_une_autre_base_voisine_meme_autorisee(tmp_path, monkeypatch):
    f = _fabriquer(tmp_path / "donnees")
    (tmp_path / "donnees" / "app.db").write_bytes(b"sqlite")
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "donnees" / "t.db"))
    monkeypatch.setenv("APP_TEST_CLEAN_RECORDINGS", "1")
    vider_recordings_de_test()
    assert f.exists()


def test_refuse_une_copie_de_base_voisine_meme_autorisee(tmp_path, monkeypatch):
    f = _fabriquer(tmp_path / "sandbox")
    (tmp_path / "sandbox" / "copie.db").write_bytes(b"sqlite")
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "sandbox" / "t.db"))
    monkeypatch.setenv("APP_TEST_CLEAN_RECORDINGS", "1")
    vider_recordings_de_test()
    assert f.exists()


def test_refuse_le_data_reel_du_depot(monkeypatch):
    reel = Path(__file__).resolve().parent.parent / "data"
    monkeypatch.setenv("APP_DB_PATH", str(reel / "t.db"))
    monkeypatch.setenv("APP_TEST_CLEAN_RECORDINGS", "1")
    appels = []
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: appels.append(self))
    vider_recordings_de_test()
    assert appels == []


def test_refuse_un_chemin_vide_ou_relatif(monkeypatch):
    appels = []
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: appels.append(self))
    monkeypatch.setenv("APP_TEST_CLEAN_RECORDINGS", "1")
    for valeur in ("", "   ", "t.db", "sous/dossier/t.db"):
        monkeypatch.setenv("APP_DB_PATH", valeur)
        vider_recordings_de_test()
    assert appels == []
