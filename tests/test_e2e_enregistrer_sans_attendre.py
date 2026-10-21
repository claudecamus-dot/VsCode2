"""P5 : enchaîner un entretien N+1 après avoir terminé N, MÊME avec des tranches
`failed` restées en base (2026-10-19). Même harnais que `test_e2e_premiers_clics.py`
(uvicorn réel, Edge/Chrome headless, `confirm()` accepté par le pilote).

Parcours rejoué, avec de VRAIS clics :
1. seed : mission nommée + tranches `InterviewSegmentJob` (2 `failed` avec texte,
   1 `running`, 1 `pending`) rattachées au jeton A ;
2. l'écran d'enregistrement libre reçoit un brouillon local (localStorage, la clé
   réelle de `rec_draft.js`) portant le texte et le jeton A ; clic sur « Reprendre
   cette session » puis clic sur « Enregistrer l'entretien » (`#rec-submit`) ;
3. attente de la FICHE (`/interviews/<id>` strict) puis contrôle en base ;
4. clic sur `#nouvel-entretien` : écran neuf, pas de reprise, Démarrer utilisable ;
5. second brouillon (autre texte, sans jeton : l'écran en forge un à l'envoi),
   restauré puis enregistré : 2 entretiens, 2 jetons, tranches de A toujours à A.

Simulé (pas de vrai micro) : le texte « déjà transcrit » entre par le brouillon
local, que l'utilisateur reprend d'un vrai clic ; pas de Démarrer cliqué (il
ouvrirait getUserMedia) — on prouve seulement qu'il est utilisable. Pas de
Whisper ni d'Ollama : `OLLAMA_HOST` pointe un port fermé (la structuration de
fond échoue vite, jamais d'appel réel). NON REJOUÉ au moment de l'écriture."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import pytest

_OBLIGATOIRE = bool(os.environ.get("E2E_OBLIGATOIRE"))
try:
    import websockets  # noqa: F401
except ModuleNotFoundError:
    if _OBLIGATOIRE:
        raise RuntimeError("E2E_OBLIGATOIRE posé mais `websockets` absent") from None
    pytest.skip("websockets (uvicorn[standard]) requis", allow_module_level=True)

from e2e_serveur import serveur_uvicorn  # noqa: E402
from navigateur_cdp import Navigateur, trouver_navigateur  # noqa: E402

from app.services import audio_transcribe  # noqa: E402

_NAVIGATEUR = trouver_navigateur()
if _NAVIGATEUR is None and _OBLIGATOIRE:
    raise RuntimeError("E2E_OBLIGATOIRE posé mais aucun navigateur Chromium trouvé")
pytestmark = [
    pytest.mark.skipif(
        _NAVIGATEUR is None, reason="aucun navigateur Chromium (Edge/Chrome) trouvé"
    ),
    # Sans moteur de transcription, record_libre.html ne rend pas son script
    # (`{% if recording_available %}`) : le bouton ne s'active jamais.
    pytest.mark.skipif(
        not audio_transcribe.is_available(),
        reason="faster-whisper absent : l'écran d'enregistrement n'a pas son script",
    ),
]

_JETON_A = "tok-e2e-chaine-A"
_TEXTE_A = "Premier entretien : le texte réel déjà transcrit avant les blocs en échec."
_TEXTE_B = "Second entretien : un autre propos, sans rapport avec le premier."


@pytest.fixture
def nav():
    profil = Path(tempfile.mkdtemp(prefix="e2e-nav-"))
    navigateur = None
    try:
        navigateur = Navigateur(_NAVIGATEUR, profil / "profil")
        yield navigateur
    finally:
        if navigateur is not None:
            navigateur.fermer()
        shutil.rmtree(profil, ignore_errors=True)


_SEED = r"""
import sys
from app.db import SessionLocal, init_db
from app.models import Mission, InterviewSegmentJob
init_db()
db = SessionLocal()
m = Mission(name="E2E enchainement", is_draft=False)
db.add(m); db.commit()
tok = sys.argv[1]
for pos, (statut, texte, err) in enumerate([
    ("failed", "Tranche 0 : texte réel que l'IA n'a pas su structurer.", "extraction impossible"),
    ("failed", "Tranche 1 : second bloc en échec, texte réel aussi.", "extraction impossible"),
    ("running", "Tranche 2 : encore en cours.", None),
    ("pending", "Tranche 3 : en attente.", None),
]):
    db.add(InterviewSegmentJob(session_token=tok, kind="libre_turns", mission_id=m.id,
                               position=pos, status=statut, text=texte, error=err))
db.commit()
print(m.id)
"""

_LIRE = r"""
import json, sys
from app.db import SessionLocal
from app.models import Interview, InterviewSegmentJob
db = SessionLocal()
mid = int(sys.argv[1])
ivs = db.query(Interview).filter(Interview.mission_id == mid).order_by(Interview.id).all()
jobs = {}
for j in db.query(InterviewSegmentJob).all():
    jobs.setdefault(j.session_token, []).append((j.position, j.status))
print(json.dumps({
    "interviews": [{"id": i.id, "token": i.segment_token, "texte": i.raw_transcript,
                    "statut": i.structuration_status} for i in ivs],
    "jobs": {k: sorted(v) for k, v in jobs.items()},
}))
"""


def _python_sur_base(base_db: Path, code: str, *args: str) -> str:
    import subprocess
    import sys
    env = dict(os.environ, APP_DB_PATH=str(base_db), PYTHONUTF8="1")
    res = subprocess.run([sys.executable, "-c", code, *args],
                         cwd=Path(__file__).resolve().parents[1], env=env,
                         capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return res.stdout.strip().splitlines()[-1]


def _etat(base_db: Path, mid: str) -> dict:
    return json.loads(_python_sur_base(base_db, _LIRE, mid))


def _attendre(nav: Navigateur, condition, delai_s: float, quoi: str) -> None:
    t0 = time.time()
    while time.time() - t0 < delai_s:
        if condition():
            return
        time.sleep(0.2)
    raise AssertionError(f"délai dépassé : {quoi} (url={nav.url()})")


def _poser_brouillon(nav: Navigateur, mid: str, texte: str, jeton: str) -> None:
    """Écrit le brouillon local dans la clé réelle de rec_draft.js
    (`i2d:brouillon-libre:<mission>`), puis recharge l'écran : la bannière de
    reprise (rec_draft.js `decision`) le propose, comme après un onglet fermé."""
    brouillon = {
        "transcript": texte, "sessionToken": jeton, "coveredLen": len(texte),
        "segmentJobPosition": 4 if jeton else 0, "savedAt": int(time.time() * 1000),
    }
    nav.evaluer(
        f"localStorage.setItem('i2d:brouillon-libre:{mid}', {json.dumps(json.dumps(brouillon))}); true"
    )
    nav.naviguer(nav.url())


def _reprendre_et_enregistrer(nav: Navigateur) -> str:
    """Clics réels : « Reprendre cette session » (#rec-draft-restore) puis
    « Enregistrer l'entretien » (#rec-submit). Rend l'URL de la fiche."""
    _attendre(nav, lambda: nav.evaluer("!document.getElementById('rec-draft-banner').hidden"),
              15, "bannière de reprise")
    nav.cliquer("#rec-draft-restore")
    _attendre(nav, lambda: nav.evaluer("!document.getElementById('rec-submit').disabled"),
              15, "bouton Enregistrer activé")
    nav.cliquer("#rec-submit")
    # Strict : `/interviews/record-libre` contient déjà « /interviews/ ».
    _attendre(nav, lambda: re.search(r"/interviews/\d+$", nav.url()) is not None,
              30, "fiche de l'entretien")
    assert not nav.erreurs_http(), nav.erreurs_http()
    return nav.url()


def test_enchainer_un_entretien_apres_un_entretien_aux_tranches_failed(
    tmp_path_factory: pytest.TempPathFactory, nav: Navigateur
) -> None:
    dossier = tmp_path_factory.mktemp("e2e-enchainement")
    base_db = dossier / "e2e.db"
    mid = _python_sur_base(base_db, _SEED, _JETON_A)
    # Port fermé : aucune structuration réelle, échec immédiat en tâche de fond.
    with serveur_uvicorn(dossier, AI_PROVIDER="ollama", OLLAMA_HOST="http://127.0.0.1:9") as base:
        ecran = f"{base}/missions/{mid}/interviews/record-libre"

        # (2) entretien N : brouillon repris + clic Enregistrer
        nav.naviguer(ecran)
        _poser_brouillon(nav, mid, _TEXTE_A, _JETON_A)
        fiche_a = _reprendre_et_enregistrer(nav)

        # (3) base : 1 entretien, texte et tranches conservés
        etat = _etat(base_db, mid)
        assert len(etat["interviews"]) == 1, etat
        a = etat["interviews"][0]
        assert a["token"] == _JETON_A and a["texte"].strip() == _TEXTE_A, a
        assert a["statut"] in ("a_traiter", "en_cours", "echec"), a  # structuration de fond
        # Aucune tranche perdue ni marquée « terminée » à tort. Leurs statuts ne sont
        # pas figés : la structuration de fond retraite toute tranche non terminée,
        # et avec un Ollama injoignable elle les fait tomber en `failed`.
        jobs_a = etat["jobs"][_JETON_A]
        assert [p for p, _ in jobs_a] == [0, 1, 2, 3], etat["jobs"]
        assert {s for _, s in jobs_a} <= {"failed", "running", "pending"}, etat["jobs"]

        # (4) « + Nouvel entretien » depuis la fiche
        assert nav.evaluer("!!document.getElementById('nouvel-entretien')"), "lien absent"
        nav.cliquer_et_attendre("#nouvel-entretien")
        assert nav.url().endswith(f"/missions/{mid}/interviews/record-libre"), nav.url()
        # Jeton de session de l'écran neuf : vide (forgé au Démarrer), jamais le A.
        assert nav.evaluer("document.getElementById('rec-session-token').value") != _JETON_A
        # Brouillon de A envoyé puis purgé par la fiche : rien à reprendre.
        assert nav.evaluer("document.getElementById('rec-draft-banner').hidden"), "reprise proposée"
        assert nav.evaluer("document.getElementById('rec-transcript').value") == ""
        assert nav.evaluer(
            "(function(){var b=document.getElementById('rec-start');"
            "return !!b && !b.disabled && !b.hidden;})()"
        ), "Démarrer grisé par l'état du précédent"
        assert nav.evaluer("document.getElementById('rec-submit').disabled"), "Enregistrer actif sans contenu"

        # (5) entretien N+1 : autre texte, aucun jeton (l'écran en forge un à l'envoi)
        _poser_brouillon(nav, mid, _TEXTE_B, "")
        fiche_b = _reprendre_et_enregistrer(nav)
        assert fiche_b != fiche_a
        etat = _etat(base_db, mid)
        assert len(etat["interviews"]) == 2, etat
        a, b = etat["interviews"]
        assert a["token"] == _JETON_A and b["token"] and b["token"] != _JETON_A, etat
        assert a["texte"].strip() == _TEXTE_A and b["texte"].strip() == _TEXTE_B, etat
        # Les tranches du premier restent à son jeton ; le second n'en hérite pas.
        jobs_a = etat["jobs"][_JETON_A]
        assert [p for p, _ in jobs_a] == [0, 1, 2, 3], etat["jobs"]
        assert {s for _, s in jobs_a} <= {"failed", "running", "pending"}, etat["jobs"]
        assert b["token"] not in etat["jobs"], etat["jobs"]
