"""Isolation de la base pour la suite de tests.

Doit s'exécuter **avant** tout import de `app.*` : pytest importe `conftest.py`
en premier, donc fixer `APP_DB_PATH` ici garantit que `app.db` crée son engine
sur une base jetable (et non sur `data/app.db`, la base de dev/prod).
"""
from __future__ import annotations

import os
import tempfile

# Base SQLite dédiée aux tests, dans le répertoire temporaire du système.
_TEST_DB = os.path.join(tempfile.gettempdir(), "interview_to_deck_test.db")
os.environ.setdefault("APP_DB_PATH", _TEST_DB)

# Images des têtes de chapitre : jamais de fetch réseau en test (offline,
# déterministe, rapide) — génération procédurale locale. cf. pptx_export.
os.environ.setdefault("PPTX_NO_PHOTO_FETCH", "1")


def vider_recordings_de_test() -> None:
    """Vide le répertoire d'enregistrements DE TEST.

    Rien ne l'a jamais nettoyé : les fichiers s'accumulaient d'un run à l'autre
    (178 constatés le 2026-09-01). C'était latent tant que le code supprimait
    lui-même les imports aboutis ; depuis que l'audio ne se supprime plus que
    par une action de l'utilisateur — la règle du produit — chaque run en
    laisse, et `test_mission_backups` (qui affirme un inventaire EXACT de
    `RECORDINGS_DIR`) échoue sur les résidus. Un échec de ce genre est le pire
    à diagnostiquer : il ne se reproduit pas en isolant le test, et il accuse
    un code qui n'a rien fait.

    Appelée au démarrage de la session (résidus du run PRÉCÉDENT) ET par le
    `setup_module` de `test_mission_backups` : les imports de la MEME session
    s'accumulent aussi — ils portent désormais le préfixe `1_import_…`, donc la
    mission n° 1 de ce module-là se les voit attribuer comme orphelins.

    Garde-fou : on ne touche qu'un répertoire situé sous le temporaire système
    et dérivé d'`APP_DB_PATH`. Jamais `data/recordings`, qui porte les
    enregistrements réels de l'utilisateur (une confusion de ce type a déjà
    coûté des données sur ce projet)."""
    from pathlib import Path

    recordings = Path(os.environ["APP_DB_PATH"]).parent / "recordings"
    temp = Path(tempfile.gettempdir()).resolve()
    try:
        if temp not in recordings.resolve().parents:
            return  # pas sous le temporaire système : on ne touche à rien
    except OSError:
        return
    for chemin in recordings.glob("*"):
        if chemin.is_file():
            try:
                chemin.unlink()
            except OSError:
                pass  # verrou Windows : le run suivant le reprendra


def pytest_sessionstart(session):  # noqa: ARG001
    """Nettoyage au démarrage de la session."""
    vider_recordings_de_test()


# --------------------------------------------------------------------------- #
# Windows : neutraliser le crash de nettoyage tmp de fin de session de pytest.
# Le housekeeping de `pytest_sessionfinish` supprime la jonction `pytest-current`
# sous %TEMP%\pytest-of-<user>\ ; sur cette machine l'unlink lève par intermittence
# `PermissionError [WinError 5]`. Levée DANS `pytest_sessionfinish`, l'exception
# supprime la ligne de synthèse `=== N passed ===` ET force un exit code 1 alors
# que tous les tests passent — 3 fausses alertes rien qu'au 2026-07-21 (cf. mémoire
# feedback-pytest-windows-teardown-noise). On rend ce ménage non fatal, sans toucher
# à la gestion normale des tmp de pytest (auto-nettoyage des anciens dossiers). Garde-
# fou : si l'API privée `_pytest.pathlib` bouge, le try/except laisse le comportement
# d'origine (le bruit revient, mais rien ne casse).
try:
    import _pytest.pathlib as _pytest_pathlib

    _orig_cleanup_dead_symlinks = _pytest_pathlib.cleanup_dead_symlinks

    def _cleanup_dead_symlinks_safe(root):
        try:
            _orig_cleanup_dead_symlinks(root)
        except OSError:
            pass  # WinError 5 sur pytest-current : housekeeping, pas un échec de test

    _pytest_pathlib.cleanup_dead_symlinks = _cleanup_dead_symlinks_safe
except Exception:  # pragma: no cover - garde-fou si l'API interne de pytest change
    pass
