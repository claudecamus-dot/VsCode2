"""Story 3.4 — intégrité des entretiens côté serveur (triage du 2026-11-16, B-*).

Un test par constat corrigé ; chacun échoue sur le code d'avant la story.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import (
    AudioFileJob,
    Interview,
    InterviewSegmentJob,
    InterviewTurn,
    Mission,
)
from app.services import interview_segment_jobs, mission_backups, structuration_libre
from app.services.interview_libre_extract_ai import InterviewLibreExtractAIError


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


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _mission(client: TestClient) -> int:
    response = client.post("/entretiens/libre/nouveau", follow_redirects=False)
    mission_id = int(response.headers["location"].split("/")[2])
    with SessionLocal() as db:
        db.get(Mission, mission_id).is_draft = False
        db.commit()
    return mission_id


def _interview(mission_id: int, statut: str, texte: str = "Du texte.", **kw) -> int:
    with SessionLocal() as db:
        interview = Interview(
            mission_id=mission_id, mode="libre", status="done",
            interviewee_name="Sans nom", raw_transcript=texte,
            structuration_status=statut, **kw,
        )
        db.add(interview)
        db.commit()
        return interview.id


def _statut(interview_id: int) -> str:
    with SessionLocal() as db:
        return db.get(Interview, interview_id).structuration_status


# --------------------------------------------------------------------------- #
# B-1 — entretien bloqué `a_transcrire` avec du texte : avertissement + sortie
# --------------------------------------------------------------------------- #
def test_b1_fiche_a_transcrire_avec_texte_explique_le_blocage_et_propose_la_sortie(
    client: TestClient,
) -> None:
    mission_id = _mission(client)
    avec_texte = _interview(mission_id, "a_transcrire", "Bonjour, je suis Alice.")
    sans_texte = _interview(mission_id, "a_transcrire", "")
    page = client.get(f"/interviews/{avec_texte}").text
    assert 'data-structuration-bloquee="1"' in page
    assert 'name="texte_disponible"' in page
    assert "Structurer le texte disponible" in page
    # Comportemental (revue) : le formulaire RENDU, soumis tel quel, débloque.
    import re as _re
    bloc = page.split('data-structuration-bloquee="1"', 1)[1].split("</form>", 1)[0]
    action = _re.search(r'action="([^"]+)"', bloc).group(1)
    champs = dict(_re.findall(r'type="hidden" name="([^"]+)" value="([^"]*)"', bloc))
    assert champs == {"texte_disponible": "1"}
    _orig = structuration_libre.planifier_structuration
    structuration_libre.planifier_structuration = lambda iid, priorite=None: True
    try:
        r = client.post(action, data=champs, follow_redirects=False)
    finally:
        structuration_libre.planifier_structuration = _orig
    assert r.status_code == 303 and "refusee" not in r.headers["location"]
    assert _statut(avec_texte) == "a_traiter"
    page_vide = client.get(f"/interviews/{sans_texte}").text
    assert "data-structuration-bloquee" not in page_vide


def test_b1_structurer_le_texte_disponible_debloque_apres_confirmation(
    client: TestClient, monkeypatch,
) -> None:
    programmes: list = []
    monkeypatch.setattr(
        structuration_libre, "planifier_structuration",
        lambda iid, priorite=None: programmes.append(iid) or True,
    )
    mission_id = _mission(client)
    iid = _interview(mission_id, "a_transcrire", "Bonjour, je suis Alice.")
    # La règle de blocage est CONSERVÉE : la relance simple reste refusée.
    r = client.post(f"/interviews/{iid}/structurer", follow_redirects=False)
    assert "structuration=refusee" in r.headers["location"]
    assert _statut(iid) == "a_transcrire"
    # Sortie explicite (bouton confirmé à l'écran).
    r = client.post(
        f"/interviews/{iid}/structurer", data={"texte_disponible": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "refusee" not in r.headers["location"]
    assert _statut(iid) == "a_traiter"
    assert programmes == [iid]


def test_b1_sortie_refusee_sans_texte(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        structuration_libre, "planifier_structuration", lambda *a, **k: True,
    )
    mission_id = _mission(client)
    iid = _interview(mission_id, "a_transcrire", "   ")
    r = client.post(
        f"/interviews/{iid}/structurer", data={"texte_disponible": "1"},
        follow_redirects=False,
    )
    assert "structuration=refusee" in r.headers["location"]
    assert _statut(iid) == "a_transcrire"


# --------------------------------------------------------------------------- #
# B-2 — aperçu live : une tranche finie hors ordre ne fait sauter aucune tranche
# --------------------------------------------------------------------------- #
def _job(jeton, position, statut, remarque=None):
    return InterviewSegmentJob(
        session_token=jeton, position=position, kind="libre_turns",
        status=statut, text=f"texte {position}",
        turns_result=(
            {"turns": [{"interlocuteur": "A", "question": None,
                        "remarque": remarque, "section_title": None}],
             "identity": {}}
            if remarque else None
        ),
    )


def test_b2_curseur_ne_depasse_pas_une_tranche_encore_en_vol(client: TestClient) -> None:
    jeton = "b2-hors-ordre"
    with SessionLocal() as db:
        db.add_all([_job(jeton, 0, "done", "t0"), _job(jeton, 1, "running"),
                    _job(jeton, 2, "done", "t2")])
        db.commit()
    r = client.get("/interviews/segment-jobs/turns",
                   params={"session_token": jeton, "since": 0}).json()
    assert [t["remarque"] for t in r["turns"]] == ["t0"]
    assert r["next_since"] == 1
    with SessionLocal() as db:
        job = db.scalar(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == jeton, InterviewSegmentJob.position == 1))
        job.status = "done"
        job.turns_result = {"turns": [{"interlocuteur": "A", "question": None,
                                       "remarque": "t1", "section_title": None}],
                            "identity": {}}
        db.commit()
    r = client.get("/interviews/segment-jobs/turns",
                   params={"session_token": jeton, "since": 1}).json()
    assert [t["remarque"] for t in r["turns"]] == ["t1", "t2"]
    assert r["next_since"] == 3


def test_b2_une_tranche_en_echec_ne_bloque_pas_l_apercu(client: TestClient) -> None:
    jeton = "b2-echec"
    with SessionLocal() as db:
        db.add_all([_job(jeton, 0, "failed"), _job(jeton, 1, "done", "t1")])
        db.commit()
    r = client.get("/interviews/segment-jobs/turns",
                   params={"session_token": jeton, "since": 0}).json()
    assert [t["remarque"] for t in r["turns"]] == ["t1"]
    assert r["next_since"] == 2


# --------------------------------------------------------------------------- #
# B-3 — jeton d'import > 64 caractères : plus de 404 sur status / retry
# --------------------------------------------------------------------------- #
def test_b3_jeton_long_status_et_retry_ne_rendent_pas_404(client: TestClient) -> None:
    long_jeton = "x" * 100
    with SessionLocal() as db:
        job = AudioFileJob(
            session_token=interview_segment_jobs.normaliser_jeton(long_jeton),
            filename="1_import.webm", status="running", block_seconds=60, blocks=[],
        )
        db.add(job)
        db.commit()
        job_id = job.id
    r = client.get("/audio/transcribe-file/status",
                   params={"job_id": job_id, "session_token": long_jeton})
    assert r.status_code == 200
    r = client.post("/audio/transcribe-file/retry",
                    data={"job_id": job_id, "session_token": long_jeton})
    # Revue finale (F7) : le statut EXACT — un import en cours, retrouvé par son
    # jeton long, n'est « pas en échec » (409), ni introuvable ni relancé.
    assert r.status_code == 409, r.text
    # Un jeton étranger reste refusé.
    r = client.get("/audio/transcribe-file/status",
                   params={"job_id": job_id, "session_token": "y" * 100})
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# B-8 — jeton de tranche fait d'espaces : 400, aucune ligne orpheline partagée
# --------------------------------------------------------------------------- #
def test_b8_jeton_d_espaces_refuse_sans_creer_de_ligne(client: TestClient, monkeypatch) -> None:
    # Aucune extraction réelle si la garde manque (le mutant programmerait la tâche).
    monkeypatch.setattr("app.routers.interviews_segment_jobs.run_segment_job", lambda _id: None)
    r = client.post("/interviews/segment-jobs",
                    data={"session_token": "   ", "position": 0, "text": "Bonjour"})
    assert r.status_code == 400
    with SessionLocal() as db:
        assert db.scalar(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == "")) is None


# --------------------------------------------------------------------------- #
# B-9 — double POST de la confirmation : un seul entretien
# --------------------------------------------------------------------------- #
def test_b9_double_post_confirm_meme_jeton_un_seul_entretien(client: TestClient) -> None:
    mission_id = _mission(client)
    data = {
        "transcript": "Bonjour.", "interviewee_name": "Alice",
        "turn_interlocuteur": ["Alice"], "turn_question": [""],
        "turn_remarque": ["Un propos."], "turn_section_title": [""],
        "session_token": "b9-jeton",
    }
    url = f"/missions/{mission_id}/interviews/record-libre/confirm"
    r1 = client.post(url, data=data, follow_redirects=False)
    r2 = client.post(url, data=data, follow_redirects=False)
    assert r1.status_code == 303 and r2.status_code == 303
    with SessionLocal() as db:
        ids = list(db.scalars(select(Interview.id).where(Interview.mission_id == mission_id)))
    assert len(ids) == 1
    assert r2.headers["location"] == r1.headers["location"]


# --------------------------------------------------------------------------- #
# B-4 — supprimer une tranche de sauvegarde laisse le trou de positions
# --------------------------------------------------------------------------- #
def test_b4_suppression_d_un_segment_garde_le_trou_de_positions(
    client: TestClient, monkeypatch, tmp_path,
) -> None:
    from app.routers import interviews_backup

    monkeypatch.setattr(interviews_backup, "RECORDINGS_DIR", tmp_path)
    mission_id = _mission(client)
    noms = [f"{mission_id}_seg{i}.webm" for i in range(3)]
    for nom in noms:
        (tmp_path / nom).write_bytes(b"audio")
    iid = _interview(
        mission_id, "fait",
        audio_segments=[{"filename": n, "position": i} for i, n in enumerate(noms)],
        audio_backup_path=noms[-1],
    )
    r = client.post(
        f"/missions/{mission_id}/interviews/record/backup/{noms[1]}/delete",
        follow_redirects=False,
    )
    assert r.status_code == 303
    with SessionLocal() as db:
        segs = db.get(Interview, iid).audio_segments
    assert [s["position"] for s in segs] == [0, 2]
    # Le rang d'AFFICHAGE de l'onglet Backup reste « n/2 », jamais « 3/2 ».
    with SessionLocal() as db:
        lignes = mission_backups.lister_backups(db.get(Mission, mission_id), tmp_path)
    rattaches = [b for b in lignes["rattaches"] if b["interview"] is not None]
    assert sorted((b["position"], b["nb_tranches"]) for b in rattaches) == [(0, 2), (1, 2)]


# --------------------------------------------------------------------------- #
# B-11 — un fichier antérieur à la mission (id SQLite réutilisé) n'est pas rattaché
# --------------------------------------------------------------------------- #
def test_b11_reference_audio_anterieure_a_la_mission_ecartee(tmp_path) -> None:
    nom = "5_ancien.webm"
    chemin = tmp_path / nom
    chemin.write_bytes(b"audio")
    vieux = time.time() - 3 * 24 * 3600
    os.utime(chemin, (vieux, vieux))
    mission = SimpleNamespace(id=5, created_at=datetime.now(timezone.utc).replace(tzinfo=None))
    segs = [{"filename": nom, "position": 0}]
    chemin_sur, segments = mission_backups.references_audio_sures(
        5, nom, segs, tmp_path, mission=mission,
    )
    assert chemin_sur is None and segments == []
    # Fichier écrit APRÈS la mission : rattaché.
    os.utime(chemin, None)
    mission.created_at = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    assert mission_backups.references_audio_sures(
        5, nom, segs, tmp_path, mission=mission,
    ) == (nom, [{"filename": nom, "position": 0}])


# --------------------------------------------------------------------------- #
# B-10 — un fichier au préfixe non canonique (« 007_x ») reste visible en orphelin
# --------------------------------------------------------------------------- #
def test_b10_prefixe_non_canonique_liste_en_orphelin(client: TestClient, tmp_path) -> None:
    mission_id = _mission(client)
    nom = f"00{mission_id}_x.webm"
    (tmp_path / nom).write_bytes(b"audio")
    with SessionLocal() as db:
        orphelins = mission_backups.lister_orphelins_globaux(tmp_path, db)["orphelins"]
    assert nom in [o["filename"] for o in orphelins]


# --------------------------------------------------------------------------- #
# B-6 — un échec tardif de la tâche d'origine n'écrase pas un `done`
# --------------------------------------------------------------------------- #
def test_b6_echec_tardif_n_ecrase_pas_un_done_recupere(monkeypatch) -> None:
    with SessionLocal() as db:
        job = InterviewSegmentJob(session_token="b6", position=0, kind="libre_turns",
                                  status="pending", text="Bonjour.")
        db.add(job)
        db.commit()
        job_id = job.id

    def _recuperee_puis_echec(db, job, *a, **k):
        # Pendant l'extraction, la récupération synchrone aboutit la tranche.
        with SessionLocal() as autre:
            ligne = autre.get(InterviewSegmentJob, job_id)
            ligne.status = "done"
            ligne.turns_result = {"turns": [], "identity": {}}
            autre.commit()
        raise InterviewLibreExtractAIError("IA indisponible")

    monkeypatch.setattr(interview_segment_jobs, "_extract_with_retries", _recuperee_puis_echec)
    interview_segment_jobs.run_segment_job(job_id)
    with SessionLocal() as db:
        assert db.get(InterviewSegmentJob, job_id).status == "done"


# --------------------------------------------------------------------------- #
# B-12 — une exception hors du `try` du worker ne laisse pas l'id « déjà en file »
# --------------------------------------------------------------------------- #
def test_b12_exception_hors_try_libere_l_inscription(monkeypatch) -> None:
    def _casse(_priorite):
        raise RuntimeError("priorité indisponible")

    monkeypatch.setattr(structuration_libre, "avec_priorite", _casse)
    marque = object()
    with structuration_libre._verrou_registre:
        structuration_libre._en_vol[987654] = marque
    structuration_libre._traiter((987654, marque, None, None, 0))  # ne lève pas
    assert not structuration_libre.est_en_vol(987654)


# --------------------------------------------------------------------------- #
# B-7 — relancer un aperçu existant DIT que les corrections manuelles sont perdues
# --------------------------------------------------------------------------- #
def test_b7_relance_avec_tours_avertit_de_la_perte_des_corrections(client: TestClient) -> None:
    mission_id = _mission(client)
    iid = _interview(mission_id, "echec")
    with SessionLocal() as db:
        db.add(InterviewTurn(interview_id=iid, position=0, interlocuteur="A",
                             question=None, remarque="Corrigé à la main", section_title=None))
        db.commit()
    page = client.get(f"/interviews/{iid}").text
    assert "corrections faites à la main sur les tours seront perdues" in page


# --------------------------------------------------------------------------- #
# B-9 (suite) — chemin « synthèse » : le jeton traverse libre_review.html
# --------------------------------------------------------------------------- #
def test_b9_synthese_transmet_le_jeton_et_confirm_dedoublonne(
    client: TestClient, monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.routers.interviews_libre.generate_repartition_from_turns",
        lambda turns, axes: {"repartition": {}, "resume": "Résumé."},
    )
    mission_id = _mission(client)
    tours = {"turn_interlocuteur": ["Alice"], "turn_question": [""],
             "turn_remarque": ["Un propos."], "turn_section_title": [""]}
    page = client.post(
        f"/missions/{mission_id}/interviews/record-libre/synthese",
        data={"transcript": "Bonjour.", "session_token": "b9s-jeton", **tours},
    ).text
    assert 'name="session_token" value="b9s-jeton"' in page
    url = f"/missions/{mission_id}/interviews/record-libre/confirm"
    data = {"transcript": "Bonjour.", "resume": "Résumé.",
            "session_token": "b9s-jeton", **tours}
    r1 = client.post(url, data=data, follow_redirects=False)
    r2 = client.post(url, data=data, follow_redirects=False)
    assert r1.status_code == r2.status_code == 303
    with SessionLocal() as db:
        n = len(list(db.scalars(select(Interview.id).where(Interview.mission_id == mission_id))))
    assert n == 1


# --------------------------------------------------------------------------- #
# B-11 (suite) — import confirmé : un fichier antérieur à la mission est écarté
# --------------------------------------------------------------------------- #
def test_b11_import_confirm_ecarte_un_audio_anterieur_a_la_mission(
    client: TestClient, monkeypatch, tmp_path,
) -> None:
    import json

    from app.routers import interviews_gestion

    monkeypatch.setattr(interviews_gestion, "RECORDINGS_DIR", tmp_path)
    mission_id = _mission(client)
    nom = f"{mission_id}_ancien.webm"
    chemin = tmp_path / nom
    chemin.write_bytes(b"audio")
    vieux = time.time() - 3 * 24 * 3600
    os.utime(chemin, (vieux, vieux))
    proposed = json.dumps({"identity": {"interviewee_name": "Bob",
                                        "audio_backup_path": nom},
                           "answers": [], "verbatims": []})
    r = client.post(f"/missions/{mission_id}/interviews/import/confirm",
                    data={"proposed": proposed}, follow_redirects=False)
    assert r.status_code in (302, 303)
    with SessionLocal() as db:
        itw = db.scalar(select(Interview).where(Interview.mission_id == mission_id))
    assert itw is not None and itw.audio_backup_path is None


# --------------------------------------------------------------------------- #
# B-13 — SEGMENT_JOB_STALE_AFTER_S borné
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("brut, attendu", [("0", 60), ("-5", 60), ("1000000000", 86400),
                                           ("600", 600), ("abc", 2700)])
def test_b13_seuil_de_peremption_borne(monkeypatch, brut, attendu) -> None:
    monkeypatch.setenv("SEGMENT_JOB_STALE_AFTER_S", brut)
    assert interview_segment_jobs.segment_job_stale_after_s() == attendu


# --------------------------------------------------------------------------- #
# Ronde de correction (revue bmad-code-review du 2026-11-16)
# --------------------------------------------------------------------------- #
_TOURS = {"turn_interlocuteur": ["Alice"], "turn_question": [""],
          "turn_remarque": ["Un propos validé."], "turn_section_title": [""]}


def test_revue1_confirm_consomme_les_jobs_un_tour_valide_n_est_pas_relancable(
    client: TestClient,
) -> None:
    mission_id = _mission(client)
    with SessionLocal() as db:
        db.add(_job("rv1-jeton", 0, "failed"))
        db.commit()
    r = client.post(f"/missions/{mission_id}/interviews/record-libre/confirm",
                    data={"transcript": "Bonjour.", "session_token": "rv1-jeton", **_TOURS},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        itw = db.scalar(select(Interview).where(Interview.mission_id == mission_id))
        assert not structuration_libre.peut_relancer(itw)
        assert db.scalar(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == "rv1-jeton")) is None


def test_revue2_confirm_complete_un_a_transcrire_sans_tour_du_meme_jeton(
    client: TestClient,
) -> None:
    mission_id = _mission(client)
    iid = _interview(mission_id, "a_transcrire", "", segment_token="rv2-jeton")
    r = client.post(f"/missions/{mission_id}/interviews/record-libre/confirm",
                    data={"transcript": "Bonjour complet.", "resume": "Résumé validé.",
                          "session_token": "rv2-jeton", **_TOURS},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        itw = db.get(Interview, iid)
        assert [t.remarque for t in itw.turns] == ["Un propos validé."]
        assert itw.resume == "Résumé validé."
        assert itw.structuration_status == "fait"
        assert itw.raw_transcript == "Bonjour complet."
        n = len(list(db.scalars(select(Interview.id).where(Interview.mission_id == mission_id))))
    assert n == 1


def test_revue2_confirm_sur_un_autre_contenu_du_meme_jeton_rend_409(client: TestClient) -> None:
    mission_id = _mission(client)
    iid = _interview(mission_id, "a_traiter", "Du texte.", segment_token="rv2b-jeton")
    r = client.post(f"/missions/{mission_id}/interviews/record-libre/confirm",
                    data={"transcript": "Bonjour.", "session_token": "rv2b-jeton", **_TOURS},
                    follow_redirects=False)
    assert r.status_code == 409
    with SessionLocal() as db:
        assert db.get(Interview, iid).turns == []


def test_revue3_fiche_affiche_le_rang_des_parties_pas_la_position(client: TestClient) -> None:
    mission_id = _mission(client)
    segs = [{"filename": f"{mission_id}_p0.webm", "position": 0},
            {"filename": f"{mission_id}_p2.webm", "position": 2}]
    iid = _interview(mission_id, "fait", audio_segments=segs)
    page = client.get(f"/interviews/{iid}").text
    assert "Partie 1" in page and "Partie 2" in page
    assert "Partie 3" not in page


def test_revue4_une_tranche_perimee_ne_gele_pas_l_apercu(client: TestClient) -> None:
    jeton = "rv4-perime"
    with SessionLocal() as db:
        mort = _job(jeton, 0, "running")
        mort.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        db.add_all([mort, _job(jeton, 1, "done", "t1")])
        db.commit()
    r = client.get("/interviews/segment-jobs/turns",
                   params={"session_token": jeton, "since": 0}).json()
    assert [t["remarque"] for t in r["turns"]] == ["t1"]
    assert r["next_since"] == 2


def test_revue5_b5_compteurs_de_l_apercu_filtres_par_nature(client: TestClient) -> None:
    jeton = "rv5-kind"
    with SessionLocal() as db:
        db.add(_job(jeton, 0, "done", "t0"))
        autre = _job(jeton, 0, "failed")
        autre.kind = "answers"
        db.add(autre)
        db.commit()
    r = client.get("/interviews/segment-jobs/turns",
                   params={"session_token": jeton, "since": 0}).json()
    assert (r["done"], r["total"]) == (1, 1)


def test_revue5_b13_synthese_garde_question_et_remarque() -> None:
    from app.services.interview_libre_extract_ai import _turns_to_text

    texte = _turns_to_text([{"interlocuteur": "A", "question": "Pourquoi ?",
                             "remarque": "Parce que.", "section_title": None}])
    assert "Pourquoi ?" in texte and "Parce que." in texte


def test_revue5_b13_doublon_de_jeton_reprend_le_reliquat(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.routers.interviews_libre.planifier_structuration",
                        lambda *a, **k: True)
    mission_id = _mission(client)
    iid = _interview(mission_id, "a_transcrire", "", segment_token="rv5-tail")
    r = client.post(f"/missions/{mission_id}/interviews/record-libre/enregistrer",
                    data={"transcript": "Texte complet.", "session_token": "rv5-tail",
                          "segment_tail": "Reliquat final."},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.get(Interview, iid).segment_tail == "Reliquat final."


def test_revue6_b1_refus_de_mise_en_file_ne_change_pas_le_statut(
    client: TestClient, monkeypatch,
) -> None:
    monkeypatch.setattr(structuration_libre, "planifier_structuration",
                        lambda *a, **k: False)
    mission_id = _mission(client)
    iid = _interview(mission_id, "a_transcrire", "Du texte.")
    assert structuration_libre.structurer_texte_disponible(iid) is False
    assert _statut(iid) == "a_transcrire"


def test_revue6_b1_n_efface_pas_l_inscription_d_un_autre(client: TestClient) -> None:
    mission_id = _mission(client)
    iid = _interview(mission_id, "a_transcrire", "Du texte.")
    marque = object()
    with structuration_libre._verrou_registre:
        structuration_libre._en_vol[iid] = marque
    try:
        assert structuration_libre.structurer_texte_disponible(iid) is False
        assert structuration_libre._en_vol.get(iid) is marque
        assert _statut(iid) == "a_transcrire"
    finally:
        structuration_libre._liberer(iid, marque)


def test_revue7_b3_jeton_long_par_le_vrai_post_d_import(
    client: TestClient, monkeypatch, tmp_path,
) -> None:
    from app.routers import interviews_audio

    monkeypatch.setattr(interviews_audio, "RECORDINGS_DIR", tmp_path)
    monkeypatch.setattr(interviews_audio, "run_audio_file_job", lambda _id: None)
    mission_id = _mission(client)
    long_jeton = "z" * 100
    r = client.post("/audio/transcribe-file",
                    data={"session_token": long_jeton, "mission_id": mission_id},
                    files={"file": ("reunion.webm", b"audio", "audio/webm")})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    r = client.get("/audio/transcribe-file/status",
                   params={"job_id": job_id, "session_token": long_jeton})
    assert r.status_code == 200


def test_revue7_b6_echec_inattendu_n_ecrase_pas_un_done(monkeypatch) -> None:
    with SessionLocal() as db:
        job = InterviewSegmentJob(session_token="rv7-b6", position=0, kind="libre_turns",
                                  status="pending", text="Bonjour.")
        db.add(job)
        db.commit()
        job_id = job.id

    def _recuperee_puis_plantage(db, job, *a, **k):
        with SessionLocal() as autre:
            ligne = autre.get(InterviewSegmentJob, job_id)
            ligne.status = "done"
            ligne.turns_result = {"turns": [], "identity": {}}
            autre.commit()
        raise RuntimeError("plantage inattendu")

    monkeypatch.setattr(interview_segment_jobs, "_extract_with_retries", _recuperee_puis_plantage)
    interview_segment_jobs.run_segment_job(job_id)
    with SessionLocal() as db:
        assert db.get(InterviewSegmentJob, job_id).status == "done"


def test_revue7_b11_enregistrement_libre_ecarte_un_audio_anterieur(
    client: TestClient, monkeypatch, tmp_path,
) -> None:
    from app.routers import interviews_libre

    monkeypatch.setattr(interviews_libre, "RECORDINGS_DIR", tmp_path)
    monkeypatch.setattr(interviews_libre, "planifier_structuration", lambda *a, **k: True)
    mission_id = _mission(client)
    nom = f"{mission_id}_ancien.webm"
    (tmp_path / nom).write_bytes(b"audio")
    vieux = time.time() - 3 * 24 * 3600
    os.utime(tmp_path / nom, (vieux, vieux))
    r = client.post(f"/missions/{mission_id}/interviews/record-libre/enregistrer",
                    data={"transcript": "Du texte.", "session_token": "rv7-b11",
                          "audio_backup_path": nom},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        itw = db.scalar(select(Interview).where(Interview.mission_id == mission_id))
    assert itw.audio_backup_path is None


# --------------------------------------------------------------------------- #
# Revue finale de la Story 3.4 (2026-11-17, F1-F7)
# --------------------------------------------------------------------------- #
def _jeton_rendu(page: str) -> str:
    import re as _re

    trouve = _re.search(r'name="session_token" value="([^"]*)"', page)
    assert trouve is not None, "champ session_token absent de la page"
    return trouve.group(1)


def test_f1_retour_tours_repost_le_jeton_et_confirm_x2_un_seul_entretien(
    client: TestClient,
) -> None:
    mission_id = _mission(client)
    with SessionLocal() as db:
        db.add(_job("f1-jeton", 0, "failed"))
        db.commit()
    base = f"/missions/{mission_id}/interviews/record-libre"
    # Synthèse -> « Retour » : l'écran des tours doit REPOSTER le jeton.
    page = client.post(f"{base}/retour-tours",
                       data={"transcript": "Bonjour.", "session_token": "f1-jeton",
                             **_TOURS}).text
    jeton = _jeton_rendu(page)
    assert jeton == "f1-jeton"
    # La garde « aucun tour » de confirm le repost aussi.
    page_vide = client.post(f"{base}/confirm",
                            data={"transcript": "Bonjour.", "session_token": "f1-jeton"}).text
    assert _jeton_rendu(page_vide) == "f1-jeton"
    data = {"transcript": "Bonjour.", "session_token": jeton, **_TOURS}
    r1 = client.post(f"{base}/confirm", data=data, follow_redirects=False)
    r2 = client.post(f"{base}/confirm", data=data, follow_redirects=False)
    assert r1.status_code == r2.status_code == 303
    with SessionLocal() as db:
        ids = list(db.scalars(select(Interview.id).where(Interview.mission_id == mission_id)))
        restants = list(db.scalars(select(InterviewSegmentJob.id).where(
            InterviewSegmentJob.session_token == "f1-jeton")))
    assert len(ids) == 1
    assert restants == []


def test_f2_deux_confirm_concurrents_ne_dupliquent_pas_les_tours(client: TestClient) -> None:
    from app.routers import interviews_libre

    mission_id = _mission(client)
    iid = _interview(mission_id, "a_transcrire", "", segment_token="f2-jeton",
                     tranches_manquantes=2)
    tours = [{"interlocuteur": "Alice", "question": None,
              "remarque": "Un propos validé.", "section_title": None}]
    # Entrelacement : les DEUX requêtes lisent « a_transcrire sans tour » avant
    # que l'une n'écrive.
    with SessionLocal() as a, SessionLocal() as b:
        ea, eb = a.get(Interview, iid), b.get(Interview, iid)
        assert ea.turns == [] and eb.turns == []
        assert ea.structuration_status == eb.structuration_status == "a_transcrire"
        ra = interviews_libre._confirm_sur_jeton_existant(
            a, a.get(Mission, mission_id), ea, tours, "Bonjour.", "Résumé.", None)
        rb = interviews_libre._confirm_sur_jeton_existant(
            b, b.get(Mission, mission_id), eb, tours, "Bonjour.", "Résumé.", None)
    assert ra.status_code == rb.status_code == 303
    with SessionLocal() as db:
        itw = db.get(Interview, iid)
        assert [t.remarque for t in itw.turns] == ["Un propos validé."]
        assert itw.structuration_status == "fait"
        # Option conservatrice : le signal d'aperçu tronqué est GARDÉ.
        assert itw.tranches_manquantes == 2


def test_f7_completion_a_transcrire_consomme_les_jobs_de_tranche(client: TestClient) -> None:
    mission_id = _mission(client)
    _interview(mission_id, "a_transcrire", "", segment_token="f7-jeton")
    with SessionLocal() as db:
        db.add(_job("f7-jeton", 0, "failed"))
        db.commit()
    r = client.post(f"/missions/{mission_id}/interviews/record-libre/confirm",
                    data={"transcript": "Bonjour.", "session_token": "f7-jeton", **_TOURS},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.scalar(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == "f7-jeton")) is None


def test_f3_tranche_finie_entre_les_deux_lectures_n_est_pas_sautee(
    client: TestClient, monkeypatch,
) -> None:
    from app.routers import interviews_segment_jobs as routeur

    jeton = "f3-course"
    with SessionLocal() as db:
        db.add_all([_job(jeton, 0, "done", "t0"), _job(jeton, 1, "running"),
                    _job(jeton, 2, "done", "t2")])
        db.commit()
    origine = routeur.segment_jobs_status_light

    def _puis_la_tranche_1_finit(*a, **k):
        resultat = origine(*a, **k)
        with SessionLocal() as autre:
            ligne = autre.scalar(select(InterviewSegmentJob).where(
                InterviewSegmentJob.session_token == jeton,
                InterviewSegmentJob.position == 1))
            ligne.status = "done"
            ligne.turns_result = {"turns": [{"interlocuteur": "A", "question": None,
                                             "remarque": "t1", "section_title": None}],
                                  "identity": {}}
            autre.commit()
        return resultat

    monkeypatch.setattr(routeur, "segment_jobs_status_light", _puis_la_tranche_1_finit)
    r1 = client.get("/interviews/segment-jobs/turns",
                    params={"session_token": jeton, "since": 0}).json()
    monkeypatch.setattr(routeur, "segment_jobs_status_light", origine)
    r2 = client.get("/interviews/segment-jobs/turns",
                    params={"session_token": jeton, "since": r1["next_since"]}).json()
    vus = [t["remarque"] for t in r1["turns"] + r2["turns"]]
    assert vus == ["t0", "t1", "t2"]


def test_f5_run_segment_job_n_ecrase_pas_un_done(monkeypatch) -> None:
    resultat = {"turns": [{"interlocuteur": "A", "question": None,
                           "remarque": "déjà là", "section_title": None}], "identity": {}}
    with SessionLocal() as db:
        job = InterviewSegmentJob(session_token="f5-run", position=0, kind="libre_turns",
                                  status="done", text="Bonjour.", turns_result=resultat)
        db.add(job)
        db.commit()
        job_id = job.id
    appels: list = []

    def _extraction(db, job, *a, **k):
        with SessionLocal() as autre:
            appels.append(autre.get(InterviewSegmentJob, job_id).status)
        return {"turns": [], "identity": {}}

    monkeypatch.setattr(interview_segment_jobs, "_extract_with_retries", _extraction)
    interview_segment_jobs.run_segment_job(job_id)
    assert appels == []  # ni repassé `running`, ni ré-extrait
    with SessionLocal() as db:
        ligne = db.get(InterviewSegmentJob, job_id)
        assert ligne.status == "done" and ligne.turns_result == resultat


def test_f5_recuperation_synchrone_n_ecrase_pas_un_done(monkeypatch) -> None:
    with SessionLocal() as db:
        job = InterviewSegmentJob(session_token="f5-rec", position=0, kind="libre_turns",
                                  status="pending", text="Bonjour.")
        db.add(job)
        db.commit()
        job_id = job.id

    def _fond_aboutit_puis_echec(db, job, *a, **k):
        with SessionLocal() as autre:
            ligne = autre.get(InterviewSegmentJob, job_id)
            ligne.status = "done"
            ligne.turns_result = {"turns": [], "identity": {}}
            autre.commit()
        raise InterviewLibreExtractAIError("IA indisponible")

    monkeypatch.setattr(interview_segment_jobs, "_extract_with_retries",
                        _fond_aboutit_puis_echec)
    with SessionLocal() as db:
        job = db.get(InterviewSegmentJob, job_id)
        interview_segment_jobs.recover_stalled_or_failed_jobs(db, [job])
        assert job.status == "done"  # l'objet relu, vu par la fusion qui suit
    with SessionLocal() as db:
        assert db.get(InterviewSegmentJob, job_id).status == "done"


def test_f7_etat_audio_applique_la_garde_de_mission(monkeypatch, tmp_path) -> None:
    from app.routers import interviews_libre

    monkeypatch.setattr(interviews_libre, "RECORDINGS_DIR", tmp_path)
    nom = "5_ancien.webm"
    (tmp_path / nom).write_bytes(b"audio")
    vieux = time.time() - 3 * 24 * 3600
    os.utime(tmp_path / nom, (vieux, vieux))
    mission = SimpleNamespace(id=5, created_at=datetime.now(timezone.utc).replace(tzinfo=None))
    identite = {"audio_backup_path": nom, "audio_segments": "[]"}
    # Fichier antérieur à la mission (id SQLite réutilisé) : ce n'est pas SON audio.
    assert interviews_libre._etat_audio(mission, identite) == (False, 0)


def test_f7_apercu_q_r_filtre_la_nature_des_tranches(client: TestClient) -> None:
    jeton = "f7-kind"
    with SessionLocal() as db:
        db.add(_job(jeton, 0, "done", "un tour libre"))  # kind="libre_turns"
        reponse = _job(jeton, 0, "failed")
        reponse.kind = "answers"
        db.add(reponse)
        db.commit()
    r = client.get("/interviews/segment-jobs/answers", params={"session_token": jeton})
    assert r.status_code == 200
    corps = r.json()
    assert (corps["done"], corps["total"]) == (0, 1)
    assert corps["answers"] == {}
