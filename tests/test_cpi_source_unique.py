"""YUI-1 : les calibrations cpi n'existent qu'une fois (pptx_deck), et le
verificateur de debordement controle avec la constante qui dimensionne les boites."""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from app.services import pptx_deck as D
from app.services.pptx_export import slides_diagnostic, slides_trajectoire


def test_verificateur_et_boites_partagent_le_cpi_pessimiste() -> None:
    defaut = inspect.signature(D.verifier_debordements_texte).parameters["cpi_pessimiste"].default
    assert defaut is D.CPI_PESSIMISTE
    assert slides_trajectoire._KPI_CPI is D.CPI_PESSIMISTE
    assert slides_diagnostic._MAT_CPI_BOITE is D.CPI_PESSIMISTE
    assert slides_trajectoire._LAYOUT_CPI is D.CPI_LAYOUT
    assert slides_diagnostic._MAT_CPI_LAYOUT is D.CPI_LAYOUT


def test_les_estimateurs_generiques_partagent_le_cpi_nominal() -> None:
    for fonction in (D.estimer_lignes, D.ajuster_police, D.tronquer_a_lignes):
        defaut = inspect.signature(fonction).parameters["cpi_ref"].default
        assert defaut is D.CPI_NOMINAL, fonction.__name__
    assert D.CPI_NOMINAL != D.CPI_PESSIMISTE and D.CPI_NOMINAL != D.CPI_LAYOUT


def test_aucune_copie_litterale_des_cpi_hors_pptx_deck() -> None:
    racine = Path(D.__file__).resolve().parents[1]
    motif = re.compile(r"(cpi\w*\s*=\s*|cpi_ref=)(10\.7|11\.0|12\.5)\b", re.IGNORECASE)
    fautifs = [
        f"{p}:{n}"
        for p in racine.rglob("*.py")
        if p.resolve() != Path(D.__file__).resolve()
        for n, ligne in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if motif.search(ligne)
    ]
    assert fautifs == []
