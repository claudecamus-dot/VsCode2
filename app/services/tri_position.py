"""Clé de tri par position, avec position NULLE explicite (Story 3.8, AC3.8.2).

Une position `None` (tranche sans position persistée, tour pas encore
numéroté) se range EN PREMIER, comme `ORDER BY` de SQLite (NULL = plus petit,
NULLS FIRST) et comme l'ancien `position or 0` (None triait avec 0), mais elle
reste DISTINGUABLE de la position réelle 0 : `None` d'abord, puis 0, 1, 2...
Décision arbitrée par l'utilisateur le 2026-11-18."""

from __future__ import annotations


def cle_position(position: int | None, *departage: int | None) -> tuple:
    """`(position non nulle, position, *(départage non nul, départage))` :
    None avant tout entier, pour la position comme pour chaque départage."""
    return (
        position is not None, position or 0,
        *(x for d in departage for x in (d is not None, d or 0)),
    )
