"""Openverse injoignable ne coûte plus une pénalité par scène.

Constat d'audit du hub (performance, 2026-09-09) : le fetch photo est
« STRICTEMENT EN SÉRIE dans une route synchrone », et une banque d'images
injoignable coûte « de l'ordre de 4 scènes x 3 orientations x 140 s dans le
thread de la requête ».

Le cache négatif existant borne la RÉPÉTITION d'une même requête, pas le coût
total : un export pose des zones de scènes différentes, donc des requêtes
différentes, et chacune repaie ses 2 variantes x 2 tentatives. Le coupe-circuit
posé le 2026-09-10 arrête d'essayer après quelques ÉCHECS RÉSEAU.

Ces tests visent les trois pièges de ce genre de garde, tous trouvés par revue
adversariale sur la première version :
- elle s'ouvrait sur un réseau SAIN (une requête sans résultat CC0 compte comme
  une panne) ;
- son compteur ne redescendait jamais, donc le seuil « 2 » devenait 1 à vie ;
- ses tests passaient sur un circuit cassé (un `float("inf")` les laissait verts).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.pptx_export import images


@pytest.fixture(autouse=True)
def _circuit_neuf(monkeypatch, tmp_path):
    """Chaque test part d'un circuit fermé et d'un cache vierge — l'état vit au
    niveau du MODULE, donc il fuirait d'un test à l'autre sans cette remise à
    zéro. `raising=False` sur `_IMG_CACHE` : il n'est défini que si le skill
    `pptx-framed-image` est importable, et sans lui la fixture lèverait avant
    que le `skip` du corps ne puisse jouer."""
    monkeypatch.setattr(images, "_echecs_consecutifs", 0)
    monkeypatch.setattr(images, "_coupure_jusqu_a", 0.0)
    monkeypatch.setattr(images, "_ECHECS_FETCH", set())
    monkeypatch.setattr(images, "_IMG_CACHE", tmp_path / "cache", raising=False)
    monkeypatch.delenv("PPTX_NO_PHOTO_FETCH", raising=False)


def _brancher_echec(monkeypatch, exception: Exception) -> list:
    """Remplace le client réseau par un compteur qui lève `exception`."""
    if not images._FRAMED_OK:
        pytest.skip("skill pptx-framed-image absente : pas de chemin de fetch")
    tentatives = []

    def _echec(*a, **k):
        tentatives.append(a)
        raise exception

    monkeypatch.setattr(images._stock_images, "fetch_to", _echec)
    monkeypatch.setattr(
        images._nature_images, "generate_to",
        lambda chemin, *a, **k: Path(chemin).write_bytes(b"png"), raising=False,
    )
    return tentatives


def _dix_scenes():
    for i in range(10):
        try:
            images._resoudre_image_cachee(
                base=f"b{i}", scene=f"scene{i}", seed=i, aspect=1.5,
                px_w=900, px_h=600, requete=f"requete {i}",
            )
        except Exception:
            pass  # le repli procédural peut manquer Pillow : on ne mesure QUE le réseau


def test_une_panne_reseau_borne_le_cout_quel_que_soit_le_nombre_de_scenes(monkeypatch):
    """Le point du constat : DIX scènes ne doivent pas coûter dix pénalités."""
    tentatives = _brancher_echec(monkeypatch, OSError("banque injoignable"))
    _dix_scenes()
    # Code d'avant : 10 scenes x 2 variantes x 2 tentatives = 40.
    # Seuil de 2 requetes en echec => 2 requetes x 2 tentatives = 4, plus la
    # marge d'une variante en vol.
    assert 0 < len(tentatives) <= 8, (
        f"{len(tentatives)} tentatives reseau pour 10 scenes : le cout suit "
        "toujours le nombre de scenes (ou le circuit s'ouvre avant d'essayer)"
    )
    assert images._circuit_ouvert(), "une vraie panne reseau doit ouvrir le circuit"


def test_une_requete_sans_resultat_n_ouvre_PAS_le_circuit(monkeypatch):
    """`search_photo` lève `RuntimeError` quand une requête n'a aucun résultat
    CC0 : réseau sain, réponse rapide, zéro délai payé. L'ancienne version le
    comptait comme une panne et coupait 600 s pour tout le processus après deux
    requêtes malchanceuses — supprimant un coût qui n'avait jamais existé."""
    tentatives = _brancher_echec(monkeypatch, RuntimeError("no Openverse cc0 result"))
    _dix_scenes()
    assert not images._circuit_ouvert(), (
        "une absence de resultat a ouvert le coupe-circuit : les exports "
        "suivants sortiront sans photos alors que le reseau va bien"
    )
    # Le cache négatif, lui, fait son travail : chaque requête n'est pas rejouée.
    assert len(tentatives) >= 10, "le cache negatif a mange des requetes distinctes"


def test_l_expiration_referme_le_circuit_ET_remet_le_compteur(monkeypatch):
    """La seule sortie réelle d'une coupure est le minuteur — circuit ouvert,
    aucun appel réseau n'a lieu, donc aucun succès ne peut survenir.

    Le piège que ce test ferme : à l'expiration, `_echecs_consecutifs` restait
    au-dessus du seuil, donc le tout premier échec suivant rouvrait une fenêtre
    PLEINE. Un hoquet de 30 s le matin condamnait les photos de la journée."""
    faux_temps = [1000.0]
    monkeypatch.setattr(images.time, "monotonic", lambda: faux_temps[0])

    images._noter_echec_fetch()
    images._noter_echec_fetch()
    assert images._circuit_ouvert(), "le circuit devait s'ouvrir au seuil"

    faux_temps[0] += images._DUREE_COUPURE_S + 1
    assert not images._circuit_ouvert(), "la fenetre a expire, le circuit doit se refermer"
    assert images._echecs_consecutifs == 0, (
        "le compteur n'est pas remis a zero : le prochain echec ISOLE rouvrira "
        "une fenetre pleine, et le seuil documente devient 1 a vie"
    )

    # Et il faut de nouveau ATTEINDRE le seuil pour rouvrir.
    images._noter_echec_fetch()
    assert not images._circuit_ouvert(), "un echec isole ne doit pas rouvrir la coupure"


def test_le_seuil_gouverne_reellement_l_ouverture(monkeypatch):
    """Le réglage doit gouverner le COMPORTEMENT, pas seulement exister : la
    première version testait `_entier_env` sans jamais vérifier que
    `_SEUIL_COUPURE` était lu — renommer la variable d'environnement laissait
    les tests verts."""
    monkeypatch.setattr(images, "_SEUIL_COUPURE", 5)
    for i in range(4):
        images._noter_echec_fetch()
        assert not images._circuit_ouvert(), f"circuit ouvert des le {i + 1}e echec"
    images._noter_echec_fetch()
    assert images._circuit_ouvert(), "le circuit ne s'ouvre pas au seuil configure"


def test_un_succes_remet_le_compteur_avant_le_seuil():
    """Cas courant : des échecs isolés qu'un succès efface avant qu'ils
    n'atteignent le seuil."""
    images._noter_echec_fetch()
    images._noter_succes_fetch()
    images._noter_echec_fetch()
    assert not images._circuit_ouvert(), (
        "deux echecs SEPARES par un succes ne sont pas consecutifs"
    )


def test_les_reglages_sont_surchargeables():
    """Un réglage figé en dur redevient faux dès que l'environnement change."""
    assert images._entier_env("PPTX_INEXISTANT", 7) == 7
    for valeur in ("zero", "0", "-3", ""):
        os_environ = {"PPTX_TEST_SEUIL": valeur}
        import os
        os.environ.update(os_environ)
        try:
            assert images._entier_env("PPTX_TEST_SEUIL", 2) == 2, (
                f"valeur {valeur!r} : un reglage illisible ou <= 0 doit retomber "
                "sur le defaut, jamais couper le circuit avant le premier essai"
            )
        finally:
            os.environ.pop("PPTX_TEST_SEUIL", None)
