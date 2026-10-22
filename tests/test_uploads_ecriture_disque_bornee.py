"""Les écritures audio sur DISQUE sont bornées, et ne laissent pas de partiel.

Dernière jambe du constat sécurité du 2026-09-04, arbitrée « traiter » le
2026-09-10. Les chemins qui lisent l'audio en MÉMOIRE ont un plafond depuis le
2026-09-09 (`lire_upload_audio_borne`) ; les deux qui l'écrivent en streaming
sur disque — `transcribe_file` et `save_record_backup` — n'en avaient aucun.
Ces routes ne sont pas authentifiées : l'atténuation tenait au binding
127.0.0.1 et à la garde CSRF, pas à une borne, et un envoi de plusieurs Go
remplissait le disque.

Ces tests échouent sur le code d'avant : `shutil.copyfileobj` copiait sans
jamais compter.
"""
from __future__ import annotations

import io

import pytest

from app.uploads import EcritureAudioTropVolumineuse, ecrire_audio_borne


def test_un_flux_sous_le_plafond_est_ecrit_entierement(tmp_path, monkeypatch):
    monkeypatch.setattr("app.uploads.MAX_AUDIO_UPLOAD_BYTES", 1024)
    destination = tmp_path / "petit.webm"
    contenu = b"x" * 500
    ecrits = ecrire_audio_borne(io.BytesIO(contenu), destination, taille_bloc=64)
    assert ecrits == 500
    assert destination.read_bytes() == contenu


def test_un_flux_au_dessus_du_plafond_est_refuse(tmp_path, monkeypatch):
    monkeypatch.setattr("app.uploads.MAX_AUDIO_UPLOAD_BYTES", 1024)
    destination = tmp_path / "gros.webm"
    with pytest.raises(EcritureAudioTropVolumineuse):
        ecrire_audio_borne(io.BytesIO(b"x" * 5000), destination, taille_bloc=64)


def test_le_fichier_partiel_ne_survit_pas_au_refus(tmp_path, monkeypatch):
    """Le laisser serait un défaut à lui seul : il occuperait le disque qu'on
    protège, et `lister_orphelins_globaux` le proposerait à la suppression
    comme s'il s'agissait d'un enregistrement légitime."""
    monkeypatch.setattr("app.uploads.MAX_AUDIO_UPLOAD_BYTES", 1024)
    destination = tmp_path / "partiel.webm"
    with pytest.raises(EcritureAudioTropVolumineuse):
        ecrire_audio_borne(io.BytesIO(b"x" * 5000), destination, taille_bloc=64)
    assert not destination.exists(), (
        "un fichier partiel est reste sur le disque que la borne protege"
    )


def test_le_partiel_ne_survit_pas_non_plus_a_une_lecture_qui_leve(tmp_path, monkeypatch):
    """Un flux réseau interrompu lève au `read`, pas au plafond. Le nettoyage
    doit couvrir ce cas aussi — d'où le `except BaseException` dans le helper."""
    monkeypatch.setattr("app.uploads.MAX_AUDIO_UPLOAD_BYTES", 10 * 1024)
    destination = tmp_path / "coupe.webm"

    class _FluxQuiCoupe:
        def __init__(self):
            self.appels = 0

        def read(self, n):
            self.appels += 1
            if self.appels > 2:
                raise OSError("connexion interrompue")
            return b"y" * n

    with pytest.raises(OSError):
        ecrire_audio_borne(_FluxQuiCoupe(), destination, taille_bloc=64)
    assert not destination.exists(), "un partiel survit a une coupure de flux"


def test_le_plafond_est_lu_a_l_appel_pas_a_l_import(tmp_path, monkeypatch):
    """Même contrat que `lire_upload_audio_borne` : un plafond figé à l'import
    rendrait le réglage — et les tests qui l'abaissent — inopérants."""
    monkeypatch.setattr("app.uploads.MAX_AUDIO_UPLOAD_BYTES", 100)
    with pytest.raises(EcritureAudioTropVolumineuse):
        ecrire_audio_borne(io.BytesIO(b"x" * 200), tmp_path / "a.webm", taille_bloc=32)
    monkeypatch.setattr("app.uploads.MAX_AUDIO_UPLOAD_BYTES", 10_000)
    assert ecrire_audio_borne(
        io.BytesIO(b"x" * 200), tmp_path / "b.webm", taille_bloc=32
    ) == 200


# --- Niveau ROUTE : flux coupé à mi-corps (comité de test 2026-10-22, trou n°3) ---
# Le test unitaire ci-dessus garde le helper ; ceux-ci gardent le CONTRAT des
# deux routes qui l'appellent : réponse JSON d'erreur (pas de HTML, pas de
# traceback), aucun partiel dans RECORDINGS_DIR, aucune référence en base.

from fastapi.testclient import TestClient  # noqa: E402

from app import uploads  # noqa: E402
from app.db import RECORDINGS_DIR, SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AudioFileJob, Interview, Mission  # noqa: E402


class _FluxQuiCoupeAuDeuxiemeBloc:
    """Rend un premier bloc, puis lève : l'écriture a DÉJÀ commencé sur disque."""

    def __init__(self):
        self.appels = 0

    def read(self, n):
        self.appels += 1
        if self.appels >= 2:
            raise OSError("connexion interrompue SECRET_CHEMIN_interne")
        return b"y" * n


def _couper_le_flux(monkeypatch, module_route):
    """Substitue le flux de l'upload par un flux qui casse au 2e bloc, en gardant
    le VRAI `ecrire_audio_borne` (c'est lui qui doit supprimer le partiel)."""
    reel = uploads.ecrire_audio_borne

    def _ecrire(_fichier, destination, *a, **k):
        return reel(_FluxQuiCoupeAuDeuxiemeBloc(), destination, taille_bloc=64)

    monkeypatch.setattr(module_route, "ecrire_audio_borne", _ecrire)


def _fichiers_recordings() -> set[str]:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    return {p.name for p in RECORDINGS_DIR.iterdir()}


@pytest.fixture
def client_route():
    init_db()
    # raise_server_exceptions=False : une 500 doit sortir comme une vraie réponse.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def mission_id_route():
    with SessionLocal() as db:
        m = Mission(name="midflux")
        db.add(m)
        db.commit()
        return m.id


def _verifie_erreur_json_sans_fuite(rep):
    assert rep.status_code >= 400
    assert rep.headers["content-type"].startswith("application/json"), rep.text[:200]
    corps = rep.json()
    assert isinstance(corps.get("error"), str) and corps["error"]
    assert "SECRET_CHEMIN" not in rep.text and "Traceback" not in rep.text


def test_route_backup_flux_coupe_donne_json_sans_partiel_ni_reference(
    client_route, mission_id_route, monkeypatch
):
    import app.routers.interviews_backup as route
    _couper_le_flux(monkeypatch, route)
    avant = _fichiers_recordings()
    rep = client_route.post(
        f"/missions/{mission_id_route}/interviews/record/backup",
        files={"file": ("entretien.webm", b"z" * 4096, "audio/webm")},
    )
    _verifie_erreur_json_sans_fuite(rep)
    assert "path" not in rep.json()  # aucun nom de fichier rendu au client
    assert _fichiers_recordings() == avant, "partiel laisse dans RECORDINGS_DIR"
    with SessionLocal() as db:
        refs = [
            i for i in db.query(Interview).filter_by(mission_id=mission_id_route)
        ]
        assert not refs, "une reference vers l'audio avorte a ete creee"


def test_route_import_flux_coupe_donne_json_sans_partiel_ni_job(
    client_route, mission_id_route, monkeypatch
):
    import app.routers.interviews_audio as route
    _couper_le_flux(monkeypatch, route)
    avant = _fichiers_recordings()
    with SessionLocal() as db:
        jobs_avant = db.query(AudioFileJob).count()
    rep = client_route.post(
        "/audio/transcribe-file",
        data={"session_token": "tok-midflux", "mission_id": str(mission_id_route)},
        files={"file": ("reunion.m4a", b"z" * 4096, "audio/mp4")},
    )
    _verifie_erreur_json_sans_fuite(rep)
    assert _fichiers_recordings() == avant, "partiel laisse dans RECORDINGS_DIR"
    with SessionLocal() as db:
        assert db.query(AudioFileJob).count() == jobs_avant, "job cree malgre l'echec"
