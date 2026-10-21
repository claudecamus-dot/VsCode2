"""Demande utilisateur du 2026-10-20 : la phrase « Les 5 catégories transverses se lisent
et se modifient dans la synthèse globale de la mission, qui croise tous les entretiens. »
est supprimée au niveau de la fiche d'entretien (pas de renvoi à la synthèse dans l'Aperçu).

Absence de texte dans un gabarit : test volontairement simple (suppression pure d'un
paragraphe, sans logique de rendu)."""
from pathlib import Path

GABARIT = (Path(__file__).resolve().parent.parent / "app" / "templates"
           / "interviews" / "libre_detail.html")


def test_la_fiche_ne_porte_plus_la_phrase_des_categories_transverses():
    src = GABARIT.read_text(encoding="utf-8")
    assert "catégories transverses" not in src
    assert "synthèse globale de la mission" not in src
