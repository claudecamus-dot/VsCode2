"""Migration sur OPT-IN explicite (arbitrage 2026-09-27,
`VSCode2:demarrage-execute-du-ddl-sur-la-base-par-defaut`).

Le démarrage sur la base par défaut (`data/app.db`) ne doit plus altérer le
schéma. Pour exercer la décision « chemin par défaut » SANS jamais ouvrir la
vraie base, chaque test fait pointer `DB_PATH`, `engine` ET `_CHEMIN_PAR_DEFAUT`
vers un fichier jetable : la base jetable EST alors la base par défaut aux yeux
de `app.db`. Poser `APP_DB_PATH` ne suffirait pas — c'est la voie de
contournement, verte sur l'ancien code aussi.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

import app.db as db


@pytest.fixture()
def base_par_defaut(tmp_path: Path, monkeypatch):
    chemin = tmp_path / "app.db"
    eng = create_engine(f"sqlite:///{chemin}", echo=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "DB_PATH", chemin)
    # raising=False : l'attribut n'existe pas sur le code d'avant (preuve P1).
    monkeypatch.setattr(db, "_CHEMIN_PAR_DEFAUT", chemin, raising=False)
    monkeypatch.delenv("APP_DB_MIGRATE", raising=False)
    yield eng
    eng.dispose()


def _colonnes(eng, table: str) -> set[str]:
    with eng.connect() as conn:
        return {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def _ancien_schema(eng) -> None:
    """Base existante à l'ancien schéma : une colonne ajoutée après coup absente."""
    db.Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE trames DROP COLUMN intro_text")
    assert "intro_text" not in _colonnes(eng, "trames")


def test_une_base_EXISTANTE_ne_se_migre_pas_implicitement(base_par_defaut):
    _ancien_schema(base_par_defaut)
    with pytest.raises(RuntimeError, match="Aucune migration implicite") as exc:
        db.init_db()
    assert "trames.intro_text" in str(exc.value)
    assert "APP_DB_MIGRATE=1" in str(exc.value)
    assert "intro_text" not in _colonnes(base_par_defaut, "trames")


def test_la_migration_reste_possible_EXPLICITEMENT(base_par_defaut, monkeypatch):
    _ancien_schema(base_par_defaut)
    monkeypatch.setenv("APP_DB_MIGRATE", "1")
    db.init_db()
    assert "intro_text" in _colonnes(base_par_defaut, "trames")
    assert db.ecarts_de_schema() == []


def test_une_base_NEUVE_reste_complete_au_demarrage(base_par_defaut, tmp_path):
    assert not (tmp_path / "app.db").exists()
    db.init_db()  # sans opt-in, sur le chemin par défaut
    assert db.ecarts_de_schema() == []
    for table in db.Base.metadata.tables.values():
        assert {c.name for c in table.columns} <= _colonnes(base_par_defaut, table.name)
    with base_par_defaut.connect() as conn:
        for _nom, table, colonnes, _o in db._INDEX_UNIQUES:
            assert db._index_unique_existe(conn, table, colonnes), table


def test_une_base_A_JOUR_demarre_sans_opt_in(base_par_defaut):
    """Cas de la vraie `data/app.db` d'aujourd'hui : déjà au schéma."""
    db.init_db()
    db.init_db()  # redémarrage : rien à migrer, aucun refus


def test_index_unique_absent_fait_aussi_refuser(base_par_defaut):
    db.Base.metadata.create_all(base_par_defaut)
    with base_par_defaut.begin() as conn:
        conn.exec_driver_sql("DROP TABLE interview_segment_jobs")
    # Recrée la table SANS la contrainte (ancien schéma) en retirant __table_args__.
    table = db.Base.metadata.tables["interview_segment_jobs"]
    colonnes = ", ".join(
        f"{c.name} {c.type.compile(base_par_defaut.dialect)}" for c in table.columns
    )
    with base_par_defaut.begin() as conn:
        conn.exec_driver_sql(f"CREATE TABLE interview_segment_jobs ({colonnes})")
    with pytest.raises(RuntimeError, match="index unique uq_segment_job_tranche"):
        db.init_db()


def _alias_de_la_base_par_defaut(tmp_path: Path, monkeypatch, alias: Path) -> None:
    """`_CHEMIN_PAR_DEFAUT` sur un fichier jetable existant, `DB_PATH` sur une
    AUTRE graphie du même fichier. Jamais la vraie base."""
    defaut = tmp_path / "app.db"
    monkeypatch.setattr(db, "_CHEMIN_PAR_DEFAUT", defaut, raising=False)
    monkeypatch.setattr(db, "DB_PATH", alias)
    monkeypatch.delenv("APP_DB_MIGRATE", raising=False)


def test_un_LIEN_DUR_vers_la_base_par_defaut_ne_migre_pas(tmp_path, monkeypatch):
    import os
    defaut = tmp_path / "app.db"
    defaut.write_bytes(b"")
    lien = tmp_path / "autre" / "lien.db"
    lien.parent.mkdir()
    os.link(defaut, lien)
    _alias_de_la_base_par_defaut(tmp_path, monkeypatch, lien)
    assert db._migration_autorisee() is False


def test_une_graphie_LONGUE_du_chemin_par_defaut_ne_migre_pas(tmp_path, monkeypatch):
    import sys
    if sys.platform != "win32":
        pytest.skip("préfixe long (\\\\?\\) propre à Windows")
    defaut = tmp_path / "app.db"
    defaut.write_bytes(b"")
    _alias_de_la_base_par_defaut(
        tmp_path, monkeypatch, Path("\\\\?\\" + str(defaut.resolve())))
    assert db._migration_autorisee() is False


def test_un_AUTRE_fichier_existant_reste_migrable(tmp_path, monkeypatch):
    (tmp_path / "app.db").write_bytes(b"")
    autre = tmp_path / "bac.db"
    autre.write_bytes(b"")
    _alias_de_la_base_par_defaut(tmp_path, monkeypatch, autre)
    assert db._migration_autorisee() is True
