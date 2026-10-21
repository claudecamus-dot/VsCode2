"""SP-1 : la fiche entretien montre le bilan audio (complétude + blocs relancés)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import AudioFileJob, Interview, Mission


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


_COMPLET = {"statut": "complet", "blocs_rejoues": [], "blocs_vides": []}


def _fiche(*, completude, lier=True, status="done", backup="x.webm", job=True, apres=()) -> int:
    """`apres` : jobs plus récents (statut, completude) rattachés à la fiche."""
    db = SessionLocal()
    try:
        m = Mission(name="M bilan", is_draft=False)
        db.add(m)
        db.flush()
        iv = Interview(mission_id=m.id, interviewee_name="T", mode="libre", status="done",
                       raw_transcript="Texte.", audio_backup_path=backup)
        db.add(iv)
        db.flush()
        if job:
            db.add(AudioFileJob(session_token="t", filename=backup, status=status,
                                blocks=[], completude=completude,
                                interview_id=iv.id if lier else None))
        for st, comp in apres:
            db.add(AudioFileJob(session_token="t", filename=backup, status=st, blocks=[],
                                completude=comp, interview_id=iv.id))
        db.commit()
        return iv.id
    finally:
        db.close()


def _get(client: TestClient, iid: int) -> str:
    r = client.get(f"/interviews/{iid}")
    assert r.status_code == 200
    return r.text


def test_fiche_montre_les_blocs_relances(client: TestClient) -> None:
    html = _get(client, _fiche(completude={"statut": "complet", "blocs_rejoues": [1, 3],
                                           "blocs_vides": []}))
    assert 'id="bilan-audio"' in html
    assert "Transcription complète." in html
    assert "2 blocs audio relancés automatiquement, puis réussis." in html


def test_fiche_incomplete_dit_les_blocs_sans_texte(client: TestClient) -> None:
    html = _get(client, _fiche(completude={"statut": "incomplet", "blocs_vides": [0, 4],
                                           "blocs_rejoues": []}))
    assert "Transcription incomplète : 2 blocs sans texte." in html
    assert "relancé" not in html.split('id="bilan-audio"')[1].split("</p>")[0]


def test_import_classique_non_rattache_est_retrouve_par_le_fichier(client: TestClient) -> None:
    html = _get(client, _fiche(completude={"statut": "complet", "blocs_rejoues": [2]},
                               lier=False, backup="imp-classique.webm"))
    assert "1 bloc audio relancé automatiquement, puis réussi." in html


@pytest.mark.parametrize("kw", [
    {"completude": None},
    {"job": False, "completude": None},
    {"completude": {"statut": "non_evaluable"}},
    {"completude": "pas un dict"},
], ids=["null", "sans_job", "non_evaluable", "type_inattendu"])
def test_sans_bilan_exploitable_pas_de_bloc_et_200(client: TestClient, kw: dict) -> None:
    assert 'id="bilan-audio"' not in _get(client, _fiche(**kw))


def _bloc(html: str) -> str:
    return html.split('id="bilan-audio"')[1].split("</p>")[0] if 'id="bilan-audio"' in html else ""


@pytest.mark.parametrize("statut,mot", [("failed", "échouée"), ("running", "en cours"),
                                        ("pending", "en cours")])
def test_un_dernier_job_non_termine_n_est_pas_masque_par_un_ancien_reussi(
    client: TestClient, statut: str, mot: str
) -> None:
    html = _get(client, _fiche(completude=_COMPLET, apres=[(statut, None)]))
    bloc = _bloc(html)
    assert mot in bloc and "Transcription complète" not in html


def test_dernier_job_reussi_apres_un_echec_montre_son_bilan(client: TestClient) -> None:
    iid = _fiche(completude=None, status="failed",
                 apres=[("done", {"statut": "complet", "blocs_rejoues": [1]})])
    bloc = _bloc(_get(client, iid))
    assert "Transcription complète." in bloc and "1 bloc audio relancé" in bloc


def test_le_repli_par_fichier_ignore_les_jobs_d_un_autre_entretien(client: TestClient) -> None:
    autre = _fiche(completude={"statut": "complet", "blocs_rejoues": [1]}, backup="partage.webm")
    assert autre
    # Fiche sans job propre, même fichier : le job RATTACHÉ à `autre` ne doit pas fuir.
    iid = _fiche(completude=None, job=False, backup="partage.webm")
    assert 'id="bilan-audio"' not in _get(client, iid)


def test_incomplet_et_echec_portent_la_classe_d_alerte(client: TestClient) -> None:
    html = _get(client, _fiche(completude={"statut": "incomplet", "blocs_vides": [0]}))
    assert 'class="ai-error" id="bilan-audio"' in html
    html = _get(client, _fiche(completude=_COMPLET, apres=[("failed", None)]))
    assert 'class="ai-error" id="bilan-audio"' in html
    html = _get(client, _fiche(completude={"statut": "complet", "blocs_rejoues": [1]}))
    assert 'class="muted" id="bilan-audio"' in html
