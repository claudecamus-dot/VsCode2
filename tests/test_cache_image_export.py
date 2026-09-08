"""Cache image de l'export PPTX (findings audit-technique performance:critique
du 2026-09-04, `.claude/audits/VSCode2.json` côté hub) :

- « Clé de cache image instable : la base intègre px_h = round(900/aspect)
  dérivé de la géométrie vivante de la zone, donc 1 px d'écart crée une entrée
  neuve — prouvé sur disque par zone_forest_12_900x1310_proc.png et
  _900x1311_proc.png, 38 _proc pour 27 _photo, 20+ orphelins qui relancent le
  fetch à chaque export ».
- « Export PPTX : sur miss du cache photo, 2 variantes x 2 tentatives = 4
  fetch_to de 35 s = jusqu'à 140 s par zone image, en série, sans aucun cache
  négatif d'échec ».

Échec sur le code d'avant : la clé portait `900x1310` / `900x1311`, donc deux
entrées ; et chaque zone repayait ses 4 tentatives.
"""
from __future__ import annotations

import pytest

import app.services.pptx_export.images as px


# Valeurs RÉELLES relevées dans data/pptx_chapitre_images le 2026-09-08 — pas
# des nombres inventés pour faire passer le test.
_ASPECT_1310 = 900 / 1310
_ASPECT_1311 = 900 / 1311
_ASPECT_1277 = 900 / 1277
_ASPECT_910 = 900 / 910
_ASPECT_911 = 900 / 911


def test_un_pixel_d_ecart_ne_cree_plus_une_entree_neuve() -> None:
    """Les deux paires exactes citées par l'audit partagent désormais leur clé."""
    assert px._cle_cache_aspect(_ASPECT_1310) == px._cle_cache_aspect(_ASPECT_1311)
    assert px._cle_cache_aspect(_ASPECT_910) == px._cle_cache_aspect(_ASPECT_911)


def test_deux_formes_reellement_differentes_gardent_des_cles_distinctes() -> None:
    """Contre-épreuve : arrondir ne doit pas tout confondre, sinon une zone
    portrait recevrait l'image d'une zone paysage. 1277 px et 1310 px sont deux
    formes distinctes (0,70 contre 0,69), elles le restent."""
    assert px._cle_cache_aspect(_ASPECT_1277) != px._cle_cache_aspect(_ASPECT_1310)
    assert px._cle_cache_aspect(2.0) != px._cle_cache_aspect(0.5)


def test_la_cle_ne_depend_plus_de_la_hauteur_en_pixels() -> None:
    """C'était la cause racine : la clé dérivait de la géométrie vivante en
    pixels. Elle ne doit plus contenir de `900x…`."""
    assert "900x" not in px._cle_cache_aspect(_ASPECT_1310)


def test_un_echec_de_fetch_n_est_retente_qu_une_fois_par_processus(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Le cœur du finding des 140 s : plusieurs zones de même scène ne doivent
    plus repayer chacune leurs 4 tentatives réseau.

    Échec sur le code d'avant : `appels` valait 8 (2 zones x 2 variantes x 2
    tentatives) au lieu de 4."""
    if not px._FRAMED_OK:  # infra image absente : rien à vérifier ici
        pytest.skip("skill pptx-framed-image absent")

    appels: list[str] = []

    def _fetch_ko(dest, requete, **kwargs):  # noqa: ANN001, ARG001
        appels.append(requete)
        raise RuntimeError("Openverse injoignable")

    monkeypatch.setattr(px, "_IMG_CACHE", tmp_path)
    monkeypatch.setattr(px, "_ECHECS_FETCH", set())
    monkeypatch.setattr(px._stock_images, "fetch_to", _fetch_ko)
    monkeypatch.setattr(px._nature_images, "generate_to",
                        lambda dest, *a, **k: __import__("pathlib").Path(dest).write_bytes(b"x"))
    monkeypatch.delenv("PPTX_NO_PHOTO_FETCH", raising=False)

    for _ in range(2):  # deux zones, même scène, même requête, même orientation
        px._resoudre_image_cachee("z_1_a1.00", "forest", 1, 1.0, 10, 10, requete="green forest")

    assert len(appels) == 4, (
        f"4 tentatives attendues au total (2 variantes x 2 essais, une seule fois), "
        f"obtenu {len(appels)} : {appels}"
    )


def test_le_cache_negatif_ne_survit_pas_au_processus() -> None:
    """Garde-fou sur la garde : le cache négatif est en mémoire, jamais sur
    disque. Un échec réseau ne doit pas condamner la vraie photo au-delà de la
    session — c'est exactement la régression de 2026-07-22 (« images générées
    servies à vie ») que le slot `_proc` séparé existe pour éviter."""
    assert isinstance(px._ECHECS_FETCH, set)
    assert not any(
        chemin.name.startswith("_echec") for chemin in px._IMG_CACHE.glob("*")
    ) if px._FRAMED_OK else True
