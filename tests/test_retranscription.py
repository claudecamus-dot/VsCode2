"""Relance de la transcription d'un entretien DÉJÀ enregistré, depuis ses
tranches audio persistées (demande utilisateur 2026-07-30, exigence (4) du TODO).

Les blobs de 60 s du direct ne survivent pas à la fermeture de l'onglet : seules
les tranches de `Interview.audio_segments` (servies par l'onglet Backup) couvrent
le cas « rejouer en consultation », d'où la piste (b) du TODO comme socle.

Whisper (`iter_transcribe_blocks`) et l'IA d'extraction (`extract_turns_from_text`)
sont monkeypatchées : ces tests couvrent le contrat (route → job multi-tranches →
statut → revue → confirmation), pas la qualité de transcription.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.main import app
from app.models import (
    AudioFileJob,
    Interview,
    InterviewSegmentJob,
    InterviewTurn,
    Mission,
)
from app.services import audio_file_jobs, audio_transcribe, interview_segment_jobs


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
    # Les tranches semées sur disque doivent partir AVEC le module : le
    # répertoire d'enregistrements de test est un chemin FIXE du dossier
    # temporaire (dérivé d'`APP_DB_PATH`, cf. conftest), pas un `tmp_path`
    # jetable — un fichier laissé ici survit à la session et apparaît comme un
    # orphelin dans `test_mission_backups`, qui liste le répertoire par préfixe
    # de mission (3 échecs constatés à la suite complète).
    for fichier in RECORDINGS_DIR.glob("*_tranche*.webm"):
        try:
            fichier.unlink()
        except OSError:
            pass


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Fixtures : un entretien libre enregistré avec 2 tranches audio sur disque
# --------------------------------------------------------------------------- #
def _entretien_avec_tranches(nb_tranches: int = 2, avec_tours: bool = True) -> dict:
    db = SessionLocal()
    try:
        mission = Mission(name="Mission retranscription")
        db.add(mission)
        db.flush()
        segments = []
        for position in range(nb_tranches):
            nom = f"{mission.id}_tranche{position}.webm"
            (RECORDINGS_DIR / nom).write_bytes(b"fake-audio-" + str(position).encode())
            segments.append({"filename": nom, "position": position})
        interview = Interview(
            mission_id=mission.id,
            mode="libre",
            interviewee_name="Alice",
            status="done",
            raw_transcript="Ancienne transcription." if avec_tours else None,
            audio_segments=segments,
            audio_backup_path=segments[-1]["filename"] if segments else None,
        )
        db.add(interview)
        db.flush()
        if avec_tours:
            db.add(
                InterviewTurn(
                    interview_id=interview.id, position=0,
                    interlocuteur="Alice", question=None, remarque="Ancien propos.",
                    section_title=None,
                )
            )
        db.commit()
        return {
            "mission_id": mission.id,
            "interview_id": interview.id,
            "fichiers": [s["filename"] for s in segments],
        }
    finally:
        db.close()


def _fake_blocks(*textes: str):
    """Blocs rendus dans l'ordre, `start_index` honoré comme le vrai
    générateur (contrat de reprise)."""
    def _iter(content: bytes, block_s: int | None = None, start_index: int = 0):
        for index, texte in enumerate(textes):
            if index < start_index:
                continue
            yield index, len(textes), texte
    return _iter


def _fake_blocks_par_fichier(par_contenu: dict[bytes, tuple[str, ...]]):
    """Blocs différents selon le fichier lu — indispensable pour vérifier
    qu'un job multi-tranches transcrit bien CHAQUE tranche."""
    def _iter(content: bytes, block_s: int | None = None, start_index: int = 0):
        textes = par_contenu[content]
        for index, texte in enumerate(textes):
            if index < start_index:
                continue
            yield index, len(textes), texte
    return _iter


def _patch_extract(monkeypatch: pytest.MonkeyPatch, spy: list | None = None):
    """Extraction IA des tours : un tour par tranche, portant son texte source
    (permet d'assert que chaque tranche a bien été extraite)."""
    def _fake(text):
        if spy is not None:
            spy.append(text)
        return {
            "turns": [
                {
                    "interlocuteur": "Alice",
                    "question": None,
                    "remarque": f"Tour de : {text[:20]}",
                    "section_title": None,
                }
            ],
            "identity": {"interviewee_name": "", "interviewee_role": "", "interviewee_entity": ""},
        }

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _fake)


def _job(interview_id: int) -> AudioFileJob | None:
    db = SessionLocal()
    try:
        return db.scalars(
            select(AudioFileJob)
            .where(AudioFileJob.interview_id == interview_id)
            .order_by(AudioFileJob.id.desc())
        ).first()
    finally:
        db.close()


def _tours(interview_id: int) -> list[InterviewTurn]:
    db = SessionLocal()
    try:
        return list(
            db.scalars(
                select(InterviewTurn)
                .where(InterviewTurn.interview_id == interview_id)
                .order_by(InterviewTurn.position)
            ).all()
        )
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Le bouton et le chemin nominal
# --------------------------------------------------------------------------- #
def test_ecran_consultation_propose_de_relancer_la_transcription(client):
    """R2 — exigence (4) : la relance doit être atteignable EN CONSULTATION,
    pas seulement pendant l'enregistrement."""
    ctx = _entretien_avec_tranches()
    html = client.get(f"/interviews/{ctx['interview_id']}").text
    assert f"/interviews/{ctx['interview_id']}/retranscrire" in html
    assert "Relancer la transcription" in html


def test_pas_de_bouton_sans_audio_persiste(client):
    """Un entretien sans aucune tranche (import .docx, entretien saisi à la
    main) n'a rien d'où repartir : ne pas proposer une action impossible."""
    ctx = _entretien_avec_tranches(nb_tranches=0)
    html = client.get(f"/interviews/{ctx['interview_id']}").text
    assert f"/interviews/{ctx['interview_id']}/retranscrire" not in html


def test_relance_transcrit_toutes_les_tranches_dans_l_ordre(client, monkeypatch):
    """Le job enchaîne les tranches persistées — la 2ᵉ tranche ne doit pas être
    oubliée (une seule colonne `filename` ne portait qu'un fichier)."""
    ctx = _entretien_avec_tranches(nb_tranches=2)
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks",
        _fake_blocks_par_fichier({
            b"fake-audio-0": ("Bloc A1.", "Bloc A2."),
            b"fake-audio-1": ("Bloc B1.",),
        }),
    )
    _patch_extract(monkeypatch)

    reponse = client.post(
        f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False
    )
    assert reponse.status_code == 303
    assert reponse.headers["location"].endswith("/retranscrire")

    job = _job(ctx["interview_id"])
    assert job is not None
    assert job.status == "done", job.error
    assert job.blocks == ["Bloc A1.", "Bloc A2.", "Bloc B1."]
    assert job.files_done == 2


def test_l_audio_de_l_entretien_n_est_JAMAIS_supprime(client, monkeypatch):
    """R1 — garde critique : `_remove_audio` supprime le fichier d'un import
    abouti. Appliquée à une retranscription, elle détruirait les
    enregistrements de l'utilisateur (`audio_segments`, servis par l'onglet
    Backup) — pas une copie de travail."""
    ctx = _entretien_avec_tranches(nb_tranches=2)
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Bloc.")
    )
    _patch_extract(monkeypatch)

    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    for nom in ctx["fichiers"]:
        assert (RECORDINGS_DIR / nom).is_file(), f"tranche supprimée : {nom}"


def test_remove_audio_refuse_de_toucher_les_tranches_d_un_entretien():
    """R1, à la source : `_remove_audio` appelé sur un job de retranscription ne
    doit RIEN supprimer, même si `filename` porte une tranche.

    Testé directement sur la fonction, et pas seulement à travers la route :
    dans le flux nominal `filename` est vide, donc le premier `return` de
    `_remove_audio` masque l'absence de garde — un futur appelant qui
    renseignerait `filename` (le champ existe et reste le chemin de l'import)
    effacerait alors l'enregistrement de l'utilisateur sans que rien n'alerte."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    tranche = ctx["fichiers"][0]
    job = AudioFileJob(
        session_token="sess-garde",
        filename=tranche,           # pire cas : le job pointe la tranche
        filenames=[tranche],        # ... mais c'est bien une retranscription
        interview_id=ctx["interview_id"],
        status="done",
    )

    audio_file_jobs._remove_audio(job)

    assert (RECORDINGS_DIR / tranche).is_file(), (
        "la tranche audio de l'entretien a été supprimée"
    )


def test_chaque_tranche_est_extraite_en_tours_de_parole(client, monkeypatch):
    """L'écran de revue attend des tours, pas seulement du texte : l'extraction
    est enchaînée tranche par tranche (InterviewSegmentJob), donc récupérable
    individuellement plutôt qu'en un seul appel sur tout l'entretien."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    # 12 blocs -> 3 tranches d'extraction de 5 blocs (BLOCS_PAR_TRANCHE_IA).
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks",
        _fake_blocks(*[f"Bloc {i}." for i in range(12)]),
    )
    spy: list[str] = []
    _patch_extract(monkeypatch, spy)

    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    assert len(spy) == 3, spy
    assert spy[0].startswith("Bloc 0.")
    assert spy[1].startswith("Bloc 5.")
    assert spy[2].startswith("Bloc 10.")


def test_statut_expose_les_deux_phases(client, monkeypatch):
    ctx = _entretien_avec_tranches(nb_tranches=2)
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Bloc un.", "Bloc deux.")
    )
    _patch_extract(monkeypatch)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    data = client.get(f"/interviews/{ctx['interview_id']}/retranscrire/statut").json()
    assert data["status"] == "done"
    assert data["blocs"] == 4  # 2 blocs par tranche, 2 tranches
    assert data["tranches_total"] == 2
    assert data["tranches_faites"] == 2
    assert data["ia_total"] >= 1
    assert data["ia_faites"] == data["ia_total"]


# --------------------------------------------------------------------------- #
# Revue avant écrasement (rien n'est écrit sans confirmation)
# --------------------------------------------------------------------------- #
def test_appliquer_n_ecrit_rien_et_montre_l_ancien_contenu(client, monkeypatch):
    """R1 — l'écran de revue ne doit RIEN écrire : c'est tout l'intérêt du
    dispositif (même parti pris que « Régénérer l'analyse »)."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Nouveau bloc.")
    )
    _patch_extract(monkeypatch)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    html = client.post(f"/interviews/{ctx['interview_id']}/retranscrire/appliquer").text
    assert "Retranscription proposée" in html
    assert "Nouveau bloc." in html
    assert "Ancienne transcription." in html  # l'ancien contenu reste visible

    db = SessionLocal()
    try:
        interview = db.get(Interview, ctx["interview_id"])
        assert interview.raw_transcript == "Ancienne transcription."
    finally:
        db.close()
    tours = _tours(ctx["interview_id"])
    assert [t.remarque for t in tours] == ["Ancien propos."]


def test_confirmer_remplace_transcription_et_tours(client, monkeypatch):
    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Nouveau bloc.")
    )
    _patch_extract(monkeypatch)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    reponse = client.post(
        f"/interviews/{ctx['interview_id']}/retranscrire/confirmer",
        data={
            "transcript": "Nouveau bloc.",
            "turn_interlocuteur": ["Bob"],
            "turn_question": [""],
            "turn_remarque": ["Nouveau propos."],
            "turn_section_title": [""],
        },
        follow_redirects=False,
    )
    assert reponse.status_code == 303

    db = SessionLocal()
    try:
        interview = db.get(Interview, ctx["interview_id"])
        assert interview.raw_transcript == "Nouveau bloc."
    finally:
        db.close()
    tours = _tours(ctx["interview_id"])
    assert [t.remarque for t in tours] == ["Nouveau propos."]
    assert [t.interlocuteur for t in tours] == ["Bob"]
    # Job et tranches d'extraction consommés : pas de résidu portant du contenu
    # d'entretien en base.
    assert _job(ctx["interview_id"]) is None


def test_confirmer_refuse_de_vider_les_tours(client, monkeypatch):
    """R1 — un formulaire arrivant sans aucune ligne ne doit pas laisser
    l'entretien sans tours (perte silencieuse de contenu)."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    reponse = client.post(
        f"/interviews/{ctx['interview_id']}/retranscrire/confirmer",
        data={"transcript": "Peu importe."},
    )
    assert reponse.status_code == 400
    assert [t.remarque for t in _tours(ctx["interview_id"])] == ["Ancien propos."]


# --------------------------------------------------------------------------- #
# Reprise et cas dégradés
# --------------------------------------------------------------------------- #
def test_relance_apres_echec_reprend_au_bloc_interrompu(client, monkeypatch):
    """R1 — le cœur de la demande : reprendre AU bloc échoué, sans re-payer les
    blocs déjà transcrits. Un job recréé de zéro les repaierait tous."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Bloc un.", "Bloc deux.")
    )
    _patch_extract(monkeypatch)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    # Échec reconstitué : 1 bloc obtenu sur 2 (ce que laisse un worker tué).
    db = SessionLocal()
    try:
        job = db.scalars(
            select(AudioFileJob).where(
                AudioFileJob.interview_id == ctx["interview_id"]
            )
        ).first()
        job_id = job.id
        job.status = "failed"
        job.error = "A child process terminated abruptly"
        job.blocks = ["Bloc un."]
        job.files_done = 0
        job.blocks_before_file = 0
        db.commit()
    finally:
        db.close()

    demandes: list[int] = []

    def _iter(content: bytes, block_s: int | None = None, start_index: int = 0):
        demandes.append(start_index)
        for index, texte in enumerate(("Bloc un.", "Bloc deux.")):
            if index < start_index:
                continue
            yield index, 2, texte

    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _iter)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    assert demandes == [1], "la reprise doit repartir du bloc 1, pas de zéro"
    job = _job(ctx["interview_id"])
    assert job.id == job_id, "le job doit être REPRIS, pas recréé"
    assert job.blocks == ["Bloc un.", "Bloc deux."]
    assert job.status == "done"


def test_relance_pendant_un_traitement_en_cours_ne_lance_pas_un_second_passage(
    client, monkeypatch
):
    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Bloc.")
    )
    _patch_extract(monkeypatch)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    db = SessionLocal()
    try:
        job = db.scalars(
            select(AudioFileJob).where(
                AudioFileJob.interview_id == ctx["interview_id"]
            )
        ).first()
        job_id, token = job.id, job.session_token
        job.status = "running"
        db.commit()
    finally:
        db.close()

    reponse = client.post(
        f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False
    )
    assert reponse.status_code == 303
    job = _job(ctx["interview_id"])
    assert job.id == job_id and job.session_token == token


def test_relance_sans_audio_ne_cree_aucun_job(client):
    ctx = _entretien_avec_tranches(nb_tranches=0)
    reponse = client.post(f"/interviews/{ctx['interview_id']}/retranscrire")
    assert reponse.status_code == 200
    assert "Aucun enregistrement audio" in reponse.text
    assert _job(ctx["interview_id"]) is None


def test_extraction_en_echec_laisse_la_transcription_exportable(client, monkeypatch):
    """R1 — leçon du 2026-07-19 : un texte transcrit (longues minutes d'audio)
    bloqué par un échec IA doit garder une issue, pas disparaître avec la page."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Bloc transcrit.")
    )

    def _echec(text):
        raise interview_segment_jobs.InterviewLibreExtractAIError("Ollama muet")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _echec)
    monkeypatch.setattr(
        "app.routers.interviews.recover_stalled_or_failed_jobs", lambda db, jobs: None
    )
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    html = client.post(f"/interviews/{ctx['interview_id']}/retranscrire/appliquer").text
    assert "Ollama muet" in html
    assert "/interviews/transcript/export-pdf" in html
    assert "Bloc transcrit." in html


def test_statut_signale_un_job_bloque(client, monkeypatch):
    """Un job resté `running` (serveur redémarré) ne doit pas faire poller
    l'écran indéfiniment."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Bloc.")
    )
    _patch_extract(monkeypatch)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    monkeypatch.setattr(
        "app.routers.interviews.is_audio_file_job_stale", lambda job: True
    )
    data = client.get(f"/interviews/{ctx['interview_id']}/retranscrire/statut").json()
    assert data["status"] == "failed"
    assert "ne répond plus" in data["error"]


def test_ecran_de_suivi_redirige_sans_job(client):
    ctx = _entretien_avec_tranches(nb_tranches=1)
    reponse = client.get(
        f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False
    )
    assert reponse.status_code == 303
    assert reponse.headers["location"].endswith(f"/interviews/{ctx['interview_id']}")


def test_import_de_fichier_supprime_toujours_son_fichier(client, monkeypatch):
    """Non-régression du chemin d'import (un seul `filename`, pas de
    `filenames`) : lui, doit continuer à libérer son fichier temporaire."""
    import io

    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Bloc unique.")
    )
    reponse = client.post(
        "/audio/transcribe-file",
        files={"file": ("entretien.weba", io.BytesIO(b"audio"), "audio/webm")},
        data={"session_token": "sess-import-retranscription"},
    )
    assert reponse.status_code == 200
    db = SessionLocal()
    try:
        job = db.get(AudioFileJob, reponse.json()["job_id"])
        assert job.status == "done"
        assert not (RECORDINGS_DIR / job.filename).exists()
    finally:
        db.close()


def test_tranches_extraction_regroupe_les_blocs(monkeypatch):
    """Découpage déterministe (la reprise de l'extraction en dépend : la tranche
    d'indice i porte toujours les mêmes blocs)."""
    blocs = [f"b{i}" for i in range(12)]
    tranches = audio_file_jobs.tranches_extraction(blocs)
    assert len(tranches) == 3
    assert tranches[0] == "b0\n\nb1\n\nb2\n\nb3\n\nb4"
    assert tranches[-1] == "b10\n\nb11"
    assert audio_file_jobs.tranches_extraction([]) == []


# --------------------------------------------------------------------------- #
# Revue adversariale du 2026-07-30 (Blind Hunter + Edge Case Hunter)
# Un test de régression par défaut corrigé (R1). Chacun échoue sur le code
# d'avant : la sémantique visée est nommée dans la docstring.
# --------------------------------------------------------------------------- #
def _entretien_tranches_sur_mesure(noms: list, ecrire: bool = True) -> dict:
    """Entretien dont `audio_segments` est posé TEL QUEL — pour les cas de bord
    que le flux normal ne produit pas (doublon d'une base ancienne, fichier
    disparu, valeur non-dict)."""
    db = SessionLocal()
    try:
        mission = Mission(name="Mission cas de bord")
        db.add(mission)
        db.flush()
        segments = []
        for position, nom in enumerate(noms):
            if ecrire and "/" not in nom and ".." not in nom:
                (RECORDINGS_DIR / nom).write_bytes(b"fake-audio-" + nom.encode())
            segments.append({"filename": nom, "position": position})
        interview = Interview(
            mission_id=mission.id, mode="libre", interviewee_name="Alice",
            status="done", audio_segments=segments,
        )
        db.add(interview)
        db.flush()
        interview_id = interview.id
        db.commit()
        return {"mission_id": mission.id, "interview_id": interview_id}
    finally:
        db.close()


def test_une_tranche_illisible_ne_condamne_pas_les_suivantes(client, monkeypatch):
    """R1 — MAJEUR. Avant : une seule tranche illisible (0 octet, `.webm`
    tronqué par un crash navigateur) faisait `return` sur le job ENTIER sans
    incrémenter `files_done` — donc chaque relance rejouait la même tranche
    fautive et les tranches SUIVANTES n'étaient JAMAIS transcrites, sans aucune
    issue dans l'UI. Le contenu doit désormais être sauvé autour du trou."""
    ctx = _entretien_avec_tranches(nb_tranches=3)

    def _iter(content: bytes, block_s=None, start_index: int = 0):
        if content == b"fake-audio-1":  # la 2e tranche est illisible
            raise audio_transcribe.TranscriptionError("Fichier audio illisible : bidon")
        texte = "Bloc A." if content == b"fake-audio-0" else "Bloc C."
        for index, t in enumerate((texte,)):
            if index < start_index:
                continue
            yield index, 1, t

    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _iter)
    _patch_extract(monkeypatch)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    job = _job(ctx["interview_id"])
    assert job.status == "done", job.error
    # La 3e tranche a bien été atteinte malgré l'échec de la 2e :
    assert job.blocks == ["Bloc A.", "Bloc C."]
    assert job.files_done == 3, "toutes les tranches doivent avoir été dépassées"
    # …et l'incident est CONSIGNÉ, pas avalé : c'est lui qui alimente
    # l'avertissement de l'écran de revue avant tout écrasement.
    assert job.error and "tranche 2" in job.error


def test_import_mono_fichier_echoue_toujours_en_bloc(client, monkeypatch):
    """Non-régression du chemin d'import : lui DOIT continuer d'abandonner le
    job (c'est ce qui porte la reprise au bloc échoué du 2026-07-29). La
    tolérance ci-dessus ne vaut que pour un job multi-tranches."""
    import io

    def _iter(content: bytes, block_s=None, start_index: int = 0):
        raise audio_transcribe.TranscriptionError("Echec de la transcription : worker")
        yield  # pragma: no cover

    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _iter)
    reponse = client.post(
        "/audio/transcribe-file",
        files={"file": ("e.weba", io.BytesIO(b"audio"), "audio/webm")},
        data={"session_token": "sess-import-echec-bloc"},
    )
    db = SessionLocal()
    try:
        job = db.get(AudioFileJob, reponse.json()["job_id"])
        assert job.status == "failed"
        assert "worker" in job.error
    finally:
        db.close()


def test_toutes_les_tranches_illisibles_le_disent(client, monkeypatch):
    """Le message doit nommer la vraie cause : « aucune parole détectée »
    enverrait chercher un problème de micro sur des fichiers illisibles."""
    ctx = _entretien_avec_tranches(nb_tranches=2)

    def _iter(content: bytes, block_s=None, start_index: int = 0):
        raise audio_transcribe.TranscriptionError("Aucun enregistrement recu.")
        yield  # pragma: no cover

    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _iter)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    job = _job(ctx["interview_id"])
    assert job.status == "failed"
    assert "Aucune tranche exploitable" in job.error


def test_une_tranche_referencee_deux_fois_n_est_transcrite_qu_une_fois(
    client, monkeypatch
):
    """R1 — MAJEUR. Deux entrées `audio_segments` peuvent pointer le MÊME
    fichier sur les entretiens enregistrés avant le suffixe aléatoire de
    `save_record_backup`. Sans déduplication, la même demi-heure était
    transcrite deux fois et le tour de table proposé — qui REMPLACE l'existant —
    portait un bloc entier en double."""
    ctx = _entretien_tranches_sur_mesure(["doublon_tranche0.webm"] * 2)
    lectures = []

    def _iter(content: bytes, block_s=None, start_index: int = 0):
        lectures.append(content)
        for index, t in enumerate(("Bloc unique.",)):
            if index < start_index:
                continue
            yield index, 1, t

    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _iter)
    _patch_extract(monkeypatch)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    assert len(lectures) == 1, "le même fichier ne doit être lu qu'une fois"
    job = _job(ctx["interview_id"])
    assert job.blocks == ["Bloc unique."]
    assert len(job.filenames) == 1


def test_audio_segments_malformes_ne_font_pas_planter(client, monkeypatch):
    """`audio_segments` vient d'un champ caché client (validé « c'est une
    liste », rien de plus) : un élément non-dict faisait lever `AttributeError`
    DANS la clé de tri, donc un 500 — le filtre `isinstance` arrivait après."""
    db = SessionLocal()
    try:
        mission = Mission(name="Mission segments casses")
        db.add(mission)
        db.flush()
        (RECORDINGS_DIR / "sain_tranche0.webm").write_bytes(b"fake-audio-sain")
        interview = Interview(
            mission_id=mission.id, mode="libre", interviewee_name="Alice",
            status="done",
            audio_segments=[
                "pas-un-dict",
                {"filename": "sain_tranche0.webm", "position": 1},
            ],
        )
        db.add(interview)
        db.flush()
        interview_id = interview.id
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Bloc.")
    )
    _patch_extract(monkeypatch)
    reponse = client.post(
        f"/interviews/{interview_id}/retranscrire", follow_redirects=False
    )
    assert reponse.status_code == 303
    assert _job(interview_id).filenames == ["sain_tranche0.webm"]


def test_nom_de_tranche_traversant_est_ecarte(client):
    """`get_record_backup`/`delete_record_backup` rejettent les separateurs et
    `..` sur ce meme champ ; le nouveau chemin de LECTURE ne le faisait pas."""
    ctx = _entretien_tranches_sur_mesure(["../secret.webm"], ecrire=False)
    reponse = client.post(f"/interviews/{ctx['interview_id']}/retranscrire")
    assert reponse.status_code == 200
    assert "Aucun enregistrement audio" in reponse.text
    assert _job(ctx["interview_id"]) is None


def test_tranche_disparue_du_disque_est_signalee(client, monkeypatch):
    """Une tranche supprimée depuis l'onglet Backup produit une transcription
    TROUÉE, qui remplacerait ensuite une transcription complète : le trou était
    escamoté en silence (seul le nombre de caractères différait)."""
    ctx = _entretien_tranches_sur_mesure(
        ["present_tranche0.webm", "absent_tranche1.webm"]
    )
    (RECORDINGS_DIR / "absent_tranche1.webm").unlink()

    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Bloc present.")
    )
    _patch_extract(monkeypatch)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    suivi = client.get(f"/interviews/{ctx['interview_id']}/retranscrire").text
    # Le template coupe la phrase sur deux lignes : on ancre sur le fragment
    # significatif, pas sur la phrase reconstituée.
    assert "1 tranche(s) audio de cet entretien sont introuvables" in suivi
    revue = client.post(f"/interviews/{ctx['interview_id']}/retranscrire/appliquer").text
    assert "Ce résultat est incomplet" in revue
    assert "introuvables sur le disque" in revue


def test_le_travail_en_cours_ne_se_declare_pas_perime(client, monkeypatch):
    """R1 — MAJEUR. `is_audio_file_job_stale` compare à `created_at`, qui n'était
    jamais rafraîchi : un traitement PLUS LONG que le seuil se déclarait « ne
    répond plus » ALORS QU'IL PROGRESSAIT, l'écran proposait « Relancer », et la
    relance lançait une seconde tâche de fond en parallèle de la première (deux
    sessions écrivant `job.blocks`)."""
    from datetime import datetime, timedelta, timezone

    ctx = _entretien_avec_tranches(nb_tranches=1)
    vieux = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)

    def _iter(content: bytes, block_s=None, start_index: int = 0):
        for index, t in enumerate(("Bloc un.", "Bloc deux.")):
            if index < start_index:
                continue
            yield index, 2, t

    monkeypatch.setattr(audio_transcribe, "iter_transcribe_blocks", _iter)
    _patch_extract(monkeypatch)

    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)
    # Job artificiellement ancien ET vidé : sans battement de coeur, il
    # resterait périmé après la relance.
    db = SessionLocal()
    try:
        job = db.scalars(
            select(AudioFileJob).where(
                AudioFileJob.interview_id == ctx["interview_id"]
            )
        ).first()
        job.created_at = vieux
        job.status = "failed"
        job.blocks = []
        job.files_done = 0
        job.blocks_before_file = 0
        db.commit()
    finally:
        db.close()

    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)
    job = _job(ctx["interview_id"])
    assert job.created_at > vieux, "le traitement doit re-dater le job en progressant"
    assert not audio_file_jobs.is_audio_file_job_stale(job)


def test_une_tranche_non_aboutie_est_annoncee_avant_tout_ecrasement(
    client, monkeypatch
):
    """R1 — MAJEUR. `merge_segment_turns` ne retient que les tranches porteuses
    d'un `turns_result` : une tranche jamais aboutie disparaissait EN SILENCE et
    la route ne signalait que le cas 100 % vide. L'utilisateur validait alors un
    tour de table AMPUTÉ qui remplaçait définitivement un tour de table complet
    (et corrigé à la main)."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe,
        "iter_transcribe_blocks",
        _fake_blocks(*[f"Bloc {i}." for i in range(10)]),  # 2 tranches d'extraction
    )

    def _fake(text):
        if "Bloc 5." in text:  # la 2e tranche échoue, systématiquement
            raise RuntimeError("Ollama indisponible")
        return {
            "turns": [{"interlocuteur": "Alice", "question": None,
                       "remarque": "Tour 1", "section_title": None}],
            "identity": {"interviewee_name": "", "interviewee_role": "",
                         "interviewee_entity": ""},
        }

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _fake)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    revue = client.post(f"/interviews/{ctx['interview_id']}/retranscrire/appliquer")
    assert revue.status_code == 200
    assert "Ce résultat est incomplet" in revue.text
    assert "1 tranche(s) sur 2" in revue.text


def test_la_recuperation_synchrone_est_plafonnee(client, monkeypatch):
    """R1 — MAJEUR. Cette récupération est SYNCHRONE, dans la requête HTTP. La
    borne « nombre de tranches » du Palier 2 supposait des tranches de 30 min ;
    ici elles font 5 min, donc un entretien d'1 h 40 en compte ~20 — Ollama
    indisponible, c'était 20 x (timeout + relance) dans un seul POST."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe,
        "iter_transcribe_blocks",
        _fake_blocks(*[f"Bloc {i}." for i in range(50)]),  # 10 tranches d'extraction
    )

    def _echoue(text):
        raise RuntimeError("Ollama indisponible")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _echoue)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    appels = []

    def _compte(text):
        appels.append(text)
        raise RuntimeError("Ollama toujours indisponible")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _compte)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire/appliquer")

    from app.routers.interviews import RECUP_TRANCHES_MAX

    assert len(appels) <= RECUP_TRANCHES_MAX, (
        f"{len(appels)} récupérations synchrones dans un seul POST — la borne "
        "doit être le plafond, pas le nombre total de tranches"
    )


def test_une_tranche_en_echec_est_rejouee_par_la_relance(client, monkeypatch):
    """R1 — la reprise sautait sur la seule PRÉSENCE de la `position` : une
    tranche restée en échec était donc définitivement irrécupérable par une
    relance (sa position existait), et ne pouvait plus être rattrapée que par la
    récupération synchrone — désormais plafonnée."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe,
        "iter_transcribe_blocks",
        _fake_blocks(*[f"Bloc {i}." for i in range(10)]),
    )

    def _echoue(text):
        raise RuntimeError("Ollama indisponible")

    monkeypatch.setattr(interview_segment_jobs, "extract_turns_from_text", _echoue)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    db = SessionLocal()
    try:
        job = db.scalars(
            select(AudioFileJob).where(
                AudioFileJob.interview_id == ctx["interview_id"]
            )
        ).first()
        token = job.session_token
        job.status = "failed"  # rend la relance possible
        db.commit()
        restants = list(
            db.scalars(
                select(InterviewSegmentJob).where(
                    InterviewSegmentJob.session_token == token
                )
            )
        )
        assert restants and all(j.turns_result is None for j in restants)
    finally:
        db.close()

    _patch_extract(monkeypatch)  # cette fois l'IA répond
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    db = SessionLocal()
    try:
        jobs = list(
            db.scalars(
                select(InterviewSegmentJob).where(
                    InterviewSegmentJob.session_token == token
                )
            )
        )
        assert jobs, "les tranches doivent avoir été rejouées"
        assert all(j.turns_result is not None for j in jobs)
        positions = [j.position for j in jobs]
        assert len(positions) == len(set(positions)), "pas de doublon de position"
    finally:
        db.close()


def test_un_resultat_abouti_non_revu_n_est_pas_detruit_par_un_nouveau_clic(
    client, monkeypatch
):
    """Le bouton détruisait un job `done` pour repartir de zéro : des heures de
    calcul jetées sans le dire. On ramène sur son écran de suivi ; « Abandonner
    ce résultat » reste la sortie explicite."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Bloc.")
    )
    _patch_extract(monkeypatch)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)
    job_id = _job(ctx["interview_id"]).id

    reponse = client.post(
        f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False
    )
    assert reponse.status_code == 303
    assert _job(ctx["interview_id"]).id == job_id, "le résultat ne doit pas être jeté"

    suivi = client.get(f"/interviews/{ctx['interview_id']}/retranscrire").text
    assert "/retranscrire/abandonner" in suivi

    client.post(
        f"/interviews/{ctx['interview_id']}/retranscrire/abandonner",
        follow_redirects=False,
    )
    assert _job(ctx["interview_id"]) is None


def test_confirmer_refuse_une_transcription_vide(client):
    """L'écriture était ASYMÉTRIQUE : les tours étaient TOUJOURS remplacés,
    `raw_transcript` seulement si le champ arrivait rempli — un champ caché vidé
    laissait un entretien dont la transcription ne correspond plus à ses tours."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    avant = [t.remarque for t in _tours(ctx["interview_id"])]

    reponse = client.post(
        f"/interviews/{ctx['interview_id']}/retranscrire/confirmer",
        data={
            "transcript": "   ",
            "turn_interlocuteur": ["Alice"],
            "turn_question": [""],
            "turn_remarque": ["Nouveau propos."],
            "turn_section_title": [""],
        },
    )
    assert reponse.status_code == 400
    assert [t.remarque for t in _tours(ctx["interview_id"])] == avant


def test_la_purge_emporte_les_tranches_d_extraction(client, monkeypatch):
    """Les tranches d'extraction portent ~5 min de propos d'entretien et ne sont
    rattachées au job que par `session_token` (aucune FK) : la purge effaçait le
    job et les laissait en base — exactement ce qu'elle existe pour éviter."""
    from datetime import datetime, timedelta, timezone

    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Bloc.")
    )
    _patch_extract(monkeypatch)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)
    token = _job(ctx["interview_id"]).session_token

    db = SessionLocal()
    try:
        job = db.scalars(
            select(AudioFileJob).where(AudioFileJob.session_token == token)
        ).first()
        job.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=30
        )
        db.commit()
        assert db.scalars(
            select(InterviewSegmentJob).where(
                InterviewSegmentJob.session_token == token
            )
        ).all()
        audio_file_jobs.purge_stale_audio_file_jobs(db)
        assert not db.scalars(
            select(InterviewSegmentJob).where(
                InterviewSegmentJob.session_token == token
            )
        ).all()
    finally:
        db.close()


def test_statut_annonce_le_vrai_total_de_tranches(client, monkeypatch):
    """`_extraire_tours` crée les tranches UNE PAR UNE juste avant de les
    exécuter : `ia_total` valait donc toujours « ce qui est fait », et la barre
    affichait 100 % avec des tranches encore à traiter."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe,
        "iter_transcribe_blocks",
        _fake_blocks(*[f"Bloc {i}." for i in range(10)]),  # 2 tranches
    )

    vues = []
    vrai_run = audio_file_jobs.run_segment_job

    def _run(job_id):
        vrai_run(job_id)
        vues.append(
            client.get(f"/interviews/{ctx['interview_id']}/retranscrire/statut").json()
        )

    _patch_extract(monkeypatch)
    monkeypatch.setattr(audio_file_jobs, "run_segment_job", _run)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)

    assert vues, "le statut doit être interrogeable pendant l'extraction"
    assert vues[0]["ia_total"] == 2, (
        f"total annoncé {vues[0]['ia_total']} après la 1re tranche sur 2 — "
        "la barre atteindrait 100 % à mi-parcours"
    )
    assert vues[0]["ia_faites"] == 1


def test_le_statut_404_arrete_le_poll(client):
    """404 (job confirmé/abandonné depuis un autre onglet) doit être TERMINAL :
    le `.catch` le traitait comme un aléa réseau et repollait indéfiniment."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    assert (
        client.get(f"/interviews/{ctx['interview_id']}/retranscrire/statut").status_code
        == 404
    )
    from app.templating import templates

    source = templates.env.loader.get_source(
        templates.env, "interviews/libre_retranscription.html"
    )[0]
    assert "res.status === 404" in source
    assert "window.location.href" in source


# --------------------------------------------------------------------------- #
# Constats de revue adversariale restés non traités par la première passe
# (2026-07-30) — corrigés et couverts ici.
# --------------------------------------------------------------------------- #
def test_un_job_orphelin_ne_sapplique_jamais_a_un_autre_entretien(client, monkeypatch):
    """R1 — constat #21. `audio_file_jobs.interview_id` est ajoutée par migration
    ADDITIVE : sur une base existante elle n'a pas de clause `REFERENCES`, donc
    aucun `ON DELETE CASCADE` n'emporte le job quand l'entretien est supprimé.
    Or SQLite RECYCLE l'identifiant libéré — le prochain entretien créé héritait
    du job orphelin comme « job courant », et son écran de revue proposait de
    remplacer son contenu par la transcription d'un AUTRE entretien.

    Reproduit ici sans dépendre du recyclage d'id : un job dont les `filenames`
    n'appartiennent pas aux tranches de l'entretien visé est exactement l'état
    que produit ce recyclage. Avant le correctif, les trois routes de
    consultation servaient ce job ; `retranscrire_start` était le seul protégé."""
    victime = _entretien_avec_tranches(nb_tranches=1)
    etranger = _entretien_avec_tranches(nb_tranches=1)
    db = SessionLocal()
    try:
        # Job portant l'audio de l'entretien ÉTRANGER, mais rattaché à la victime.
        db.add(
            AudioFileJob(
                session_token="orphelin",
                filename="",
                filenames=etranger["fichiers"],
                interview_id=victime["interview_id"],
                status="done",
                block_seconds=60,
                blocks=["Propos venus dun tout autre entretien."],
            )
        )
        db.commit()
    finally:
        db.close()

    iid = victime["interview_id"]
    suivi = client.get(f"/interviews/{iid}/retranscrire", follow_redirects=False)
    assert suivi.status_code == 303 and suivi.headers["location"] == f"/interviews/{iid}"
    assert client.get(f"/interviews/{iid}/retranscrire/statut").status_code == 404
    revue = client.post(f"/interviews/{iid}/retranscrire/appliquer", follow_redirects=False)
    assert "Propos d'un tout autre entretien." not in revue.text

    # …et l'entretien reste intact (rien n'a pu être écrit depuis ce job).
    db = SessionLocal()
    try:
        interview = db.get(Interview, iid)
        assert interview.raw_transcript == "Ancienne transcription."
    finally:
        db.close()


def test_le_pdf_de_secours_de_la_revue_porte_le_nom_de_l_interlocuteur(
    client, monkeypatch
):
    """R1 — constat #25. Le bouton « Télécharger (PDF) » de l'écran de revue
    poste vers `/interviews/turns/export-pdf`, qui lit `interviewee_name` en
    `Form` : le champ manquant, le PDF de secours sortait ANONYME (titre et nom
    de fichier « brute.pdf ») alors que l'entretien est identifié."""
    ctx = _entretien_avec_tranches(nb_tranches=1)
    monkeypatch.setattr(
        audio_transcribe, "iter_transcribe_blocks", _fake_blocks("Un propos.")
    )
    _patch_extract(monkeypatch)
    client.post(f"/interviews/{ctx['interview_id']}/retranscrire", follow_redirects=False)
    revue = client.post(
        f"/interviews/{ctx['interview_id']}/retranscrire/appliquer",
        follow_redirects=False,
    )
    assert 'name="interviewee_name"' in revue.text
    assert 'value="Alice"' in revue.text


def test_la_relance_est_protegee_du_double_clic(client, monkeypatch):
    """R1 — constat #22. Deux clics rapides sur « Relancer la retranscription »
    postaient deux fois : le second passait avant que le premier n'ait remis le
    job en `pending`, donc la garde « déjà en cours » de `retranscrire_start` ne
    le voyait pas et deux tâches de fond écrivaient les mêmes `job.blocks`.
    `busy.js` gèle le formulaire dès la première soumission."""
    from app.templating import templates

    source = templates.env.loader.get_source(
        templates.env, "interviews/libre_retranscription.html"
    )[0]
    assert 'id="retr-relancer-form" data-busy-label=' in source
