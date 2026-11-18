"""Clé de tri par position, avec position NULLE explicite (Story 3.8, AC3.8.2).

Une position `None` (tranche sans position persistée, tour pas encore
numéroté) se range EN DERNIER : elle n'entre plus en collision avec la
position réelle 0 (`position or 0` les mêlait, l'ordre dépendait alors de
l'ordre d'entrée). Décision par défaut, aucun test/code antérieur n'imposait
un autre ordre."""

from __future__ import annotations


def cle_position(position: int | None, *departage: int | None) -> tuple:
    """`(position est nulle, position, *départage)` ; départage nul -> 0."""
    return (position is None, position or 0, *((d or 0) for d in departage))
