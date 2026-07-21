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
