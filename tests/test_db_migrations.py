"""Filet de sécurité du mécanisme de migrations additives de `app.db`.

Finding audit 2026-07-24 (risque technique) : les migrations SQLite sont des
ALTER TABLE additifs en f-string DDL — pas d'outil de migration, pas de
rollback (dette assumée, documentée dans app/db.py). Ces tests ne remplacent
pas un outil : ils verrouillent les garanties dont le mécanisme dépend —
(1) idempotence de init_db (rejouée à chaque démarrage), (2) rattrapage réel
d'une base ancienne à qui manquent des colonnes ajoutées après coup,
(3) aucune dérive silencieuse entre les modèles SQLAlchemy et le schéma
réellement présent en base après init_db.

Chaque test travaille sur SA PROPRE base jetable (engine monkeypatché) :
jamais la base partagée de la suite — retirer des colonnes d'une base que
d'autres tests utilisent perdrait leurs données.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

import app.db as db
from app.models import Base


@pytest.fixture()
def moteur_jetable(tmp_path: Path, monkeypatch) -> Engine:
    """Engine SQLite sur une base neuve, substitué au global de app.db le temps
    du test — init_db/_add_missing_columns lisent `engine` au moment de l'appel."""
    eng = create_engine(f"sqlite:///{tmp_path / 'migration_test.db'}", echo=False)
    monkeypatch.setattr(db, "engine", eng)
    yield eng
    eng.dispose()


def _colonnes(eng: Engine, table: str) -> set[str]:
    with eng.begin() as conn:
        return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def _derives_modeles_vs_base(eng: Engine) -> list[str]:
    """Colonnes des modèles absentes de la base — vide si le schéma est à jour."""
    manquantes: list[str] = []
    for table in Base.metadata.tables.values():
        presentes = _colonnes(eng, table.name)
        if not presentes:
            manquantes.append(f"table absente : {table.name}")
            continue
        manquantes.extend(
            f"{table.name}.{col.name}"
            for col in table.columns
            if col.name not in presentes
        )
    return manquantes


def test_init_db_idempotente(moteur_jetable: Engine) -> None:
    """Rejouer init_db (comme à chaque démarrage) ne casse rien et n'altère pas
    le schéma — les ALTER TABLE ne s'appliquent qu'aux colonnes absentes."""
    db.init_db()
    avant = {t.name: _colonnes(moteur_jetable, t.name) for t in Base.metadata.tables.values()}
    db.init_db()
    apres = {t.name: _colonnes(moteur_jetable, t.name) for t in Base.metadata.tables.values()}
    assert avant == apres
    assert _derives_modeles_vs_base(moteur_jetable) == []


def test_migration_rattrape_une_base_ancienne(moteur_jetable: Engine) -> None:
    """Simule une base d'avant les évolutions de schéma : on retire des colonnes
    ajoutées après coup (échantillon couvrant chaque table migrée), init_db doit
    les recréer via _add_missing_columns."""
    db.init_db()
    retirees = [
        ("interviews", "resume"),
        ("trames", "intro_text"),
        ("questions", "help_text"),
        ("missions", "is_demo"),
        ("interview_turns", "section_title"),
    ]
    with moteur_jetable.begin() as conn:
        for table, col in retirees:
            conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {col}")
    for table, col in retirees:
        assert col not in _colonnes(moteur_jetable, table), "le DROP simulant la base ancienne n'a pas eu lieu"

    db.init_db()

    for table, col in retirees:
        assert col in _colonnes(moteur_jetable, table), f"colonne non rattrapée : {table}.{col}"
    assert _derives_modeles_vs_base(moteur_jetable) == []


def test_schema_migre_couvre_les_modeles(moteur_jetable: Engine) -> None:
    """Garde anti-dérive : après init_db, TOUTE colonne déclarée dans les modèles
    existe en base. Échoue si une colonne est ajoutée à un modèle sans son entrée
    dans `additions` (le cas que le mécanisme manuel ne détecte pas tout seul)."""
    db.init_db()
    assert _derives_modeles_vs_base(moteur_jetable) == []
