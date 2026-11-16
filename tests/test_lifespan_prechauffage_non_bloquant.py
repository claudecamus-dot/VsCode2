"""Le démarrage ne doit PAS attendre les préchauffages Whisper/Ollama.

Incident du 2026-11-05 : `lifespan` jouait `audio_transcribe.warm_up()` avant son
`yield` ; sous charge le chargement durait 12 à 15 min, port fermé pendant tout ce
temps, donc « démarre le site » n'était effectif qu'après ce délai.
"""
import logging
import threading

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _prechauffages_actives(monkeypatch):
    """conftest pose WHISPER_WARM_UP=0 / OLLAMA_WARM_UP=0 par défaut, et
    `app.main` saute alors le préchauffage AVANT d'appeler `warm_up()` : sans
    cette fixture, les stubs de ces tests ne seraient jamais appelés. Le test
    « désactivé » repose ses variables à 0 lui-même."""
    monkeypatch.setenv("WHISPER_WARM_UP", "1")
    monkeypatch.setenv("OLLAMA_WARM_UP", "1")


def _joindre_prechauffages() -> None:
    for f in threading.enumerate():
        if f.name.startswith("prechauffage-"):
            f.join(10)


def test_le_lifespan_rend_la_main_pendant_que_whisper_charge(monkeypatch):
    from app import main as app_main

    libere = threading.Event()
    demarre = threading.Event()

    def _whisper_lent():
        demarre.set()
        libere.wait(30)

    monkeypatch.setattr(app_main.audio_transcribe, "warm_up", _whisper_lent)
    monkeypatch.setattr(app_main, "warm_up_ollama", lambda: None)
    sorti = threading.Event()

    def _jouer():
        with TestClient(app_main.app):
            sorti.set()

    t = threading.Thread(target=_jouer, daemon=True)
    t.start()
    try:
        assert sorti.wait(10), "le lifespan attend le préchauffage Whisper avant d'ouvrir le port"
        assert demarre.wait(5), "le préchauffage Whisper n'est jamais lancé"
    finally:
        libere.set()
        t.join(10)
        # les fils de préchauffage doivent finir AVANT le retrait des monkeypatch,
        # sinon le vrai warm_up_ollama peut s'exécuter hors du test
        _joindre_prechauffages()


def test_les_deux_prechauffages_sont_bien_lances_au_demarrage(monkeypatch):
    from app import main as app_main

    faits = {"whisper": threading.Event(), "ollama": threading.Event()}
    monkeypatch.setattr(app_main.audio_transcribe, "warm_up", faits["whisper"].set)
    monkeypatch.setattr(app_main, "warm_up_ollama", faits["ollama"].set)

    with TestClient(app_main.app):
        assert faits["whisper"].wait(10), "Whisper n'est pas préchauffé"
        assert faits["ollama"].wait(10), "Ollama n'est pas préchauffé"
    _joindre_prechauffages()


def test_ollama_n_attend_pas_que_whisper_ait_fini(monkeypatch):
    from app import main as app_main

    libere = threading.Event()
    ollama = threading.Event()

    def _whisper_bloque():
        # bloque jusqu'à libération (finally) : en version séquentielle, Ollama
        # ne démarre jamais pendant le `with` et l'assertion ci-dessous échoue
        libere.wait(60)

    monkeypatch.setattr(app_main.audio_transcribe, "warm_up", _whisper_bloque)
    monkeypatch.setattr(app_main, "warm_up_ollama", ollama.set)

    # le démarrage tourne dans un fil : en version séquentielle il resterait
    # bloqué dans le lifespan sur Whisper, et c'est l'assertion (côté test) qui
    # échoue dans le délai, sans attendre la libération de Whisper.
    def _jouer():
        with TestClient(app_main.app):
            pass

    t = threading.Thread(target=_jouer, daemon=True)
    t.start()
    try:
        assert ollama.wait(5), "Ollama attend la fin du chargement de Whisper"
    finally:
        libere.set()
        t.join(10)
        _joindre_prechauffages()


def test_un_prechauffage_qui_leve_est_absorbe_et_journalise(monkeypatch, caplog):
    from app import main as app_main

    ollama = threading.Event()

    def _boom():
        raise RuntimeError("whisper-KO-sentinelle")

    monkeypatch.setattr(app_main.audio_transcribe, "warm_up", _boom)
    monkeypatch.setattr(app_main, "warm_up_ollama", ollama.set)

    with caplog.at_level(logging.INFO):
        with TestClient(app_main.app):
            assert ollama.wait(10), "l'échec de Whisper a coupé le préchauffage Ollama"
        _joindre_prechauffages()
    erreurs = [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info]
    assert any("Whisper" in r.getMessage() for r in erreurs), "exception Whisper non journalisée"
    assert any("whisper-KO-sentinelle" in str(r.exc_info[1]) for r in erreurs)
    assert not any("Whisper prêt" in r.getMessage() for r in caplog.records),         "un préchauffage échoué ne doit pas annoncer « prêt »"


def test_la_fin_de_chaque_prechauffage_est_journalisee(monkeypatch, caplog):
    from app import main as app_main

    monkeypatch.setattr(app_main.audio_transcribe, "warm_up", lambda: None)
    monkeypatch.setattr(app_main, "warm_up_ollama", lambda: None)
    with caplog.at_level(logging.INFO):
        with TestClient(app_main.app):
            pass
        _joindre_prechauffages()
    textes = " ".join(r.getMessage() for r in caplog.records)
    assert "modèle Whisper prêt" in textes and "modèle Ollama prêt" in textes


def test_un_prechauffage_desactive_n_annonce_pas_de_modele_pret(monkeypatch, caplog):
    """WHISPER_WARM_UP=0 / OLLAMA_WARM_UP=0 : rien n'est chargé, donc le journal
    ne doit pas écrire « modèle prêt » (il le faisait, relecture du 2026-11-16)."""
    from app import main as app_main

    monkeypatch.setenv("WHISPER_WARM_UP", "0")
    monkeypatch.setenv("OLLAMA_WARM_UP", "0")
    with caplog.at_level(logging.INFO):
        with TestClient(app_main.app):
            pass
        _joindre_prechauffages()
    textes = " ".join(r.getMessage() for r in caplog.records)
    assert "prêt" not in textes, textes
    assert "préchauffage Whisper désactivé" in textes
    assert "préchauffage Ollama désactivé" in textes
