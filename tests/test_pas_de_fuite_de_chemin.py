"""Aucun message d'exception brut renvoyé au client (finding audit-technique
securite:critique du 2026-09-04, `.claude/audits/VSCode2.json` côté hub) :

« Message d'exception brut renvoyé au client sur 12 sites, divulguant les
chemins absolus du poste sur les OSError — à distinguer d'une fuite de stack
trace, qui elle n'existe pas ».

Les sites en cause sont ceux qui répondent 500 sur une exception QUELCONQUE :
`str(exc)` y valait `[Errno 13] ... 'C:\\Users\\<nom>\\Documents\\VSCode2\\data\\
recordings\\...'`. Les exceptions MÉTIER du projet (`TranscriptionError`,
`SynthesisAIError`, `AnalysisParseError`, `UploadTropVolumineux`) portent au
contraire un message écrit pour l'utilisateur : elles restent affichées telles
quelles, et ces tests ne doivent pas les confondre avec une fuite.

Échec sur le code d'avant : la réponse contenait le chemin absolu levé par
l'OSError simulée.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview, Mission
from app.routers import interviews as routeur_interviews

_CHEMIN_SECRET = r"C:\Users\jean.dupont\Documents\VSCode2\data\recordings\secret.webm"


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
    """`raise_server_exceptions=False` : c'est la réponse REÇUE PAR LE
    NAVIGATEUR qu'on veut inspecter, pas l'exception re-levée côté test."""
    return TestClient(app, raise_server_exceptions=False)


def _oserror(*_args, **_kwargs):
    raise OSError(13, "Permission denied", _CHEMIN_SECRET)


def test_transcription_de_segment_ne_publie_pas_le_chemin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`POST /audio/transcribe-segment` — premier des 5 sites cités."""
    monkeypatch.setattr(routeur_interviews.audio_transcribe, "transcribe_audio", _oserror)

    response = client.post(
        "/audio/transcribe-segment",
        files={"file": ("seg.webm", b"des octets", "audio/webm")},
    )

    assert response.status_code == 500
    assert _CHEMIN_SECRET not in response.text
    assert "jean.dupont" not in response.text
    assert "Permission denied" not in response.text
    assert "transcription" in response.json()["error"].lower()


def test_sauvegarde_audio_de_secours_ne_publie_pas_le_chemin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`POST /interviews/record-backup` — l'écriture disque est justement le
    site où l'OSError porte un chemin absolu.

    Le point d'injection a changé le 2026-09-10 : la route n'appelle plus
    `shutil.copyfileobj` en direct mais `uploads.ecrire_audio_borne`, qui porte
    le plafond disque. On monkeypatche donc la fonction telle que le ROUTEUR la
    voit (elle y est importée par nom, donc liée dans son espace de noms) —
    patcher `app.uploads.ecrire_audio_borne` laisserait la route appeler
    l'originale et le test passerait sans jamais atteindre le bloc en cause."""
    monkeypatch.setattr(routeur_interviews, "ecrire_audio_borne", _oserror)

    response = client.post(
        "/missions/1/interviews/record/backup",
        files={"file": ("entretien.webm", b"des octets", "audio/webm")},
    )

    assert response.status_code == 500
    assert _CHEMIN_SECRET not in response.text
    assert "jean.dupont" not in response.text


def test_transcription_des_notes_ne_publie_pas_le_chemin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`POST /interviews/{id}/notes/transcribe` — dernier site cité
    (`interviews.py:2974` dans la numérotation de l'audit).

    L'entretien est créé pour de vrai : sans lui la route répond 404 et le
    test passerait sans jamais atteindre le bloc en cause — une assertion
    qui ne s'exécute pas est un test vert par construction."""
    session = SessionLocal()
    try:
        mission = Mission(name="Mission notes")
        session.add(mission)
        session.commit()
        interview = Interview(
            mission_id=mission.id, interviewee_name="Bob", mode="libre", status="done",
        )
        session.add(interview)
        session.commit()
        interview_id = interview.id
    finally:
        session.close()

    monkeypatch.setattr(routeur_interviews.audio_transcribe, "transcribe_audio", _oserror)

    response = client.post(
        f"/interviews/{interview_id}/notes/transcribe",
        files={"file": ("notes.webm", b"des octets", "audio/webm")},
    )

    assert response.status_code == 500, "le test doit atteindre le bloc de transcription"
    assert _CHEMIN_SECRET not in response.text
    assert "jean.dupont" not in response.text


def test_les_messages_metier_restent_affiches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contre-épreuve indispensable : le correctif ne doit pas transformer
    TOUT message d'erreur en générique. Une `TranscriptionError` porte un
    texte écrit pour l'utilisateur (« aucune parole détectée », etc.) — le
    masquer dégraderait le produit sans rien sécuriser."""

    def _metier(*_args, **_kwargs):
        raise routeur_interviews.audio_transcribe.TranscriptionError(
            "Le fichier audio est vide."
        )

    monkeypatch.setattr(routeur_interviews.audio_transcribe, "transcribe_audio", _metier)

    response = client.post(
        "/audio/transcribe-segment",
        files={"file": ("seg.webm", b"des octets", "audio/webm")},
    )

    assert response.status_code == 422
    assert "Le fichier audio est vide." in response.json()["error"]
