"""P5: real-browser clicks for the behaviours merged with Stories 3.3, 3.4 and
triage B-2 (2026-11-17). Same harness as `test_e2e_premiers_clics.py`: a real
uvicorn on a free port (`tests/e2e_serveur.py`), Edge/Chrome headless driven
over CDP (`tests/navigateur_cdp.py`, `confirm()` accepted by the driver).

No Whisper model and no real Ollama: `OLLAMA_HOST` points at a fake Ollama
held by this module (the `/api/chat` contract only, same shape as the one in
`test_e2e_enregistrement_libre.py`), so the structuring/synthesis steps that
the clicks trigger answer instantly and deterministically.

C1 (Story 3.4 B-1): « Structurer le texte disponible » on an `a_transcrire`
    interview that carries text, confirm() accepted -> leaves `a_transcrire`.
C2 (Story 3.4 B-9/F1): synthesis -> « Revenir aux questions/réponses » ->
    synthesis -> double click « Enregistrer l'entretien »: one interview for
    the session token, its segment jobs consumed, not relançable.
C3 (Story 3.3 A-8): libre wait page with a pending tranche, status polls cut
    in the page -> exit panel -> « Finaliser avec le texte disponible »: the
    button is disabled after the click, ONE POST, the turns review shows.
C4 (triage B-2): « Répartition » live preview: t0 done, t1 failed, t2 done ->
    t0, t2; t1 marked done in the DB -> t1 appears between them, once.

Seeding goes through the ORM in a subprocess bound to the test database
(`APP_DB_PATH`). Pending tranches are seeded AFTER the server started: the
startup reconciliation (`interview_segment_jobs.reconcile_running_on_startup`)
turns every pending/running tranche into `failed`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
pytestmark = pytest.mark.skipif(
    _NAVIGATEUR is None, reason="aucun navigateur Chromium (Edge/Chrome) trouvé"
)

_RACINE = Path(__file__).resolve().parents[1]
# Per-run database folders, outside the repo and away from data/app.db.
_RACINE_BASES = Path(os.environ.get("E2E_STORIES3_DB_ROOT", r"C:\tmp\we2edb"))


# --------------------------------------------------------------------------- #
# Fake Ollama: the /api/chat contract, nothing else
# --------------------------------------------------------------------------- #
class FauxOllama:
    """Answers `POST /api/chat` like Ollama, recognising the contract asked in
    the system message: turn extraction (`"turns"`) or per-axis repartition
    (`"repartition"`). Counts its calls."""

    def __init__(self) -> None:
        self.extractions = 0
        self.repartitions = 0
        self._verrou = threading.Lock()
        faux = self

        class Requete(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass

            def _json(self, corps: dict) -> None:
                donnees = json.dumps(corps, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(donnees)))
                self.end_headers()
                self.wfile.write(donnees)

            def do_GET(self) -> None:
                self._json({"models": [{"name": "faux-modele"}]})

            def do_POST(self) -> None:
                taille = int(self.headers.get("Content-Length") or 0)
                corps = json.loads(self.rfile.read(taille) or b"{}")
                messages = corps.get("messages") or []
                if not messages:
                    self._json({"done": True})
                    return
                systeme = str(messages[0].get("content", ""))
                prompt = str(messages[-1].get("content", ""))
                with faux._verrou:
                    contenu = faux._repondre(systeme, prompt)
                self._json({
                    "model": corps.get("model"),
                    "message": {"role": "assistant",
                                "content": json.dumps(contenu, ensure_ascii=False)},
                    "done": True,
                })

        self.serveur = ThreadingHTTPServer(("127.0.0.1", 0), Requete)
        self.hote = f"http://127.0.0.1:{self.serveur.server_address[1]}"
        self._fil = threading.Thread(target=self.serveur.serve_forever, daemon=True)
        self._fil.start()

    def _repondre(self, systeme: str, prompt: str) -> dict:
        if '"turns"' in systeme:
            self.extractions += 1
            texte = prompt.split("TRANSCRIPTION :", 1)[-1].strip() or "(vide)"
            return {
                "turns": [
                    {"interlocuteur": "Consultant·e", "question": "Pouvez-vous vous présenter ?",
                     "section_title": f"Extraction n°{self.extractions}"},
                    {"interlocuteur": "Interviewé·e", "remarque": texte[:300]},
                ],
                "identite": {"interviewee_name": "", "interviewee_role": "",
                             "interviewee_entity": ""},
            }
        if '"repartition"' in systeme:
            self.repartitions += 1
            m = re.search(r"aux \d+ clés (.+?)\)", systeme)
            cles = m.group(1).split("/") if m else []
            return {
                "repartition": {c: f"Répartition n°{self.repartitions} — {c}" for c in cles},
                "resume": f"Résumé factice n°{self.repartitions}",
            }
        return {}

    def fermer(self) -> None:
        self.serveur.shutdown()
        self.serveur.server_close()


@pytest.fixture(scope="module")
def faux_ollama():
    faux = FauxOllama()
    try:
        yield faux
    finally:
        faux.fermer()


@pytest.fixture
def dossier() -> Path:
    """A fresh, empty folder per test: the server's SQLite file and its log."""
    d = _RACINE_BASES / uuid.uuid4().hex[:10]
    d.mkdir(parents=True, exist_ok=False)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def nav():
    profil = Path(tempfile.mkdtemp(prefix="e2e-s3-"))
    navigateur = None
    try:
        navigateur = Navigateur(_NAVIGATEUR, profil / "profil")
        yield navigateur
    finally:
        if navigateur is not None:
            navigateur.fermer()
        shutil.rmtree(profil, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _python_sur_base(dossier: Path, code: str, *args: str) -> str:
    env = dict(os.environ, APP_DB_PATH=str(dossier / "e2e.db"), PYTHONUTF8="1")
    res = subprocess.run([sys.executable, "-c", code, *args], cwd=_RACINE, env=env,
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


def _poster_depuis_la_page(nav: Navigateur, action: str, champs: list[tuple[str, str]]) -> None:
    """Reaches a POST-rendered screen the way the previous wizard step would:
    a same-origin form, submitted by a REAL click on its button (so Origin,
    cookies and CSRF checks are the browser's own)."""
    nav.evaluer(
        "(function(){var f=document.createElement('form');f.method='post';"
        "f.action=" + json.dumps(action) + ";f.id='e2e-amorce';"
        "f.style.cssText='position:fixed;top:0;left:0;z-index:2147483647;background:#fff';"
        "var c=" + json.dumps(champs) + ";"
        "c.forEach(function(p){var i=document.createElement('input');i.type='hidden';"
        "i.name=p[0];i.value=p[1];f.appendChild(i);});"
        "var b=document.createElement('button');b.type='submit';b.id='e2e-amorce-go';"
        "b.textContent='go';b.style.cssText='width:80px;height:40px';f.appendChild(b);"
        "document.body.appendChild(f);return true;})()"
    )
    nav.cliquer_et_attendre("#e2e-amorce-go")


def _posts_journal(dossier: Path, suffixe: str) -> int:
    """POST requests to a path ending with `suffixe`, as uvicorn logged them
    (server-side truth, independent of what the browser reports)."""
    journal = (dossier / "uvicorn.log").read_text(encoding="utf-8", errors="replace")
    return len(re.findall(r'"POST [^" ]*' + re.escape(suffixe) + r' HTTP', journal))


_SEED_MISSION = r"""
from app.db import SessionLocal, init_db
from app.models import Mission
init_db()
db = SessionLocal()
m = Mission(name="E2E stories 3", is_draft=False)
db.add(m); db.commit()
print(m.id)
"""

_SEED_JOBS = r"""
import json, sys
from app.db import SessionLocal
from app.models import InterviewSegmentJob
db = SessionLocal()
mid, tok, spec = int(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])
for pos, statut, texte, tours in spec:
    db.add(InterviewSegmentJob(session_token=tok, kind="libre_turns", mission_id=mid,
                               position=pos, status=statut, text=texte,
                               turns_result=({"turns": tours, "identity": {}} if tours else None),
                               error=("extraction impossible" if statut == "failed" else None)))
db.commit()
print("ok")
"""


# --------------------------------------------------------------------------- #
# C1 — Story 3.4 B-1: « Structurer le texte disponible »
# --------------------------------------------------------------------------- #
_SEED_C1 = r"""
import json
from app.db import SessionLocal, init_db
from app.models import Interview, Mission
init_db()
db = SessionLocal()
m = Mission(name="E2E B-1", is_draft=False)
db.add(m); db.flush()
iv = Interview(mission_id=m.id, interviewee_name="Témoin bloqué", mode="libre", status="done",
               structuration_status="a_transcrire", tranches_manquantes=1,
               raw_transcript="Texte réellement transcrit avant la panne d'une tranche.")
db.add(iv); db.commit()
print(json.dumps({"iid": iv.id}))
"""

_LIRE_C1 = r"""
import json, sys
from app.db import SessionLocal
from app.models import Interview
db = SessionLocal()
iv = db.get(Interview, int(sys.argv[1]))
print(json.dumps({"statut": iv.structuration_status, "tours": len(iv.turns)}))
"""


def test_c1_structurer_le_texte_disponible_sort_du_blocage(
    dossier: Path, faux_ollama: FauxOllama, nav: Navigateur,
) -> None:
    iid = json.loads(_python_sur_base(dossier, _SEED_C1))["iid"]
    with serveur_uvicorn(dossier, AI_PROVIDER="ollama", OLLAMA_HOST=faux_ollama.hote,
                         OLLAMA_TIMEOUT="30") as base:
        nav.naviguer(f"{base}/interviews/{iid}")
        assert nav.evaluer("!!document.querySelector('[data-structuration-bloquee]')"), (
            "panneau « bloqué » absent : " + nav.texte()[:600])
        avant = faux_ollama.extractions
        # Real click; the driver accepts the confirm() dialog.
        nav.cliquer_et_attendre("[data-structuration-bloquee] form button[type=submit]")
        assert re.search(rf"/interviews/{iid}$", nav.url()), nav.url()  # not ?structuration=refusee
        nav.drainer()
        assert not nav.erreurs_http(), nav.erreurs_http()  # CSRF/origin passed
        assert "Relance non prise en compte" not in nav.texte()
        # a_transcrire -> a_traiter, then the LIVE queue runs (fake Ollama): the
        # status may already have moved on; it must never stay a_transcrire.
        etat = json.loads(_python_sur_base(dossier, _LIRE_C1, str(iid)))
        assert etat["statut"] in ("a_traiter", "en_cours", "fait"), etat
        _attendre(nav, lambda: json.loads(
            _python_sur_base(dossier, _LIRE_C1, str(iid)))["statut"] == "fait",
            60, "structuration terminée")
        etat = json.loads(_python_sur_base(dossier, _LIRE_C1, str(iid)))
        assert etat["tours"] > 0 and faux_ollama.extractions > avant, etat


# --------------------------------------------------------------------------- #
# C2 — Story 3.4 B-9/F1: synthesis -> Retour -> synthesis -> double Enregistrer
# --------------------------------------------------------------------------- #
_JETON_C2 = "tok-e2e-s3-retour"

_LIRE_C2 = r"""
import json, sys
from app.db import SessionLocal
from app.models import Interview, InterviewSegmentJob
from app.services.structuration_libre import peut_relancer
db = SessionLocal()
mid, tok = int(sys.argv[1]), sys.argv[2]
ivs = db.query(Interview).filter(Interview.mission_id == mid).all()
print(json.dumps({
    "interviews": [{"id": i.id, "token": i.segment_token, "statut": i.structuration_status,
                    "tours": len(i.turns), "relancable": peut_relancer(i)} for i in ivs],
    "jobs": db.query(InterviewSegmentJob).filter(InterviewSegmentJob.session_token == tok).count(),
}))
"""


def test_c2_retour_puis_double_enregistrer_cree_un_seul_entretien(
    dossier: Path, faux_ollama: FauxOllama, nav: Navigateur,
) -> None:
    mid = _python_sur_base(dossier, _SEED_MISSION)
    # A failed tranche on the session: kept, it would make the interview relançable.
    _python_sur_base(dossier, _SEED_JOBS, mid, _JETON_C2, json.dumps(
        [[0, "failed", "Tranche 0 en échec.", None]]))
    with serveur_uvicorn(dossier, AI_PROVIDER="ollama", OLLAMA_HOST=faux_ollama.hote,
                         OLLAMA_TIMEOUT="30") as base:
        nav.naviguer(f"{base}/missions/{mid}")
        # Step 2 of the wizard (turns review), as the previous step posts it.
        _poster_depuis_la_page(nav, f"/missions/{mid}/interviews/record-libre/retour-tours", [
            ("transcript", "Bonjour, je suis Alice. Nous parlons de l'organisation."),
            ("interviewee_name", "Alice Retour"),
            ("session_token", _JETON_C2),
            ("turn_interlocuteur", "Consultant·e"), ("turn_question", "Présentez-vous ?"),
            ("turn_remarque", ""), ("turn_section_title", "Ouverture"),
            ("turn_interlocuteur", "Interviewé·e"), ("turn_question", ""),
            ("turn_remarque", "Je suis Alice, responsable d'équipe."), ("turn_section_title", ""),
        ])
        jeton = "document.querySelector('input[name=session_token]').value"
        continuer = "#turns-review-form .form-actions button.btn-primary[type=submit]"
        enregistrer = "form[action$='/record-libre/confirm'] .form-actions button.btn-primary"
        assert nav.evaluer(jeton) == _JETON_C2

        # Synthesis (fake Ollama answers the repartition).
        nav.cliquer_et_attendre(continuer)
        assert nav.evaluer(f"!!document.querySelector(\"{enregistrer}\")"), nav.texte()[:600]
        assert nav.evaluer(jeton) == _JETON_C2
        # « ← Revenir aux questions/réponses » (retour-tours): the token survives.
        nav.cliquer_et_attendre("button[formaction$='/record-libre/retour-tours']")
        assert nav.evaluer("!!document.getElementById('turns-review-form')"), nav.texte()[:600]
        assert nav.evaluer(jeton) == _JETON_C2, "jeton perdu au retour (F1)"
        # Back to the synthesis.
        nav.cliquer_et_attendre(continuer)
        assert nav.evaluer(jeton) == _JETON_C2
        assert faux_ollama.repartitions >= 2

        # Real double click on « Enregistrer l'entretien ».
        nav.double_cliquer(enregistrer)
        _attendre(nav, lambda: re.search(r"/interviews/\d+(\?.*)?$", nav.url()) is not None,
                  30, "fiche de l'entretien")
        time.sleep(2)  # leave a second POST, if any, the time to land
        nav.drainer()
        assert not nav.erreurs_http(), nav.erreurs_http()
        etat = json.loads(_python_sur_base(dossier, _LIRE_C2, mid, _JETON_C2))
        assert len(etat["interviews"]) == 1, (etat, _posts_journal(dossier, "/record-libre/confirm"))
        iv = etat["interviews"][0]
        assert iv["token"] == _JETON_C2 and iv["tours"] == 2, iv
        assert etat["jobs"] == 0, "tranches de la session non consommées"
        assert iv["relancable"] is False, iv


# --------------------------------------------------------------------------- #
# C3 — Story 3.3 A-8: wait page exit panel, « Finaliser avec le texte disponible »
# --------------------------------------------------------------------------- #
_JETON_C3 = "tok-e2e-s3-attente"

# Status polls fail in the page (connection cut simulated by a fetch stub):
# EXIT_AFTER_FAILS (6) failures in a row show the exit panel (~30 s).
_COUPER_SUIVI = r"""
(function(){
  window.__echecsSuivi = 0;
  var f0 = window.fetch;
  window.fetch = function (u, o) {
    if (String(u).indexOf('/interviews/segment-jobs/status') >= 0) {
      window.__echecsSuivi++;
      return Promise.reject(new TypeError('coupure simulée'));
    }
    return f0.apply(this, arguments);
  };
  return true;
})()
"""


def test_c3_finaliser_avec_le_texte_disponible_depuis_l_attente(
    dossier: Path, faux_ollama: FauxOllama, nav: Navigateur,
) -> None:
    mid = _python_sur_base(dossier, _SEED_MISSION)
    with serveur_uvicorn(dossier, AI_PROVIDER="ollama", OLLAMA_HOST=faux_ollama.hote,
                         OLLAMA_TIMEOUT="30") as base:
        # Pending tranche seeded AFTER startup (the reconciliation would fail it).
        _python_sur_base(dossier, _SEED_JOBS, mid, _JETON_C3, json.dumps(
            [[0, "done", "Tranche 0.", [{"interlocuteur": "Interviewé·e", "remarque": "T0"}]],
             [1, "pending", "Tranche 1 encore en attente.", None]]))
        nav.naviguer(f"{base}/missions/{mid}")
        _poster_depuis_la_page(nav, f"/missions/{mid}/interviews/record-libre", [
            ("transcript", "Texte brut reçu par la page d'attente."),
            ("session_token", _JETON_C3),
        ])
        assert nav.evaluer("!!document.getElementById('seg-exit')"), nav.texte()[:600]
        assert nav.evaluer("document.getElementById('seg-exit').hidden"), "panneau ouvert d'office"
        nav.evaluer(_COUPER_SUIVI)
        _attendre(nav, lambda: nav.evaluer("!document.getElementById('seg-exit').hidden"),
                  90, "panneau de sortie (suivi muet)")
        assert nav.evaluer("window.__echecsSuivi") >= 6
        assert nav.evaluer("!document.getElementById('seg-exit-finalize').disabled")

        # Probes that survive the navigation (sessionStorage of the tab): the
        # button state right after each click (our listener runs after the
        # page's own) and every call to form.submit().
        nav.evaluer(r"""(function(){
          sessionStorage.setItem('e2e-clics', '[]');
          sessionStorage.setItem('e2e-submits', '0');
          var b = document.getElementById('seg-exit-finalize');
          b.addEventListener('click', function () {
            var l = JSON.parse(sessionStorage.getItem('e2e-clics'));
            l.push(b.disabled); sessionStorage.setItem('e2e-clics', JSON.stringify(l));
          });
          var s0 = HTMLFormElement.prototype.submit;
          HTMLFormElement.prototype.submit = function () {
            sessionStorage.setItem('e2e-submits',
              String(Number(sessionStorage.getItem('e2e-submits')) + 1));
            return s0.apply(this, arguments);
          };
          return true;
        })()""")
        nav.emuler_latence(1500)  # keeps the wait page up while the 2nd click lands
        nav.double_cliquer("#seg-exit-finalize")  # confirm() accepted by the driver
        _attendre(nav, lambda: nav.evaluer(
            "!!document.getElementById('turns-review-form')"), 60, "revue des tours")
        nav.emuler_latence(0)
        assert nav.url().endswith("/record-libre/from-jobs"), nav.url()
        clics = json.loads(nav.evaluer("sessionStorage.getItem('e2e-clics')"))
        assert clics and clics[0] is True, f"bouton actif après le clic : {clics}"
        assert nav.evaluer("sessionStorage.getItem('e2e-submits')") == "1"
        time.sleep(1.5)
        nav.drainer()
        assert not nav.erreurs_http(), nav.erreurs_http()
        assert _posts_journal(dossier, "/record-libre/from-jobs") == 1, (
            "double soumission de la finalisation")


# --------------------------------------------------------------------------- #
# C4 — triage B-2: a late tranche reaches the live « Répartition » preview
# --------------------------------------------------------------------------- #
_JETON_C4 = "tok-e2e-s3-apercu"

_MARQUER_FAIT = r"""
import sys
from app.db import SessionLocal
from app.models import InterviewSegmentJob
db = SessionLocal()
j = db.query(InterviewSegmentJob).filter(InterviewSegmentJob.session_token == sys.argv[1],
                                         InterviewSegmentJob.position == 1).one()
j.status = "done"; j.error = None
j.turns_result = {"turns": [{"interlocuteur": "Interviewé·e", "remarque": "Propos tranche UN"}],
                  "identity": {}}
db.commit()
print("ok")
"""


@pytest.mark.skipif(
    not audio_transcribe.is_available(),
    reason="faster-whisper absent : l'écran d'enregistrement n'a pas son script",
)
def test_c4_tranche_tardive_inseree_a_sa_place_dans_l_apercu(
    dossier: Path, faux_ollama: FauxOllama, nav: Navigateur,
) -> None:
    mid = _python_sur_base(dossier, _SEED_MISSION)
    _python_sur_base(dossier, _SEED_JOBS, mid, _JETON_C4, json.dumps([
        [0, "done", "T0", [{"interlocuteur": "Interviewé·e", "remarque": "Propos tranche ZERO"}]],
        [1, "failed", "T1", None],
        [2, "done", "T2", [{"interlocuteur": "Interviewé·e", "remarque": "Propos tranche DEUX"}]],
    ]))
    with serveur_uvicorn(dossier, AI_PROVIDER="ollama", OLLAMA_HOST=faux_ollama.hote,
                         OLLAMA_TIMEOUT="30") as base:
        ecran = f"{base}/missions/{mid}/interviews/record-libre"
        nav.naviguer(ecran)
        # The live session token comes back through a local draft (real key of
        # rec_draft.js), resumed by a real click — no microphone involved.
        brouillon = {"transcript": "Texte déjà transcrit de la session.",
                     "sessionToken": _JETON_C4, "coveredLen": 10,
                     "segmentJobPosition": 3, "savedAt": int(time.time() * 1000)}
        nav.evaluer(f"localStorage.setItem('i2d:brouillon-libre:{mid}', "
                    f"{json.dumps(json.dumps(brouillon))}); true")
        nav.naviguer(ecran)
        _attendre(nav, lambda: nav.evaluer(
            "!document.getElementById('rec-draft-banner').hidden"), 15, "bannière de reprise")
        nav.cliquer("#rec-draft-restore")
        assert nav.evaluer("document.getElementById('rec-session-token').value") == _JETON_C4
        nav.cliquer(".rec-tab[data-tab='repartition']")
        apercu = "document.getElementById('rec-repartition').innerText"
        _attendre(nav, lambda: "DEUX" in nav.evaluer(apercu), 20, "aperçu t0/t2")
        texte = nav.evaluer(apercu)
        assert "Propos tranche ZERO" in texte, texte
        assert "Propos tranche UN" not in texte, texte
        assert texte.index("ZERO") < texte.index("DEUX"), texte

        _python_sur_base(dossier, _MARQUER_FAIT, _JETON_C4)
        _attendre(nav, lambda: "Propos tranche UN" in nav.evaluer(apercu), 20,
                  "tranche 1 tardive dans l'aperçu")
        time.sleep(11)  # two more polls: the late tranche must not come back twice
        texte = nav.evaluer(apercu)
        assert texte.count("Propos tranche UN") == 1, texte
        assert texte.count("Propos tranche ZERO") == 1 and texte.count("Propos tranche DEUX") == 1
        assert texte.index("ZERO") < texte.index("Propos tranche UN") < texte.index("DEUX"), texte
        nav.drainer()
        assert not nav.erreurs_http(), nav.erreurs_http()


# --------------------------------------------------------------------------- #
# C5 — Story 3.5: deleting the LAST backup part leaves a visible trace
# --------------------------------------------------------------------------- #
_SEED_C5 = r"""
import json
from app.db import RECORDINGS_DIR, SessionLocal, init_db
from app.models import Interview, InterviewTurn, Mission
init_db()
db = SessionLocal()
m = Mission(name="E2E S3.5", is_draft=False)
db.add(m); db.flush()
noms = [f"{m.id}_e2e_s35_tranche{i}.webm" for i in range(3)]
for nom in noms:
    (RECORDINGS_DIR / nom).write_bytes(b"audio")
iv = Interview(mission_id=m.id, mode="libre", status="done", interviewee_name="Témoin S35",
               raw_transcript="Texte transcrit.", structuration_status="fait",
               audio_segments=[{"filename": n, "position": i} for i, n in enumerate(noms)],
               audio_backup_path=noms[-1])
db.add(iv); db.flush()
db.add(InterviewTurn(interview_id=iv.id, position=0, interlocuteur="Témoin S35",
                     question=None, remarque="Un propos.", section_title=None))
db.commit()
print(json.dumps({"mid": m.id, "iid": iv.id, "noms": noms,
                  "dossier": str(RECORDINGS_DIR)}))
"""

_LIRE_C5 = r"""
import json, sys
from app.db import SessionLocal
from app.models import Interview
db = SessionLocal()
iv = db.get(Interview, int(sys.argv[1]))
print(json.dumps({"supprimees": iv.parties_supprimees,
                  "segments": [s["filename"] for s in iv.audio_segments or []]}))
"""


def test_c5_supprimer_la_derniere_partie_laisse_une_trace(
    dossier: Path, nav: Navigateur,
) -> None:
    graine = json.loads(_python_sur_base(dossier, _SEED_C5))
    mid, iid, noms = graine["mid"], graine["iid"], graine["noms"]
    derniere = Path(graine["dossier"]) / noms[-1]
    with serveur_uvicorn(dossier) as base:
        nav.naviguer(f"{base}/interviews/{iid}")
        assert not nav.evaluer("!!document.querySelector('[data-parties-supprimees]')"), (
            "trace présente avant toute suppression")

        nav.naviguer(f"{base}/missions/{mid}")
        nav.cliquer(".tab[data-tab='backup']")
        bouton = f"#backup form[action$='/{noms[-1]}/delete'] button[type=submit]"
        assert nav.evaluer(f"!!document.querySelector(\"{bouton}\")"), nav.texte()[:600]
        # Real click; the form's onsubmit confirm() is accepted by the driver.
        nav.cliquer_et_attendre(bouton)
        assert re.search(rf"/missions/{mid}(#backup)?$", nav.url()), nav.url()
        nav.drainer()
        assert not nav.erreurs_http(), nav.erreurs_http()  # CSRF/origin passed
        assert not derniere.exists(), "fichier de la dernière partie encore sur disque"
        etat = json.loads(_python_sur_base(dossier, _LIRE_C5, str(iid)))
        assert etat == {"supprimees": 1, "segments": noms[:-1]}, etat

        nav.naviguer(f"{base}/interviews/{iid}")
        assert nav.evaluer(
            "!!document.querySelector('[data-parties-supprimees=\"1\"]')"), nav.texte()[:800]
        trace = nav.evaluer(
            "document.querySelector('[data-parties-supprimees=\"1\"]').innerText")
        assert "1 partie" in trace and "1 parties" not in trace, trace
        nav.drainer()
        assert not nav.erreurs_http(), nav.erreurs_http()
