"""Bilan de complétude d'une transcription de fichier (lot « transcription
fiable », commit 2).

Critère : « une transcription de la totalité de l'audio sans segment failed du
1er coup ». Avant : un job pouvait finir `done` incomplet SANS alarme (seul le
cas « tous les blocs vides » échouait), et les incidents (tranches ignorées)
vivaient dans une variable locale puis dans `job.error`, remis à None à la
reprise — donc perdus à la coupure. Désormais `AudioFileJob.completude` porte
un bilan CUMULÉ ; `done` reste `done` (on signale sans bloquer).

Whisper est doublé (`iter_transcribe_blocks`) ; aucune IA, base de test jetable.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.main import app
from app.models import AudioFileJob, Interview, Mission
from app.services import audio_file_jobs, audio_transcribe
from app.services.audio_transcribe import bilan_completude

_PREFIXE = "completude_test_"


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
    for fichier in RECORDINGS_DIR.glob(f"{_PREFIXE}*"):
        try:
            fichier.unlink()
        except OSError:
            pass


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# --------------------------------------------------------------------------- #
# bilan_completude : fonction pure, sans base
# --------------------------------------------------------------------------- #
def _bilan(**kw):
    base = dict(
        duree_conteneur_s=120.0, duree_decodee_s=120.0, blocs=["a", "b"],
        block_s=60, tranches_ignorees=[], blocs_rejoues=[],
    )
    base.update(kw)
    return bilan_completude(**base)


def test_bilan_temoin_negatif_complet():
    b = _bilan()
    assert b["statut"] == "complet"
    assert b["blocs_vides"] == [] and b["tranches_ignorees"] == []
    assert b["blocs_rejoues"] == [] and b["ecart_s"] == 0.0


def test_bilan_bloc_vide_est_incomplet_avec_son_index():
    b = _bilan(blocs=["a", "  ", "c", ""])
    assert b["statut"] == "incomplet"
    assert b["blocs_vides"] == [1, 3]


def test_bilan_tranche_ignoree_est_incomplete():
    b = _bilan(tranches_ignorees=[2])
    assert b["statut"] == "incomplet"
    assert b["tranches_ignorees"] == [2]


def test_bilan_conteneur_illisible_sans_autre_signe_est_non_evaluable():
    b = _bilan(duree_conteneur_s=None)
    assert b["statut"] == "non_evaluable"
    assert b["ecart_s"] is None and b["duree_conteneur_s"] is None


def test_bilan_conteneur_illisible_mais_bloc_vide_reste_incomplet():
    assert _bilan(duree_conteneur_s=None, blocs=["a", ""])["statut"] == "incomplet"
    assert _bilan(duree_conteneur_s=None, tranches_ignorees=[1])["statut"] == "incomplet"


def test_bilan_ecart_au_dela_de_la_tolerance_est_incomplet():
    b = _bilan(duree_conteneur_s=600.0, duree_decodee_s=570.0)  # 30 s > 2 % (12 s)
    assert b["statut"] == "incomplet"
    assert b["ecart_s"] == 30.0


def test_bilan_ecart_dans_la_tolerance_est_complet():
    assert _bilan(duree_conteneur_s=600.0, duree_decodee_s=590.0)["statut"] == "complet"
    # Plancher de 2 s sur un fichier court (2 % de 60 s = 1,2 s).
    assert _bilan(duree_conteneur_s=60.0, duree_decodee_s=58.5)["statut"] == "complet"
    assert _bilan(duree_conteneur_s=60.0, duree_decodee_s=57.0)["statut"] == "incomplet"


def test_bilan_bloc_rejoue_seul_reste_complet_mais_expose():
    b = _bilan(blocs_rejoues=[1, 0, 1])
    assert b["statut"] == "complet"
    assert b["blocs_rejoues"] == [0, 1]


# --------------------------------------------------------------------------- #
# run_audio_file_job : bilan persisté et cumulé
# --------------------------------------------------------------------------- #
def _fichier(nom: str) -> str:
    """Pose un fichier audio factice ; le contenu (= le nom) identifie la tranche."""
    complet = f"{_PREFIXE}{nom}"
    (RECORDINGS_DIR / complet).write_bytes(complet.encode())
    return complet


def _faux_iter(plan: dict):
    """Double de `iter_transcribe_blocks` piloté par `plan[contenu]` :
    blocs, conteneur, decodee, rejoues, muet, leve_au (coupure avant ce bloc)."""
    def _iter(content, block_s=None, start_index=0, stats=None):
        p = plan[content.decode()]
        if stats is not None:
            stats["blocs_rejoues"] = list(p.get("rejoues", []))
            stats["duree_conteneur_s"] = p.get("conteneur")
            stats["duree_decodee_s"] = p.get("decodee")
        if p.get("muet"):
            raise audio_transcribe.NoSpeechError("Aucune parole détectée.")
        blocs = p["blocs"]
        for i in range(start_index, len(blocs)):
            if p.get("leve_au") == i:
                raise RuntimeError("coupure simulée")
            yield i, len(blocs), blocs[i]
    return _iter


def _creer_job(**champs) -> int:
    db = SessionLocal()
    try:
        champs.setdefault("session_token", "tok-completude")
        champs.setdefault("status", "pending")
        job = AudioFileJob(**champs)
        db.add(job)
        db.commit()
        return job.id
    finally:
        db.close()


def _relire(job_id: int) -> AudioFileJob:
    db = SessionLocal()
    try:
        job = db.get(AudioFileJob, job_id)
        db.expunge(job)
        return job
    finally:
        db.close()


def test_reprise_apres_coupure_garde_les_incidents(monkeypatch):
    """Le test qui départage la colonne de `job.error` : une tranche ignorée et
    un bloc rejoué AVANT la coupure doivent figurer dans le bilan final, alors
    que la reprise remet `error` à None et ne repasse plus par ces tranches."""
    a, b, c = _fichier("a.webm"), _fichier("b.webm"), _fichier("c.webm")
    plan = {
        a: {"muet": True},
        b: {"blocs": ["B0.", "B1."], "rejoues": [0], "leve_au": 1,
            "conteneur": 120.0, "decodee": 120.0},
        c: {"blocs": ["C0."], "conteneur": 60.0, "decodee": 60.0},
    }
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _faux_iter(plan))
    job_id = _creer_job(filenames=[a, b, c])

    audio_file_jobs.run_audio_file_job(job_id)
    coupe = _relire(job_id)
    assert coupe.status == "failed"
    # Le bilan partiel est gardé même sur un échec.
    assert coupe.completude["tranches_ignorees"] == [1]
    assert coupe.completude["blocs_rejoues"] == [0]

    # Reprise : la coupure est réparée, la tranche 1 ne sera PAS rejouée.
    plan[b]["leve_au"] = None
    plan[b]["rejoues"] = []
    db = SessionLocal()
    try:
        assert audio_file_jobs.reprendre_job_echoue_ou_perime(db, db.get(AudioFileJob, job_id))
    finally:
        db.close()
    audio_file_jobs.run_audio_file_job(job_id)

    fin = _relire(job_id)
    assert fin.status == "done", fin.error
    assert fin.error is None  # la reprise remet bien `error` à None...
    assert fin.completude["tranches_ignorees"] == [1]  # ...mais le bilan, non
    assert fin.completude["blocs_rejoues"] == [0]
    assert fin.completude["statut"] == "incomplet"
    assert fin.blocks == ["B0.", "B1.", "C0."]


def test_job_done_avec_bloc_vide_reste_done_mais_incomplet(monkeypatch):
    f = _fichier("vide.webm")
    plan = {f: {"blocs": ["Texte.", "   ", "Fin."], "conteneur": 180.0, "decodee": 180.0}}
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _faux_iter(plan))
    job_id = _creer_job(filename=f)

    audio_file_jobs.run_audio_file_job(job_id)

    job = _relire(job_id)
    assert job.status == "done"
    assert job.error is None  # sémantique de `error` inchangée
    assert job.completude["statut"] == "incomplet"
    assert job.completude["blocs_vides"] == [1]


def test_job_dont_tous_les_blocs_sont_vides_echoue_comme_avant(monkeypatch):
    f = _fichier("muet.webm")
    plan = {f: {"blocs": ["", "  "], "conteneur": 120.0, "decodee": 120.0}}
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _faux_iter(plan))
    job_id = _creer_job(filename=f)

    audio_file_jobs.run_audio_file_job(job_id)

    job = _relire(job_id)
    assert job.status == "failed"
    assert job.error == "Aucune parole détectée dans l'enregistrement."
    assert job.completude["blocs_vides"] == [0, 1]
    assert job.completude["statut"] == "incomplet"


def test_bloc_rejoue_est_expose_sans_rendre_le_job_incomplet(monkeypatch):
    f = _fichier("rejoue.webm")
    plan = {f: {"blocs": ["A.", "B."], "rejoues": [1], "conteneur": 120.0, "decodee": 120.0}}
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _faux_iter(plan))
    job_id = _creer_job(filename=f)

    audio_file_jobs.run_audio_file_job(job_id)

    job = _relire(job_id)
    assert job.status == "done"
    assert job.completude["statut"] == "complet"
    assert job.completude["blocs_rejoues"] == [1]


def test_ecart_de_duree_conteneur_decode_rend_le_job_incomplet(monkeypatch):
    f = _fichier("ecart.webm")
    plan = {f: {"blocs": ["A.", "B."], "conteneur": 600.0, "decodee": 540.0}}
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _faux_iter(plan))
    job_id = _creer_job(filename=f)

    audio_file_jobs.run_audio_file_job(job_id)

    job = _relire(job_id)
    assert job.status == "done"
    assert job.completude["statut"] == "incomplet"
    assert job.completude["ecart_s"] == 60.0


def test_un_echec_de_tranche_garde_le_bilan_partiel(monkeypatch):
    """Un job `failed` (erreur inattendue au bloc 0, après un rejeu) garde les
    rejeux déjà survenus."""
    f = _fichier("echec.webm")
    plan = {f: {"blocs": ["A."], "rejoues": [0], "leve_au": 0,
                "conteneur": 60.0, "decodee": 60.0}}
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _faux_iter(plan))
    job_id = _creer_job(filename=f)

    audio_file_jobs.run_audio_file_job(job_id)

    job = _relire(job_id)
    assert job.status == "failed"
    assert job.completude is not None
    assert job.completude["blocs_rejoues"] == [0]


def test_un_double_sans_stats_donne_un_bilan_non_evaluable(monkeypatch):
    f = _fichier("sansstats.webm")

    def _iter(content, block_s=None, start_index=0):
        yield 0, 1, "Texte."

    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _iter)
    job_id = _creer_job(filename=f)

    audio_file_jobs.run_audio_file_job(job_id)

    job = _relire(job_id)
    assert job.status == "done"
    assert job.completude["statut"] == "non_evaluable"


def test_le_bilan_persiste_ne_porte_ni_chemin_ni_texte(monkeypatch):
    a, b = _fichier("secret_a.webm"), _fichier("secret_b.webm")
    plan = {
        a: {"muet": True},
        b: {"blocs": ["Propos confidentiel.", ""], "rejoues": [0],
            "conteneur": 120.0, "decodee": 120.0},
    }
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _faux_iter(plan))
    job_id = _creer_job(filenames=[a, b])

    audio_file_jobs.run_audio_file_job(job_id)

    brut = json.dumps(_relire(job_id).completude)
    assert "Propos" not in brut
    assert _PREFIXE not in brut and "secret_" not in brut
    assert str(RECORDINGS_DIR) not in brut


# --------------------------------------------------------------------------- #
# Pollers : champ plat `completude` (dict ou null)
# --------------------------------------------------------------------------- #
def test_poller_import_expose_completude_et_null_pour_un_job_ancien(client):
    bilan = {"statut": "incomplet", "blocs_vides": [1], "tranches_ignorees": [],
             "blocs_rejoues": [], "duree_conteneur_s": None, "duree_decodee_s": None,
             "ecart_s": None}
    neuf = _creer_job(session_token="tok-poll-neuf", status="failed", blocks=["x"],
                      completude=bilan)
    ancien = _creer_job(session_token="tok-poll-ancien", status="failed", blocks=["x"])

    d_neuf = client.get(
        f"/audio/transcribe-file/status?job_id={neuf}&session_token=tok-poll-neuf&since=0"
    ).json()
    d_ancien = client.get(
        f"/audio/transcribe-file/status?job_id={ancien}&session_token=tok-poll-ancien&since=0"
    ).json()

    assert d_neuf["completude"] == bilan
    assert "completude" in d_ancien and d_ancien["completude"] is None
    for cle in ("status", "blocks", "done", "total", "error", "filename"):
        assert cle in d_neuf


def test_poller_retranscription_expose_completude_et_null_pour_un_job_ancien(client):
    db = SessionLocal()
    try:
        mission = Mission(name="Mission complétude")
        db.add(mission)
        db.flush()
        nom = _fichier("retrans.webm")
        interview = Interview(
            mission_id=mission.id, mode="libre", interviewee_name="Alice",
            status="done", audio_segments=[{"filename": nom, "position": 0}],
        )
        db.add(interview)
        db.flush()
        job = AudioFileJob(
            session_token="tok-retrans", status="done", filenames=[nom],
            interview_id=interview.id, blocks=["x"], files_done=1,
            completude={"statut": "complet", "blocs_rejoues": [0]},
        )
        db.add(job)
        db.commit()
        iid, jid = interview.id, job.id
    finally:
        db.close()

    url = f"/interviews/{iid}/retranscrire/statut"
    data = client.get(url).json()
    assert data["completude"] == {"statut": "complet", "blocs_rejoues": [0]}
    assert data["status"] == "done" and "blocs" in data and "ia_total" in data

    db = SessionLocal()
    try:
        db.get(AudioFileJob, jid).completude = None
        db.commit()
    finally:
        db.close()
    data = client.get(url).json()
    assert "completude" in data and data["completude"] is None


# --------------------------------------------------------------------------- #
# iter_transcribe_blocks expose la durée du conteneur
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sonde", [12.5, None])
def test_le_generateur_expose_la_duree_du_conteneur(monkeypatch, sonde):
    import numpy as np

    at = audio_transcribe
    monkeypatch.setattr(at, "_faster_whisper", lambda: object())
    monkeypatch.setattr(at, "_probe_duration_s", lambda content: sonde)
    monkeypatch.setattr(
        at, "_decode_to_pcm16k", lambda content: np.zeros(16000 * 5, dtype=np.float32)
    )
    monkeypatch.setattr(at, "_transcribe_pcm_sequential", lambda pcm: "texte")

    stats: dict = {}
    avec = list(at.iter_transcribe_blocks(b"x", block_s=60, stats=stats))
    sans = list(at.iter_transcribe_blocks(b"x", block_s=60))

    assert stats["duree_conteneur_s"] == sonde
    assert stats["duree_decodee_s"] == 5.0
    assert avec == sans == [(0, 1, "texte")]


# --------------------------------------------------------------------------- #
# Revue adversariale du commit 2 : mutants survivants fermés
# --------------------------------------------------------------------------- #
def test_bilan_borne_exacte_de_la_tolerance():
    # Plafond : max(2 s, 2 %). Sur la borne exacte -> complet ; au-delà -> incomplet.
    assert _bilan(duree_conteneur_s=600.0, duree_decodee_s=588.0)["statut"] == "complet"
    assert _bilan(duree_conteneur_s=600.0, duree_decodee_s=587.9)["statut"] == "incomplet"
    assert _bilan(duree_conteneur_s=60.0, duree_decodee_s=58.0)["statut"] == "complet"
    assert _bilan(duree_conteneur_s=60.0, duree_decodee_s=57.9)["statut"] == "incomplet"


def test_bilan_decode_plus_long_que_le_conteneur_n_est_pas_incomplet():
    """Seul de l'audio PERDU au décodage est une incomplétude ; la durée d'un
    .webm de MediaRecorder est approximative, un décodé plus long ne doit pas
    alarmer. L'écart reste exposé (négatif)."""
    b = _bilan(duree_conteneur_s=600.0, duree_decodee_s=640.0)
    assert b["statut"] == "complet"
    assert b["ecart_s"] == -40.0


def test_bilan_non_termine_n_affirme_jamais_complet():
    # Durées partielles (job en échec / en reprise) : « non évaluable », pas « complet ».
    assert _bilan(termine=False)["statut"] == "non_evaluable"
    # ... mais une preuve d'incomplétude reste dite.
    assert _bilan(termine=False, blocs=["a", ""])["statut"] == "incomplet"
    assert _bilan(termine=False, tranches_ignorees=[1])["statut"] == "incomplet"


def test_job_en_echec_en_cours_de_fichier_n_est_jamais_dit_complet(monkeypatch):
    """Fichier unique : 3 blocs non vides livrés puis une panne. Les durées n'ont
    pas été cumulées (0.0 / 0.0) : sans garde, l'écart nul donnait « complet »
    sur un job `failed`."""
    f = _fichier("panne.webm")
    plan = {f: {"blocs": ["A.", "B.", "C.", "D."], "leve_au": 3,
                "conteneur": 240.0, "decodee": 240.0}}
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _faux_iter(plan))
    job_id = _creer_job(filename=f)

    audio_file_jobs.run_audio_file_job(job_id)

    job = _relire(job_id)
    assert job.status == "failed"
    assert job.completude["statut"] == "non_evaluable"


def test_reprise_conserve_les_durees_deja_cumulees(monkeypatch):
    """Les durées des tranches finies sont reprises du bilan persisté : sans
    elles la reprise repartait de 0.0 et le total final était faux."""
    a, b = _fichier("d_a.webm"), _fichier("d_b.webm")
    plan = {
        a: {"blocs": ["A0.", "A1."], "conteneur": 120.0, "decodee": 120.0},
        b: {"blocs": ["B0."], "leve_au": 0, "conteneur": 60.0, "decodee": 60.0},
    }
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _faux_iter(plan))
    job_id = _creer_job(filenames=[a, b])

    audio_file_jobs.run_audio_file_job(job_id)
    assert _relire(job_id).status == "failed"

    plan[b]["leve_au"] = None
    db = SessionLocal()
    try:
        assert audio_file_jobs.reprendre_job_echoue_ou_perime(db, db.get(AudioFileJob, job_id))
    finally:
        db.close()
    audio_file_jobs.run_audio_file_job(job_id)

    fin = _relire(job_id)
    assert fin.status == "done", fin.error
    assert fin.completude["duree_conteneur_s"] == 180.0   # 120 (avant coupure) + 60
    assert fin.completude["duree_decodee_s"] == 180.0
    assert fin.completude["statut"] == "complet"


def test_fichier_introuvable_garde_un_bilan(monkeypatch):
    job_id = _creer_job(filename=f"{_PREFIXE}absent.webm")   # jamais écrit sur disque

    audio_file_jobs.run_audio_file_job(job_id)

    job = _relire(job_id)
    assert job.error == "Fichier audio introuvable ou illisible."
    assert job.completude is not None
    assert job.completude["statut"] == "non_evaluable"


def test_toutes_tranches_ignorees_nomme_aussi_celles_d_avant_la_coupure(monkeypatch):
    a, b = _fichier("t_a.webm"), _fichier("t_b.webm")
    plan = {a: {"muet": True}, b: {"blocs": ["x"], "leve_au": 0}}
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _faux_iter(plan))
    job_id = _creer_job(filenames=[a, b])

    audio_file_jobs.run_audio_file_job(job_id)           # a ignorée, b coupée
    assert _relire(job_id).status == "failed"

    plan[b] = {"muet": True}
    db = SessionLocal()
    try:
        assert audio_file_jobs.reprendre_job_echoue_ou_perime(db, db.get(AudioFileJob, job_id))
    finally:
        db.close()
    audio_file_jobs.run_audio_file_job(job_id)           # b ignorée à son tour

    job = _relire(job_id)
    assert job.status == "failed"
    assert job.error.startswith("Aucune tranche exploitable")
    assert "tranche 1 ignorée" in job.error and "tranche 2 ignorée" in job.error
    assert job.completude["tranches_ignorees"] == [1, 2]


def test_le_filet_ne_depend_pas_du_bilan(monkeypatch):
    """Si le bilan lève lui-même, le job ne doit PAS rester « running » : le
    filet final termine en `failed` malgré tout."""
    f = _fichier("filet.webm")
    plan = {f: {"blocs": ["A."], "conteneur": 60.0, "decodee": 60.0}}
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _faux_iter(plan))

    def _boum(*a, **k):
        raise ValueError("bilan cassé")

    monkeypatch.setattr(audio_transcribe, "bilan_completude", _boum)
    job_id = _creer_job(filename=f)

    audio_file_jobs.run_audio_file_job(job_id)

    job = _relire(job_id)
    assert job.status == "failed"
    assert job.error == "Échec inattendu (ValueError)."


def test_un_bilan_persiste_corrompu_ne_bloque_pas_le_job(monkeypatch):
    f = _fichier("corrompu.webm")
    plan = {f: {"blocs": ["A."], "conteneur": 60.0, "decodee": 60.0}}
    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _faux_iter(plan))
    job_id = _creer_job(filename=f, completude=["pas", "un", "dict"])

    audio_file_jobs.run_audio_file_job(job_id)

    job = _relire(job_id)
    assert job.status == "done", job.error
    assert job.completude["statut"] == "complet"


def test_accepte_stats_voit_les_kwargs_et_le_parametre_stats():
    assert audio_file_jobs._accepte_stats(lambda c, block_s=None, stats=None: None)
    assert audio_file_jobs._accepte_stats(lambda *a, **k: None)
    assert not audio_file_jobs._accepte_stats(lambda c, block_s=None: None)
