"""Constats qualifiés consensus / écart — tranche 1 de I2 (cadrage
« restitution défendable », docs/reflexions/spec-restitution-defendable.md).

Ce que ces tests figent : un constat désigne EXPLICITEMENT les entretiens qui
le portent (jamais un jugement du modèle), le décompte « N sur M » en dérive
par code, et la règle de signalement `consensus_non_etaye` rend visible le
risque produit n°1 du cadrage — un « consensus » affirmé par 2 interviewés
sur 9. Les cascades sont testées sur la VRAIE base (PRAGMA foreign_keys=ON) :
supprimer un entretien ne doit laisser aucune ligne d'association qu'un id
SQLite réutilisé viendrait réadopter (leçon feedback_sqlite_id_reuse).
"""
from __future__ import annotations

from sqlalchemy import text

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import Interview, Mission, MissionConstat
from app.services.synthese_material import (
    consensus_non_etaye,
    couverture_mission,
    interviews_contributifs,
)


def setup_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def teardown_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


def _mission_avec_entretiens(nom: str, noms: list[str]) -> tuple[int, list[int]]:
    db = SessionLocal()
    try:
        mission = Mission(name=nom)
        db.add(mission)
        db.commit()
        ids = []
        for n in noms:
            iv = Interview(mission_id=mission.id, interviewee_name=n,
                           mode="libre", status="done",
                           repartition={"contexte": "Vu."})
            db.add(iv)
            db.commit()
            ids.append(iv.id)
        return mission.id, ids
    finally:
        db.close()


def _lignes_association(constat_id: int) -> list[tuple[int, int]]:
    """Scopé au constat : les modules de test partagent la même base."""
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(
            text("SELECT constat_id, interview_id FROM constat_interviews "
                 "WHERE constat_id = :c ORDER BY interview_id"),
            {"c": constat_id},
        )]


def test_un_constat_porte_ses_entretiens_et_leur_ordre() -> None:
    mission_id, (a, b) = _mission_avec_entretiens("Constats", ["Alix", "Bao"])
    db = SessionLocal()
    try:
        second = MissionConstat(mission_id=mission_id, axe_key="gouvernance",
                                type="ecart", libelle="Avis divergents",
                                position=1)
        premier = MissionConstat(mission_id=mission_id, axe_key="gouvernance",
                                 type="consensus", libelle="Cap partagé",
                                 position=0)
        premier.interviews = [db.get(Interview, b), db.get(Interview, a)]
        db.add_all([second, premier])
        db.commit()
        # Recharger depuis la base : sans expire, la collection garderait
        # l'ordre d'ASSIGNATION et l'order_by ne serait jamais exercé.
        db.expire_all()

        mission = db.get(Mission, mission_id)
        # order_by="MissionConstat.position" : le constat en position 0 d'abord,
        # quel que soit l'ordre d'insertion (les ids sont dans l'autre sens).
        assert [c.libelle for c in mission.constats] == ["Cap partagé",
                                                         "Avis divergents"]
        # order_by="Interview.id" côté porteurs : stable, pas l'ordre d'ajout.
        assert [iv.id for iv in mission.constats[0].interviews] == [a, b]
        assert mission.constats[0].edite is False
    finally:
        db.close()


def test_supprimer_un_entretien_retire_sa_ligne_d_association() -> None:
    """Le CASCADE est déclaré sur la table NEUVE : il doit jouer en SQL brut,
    pas seulement quand l'ORM a les deux objets en session."""
    mission_id, (a, b) = _mission_avec_entretiens("Cascade IV", ["Alix", "Bao"])
    db = SessionLocal()
    try:
        constat = MissionConstat(mission_id=mission_id, axe_key="outillage",
                                 type="consensus", libelle="Outil adopté")
        constat.interviews = [db.get(Interview, a), db.get(Interview, b)]
        db.add(constat)
        db.commit()
        constat_id = constat.id
    finally:
        db.close()

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM interviews WHERE id = :id"), {"id": a})

    assert _lignes_association(constat_id) == [(constat_id, b)]
    db = SessionLocal()
    try:
        # Le constat SURVIT à la perte d'un porteur — seul le lien tombe.
        assert db.get(MissionConstat, constat_id).libelle == "Outil adopté"
    finally:
        db.close()


def test_supprimer_la_mission_ne_laisse_ni_constat_ni_association() -> None:
    mission_id, ids = _mission_avec_entretiens("Cascade mission", ["Alix"])
    db = SessionLocal()
    try:
        constat = MissionConstat(mission_id=mission_id, axe_key="contexte",
                                 type="ecart", libelle="Vision contestée")
        constat.interviews = [db.get(Interview, ids[0])]
        db.add(constat)
        db.commit()
        constat_id = constat.id
        db.delete(db.get(Mission, mission_id))
        db.commit()
    finally:
        db.close()

    assert _lignes_association(constat_id) == []
    with engine.connect() as conn:
        restants = conn.execute(
            text("SELECT COUNT(*) FROM mission_constats WHERE mission_id = :m"),
            {"m": mission_id},
        ).scalar()
    assert restants == 0


def test_le_ratio_de_mission_derive_des_memes_contributifs() -> None:
    """Définition UNIQUE (I1 et dénominateur des constats) : le ratio de
    `couverture_mission` doit être exactement `len(interviews_contributifs)` —
    deux définitions écrites séparément finiraient par se contredire à l'écran."""
    mission_id, _ = _mission_avec_entretiens("Une définition", ["Alix", "Bao"])
    db = SessionLocal()
    try:
        # Un entretien libre NON analysé : présent au total, pas contributif.
        db.add(Interview(mission_id=mission_id, interviewee_name="Chris",
                         mode="libre", status="done", repartition=None))
        db.commit()
        mission = db.get(Mission, mission_id)
        contributifs = interviews_contributifs(mission)
        assert couverture_mission(mission) == (len(contributifs), 3)
        assert [iv.interviewee_name for iv in contributifs] == ["Alix", "Bao"]
    finally:
        db.close()


def test_consensus_non_etaye_majorite_stricte_en_entiers() -> None:
    # 2 porteurs sur 9 exploités : LE cas du cadrage — signalé.
    assert consensus_non_etaye(2, 9) is True
    # Moitié exacte : 2 sur 4 n'est pas un consensus.
    assert consensus_non_etaye(2, 4) is True
    # Majorité stricte : 3 sur 4, 5 sur 9 — étayé.
    assert consensus_non_etaye(3, 4) is False
    assert consensus_non_etaye(5, 9) is False
    # Aucun entretien exploité : rien à signaler (rien à mesurer).
    assert consensus_non_etaye(0, 0) is False
