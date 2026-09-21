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

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import InterviewSegmentJob, Mission
from app.routers import interviews as interviews_router
from app.services import interview_segment_jobs


def setup_module() -> None:
    # Le pool de `engine` est PARTAGE par toute la suite : sans ce dispose,
    # le fichier de test precedent tient encore la base et l'unlink leve
    # WinError 32 sur Windows (vert en isolation, rouge en ordre de
    # collecte). Motif canonique de la suite, cf. test_deck_qualite.py.
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
    j0 = InterviewSegmentJob(session_token="t-merge-ordre", position=0,
                             turns_result=_turns_payload("Alice", "Q0"))
    j1 = InterviewSegmentJob(session_token="t-merge-ordre", position=1,
                             turns_result=_turns_payload("Bob", "Q1"))
    tail = _turns_payload("Carol", "Qtail")
    # Volontairement dans le désordre pour vérifier le tri par position.
    merged = interview_segment_jobs.merge_segment_turns([j1, j0], tail)
    questions = [t["question"] for t in merged["turns"]]
    assert questions == ["Q0", "Q1", "Qtail"]
    # Première identité non vide (job position 0) l'emporte.
    assert merged["identity"]["interviewee_name"] == "Alice"


def test_merge_segment_turns_without_tail() -> None:
    j0 = InterviewSegmentJob(session_token="t-merge-sans-queue", position=0,
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
    job = InterviewSegmentJob(session_token="t-recover-failed", position=0, status="failed",
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
    job = InterviewSegmentJob(session_token="t-recover-stale", position=0, status="running",
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
    job = InterviewSegmentJob(session_token="t-recover-fresh", position=0, status="running", text="texte")
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
    job = InterviewSegmentJob(session_token="t-recover-2e-echec", position=0, status="failed",
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
    job = InterviewSegmentJob(session_token="t-recover-sans-texte", position=0, status="failed", text="")
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
    assert resp.json() == {"turns": [], "done": 0, "total": 0, "next_since": 0}


def test_turns_endpoint_since_cursor_only_returns_new_turns(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le curseur `since` (position) ne redemande/refusionne QUE les tranches
    `done` à partir de cette position — sondé toutes les 5s pendant tout un
    entretien, c'est ce qui évite de recharger et refusionner l'intégralité de
    la session à chaque appel (correctif du 2026-09-21). Sans `since`, le
    comportement complet d'avant est inchangé (compat frontend en cache)."""
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="cursor-tok", position=0, status="done",
                               text="texte tranche 0 " * 500,
                               turns_result=_turns_payload("Alice", "Q0")))
    db.add(InterviewSegmentJob(session_token="cursor-tok", position=1, status="done",
                               text="texte tranche 1 " * 500,
                               turns_result=_turns_payload("Bob", "Q1")))
    db.commit()
    db.close()

    # Sans curseur : comportement historique, fusion complète.
    resp = client.get("/interviews/segment-jobs/turns", params={"session_token": "cursor-tok"})
    body = resp.json()
    assert [t["question"] for t in body["turns"]] == ["Q0", "Q1"]
    next_since = body["next_since"]
    assert next_since == 2  # position du dernier job vu (1) + 1

    # AVANT le correctif, un second appel rechargeait et refusionnait les DEUX
    # tranches (dont leur colonne `text`, ici volontairement volumineuse) —
    # preuve que la requête sous-jacente ne recharge plus les entités déjà
    # vues : on instrumente `segment_jobs_status_light` pour compter les jobs
    # RÉELLEMENT relus par la fusion.
    jobs_lus = []
    original = interview_segment_jobs.segment_jobs_status_light

    def _espion(db_arg, session_token, since_position=0):
        result = original(db_arg, session_token, since_position=since_position)
        jobs_lus.append(list(result["jobs"]))
        return result

    monkeypatch.setattr(interviews_router, "segment_jobs_status_light", _espion)

    # Une 3e tranche termine ENTRE les deux polls du client.
    db = SessionLocal()
    db.add(InterviewSegmentJob(session_token="cursor-tok", position=2, status="done",
                               text="texte tranche 2 " * 500,
                               turns_result=_turns_payload("Carla", "Q2")))
    db.commit()
    db.close()

    resp2 = client.get(
        "/interviews/segment-jobs/turns",
        params={"session_token": "cursor-tok", "since": next_since},
    )
    body2 = resp2.json()

    # Preuve rouge->vert : seule la tranche NOUVELLE (position 2) a été
    # rechargée/refusionnée — pas les deux déjà vues.
    assert len(jobs_lus) == 1
    assert [j.position for j in jobs_lus[0]] == [2]
    assert [t["question"] for t in body2["turns"]] == ["Q2"]
    assert body2["total"] == 3
    assert body2["done"] == 3
    assert body2["next_since"] == 3


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
    """Quand TOUS les jobs échouent (ex. timeout Ollama), la revue des tours
    s'ouvre quand même — vide — au lieu de refuser la page (demande utilisateur
    2026-09-04 : la répartition Q/R ne bloque plus rien). Le texte reste porté
    par le formulaire, donc rien n'est perdu.

    Échoue sur le code d'avant, qui rendait l'écran d'enregistrement avec le
    message d'erreur du job au lieu de la revue."""
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
    # La revue des tours, pas l'écran d'enregistrement avec son erreur.
    assert "Revue des questions/réponses" in resp.text
    assert "OLLAMA_TIMEOUT" not in resp.text
    # Le texte n'est pas perdu : il repart dans le formulaire de la revue.
    assert "un entretien court" in resp.text


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
    """Une tranche irrécupérable ne retient PLUS l'entretien (demande
    utilisateur 2026-09-04) : la revue s'ouvre avec les tours qui ont abouti.

    La perte reste signalée — c'était la raison d'être du blocage du 2026-08-31,
    et elle survit à sa levée : `_extraire_tours_libre` compte les tranches
    manquantes, l'écran d'arrivée les affiche (cf. le test d'enregistrement
    direct plus bas, qui vérifie le bandeau).

    Échoue sur le code d'avant : la réponse était l'écran d'erreur
    « MANQUERAIT », pas la revue."""
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
    assert resp.status_code == 200
    assert "MANQUERAIT" not in resp.text
    # Ce qui a abouti est là, malgré l'échec de la tranche 1.
    assert "Q0" in resp.text

    # Les jobs sont consommés : leur texte vit dans la transcription postée par
    # l'écran, que l'enregistrement conserve en entier (`raw_transcript`).
    db = SessionLocal()
    restants = db.scalars(
        select(InterviewSegmentJob).where(InterviewSegmentJob.session_token == "partiel-tok")
    ).all()
    assert restants == []
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


def test_record_libre_enregistre_malgre_une_tranche_en_echec(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Demande utilisateur du 2026-09-04 : que la répartition Q/R aboutisse ou
    non, elle ne bloque plus l'enregistrement de l'entretien. Il n'y a plus de
    dérogation à cliquer (« Enregistrer quand même » a disparu) — le premier
    envoi crée l'entretien.

    Ce qui a abouti est conservé ; le texte de la tranche perdue survit dans
    `raw_transcript` ; et la fiche d'arrivée DIT ce qui manque, sans quoi on
    aurait remplacé un blocage par une perte silencieuse.

    Échoue sur le code d'avant : le premier envoi rendait 200 + « MANQUERAIT »
    et ne créait aucun entretien."""
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

    ok = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "la transcription complète de l entretien",
              "session_token": "derog-tok", "segment_tail": ""},
        follow_redirects=False,
    )
    assert ok.status_code == 303, ok.text[:400]
    # La mission est un brouillon : l'écran d'arrivée est la finalisation.
    # Le décompte est persisté (finding F2), plus véhiculé par l'URL.
    assert ok.headers["location"] == f"/missions/{mission_id}/finaliser"

    db = SessionLocal()
    interview = db.scalars(
        select(Interview).where(Interview.mission_id == mission_id)
    ).one()
    assert [t.question for t in interview.turns] == ["Q0"]
    assert "la transcription complète" in interview.raw_transcript
    assert interview.tranches_manquantes == 1
    interview_id = interview.id
    db.close()

    # Le bandeau de la fiche : l'entretien est enregistré, ET son tour de table
    # est annoncé incomplet. Un simple GET, SANS query string, doit suffire —
    # le compteur vient de la base (finding F2), pas de l'URL.
    fiche = client.get(f"/interviews/{interview_id}")
    assert fiche.status_code == 200
    assert "pas pu être structurée" in fiche.text
    assert "Relancer la transcription" in fiche.text


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


# --- Réconciliation au démarrage (incident du 2026-09-08) -------------------
#
# Une tâche de fond ne survit pas à son processus. Avant ce correctif, rien ne
# ramassait au démarrage les tranches laissées `pending`/`running` par le
# processus d'avant : elles restaient `running` À VIE. Mesuré sur un entretien
# réel de 2h le 2026-09-08 — 3 tranches figées depuis 5h30, alors qu'un vrai
# timeout Ollama est borné à 30min au pire.
#
# Ces deux tests échouent sur le code d'avant : la fonction n'existait pas
# (ImportError), et le `lifespan` ne l'appelait pas.


def _job(db, *, position: int, status: str, text: str = "du texte") -> int:
    job = InterviewSegmentJob(
        session_token="tok-reconcile", position=position, status=status, text=text
    )
    db.add(job)
    db.commit()
    return job.id


def test_reconcile_au_demarrage_libere_les_tranches_figees() -> None:
    """Une tranche `running` ou `pending` héritée d'un processus mort repasse en
    `failed` avec un motif lisible — donc rejouable par
    `recover_stalled_or_failed_jobs`, au lieu de rester bloquée pour toujours."""
    db = SessionLocal()
    try:
        id_running = _job(db, position=0, status="running")
        id_pending = _job(db, position=1, status="pending")
        id_done = _job(db, position=2, status="done")
    finally:
        db.close()

    # `>= 2` et non `== 2` : la base de test est partagée par toute la session
    # (chemin fixe, conftest.py) — d'autres modules peuvent y laisser des
    # tranches non abouties. L'assertion exacte porte sur NOS trois lignes.
    nombre = interview_segment_jobs.reconcile_running_on_startup()
    assert nombre >= 2, "les tranches running ET pending doivent être ramassées"

    db = SessionLocal()
    try:
        running = db.get(InterviewSegmentJob, id_running)
        pending = db.get(InterviewSegmentJob, id_pending)
        done = db.get(InterviewSegmentJob, id_done)

        assert running.status == "failed"
        assert pending.status == "failed"
        assert "redémarrage" in (running.error or "")
        # Le texte reste intact : c'est lui qui permet de rejouer la tranche
        # sans retoucher à la transcription entière.
        assert running.text == "du texte"
        # Une tranche déjà aboutie n'est jamais touchée.
        assert done.status == "done"
        assert done.error is None
    finally:
        db.close()


def test_le_demarrage_de_l_application_declenche_la_reconciliation(monkeypatch) -> None:
    """Le câblage lui-même, pas seulement la fonction : c'est le `lifespan` qui
    doit l'appeler. Sans cette assertion, retirer l'appel de `app/main.py`
    laisserait le test précédent vert et le bug reviendrait entier.

    Les préchauffages (Whisper, Ollama) sont neutralisés : ils sont lents et
    sans rapport avec ce qu'on vérifie ici."""
    from app import main as app_main

    monkeypatch.setattr(app_main, "warm_up_ollama", lambda: None)
    monkeypatch.setattr(app_main.audio_transcribe, "warm_up", lambda: None)

    db = SessionLocal()
    try:
        id_running = _job(db, position=10, status="running")
    finally:
        db.close()

    # Le context manager du TestClient joue le `lifespan` de l'application.
    with TestClient(app_main.app):
        pass

    db = SessionLocal()
    try:
        assert db.get(InterviewSegmentJob, id_running).status == "failed"
    finally:
        db.close()


def test_un_commit_rate_ne_laisse_pas_la_tranche_bloquee_en_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garde-fou du garde-fou (audit-technique robustesse du 2026-09-09).

    Le `except Exception` de `run_segment_job` promet qu'un job planté ne reste
    jamais `running`. Il ne tient cette promesse que si la session est
    UTILISABLE au moment du `db.get()` de secours : quand l'exception vient
    d'un commit raté (verrou SQLite en production), la session est en
    PendingRollback et ce `db.get()` lève à son tour — avalé par le `except`
    englobant, il laisse le statut bloqué à `running` par un 2e chemin. Seul
    `global_synthesis_job` avait reçu le `db.rollback()` correspondant (revue
    du 2026-09-07) ; il est ici porté à l'identique.

    Le commit raté est reproduit par une violation de clé primaire — même état
    de session qu'un verrou, sans dépendre d'une course."""

    def _casse_la_session(db, job):
        # Commit qui échoue : la session reste en transaction cassée. Le
        # `except`/`pass` reproduit l'avalement qui se produit en vrai (le
        # commit raté est à l'intérieur du try de la tâche de fond).
        db.add(InterviewSegmentJob(id=job.id, session_token="doublon", position=0))
        try:
            db.commit()
        except Exception:
            pass
        raise RuntimeError("échec survenu après un commit raté")

    monkeypatch.setattr(interview_segment_jobs, "_extract_for_job", _casse_la_session)
    db = SessionLocal()
    job = InterviewSegmentJob(session_token="tok-rollback", position=0,
                              status="pending", text="texte")
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    interview_segment_jobs.run_segment_job(job_id)  # contrat : ne lève jamais

    db = SessionLocal()
    refreshed = db.get(InterviewSegmentJob, job_id)
    assert refreshed.status == "failed", (
        "tranche bloquee en 'running' a vie : l'ecran polle un job qui ne "
        "changera plus jamais d'etat"
    )
    assert "RuntimeError" in (refreshed.error or "")
    db.close()
