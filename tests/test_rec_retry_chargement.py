"""Câblage de `rec_retry.js` (lot « transcription fiable », commit 4).

Gardes de FORME sur les templates — elles ne prouvent pas que le navigateur
exécute le bon chemin (c'est le rôle de test_micro_direct_retry_execution.py,
qui exécute le JS, et de test_e2e_micro_retry.py, qui clique) ; elles figent ce
qu'un test d'exécution ne voit pas : le script est-il chargé, et AVANT le body ?

Même leçon que rec_fetch.js / rec_draft.js : un `defer` rendrait
`window.recRetryPlan` indéfini au moment où les <script> inline des écrans le
lisent (analyse du <body>) — SANS erreur au chargement, la page « marcherait »
jusqu'au premier segment échoué.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
BASE_HTML = RACINE / "app" / "templates" / "base.html"
TEMPLATES = RACINE / "app" / "templates" / "interviews"
RECORD_LIBRE = TEMPLATES / "record_libre.html"
RECORD = TEMPLATES / "record.html"
RETRANSCRIPTION = TEMPLATES / "libre_retranscription.html"


def test_rec_retry_est_charge_sans_defer_avant_le_body() -> None:
    html = BASE_HTML.read_text(encoding="utf-8")
    balise = re.search(r"<script[^>]*rec_retry\.js[^>]*>", html)
    assert balise is not None, "rec_retry.js n'est pas chargé par base.html"
    assert "defer" not in balise.group(0) and "async" not in balise.group(0)
    body = re.search(r"^<body", html, re.M)
    assert body is not None
    assert balise.start() < body.start()
    # Le commentaire Jinja qui explique la règle est là, juste avant la balise
    # (comme pour les scripts voisins), et ne mentionne pas le `defer` en vain.
    avant = html[:balise.start()].rstrip()
    assert avant.endswith("#}"), "commentaire Jinja manquant avant la balise rec_retry.js"
    assert "recRetryPlan" in avant[avant.rfind("{#"):]


@pytest.mark.parametrize("ecran", [RECORD_LIBRE, RECORD], ids=lambda p: p.name)
def test_les_ecrans_d_enregistrement_lisent_le_plan_commun(ecran: Path) -> None:
    html = ecran.read_text(encoding="utf-8")
    assert "SEGMENT_RETRY_DELAYS_MS" not in html, "constante de délais dupliquée"
    assert "var RETRY_PLAN = window.recRetryPlan;" in html
    assert not re.search(r"\[\s*2000\s*,\s*6000", html), "délais recopiés en dur"
    # Le compteur discret et le bandeau de complétude ont leur emplacement,
    # distinct du bandeau rouge des segments perdus.
    assert 'id="rec-replay-status"' in html
    assert 'id="rec-completude-banner"' in html
    assert "RETRY_PLAN.afficherCompletude" in html  # CR-27: message built in rec_retry.js


def test_la_page_de_suivi_de_retranscription_affiche_la_completude() -> None:
    html = RETRANSCRIPTION.read_text(encoding="utf-8")
    assert 'id="retr-completude"' in html
    assert "recRetryPlan.messageCompletude(s.completude)" in html
