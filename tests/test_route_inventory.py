"""Inventaire des routes enregistrées sur `app` — figé en instantané versionné.

Diagnostic superviseur du 2026-09-07 (cible `pytest`, verification-manquante) :
l'oracle de test n'est fiable qu'en suite complète (9 min, non réentrante) — un
décorateur décapité (le lot 7355e29 avait rendu /interviews/segment-jobs/status
introuvable, 404) n'a été vu que là, parce que le fichier de test neuf du même
lot ne l'appelait pas. Ce test tourne en une fraction de seconde, sans base ni
serveur : il suffit d'importer `app` et de lire la table de routage FastAPI.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.main import app

SNAPSHOT = Path(__file__).parent / "fixtures" / "route_inventory.json"


def _routes_actuelles() -> list[tuple[str, str]]:
    """Table effective (méthode, chemin) via `app.openapi()` — pas `app.routes`,
    dont la représentation interne (FastAPI ≥ 0.138 : `_IncludedRouter` paresseux,
    résolu à la demande) ne reflète plus les routes tant que rien ne l'a forcée."""
    vues = {
        (methode.upper(), chemin)
        for chemin, methodes in app.openapi()["paths"].items()
        for methode in methodes
    }
    return sorted(vues)


def test_inventaire_routes_inchange() -> None:
    attendu = [tuple(r) for r in json.loads(SNAPSHOT.read_text(encoding="utf-8"))]
    actuel: list[tuple[str, str]] = _routes_actuelles()
    disparues = [r for r in attendu if r not in actuel]
    nouvelles = [r for r in actuel if r not in attendu]
    assert not disparues and not nouvelles, (
        "Le jeu de routes enregistrées sur `app` a change.\n"
        f"Disparues (decorateur decapite ?) : {disparues}\n"
        f"Nouvelles (mettre a jour {SNAPSHOT.name} si voulu) : {nouvelles}"
    )
