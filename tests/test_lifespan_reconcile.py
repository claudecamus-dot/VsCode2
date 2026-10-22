"""Le CÂBLAGE du `lifespan` : chacune des quatre réconciliations de démarrage
doit y être appelée, exactement une fois.

Seul le câblage audio était gardé (`test_audio_file_jobs_reconcile`). Retirer
l'un des trois autres appels de `app/main.py` laissait toute la suite verte et
un job resté `running` après redémarrage n'était plus jamais repris.

Le `lifespan` n'entoure PAS ces appels d'un try/except (seuls les préchauffages
Whisper/Ollama le sont) : l'échec d'une réconciliation fait échouer le
démarrage. Ce comportement n'est donc volontairement pas testé ici.
"""
import pytest
from fastapi.testclient import TestClient

# (nom du symbole tel qu'importé dans app.main)
RECONCILIATIONS = [
    "reconcile_running_on_startup",
    "reconcile_segment_jobs_on_startup",
    "reconcile_audio_file_jobs_on_startup",
    "reconcile_structurations_on_startup",
]


@pytest.mark.parametrize("nom", RECONCILIATIONS)
def test_le_demarrage_appelle_chaque_reconciliation_une_fois(monkeypatch, nom):
    from app import main as app_main

    monkeypatch.setattr(app_main, "warm_up_ollama", lambda: None)
    monkeypatch.setattr(app_main.audio_transcribe, "warm_up", lambda: None)
    appels = {n: 0 for n in RECONCILIATIONS}

    def _espion(n):
        def _f():
            appels[n] += 1
            return 0
        return _f

    for n in RECONCILIATIONS:
        monkeypatch.setattr(app_main, n, _espion(n))

    with TestClient(app_main.app):
        pass

    assert appels[nom] == 1, (
        f"le lifespan doit appeler {nom} exactement une fois (appels={appels[nom]})"
    )
