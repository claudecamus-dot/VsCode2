"""Génération de l'export PowerPoint (évol) — restitue à l'identique de
l'aperçu web (`synthese/apercu.html`) : slide de titre, sommaire, une slide
par catégorie de synthèse globale, une vue d'ensemble des axes, une matrice
effort/valeur puis une slide par recommandation (gabarit fixe calqué sur un
rapport de restitution réel).

Si `mission.pptx_template_path` est renseigné, la présentation démarre à
partir de ce template client (hérite thème/masters/logo) ; sinon une
présentation vierge en 16:9, stylée via `pptx_deck` (skill pptx-deck, copié
tel quel dans ce service pour ne pas dépendre du chemin d'installation du
skill — cf. son propre en-tête).

Découpage en package (finding audit 2026-07-24 « gros module pptx_export ») :
`base` (constantes de mise en page, FIELD_SHAPE/field_fit_hint, helpers de
slide et de texte mesuré), `images` (photos Openverse/procédurales, cadres
OCTO), `slides_cadre` / `slides_diagnostic` / `slides_trajectoire` (les
slides, groupées par chapitre narratif du deck), `build` (la façade).
L'API publique est inchangée : `build_presentation`, `field_fit_hint`,
`FIELD_SHAPE` s'importent toujours depuis `app.services.pptx_export`.
"""
from __future__ import annotations

from .base import FIELD_SHAPE, MARGIN, OCTO_TEMPLATE_PATH, field_fit_hint
from .build import build_presentation

__all__ = [
    "FIELD_SHAPE",
    "MARGIN",
    "OCTO_TEMPLATE_PATH",
    "build_presentation",
    "field_fit_hint",
]
