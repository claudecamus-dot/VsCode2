"""Story 3.6 — l'entretien libre ne cache aucune lacune à l'écran.

AC3.6.1 : la route historique `record-libre` persiste la transcription incomplète.
AC3.6.2 : revue de retranscription + finalisation de mission disent `parties_supprimees`.
AC3.6.3 : la confirmation de suppression n'annonce « INCOMPLET » que pour un fichier rattaché.
"""
from __future__ import annotations

import html as _html
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import AudioFileJob, Interview, Mission
from app.services.interview_export import texte_parties_supprimees


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


def _mission(**kw) -> int:
    with SessionLocal() as db:
        m = Mission(name="S36", **kw)
        db.add(m)
        db.commit()
        return m.id


# --------------------------------------------------------------------------- #
# AC3.6.1
# --------------------------------------------------------------------------- #
def test_ac361_record_libre_historique_persiste_les_tranches_manquantes(client, monkeypatch):
    from app.routers import interviews_record

    monkeypatch.setattr(interviews_record, "_extraire_tours_libre", lambda *a, **k: {
        "turns": [{"interlocuteur": "A", "question": "Bonjour", "remarque": None,
                   "section_title": None}],
        "identity": {}, "tranches_manquantes": 2})
    mid = _mission()
    r = client.post(f"/missions/{mid}/interviews/record-libre",
                    data={"transcript": "Texte S36 un.", "session_token": "tok-s36-361"})
    assert r.status_code == 200
    page = _html.unescape(r.text)
    assert "2 tranches n'ont pas pu être structurées" in page  # signal de la revue
    # le formulaire de la revue porte le compteur jusqu'à « Enregistrer sans la synthèse »
    champs = dict(re.findall(r'name="(tranches_manquantes)" value="(\d+)"', page))
    assert champs == {"tranches_manquantes": "2"}, page[:400]

    conf = client.post(f"/missions/{mid}/interviews/record-libre/confirm", data={
        "transcript": "Texte S36 un.", "session_token": "tok-s36-361",
        "tranches_manquantes": champs["tranches_manquantes"],
        "turn_interlocuteur": "A", "turn_question": "Bonjour",
        "turn_remarque": "", "turn_section_title": ""}, follow_redirects=False)
    assert conf.status_code == 303
    with SessionLocal() as db:
        iv = db.scalars(select(Interview).where(Interview.mission_id == mid)).one()
        assert iv.tranches_manquantes == 2
        iid = iv.id
    fiche = client.get(f"/interviews/{iid}")
    assert "2 tranches" in _html.unescape(fiche.text)


def test_ac361_compteur_invalide_ne_leve_pas_et_reste_a_zero(client):
    mid = _mission()
    r = client.post(f"/missions/{mid}/interviews/record-libre/confirm", data={
        "transcript": "T", "tranches_manquantes": "abc",
        "turn_interlocuteur": "A", "turn_question": "Q",
        "turn_remarque": "", "turn_section_title": ""}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        iv = db.scalars(select(Interview).where(Interview.mission_id == mid)).one()
        assert iv.tranches_manquantes == 0


def test_ac361_le_compteur_traverse_la_synthese(client, monkeypatch):
    from app.routers import interviews_libre

    monkeypatch.setattr(interviews_libre, "generate_repartition_from_turns",
                        lambda turns, axes: {"repartition": {}, "resume": "r"})
    mid = _mission()
    r = client.post(f"/missions/{mid}/interviews/record-libre/synthese", data={
        "transcript": "T", "tranches_manquantes": "3", "turn_interlocuteur": "A",
        "turn_question": "Q", "turn_remarque": "", "turn_section_title": ""})
    assert r.status_code == 200
    assert 'name="tranches_manquantes" value="3"' in r.text


# --------------------------------------------------------------------------- #
# AC3.6.2
# --------------------------------------------------------------------------- #
def test_ac362_finaliser_dit_les_parties_supprimees(client):
    mid = _mission(is_draft=True)
    with SessionLocal() as db:
        iv = Interview(mission_id=mid, mode="libre", status="done",
                       interviewee_name="Zoé", parties_supprimees=2)
        db.add(iv)
        db.commit()
        attendu = texte_parties_supprimees(iv)
    assert attendu
    r = client.get(f"/missions/{mid}/finaliser")
    assert r.status_code == 200
    assert attendu in _html.unescape(r.text)


def test_ac362_finaliser_sans_partie_supprimee_ne_dit_rien(client):
    mid = _mission(is_draft=True)
    with SessionLocal() as db:
        db.add(Interview(mission_id=mid, mode="libre", status="done", interviewee_name="Zoé"))
        db.commit()
    r = client.get(f"/missions/{mid}/finaliser")
    assert "data-parties-supprimees-texte" not in r.text


@pytest.mark.parametrize("supprimees", [0, 1])
def test_ac362_revue_de_retranscription(client, monkeypatch, supprimees):
    from app.routers import interviews_retranscription as ir

    nom = "x_s36.webm"
    mid = _mission()
    with SessionLocal() as db:
        iv = Interview(mission_id=mid, mode="libre", status="done", interviewee_name="Léa",
                       audio_segments=[{"filename": nom, "position": 0}],
                       parties_supprimees=supprimees)
        db.add(iv)
        db.flush()
        db.add(AudioFileJob(session_token="tok-s36-ret", status="done", filenames=[nom],
                            interview_id=iv.id, blocks=["bloc"], files_done=1))
        db.commit()
        iid = iv.id
        attendu = texte_parties_supprimees(iv)
    monkeypatch.setattr(ir, "merge_segment_turns", lambda jobs, x: {
        "turns": [{"interlocuteur": "A", "question": "Q", "remarque": None,
                   "section_title": None}]})
    r = client.post(f"/interviews/{iid}/retranscrire/appliquer")
    assert r.status_code == 200, r.text[:300]
    assert "Retranscription proposée" in r.text
    if supprimees:
        assert attendu in _html.unescape(r.text)
    else:
        assert "data-parties-supprimees-texte" not in r.text


# --------------------------------------------------------------------------- #
# AC3.6.3 (déjà vrai : la mise en garde n'est que dans la boucle des rattachés)
# --------------------------------------------------------------------------- #
def test_ac363_confirmation_incomplet_seulement_pour_un_fichier_rattache(
    client, monkeypatch, tmp_path: Path,
):
    from app.routers import missions

    monkeypatch.setattr(missions, "RECORDINGS_DIR", tmp_path)
    mid = _mission()
    rattache, orphelin = f"{mid}_s36_att.webm", f"{mid}_s36_orph.webm"
    for n in (rattache, orphelin):
        (tmp_path / n).write_bytes(b"a")
    with SessionLocal() as db:
        db.add(Interview(mission_id=mid, mode="libre", status="done", interviewee_name="Max",
                         audio_segments=[{"filename": rattache, "position": 0}]))
        db.commit()
    page = _html.unescape(client.get(f"/missions/{mid}").text)
    forms = re.findall(r'action="[^"]*/backup/([^"/]+)/delete"[^>]*?onsubmit="([^"]*)"',
                       page, flags=re.S)
    par_fichier = dict(forms)
    assert set(par_fichier) == {rattache, orphelin}, list(par_fichier)
    assert "INCOMPLET" in par_fichier[rattache]
    assert "INCOMPLET" not in par_fichier[orphelin]
