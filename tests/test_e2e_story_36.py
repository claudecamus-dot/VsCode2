# ruff: noqa: F811  (fixtures importées de test_e2e_stories_3, reprises comme arguments)
"""P5 — Story 3.6 : clics réels (Edge/Chrome headless, vrai uvicorn).

E1 (AC3.6.1) : route historique `record-libre`, une tranche en échec dont la
    récupération échoue (Ollama injoignable) -> la revue le dit -> clic
    « Enregistrer sans la synthèse » -> la fiche affiche la transcription incomplète
    ET la base porte `tranches_manquantes`.
E2 (AC3.6.4) : `record_segment_wait.html` (mode structuré) : suivi coupé -> panneau de
    sortie -> clics Copier, Télécharger, puis « Finaliser avec le texte disponible »
    (confirm() accepté par le pilote) : UN seul POST `record/from-jobs`.

Mêmes harnais que `test_e2e_stories_3.py` (réutilisés, non copiés).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from navigateur_cdp import Navigateur  # noqa: E402
from test_e2e_stories_3 import (  # noqa: E402,F401
    _COUPER_SUIVI,
    _NAVIGATEUR,
    _SEED_MISSION,
    FauxOllama,
    _attendre,
    _poster_depuis_la_page,
    _posts_journal,
    _python_sur_base,
    dossier,
    faux_ollama,
    nav,
    serveur_uvicorn,
)

pytestmark = pytest.mark.skipif(
    _NAVIGATEUR is None, reason="aucun navigateur Chromium (Edge/Chrome) trouvé"
)

_SEED_JOBS_E1 = r"""
import json, sys
from app.db import SessionLocal
from app.models import InterviewSegmentJob
db = SessionLocal()
mid, tok = int(sys.argv[1]), sys.argv[2]
db.add(InterviewSegmentJob(session_token=tok, kind="libre_turns", mission_id=mid, position=0,
    status="done", text="Tranche 0.",
    turns_result={"turns": [{"interlocuteur": "Interviewé·e", "remarque": "T0"}], "identity": {}}))
db.add(InterviewSegmentJob(session_token=tok, kind="libre_turns", mission_id=mid, position=1,
    status="failed", text="Tranche 1 jamais structurée.", error="extraction impossible"))
db.commit()
print("ok")
"""

_SEED_JOB_E2 = r"""
import sys
from app.db import SessionLocal
from app.models import InterviewSegmentJob
db = SessionLocal()
db.add(InterviewSegmentJob(session_token=sys.argv[2], kind="answers", mission_id=int(sys.argv[1]),
    position=0, status="pending", text="Tranche en attente."))
db.commit()
print("ok")
"""

_SEED_JOB_E3 = _SEED_JOB_E2.replace('kind="answers"', 'kind="libre_turns"')

_LIRE_E1 = r"""
import json, sys
from app.db import SessionLocal
from app.models import Interview
db = SessionLocal()
ivs = db.query(Interview).filter(Interview.mission_id == int(sys.argv[1])).all()
print(json.dumps([[i.id, i.tranches_manquantes] for i in ivs]))
"""


def test_e1_record_libre_historique_persiste_la_transcription_incomplete(
    dossier: Path, nav: Navigateur,
) -> None:
    mid = _python_sur_base(dossier, _SEED_MISSION)
    # Ollama injoignable : la récupération de la tranche échouée échoue -> tranche manquante.
    with serveur_uvicorn(dossier, AI_PROVIDER="ollama", OLLAMA_HOST="http://127.0.0.1:9",
                         OLLAMA_TIMEOUT="3") as base:
        _python_sur_base(dossier, _SEED_JOBS_E1, mid, "tok-e2e-s36-e1")
        nav.naviguer(f"{base}/missions/{mid}")
        _poster_depuis_la_page(nav, f"/missions/{mid}/interviews/record-libre", [
            ("transcript", "Texte brut de l'entretien."),
            ("session_token", "tok-e2e-s36-e1"),
        ])
        assert nav.evaluer("!!document.getElementById('turns-review-form')"), nav.texte()[:600]
        assert "pas pu être structurée" in nav.texte(), nav.texte()[:800]
        nav.cliquer_et_attendre("button[formaction$='/record-libre/confirm']")
        _attendre(nav, lambda: "/interviews/" in nav.url(), 30, "fiche de l'entretien")
        assert "pas pu être structurée" in nav.texte(), nav.texte()[:800]
        lignes = json.loads(_python_sur_base(dossier, _LIRE_E1, mid))
        assert len(lignes) == 1 and lignes[0][1] == 1, lignes


def test_e2_page_d_attente_structuree_sortie_copie_export_finalisation(
    dossier: Path, faux_ollama: FauxOllama, nav: Navigateur,
) -> None:
    mid = _python_sur_base(dossier, _SEED_MISSION)
    jeton = "tok-e2e-s36-e2"
    with serveur_uvicorn(dossier, AI_PROVIDER="ollama", OLLAMA_HOST=faux_ollama.hote,
                         OLLAMA_TIMEOUT="30") as base:
        _python_sur_base(dossier, _SEED_JOB_E2, mid, jeton)
        nav.naviguer(f"{base}/missions/{mid}")
        _poster_depuis_la_page(nav, f"/missions/{mid}/interviews/record", [
            ("transcript", "Texte brut reçu par la page d'attente structurée."),
            ("session_token", jeton),
        ])
        assert nav.evaluer("!!document.getElementById('finalize-form')"), nav.texte()[:600]
        assert nav.evaluer("document.getElementById('seg-exit').hidden")
        nav.evaluer(_COUPER_SUIVI)
        _attendre(nav, lambda: nav.evaluer("!document.getElementById('seg-exit').hidden"),
                  90, "panneau de sortie (suivi muet)")
        assert nav.evaluer("!document.getElementById('seg-exit-finalize').disabled")

        _sortie_copie_export_finalisation(nav, dossier, "/record/from-jobs")


_STATUT = "document.getElementById('seg-exit-status').textContent"


def _sortie_copie_export_finalisation(nav: Navigateur, dossier: Path, suffixe: str) -> None:
    # Presse-papiers remplacé par un succès : le texte attendu est exact, jamais
    # « copiée OU refusée ».
    nav.evaluer("navigator.clipboard.writeText = function(){return Promise.resolve();}; true")
    nav.cliquer("#seg-exit-copy")
    _attendre(nav, lambda: nav.evaluer(_STATUT)
              == "Transcription brute copiée dans le presse-papiers.", 15, "copie")
    nav.cliquer("#seg-exit-download")
    _attendre(nav, lambda: nav.evaluer(_STATUT)
              == "Transcription brute téléchargée.", 15, "export")
    nav.emuler_latence(1500)
    nav.double_cliquer("#seg-exit-finalize")  # confirm() accepté par le pilote
    _attendre(nav, lambda: nav.url().endswith(suffixe), 60, "finalisation")
    nav.emuler_latence(0)
    _attendre(nav, lambda: _posts_journal(dossier, suffixe) >= 1, 15, "POST journalisé")
    # Un second POST tardif doit aussi être vu : le compte reste 1 sur toute une fenêtre
    # (latence émulée + marge), pas seulement à l'instant du premier.
    fin = time.time() + 4.0
    while time.time() < fin:
        nav.drainer()
        assert _posts_journal(dossier, suffixe) == 1, "double soumission"
        time.sleep(0.25)


def test_e3_page_d_attente_libre_meme_sortie(
    dossier: Path, faux_ollama: FauxOllama, nav: Navigateur,
) -> None:
    mid = _python_sur_base(dossier, _SEED_MISSION)
    jeton = "tok-e2e-s36-e3"
    with serveur_uvicorn(dossier, AI_PROVIDER="ollama", OLLAMA_HOST=faux_ollama.hote,
                         OLLAMA_TIMEOUT="30") as base:
        _python_sur_base(dossier, _SEED_JOB_E3, mid, jeton)
        nav.naviguer(f"{base}/missions/{mid}")
        _poster_depuis_la_page(nav, f"/missions/{mid}/interviews/record-libre", [
            ("transcript", "Texte brut reçu par la page d'attente libre."),
            ("session_token", jeton),
        ])
        assert nav.evaluer("!!document.getElementById('seg-exit')"), nav.texte()[:600]
        nav.evaluer(_COUPER_SUIVI)
        _attendre(nav, lambda: nav.evaluer("!document.getElementById('seg-exit').hidden"),
                  90, "panneau de sortie (suivi muet)")
        _sortie_copie_export_finalisation(nav, dossier, "/record-libre/from-jobs")


_SEED_RENDU = r"""
import json, sys
from app.db import SessionLocal, init_db
from app.models import AudioFileJob, Interview, InterviewSegmentJob, Mission
init_db()
db = SessionLocal()
m = Mission(name="Brouillon rendu", is_draft=True); db.add(m); db.flush()
nom = f"{m.id}_rendu.webm"
iv = Interview(mission_id=m.id, mode="libre", status="done", interviewee_name="Camille Test",
               audio_segments=[{"filename": nom, "position": 0}], parties_supprimees=2,
               tranches_manquantes=1, structuration_status="fait")
db.add(iv); db.flush()
db.add(AudioFileJob(session_token="tok-rendu", status="done", filenames=[nom],
                    interview_id=iv.id, blocks=["bloc"], files_done=1))
db.add(InterviewSegmentJob(session_token="tok-rendu", kind="libre_turns", mission_id=m.id,
    position=0, status="done", text="t",
    turns_result={"turns": [{"interlocuteur": "Interviewé·e", "remarque": "Propos"}], "identity": {}}))
db.commit()
print(json.dumps({"mid": m.id, "iid": iv.id}))
"""


def test_e4_rendu_des_bandeaux(dossier: Path, faux_ollama: FauxOllama, nav: Navigateur) -> None:
    """Screenshots of the three banners (written when S36_RENDER_DIR is set) +
    assertions on what the browser shows."""
    rendu = os.environ.get("S36_RENDER_DIR")
    ids = json.loads(_python_sur_base(dossier, _SEED_RENDU))
    with serveur_uvicorn(dossier, AI_PROVIDER="ollama", OLLAMA_HOST=faux_ollama.hote,
                         OLLAMA_TIMEOUT="30") as base:
        nav.naviguer(f"{base}/missions/{ids['mid']}/finaliser")
        assert "partie" in nav.texte() and "supprimée" in nav.texte(), nav.texte()[:600]
        assert "le contenu restitué ne couvre pas tout l'entretien" in nav.texte()
        if rendu:
            nav.capturer(Path(rendu) / "finaliser.png")
        nav.naviguer(f"{base}/interviews/{ids['iid']}")
        _poster_depuis_la_page(nav, f"/interviews/{ids['iid']}/retranscrire/appliquer", [])
        assert "Retranscription proposée" in nav.texte(), nav.texte()[:400]
        assert "le contenu restitué ne couvre pas tout l'entretien" in nav.texte()
        if rendu:
            nav.capturer(Path(rendu) / "retranscription.png")
        _poster_depuis_la_page(nav, f"/missions/{ids['mid']}/interviews/record-libre/synthese", [
            ("transcript", "T"), ("tranches_manquantes", "2"),
            ("turn_interlocuteur", "A"), ("turn_question", "Q"),
            ("turn_remarque", ""), ("turn_section_title", ""),
        ])
        assert "pas pu être structurées" in nav.texte(), nav.texte()[:600]
        if rendu:
            nav.capturer(Path(rendu) / "etape3.png")
