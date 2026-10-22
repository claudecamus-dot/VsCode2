"""Horloge : « maintenant » UTC naïf (module feuille, aucun import d'app).

SQLite ne préserve pas le tzinfo : les colonnes datetime des jobs sont stockées
et comparées en UTC naïf. `models._utcnow` est, lui, AWARE : ce n'est pas un doublon.
"""
from datetime import UTC, datetime


def utc_naif() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
