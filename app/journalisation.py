"""Application logging setup (Story 3.1, AC3.1.1 — finding D-1).

Before this module, nothing in `app/` configured logging: every `logger.info`
of the domain (reconciliations, warm-ups, jobs) went nowhere, and WARNING+
reached stderr through `logging.lastResort` without a timestamp. A frozen job
or a dead server left no dated trace to read.

`configurer_journalisation()` attaches ONE rotating file handler to the `app`
logger (all `app.*` loggers propagate to it), and enables `faulthandler`.
It is idempotent: `uvicorn --reload` re-imports `app.main` in each worker and
every `TestClient(app)` replays the lifespan — a second call must not add a
second handler (each line would be written twice, then three times...).

Log directory: `APP_LOG_DIR` if set, else `<DATA_DIR>/logs`, where DATA_DIR
follows `APP_DB_PATH` (app/db.py) — so the test suite, which points
`APP_DB_PATH` at a throwaway base, never writes into the real `data/logs/`.
`data/` is gitignored.
"""
from __future__ import annotations

import faulthandler
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"
NOM_FICHIER = "app.log"
TAILLE_MAX_OCTETS = 5 * 1024 * 1024
NB_SAUVEGARDES = 5
# Marker attribute: identifies OUR handler among those already attached, so a
# second call (reload, new TestClient lifespan) recognises it instead of
# stacking a duplicate.
_MARQUEUR = "_journal_app_s31"


def dossier_journaux() -> Path:
    env = os.environ.get("APP_LOG_DIR")
    if env:
        return Path(env)
    from .db import DATA_DIR

    return Path(DATA_DIR) / "logs"


def configurer_journalisation() -> Path | None:
    """Attach the rotating file handler to the `app` logger (once) and enable
    faulthandler. Returns the log file path, or None if it could not be opened
    (a log that cannot be written must not take the server down)."""
    if not faulthandler.is_enabled():
        try:
            faulthandler.enable()
        except (RuntimeError, ValueError, OSError):
            pass  # no usable stderr (pythonw): the file log still works
    racine = logging.getLogger("app")
    for h in racine.handlers:
        if getattr(h, _MARQUEUR, False):
            return Path(h.baseFilename)
    chemin = dossier_journaux() / NOM_FICHIER
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            chemin, maxBytes=TAILLE_MAX_OCTETS, backupCount=NB_SAUVEGARDES,
            encoding="utf-8", delay=True,
        )
    except OSError as exc:
        print(f"[app.journalisation] log file unavailable ({chemin}): {exc}",
              file=sys.stderr, flush=True)
        return None
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.setLevel(logging.INFO)
    setattr(handler, _MARQUEUR, True)
    racine.addHandler(handler)
    if racine.level == logging.NOTSET or racine.level > logging.INFO:
        racine.setLevel(logging.INFO)
    return chemin


NOM_FICHIER_WORKERS = "workers.log"
_MARQUEUR_WORKER = "_journal_worker_s31"


def configurer_journalisation_processus() -> Path | None:
    """For a CHILD process (e.g. a transcription pool initializer): idempotent,
    no lifespan, no app import beyond app.db's path. Writes to `workers.log`
    (same directory as app.log) with the process name in each line. Plain
    append, NO rotation: several processes rotating one file race on Windows
    (a rename fails while another process holds it open)."""
    if not faulthandler.is_enabled():
        try:
            faulthandler.enable()
        except (RuntimeError, ValueError, OSError):
            pass
    racine = logging.getLogger("app")
    for h in racine.handlers:
        if getattr(h, _MARQUEUR_WORKER, False):
            return Path(h.baseFilename)
    chemin = dossier_journaux() / NOM_FICHIER_WORKERS
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(chemin, mode="a", encoding="utf-8", delay=True)
    except OSError as exc:
        print(f"[app.journalisation] worker log unavailable ({chemin}): {exc}",
              file=sys.stderr, flush=True)
        return None
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(processName)s/%(threadName)s] %(message)s"))
    handler.setLevel(logging.INFO)
    setattr(handler, _MARQUEUR_WORKER, True)
    racine.addHandler(handler)
    if racine.level == logging.NOTSET or racine.level > logging.INFO:
        racine.setLevel(logging.INFO)
    return chemin
