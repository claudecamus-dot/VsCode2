"""SH-1 (routes) : GET/POST-delete de `/record/backup/{filename}` refusent `:`
(flux NTFS, lecteur Windows), y compris pour un nom present comme REFERENCE en
base, que `appartient_a_mission` laisserait passer."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview, Mission


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


def _mission(segments: list[str]) -> int:
    with SessionLocal() as db:
        m = Mission(name="SH1 routes")
        db.add(m)
        db.flush()
        db.add(Interview(
            mission_id=m.id, interviewee_name="T", mode="libre",
            audio_segments=[{"filename": f, "position": i} for i, f in enumerate(segments)],
            audio_backup_path=segments[-1] if segments else None,
        ))
        db.commit()
        return m.id


def _base(mid: int, nom: str) -> str:
    return f"/missions/{mid}/interviews/record/backup/{nom}"


def test_prefixe_de_mission_flux_ntfs_refuse_et_fichier_intact(client):
    mid = _mission([])
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    reel = RECORDINGS_DIR / f"{mid}_x.webm"
    reel.write_bytes(b"x")
    nom = f"{mid}_x.webm:flux"
    assert client.get(_base(mid, nom)).status_code == 400
    assert client.post(_base(mid, nom) + "/delete", follow_redirects=False).status_code == 400
    assert reel.exists()
    reel.unlink()


@pytest.mark.parametrize("nom", ["D:x.webm", "C:evil.webm"])
def test_reference_polluee_avec_lecteur_refusee_et_reference_conservee(client, nom):
    mid = _mission([nom])
    assert client.get(_base(mid, nom)).status_code == 400
    assert client.post(_base(mid, nom) + "/delete", follow_redirects=False).status_code == 400
    with SessionLocal() as db:
        itw = db.query(Interview).filter_by(mission_id=mid).one()
        assert [s["filename"] for s in itw.audio_segments] == [nom]


def test_nom_legitime_get_et_delete_fonctionnent(client):
    mid = _mission([])
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    f = RECORDINGS_DIR / f"{mid}_a.webm"
    f.write_bytes(b"audio")
    assert client.get(_base(mid, f.name)).status_code == 200
    r = client.post(_base(mid, f.name) + "/delete", follow_redirects=False)
    assert r.status_code == 303
    assert not f.exists()


@pytest.mark.parametrize("nom", ["a..b.webm", "..x", "a%5Cb.webm"])
def test_rejets_existants_tiennent(client, nom):
    mid = _mission([])
    assert client.get(_base(mid, nom)).status_code == 400
    assert client.post(_base(mid, nom) + "/delete", follow_redirects=False).status_code == 400


def test_delete_fichier_verrouille_409_fichier_et_reference_restent(client, monkeypatch):
    """Trou n°8 : `unlink` lève PermissionError (fichier ouvert sous Windows) ->
    409, le fichier ET sa référence en base restent (aucune référence orpheline,
    aucune suppression à moitié)."""
    from pathlib import Path

    mid = _mission([])
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    f = RECORDINGS_DIR / f"{mid}_lock.webm"
    f.write_bytes(b"audio")
    with SessionLocal() as db:
        itw = db.query(Interview).filter_by(mission_id=mid).one()
        itw.audio_segments = [{"filename": f.name, "position": 0}]
        itw.audio_backup_path = f.name
        db.commit()
    vrai_unlink = Path.unlink

    def unlink_verrouille(self, *a, **kw):
        if self.name == f.name:
            raise PermissionError(13, "verrouillé")
        return vrai_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", unlink_verrouille)
    try:
        r = client.post(_base(mid, f.name) + "/delete", follow_redirects=False)
        assert r.status_code == 409
        assert f.exists()
        with SessionLocal() as db:
            itw = db.query(Interview).filter_by(mission_id=mid).one()
            assert [s["filename"] for s in itw.audio_segments] == [f.name]
            assert itw.audio_backup_path == f.name
    finally:
        monkeypatch.undo()
        f.unlink(missing_ok=True)
