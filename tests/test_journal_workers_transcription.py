"""Story 3.1 (revue finale, finding 1) : les processus du pool de transcription
écrivent dans `workers.log`. `configurer_journalisation_processus()` existait
mais n'était appelée nulle part : un worker gelé ne laissait aucune trace."""
from __future__ import annotations

import logging


def test_l_initializer_du_pool_branche_le_journal_des_workers(monkeypatch, tmp_path):
    from app import journalisation
    from app.services import audio_transcribe

    monkeypatch.setenv("APP_LOG_DIR", str(tmp_path))
    racine = logging.getLogger("app")
    avant = list(racine.handlers)
    # partir d'un logger sans handler worker (idempotence de la fonction)
    for h in avant:
        if getattr(h, journalisation._MARQUEUR_WORKER, False):
            racine.removeHandler(h)
    ancien_niveau = racine.level
    try:
        audio_transcribe._init_worker(None, None)
        logging.getLogger("app.services.audio_transcribe").warning("sentinelle-worker-s31")
        ajoutes = [h for h in racine.handlers if getattr(h, journalisation._MARQUEUR_WORKER, False)]
        assert ajoutes, "l'initializer du pool ne branche pas le journal des workers"
        for h in ajoutes:
            h.flush()
        journal = tmp_path / journalisation.NOM_FICHIER_WORKERS
        assert journal.exists() and "sentinelle-worker-s31" in journal.read_text(encoding="utf-8")
    finally:
        for h in list(racine.handlers):
            if h not in avant:
                racine.removeHandler(h)
                h.close()
        for h in avant:
            if h not in racine.handlers:
                racine.addHandler(h)
        racine.setLevel(ancien_niveau)
