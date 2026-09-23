"""Filets « chrome du gabarit » de l'export PPT de production (audit 2026-09-21).

`verifier_plancher_de_dessin` et `verifier_chrome_gabarit` sont portés de la
skill pptx-deck dans `app.services.pptx_deck` et APPELÉS par
`build_presentation` avec la même politique que `verifier_geometrie` (échec
bruyant). Chaque test ci-dessous tombe si l'appel correspondant est retiré de
build.py : il provoque le défaut que le filet doit attraper et exige l'échec.
"""
from __future__ import annotations

import pytest

import test_deck_qualite as T
from app.services import pptx_deck as D
from app.services.pptx_export import build as B


def setup_module() -> None:
    T.setup_module()


def teardown_module() -> None:
    T.teardown_module()


def test_deck_reel_ne_recouvre_pas_le_numero_de_page() -> None:
    # Deck complet (18 slides) : aucun recouvrement du badge lu sur le gabarit.
    prs = T._prs_complete()
    compte: dict = {}
    assert D.verifier_chrome_gabarit(prs, compte=compte) == []
    assert compte["examinees"] > 0
    zones = D.zones_numero_page(prs.slides[1])
    assert zones and all(z != D._ZONE_NUMERO_PAGE_IN for z in zones)  # lue, pas le repli


def test_build_echoue_si_une_forme_recouvre_le_badge(monkeypatch) -> None:
    origine = B._slide_swot

    def swot_sur_le_badge(prs, swot):
        origine(prs, swot)
        D.add_rect(prs.slides[-1], 8.9, 4.8, 0.8, 0.5, fill="#FF0000")

    monkeypatch.setattr(B, "_slide_swot", swot_sur_le_badge)
    with pytest.raises(RuntimeError, match="n° de page"):
        T._prs_complete()


def test_build_echoue_si_le_plancher_decroche_du_gabarit(monkeypatch) -> None:
    monkeypatch.setattr(B, "_PLANCHER_DESSIN_IN", B._H_IN - 0.40)
    with pytest.raises(RuntimeError, match="plancher"):
        T._prs_complete()


def test_plancher_de_production_reste_au_dessus_du_badge() -> None:
    prs = T._prs_complete()
    assert D.verifier_plancher_de_dessin(prs, B._PLANCHER_DESSIN_IN) == []
    assert D.verifier_plancher_de_dessin(prs, B._H_IN - 0.40)  # le filet sait crier
