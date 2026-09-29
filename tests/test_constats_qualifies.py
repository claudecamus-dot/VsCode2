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


# --- Tranche 2 : import `## CONSTATS` et écran ------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.analyse_import import parse_analysis_markdown  # noqa: E402

_CONSTATS_MD = """
## CONSTATS

### Contexte
- [consensus] Croissance subie (Alix, Bao, Chris)
- [écart] Vision du cap (Alix, Inconnu)
- [consensus] Outil API (REST) trop lent (Dany)
- ligne libre ignorée

### Rubrique que la mission n'étudie pas
- [consensus] Perdu (Alix)
"""


def test_le_parseur_lit_type_libelle_et_noms() -> None:
    constats = parse_analysis_markdown(_CONSTATS_MD)["constats"]
    assert [(c["axe_key"], c["type"], c["libelle"], c["noms"]) for c in constats] == [
        ("contexte", "consensus", "Croissance subie", ["Alix", "Bao", "Chris"]),
        ("contexte", "ecart", "Vision du cap", ["Alix", "Inconnu"]),
        # Seule la DERNIÈRE parenthèse porte les noms.
        ("contexte", "consensus", "Outil API (REST) trop lent", ["Dany"]),
    ]


def test_sans_rubrique_constats_la_liste_est_vide() -> None:
    parsed = parse_analysis_markdown("## SYNTHÈSE GLOBALE\n\n### Contexte\nx\n")
    assert parsed["constats"] == []


def _importer(client: TestClient, mission_id: int, texte: str):
    files = {"file": ("analyse.md", texte.encode("utf-8"), "text/markdown")}
    return client.post(f"/missions/{mission_id}/import/analyse", files=files,
                       follow_redirects=False)


def test_import_rattache_compte_et_signale() -> None:
    """Cinq entretiens contributifs dont deux homonymes « Dany » : le constat
    porté par « Dany » ne peut rattacher personne, et le dit."""
    mission_id, _ = _mission_avec_entretiens(
        "Import constats", ["Alix", "Bao", "Chris", "Dany", "Dany"])
    client = TestClient(app)
    assert _importer(client, mission_id, _CONSTATS_MD).status_code == 303

    db = SessionLocal()
    try:
        constats = db.get(Mission, mission_id).constats
        par_libelle = {c.libelle: c for c in constats}
        assert [iv.interviewee_name for iv in par_libelle["Croissance subie"].interviews] \
            == ["Alix", "Bao", "Chris"]
        assert par_libelle["Vision du cap"].noms_non_rattaches == "Inconnu"
        assert par_libelle["Outil API (REST) trop lent"].interviews == []
        assert par_libelle["Outil API (REST) trop lent"].noms_non_rattaches == "Dany"
    finally:
        db.close()

    page = client.get(f"/missions/{mission_id}/synthese/globale").text
    assert "Constats par axe" in page
    assert "3/5" in page                      # consensus étayé : 3 > 5/2
    assert "Non rattaché : Inconnu" in page
    # 0 porteur sur 5 : le consensus est signalé, pas affiché comme mesure.
    assert "Consensus non étayé" in page
    assert page.count("Consensus non étayé") == 1
    # Un écart n'est jamais peint du vert du « complet » (rendu réel du 2026-09-29).
    assert '<span class="couverture couverture-neutre">1/5</span>' in page
    # Chaque alerte est son propre bloc, pas collée au libellé.
    assert '<span class="constat-alerte">Non rattaché : Inconnu</span>' in page


def test_reimport_remplace_mais_garde_le_constat_edite() -> None:
    mission_id, _ = _mission_avec_entretiens("Réimport", ["Alix", "Bao"])
    client = TestClient(app)
    _importer(client, mission_id, _CONSTATS_MD)
    db = SessionLocal()
    try:
        mission = db.get(Mission, mission_id)
        edite = next(c for c in mission.constats if c.libelle == "Vision du cap")
        edite.edite = True
        edite.type = "consensus"   # requalifié à la main
        db.commit()
    finally:
        db.close()

    _importer(client, mission_id,
              "## CONSTATS\n\n### Contexte\n- [écart] Vision du cap (Bao)\n"
              "- [consensus] Nouveau constat (Bao)\n")

    db = SessionLocal()
    try:
        constats = db.get(Mission, mission_id).constats
        assert [(c.libelle, c.type, c.edite) for c in constats] == [
            ("Vision du cap", "consensus", True),   # la main l'emporte
            ("Nouveau constat", "consensus", False),
        ]
    finally:
        db.close()


def test_un_import_sans_rubrique_constats_n_efface_rien() -> None:
    mission_id, _ = _mission_avec_entretiens("Sans rubrique", ["Alix"])
    client = TestClient(app)
    _importer(client, mission_id, _CONSTATS_MD)
    _importer(client, mission_id, "## SYNTHÈSE GLOBALE\n\n### Contexte\nx\n")
    db = SessionLocal()
    try:
        assert len(db.get(Mission, mission_id).constats) == 3
    finally:
        db.close()


def test_une_base_de_la_tranche_1_recoit_la_colonne_a_la_migration() -> None:
    """Une base démarrée entre les tranches 1 et 2 porte déjà
    `mission_constats`, SANS `noms_non_rattaches` : `create_all` n'y ajoute
    rien, seule la migration additive le peut. Trouvé au rendu réel sur une
    copie de data/app.db — une base de test neuve ne le montrait pas."""
    from app.db import ecarts_de_schema

    engine.dispose()
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "ALTER TABLE mission_constats DROP COLUMN noms_non_rattaches")
    assert "colonne mission_constats.noms_non_rattaches" in ecarts_de_schema()
    init_db()
    assert ecarts_de_schema() == []


# --- Correctifs de revue (bmad-code-review + salle code-review-crew, 2026-09-29) ---

import pytest  # noqa: E402


def test_un_type_hors_enumeration_est_refuse() -> None:
    """CONSTAT_TYPES n'était consulté par personne : `type="autre"` s'écrivait."""
    with pytest.raises(ValueError):
        MissionConstat(type="autre")


def test_un_constat_repete_dans_le_fichier_n_est_cree_qu_une_fois() -> None:
    mission_id, _ = _mission_avec_entretiens("Répété", ["Alix"])
    client = TestClient(app)
    _importer(client, mission_id,
              "## CONSTATS\n\n### Contexte\n"
              "- [consensus] Même constat (Alix)\n"
              "- [consensus] Même constat (Alix)\n")
    db = SessionLocal()
    try:
        assert [c.libelle for c in db.get(Mission, mission_id).constats] == ["Même constat"]
    finally:
        db.close()


def test_un_nom_non_rattache_cite_deux_fois_n_apparait_qu_une_fois() -> None:
    mission_id, _ = _mission_avec_entretiens("Nom répété", ["Alix"])
    client = TestClient(app)
    _importer(client, mission_id,
              "## CONSTATS\n\n### Contexte\n- [écart] Doublon (Inconnu, Inconnu)\n")
    db = SessionLocal()
    try:
        assert db.get(Mission, mission_id).constats[0].noms_non_rattaches == "Inconnu"
    finally:
        db.close()


def test_les_porteurs_affiches_sont_exactement_ceux_que_n_compte() -> None:
    """Un porteur rattaché mais NON contributif (libre sans répartition)
    était listé à l'écran tout en étant exclu de N : « Alix, Bao — 1/1 »."""
    from app.services.constats import constats_par_axe

    mission_id, (alix,) = _mission_avec_entretiens("Porteurs", ["Alix"])
    db = SessionLocal()
    try:
        bao = Interview(mission_id=mission_id, interviewee_name="Bao",
                        mode="libre", status="done", repartition=None)
        db.add(bao)
        db.commit()
        constat = MissionConstat(mission_id=mission_id, axe_key="contexte",
                                 type="consensus", libelle="Partagé")
        constat.interviews = [db.get(Interview, alix), bao]
        db.add(constat)
        db.commit()
        ligne = constats_par_axe(db.get(Mission, mission_id))["contexte"][0]
        assert (ligne["porteurs"], ligne["n"], ligne["m"]) == (["Alix"], 1, 1)
    finally:
        db.close()


def test_couverture_mission_a_oracle_ecrit_en_dur() -> None:
    """Oracle INDÉPENDANT (salle du 2026-09-29 : le test de dérivation
    calculait son attendu avec la fonction testée). Deux libres analysés, un
    libre sans répartition, un structuré sans réponse : 2 sur 4, écrit à la main."""
    mission_id, _ = _mission_avec_entretiens("Oracle", ["Alix", "Bao"])
    db = SessionLocal()
    try:
        db.add(Interview(mission_id=mission_id, interviewee_name="Chris",
                         mode="libre", status="done", repartition=None))
        db.add(Interview(mission_id=mission_id, interviewee_name="Dany",
                         status="done"))
        db.commit()
        assert couverture_mission(db.get(Mission, mission_id)) == (2, 4)
    finally:
        db.close()


# --- Correctifs des salles code-review-crew (2026-09-29) --------------------

def test_le_gabarit_exporte_demande_la_rubrique_constats() -> None:
    """Bloquant de la salle : sans cette rubrique dans le document envoyé à
    l'analyste, aucun constat ne revenait jamais dans le vrai parcours."""
    from app.services.mission_export import build_export_markdown

    mission_id, _ = _mission_avec_entretiens("Gabarit", ["Alix"])
    db = SessionLocal()
    try:
        gabarit = build_export_markdown(db.get(Mission, mission_id))
    finally:
        db.close()
    rubrique = gabarit.split("## CONSTATS", 1)[1].split("## RECOMMANDATIONS", 1)[0]
    assert "### Contexte" in rubrique
    assert "[consensus]" in rubrique and "[écart]" in rubrique
    # Réimporter le gabarit NON rempli ne fabrique aucun constat : la consigne
    # vit avant le premier `###`, elle n'est pas relue.
    assert parse_analysis_markdown(gabarit)["constats"] == []


def test_synthese_des_constats_reste_la_synthese_globale() -> None:
    parsed = parse_analysis_markdown(
        "## Synthèse des constats\n\n### Contexte\nCroissance rapide\n")
    assert parsed["global_synthesis"]["contexte"] == "Croissance rapide"
    assert parsed["constats"] == []


def test_un_nom_non_rattache_n_est_pas_double_par_sa_casse() -> None:
    mission_id, _ = _mission_avec_entretiens("Casse", ["Alix"])
    client = TestClient(app)
    _importer(client, mission_id,
              "## CONSTATS\n\n### Contexte\n- [écart] Casse (Dany, dany)\n")
    db = SessionLocal()
    try:
        assert db.get(Mission, mission_id).constats[0].noms_non_rattaches == "Dany"
    finally:
        db.close()


def test_les_constats_d_un_axe_retire_restent_visibles() -> None:
    mission_id, (alix,) = _mission_avec_entretiens("Axe retiré", ["Alix"])
    db = SessionLocal()
    try:
        constat = MissionConstat(mission_id=mission_id, axe_key="axe_disparu",
                                 type="consensus", libelle="Toujours là")
        constat.interviews = [db.get(Interview, alix)]
        db.add(constat)
        db.commit()
    finally:
        db.close()
    page = TestClient(app).get(f"/missions/{mission_id}/synthese/globale").text
    assert "Axe retiré de la mission" in page
    assert "Toujours là" in page


def test_un_libelle_hostile_est_echappe_au_rendu() -> None:
    """Rien ne le prouvait : ajouter un jour `|safe` au template aurait ouvert
    une injection depuis un fichier téléversé, sans aucun test rouge."""
    mission_id, _ = _mission_avec_entretiens("XSS", ["Alix"])
    client = TestClient(app)
    _importer(client, mission_id,
              "## CONSTATS\n\n### Contexte\n"
              "- [consensus] <script>alert(1)</script> (<b>X</b>)\n")
    page = client.get(f"/missions/{mission_id}/synthese/globale").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<b>X</b>" not in page


# --- Contre-revue des correctifs (2026-09-29) --------------------------------

def test_consensus_et_ecart_au_meme_libelle_sont_deux_constats() -> None:
    """Le dédoublonnage intra-fichier ignorait le type : la seconde lecture
    disparaissait sans signal."""
    mission_id, _ = _mission_avec_entretiens("Deux lectures", ["Alix", "Bao"])
    client = TestClient(app)
    _importer(client, mission_id,
              "## CONSTATS\n\n### Contexte\n"
              "- [consensus] Le legacy freine (Alix)\n"
              "- [écart] Le legacy freine (Bao)\n")
    db = SessionLocal()
    try:
        assert [(c.libelle, c.type) for c in db.get(Mission, mission_id).constats] == [
            ("Le legacy freine", "consensus"), ("Le legacy freine", "ecart")]
    finally:
        db.close()


def test_le_point_virgule_separe_les_noms_au_parsing() -> None:
    constats = parse_analysis_markdown(
        "## CONSTATS\n\n### Contexte\n- [écart] B (Alix ; Bao)\n")["constats"]
    assert constats[0]["noms"] == ["Alix", "Bao"]


def test_et_separe_deux_personnes_mais_pas_un_nom_compose() -> None:
    """« Alix et Bao » : deux porteurs. « Dupont et Fils » : un entretien
    existant de ce nom, rattaché tel quel au lieu d'être coupé en deux."""
    mission_id, _ = _mission_avec_entretiens(
        "Et", ["Alix", "Bao", "Dupont et Fils"])
    client = TestClient(app)
    _importer(client, mission_id,
              "## CONSTATS\n\n### Contexte\n"
              "- [consensus] A (Alix et Bao)\n"
              "- [écart] B (Dupont et Fils)\n")
    db = SessionLocal()
    try:
        a, b = db.get(Mission, mission_id).constats
        assert [iv.interviewee_name for iv in a.interviews] == ["Alix", "Bao"]
        assert [iv.interviewee_name for iv in b.interviews] == ["Dupont et Fils"]
        assert (a.noms_non_rattaches, b.noms_non_rattaches) == ("", "")
    finally:
        db.close()


def test_un_constat_edite_masque_les_deux_lectures_du_fichier() -> None:
    """Comportement VOULU, figé : la requalification à la main l'emporte sur
    toute lecture du fichier au même libellé, consensus comme écart."""
    mission_id, _ = _mission_avec_entretiens("Édité", ["Alix"])
    db = SessionLocal()
    try:
        db.add(MissionConstat(mission_id=mission_id, axe_key="contexte",
                              type="consensus", libelle="Vision du cap",
                              edite=True))
        db.commit()
    finally:
        db.close()
    _importer(TestClient(app), mission_id,
              "## CONSTATS\n\n### Contexte\n"
              "- [consensus] Vision du cap (Alix)\n"
              "- [écart] Vision du cap (Alix)\n")
    db = SessionLocal()
    try:
        constats = db.get(Mission, mission_id).constats
        assert [(c.libelle, c.edite) for c in constats] == [("Vision du cap", True)]
    finally:
        db.close()


