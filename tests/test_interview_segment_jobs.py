"""Tests du traitement asynchrone des tranches d'entretien libre (Palier 2 —
`docs/reflexions/enregistrement-segmente-30min.md` §4), y compris les 3
correctifs du 2026-07-20 suite à une revue adversariale (Blind Hunter + Edge
Case Hunter) :

1. Le fallback de finalisation ne retraite plus JAMAIS la transcription
   ENTIÈRE — seuls les jobs `failed`/bloqués sont re-traités individuellement
   sur leur propre tranche (`recover_stalled_or_failed_jobs`).
2. Un job resté `pending`/`running` trop longtemps (`created_at` périmé) est
   détecté comme `stale` et traité comme un échec pour la finalisation — plus
   d'attente infinie sur un job perdu (crash de tâche de fond, redémarrage
   serveur).
3. Le texte d'une tranche est désormais persisté sur le job (colonne `text`,
   pas seulement porté par la closure de la tâche de fond) — un job survit à
   un redémarrage serveur et peut être re-traité a posteriori.

La race de duplication (soumission utilisateur pendant qu'un POST de création
de job est en vol) est un correctif purement côté JS (`record_libre.html` —
gate `pendingSegmentJobSubmits` sur le bouton "Extraire", même pattern que
`pendingSegments`) : non testable en pytest, vérifié par lecture de code et
par le rendu réel (`run-dev-server`).

Comme `test_interview_libre.py`, l'IA (`extract_turns_from_text`) est
monkeypatchée — aucun appel réseau. Le `TestClient` de Starlette exécute les
`BackgroundTasks` de façon synchrone dans la requête, donc un POST de création
de job traite le job avant de rendre la main.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import unescape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import InterviewSegmentJob, Mission
from app.routers import interviews as interviews_router
from app.services import interview_segment_jobs


def setup_module() -> None:
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


def _turns_payload(name: str, question: str) -> dict:
    return {
        "turns": [
            {"interlocuteur": name, "question": question, "remarque": "", "section_title": ""},
        ],
        "identity": {"interviewee_name": name, "interviewee_role": "", "interviewee_entity": ""},
    }


def _make_draft_mission() -> int:
    db = SessionLocal()
    try:
        mission = Mission(name="Mission Palier 2", is_draft=True)
        db.add(mission)
        db.commit()
        return mission.id
    finally:
        db.close()


def _stale_created_at() -> datetime:
    """Un `created_at` largement au-delà du seuil de péremption par défaut
    (45min) — naïf en UTC, comme ce que SQLite rend réellement (cf. le
    correctif : SQLite ne préserve pas le tzinfo, `_is_stale` compare donc du
    naïf à du naïf)."""
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)


# --------------------------------------------------------------------------- #
# Service — tâche de fond (lit désormais `job.text`, persisté à la création)
# --------------------------------------------------------------------------- #
def test_run_segment_job_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_segment_jobs, "extract_turns_from_text",
        lambda text: _turns_payload("Alice", "Comment ça va ?"),
    )
    db = SessionLocal()
    job = InterviewSegmentJob(session_token="tok-success", position=0, status="pending",
                              text="un texte de tranche")
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    interview_segment_jobs.run_segment_job(job_id)

    db = SessionLocal()
    refreshed = db.get(InterviewSegmentJob, job_id)
    assert refreshed.status == "done"
    assert refreshed.turns_result["turns"][0]["interlocuteur"] == "Alice"
    assert refreshed.error is None
    db.close()


def test_run_segment_job_failure_records_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(text):
        raise interview_segment_jobs.InterviewLibreExtractAIError("Ollama injoignable")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _boom)
    db = SessionLocal()
    job = InterviewSegmentJob(session_token="tok-fail", position=0, status="pending", text="texte")
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    interview_segment_jobs.run_segment_job(job_id)

    db = SessionLocal()
    refreshed = db.get(InterviewSegmentJob, job_id)
    assert refreshed.status == "failed"
    assert "Ollama" in refreshed.error
    assert refreshed.turns_result is None
    db.close()


# --------------------------------------------------------------------------- #
# Service — fusion + statut
# --------------------------------------------------------------------------- #
def test_merge_segment_turns_orders_by_position_and_appends_tail() -> None:
    j0 = InterviewSegmentJob(session_token="t", position=0,
                             turns_result=_turns_payload("Alice", "Q0"))
    j1 = InterviewSegmentJob(session_token="t", position=1,
                             turns_result=_turns_payload("Bob", "Q1"))
    tail = _turns_payload("Carol", "Qtail")
    # Volontairement dans le désordre pour vérifier le tri par position.
    merged = interview_segment_jobs.merge_segment_turns([j1, j0], tail)
    questions = [t["question"] for t in merged["turns"]]
    assert questions == ["Q0", "Q1", "Qtail"]
    # Première identité non vide (job position 0) l'emporte.
    assert merged["identity"]["interviewee_name"] == "Alice"


def test_merge_segment_turns_without_tail() -> None:
    j0 = InterviewSegmentJob(session_token="t", position=0,
                             turns_result=_turns_payload("Alice", "Q0"))
    merged = interview_segment_jobs.merge_segment_turns([j0], None)
    assert [t["question"] for t in merged["turns"]] == ["Q0"]


def test_segment_jobs_status_counts() -> None:
    db = SessionLocal()
    for pos, status in enumerate(["done", "done", "running"]):
        db.add(InterviewSegmentJob(session_token="tok-status", position=pos, status=status))
    db.commit()

    status = interview_segment_jobs.segment_jobs_status(db, "tok-status")
    assert status["total"] == 3
    assert status["done"] == 2
    assert status["stale"] == 0  # job "running" tout frais -> pas périmé
    assert status["all_done"] is False
    assert status["any_failed"] is False
    db.close()


def test_segment_jobs_status_all_done() -> None:
    db = SessionLocal()
    for pos in range(2):
        db.add(InterviewSegmentJob(session_token="tok-alldone", position=pos, status="done"))
    db.commit()
    status = interview_segment_jobs.segment_jobs_status(db, "tok-alldone")
    assert status["all_done"] is True
    assert status["any_failed"] is False
    db.close()


def test_segment_jobs_status_empty_token() -> None:
    db = SessionLocal()
    status = interview_segment_jobs.segment_jobs_status(db, "")
    assert status["total"] == 0
    assert status["all_done"] is False
    db.close()


def test_delete_segment_jobs() -> None:
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="tok-del", position=0, status="done"))
    db.commit()
    interview_segment_jobs.delete_segment_jobs(db, "tok-del")
    remaining = db.scalars(
        select(InterviewSegmentJob).where(InterviewSegmentJob.session_token == "tok-del")
    ).all()
    assert remaining == []
    db.close()


# --------------------------------------------------------------------------- #
# Correctif #2 — job bloqué (`pending`/`running` périmé) détecté comme "stale"
# --------------------------------------------------------------------------- #
def test_segment_jobs_status_marks_old_running_job_as_stale() -> None:
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="tok-stale", position=0, status="running",
                                text="texte", created_at=_stale_created_at()))
    db.commit()

    status = interview_segment_jobs.segment_jobs_status(db, "tok-stale")
    assert status["stale"] == 1
    # Un job périmé compte comme "any_failed" pour mettre fin à l'attente côté
    # écran de statut (poll) — sinon boucle infinie sur un job perdu.
    assert status["any_failed"] is True
    assert status["all_done"] is False
    db.close()


def test_segment_jobs_status_fresh_pending_job_is_not_stale() -> None:
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="tok-fresh", position=0, status="pending",
                                text="texte"))  # created_at = maintenant (défaut)
    db.commit()
    status = interview_segment_jobs.segment_jobs_status(db, "tok-fresh")
    assert status["stale"] == 0
    assert status["any_failed"] is False
    db.close()


def test_segment_job_stale_after_s_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEGMENT_JOB_STALE_AFTER_S", "60")
    assert interview_segment_jobs.segment_job_stale_after_s() == 60


def test_segment_job_stale_after_s_default() -> None:
    assert interview_segment_jobs.segment_job_stale_after_s() == 45 * 60


# --------------------------------------------------------------------------- #
# Correctifs #2+#3 — récupération BORNÉE (jamais la transcription entière)
# --------------------------------------------------------------------------- #
def test_recover_recovers_failed_job_from_its_own_persisted_text(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def _extract(text):
        calls.append(text)
        return _turns_payload("Alice", "Récupérée")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _extract)
    db = SessionLocal()
    job = InterviewSegmentJob(session_token="t", position=0, status="failed",
                              text="texte de la tranche seule", error="ancien timeout")
    db.add(job)
    db.commit()

    interview_segment_jobs.recover_stalled_or_failed_jobs(db, [job])

    assert job.status == "done"
    assert job.error is None
    assert job.turns_result["turns"][0]["interlocuteur"] == "Alice"
    # La récupération n'a traité QUE le texte de CE job, jamais autre chose.
    assert calls == ["texte de la tranche seule"]
    db.close()


def test_recover_recovers_stale_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_segment_jobs, "extract_turns_from_text",
        lambda text: _turns_payload("Bob", "Depuis job périmé"),
    )
    db = SessionLocal()
    job = InterviewSegmentJob(session_token="t", position=0, status="running",
                              text="texte", created_at=_stale_created_at())
    db.add(job)
    db.commit()

    interview_segment_jobs.recover_stalled_or_failed_jobs(db, [job])

    assert job.status == "done"
    assert job.turns_result["turns"][0]["interlocuteur"] == "Bob"
    db.close()


def test_recover_also_recovers_fresh_running_job_when_called(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug trouvé en auto-relecture avant commit (2026-07-20) : la première
    version de `recover_stalled_or_failed_jobs` ne re-traitait QUE les jobs
    `failed`/stale, laissant un job `running` frais totalement de côté. Or la
    fonction n'est appelée QUE quand la finalisation a déjà décidé de
    procéder MAINTENANT (tous done, ou un job frère failed/stale a fait
    sauter l'attente) — un job running non recover à cet instant ne serait
    JAMAIS rattrapé ailleurs, perdant silencieusement sa tranche de contenu.
    Contrat correct : `recover_stalled_or_failed_jobs` traite TOUT job pas
    encore `done`, sans condition sur son statut exact."""
    monkeypatch.setattr(
        interview_segment_jobs, "extract_turns_from_text",
        lambda text: _turns_payload("Zoé", "Récupérée bien que fraîche"),
    )
    db = SessionLocal()
    job = InterviewSegmentJob(session_token="t", position=0, status="running", text="texte")
    db.add(job)
    db.commit()

    interview_segment_jobs.recover_stalled_or_failed_jobs(db, [job])

    assert job.status == "done"
    assert job.turns_result["turns"][0]["interlocuteur"] == "Zoé"
    db.close()


def test_recover_second_failure_keeps_job_failed_with_new_error(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(text):
        raise interview_segment_jobs.InterviewLibreExtractAIError("échec persistant")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _boom)
    db = SessionLocal()
    job = InterviewSegmentJob(session_token="t", position=0, status="failed",
                              text="texte", error="premier échec")
    db.add(job)
    db.commit()

    interview_segment_jobs.recover_stalled_or_failed_jobs(db, [job])

    assert job.status == "failed"
    assert job.error == "échec persistant"
    db.close()


def test_recover_job_without_text_stays_failed_without_calling_ai(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def _extract(text):
        raise AssertionError("ne doit jamais être appelé sans texte")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _extract)
    db = SessionLocal()
    job = InterviewSegmentJob(session_token="t", position=0, status="failed", text="")
    db.add(job)
    db.commit()

    interview_segment_jobs.recover_stalled_or_failed_jobs(db, [job])

    assert job.status == "failed"
    assert job.error
    db.close()


# --------------------------------------------------------------------------- #
# HTTP — création de job (tâche de fond exécutée par TestClient) + statut
# --------------------------------------------------------------------------- #
def test_create_segment_job_persists_text_and_processes_in_background(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        interview_segment_jobs, "extract_turns_from_text",
        lambda text: _turns_payload("Alice", "Q"),
    )
    resp = client.post(
        "/interviews/segment-jobs",
        data={"session_token": "http-tok", "position": "0", "text": "tranche de texte"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    # Le BackgroundTask a tourné pendant la requête -> statut done.
    status = client.get("/interviews/segment-jobs/status", params={"session_token": "http-tok"})
    body = status.json()
    assert body["total"] == 1
    assert body["done"] == 1
    assert body["all_done"] is True

    # Correctif #2/#3 : le texte est bien persisté sur la ligne (pas seulement
    # passé en paramètre de tâche de fond) — survit à un redémarrage serveur.
    db = SessionLocal()
    job = db.scalars(
        select(InterviewSegmentJob).where(InterviewSegmentJob.session_token == "http-tok")
    ).one()
    assert job.text == "tranche de texte"
    db.close()


def test_status_endpoint_unknown_token_is_empty(client: TestClient) -> None:
    resp = client.get("/interviews/segment-jobs/status", params={"session_token": "does-not-exist"})
    body = resp.json()
    assert body == {"total": 0, "done": 0, "failed": 0, "all_done": False, "any_failed": False}


def test_turns_endpoint_merges_done_jobs_in_position_order(client: TestClient) -> None:
    """Aperçu live (onglet « Répartition », Palier A) : fusionne les tours des
    jobs TERMINÉS par position, exclut ceux encore en cours, sans appel IA."""
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="turns-tok", position=1, status="done",
                               text="x", turns_result=_turns_payload("Bob", "Q1")))
    db.add(InterviewSegmentJob(session_token="turns-tok", position=0, status="done",
                               text="x", turns_result=_turns_payload("Alice", "Q0")))
    db.add(InterviewSegmentJob(session_token="turns-tok", position=2, status="running",
                               text="x"))  # pas encore terminé -> absent de l'aperçu
    db.commit()
    db.close()

    resp = client.get("/interviews/segment-jobs/turns", params={"session_token": "turns-tok"})
    body = resp.json()
    assert body["total"] == 3
    assert body["done"] == 2
    assert [t["question"] for t in body["turns"]] == ["Q0", "Q1"]


def test_turns_endpoint_unknown_token_is_empty(client: TestClient) -> None:
    resp = client.get("/interviews/segment-jobs/turns", params={"session_token": "nope"})
    assert resp.json() == {"turns": [], "done": 0, "total": 0}


# --------------------------------------------------------------------------- #
# HTTP — record_libre : attente vs fusion vs récupération bornée
# --------------------------------------------------------------------------- #
def test_record_libre_shows_wait_screen_when_jobs_pending(client: TestClient) -> None:
    mission_id = _make_draft_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="wait-tok", position=0, status="running", text="x"))
    db.commit()
    db.close()

    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "une longue transcription", "session_token": "wait-tok"},
    )
    assert resp.status_code == 200
    assert "Traitement des tranches" in resp.text
    # L'écran d'attente poste la finalisation vers from-jobs.
    assert "record-libre/from-jobs" in resp.text


def test_record_libre_from_jobs_merges_done_turns(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _make_draft_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token="merge-tok", position=0, status="done", text="x",
        turns_result=_turns_payload("Alice", "Question issue du job"),
    ))
    db.commit()
    db.close()

    # Reliquat final traité en synchrone à la finalisation.
    monkeypatch.setattr(
        interviews_router, "extract_turns_from_text",
        lambda text: _turns_payload("Bob", "Question du reliquat"),
    )
    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre/from-jobs",
        data={
            "transcript": "transcription complète",
            "session_token": "merge-tok",
            "segment_tail": "reliquat non couvert",
        },
    )
    assert resp.status_code == 200
    assert "Question issue du job" in resp.text
    assert "Question du reliquat" in resp.text
    # Jobs consommés puis supprimés.
    db = SessionLocal()
    remaining = db.scalars(
        select(InterviewSegmentJob).where(InterviewSegmentJob.session_token == "merge-tok")
    ).all()
    assert remaining == []
    db.close()


def test_record_libre_no_jobs_uses_synchronous_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-régression : un entretien court (aucun job) suit le chemin
    synchrone d'avant le Palier 2— seul cas où extract_turns_from_text voit
    la transcription entière."""
    mission_id = _make_draft_mission()
    monkeypatch.setattr(
        interviews_router, "extract_turns_from_text",
        lambda text: _turns_payload("Alice", "Extraction synchrone"),
    )
    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "un entretien court", "session_token": ""},
    )
    assert resp.status_code == 200
    assert "Extraction synchrone" in resp.text


def test_record_libre_recovers_failed_job_individually_never_whole_transcript(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Correctif #3 (le cœur du bug) : sur job KO, on NE retraite JAMAIS la
    transcription entière — seule la tranche du job en échec est reprocessée.
    Preuve : la transcription (un marqueur unique, jamais vu ailleurs) n'est
    JAMAIS passée à extract_turns_from_text ; le job récupère sa PROPRE
    tranche persistée."""
    mission_id = _make_draft_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token="failed-tok", position=0, status="done", text="x",
        turns_result=_turns_payload("Alice", "Tranche 1 déjà traitée"),
    ))
    db.add(InterviewSegmentJob(
        session_token="failed-tok", position=1, status="failed",
        text="texte de la tranche 2 (échouée)", error="timeout",
    ))
    db.commit()
    db.close()

    calls = []
    GIANT_TRANSCRIPT_MARKER = "MARQUEUR_TRANSCRIPTION_COMPLETE_3H_JAMAIS_ATTENDU"

    def _extract(text):
        calls.append(text)
        assert GIANT_TRANSCRIPT_MARKER not in text, (
            "la transcription entière a été passée à l'IA — le mur "
            "synchrone multi-heures est de retour"
        )
        return _turns_payload("Bob", "Tranche 2 récupérée")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _extract)
    monkeypatch.setattr(interviews_router, "extract_turns_from_text", _extract)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={
            "transcript": GIANT_TRANSCRIPT_MARKER + " (simule 3h d'entretien)",
            "session_token": "failed-tok",
        },
    )
    assert resp.status_code == 200
    assert "Tranche 1 déjà traitée" in resp.text
    assert "Tranche 2 récupérée" in resp.text
    # Un seul appel IA : la récupération de la tranche 2, avec SON texte à
    # elle (pas la transcription complète).
    assert calls == ["texte de la tranche 2 (échouée)"]


def test_record_libre_surfaces_actionable_error_when_all_jobs_fail(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B1 (revue adversariale 2026-07-22) : quand la finalisation passe par le
    chemin JOB (le fix « répartition Q/R vide » force désormais un job même pour
    un entretien court) et que TOUS les jobs échouent (ex. timeout Ollama), l'écran
    doit resurfacer le message ACTIONABLE du job (levier OLLAMA_TIMEOUT/…), pas le
    générique trompeur « Aucun tour de parole détecté ». Parité avec le chemin
    synchrone (status total==0) qui le remonte déjà via str(exc). Sans ce fix, le
    chemin job avalait l'exception dans job.error et l'utilisateur voyait « aucun
    tour détecté » alors qu'Ollama avait simplement timeouté."""
    mission_id = _make_draft_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token="err-tok", position=0, status="failed",
        text="texte de la tranche", error="ancienne erreur",
    ))
    db.commit()
    db.close()

    ACTIONABLE = "Ollama n a pas repondu a temps — augmentez OLLAMA_TIMEOUT ou reduisez OLLAMA_CHUNK_MAX_WORDS."

    def _boom(text):
        raise interview_segment_jobs.InterviewLibreExtractAIError(ACTIONABLE)

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _boom)
    monkeypatch.setattr(interviews_router, "extract_turns_from_text", _boom)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "un entretien court", "session_token": "err-tok", "segment_tail": ""},
    )
    assert resp.status_code == 200
    assert "OLLAMA_TIMEOUT" in resp.text                    # message actionable resurface
    assert "Aucun tour de parole détecté" not in resp.text  # jamais le generique trompeur


def test_record_libre_stale_job_recovered_at_finalize(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Correctif #2 : un job resté `running` bien au-delà du seuil de
    péremption est traité comme un échec (pas d'attente infinie) et récupéré
    individuellement à la finalisation, comme un job `failed` classique."""
    mission_id = _make_draft_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token="stale-tok", position=0, status="running",
        text="texte bloqué depuis 2h", created_at=_stale_created_at(),
    ))
    db.commit()
    db.close()

    monkeypatch.setattr(
        interview_segment_jobs, "extract_turns_from_text",
        lambda text: _turns_payload("Alice", "Récupérée après péremption"),
    )
    # Le POST direct sur /record-libre doit sauter l'écran d'attente (le job
    # périmé compte comme "any_failed") et finaliser directement.
    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "transcription", "session_token": "stale-tok"},
    )
    assert resp.status_code == 200
    assert "Récupérée après péremption" in resp.text
    assert "Traitement des tranches" not in resp.text


def test_record_libre_does_not_drop_a_fresh_running_sibling_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Régression du bug trouvé en auto-relecture avant commit (2026-07-20) :
    un job `failed` fait sauter l'écran d'attente pour TOUTE la session
    (`any_failed`), y compris pour un job FRÈRE encore `running` (ni failed,
    ni stale) qui n'a simplement pas eu le temps de finir. Sans le correctif,
    ce job frère n'était ni fusionné (turns_result encore None) ni récupéré
    (la récupération ne touchait que failed/stale) — son contenu disparaissait
    silencieusement. Doit maintenant être récupéré comme les autres."""
    mission_id = _make_draft_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(
        session_token="mixed-tok", position=0, status="done", text="x",
        turns_result=_turns_payload("Alice", "Tranche 0 déjà traitée"),
    ))
    db.add(InterviewSegmentJob(
        session_token="mixed-tok", position=1, status="running",
        text="texte de la tranche 1 (encore en cours, fraîche)",
    ))
    db.add(InterviewSegmentJob(
        session_token="mixed-tok", position=2, status="failed",
        text="texte de la tranche 2 (échouée)", error="timeout",
    ))
    db.commit()
    db.close()

    def _extract(text):
        if "tranche 1" in text:
            return _turns_payload("Bob", "Tranche 1 récupérée malgré tout")
        return _turns_payload("Carol", "Tranche 2 récupérée")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _extract)
    monkeypatch.setattr(interviews_router, "extract_turns_from_text", _extract)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "transcription complète", "session_token": "mixed-tok"},
    )
    assert resp.status_code == 200
    assert "Tranche 0 déjà traitée" in resp.text
    assert "Tranche 1 récupérée malgré tout" in resp.text  # sinon : perdue
    assert "Tranche 2 récupérée" in resp.text


# --------------------------------------------------------------------------- #
# Revue du 2026-08-31 — le chemin NOMINAL du mode libre n'avait aucun des deux
# garde-fous posés le 2026-07-31 (d36aef6) sur `retranscrire_appliquer` :
# ni plafond de récupération, ni détection de perte partielle. Le mode
# paramétré (`interviews.py`, chemin frère) bloquait déjà sur `still_ko`.
# --------------------------------------------------------------------------- #


def test_record_libre_bloque_quand_une_tranche_reste_en_echec(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Perte PARTIELLE signalée (revue du 2026-08-31). Avant : seul le cas
    100 % vide était détecté ; avec 1 tranche sur 3 restée en échec, l'entretien
    était créé `status="done"` amputé de ces 5 minutes, SANS message, sans log,
    et le `delete_segment_jobs` de la fin détruisait le texte qui les portait.

    Le test échoue sur le code d'avant : la réponse était une redirection 303
    vers l'entretien créé, pas la page d'erreur."""
    mission_id = _make_draft_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="partiel-tok", position=0, status="done",
                               text="tranche 0 réussie",
                               turns_result=_turns_payload("Alice", "Q0")))
    db.add(InterviewSegmentJob(session_token="partiel-tok", position=1, status="failed",
                               text="tranche 1 irrécupérable", error="Ollama saturé"))
    db.commit()
    db.close()

    def _boom(text):
        raise interview_segment_jobs.InterviewLibreExtractAIError("Ollama saturé")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _boom)
    monkeypatch.setattr(interviews_router, "extract_turns_from_text", _boom)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "un entretien", "session_token": "partiel-tok", "segment_tail": ""},
    )
    # Page d'erreur, PAS la création silencieuse d'un entretien amputé.
    assert resp.status_code == 200
    assert "MANQUERAIT" in resp.text
    # Aucune tranche récupérée sur cet envoi : le message doit le dire, et non
    # laisser croire à un progrès (revue adversariale 2026-08-31).
    # Fragment sans apostrophe : Jinja échappe `'` en `&#39;` dans le rendu.
    assert "récupérée sur cet envoi" in resp.text
    assert "Ollama saturé" in resp.text  # l'erreur du job reste resurfacée

    # Les jobs SURVIVENT : la tranche réussie n'est pas re-traitée à la relance,
    # et le texte de la tranche en échec n'est pas détruit.
    db = SessionLocal()
    restants = db.scalars(
        select(InterviewSegmentJob).where(InterviewSegmentJob.session_token == "partiel-tok")
    ).all()
    assert len(restants) == 2, "les jobs ne doivent pas être supprimés sur ce chemin"
    assert any(j.text == "tranche 1 irrécupérable" for j in restants)
    db.close()


def test_record_libre_plafonne_la_recuperation_synchrone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Récupération PLAFONNÉE (revue du 2026-08-31), parité avec
    `retranscrire_appliquer`. Avant : `recover_stalled_or_failed_jobs(db,
    status["jobs"])` retraitait TOUTES les tranches non abouties dans la requête
    HTTP — sur un entretien de 2 h avec Ollama saturé, 24 × (timeout + relance)
    dans un seul POST, soit des heures de requête bloquée avant une page
    d'erreur, l'entretien n'existant pas encore en base.

    Le test échoue sur le code d'avant : 6 appels au lieu de 3."""
    from app.routers.interviews import RECUP_TRANCHES_MAX

    mission_id = _make_draft_mission()
    db = SessionLocal()
    for pos in range(6):
        db.add(InterviewSegmentJob(session_token="plafond-tok", position=pos,
                                   status="failed", text=f"tranche {pos}",
                                   error="Ollama saturé"))
    db.commit()
    db.close()

    appels: list[str] = []

    def _boom(text):
        appels.append(text)
        raise interview_segment_jobs.InterviewLibreExtractAIError("Ollama saturé")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _boom)
    monkeypatch.setattr(interviews_router, "extract_turns_from_text", _boom)

    resp = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "un entretien long", "session_token": "plafond-tok", "segment_tail": ""},
    )
    assert resp.status_code == 200
    assert len(appels) <= RECUP_TRANCHES_MAX, (
        f"{len(appels)} tranches retraitées dans un seul POST — le plafond "
        f"({RECUP_TRANCHES_MAX}) ne s'applique pas au chemin libre direct"
    )


def test_record_libre_enregistre_quand_meme_sur_derogation_explicite(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Porte de sortie (arbitrage utilisateur 2026-08-31). Le blocage introduit
    le même jour protège la matière, mais il coinçait la séance : avec un service
    d'IA durablement indisponible ET au moins une tranche aboutie, l'entretien
    devenait DÉFINITIVEMENT non enregistrable — seules issues « Recommencer »
    (qui détruit la transcription) ou l'export PDF.

    Le bouton « Enregistrer quand même » poste `ignorer_tranches_manquantes` ; la
    dérogation ne vaut que sur ce geste, jamais par défaut. Ce qui a abouti est
    conservé, et le texte des tranches perdues reste dans `raw_transcript`."""
    from app.models import Interview

    mission_id = _make_draft_mission()
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="derog-tok", position=0, status="done",
                               text="tranche 0 réussie",
                               turns_result=_turns_payload("Alice", "Q0")))
    db.add(InterviewSegmentJob(session_token="derog-tok", position=1, status="failed",
                               text="tranche 1 irrécupérable", error="Ollama saturé"))
    db.commit()
    db.close()

    def _boom(text):
        raise interview_segment_jobs.InterviewLibreExtractAIError("Ollama saturé")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _boom)
    monkeypatch.setattr(interviews_router, "extract_turns_from_text", _boom)

    commun = {"transcript": "la transcription complète de l entretien",
              "session_token": "derog-tok", "segment_tail": ""}

    # Sans la dérogation : refus (le garde-fou tient).
    refus = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer", data=commun
    )
    assert refus.status_code == 200 and "MANQUERAIT" in refus.text
    # ... et la page propose bien la sortie, avec son décompte.
    assert "ignorer_tranches_manquantes" in refus.text
    assert "Enregistrer quand même (1 tranche manquante)" in unescape(refus.text)

    # Avec la dérogation : l'entretien est créé.
    ok = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={**commun, "ignorer_tranches_manquantes": "1"}, follow_redirects=False,
    )
    assert ok.status_code == 303, ok.text[:400]

    db = SessionLocal()
    interview = db.scalars(
        select(Interview).where(Interview.mission_id == mission_id)
    ).one()
    # La tranche aboutie est conservée ; la perdue manque au tour de table mais
    # son texte survit dans la transcription brute.
    assert [t.question for t in interview.turns] == ["Q0"]
    assert "la transcription complète" in interview.raw_transcript
    db.close()


def test_fenetre_de_recuperation_anti_famine() -> None:
    """R3-M3 (2026-08-31) : la fenêtre de récupération n'est plus un préfixe
    fixe. Deux garanties, chacune testée sur la fonction réelle :

    - les tranches jamais tombées en erreur passent AVANT les échecs déjà
      constatés — trois échecs déterministes en tête de liste (ex. trame
      supprimée) monopolisaient sinon le préfixe `[:RECUP_TRANCHES_MAX]` et les
      tranches suivantes n'étaient JAMAIS tentées, pendant que la page d'erreur
      promettait « relance l'envoi » à l'infini ;
    - une tranche SANS texte (acceptée par `create_segment_job`, re-marquée
      `failed` à chaque récupération) ne consomme plus un créneau à chaque
      envoi : même filtre de matière que le décompte de perte `still_ko`.
    """
    from types import SimpleNamespace

    from app.routers.interviews import RECUP_TRANCHES_MAX, _fenetre_recuperation

    def tranche(pos, error=None, text="de la matière"):
        return SimpleNamespace(position=pos, error=error, text=text,
                               turns_result=None)

    poison = [tranche(0, error="KO"), tranche(1, error="KO"), tranche(2, error="KO")]
    fraiches = [tranche(3), tranche(4)]
    vide = tranche(5, text="   ")

    fenetre = _fenetre_recuperation(
        poison + fraiches + [vide], lambda j: bool(j.turns_result)
    )

    assert len(fenetre) == RECUP_TRANCHES_MAX
    assert [j.position for j in fenetre] == [3, 4, 0], (
        "les tranches jamais tentées doivent passer avant les échecs déjà "
        f"constatés — fenêtre obtenue : {[j.position for j in fenetre]}"
    )
    assert vide not in fenetre, "une tranche sans texte ne doit jamais consommer un créneau"


def test_fenetre_de_recuperation_ignore_les_tranches_abouties() -> None:
    """La fenêtre ne retraite jamais une tranche déjà structurée : `deja_abouti`
    est le même critère que la fusion (`merge_segment_turns` ignore les jobs
    sans `turns_result`)."""
    from types import SimpleNamespace

    from app.routers.interviews import _fenetre_recuperation

    faite = SimpleNamespace(position=0, error=None, text="ok",
                            turns_result={"turns": [{"remarque": "vu"}]})
    restante = SimpleNamespace(position=1, error=None, text="reste",
                               turns_result=None)

    fenetre = _fenetre_recuperation([faite, restante],
                                    lambda j: bool(j.turns_result))
    assert fenetre == [restante]
