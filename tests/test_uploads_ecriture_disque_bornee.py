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
