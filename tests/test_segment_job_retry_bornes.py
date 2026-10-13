"""Bornes des réglages de reprise des jobs de tranche (E3, 2026-10-13).

Aucune base ni vrai sleep : env + appel des deux fonctions ; le test de bout en
bout remplace `_extract_for_job` et `_attendre` par des faux."""
from __future__ import annotations

import math

import pytest

from app.services import interview_segment_jobs as isj

DELAY = "SEGMENT_JOB_RETRY_DELAY_S"
RETRIES = "SEGMENT_JOB_RETRIES"


@pytest.mark.parametrize(
    "raw, attendu",
    [
        ("inf", isj._RETRY_DELAY_DEFAUT_S),
        ("nan", isj._RETRY_DELAY_DEFAUT_S),
        ("1e400", isj._RETRY_DELAY_DEFAUT_S),
        ("abc", isj._RETRY_DELAY_DEFAUT_S),
        ("", isj._RETRY_DELAY_DEFAUT_S),
        ("-1", 0.0),
        ("-0.0", 0.0),
        ("1e9", isj._RETRY_DELAY_MAX_S),
        ("60", isj._RETRY_DELAY_MAX_S),
        ("5", 5.0),
    ],
)
def test_delai(monkeypatch, raw, attendu):
    monkeypatch.setenv(DELAY, raw)
    d = isj.segment_job_retry_delay_s()
    assert d == attendu
    assert math.isfinite(d) and 0.0 <= d <= isj._RETRY_DELAY_MAX_S


@pytest.mark.parametrize(
    "raw, attendu",
    [
        ("abc", isj._RETRIES_DEFAUT),
        ("inf", isj._RETRIES_DEFAUT),
        ("1.5", isj._RETRIES_DEFAUT),
        ("-3", 0),
        ("1000000000", isj._RETRIES_MAX),
        ("0", 0),
        ("2", 2),
        ("4", isj._RETRIES_MAX),
        ("5", isj._RETRIES_MAX),
    ],
)
def test_essais(monkeypatch, raw, attendu):
    monkeypatch.setenv(RETRIES, raw)
    n = isj.segment_job_retries()
    assert n == attendu
    assert 0 <= n <= isj._RETRIES_MAX


def test_defauts_sans_env(monkeypatch):
    monkeypatch.delenv(DELAY, raising=False)
    monkeypatch.delenv(RETRIES, raising=False)
    assert isj.segment_job_retry_delay_s() == isj._RETRY_DELAY_DEFAUT_S
    assert isj.segment_job_retries() == isj._RETRIES_DEFAUT


@pytest.mark.parametrize("delai", ["inf", "nan", "1e9"])
def test_extract_with_retries_borne_de_bout_en_bout(monkeypatch, delai):
    """Réglages extrêmes : nombre d'appels plafonné, attentes finies et bornées,
    aucun OverflowError, aucun vrai sleep."""
    monkeypatch.setenv(DELAY, delai)
    monkeypatch.setenv(RETRIES, "1000000000")
    appels = []
    attentes = []

    def faux_extract(db, job):
        appels.append(1)
        exc = isj._EXTRACT_ERRORS[0]("panne simulee")
        exc.transitoire = True
        raise exc

    monkeypatch.setattr(isj, "_extract_for_job", faux_extract)
    monkeypatch.setattr(isj, "_attendre", attentes.append)
    job = type("JobFactice", (), {"id": 1})()

    with pytest.raises(isj._EXTRACT_ERRORS):
        isj._extract_with_retries(None, job)

    assert len(appels) == isj._RETRIES_MAX + 1 == 5
    assert len(attentes) == isj._RETRIES_MAX
    for n, v in enumerate(attentes):
        assert isinstance(v, float) and math.isfinite(v)
        assert 0.0 <= v <= isj._RETRY_DELAY_MAX_S * (n + 1)
    assert max(attentes) <= 240.0
