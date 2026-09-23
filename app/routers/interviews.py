"""Routeur des entretiens : agrégat des sous-routeurs par domaine.

Découpé le 2026-09-23 (constat d'audit risque technique : 3385 lignes /
56 routes dans ce seul fichier). Le code a été déplacé tel quel dans les
modules `interviews_*` ; ce fichier ne fait plus qu'inclure leurs routeurs
DANS L'ORDRE d'origine — FastAPI résout les routes dans leur ordre
d'enregistrement, et l'ordre (méthode, chemin) a été comparé avant/après.
`app.main` continue d'inclure `interviews.router` : aucune URL ne change.

COMPATIBILITÉ DES REMPLACEMENTS. Avant le découpage, les tests remplaçaient
des dépendances en visant `app.routers.interviews.<nom>` (monkeypatch par
chaîne, ou `routeur.run_segment_job = ...`). Pour que ces remplacements
continuent d'atteindre le code qui s'exécute, ce module est un AGRÉGAT : lire
un nom le cherche dans les sous-modules (ordre ci-dessous), et l'écrire le
remplace dans CHAQUE sous-module qui le définit — puis la restauration du
monkeypatch le rétablit partout de la même façon. Le comportement applicatif
n'en dépend pas : seules les écritures d'attribut (tests) passent par là.
"""
from __future__ import annotations

import sys
import types

from fastapi import APIRouter

from . import (
    interviews_analyse,
    interviews_backup,
    interviews_commun,
    interviews_creation,
    interviews_export,
    interviews_gestion,
    interviews_libre,
    interviews_record,
    interviews_retranscription,
    interviews_saisie,
    interviews_segment_jobs,
)

_SOUS_MODULES = (
    interviews_creation,
    interviews_record,
    interviews_segment_jobs,
    interviews_libre,
    interviews_backup,
    interviews_gestion,
    interviews_analyse,
    interviews_retranscription,
    interviews_saisie,
    interviews_export,
)

router = APIRouter()
for _module in _SOUS_MODULES:
    router.include_router(_module.router)

_TOUS = (interviews_commun, *_SOUS_MODULES)


class _Agregat(types.ModuleType):
    """Lecture : premier sous-module qui porte le nom. Écriture : tous ceux
    qui le portent (un même import, p. ex. `extract_turns_from_text`, vit
    dans plusieurs modules après le découpage)."""

    def __getattr__(self, nom):
        for module in _TOUS:
            if nom in vars(module):
                return vars(module)[nom]
        raise AttributeError(f"module {__name__!r} has no attribute {nom!r}")

    def __setattr__(self, nom, valeur):
        # Un nom propre à l'agrégat (`router`, ...) ne descend jamais : il
        # écraserait le routeur de chaque sous-module.
        porteurs = [] if nom in vars(self) else [m for m in _TOUS if nom in vars(m)]
        for module in porteurs:
            setattr(module, nom, valeur)
        if not porteurs:
            super().__setattr__(nom, valeur)


sys.modules[__name__].__class__ = _Agregat
