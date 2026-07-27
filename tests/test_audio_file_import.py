"""Tests de l'import d'un fichier audio transcrit BLOC PAR BLOC (2026-07-27).

Avant ce changement, importer un fichier audio postait le fichier ENTIER sur
`/audio/transcribe-segment` : un seul appel synchrone, rien à l'écran avant la
fin (des dizaines de minutes sur un entretien réel), puis UNE extraction IA sur
toute la transcription. Le comportement attendu — et testé ici — est celui du
direct : transcription par blocs, chaque bloc affiché puis extrait en tâche de
fond dès qu'il est prêt.

La transcription Whisper elle-même est monkeypatchée (`iter_transcribe_blocks`)
— ces tests couvrent le contrat de bout en bout (route → job → poll → blocs →
nettoyage du fichier), pas la qualité de la transcription (couverte par les
tests audio réels). La vérification en conditions réelles (vrai fichier,
vrai Whisper, blocs multiples) a été faite séparément avec
`tests/exemple/split_02_petit_30s.weba`.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.main import app
from app.models import AudioFileJob
from app.services import audio_file_jobs, audio_transcribe


def setup_module() -> None:
    # engine.dispose() avant l'unlink : le pool du fichier de test précédent
    # garde sinon une connexion ouverte sur DB_PATH (verrou Windows).
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


def _fake_blocks(*textes: str):
    """Remplace la transcription Whisper par des blocs prédéfinis, rendus dans
    l'ordre comme le fait `iter_transcribe_blocks`."""
    def _iter(content: bytes, block_s: int | None = None):
        for index, texte in enumerate(textes):
            yield index, len(textes), texte
    return _iter


_TOKEN = "sess-test"


def _upload(client: TestClient, contenu: bytes = b"fake-audio", token: str = _TOKEN) -> dict:
    response = client.post(
        "/audio/transcribe-file",
        files={"file": ("entretien.weba", io.BytesIO(contenu), "audio/webm")},
        data={"session_token": token},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _status(client: TestClient, job_id: int, since: int = 0, token: str = _TOKEN):
    return client.get(
        f"/audio/transcribe-file/status?job_id={job_id}"
        f"&session_token={token}&since={since}"
    )


# --------------------------------------------------------------------------- #
# Contrat de la route d'import
# --------------------------------------------------------------------------- #
def test_import_transcrit_le_fichier_bloc_par_bloc(client, monkeypatch):
    """Le fichier importé produit UN bloc par tranche, dans l'ordre — et non
    un unique texte monolithique comme l'ancien appel synchrone."""
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks",
        _fake_blocks("Bloc un.", "Bloc deux.", "Bloc trois."),
    )
    job = _upload(client)

    # Le TestClient exécute les BackgroundTasks en synchrone : le job est déjà
    # traité au retour de la requête d'import.
    status = _status(client, job["job_id"])
    assert status.status_code == 200
    data = status.json()
    assert data["status"] == "done"
    assert data["blocks"] == ["Bloc un.", "Bloc deux.", "Bloc trois."]
    assert data["total"] == 3
    assert data["error"] == ""


def test_le_curseur_since_ne_renvoie_que_les_blocs_neufs(client, monkeypatch):
    """Sans curseur, chaque tick de 3 s re-transférait toute la transcription
    accumulée — quadratique sur un entretien long."""
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks",
        _fake_blocks("Un.", "Deux.", "Trois."),
    )
    job_id = _upload(client)["job_id"]
    data = _status(client, job_id, since=2).json()
    assert data["blocks"] == ["Trois."]
    assert data["done"] == 3  # le client sait où placer son curseur


def test_le_statut_exige_le_jeton_de_session(client, monkeypatch):
    """Les `job_id` sont séquentiels et cette route est la seule à renvoyer du
    contenu d'entretien : sans le bon jeton, elle ne doit rien dire."""
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Secret."),
    )
    job_id = _upload(client, token="sess-proprietaire")["job_id"]
    assert client.get(f"/audio/transcribe-file/status?job_id={job_id}").status_code == 404
    assert _status(client, job_id, token="sess-intrus").status_code == 404
    ok = _status(client, job_id, token="sess-proprietaire")
    assert ok.status_code == 200 and ok.json()["blocks"] == ["Secret."]


def test_import_sans_jeton_refuse_et_ne_laisse_pas_de_fichier(client):
    """Un import sans session ne serait relisable par personne (le statut est
    scopé au jeton) : on refuse, et on ne laisse pas l'audio sur le disque."""
    avant = set(p.name for p in RECORDINGS_DIR.glob("import_*"))
    response = client.post(
        "/audio/transcribe-file",
        files={"file": ("x.weba", io.BytesIO(b"audio"), "audio/webm")},
        data={"session_token": "  "},
    )
    assert response.status_code == 400
    assert set(p.name for p in RECORDINGS_DIR.glob("import_*")) == avant


def test_le_job_est_supprime_une_fois_entierement_consomme(client, monkeypatch):
    """Les blocs portent la transcription d'un entretien : le job ne doit pas
    rester en base une fois que le client a tout récupéré (la purge des 7
    jours ne tourne qu'au prochain import)."""
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("A.", "B."),
    )
    job_id = _upload(client)["job_id"]
    _status(client, job_id, since=0)          # pas encore tout consommé
    db = SessionLocal()
    try:
        assert db.get(AudioFileJob, job_id) is not None
    finally:
        db.close()
    _status(client, job_id, since=2)          # curseur au bout -> nettoyage
    db = SessionLocal()
    try:
        assert db.get(AudioFileJob, job_id) is None
    finally:
        db.close()


def test_un_job_bloque_est_rapporte_en_echec(client):
    """Serveur redémarré pendant la transcription : sans détection, l'écran
    pollait un job qui ne changerait plus jamais d'état, bouton « Continuer »
    désactivé à vie."""
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    try:
        job = AudioFileJob(
            session_token=_TOKEN, filename="", status="running", blocks=[],
        )
        job.created_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        )
        db.add(job)
        db.commit()
        job_id = job.id
    finally:
        db.close()

    data = _status(client, job_id).json()
    assert data["status"] == "failed"
    assert "ne répond plus" in data["error"]


def test_un_fichier_muet_est_un_echec_explicite(client, monkeypatch):
    """Parité avec `transcribe_audio` : sans ce contrôle, l'UI annonçait
    « Fichier transcrit » avec un bouton qui restait désactivé, sans rien
    expliquer."""
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("", "   "),
    )
    job_id = _upload(client)["job_id"]
    data = _status(client, job_id).json()
    assert data["status"] == "failed"
    assert "Aucune parole" in data["error"]


def test_chaque_bloc_est_persiste_des_qu_il_est_pret(client, monkeypatch):
    """Régression du défaut central : les blocs doivent être visibles AU FIL
    DE L'EAU. On observe, depuis le générateur lui-même, ce que le poll aurait
    vu à chaque étape — si `run_audio_file_job` n'avait committé qu'à la fin,
    les états intermédiaires seraient vides."""
    vus: list[list[str]] = []

    def _iter(content: bytes, block_s: int | None = None):
        for index, texte in enumerate(("A", "B", "C")):
            yield index, 3, texte
            # Ce que verrait un poll juste après ce bloc (session distincte,
            # donc uniquement ce qui est réellement committé).
            db = SessionLocal()
            try:
                job = db.scalars(
                    select(AudioFileJob).order_by(AudioFileJob.id.desc()).limit(1)
                ).first()
                vus.append(list(job.blocks or []) if job is not None else [])
            finally:
                db.close()

    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _iter)
    _upload(client)

    assert vus == [["A"], ["A", "B"], ["A", "B", "C"]]


def test_echec_de_transcription_remonte_un_message_utilisable(client, monkeypatch):
    """Un échec ne laisse pas le job « running » (l'écran polle indéfiniment) :
    statut `failed` + message destiné à l'UI."""
    def _iter(content: bytes, block_s: int | None = None):
        raise audio_transcribe.TranscriptionError("Fichier audio illisible : test.")
        yield  # pragma: no cover - rend la fonction génératrice

    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _iter)
    job = _upload(client)

    data = _status(client, job["job_id"]).json()
    assert data["status"] == "failed"
    assert "illisible" in data["error"]


def test_fichier_audio_supprime_apres_traitement(client, monkeypatch):
    """L'audio d'un entretien ne doit pas s'entasser sur le disque une fois son
    texte obtenu (même exigence que la purge des tranches de texte)."""
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Un bloc."),
    )
    job_id = _upload(client)["job_id"]
    db = SessionLocal()
    try:
        job = db.get(AudioFileJob, job_id)
        assert job.status == "done"
        assert not (RECORDINGS_DIR / job.filename).exists()
    finally:
        db.close()


def test_statut_d_un_import_inconnu_est_un_404(client):
    assert _status(client, 999999).status_code == 404


# --------------------------------------------------------------------------- #
# Le générateur réel (sans Whisper : le pool de processus est remplacé par une
# exécution en ligne, ce qui exerce quand même la fenêtre glissante et l'ordre)
# --------------------------------------------------------------------------- #
class _InlineExecutor:
    """Exécute chaque tâche immédiatement, dans l'ordre de soumission —
    substitut de `ProcessPoolExecutor` pour tester la mécanique de fenêtre
    sans charger de modèle."""

    def __init__(self, max_workers=None):
        self.soumissions = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, args):
        self.soumissions.append(args)

        class _F:
            def __init__(self, value=None, exc=None):
                self._value, self._exc = value, exc

            def result(self):
                if self._exc:
                    raise self._exc
                return self._value

        try:
            return _F(value=fn(args))
        except Exception as exc:  # remonté au .result(), comme un vrai future
            return _F(exc=exc)


def _stub_whisper(monkeypatch, textes, erreur_a=None):
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(audio_transcribe, "_faster_whisper", lambda: object())
    monkeypatch.setattr(audio_transcribe, "ProcessPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(audio_transcribe, "MAX_PARALLEL_WORKERS", 2)
    appels = {"n": 0}

    def _chunk(args):
        i = appels["n"]
        appels["n"] += 1
        if erreur_a is not None and i == erreur_a:
            raise RuntimeError("worker mort")
        return textes[i]

    monkeypatch.setattr(audio_transcribe, "_transcribe_pcm_chunk", _chunk)
    monkeypatch.setattr(
        audio_transcribe, "_transcribe_pcm_sequential", lambda pcm: textes[0]
    )
    return np


def test_le_generateur_rend_les_blocs_dans_l_ordre(monkeypatch):
    np = _stub_whisper(monkeypatch, ["un", "deux", "trois", "quatre", "cinq"])
    monkeypatch.setattr(
        audio_transcribe, "_decode_to_pcm16k",
        lambda content: np.zeros(16000 * 5, dtype=np.float32),
    )
    rendu = list(audio_transcribe.iter_transcribe_blocks(b"x", block_s=1))
    assert [t for _, _, t in rendu] == ["un", "deux", "trois", "quatre", "cinq"]
    assert [i for i, _, _ in rendu] == [0, 1, 2, 3, 4]
    assert all(total == 5 for _, total, _ in rendu)


def test_le_generateur_utilise_le_chemin_sequentiel_pour_un_seul_bloc(monkeypatch):
    np = _stub_whisper(monkeypatch, ["bloc unique"])
    monkeypatch.setattr(
        audio_transcribe, "_decode_to_pcm16k",
        lambda content: np.zeros(16000 * 3, dtype=np.float32),
    )
    assert list(audio_transcribe.iter_transcribe_blocks(b"x", block_s=60)) == [
        (0, 1, "bloc unique")
    ]


def test_un_worker_qui_meurt_devient_une_erreur_fonctionnelle(monkeypatch):
    np = _stub_whisper(monkeypatch, ["un", "deux", "trois"], erreur_a=1)
    monkeypatch.setattr(
        audio_transcribe, "_decode_to_pcm16k",
        lambda content: np.zeros(16000 * 3, dtype=np.float32),
    )
    gen = audio_transcribe.iter_transcribe_blocks(b"x", block_s=1)
    assert next(gen)[2] == "un"  # le bloc déjà produit reste acquis
    with pytest.raises(audio_transcribe.TranscriptionError):
        next(gen)


def test_un_pcm_vide_est_une_erreur_fonctionnelle(monkeypatch):
    np = _stub_whisper(monkeypatch, [])
    monkeypatch.setattr(
        audio_transcribe, "_decode_to_pcm16k",
        lambda content: np.array([], dtype=np.float32),
    )
    with pytest.raises(audio_transcribe.TranscriptionError):
        list(audio_transcribe.iter_transcribe_blocks(b"x"))


def test_purge_supprime_les_imports_perimes(monkeypatch):
    """Les blocs portent du texte d'entretien : un import abandonné ne reste
    pas en base indéfiniment."""
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    try:
        vieux = AudioFileJob(
            session_token="vieux", filename="", status="done", blocks=["texte"],
        )
        vieux.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        db.add(vieux)
        db.commit()
        vieux_id = vieux.id
        audio_file_jobs.purge_stale_audio_file_jobs(db)
        assert db.get(AudioFileJob, vieux_id) is None
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Découpage en blocs (pur, sans Whisper)
# --------------------------------------------------------------------------- #
def test_decoupage_en_blocs_couvre_tout_le_signal():
    np = pytest.importorskip("numpy")
    pcm = np.arange(16000 * 25, dtype=np.float32)  # 25 s à 16 kHz
    blocs = audio_transcribe.split_pcm_blocks(pcm, 10)
    assert [len(b) for b in blocs] == [160000, 160000, 80000]
    # Aucun échantillon perdu ni dupliqué entre les blocs.
    assert np.array_equal(np.concatenate(blocs), pcm)


def test_un_signal_plus_court_qu_un_bloc_donne_un_seul_bloc():
    np = pytest.importorskip("numpy")
    pcm = np.zeros(16000 * 3, dtype=np.float32)
    assert len(audio_transcribe.split_pcm_blocks(pcm, 300)) == 1


# --------------------------------------------------------------------------- #
# Écrans : l'import doit exister ET poller, sur les DEUX modes
# --------------------------------------------------------------------------- #
def _mission_id() -> int:
    from app.models import Mission

    db = SessionLocal()
    try:
        mission = Mission(name="Mission import audio")
        db.add(mission)
        db.commit()
        return mission.id
    finally:
        db.close()


@pytest.mark.parametrize("chemin", ["record-libre", "record"])
def test_les_deux_ecrans_importent_par_blocs(client, chemin, monkeypatch):
    """Parité libre/structuré : les deux écrans d'enregistrement proposent
    l'import d'un fichier et le traitent par blocs (poll de `/status` +
    soumission d'un job d'extraction par bloc), au lieu du POST unique
    `/audio/transcribe-segment` sur le fichier entier."""
    # Les écrans masquent tout le dispositif si la transcription locale n'est
    # pas installée — on teste le rendu, pas la présence de faster-whisper.
    monkeypatch.setattr(audio_transcribe, "is_available", lambda: True)
    mission_id = _mission_id()
    html = client.get(f"/missions/{mission_id}/interviews/{chemin}").text
    assert "/audio/transcribe-file" in html
    assert "/audio/transcribe-file/status" in html
    assert "pollFileImport" in html
    # Le bloc transcrit est immédiatement soumis à l'extraction en tâche de fond.
    assert "submitSegmentJob();" in html
