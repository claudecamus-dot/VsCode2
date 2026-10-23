"""P5 : clics « Relancer ce passage » et « Relancer tous les passages en échec »
dans un vrai navigateur (uvicorn réel, Edge/Chrome headless, `confirm()` accepté par
le pilote). Ollama injoignable (`OLLAMA_HOST` -> port fermé) : une tranche relancée ne
peut pas réussir ; on n'affirme donc que des faits stables (fiche lisible, pas
d'erreur HTTP, positions conservées, aucune tranche voisine touchée, rien `done` à
tort), jamais un statut transitoire `running`.

Non couvert : refus d'un clic pendant une structuration `en_cours` portée par un worker
VIVANT — le registre des workers est en mémoire du processus uvicorn, non
amorçable depuis la base."""
from __future__ import annotations

import json
import os
import re
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

_JETON = "tok-e2e-relance"


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


# Entretien libre `fait` partiel : tranches 0 et 3 réussies, 1 et 2 en échec.
_SEED = r"""
import json, sys
from app.db import SessionLocal, init_db
from app.models import Interview, InterviewSegmentJob, Mission
init_db()
db = SessionLocal()
m = Mission(name="E2E relance tranches", is_draft=False)
db.add(m); db.flush()
tok = sys.argv[1]
iv = Interview(mission_id=m.id, interviewee_name="Témoin", mode="libre", status="done",
               segment_token=tok, structuration_status="fait", tranches_manquantes=2,
               raw_transcript="Texte complet déjà transcrit.")
db.add(iv)
jobs = []
for pos, (statut, err) in enumerate([("done", None), ("failed", "ia indisponible 1"),
                                     ("failed", "ia indisponible 2"), ("done", None)]):
    j = InterviewSegmentJob(session_token=tok, kind="libre_turns", mission_id=m.id,
                            position=pos, status=statut, text=f"Texte de la tranche {pos}.",
                            error=err)
    db.add(j); jobs.append(j)
db.commit()
print(json.dumps({"mid": m.id, "iid": iv.id, "jobs": [j.id for j in jobs]}))
"""

_LIRE = r"""
import json, sys
from app.db import SessionLocal
from app.models import InterviewSegmentJob
db = SessionLocal()
js = db.query(InterviewSegmentJob).filter(InterviewSegmentJob.session_token == sys.argv[1]).all()
print(json.dumps({j.id: [j.position, j.status, str(j.created_at), j.text] for j in js}))
"""


def _python_sur_base(base_db: Path, code: str, *args: str) -> str:
    env = dict(os.environ, APP_DB_PATH=str(base_db), PYTHONUTF8="1")
    res = subprocess.run([sys.executable, "-c", code, *args],
                         cwd=Path(__file__).resolve().parents[1], env=env,
                         capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return res.stdout.strip().splitlines()[-1]


def _etat(base_db: Path) -> dict[int, list]:
    return {int(k): v for k, v in json.loads(_python_sur_base(base_db, _LIRE, _JETON)).items()}


def _stabilise(base_db: Path, ids: list[int], delai_s: float = 30) -> dict[int, list]:
    """Attend que les tranches relancées ne soient plus `running` (Ollama injoignable :
    elles retombent en `failed` en quelques ms)."""
    t0 = time.time()
    while True:
        etat = _etat(base_db)
        if all(etat[i][1] != "running" for i in ids):
            return etat
        assert time.time() - t0 <= delai_s, f"tranche restée `running` après {delai_s}s : {etat}"
        time.sleep(0.3)


def _sur_la_fiche(nav: Navigateur, iid: int) -> None:
    assert re.search(rf"/interviews/{iid}$", nav.url()), nav.url()  # ni ?tranche=…, ni erreur
    assert not nav.erreurs_http(), nav.erreurs_http()
    texte = nav.texte()
    assert "Structuration en tours de parole" in texte, texte[:400]
    assert "Traceback" not in texte and "Internal Server Error" not in texte
    assert "n'existe plus ou est déjà en cours" not in texte, "relance refusée à tort"


def _nb(nav: Navigateur, selecteur: str) -> int:
    return nav.evaluer(f"document.querySelectorAll({json.dumps(selecteur)}).length")


def test_relancer_une_tranche_puis_toutes_au_clic(
    tmp_path_factory: pytest.TempPathFactory, nav: Navigateur
) -> None:
    dossier = tmp_path_factory.mktemp("e2e-relance")
    base_db = dossier / "e2e.db"
    ids = json.loads(_python_sur_base(base_db, _SEED, _JETON))
    iid, (j0, j1, j2, j3) = ids["iid"], ids["jobs"]
    avant = _etat(base_db)

    with serveur_uvicorn(dossier, AI_PROVIDER="ollama", OLLAMA_HOST="http://127.0.0.1:9") as base:
        fiche = f"{base}/interviews/{iid}"
        nav.naviguer(fiche)

        # (1) liste des tranches en échec visible, avec ses boutons
        texte = nav.texte()
        assert "2 passages en échec" in texte, texte[:600]
        assert "Tranche 2" in texte and "Tranche 3" in texte
        assert _nb(nav, f"form[action$='/tranches/{j1}/relancer'] button") == 1
        assert _nb(nav, f"form[action$='/tranches/{j2}/relancer'] button") == 1
        assert _nb(nav, f"form[action$='/tranches/{j0}/relancer']") == 0, \
            "une tranche réussie ne doit pas offrir de relance"
        assert _nb(nav, f"form[action$='/interviews/{iid}/tranches/relancer'] button") == 1

        # (2) « Relancer ce passage » sur la tranche 2 (job j1) seulement
        nav.cliquer_et_attendre(f"form[action$='/tranches/{j1}/relancer'] button")
        _sur_la_fiche(nav, iid)
        apres = _stabilise(base_db, [j1])
        assert set(apres) == set(avant), "tranche perdue ou ajoutée"
        for jid in (j0, j1, j2, j3):
            assert apres[jid][0] == avant[jid][0] and apres[jid][3] == avant[jid][3], \
                f"position/texte de {jid} modifiés"
        assert apres[j1][1] in ("failed", "running"), apres[j1]  # jamais `done` sans IA
        assert apres[j1][2] != avant[j1][2], "la tranche cliquée n'a pas été réservée"
        for jid in (j0, j2, j3):  # voisines intactes
            assert apres[jid] == avant[jid], (jid, avant[jid], apres[jid])
        # la fiche rechargée montre à nouveau la tranche relançable, sans page d'erreur
        nav.naviguer(fiche)
        _sur_la_fiche(nav, iid)
        assert "Relancer ce passage" in nav.texte()

        # (3) « Relancer tous les passages en échec »
        avant2 = _etat(base_db)
        nav.cliquer_et_attendre(f"form[action$='/interviews/{iid}/tranches/relancer'] button")
        _sur_la_fiche(nav, iid)
        apres2 = _stabilise(base_db, [j1, j2])
        for jid in (j1, j2):
            assert apres2[jid][2] != avant2[jid][2], f"tranche {jid} non traitée"
            assert apres2[jid][1] in ("failed", "running"), apres2[jid]
            assert apres2[jid][0] == avant[jid][0] and apres2[jid][3] == avant[jid][3]
        for jid in (j0, j3):  # tranches réussies intactes
            assert apres2[jid] == avant[jid], (jid, avant[jid], apres2[jid])
        nav.naviguer(fiche)
        _sur_la_fiche(nav, iid)
        assert "Relancer tous les passages en échec" in nav.texte()
