"""Le repli texte-seul des intercalaires (skill pptx-framed-image absent ou cassé)
doit laisser une trace WARNING : un repli silencieux change le livrable sans signal
(audit VSCode2, robustesse/risque_technique 2026-10-04)."""
from __future__ import annotations

import importlib
import logging
import sys

import pytest


def test_repli_texte_seul_journalise_un_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import app.services.pptx_export.images as images

    # None dans sys.modules force ImportError à l'import du skill.
    monkeypatch.setitem(sys.modules, "nature_images", None)
    try:
        with caplog.at_level(logging.WARNING, logger=images.logger.name):
            importlib.reload(images)
        assert images._FRAMED_OK is False
        assert any("pptx-framed-image indisponible" in r.getMessage() for r in caplog.records)
    finally:
        monkeypatch.undo()
        importlib.reload(images)
