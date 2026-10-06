"""Quand le secours d'un job de fond échoue à son tour (rollback impossible),
le statut peut rester figé : ce second échec doit laisser une trace, il
n'est plus avalé par `except Exception: pass` (audit robustesse 2026-10-06)."""
from __future__ import annotations

import logging

from app.services import audio_file_jobs as job


class _SessionCassee:
    def get(self, *_a):
        raise RuntimeError("echec-corps")

    def rollback(self):
        raise RuntimeError("echec-rollback")

    def commit(self):
        pass

    def close(self):
        pass


def test_secours_qui_echoue_est_journalise(monkeypatch, caplog) -> None:
    monkeypatch.setattr(job, "SessionLocal", lambda: _SessionCassee())
    with caplog.at_level(logging.ERROR, logger=job.__name__):
        job.run_audio_file_job(1)  # contrat : ne lève jamais
    trace = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert "Secours d'échec impossible" in trace
    assert "echec-rollback" in trace
