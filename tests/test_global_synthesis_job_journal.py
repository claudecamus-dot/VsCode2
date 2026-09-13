"""Échec de la synthèse globale JAMAIS silencieux — finding audit-technique
robustesse du 2026-09-13 (`.claude/audits/VSCode2.json` côté hub).

Le garde-fou `except Exception` de `run_global_synthesis_job` n'appelait
`logger.exception` qu'à l'INTÉRIEUR du `if mission is not None and
mission.global_synthesis is not None`. Trois branches partaient donc sans une
seule ligne de journal — `db.rollback()` qui lève, mission disparue pendant le
job, `global_synthesis` nul — et l'exception d'origine finissait dans le
`except Exception: pass` : le job échouait et rien, nulle part, ne disait
pourquoi. Ses deux sœurs au même contrat (`audio_file_jobs`,
`interview_segment_jobs`) journalisent dès l'entrée du handler ; celle-ci était
la seule des trois à ne pas le faire.

Ces trois cas ÉCHOUAIENT sur le code d'avant (aucun enregistrement capturé), et
le quatrième — le cas nominal, mission vivante — passait déjà : il est gardé ici
pour prouver que le déplacement du log n'a rien perdu au passage.

Aucune base n'est touchée : `SessionLocal` est remplacée par un double qui lève
là où on veut, ce qui rend le test instantané et sans effet de bord (le journal
ne doit pas dépendre de l'état de `data/app.db`).
"""
from __future__ import annotations

import logging

import pytest

from app.services import global_synthesis_job as job


class _SessionQuiEchoue:
    """Session minimale : `get()` lève, et `rollback()` lève optionnellement —
    les deux portes par lesquelles l'échec devenait muet."""

    def __init__(self, rollback_leve: bool = False, mission=None):
        self.rollback_leve = rollback_leve
        self.mission = mission
        self.closed = False

    def get(self, modele, ident):
        # 1er appel (corps du job) : c'est LUI qui déclenche le garde-fou.
        # 2e appel (secours, après rollback) : rend ce que le scénario veut.
        if self.mission is _SENTINELLE:
            raise RuntimeError("echec fabrique : lecture de la mission impossible")
        if not getattr(self, "_premier_appel_fait", False):
            self._premier_appel_fait = True
            raise RuntimeError("echec fabrique dans le corps du job")
        return self.mission

    def rollback(self):
        if self.rollback_leve:
            raise RuntimeError("echec fabrique : rollback impossible (session cassee)")

    def commit(self):
        pass

    def close(self):
        self.closed = True


_SENTINELLE = object()


class _MissionSansSynthese:
    global_synthesis = None


def _lancer(monkeypatch, caplog, session) -> list[str]:
    monkeypatch.setattr(job, "SessionLocal", lambda: session)
    with caplog.at_level(logging.ERROR, logger=job.__name__):
        # Contrat du module : « ne lève jamais ». Vérifié ici aussi.
        job.run_global_synthesis_job(1)
    return [r.getMessage() + (r.exc_text or "") for r in caplog.records]


@pytest.mark.parametrize(
    "session, cas",
    [
        (_SessionQuiEchoue(rollback_leve=True), "le rollback de secours leve"),
        (_SessionQuiEchoue(mission=None), "la mission a disparu pendant le job"),
        (_SessionQuiEchoue(mission=_MissionSansSynthese()), "global_synthesis est nul"),
    ],
    ids=["rollback-leve", "mission-disparue", "synthese-nulle"],
)
def test_un_echec_de_synthese_globale_laisse_toujours_une_trace(
    monkeypatch, caplog, session, cas
):
    lignes = _lancer(monkeypatch, caplog, session)
    trace = "\n".join(lignes)
    assert lignes, f"echec totalement silencieux alors que {cas}"
    assert "synthèse globale" in trace or "synthese globale" in trace
    # L'exception d'ORIGINE, pas seulement un message générique : c'est elle qui
    # dit pourquoi le job a échoué.
    assert "echec fabrique" in trace
    assert session.closed, "la session doit etre refermee dans tous les cas"
