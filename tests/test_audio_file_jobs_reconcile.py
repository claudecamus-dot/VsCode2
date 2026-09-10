"""Reprise après crash des imports audio : le troisième volet du filet.

Constat d'audit du hub (risque technique, 2026-09-09) : `main.py` appelait
`reconcile_running_on_startup()` pour la synthèse globale ET pour les tranches
d'entretien, mais `audio_file_jobs.py` n'avait aucun équivalent (`grep
reconcile` : 0 occurrence). Un import audio orphelin d'un redémarrage ne se
corrigeait que RÉACTIVEMENT, via `is_audio_file_job_stale` — c'est-à-dire
seulement si quelqu'un revenait sur l'écran qui l'interroge, et pas avant 3 h.

C'est exactement le scénario que la réconciliation des tranches a été posée
pour fermer le 2026-09-08, après un incident réel (3 tranches figées 5h30 sur
un entretien de 2h).

Ces tests échouent sur le code d'avant : `audio_file_jobs.reconcile_running_on_
startup` n'existait pas (AttributeError), et le `lifespan` ne l'appelait pas.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import AudioFileJob
from app.services import audio_file_jobs


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


def _job(db, *, statut: str, blocs: list[str] | None = None) -> int:
    job = AudioFileJob(
        session_token="tok-audio-reconcile",
        filename="entretien.m4a",
        status=statut,
        blocks=blocs if blocs is not None else [],
        total_blocks=3,
        files_done=1,
        blocks_before_file=2,
    )
    db.add(job)
    db.commit()
    return job.id


def test_reconcile_libere_les_imports_figes_et_garde_les_blocs():
    """Un import `running` ou `pending` hérité d'un processus mort repasse en
    `failed` — et surtout, les blocs déjà transcrits sont CONSERVÉS : une
    transcription d'entretien coûte des dizaines de minutes."""
    db = SessionLocal()
    try:
        id_running = _job(db, statut="running", blocs=["bloc un", "bloc deux"])
        id_pending = _job(db, statut="pending")
        id_done = _job(db, statut="done", blocs=["fini"])
    finally:
        db.close()

    # `>= 2` et non `== 2` : la base de test est partagée par la session.
    # L'assertion exacte porte sur NOS trois lignes, ci-dessous.
    nombre = audio_file_jobs.reconcile_running_on_startup()
    assert nombre >= 2, "les imports running ET pending doivent etre ramasses"

    db = SessionLocal()
    try:
        running = db.get(AudioFileJob, id_running)
        pending = db.get(AudioFileJob, id_pending)
        done = db.get(AudioFileJob, id_done)

        assert running.status == "failed"
        assert pending.status == "failed"
        assert "redémarrage" in (running.error or "")
        # Le travail déjà payé reste acquis : c'est la raison d'être du choix
        # « seul le statut change ».
        assert running.blocks == ["bloc un", "bloc deux"]
        assert running.files_done == 1
        assert running.blocks_before_file == 2
        # Un import déjà abouti n'est jamais touché.
        assert done.status == "done"
        assert done.error is None
        assert done.blocks == ["fini"]
    finally:
        db.close()


def test_le_demarrage_de_l_application_declenche_la_reconciliation_audio(monkeypatch):
    """Le CÂBLAGE, pas seulement la fonction : c'est le `lifespan` qui doit
    l'appeler. Sans cette assertion, retirer l'appel de `app/main.py` laisserait
    le test précédent vert et le défaut reviendrait entier — c'est précisément
    l'asymétrie que l'audit a relevée."""
    from app import main as app_main

    monkeypatch.setattr(app_main, "warm_up_ollama", lambda: None)
    monkeypatch.setattr(app_main.audio_transcribe, "warm_up", lambda: None)

    db = SessionLocal()
    try:
        id_running = _job(db, statut="running", blocs=["a"])
    finally:
        db.close()

    with TestClient(app_main.app):
        pass

    db = SessionLocal()
    try:
        job = db.get(AudioFileJob, id_running)
        assert job.status == "failed", (
            "le lifespan n'appelle pas la reconciliation des imports audio"
        )
        assert job.blocks == ["a"]
    finally:
        db.close()


def test_reconcile_est_rejouable_sans_toucher_aux_echecs_deja_marques():
    """`init_db`/`lifespan` tourne à chaque démarrage : un second passage ne
    doit pas réécrire le motif d'un échec qui n'a rien à voir avec un
    redémarrage — sinon on efface le diagnostic d'une vraie panne."""
    db = SessionLocal()
    try:
        job = AudioFileJob(
            session_token="tok-audio-reconcile",
            filename="casse.m4a",
            status="failed",
            error="Fichier audio illisible.",
        )
        db.add(job)
        db.commit()
        id_echec = job.id
    finally:
        db.close()

    audio_file_jobs.reconcile_running_on_startup()

    db = SessionLocal()
    try:
        assert db.get(AudioFileJob, id_echec).error == "Fichier audio illisible."
    finally:
        db.close()
