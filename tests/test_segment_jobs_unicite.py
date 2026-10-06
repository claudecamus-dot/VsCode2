"""Unicité d'une tranche d'entretien : une même (session_token, position) ne
peut pas produire deux jobs.

Constat d'audit du hub (robustesse, 2026-09-09, le seul rescapé des cinq du
2026-09-04) : `InterviewSegmentJob` n'a aucune contrainte d'unicité, alors que
six `UniqueConstraint` existent ailleurs dans `models.py`. `create_segment_job`
insère sans jamais interroger la position fournie par le CLIENT, et
`merge_segment_turns` concatène ensuite les jobs triés par position sans
dédoublonner. Conséquence sur un entretien réel : la rotation JS de 5 min de
`record_libre.html` est un POST fire-and-forget ; un second POST au même
triplet crée une SECONDE tranche à la même position, et les tours de parole de
ces 5 minutes sortent EN DOUBLE dans la transcription finale.

Quels vecteurs, exactement — parce que la première rédaction de ce fichier
citait « l'utilisateur relance » et c'était faux (revue adversariale du
2026-09-10) : `record_libre.html` mine sa position AVANT l'envoi
(`var pos = segmentJobPosition++`) et la relance après échec repasse par toute
la fonction, donc soumet P+1. Restent (1) la restauration d'un brouillon, qui
remet `segmentJobPosition` à une valeur déjà utilisée, et (2) un rejeu de la
requête HTTP hors du JS — proxy, navigateur. Le doublon INTER-POSITIONS que
produit la relance JS est un autre problème, que cette contrainte ne voit pas
et qui n'est traité NULLE PART aujourd'hui : `merge_segment_turns` concatène
les jobs triés par position sans dédoublonner. (La phrase « qui se traite à la
fusion » figurait ici et dans `models.py` ; elle est fausse, et c'est cette
fausse réassurance qui avait laissé passer un correctif renvoyant le client
soumettre le même texte à la position suivante.)

Le dépôt nomme lui-même le défaut sur le chemin frère : `audio_file_jobs.py`
rejoue le job existant à cette position « plutôt que d'en créer un second, ce
qui dupliquerait ses tours à la fusion ». C'est ce contrat-là qui manquait à la
route HTTP, alors que c'est elle que le navigateur appelle.

Déterministe : aucun thread, aucun sleep. La duplication ne demande pas une
course — deux POST successifs suffisent, ce qui est exactement ce qu'un rejeu
réseau produit.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import InterviewSegmentJob
from app.services import interview_segment_jobs

RACINE_APP = Path(__file__).resolve().parent.parent / "app"


def setup_module() -> None:
    # `engine.dispose()` AVANT le unlink, comme dans le teardown ci-dessous et
    # comme dans le fichier frère : en suite complète, le pool du module
    # PRÉCÉDENT tient encore le fichier et Windows refuse la suppression
    # (WinError 32) — déterministe selon l'ordre de collecte, donc invisible
    # quand ce fichier est joué seul (revue adversariale du 2026-09-10, n°9).
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def teardown_module() -> None:
    # `engine.dispose()` AVANT le unlink : sans lui, le pool de connexions
    # partagé garde le fichier ouvert et Windows refuse la suppression
    # (WinError 32), de façon déterministe selon l'ordre de collecte.
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _ia_muette(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucun appel réseau : l'extraction rend un tour reconnaissable, dont on
    compte les occurrences dans la fusion."""
    def _faux_extract(texte: str, *_a, **_k):
        return {"turns": [{"speaker": "A", "text": texte.strip()}], "identity": {}}

    monkeypatch.setattr(
        interview_segment_jobs, "extract_turns_from_text", _faux_extract, raising=False
    )


def _poster(client: TestClient, jeton: str, position: int, texte: str):
    return client.post(
        "/interviews/segment-jobs",
        data={
            "session_token": jeton,
            "position": position,
            "text": texte,
            "kind": "libre_turns",
        },
    )


def test_deux_posts_a_la_meme_position_ne_creent_qu_une_tranche():
    """Le rejeu réseau de la rotation 5 min ne doit pas doubler la tranche."""
    jeton = "jeton-rejeu-reseau"
    r1 = _poster(client_ := TestClient(app), jeton, 3, "Bonjour, je suis le DSI.")
    assert r1.status_code == 200, r1.text
    r2 = _poster(client_, jeton, 3, "Bonjour, je suis le DSI.")
    assert r2.status_code == 200, r2.text

    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(InterviewSegmentJob).where(
                    InterviewSegmentJob.session_token == jeton
                )
            )
        )
    assert len(jobs) == 1, (
        "deux POST sur la meme (session_token, position) ont cree "
        f"{len(jobs)} tranches : les tours de ces 5 minutes sortiront en double "
        "a la fusion"
    )


def test_un_rejeu_a_l_identique_ne_repaie_pas_l_appel_ia():
    """Rejeu de la MÊME tranche (texte identique, ou tronqué par une
    retransmission partielle) : rien ne bouge, et surtout on ne repaie pas
    l'extraction. La règle est « plus long = c'est la vérité, on ré-extrait » ;
    tout le reste laisse la ligne intacte, et garder l'ancien texte est le bon
    choix face à un texte PLUS COURT — c'est le complet qui fait foi."""
    jeton = "jeton-rejeu-identique"
    client_ = TestClient(app)
    assert _poster(client_, jeton, 0, "Le premier passage.").status_code == 200

    with SessionLocal() as db:
        job = db.scalar(
            select(InterviewSegmentJob).where(
                InterviewSegmentJob.session_token == jeton
            )
        )
        assert job is not None
        premier_id, premier_resultat = job.id, job.turns_result
    assert premier_resultat is not None, "precondition : la tranche a bien abouti"

    # Identique, puis tronqué : ni l'un ni l'autre ne doit rien changer.
    assert _poster(client_, jeton, 0, "Le premier passage.").status_code == 200
    assert _poster(client_, jeton, 0, "Le premier").status_code == 200

    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(InterviewSegmentJob).where(
                    InterviewSegmentJob.session_token == jeton
                )
            )
        )
    assert len(jobs) == 1, f"{len(jobs)} tranches au lieu d'une"
    assert jobs[0].id == premier_id, "la tranche a ete recreee au lieu d'etre reprise"
    assert jobs[0].text == "Le premier passage.", (
        "un rejeu tronque a ampute le texte de la tranche"
    )
    assert jobs[0].turns_result == premier_resultat, (
        "le resultat deja abouti a ete recalcule pour rien"
    )


def test_deux_positions_distinctes_restent_deux_tranches():
    """La garde ne doit pas confondre unicité et écrasement : deux positions
    différentes de la même session sont deux tranches légitimes."""
    jeton = "jeton-deux-positions"
    client_ = TestClient(app)
    assert _poster(client_, jeton, 0, "Premiere tranche.").status_code == 200
    assert _poster(client_, jeton, 1, "Deuxieme tranche.").status_code == 200

    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(InterviewSegmentJob)
                .where(InterviewSegmentJob.session_token == jeton)
                .order_by(InterviewSegmentJob.position)
            )
        )
    assert [j.position for j in jobs] == [0, 1]


def _base_ancien_schema(chemin):
    """Une base au schéma d'AVANT le correctif : table sans contrainte
    d'unicité. `create_all` ne sait plus la produire (le modèle porte
    désormais `__table_args__`), et l'auto-index d'une base neuve ne se
    supprime pas — c'est donc le seul moyen honnête d'exercer la migration
    sur ce qu'elle rencontrera vraiment : les postes déjà installés."""
    import sqlite3

    conn = sqlite3.connect(chemin)
    conn.execute(
        "CREATE TABLE interview_segment_jobs ("
        " id INTEGER PRIMARY KEY, session_token VARCHAR(64), interview_id INTEGER,"
        " kind VARCHAR(20), mission_id INTEGER, position INTEGER, status VARCHAR(20),"
        " text TEXT, turns_result JSON, error TEXT, created_at DATETIME)"
    )
    return conn


def test_la_migration_dedoublonne_une_base_existante_et_garde_l_abouti(tmp_path):
    """La migration tourne au DÉMARRAGE, sur des bases qui portent déjà le
    défaut que l'index vient interdire.

    C'est le chemin le plus risqué du correctif : si le dédoublonnage est faux,
    `CREATE UNIQUE INDEX` échoue, `init_db()` lève, et l'application ne démarre
    plus du tout. Deux choses sont vérifiées : la migration passe sur une base
    qui porte le défaut, et c'est le job ABOUTI qui survit — jeter celui-là
    ferait repayer un appel IA de plusieurs minutes et perdre le texte de la
    tranche."""
    from sqlalchemy import create_engine

    from app.db import _INDEX_UNIQUES, _poser_index_uniques

    chemin = tmp_path / "ancienne.db"
    brut = _base_ancien_schema(str(chemin))
    lignes = [
        # 3 doublons en position 0 : le 9002 est le seul abouti.
        (9001, "s", 0, None), (9002, "s", 0, '{"turns": []}'), (9003, "s", 0, None),
        # 2 doublons en position 1, aucun abouti : le plus ancien gagne.
        (9010, "s", 1, None), (9011, "s", 1, None),
        # Une tranche légitime, seule à sa position : elle doit survivre.
        (9020, "s", 2, None),
        # Une autre session, même position 0 : ce n'est PAS un doublon.
        (9030, "autre", 0, None),
    ]
    for rid, jeton, position, resultat in lignes:
        brut.execute(
            "INSERT INTO interview_segment_jobs"
            " (id, session_token, position, kind, status, text, turns_result, created_at)"
            " VALUES (?, ?, ?, 'libre_turns', 'pending', 'x', ?, '2026-09-01 10:00:00')",
            (rid, jeton, position, resultat),
        )
    brut.commit()
    brut.close()

    moteur = create_engine(f"sqlite:///{chemin}")
    try:
        with moteur.begin() as conn:
            _poser_index_uniques(conn, _INDEX_UNIQUES)
        with moteur.begin() as conn:
            restants = sorted(
                conn.exec_driver_sql(
                    "SELECT id, session_token, position FROM interview_segment_jobs"
                ).fetchall()
            )
    finally:
        moteur.dispose()

    assert [r[0] for r in restants] == [9002, 9010, 9020, 9030], (
        f"survivants {restants} : la migration doit garder l'abouti (9002) en "
        "position 0, le plus ancien (9010) en position 1, la tranche seule "
        "(9020) et l'autre session (9030)"
    )


def test_la_migration_est_rejouable_et_ne_repose_pas_un_index_deja_la(tmp_path):
    """`init_db()` tourne à CHAQUE démarrage : le second passage doit être un
    no-op. Et sur une base NEUVE, la contrainte vient de `__table_args__` :
    SQLite l'indexe sous un nom automatique, que la détection doit reconnaître
    — sinon chaque installation neuve reçoit un index redondant."""
    from sqlalchemy import create_engine

    from app.db import _INDEX_UNIQUES, _poser_index_uniques

    chemin = tmp_path / "rejouable.db"
    brut = _base_ancien_schema(str(chemin))
    brut.execute(
        "INSERT INTO interview_segment_jobs"
        " (id, session_token, position, kind, status, text, created_at)"
        " VALUES (1, 's', 0, 'libre_turns', 'pending', 'x', '2026-09-01 10:00:00')"
    )
    brut.commit()
    brut.close()

    moteur = create_engine(f"sqlite:///{chemin}")
    try:
        with moteur.begin() as conn:
            _poser_index_uniques(conn, _INDEX_UNIQUES)
            _poser_index_uniques(conn, _INDEX_UNIQUES)
            _poser_index_uniques(conn, _INDEX_UNIQUES)
            restants = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM interview_segment_jobs"
            ).scalar()
            index_uniques = [
                r[1]
                for r in conn.exec_driver_sql(
                    "PRAGMA index_list(interview_segment_jobs)"
                )
                if r[2]
            ]
    finally:
        moteur.dispose()

    assert restants == 1, "un rejeu de la migration a supprime une ligne"
    assert len(index_uniques) == 1, (
        f"{len(index_uniques)} index uniques poses ({index_uniques}) : la "
        "migration en repose un a chaque passage"
    )


def test_une_base_neuve_ne_recoit_pas_d_index_redondant():
    """Sur la base du module (créée par `create_all`, donc avec la contrainte
    en ligne), la détection doit voir la contrainte malgré son nom automatique
    `sqlite_autoindex_…`, et ne rien reposer."""
    from app.db import _INDEX_UNIQUES, _index_unique_existe, _poser_index_uniques

    with engine.begin() as conn:
        assert _index_unique_existe(
            conn, "interview_segment_jobs", ("session_token", "position", "kind")
        ), "la contrainte de create_all n'est pas reconnue : un index redondant serait pose"
        # Et on COMPTE, plutôt que de constater une existence : un index
        # redondant laisserait l'assertion ci-dessus verte (2e passe, N11).
        avant = len([
            r for r in conn.exec_driver_sql(
                "PRAGMA index_list(interview_segment_jobs)"
            ) if r[2]
        ])
        _poser_index_uniques(conn, _INDEX_UNIQUES)
        apres = len([
            r for r in conn.exec_driver_sql(
                "PRAGMA index_list(interview_segment_jobs)"
            ) if r[2]
        ])
    assert apres == avant, (
        f"{apres - avant} index unique(s) redondant(s) pose(s) sur une base neuve"
    )


def test_la_base_refuse_le_doublon_meme_sans_passer_par_la_route():
    """Ceinture ET bretelles : la garde applicative protège le chemin HTTP, la
    contrainte de base protège tous les autres (import audio, reprise, script).
    Sans elle, la garde de la route serait le seul rempart d'un invariant de
    données — exactement ce que l'audit reprochait."""
    from sqlalchemy.exc import IntegrityError

    jeton = "jeton-contrainte-base"
    with SessionLocal() as db:
        db.add(
            InterviewSegmentJob(
                session_token=jeton, position=0, status="pending",
                text="a", kind="libre_turns",
            )
        )
        db.commit()

    with SessionLocal() as db:
        db.add(
            InterviewSegmentJob(
                session_token=jeton, position=0, status="pending",
                text="b", kind="libre_turns",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


# --------------------------------------------------------------------------- #
# Comportements ajoutés par la revue adversariale du 2026-09-10 (findings 2, 4
# et 5) : la première version de la garde était un SELECT-puis-INSERT, qui
# transformait la course en 500 avec perte de tranche, ignorait un texte
# complété, et relançait une extraction déjà en cours.
# --------------------------------------------------------------------------- #


def test_la_course_a_l_insertion_ne_leve_pas_et_ne_perd_pas_la_tranche():
    """Deux POST concurrents passent tous deux le SELECT préalable. Sans
    `ON CONFLICT DO NOTHING`, le perdant heurte la contrainte d'unicité, rend
    un 500, et le texte de SA tranche est perdu — le POST est fire-and-forget
    et cet INSERT en est la seule persistance. C'est une perte que le code
    d'AVANT la contrainte ne produisait pas (il faisait un doublon, récupérable) :
    la garde ne doit pas être pire que le défaut qu'elle corrige.

    L'entrelacement est reproduit sans thread : on insère la ligne
    concurrente juste après que la route a lu (et n'a rien trouvé), en
    interceptant `_tranche_existante`."""
    from app.routers import interviews as routeur

    jeton = "jeton-course-insert"
    vrai_lecteur = routeur._tranche_existante
    appels = {"n": 0}

    def _lecteur_qui_simule_un_concurrent(db, j, position, kind):
        appels["n"] += 1
        if appels["n"] == 1:
            # Le concurrent gagne la course JUSTE APRÈS notre lecture à vide.
            with SessionLocal() as autre:
                autre.add(
                    InterviewSegmentJob(
                        session_token=j, position=position, kind=kind,
                        status="pending", text="texte du concurrent",
                    )
                )
                autre.commit()
            return None
        return vrai_lecteur(db, j, position, kind)

    lances = []
    vrai_run = routeur.run_segment_job
    routeur.run_segment_job = lambda job_id: lances.append(job_id)
    routeur._tranche_existante = _lecteur_qui_simule_un_concurrent
    try:
        reponse = _poster(TestClient(app), jeton, 0, "notre texte")
    finally:
        routeur._tranche_existante = vrai_lecteur
        routeur.run_segment_job = vrai_run

    assert reponse.status_code == 200, (
        f"la course rend {reponse.status_code} : le perdant perd sa tranche"
    )
    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(InterviewSegmentJob).where(
                    InterviewSegmentJob.session_token == jeton
                )
            )
        )
    assert len(jobs) == 1, f"{len(jobs)} tranches : l'upsert n'a pas resolu la course"
    # Le perdant ne programme PAS de tâche : c'est le gagnant qui l'a fait.
    # Sans cette assertion, retirer le garde `nous_avons_cree` laisserait ce
    # test vert alors que deux extractions tourneraient sur la même ligne
    # (revue du 2026-09-10, 2e passe, N8).
    assert lances == [], (
        f"le perdant de la course a programme une 2e extraction : {lances}"
    )


def test_un_texte_complete_a_la_meme_position_est_conserve():
    """Réponse perdue puis restauration d'onglet : le client repose la même
    position avec un texte STRICTEMENT plus long. Ignorer le surplus serait une
    perte de parole silencieuse — le client avance son curseur sur la foi du
    200 et vide son reliquat."""
    jeton = "jeton-texte-complete"
    client_ = TestClient(app)
    with SessionLocal() as db:
        db.add(
            InterviewSegmentJob(
                session_token=jeton, position=0, kind="libre_turns",
                status="failed", text="Le debut de la tranche.",
            )
        )
        db.commit()

    assert _poster(
        client_, jeton, 0, "Le debut de la tranche. Et la suite qui manquait."
    ).status_code == 200

    with SessionLocal() as db:
        job = db.scalar(
            select(InterviewSegmentJob).where(
                InterviewSegmentJob.session_token == jeton
            )
        )
    assert job.text == "Le debut de la tranche. Et la suite qui manquait.", (
        "le surplus de texte a ete ignore : cette parole n'est nulle part"
    )


def test_un_texte_complete_sur_une_tranche_deja_traitee_est_re_extrait():
    """Même situation mais la tranche est déjà ABOUTIE.

    La première version rendait un 409 « soumettez le complément à la
    suivante » : le client ne sait pas le lire (`if (!res.ok) throw`), il
    relançait donc sur la position SUIVANTE avec le préfixe compris, et
    `merge_segment_turns` concatène sans dédoublonner — les mêmes tours
    sortaient DEUX FOIS. On troquait un doublon que la contrainte bloque
    contre un doublon qu'elle ne voit pas (revue du 2026-09-10, 2e passe, N1).

    Le comportement retenu : ré-extraire à la MÊME position. `turns_result` est
    remplacé, donc ce n'est jamais un doublon ; le coût est un appel IA
    repayé sur une tranche, contre de la parole d'entretien perdue."""
    jeton = "jeton-complete-abouti"
    with SessionLocal() as db:
        db.add(
            InterviewSegmentJob(
                session_token=jeton, position=0, kind="libre_turns",
                status="done", text="Deja traite.",
                turns_result={"turns": [], "identity": {}},
            )
        )
        db.commit()

    reponse = _poster(TestClient(app), jeton, 0, "Deja traite. Avec un ajout.")
    assert reponse.status_code == 200, reponse.text

    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(InterviewSegmentJob).where(
                    InterviewSegmentJob.session_token == jeton
                )
            )
        )
    assert len(jobs) == 1, f"{len(jobs)} tranches : un doublon inter-positions a ete cree"
    assert jobs[0].text == "Deja traite. Avec un ajout."


def test_un_rejeu_ne_relance_pas_une_extraction_deja_en_cours():
    """`run_segment_job` n'a aucune garde de ré-entrance : le relancer sur une
    ligne `running` fait DEUX extractions concurrentes sur la même tranche —
    appel IA payé deux fois, écriture concurrente de `turns_result`, et un
    échec tardif peut écraser un succès."""
    from app.routers import interviews as routeur

    jeton = "jeton-deja-en-cours"
    with SessionLocal() as db:
        db.add(
            InterviewSegmentJob(
                session_token=jeton, position=0, kind="libre_turns",
                status="running", text="tranche en cours de traitement",
            )
        )
        db.commit()

    lances = []
    vrai_run = routeur.run_segment_job
    routeur.run_segment_job = lambda job_id: lances.append(job_id)
    try:
        reponse = _poster(TestClient(app), jeton, 0, "tranche en cours de traitement")
    finally:
        routeur.run_segment_job = vrai_run

    assert reponse.status_code == 200
    assert lances == [], f"une 2e extraction a ete programmee sur la meme ligne : {lances}"


def test_init_db_cable_reellement_la_pose_des_index(monkeypatch):
    """Le CÂBLAGE, pas seulement la fonction (revue du 2026-09-10, 2e passe,
    N7). Les tests de migration ci-dessus appellent `_poser_index_uniques` à la
    main sur un moteur construit pour eux, et le test de contrainte tourne sur
    une base dont l'index vient de `create_all` : retirer l'appel
    `_add_missing_indexes()` d'`init_db()` les laisserait TOUS verts, et plus
    aucune base déjà installée ne recevrait jamais l'index — c'est-à-dire
    nulle part où les données ont de la valeur."""
    import app.db as module_db

    appels = []
    monkeypatch.setattr(module_db, "_add_missing_indexes", lambda: appels.append(1))
    module_db.init_db()
    assert appels == [1], "init_db() ne pose plus les index : les bases existantes n'en auront jamais"


def test_le_dedoublonnage_journalise_ce_qu_il_supprime(tmp_path, caplog):
    """Le DELETE est la seule opération DESTRUCTIVE du démarrage, et elle porte
    sur des données d'entretien : elle doit laisser une trace. Sans ce test, la
    ligne de journal se supprime sans qu'aucun test ne bouge (N9)."""
    import logging

    from sqlalchemy import create_engine

    from app.db import _INDEX_UNIQUES, _poser_index_uniques

    chemin = tmp_path / "bruyante.db"
    brut = _base_ancien_schema(str(chemin))
    for rid in (1, 2):
        brut.execute(
            "INSERT INTO interview_segment_jobs"
            " (id, session_token, position, kind, status, text, created_at)"
            " VALUES (?, 's', 0, 'libre_turns', 'pending', 'x',"
            "  '2026-09-01 10:00:00')",
            (rid,),
        )
    brut.commit()
    brut.close()

    moteur = create_engine(f"sqlite:///{chemin}")
    try:
        with caplog.at_level(logging.WARNING, logger="app.db"):
            with moteur.begin() as conn:
                _poser_index_uniques(conn, _INDEX_UNIQUES)
    finally:
        moteur.dispose()

    trace = "\n".join(r.getMessage() for r in caplog.records)
    assert "doublon" in trace, (
        "une suppression de donnees d'entretien n'a laisse aucune trace : "
        f"journal = {trace!r}"
    )


def test_un_index_unique_partiel_ne_compte_pas_pour_l_invariant(tmp_path):
    """Un index unique PARTIEL ne contraint que les lignes qui satisfont sa
    condition : le prendre pour l'invariant ferait SAUTER la pose du vrai
    index, en silence (N8). Sans ce test, le garde `or partiel` se supprime
    sans qu'aucun test ne bouge."""
    from sqlalchemy import create_engine

    from app.db import _index_unique_existe

    chemin = tmp_path / "partielle.db"
    brut = _base_ancien_schema(str(chemin))
    brut.execute(
        "CREATE UNIQUE INDEX ix_partiel ON interview_segment_jobs"
        " (session_token, position, kind) WHERE kind IS NOT NULL"
    )
    brut.commit()
    brut.close()

    moteur = create_engine(f"sqlite:///{chemin}")
    try:
        with moteur.begin() as conn:
            trouve = _index_unique_existe(
                conn, "interview_segment_jobs",
                ("session_token", "position", "kind"),
            )
    finally:
        moteur.dispose()

    assert trouve is False, (
        "un index PARTIEL est pris pour l'invariant : le vrai index ne sera "
        "jamais pose et les doublons rentreront sans erreur"
    )


def test_un_rejeu_ne_relance_pas_non_plus_une_tranche_pending():
    """`pending` est l'état le PLUS probable au moment d'un rejeu : les
    `BackgroundTasks` de Starlette s'exécutent après la réponse. Le test voisin
    ne couvrait que `running` (N10)."""
    from app.routers import interviews as routeur

    jeton = "jeton-deja-pending"
    with SessionLocal() as db:
        db.add(
            InterviewSegmentJob(
                session_token=jeton, position=0, kind="libre_turns",
                status="pending", text="tranche deja programmee",
            )
        )
        db.commit()

    lances = []
    vrai_run = routeur.run_segment_job
    routeur.run_segment_job = lambda job_id: lances.append(job_id)
    try:
        reponse = _poster(TestClient(app), jeton, 0, "tranche deja programmee")
    finally:
        routeur.run_segment_job = vrai_run

    assert reponse.status_code == 200
    assert lances == [], f"une 2e extraction programmee sur une ligne pending : {lances}"


def test_le_resultat_d_une_extraction_perimee_n_ecrase_pas_le_texte_complete():
    """Garde comparer-puis-écrire de `run_segment_job` (N3). Si le client
    complète la tranche PENDANT l'extraction, le résultat calculé sur l'ancien
    texte ne doit pas être écrit : il passerait la ligne en `done` sur un
    contenu amputé, et le surplus de parole ne serait jamais extrait."""
    from app.services import interview_segment_jobs as service

    jeton = "jeton-texte-remplace-en-vol"
    with SessionLocal() as db:
        job = InterviewSegmentJob(
            session_token=jeton, position=0, kind="libre_turns",
            status="pending", text="Le debut seulement.",
        )
        db.add(job)
        db.commit()
        job_id = job.id

    def _extraction_lente(db_, job_):
        # Le client complète la tranche pendant que l'IA travaille.
        with SessionLocal() as autre:
            ligne = autre.get(InterviewSegmentJob, job_id)
            ligne.text = "Le debut seulement. Et la suite."
            autre.commit()
        return {"turns": [{"speaker": "A", "text": "Le debut seulement."}]}

    vrai = service._extract_for_job
    service._extract_for_job = _extraction_lente
    try:
        service.run_segment_job(job_id)
    finally:
        service._extract_for_job = vrai

    with SessionLocal() as db:
        ligne = db.get(InterviewSegmentJob, job_id)
    assert ligne.turns_result is None, (
        "un resultat extrait du texte PERIME a ete ecrit : le surplus de "
        "parole ne sera jamais extrait"
    )
    assert ligne.status != "done", f"statut {ligne.status} : la ligne ment sur ce qui a ete extrait"
    assert ligne.text == "Le debut seulement. Et la suite."


def test_un_echec_perime_n_ecrase_pas_le_succes_d_une_extraction_plus_recente():
    """Garde comparer-puis-écrire sur le chemin d'ÉCHEC (3e passe, M1).

    Entrelacement : la tâche A extrait T1 ; le client repose T2 plus long, la
    route ré-arme et programme B ; B aboutit et pose `done` + turns_result(T2) ;
    A échoue ENSUITE — le timeout IA est précisément ce qui l'a fait durer.
    Sans garde, A écrit `failed` par-dessus : la ligne finit en échec en
    portant un résultat valide, l'écran d'attente sort en erreur, et la
    finalisation repaie une extraction complète."""
    from app.services import interview_segment_jobs as service

    jeton = "jeton-echec-perime"
    with SessionLocal() as db:
        job = InterviewSegmentJob(
            session_token=jeton, position=0, kind="libre_turns",
            status="running", text="Texte T1.",
        )
        db.add(job)
        db.commit()
        job_id = job.id

    def _extraction_qui_echoue_apres_coup(db_, job_):
        # Pendant l'extraction : le client complète, et une tâche plus récente
        # aboutit sur le nouveau texte.
        with SessionLocal() as autre:
            ligne = autre.get(InterviewSegmentJob, job_id)
            ligne.text = "Texte T1. Et la suite T2."
            ligne.turns_result = {"turns": [{"speaker": "A", "text": "T1 et T2"}]}
            ligne.status = "done"
            autre.commit()
        raise service._EXTRACT_ERRORS[0]("timeout IA")

    vrai = service._extract_for_job
    service._extract_for_job = _extraction_qui_echoue_apres_coup
    try:
        service.run_segment_job(job_id)
    finally:
        service._extract_for_job = vrai

    with SessionLocal() as db:
        ligne = db.get(InterviewSegmentJob, job_id)
    assert ligne.status == "done", (
        f"statut {ligne.status} : un echec PERIME a ecrase un succes plus recent"
    )
    assert ligne.turns_result is not None, "le resultat valide a ete perdu"


def test_une_re_extraction_ne_jette_pas_le_resultat_deja_paye():
    """Un texte plus long relance l'extraction, mais `turns_result` doit
    SURVIVRE jusqu'à ce qu'un nouveau existe (3e passe, M3). Sinon, une IA
    indisponible fait disparaître la tranche de la transcription fusionnée —
    `merge_segment_turns` filtre sur `turns_result`."""
    jeton = "jeton-resultat-conserve"
    with SessionLocal() as db:
        db.add(
            InterviewSegmentJob(
                session_token=jeton, position=0, kind="libre_turns",
                status="done", text="Court.",
                turns_result={"turns": [{"speaker": "A", "text": "Court."}]},
            )
        )
        db.commit()

    from app.routers import interviews as routeur

    vrai_run = routeur.run_segment_job
    routeur.run_segment_job = lambda job_id: None  # l'IA ne répond pas
    try:
        assert _poster(
            TestClient(app), jeton, 0, "Court. Puis plus long."
        ).status_code == 200
    finally:
        routeur.run_segment_job = vrai_run

    with SessionLocal() as db:
        ligne = db.scalar(
            select(InterviewSegmentJob).where(
                InterviewSegmentJob.session_token == jeton
            )
        )
    assert ligne.turns_result is not None, (
        "le resultat deja paye a ete efface avant qu'un nouveau existe : si la "
        "re-extraction echoue, la tranche disparait de la transcription"
    )
    assert ligne.status == "pending"
    assert ligne.text == "Court. Puis plus long."


def test_une_tranche_en_echec_est_relancee_sur_son_texte():
    """La branche `failed` de la route n'était atteinte par AUCUN test (3e
    passe, m9) : le seul `failed` pré-semé recevait un texte plus long, donc
    partait dans la règle du texte neuf."""
    from app.routers import interviews as routeur

    jeton = "jeton-relance-failed"
    with SessionLocal() as db:
        db.add(
            InterviewSegmentJob(
                session_token=jeton, position=0, kind="libre_turns",
                status="failed", text="Le texte persiste.",
                error="Ollama injoignable.",
            )
        )
        db.commit()

    lances = []
    vrai_run = routeur.run_segment_job
    routeur.run_segment_job = lambda job_id: lances.append(job_id)
    try:
        # Texte IDENTIQUE : c'est bien la branche `failed` qui doit agir.
        reponse = _poster(TestClient(app), jeton, 0, "Le texte persiste.")
    finally:
        routeur.run_segment_job = vrai_run

    assert reponse.status_code == 200
    assert reponse.json()["status"] == "pending"
    assert len(lances) == 1, f"la relance n'a pas ete programmee : {lances}"

    with SessionLocal() as db:
        ligne = db.scalar(
            select(InterviewSegmentJob).where(
                InterviewSegmentJob.session_token == jeton
            )
        )
    assert ligne.status == "pending"
    assert ligne.error is None, "le motif d'echec perime est reste colle"
    assert ligne.text == "Le texte persiste."


def test_une_tranche_re_soumise_non_encore_re_extraite_n_est_pas_prise_pour_aboutie():
    """P1 (4e passe) : le correctif M3 laisse survivre l'ancien `turns_result`
    d'une tranche re-soumise, en attendant la ré-extraction. Trois consommateurs
    du chemin de finalisation LIBRE traitaient `turns_result` comme la preuve
    qu'une tranche avait abouti — équivalence vraie jusqu'à ce lot, fausse
    depuis.

    Conséquence si on ne suit pas : la tranche est exclue de la fenêtre de
    récupération (jamais relancée), exclue du compte des manquantes (aucun
    avertissement), et `merge_segment_turns` expédie l'extraction PÉRIMÉE — le
    deck part avec la tranche tronquée, sans un mot à l'utilisateur.

    Ce test épingle les deux prédicats. Sans lui, un retour à `turns_result` ne
    ferait rougir aucun test — le mode paramétré, lui, a toujours utilisé
    `status`, et c'est cette asymétrie qui a caché le défaut."""
    # Routeur découpé par domaine le 2026-09-23 : les deux chemins vivent dans
    # des modules `interviews_*` distincts — on lit donc tout le routeur.
    # Le chemin LIBRE a quitté le routeur le 2026-10-06 pour
    # `services/structuration_libre.py` (structuration différée) : lu aussi.
    brut = chr(10).join(
        p.read_text(encoding="utf-8")
        for p in [
            *sorted((RACINE_APP / "routers").glob("interviews*.py")),
            RACINE_APP / "services" / "structuration_libre.py",
        ]
    )
    # Les COMMENTAIRES sont retirés : celui qui explique ce correctif cite
    # `bool(j.turns_result)` pour dire de ne pas y revenir, et l'assertion
    # ci-dessous se déclencherait sur son propre avertissement.
    source = chr(10).join(
        ligne for ligne in brut.splitlines() if not ligne.lstrip().startswith("#")
    )
    # Les deux chemins de finalisation, libre ET paramétré, doivent filtrer sur
    # le STATUT. Deux occurrences de chaque, une par chemin.
    assert source.count('lambda j: j.status == "done"') == 2, (
        "un chemin de finalisation ne filtre plus la fenetre de recuperation "
        "sur le statut : une tranche re-soumise sera prise pour aboutie"
    )
    assert source.count('j.status != "done" and j.text.strip()') == 2, (
        "un chemin de finalisation ne compte plus les manquantes sur le statut"
    )
    assert "bool(j.turns_result)" not in source, (
        "un filtre est revenu sur turns_result : depuis 2026-09-10 une tranche "
        "peut porter un resultat PERIME en attendant sa re-extraction"
    )
