"""Enregistrement robuste aux segments `failed` (2026-10-19).

C3 récupération avec retry transitoire, C5 retry par bloc audio + cause en base,
C6 bloc muet isolé, V1 date du jour par défaut, V2 (JS EXÉCUTÉ) : segments perdus
non bloquants quand l'audio complet est sauvegardé côté serveur."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.main import app
from app.models import AudioFileJob, InterviewSegmentJob, Mission
from app.services import audio_file_jobs, audio_transcribe
from app.services import interview_segment_jobs as isj

RACINE = Path(__file__).resolve().parent.parent
LIBRE = RACINE / "app" / "templates" / "interviews" / "record_libre.html"
_MISSION_ID = 0


def setup_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()
    global _MISSION_ID
    db = SessionLocal()
    try:
        m = Mission(name="Mission segments failed")
        db.add(m)
        db.commit()
        _MISSION_ID = m.id
    finally:
        db.close()


def teardown_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


# --------------------------------------------------------------------------- #
# C5 / C6 — run_audio_file_job
# --------------------------------------------------------------------------- #
def _job_audio(nom: str) -> int:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    (RECORDINGS_DIR / nom).write_bytes(b"x")
    db = SessionLocal()
    try:
        job = AudioFileJob(session_token="tok-" + nom, filename=nom, status="pending",
                           blocks=[], total_blocks=0)
        db.add(job)
        db.commit()
        return job.id
    finally:
        db.close()


def _lire(job_id: int) -> AudioFileJob:
    db = SessionLocal()
    try:
        job = db.get(AudioFileJob, job_id)
        db.expunge(job)
        return job
    finally:
        db.close()


def _lancer(monkeypatch, nom, iterateur):
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", iterateur)
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0.0, raising=False)
    job_id = _job_audio(nom)
    audio_file_jobs.run_audio_file_job(job_id)
    return _lire(job_id)


def test_bloc_en_echec_transitoire_est_retente_puis_done(monkeypatch):
    appels = []

    def _iter(content, block_s=None, start_index=0):
        appels.append(start_index)
        if len(appels) == 1:
            yield 0, 2, "bloc un"
            raise audio_transcribe.TranscriptionError(audio_transcribe.ECHEC_TRANSCRIPTION)
        yield 1, 2, "bloc deux"

    job = _lancer(monkeypatch, "w2-retry.webm", _iter)
    assert job.status == "done"
    assert job.blocks == ["bloc un", "bloc deux"]
    assert appels == [0, 1]  # reprise au bloc courant, pas de re-transcription


def test_echec_persistant_consigne_la_cause_reelle_en_base(monkeypatch):
    appels = []

    def _iter(content, block_s=None, start_index=0):
        appels.append(1)
        try:
            raise ValueError("secret C:/chemin")
        except ValueError as exc:
            raise audio_transcribe.TranscriptionError(
                audio_transcribe.ECHEC_TRANSCRIPTION) from exc
        yield  # pragma: no cover

    job = _lancer(monkeypatch, "w2-cause.webm", _iter)
    assert job.status == "failed"
    assert len(appels) == audio_file_jobs.BLOC_RETRIES + 1
    assert "ValueError" in job.error
    assert "chemin" not in job.error  # type seul, jamais le texte


def test_bloc_muet_isole_n_echoue_pas_le_job_unique(monkeypatch):
    def _iter(content, block_s=None, start_index=0):
        yield 0, 3, "avant"
        yield 1, 3, ""
        yield 2, 3, "apres"

    job = _lancer(monkeypatch, "w2-muet.webm", _iter)
    assert job.status == "done"
    assert job.blocks == ["avant", "", "apres"]


def test_fichier_entierement_muet_reste_un_echec(monkeypatch):
    def _iter(content, block_s=None, start_index=0):
        raise audio_transcribe.NoSpeechError("Aucune parole détectée dans l'enregistrement.")
        yield  # pragma: no cover

    job = _lancer(monkeypatch, "w2-tout-muet.webm", _iter)
    assert job.status == "failed"
    assert "Aucune parole" in job.error


# --------------------------------------------------------------------------- #
# C3 — recover_stalled_or_failed_jobs retente les erreurs transitoires
# --------------------------------------------------------------------------- #
def test_recuperation_retente_une_erreur_transitoire_puis_done(monkeypatch):
    monkeypatch.setenv("SEGMENT_JOB_RETRIES", "2")
    monkeypatch.setattr(isj, "_attendre", lambda s: None)
    appels = []

    def faux(db, job):
        appels.append(1)
        if len(appels) == 1:
            exc = isj._EXTRACT_ERRORS[0]("panne")
            exc.transitoire = True
            raise exc
        return {"turns": []}

    monkeypatch.setattr(isj, "_extract_for_job", faux)
    db = SessionLocal()
    try:
        job = InterviewSegmentJob(session_token="tok-c3", position=0, status="failed",
                                  text="du texte")
        db.add(job)
        db.commit()
        isj.recover_stalled_or_failed_jobs(db, [job])
        assert job.status == "done" and job.error is None
        assert len(appels) == 2
    finally:
        db.close()


def test_recuperation_synchrone_reste_bornee_un_essai_court(monkeypatch):
    """Revue 2026-10-19 : la récupération tourne DANS le POST d'enregistrement ;
    2 essais x 30 s x n tranches l'auraient fait pendre. Un seul nouvel essai, 2 s."""
    monkeypatch.setenv("SEGMENT_JOB_RETRIES", "4")
    monkeypatch.setenv("SEGMENT_JOB_RETRY_DELAY_S", "60")
    attentes, appels = [], []
    monkeypatch.setattr(isj, "_attendre", attentes.append)

    def toujours_transitoire(db, job):
        appels.append(1)
        exc = isj._EXTRACT_ERRORS[0]("panne")
        exc.transitoire = True
        raise exc

    monkeypatch.setattr(isj, "_extract_for_job", toujours_transitoire)
    db = SessionLocal()
    try:
        job = InterviewSegmentJob(session_token="tok-c3b", position=0, status="failed",
                                  text="du texte")
        db.add(job)
        db.commit()
        isj.recover_stalled_or_failed_jobs(db, [job])
        assert job.status == "failed"
        assert len(appels) == 2 and attentes == [2.0]
    finally:
        db.close()


def test_ecran_de_revue_ne_prefille_pas_la_date_du_jour():
    """Revue 2026-10-19 : un entretien existant sans date ne doit pas être
    enregistré « aujourd'hui » à la simple revue."""
    src = (Path(__file__).resolve().parents[1] / "app" / "templates" / "interviews"
           / "libre_review.html").read_text(encoding="utf-8")
    assert "aujourdhui_iso" not in src


# --------------------------------------------------------------------------- #
# V1 — date du jour par défaut (serveur)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("chemin", ["record-libre", "record", "import"])
def test_date_par_defaut_est_aujourdhui(chemin):
    html = TestClient(app).get(f"/missions/{_MISSION_ID}/interviews/{chemin}").text
    champ = re.search(r'<input type="date" name="interview_date"\s+value="([^"]*)"', html)
    assert champ, f"champ date introuvable sur {chemin}"
    assert champ.group(1) == date.today().isoformat()


# --------------------------------------------------------------------------- #
# V2 — JS EXÉCUTÉ : segments perdus non bloquants si l'audio est sur le serveur
# --------------------------------------------------------------------------- #
def _fonction(src: str, nom: str) -> str:
    m = re.search(r"function\s+%s\s*\(" % nom, src)
    assert m
    i = src.index("{", m.start())
    prof = 0
    for j in range(i, len(src)):
        prof += (src[j] == "{") - (src[j] == "}")
        if prof == 0:
            return src[m.start(): j + 1]
    raise AssertionError


def _etat_bouton(blocking: int, backup_serveur: int, locaux: int, retry_blocking: int = 0) -> dict:
    if shutil.which("node") is None:
        pytest.fail("node est requis pour exécuter le JS de l'écran.")
    src = LIBRE.read_text(encoding="utf-8")
    code = _fonction(src, "updateSubmitState")
    script = """
var transcriptSoFar='x', pendingSegments=0, pendingSegmentJobSubmits=0, pendingBackups=0,
    finalizing=false, recordingActive=false, missionAbsente=false, backupLocalAcquitte=false; var backupConfirmes=1, sessionRestauree=false;
var lostSegments=%s, lostRetryBlocking=%d;
var backupSegments=%s, backupPerdusLocaux=%s;
var submitBtn={disabled:null}, pdfBtn={disabled:null}, statusEl={textContent:''};
%s
updateSubmitState();
console.log(JSON.stringify({disabled: submitBtn.disabled, msg: statusEl.textContent}));
""" % (
        json.dumps([{"blocking": True}] * blocking), retry_blocking,
        json.dumps([{"filename": "a.webm", "position": 0}] * backup_serveur),
        json.dumps([{"position": 1}] * locaux), code,
    )
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr[:2000]
    return json.loads(res.stdout.strip())


def test_segment_perdu_sans_copie_serveur_reste_bloquant():
    assert _etat_bouton(blocking=2, backup_serveur=0, locaux=0)["disabled"] is True


def test_segment_perdu_avec_audio_complet_sur_serveur_ne_bloque_plus():
    etat = _etat_bouton(blocking=2, backup_serveur=1, locaux=0)
    assert etat["disabled"] is False
    assert "2 segments non transcrits" in etat["msg"]
    assert "relancer la transcription" in etat["msg"]


def test_copie_locale_non_transmise_reste_bloquante_meme_avec_segments():
    assert _etat_bouton(blocking=1, backup_serveur=1, locaux=1)["disabled"] is True


def test_le_clic_enregistrer_demande_confirmation_si_segments_perdus():
    src = LIBRE.read_text(encoding="utf-8")
    i = src.index("getElementById('record-form').addEventListener('submit'")
    assert "confirm(" in src[i: i + 700]
