"""Story 3.5 — une partie d'entretien supprimée reste visible comme manquante.

Colonne dédiée `Interview.parties_supprimees` (arbitrage utilisateur du
2026-11-17) : ni marque dans `audio_segments`, ni réemploi de
`tranches_manquantes` (transcription incomplète, remis à 0 par une
retranscription complète). Un test par critère et par chemin touché.
"""
from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import app.db as db_module
from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import AudioFileJob, Interview, InterviewTurn, Mission
from app.services.interview_pdf_export import build_interview_pdf
from app.services.pptx_export import build_presentation


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


@pytest.fixture
def recordings(monkeypatch, tmp_path: Path) -> Path:
    from app.routers import interviews_backup, interviews_retranscription

    monkeypatch.setattr(interviews_backup, "RECORDINGS_DIR", tmp_path)
    monkeypatch.setattr(interviews_retranscription, "RECORDINGS_DIR", tmp_path)
    return tmp_path


def _mission() -> int:
    with SessionLocal() as db:
        m = Mission(name="S35")
        db.add(m)
        db.commit()
        return m.id


def _entretien(mission_id: int, recordings: Path, nb: int = 3, **kw) -> tuple[int, list[str]]:
    noms = [f"{mission_id}_s35_tranche{i}.webm" for i in range(nb)]
    for nom in noms:
        (recordings / nom).write_bytes(b"audio")
    with SessionLocal() as db:
        iv = Interview(
            mission_id=mission_id, mode="libre", status="done",
            interviewee_name="Alice", raw_transcript="Ancien texte.",
            structuration_status="fait",
            audio_segments=[{"filename": n, "position": i} for i, n in enumerate(noms)],
            audio_backup_path=noms[-1], **kw,
        )
        db.add(iv)
        db.flush()
        db.add(InterviewTurn(interview_id=iv.id, position=0, interlocuteur="Alice",
                             question=None, remarque="Ancien propos.", section_title=None))
        db.commit()
        return iv.id, noms


def _supprimer(client: TestClient, mission_id: int, nom: str) -> None:
    r = client.post(f"/missions/{mission_id}/interviews/record/backup/{nom}/delete",
                    follow_redirects=False)
    assert r.status_code == 303, r.text


def _compte(interview_id: int) -> int:
    with SessionLocal() as db:
        return db.get(Interview, interview_id).parties_supprimees


# --------------------------------------------------------------------------- #
# AC3.5.1 — la suppression compte, première, milieu ou DERNIÈRE partie
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rang", [0, 1, 2], ids=["premiere", "milieu", "derniere"])
def test_supprimer_une_partie_incremente_le_compte(client, recordings, rang) -> None:
    mission_id = _mission()
    iid, noms = _entretien(mission_id, recordings)
    assert _compte(iid) == 0
    _supprimer(client, mission_id, noms[rang])
    assert _compte(iid) == 1
    with SessionLocal() as db:
        iv = db.get(Interview, iid)
        assert iv.tranches_manquantes == 0  # jamais confondu avec la transcription


def test_supprimer_la_derniere_partie_est_compte_sans_trou_de_positions(
    client, recordings,
) -> None:
    """Le cas que rien ne détectait : 0,1,2 -> 0,1, aucun trou."""
    mission_id = _mission()
    iid, noms = _entretien(mission_id, recordings)
    _supprimer(client, mission_id, noms[-1])
    with SessionLocal() as db:
        iv = db.get(Interview, iid)
        assert [s["position"] for s in iv.audio_segments] == [0, 1]
        assert iv.parties_supprimees == 1
    _supprimer(client, mission_id, noms[0])
    assert _compte(iid) == 2


def test_supprimer_l_audio_unique_d_un_entretien_sans_parties_est_compte(
    client, recordings,
) -> None:
    mission_id = _mission()
    nom = f"{mission_id}_s35_unique.webm"
    (recordings / nom).write_bytes(b"audio")
    with SessionLocal() as db:
        iv = Interview(mission_id=mission_id, mode="libre", status="done",
                       interviewee_name="Bob", audio_backup_path=nom)
        db.add(iv)
        db.commit()
        iid = iv.id
    _supprimer(client, mission_id, nom)
    assert _compte(iid) == 1


def test_la_fiche_libre_affiche_n_parties_supprimees(client, recordings) -> None:
    mission_id = _mission()
    iid, noms = _entretien(mission_id, recordings)
    assert "data-parties-supprimees" not in client.get(f"/interviews/{iid}").text
    _supprimer(client, mission_id, noms[-1])
    page = client.get(f"/interviews/{iid}").text
    assert "1 partie supprimée" in page
    assert 'data-parties-supprimees="1"' in page
    _supprimer(client, mission_id, noms[0])
    assert "2 parties supprimées" in client.get(f"/interviews/{iid}").text


def test_la_fiche_structuree_affiche_n_parties_supprimees(client) -> None:
    mission_id = _mission()
    with SessionLocal() as db:
        iv = Interview(mission_id=mission_id, interviewee_name="Chris", status="done",
                       parties_supprimees=3)
        db.add(iv)
        db.commit()
        iid = iv.id
    page = client.get(f"/interviews/{iid}").text
    assert "3 parties supprimées" in page


# --------------------------------------------------------------------------- #
# AC3.5.3 — une retranscription complète ne remet PAS le compte à zéro
# --------------------------------------------------------------------------- #
def test_une_retranscription_complete_ne_remet_pas_le_compte_a_zero(
    client, recordings,
) -> None:
    mission_id = _mission()
    iid, noms = _entretien(mission_id, recordings, tranches_manquantes=1)
    _supprimer(client, mission_id, noms[-1])
    restants = noms[:-1]
    with SessionLocal() as db:
        db.add(AudioFileJob(session_token="s35-retr", filename="", filenames=restants,
                            interview_id=iid, status="done", error=None,
                            block_seconds=60, blocks=[]))
        db.commit()
    r = client.post(
        f"/interviews/{iid}/retranscrire/confirmer",
        data={"transcript": "Nouvelle transcription.", "turn_interlocuteur": ["Bob"],
              "turn_question": [""], "turn_remarque": ["Propos."],
              "turn_section_title": [""]},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    with SessionLocal() as db:
        iv = db.get(Interview, iid)
        # Témoin : la retranscription est bien jugée COMPLÈTE (tranches remises à 0)…
        assert iv.tranches_manquantes == 0
        # … et l'audio supprimé reste perdu.
        assert iv.parties_supprimees == 1


# --------------------------------------------------------------------------- #
# AC3.5.4 — complétude de la fiche, PDF d'entretien, deck
# --------------------------------------------------------------------------- #
def test_le_bilan_complet_de_la_fiche_ne_dit_pas_complet_si_une_partie_manque(
    client, recordings,
) -> None:
    mission_id = _mission()
    iid, noms = _entretien(mission_id, recordings)
    with SessionLocal() as db:
        db.add(AudioFileJob(session_token="s35-bilan", filename="", filenames=noms,
                            interview_id=iid, status="done", block_seconds=60, blocks=[],
                            completude={"statut": "complet"}))
        db.commit()
    assert "Transcription complète." in client.get(f"/interviews/{iid}").text
    _supprimer(client, mission_id, noms[-1])
    page = client.get(f"/interviews/{iid}").text
    assert "Transcription complète." not in page
    assert "Transcription complète des parties restantes seulement" in page


def _texte_pdf(pdf: bytes) -> str:
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        return " ".join(" ".join(p.get_text().split()) for p in doc)
    finally:
        doc.close()


def test_le_pdf_d_entretien_signale_l_entretien_incomplet() -> None:
    mission_id = _mission()
    with SessionLocal() as db:
        iv = Interview(mission_id=mission_id, mode="libre", status="done",
                       interviewee_name="Dana", resume="Un résumé.")
        db.add(iv)
        db.commit()
        assert "Entretien incomplet" not in _texte_pdf(build_interview_pdf(iv))
        iv.parties_supprimees = 2
        db.commit()
        texte = _texte_pdf(build_interview_pdf(iv))
    assert "Entretien incomplet" in texte
    assert "2 parties audio supprimées" in texte


def _texte_base_analyse(mission_id: int) -> str:
    with SessionLocal() as db:
        prs = build_presentation(db.get(Mission, mission_id))
    for s in prs.slides:
        t = s.shapes.title
        if t is not None and t.has_text_frame and t.text_frame.text.startswith(
            "Base de l'analyse"
        ):
            return "\n".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame)
    raise AssertionError("slide « Base de l'analyse » absente")


def test_le_deck_signale_les_entretiens_incomplets() -> None:
    mission_id = _mission()
    with SessionLocal() as db:
        db.add(Interview(mission_id=mission_id, mode="libre", status="done",
                         interviewee_name="Eli", repartition={"contexte": "Vu."}))
        amp = Interview(mission_id=mission_id, mode="libre", status="done",
                        interviewee_name="Fay", repartition={"contexte": "Vu."})
        db.add(amp)
        db.commit()
        amp_id = amp.id
    assert "incomplet" not in _texte_base_analyse(mission_id)
    with SessionLocal() as db:
        db.get(Interview, amp_id).parties_supprimees = 1
        db.commit()
    assert "1 entretien incomplet : partie audio supprimée" in _texte_base_analyse(mission_id)


# --------------------------------------------------------------------------- #
# AC3.5.2 — migration : base à l'ancien schéma refusée, migrée sur opt-in
# --------------------------------------------------------------------------- #
@pytest.fixture()
def base_par_defaut(tmp_path: Path, monkeypatch):
    """La base jetable EST la base par défaut aux yeux de `app.db` (même montage
    que `test_audio_completude_migration.py`) : la vraie n'est jamais ouverte."""
    chemin = tmp_path / "app.db"
    eng = create_engine(f"sqlite:///{chemin}", echo=False)
    monkeypatch.setattr(db_module, "engine", eng)
    monkeypatch.setattr(db_module, "DB_PATH", chemin)
    monkeypatch.setattr(db_module, "_CHEMIN_PAR_DEFAUT", chemin, raising=False)
    monkeypatch.delenv("APP_DB_MIGRATE", raising=False)
    yield eng
    eng.dispose()


def _ancien_schema_avec_un_entretien(eng) -> None:
    db_module.Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE interviews DROP COLUMN parties_supprimees")
        conn.exec_driver_sql(
            "INSERT INTO missions (name, created_at, is_draft, is_demo, "
            "restitution_verbatim_ids) VALUES ('Ancienne', '2026-01-01 00:00:00', 0, 0, '[]')"
        )
        conn.exec_driver_sql(
            "INSERT INTO interviews (mission_id, interviewee_name, status, mode, "
            "repartition, audio_segments, tranches_manquantes, created_at) VALUES "
            "(1, 'Ancien', 'done', 'libre', '{}', '[]', 2, '2026-01-01 00:00:00')"
        )


def _colonnes(eng) -> set[str]:
    with eng.connect() as conn:
        return {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(interviews)")}


def test_sans_opt_in_une_base_sans_la_colonne_est_refusee(base_par_defaut) -> None:
    _ancien_schema_avec_un_entretien(base_par_defaut)
    with pytest.raises(db_module.SchemaEnRetard) as exc:
        db_module.init_db()
    assert "interviews.parties_supprimees" in str(exc.value)
    assert "parties_supprimees" not in _colonnes(base_par_defaut)


def test_la_migration_explicite_ajoute_la_colonne_a_zero_sans_toucher_l_existant(
    base_par_defaut, monkeypatch,
) -> None:
    _ancien_schema_avec_un_entretien(base_par_defaut)
    monkeypatch.setenv("APP_DB_MIGRATE", "1")
    db_module.init_db()
    assert "parties_supprimees" in _colonnes(base_par_defaut)
    with base_par_defaut.connect() as conn:
        ligne = conn.exec_driver_sql(
            "SELECT interviewee_name, tranches_manquantes, parties_supprimees FROM interviews"
        ).one()
    assert tuple(ligne) == ("Ancien", 2, 0)
