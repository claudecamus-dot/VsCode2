"""Tests de la répartition question/réponse « au fil de l'eau » du mode
STRUCTURÉ (`record.html`, 2026-07-25) — portage du dispositif de jobs de
tranche du wizard libre (`kind="answers"`) :

- pendant l'enregistrement, chaque tranche de ~5 min de texte transcrit est
  soumise comme job de fond et répartie sur les questions de la trame
  (`extract_answers_from_text`) pendant que la transcription CONTINUE ;
- l'onglet « Répartition (Q/R) » affiche le résultat en lecture seule au fil
  des jobs terminés (`GET /interviews/segment-jobs/answers`) ;
- à l'envoi, seul le reliquat (`segment_tail`) est traité en synchrone, puis
  fusion « première réponse non vide par question » — JAMAIS de retraitement
  de la transcription entière (mêmes garanties que le Palier 2 libre).

Comme `test_interview_segment_jobs.py`, l'IA est monkeypatchée — aucun appel
réseau. Le `TestClient` de Starlette exécute les `BackgroundTasks` en
synchrone dans la requête.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import InterviewSegmentJob, Mission, Question, Theme, Trame
from app.routers import interviews as interviews_router
from app.services import interview_segment_jobs


def setup_module() -> None:
    # dispose() AVANT unlink : le pool partagé du fichier de tests précédent
    # garde sinon un verrou Windows sur DB_PATH (déterministe selon l'ordre de
    # collecte, invisible en exécution isolée).
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
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # L'écran d'enregistrement ne se rend que si faster-whisper est présent —
    # indépendant de ce qu'on teste ici.
    monkeypatch.setattr(
        interviews_router.audio_transcribe, "is_available", lambda: True
    )
    return TestClient(app)


def _make_structured_mission() -> tuple[int, list[int]]:
    """Mission avec trame (1 thème, 2 questions) — renvoie (mission_id, [qids])."""
    db = SessionLocal()
    try:
        mission = Mission(name="Mission structurée")
        trame = Trame(name="Trame test", mission=mission)
        theme = Theme(title="Organisation", position=0, trame=trame)
        q1 = Question(label="Comment est structurée l'équipe ?", theme=theme)
        q2 = Question(label="Quels outils utilisez-vous ?", theme=theme)
        db.add(mission)
        db.commit()
        return mission.id, [q1.id, q2.id]
    finally:
        db.close()


def _answers_payload(qid: int, text: str) -> dict:
    return {"answers": {str(qid): {"text": text, "verbatims": []}}}


# --------------------------------------------------------------------------- #
# Service — tâche de fond kind="answers"
# --------------------------------------------------------------------------- #
def test_run_segment_job_answers_success(monkeypatch: pytest.MonkeyPatch) -> None:
    mission_id, qids = _make_structured_mission()

    def _extract(questions, text):
        # Les questions de la trame de la mission sont bien transmises.
        assert [q.id for q in questions] == qids
        return {qids[0]: {"text": "Équipe de 8 personnes", "verbatims": ["on est 8"]}}

    monkeypatch.setattr(interview_segment_jobs, "extract_answers_from_text", _extract)
    db = SessionLocal()
    job = InterviewSegmentJob(session_token="ans-ok", position=0, status="pending",
                              text="une tranche de texte", kind="answers",
                              mission_id=mission_id)
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    interview_segment_jobs.run_segment_job(job_id)

    db = SessionLocal()
    refreshed = db.get(InterviewSegmentJob, job_id)
    assert refreshed.status == "done"
    # Clés str : JSON SQLite ne préserve pas les clés int.
    assert refreshed.turns_result["answers"][str(qids[0])]["text"] == "Équipe de 8 personnes"
    assert refreshed.error is None
    db.close()


def test_run_segment_job_answers_without_trame_fails_cleanly() -> None:
    """Mission sans trame (ou supprimée entre-temps) : le job se met en
    `failed` avec un message clair — jamais de crash de tâche de fond."""
    db = SessionLocal()
    mission = Mission(name="Sans trame")
    db.add(mission)
    db.commit()
    job = InterviewSegmentJob(session_token="ans-notrame", position=0,
                              status="pending", text="texte", kind="answers",
                              mission_id=mission.id)
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    interview_segment_jobs.run_segment_job(job_id)

    db = SessionLocal()
    refreshed = db.get(InterviewSegmentJob, job_id)
    assert refreshed.status == "failed"
    assert "trame" in refreshed.error
    db.close()


def test_merge_segment_answers_concatenates_continuations_in_position_order() -> None:
    """Règle de fusion (revue adversariale 2026-07-25) : les tranches sont des
    fenêtres TEMPORELLES — une réponse à cheval sur une frontière de 5 min est
    vue par deux tranches, sa continuation est CONCATÉNÉE (ordre des
    positions) au lieu d'être perdue en first-wins ; une redite exacte est
    ignorée ; les verbatims sont cumulés sans doublon. Clés reconverties en
    int."""
    j0 = InterviewSegmentJob(
        session_token="t", position=0, status="done", kind="answers",
        turns_result={"answers": {"1": {
            "text": "Début de réponse (tranche 0)",
            "verbatims": ["citation A"],
        }}})
    j1 = InterviewSegmentJob(
        session_token="t", position=1, status="done", kind="answers",
        turns_result={"answers": {
            "1": {"text": "Suite de la réponse (tranche 1)",
                  "verbatims": ["citation A", "citation B"]},
            "2": {"text": "Réponse tranche 1", "verbatims": []},
        }})
    tail = {2: {"text": "Réponse tranche 1", "verbatims": []},  # redite exacte : ignorée
            3: {"text": "Réponse reliquat", "verbatims": []}}
    # Volontairement dans le désordre pour vérifier le tri par position.
    merged = interview_segment_jobs.merge_segment_answers([j1, j0], tail)
    assert merged == {
        1: {"text": "Début de réponse (tranche 0)\nSuite de la réponse (tranche 1)",
            "verbatims": ["citation A", "citation B"]},
        2: {"text": "Réponse tranche 1", "verbatims": []},
        3: {"text": "Réponse reliquat", "verbatims": []},
    }


def test_recover_answers_job_uses_only_its_own_slice(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id, qids = _make_structured_mission()
    calls = []

    def _extract(questions, text):
        calls.append(text)
        return {qids[0]: {"text": "Récupérée", "verbatims": []}}

    monkeypatch.setattr(interview_segment_jobs, "extract_answers_from_text", _extract)
    db = SessionLocal()
    job = InterviewSegmentJob(session_token="t", position=0, status="failed",
                              text="texte de la tranche seule", error="timeout",
                              kind="answers", mission_id=mission_id)
    db.add(job)
    db.commit()

    interview_segment_jobs.recover_stalled_or_failed_jobs(db, [job])

    assert job.status == "done"
    assert job.turns_result["answers"][str(qids[0])]["text"] == "Récupérée"
    assert calls == ["texte de la tranche seule"]
    db.close()


# --------------------------------------------------------------------------- #
# HTTP — création de job kind="answers" + aperçu live
# --------------------------------------------------------------------------- #
def test_create_segment_job_answers_processes_in_background(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id, qids = _make_structured_mission()
    monkeypatch.setattr(
        interview_segment_jobs, "extract_answers_from_text",
        lambda questions, text: {qids[0]: {"text": "Depuis le job", "verbatims": []}},
    )
    resp = client.post(
        "/interviews/segment-jobs",
        data={"session_token": "http-ans", "position": "0",
              "text": "tranche de texte", "kind": "answers",
              "mission_id": str(mission_id)},
    )
    assert resp.status_code == 200

    live = client.get("/interviews/segment-jobs/answers",
                      params={"session_token": "http-ans"})
    body = live.json()
    assert body["total"] == 1
    assert body["done"] == 1
    assert body["answers"][str(qids[0])]["text"] == "Depuis le job"


def test_create_segment_job_rejects_unknown_kind(client: TestClient) -> None:
    resp = client.post(
        "/interviews/segment-jobs",
        data={"session_token": "bad-kind", "position": "0", "text": "x",
              "kind": "nimporte"},
    )
    assert resp.status_code == 400


def test_create_segment_job_answers_requires_existing_mission(
    client: TestClient,
) -> None:
    resp = client.post(
        "/interviews/segment-jobs",
        data={"session_token": "no-mission", "position": "0", "text": "x",
              "kind": "answers", "mission_id": "999999"},
    )
    assert resp.status_code == 404


def test_answers_endpoint_ignores_jobs_not_done(client: TestClient) -> None:
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="ans-live", position=0, status="done",
                               text="x", kind="answers",
                               turns_result=_answers_payload(7, "Visible")))
    db.add(InterviewSegmentJob(session_token="ans-live", position=1, status="running",
                               text="x", kind="answers"))
    db.commit()
    db.close()

    resp = client.get("/interviews/segment-jobs/answers",
                      params={"session_token": "ans-live"})
    body = resp.json()
    assert body["total"] == 2
    assert body["done"] == 1
    assert body["answers"] == {"7": {"text": "Visible", "verbatims": []}}


# --------------------------------------------------------------------------- #
# HTTP — soumission record : jobs + reliquat, jamais la transcription entière
# --------------------------------------------------------------------------- #
def test_record_merges_jobs_and_tail_never_whole_transcript(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id, qids = _make_structured_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token="rec-merge", position=0, status="done", text="x",
        kind="answers", mission_id=mission_id,
        turns_result=_answers_payload(qids[0], "Réponse issue du job"),
    ))
    db.commit()
    db.close()

    calls = []
    GIANT = "MARQUEUR_TRANSCRIPTION_COMPLETE_JAMAIS_ATTENDU"

    def _extract(questions, text):
        calls.append(text)
        assert GIANT not in text, (
            "la transcription entière a été passée à l'IA — le mur synchrone "
            "est de retour"
        )
        return {qids[1]: {"text": "Réponse du reliquat", "verbatims": []}}

    monkeypatch.setattr(interviews_router, "extract_answers_from_text", _extract)
    monkeypatch.setattr(interview_segment_jobs, "extract_answers_from_text", _extract)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record",
        data={"transcript": GIANT + " (simule 2h d'entretien)",
              "session_token": "rec-merge",
              "segment_tail": "reliquat non couvert"},
    )
    assert resp.status_code == 200
    assert "Réponse issue du job" in resp.text
    assert "Réponse du reliquat" in resp.text
    # Un seul appel IA : le reliquat, jamais la transcription complète.
    assert calls == ["reliquat non couvert"]
    # Jobs consommés puis supprimés.
    db = SessionLocal()
    remaining = db.scalars(
        select(InterviewSegmentJob).where(InterviewSegmentJob.session_token == "rec-merge")
    ).all()
    assert remaining == []
    db.close()


def test_record_no_jobs_uses_synchronous_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-régression : un entretien court (aucun job) suit le chemin
    synchrone historique — seul cas où extract_answers_from_text voit la
    transcription entière."""
    mission_id, qids = _make_structured_mission()
    monkeypatch.setattr(
        interviews_router, "extract_answers_from_text",
        lambda questions, text: {qids[0]: {"text": "Extraction synchrone", "verbatims": []}},
    )
    resp = client.post(
        f"/missions/{mission_id}/interviews/record",
        data={"transcript": "un entretien court", "session_token": ""},
    )
    assert resp.status_code == 200
    assert "Extraction synchrone" in resp.text


def test_record_shows_wait_screen_when_jobs_running(client: TestClient) -> None:
    mission_id, _ = _make_structured_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="rec-wait", position=0,
                               status="running", text="x", kind="answers",
                               mission_id=mission_id))
    db.commit()
    db.close()

    resp = client.post(
        f"/missions/{mission_id}/interviews/record",
        data={"transcript": "une longue transcription", "session_token": "rec-wait"},
    )
    assert resp.status_code == 200
    assert "Traitement des tranches" in resp.text
    # L'écran d'attente poste la finalisation vers record/from-jobs.
    assert "interviews/record/from-jobs" in resp.text


def test_record_from_jobs_merges_done_answers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id, qids = _make_structured_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token="rec-fromjobs", position=0, status="done", text="x",
        kind="answers", mission_id=mission_id,
        turns_result=_answers_payload(qids[0], "Réponse du job"),
    ))
    db.commit()
    db.close()

    monkeypatch.setattr(
        interviews_router, "extract_answers_from_text",
        lambda questions, text: {qids[1]: {"text": "Réponse du reliquat", "verbatims": []}},
    )
    resp = client.post(
        f"/missions/{mission_id}/interviews/record/from-jobs",
        data={"transcript": "transcription complète",
              "session_token": "rec-fromjobs",
              "segment_tail": "reliquat"},
    )
    assert resp.status_code == 200
    assert "Réponse du job" in resp.text
    assert "Réponse du reliquat" in resp.text


def test_record_recovers_failed_job_individually(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id, qids = _make_structured_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token="rec-failed", position=0, status="failed",
        text="texte de la tranche échouée", error="timeout",
        kind="answers", mission_id=mission_id,
    ))
    db.commit()
    db.close()

    calls = []

    def _extract(questions, text):
        calls.append(text)
        return {qids[0]: {"text": "Tranche récupérée", "verbatims": []}}

    monkeypatch.setattr(interview_segment_jobs, "extract_answers_from_text", _extract)
    monkeypatch.setattr(interviews_router, "extract_answers_from_text", _extract)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record",
        data={"transcript": "transcription complète (jamais retraitée)",
              "session_token": "rec-failed", "segment_tail": ""},
    )
    assert resp.status_code == 200
    assert "Tranche récupérée" in resp.text
    # La récupération n'a traité QUE la tranche du job en échec.
    assert calls == ["texte de la tranche échouée"]


def test_record_all_jobs_fail_surfaces_actionable_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parité B1 avec le libre : quand tous les jobs échouent (ex. timeout
    Ollama), l'écran resurface le message ACTIONABLE du job, pas le générique
    « aucune réponse détectée »."""
    mission_id, _ = _make_structured_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token="rec-allfail", position=0, status="failed",
        text="texte de la tranche", error="ancienne erreur",
        kind="answers", mission_id=mission_id,
    ))
    db.commit()
    db.close()

    ACTIONABLE = "Ollama n a pas repondu a temps — augmentez OLLAMA_TIMEOUT."

    def _boom(questions, text):
        raise interviews_router.InterviewExtractAIError(ACTIONABLE)

    monkeypatch.setattr(interview_segment_jobs, "extract_answers_from_text", _boom)
    monkeypatch.setattr(interviews_router, "extract_answers_from_text", _boom)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record",
        data={"transcript": "un entretien", "session_token": "rec-allfail",
              "segment_tail": ""},
    )
    assert resp.status_code == 200
    assert "OLLAMA_TIMEOUT" in resp.text
    assert "Aucune réponse détectée" not in resp.text


def test_record_plafonne_la_recuperation_synchrone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plafond du chemin FRÈRE (revue R3-M1 du 2026-08-31) : quand le plafond a
    été posé sur le mode libre, la récupération synchrone du mode paramétré est
    restée sans borne (leçon apply-the-lesson-to-sibling-paths) — un entretien
    sur trame de 2 h avec Ollama saturé enchaînait ~24 × (timeout + relance)
    dans un seul POST /record.

    Le test échoue sur le code d'avant : 6 appels au lieu de 3."""
    from app.routers.interviews import RECUP_TRANCHES_MAX

    mission_id, _ = _make_structured_mission()
    db = SessionLocal()
    for pos in range(6):
        db.add(InterviewSegmentJob(
            session_token="rec-plafond", position=pos, status="failed",
            text=f"tranche {pos}", error="Ollama saturé",
            kind="answers", mission_id=mission_id,
        ))
    db.commit()
    db.close()

    appels: list[str] = []

    def _boom(questions, text):
        appels.append(text)
        raise interviews_router.InterviewExtractAIError("Ollama saturé")

    monkeypatch.setattr(interview_segment_jobs, "extract_answers_from_text", _boom)
    monkeypatch.setattr(interviews_router, "extract_answers_from_text", _boom)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record",
        data={"transcript": "un entretien long", "session_token": "rec-plafond",
              "segment_tail": ""},
    )
    assert resp.status_code == 200
    assert len(appels) <= RECUP_TRANCHES_MAX, (
        f"{len(appels)} tranches retraitées dans un seul POST — le plafond "
        f"({RECUP_TRANCHES_MAX}) ne s'applique pas au chemin paramétré"
    )


def _six_jobs_dont_un_en_echec_ancien(mission_id: int, token: str) -> None:
    """Position 0 porte une erreur ANCIENNE ; 1..5 n'ont jamais été tentées.

    Le tri `(error is not None, position)` met donc les jamais-tentées devant :
    la fenêtre de cet envoi est [1, 2, 3], et la position 0 — porteuse de
    l'erreur périmée — n'en fait PAS partie.
    """
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token=token, position=0, status="failed", text="tranche 0",
        error="CAUSE PERIMEE", kind="answers", mission_id=mission_id,
    ))
    for pos in range(1, 6):
        db.add(InterviewSegmentJob(
            session_token=token, position=pos, status="failed",
            text=f"tranche {pos}", error=None,
            kind="answers", mission_id=mission_id,
        ))
    db.commit()
    db.close()


def test_record_dit_ou_on_en_est_quand_le_plafond_bloque(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N1 (2026-09-01) : R3-M1 a posé le plafond sur ce chemin sans porter le
    message qui le rend tenable. Avec 6 tranches en échec il faut 2 envois, et
    l'ancien texte rendait des pages IDENTIQUES dont la promesse « seules les
    tranches en échec seront retraitées » était devenue fausse (au plus
    RECUP_TRANCHES_MAX le sont). L'utilisateur croyait que rien n'avançait.

    Échoue sur le code d'avant : le message ne portait aucun compteur."""
    from app.routers.interviews import RECUP_TRANCHES_MAX

    mission_id, qids = _make_structured_mission()
    _six_jobs_dont_un_en_echec_ancien(mission_id, "rec-progres")

    def _ok(questions, text):
        return {qids[0]: {"text": "réponse", "verbatims": []}}

    monkeypatch.setattr(interview_segment_jobs, "extract_answers_from_text", _ok)
    monkeypatch.setattr(interviews_router, "extract_answers_from_text", _ok)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record",
        data={"transcript": "un entretien long", "session_token": "rec-progres",
              "segment_tail": ""},
    )
    assert resp.status_code == 200
    # Sous-chaînes sans apostrophe : Jinja échappe `'` en `&#39;` dans le rendu.
    assert "tranche(s) viennent" in resp.text, (
        "la page ne dit pas que des tranches ont été récupérées — sans ce "
        "compteur, les N envois nécessaires rendent des pages identiques"
    )
    assert f"reste {6 - RECUP_TRANCHES_MAX} sur 6" in resp.text, (
        "la page ne chiffre pas ce qui reste : l'utilisateur ne peut pas "
        "distinguer « ça avance » de « ça ne bougera plus »"
    )


def test_record_montre_le_levier_meme_quand_ca_progresse(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4 (re-revue 2026-09-01) : quand 1 tranche sur 3 est récupérée et que les
    2 autres échouent avec un message ACTIONNABLE frais (« augmente
    OLLAMA_TIMEOUT »), la branche « ça progresse » avalait l'erreur. Sur
    24 tranches à 1 récupérée par envoi, l'utilisateur enchaîne les envois sans
    jamais voir le levier qui débloquerait tout.

    Échoue sur le code d'avant F4 : la branche de progrès ne portait aucune
    erreur."""
    mission_id, qids = _make_structured_mission()
    _six_jobs_dont_un_en_echec_ancien(mission_id, "rec-levier")

    appels: list[str] = []

    def _un_sur_trois(questions, text):
        appels.append(text)
        if len(appels) == 1:
            return {qids[0]: {"text": "réponse", "verbatims": []}}
        raise interviews_router.InterviewExtractAIError(
            "Ollama a expiré — augmente OLLAMA_TIMEOUT"
        )

    monkeypatch.setattr(
        interview_segment_jobs, "extract_answers_from_text", _un_sur_trois
    )
    monkeypatch.setattr(interviews_router, "extract_answers_from_text", _un_sur_trois)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record",
        data={"transcript": "un entretien long", "session_token": "rec-levier",
              "segment_tail": ""},
    )
    assert resp.status_code == 200
    assert "tranche(s) viennent" in resp.text, "le compteur de progrès a disparu"
    assert "OLLAMA_TIMEOUT" in resp.text, (
        "la branche « ça progresse » avale le message actionnable des tranches "
        "qui viennent d'échouer — le levier reste invisible envoi après envoi"
    )


def test_record_ne_resurface_pas_une_erreur_perimee(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N2 (2026-09-01) : corollaire du plafond. `job_error` était pris sur la
    plus basse tranche encore KO — donc, depuis le plafond, presque toujours
    une tranche NON retentée à cet envoi, dont l'`error` date d'un envoi
    antérieur. La page annonçait « Ollama saturé » alors qu'Ollama répondait,
    et poussait à cesser de relancer précisément quand relancer marche.

    Échoue sur le code d'avant : « CAUSE PERIMEE » (position 0, hors fenêtre)
    remontait à l'écran."""
    mission_id, _ = _make_structured_mission()
    _six_jobs_dont_un_en_echec_ancien(mission_id, "rec-perimee")

    def _boom(questions, text):
        raise interviews_router.InterviewExtractAIError("cause fraiche")

    monkeypatch.setattr(interview_segment_jobs, "extract_answers_from_text", _boom)
    monkeypatch.setattr(interviews_router, "extract_answers_from_text", _boom)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record",
        data={"transcript": "un entretien long", "session_token": "rec-perimee",
              "segment_tail": ""},
    )
    assert resp.status_code == 200
    assert "CAUSE PERIMEE" not in resp.text, (
        "la page resurface l'erreur d'une tranche NON retentée à cet envoi — "
        "elle désigne une cause déjà révolue et fait renoncer l'utilisateur"
    )
    assert "cause fraiche" in resp.text, (
        "la page doit porter l'erreur des tranches réellement retentées"
    )


def test_record_blocks_finalize_when_a_job_stays_failed_with_content(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revue adversariale 2026-07-25 (constat haute sévérité) : un job qui
    reste en échec APRÈS récupération, alors qu'un frère a produit des
    réponses, faisait afficher la revue SANS sa tranche (jusqu'à 5 min de
    propos) et `delete_segment_jobs` détruisait le texte — perte silencieuse.
    Désormais : écran d'erreur avec le message actionnable du job, et les
    jobs (dont les `done`) sont CONSERVÉS pour un nouvel essai borné."""
    mission_id, qids = _make_structured_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token="rec-partial", position=0, status="done", text="x",
        kind="answers", mission_id=mission_id,
        turns_result=_answers_payload(qids[0], "Réponse déjà réussie"),
    ))
    db.add(InterviewSegmentJob(
        session_token="rec-partial", position=1, status="failed",
        text="tranche qui échoue encore", error="timeout",
        kind="answers", mission_id=mission_id,
    ))
    db.commit()
    db.close()

    def _boom(questions, text):
        raise interviews_router.InterviewExtractAIError(
            "Ollama n a pas repondu a temps — augmentez OLLAMA_TIMEOUT."
        )

    monkeypatch.setattr(interview_segment_jobs, "extract_answers_from_text", _boom)
    monkeypatch.setattr(interviews_router, "extract_answers_from_text", _boom)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record",
        data={"transcript": "transcription", "session_token": "rec-partial",
              "segment_tail": ""},
    )
    assert resp.status_code == 200
    assert "OLLAMA_TIMEOUT" in resp.text          # message actionnable
    assert "Réponse déjà réussie" not in resp.text  # PAS l'écran de revue
    # Les jobs survivent : un nouvel essai ne recoûtera que la tranche KO.
    db = SessionLocal()
    remaining = db.scalars(
        select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == "rec-partial"
        )
    ).all()
    assert {j.status for j in remaining} == {"done", "failed"}
    db.close()


def test_record_without_trame_shows_error_not_500(client: TestClient) -> None:
    """Revue adversariale 2026-07-25 : trame supprimée pendant
    l'enregistrement → `_mission_questions` levait AttributeError (500 brut,
    transcription et bouton PDF de secours perdus). Désormais : écran d'erreur
    propre qui préserve le formulaire."""
    db = SessionLocal()
    mission = Mission(name="Sans trame record")
    db.add(mission)
    db.commit()
    mission_id = mission.id
    db.close()

    resp = client.post(
        f"/missions/{mission_id}/interviews/record",
        data={"transcript": "du texte transcrit", "session_token": ""},
    )
    assert resp.status_code == 200
    assert "trame" in resp.text


def test_create_segment_job_answers_requires_trame_with_questions(
    client: TestClient,
) -> None:
    db = SessionLocal()
    mission = Mission(name="Sans trame jobs")
    db.add(mission)
    db.commit()
    mission_id = mission.id
    db.close()

    resp = client.post(
        "/interviews/segment-jobs",
        data={"session_token": "no-trame", "position": "0", "text": "x",
              "kind": "answers", "mission_id": str(mission_id)},
    )
    assert resp.status_code == 400


def test_create_segment_job_purges_stale_orphan_jobs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revue adversariale 2026-07-25 : les jobs d'une session jamais finalisée
    (Recommencer, wizard abandonné) portent du contenu d'entretien et
    n'étaient JAMAIS purgés — balayés passé 7 jours à la création d'un job."""
    from datetime import datetime, timedelta, timezone

    mission_id, qids = _make_structured_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token="vieille-session", position=0, status="done",
        text="propos d'entretien abandonnés",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10),
    ))
    db.commit()
    db.close()

    monkeypatch.setattr(
        interview_segment_jobs, "extract_answers_from_text",
        lambda questions, text: {},
    )
    client.post(
        "/interviews/segment-jobs",
        data={"session_token": "session-neuve", "position": "0", "text": "x",
              "kind": "answers", "mission_id": str(mission_id)},
    )

    db = SessionLocal()
    old = db.scalars(select(InterviewSegmentJob).where(
        InterviewSegmentJob.session_token == "vieille-session")).all()
    assert old == []  # purgé
    db.close()


# --------------------------------------------------------------------------- #
# Gabarit — l'écran d'enregistrement embarque le dispositif au fil de l'eau
# --------------------------------------------------------------------------- #
def test_record_form_renders_live_repartition_markers(client: TestClient) -> None:
    """Régression de gabarit (un screenshot prouve « ça marche maintenant »,
    pas « ça ne régressera pas ») : l'écran d'enregistrement structuré doit
    porter l'onglet Répartition (Q/R) avec les questions de la trame, les
    champs de session et le JS de soumission de jobs 5 min."""
    mission_id, _ = _make_structured_mission()
    resp = client.get(f"/missions/{mission_id}/interviews/record")
    assert resp.status_code == 200
    for marker in (
        "Répartition (Q/R)",                      # onglet
        "Quels outils utilisez-vous ?",           # questions de la trame rendues
                                                  # (l'autre libellé porte une
                                                  # apostrophe échappée par Jinja)
        "rec-session-token",                      # champs de session portés au POST
        "rec-segment-tail",
        "JOB_SEGMENT_MS",                         # timer de soumission 5 min
        "/interviews/segment-jobs",               # création de job côté JS
        "segment-jobs/answers",                   # poll de l'aperçu live
        "'answers'",                              # kind soumis par le JS
        "pendingSegmentJobSubmits",               # gate anti-duplication
    ):
        assert marker in resp.text, f"marqueur absent du gabarit : {marker}"
