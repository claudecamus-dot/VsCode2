"""P5 : la fiche d'un entretien libre n'a que deux onglets (Transcription, Aperçu) ;
modifier le nom dans le pavé « Informations » de l'Aperçu et cliquer « Enregistrer »
ne perd AUCUN tour (2026-10-19). Même harnais que `test_e2e_enregistrer_sans_attendre.py`
(uvicorn réel, Edge/Chrome headless).

Parcours rejoué avec de VRAIS clics :
1. seed : entretien libre `fait` avec 3 tours de parole (base temporaire) ;
2. ouverture de la fiche : 2 onglets, l'Aperçu est affiché, le pavé Informations est dedans ;
3. changement du nom puis clic sur le bouton « Enregistrer » du pavé ;
4. rechargement sur `?onglet=apercu`, base relue : 3 tours inchangés, nom changé.

Pas de Whisper ni d'Ollama. NON REJOUÉ au moment de l'écriture (machine chargée)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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

_NAVIGATEUR = trouver_navigateur()
if _NAVIGATEUR is None and _OBLIGATOIRE:
    raise RuntimeError("E2E_OBLIGATOIRE posé mais aucun navigateur Chromium trouvé")
pytestmark = pytest.mark.skipif(
    _NAVIGATEUR is None, reason="aucun navigateur Chromium (Edge/Chrome) trouvé"
)


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
from app.db import SessionLocal, init_db
from app.models import Interview, InterviewTurn, Mission
init_db()
db = SessionLocal()
m = Mission(name="E2E fiche apercu", is_draft=False)
db.add(m); db.commit()
iv = Interview(mission_id=m.id, interviewee_name="Marc Dupont", mode="libre",
               raw_transcript="Texte brut.", structuration_status="fait")
db.add(iv); db.commit()
for pos, (loc, q, r, s) in enumerate([
    ("Consultant", "Comment ça se passe ?", None, "Contexte"),
    ("Marc Dupont", None, "On travaille en silo.", None),
    ("Marc Dupont", None, "Les outils divergent.", None),
]):
    db.add(InterviewTurn(interview_id=iv.id, position=pos, interlocuteur=loc,
                         question=q, remarque=r, section_title=s))
db.commit()
print(iv.id)
"""

_LIRE = r"""
import json, sys
from app.db import SessionLocal
from app.models import Interview
db = SessionLocal()
iv = db.get(Interview, int(sys.argv[1]))
print(json.dumps({"nom": iv.interviewee_name, "tours": [
    [t.position, t.interlocuteur, t.question, t.remarque, t.section_title]
    for t in sorted(iv.turns, key=lambda t: t.position)]}))
"""


def _python_sur_base(base_db: Path, code: str, *args: str) -> str:
    env = dict(os.environ, APP_DB_PATH=str(base_db), PYTHONUTF8="1")
    res = subprocess.run([sys.executable, "-c", code, *args],
                         cwd=Path(__file__).resolve().parents[1], env=env,
                         capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return res.stdout.strip().splitlines()[-1]


def _attendre(nav: Navigateur, condition, delai_s: float, quoi: str) -> None:
    t0 = time.time()
    while time.time() - t0 < delai_s:
        if condition():
            return
        time.sleep(0.2)
    raise AssertionError(f"délai dépassé : {quoi} (url={nav.url()})")


def test_enregistrer_les_informations_depuis_l_apercu_conserve_les_tours(
    tmp_path_factory: pytest.TempPathFactory, nav: Navigateur
) -> None:
    dossier = tmp_path_factory.mktemp("e2e-fiche-apercu")
    base_db = dossier / "e2e.db"
    iid = _python_sur_base(base_db, _SEED)
    avant = json.loads(_python_sur_base(base_db, _LIRE, iid))["tours"]
    assert len(avant) == 3
    with serveur_uvicorn(dossier, AI_PROVIDER="ollama", OLLAMA_HOST="http://127.0.0.1:9") as base:
        nav.naviguer(f"{base}/interviews/{iid}")
        # Deux onglets, l'Aperçu est affiché, le pavé Informations est dedans.
        assert nav.evaluer("document.querySelectorAll('.rec-tab[data-turntab]').length") == 2
        assert nav.evaluer("document.getElementById('turntab-apercu').hidden") is False
        assert nav.evaluer("!!document.querySelector('#turntab-apercu #apercu-informations')")
        assert "Tour de table" not in nav.texte()

        nav.remplir("#apercu-informations input[name=interviewee_name]", "Marie Durand")
        nav.cliquer_et_attendre("#apercu-informations button[type=submit]")
        _attendre(nav, lambda: "onglet=apercu" in nav.url(), 20, "rechargement sur l'Aperçu")
        assert not nav.erreurs_http(), nav.erreurs_http()
        assert "Marie Durand" in nav.texte()

    apres = json.loads(_python_sur_base(base_db, _LIRE, iid))
    assert apres["nom"] == "Marie Durand"
    assert apres["tours"] == avant
