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


def test_import_du_skill_par_sys_path_reussit_et_pointe_le_dossier_du_skill() -> None:
    """Chemin NOMINAL : l'import par sys.path doit trouver le skill du dépôt.
    Sans ce test, un dossier renommé/déplacé bascule tous les intercalaires en
    texte seul sans qu'aucun test ne rougisse."""
    import app.services.pptx_export.images as images
    from pathlib import Path

    importlib.reload(images)
    attendu = Path(images.__file__).resolve().parents[3] / ".claude" / "skills" / "pptx-framed-image" / "scripts"
    assert images._FRAMED_OK is True
    assert str(attendu) in sys.path
    for nom in ("_nature_images", "_stock_images"):
        assert Path(getattr(images, nom).__file__).resolve().parent == attendu
    assert Path(images._place_image_in_frame.__code__.co_filename).resolve().parent == attendu
    assert callable(images._cover_crop_to_aspect)
