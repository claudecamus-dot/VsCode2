"""Triage B-2 (user decision 2026-11-17) -- late tranches reach the live preview.

During a recording the "Repartition" tab polls `/interviews/segment-jobs/turns`
with a position cursor (`since`). The cursor moves past a FAILED (or stale)
tranche so the preview is not frozen; when that tranche is later recovered and
turns `done`, it sits BELOW the cursor and its turns never reached the preview.

Fix: the client lists the positions below `since` it never received
(`manquantes=1,4`); the server returns those `done` tranches once, grouped by
position (`tranches`), and the client inserts them in position order.

- server side: TestClient on the real route;
- client side: the WHOLE inline script of `record_libre.html` executed under
  node (`_harnais_ordre_js`), `pollRepartition` captured from its real
  `setInterval` -- no function extraction, so the new state really lives in
  the IIFE where its callers are.
- `record.html` (structured answers) polls `/segment-jobs/answers` WITHOUT a
  cursor (full re-merge each poll): a late tranche is already included there;
  the server test below pins it.
"""
from __future__ import annotations

import json
from datetime import datetime
import os
import shutil
import subprocess
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import InterviewSegmentJob

try:
    from _harnais_ordre_js import _HOTE, _PRELUDE, REC_DRAFT, RACINE, script_inline
except ImportError:  # pragma: no cover
    from tests._harnais_ordre_js import _HOTE, _PRELUDE, REC_DRAFT, RACINE, script_inline

LIBRE = RACINE / "app" / "templates" / "interviews" / "record_libre.html"


def setup_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def teardown_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _tour(remarque: str) -> dict:
    return {"interlocuteur": "A", "question": None, "remarque": remarque, "section_title": None}


def _job(jeton: str, position: int, statut: str, remarque: str | None = None,
         kind: str = "libre_turns", answers: dict | None = None) -> InterviewSegmentJob:
    resultat = None
    if kind == "libre_turns" and remarque:
        resultat = {"turns": [_tour(remarque)], "identity": {}}
    if kind == "answers" and answers:
        resultat = answers
    return InterviewSegmentJob(session_token=jeton, position=position, kind=kind,
                               status=statut, text=f"texte {position}", turns_result=resultat)


def _terminer(jeton: str, position: int, resultat: dict) -> None:
    with SessionLocal() as db:
        job = db.scalar(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == jeton, InterviewSegmentJob.position == position))
        job.status = "done"
        job.error = None
        job.turns_result = resultat
        db.commit()


def _poll(client: TestClient, jeton: str, since: int, manquantes: str | None = None) -> dict:
    params = {"session_token": jeton, "since": since}
    if manquantes is not None:
        params["manquantes"] = manquantes
    r = client.get("/interviews/segment-jobs/turns", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _remarques(data: dict) -> list:
    return [t["remarque"] for t in data["turns"]]


# --------------------------------------------------------------------------- #
# Server side
# --------------------------------------------------------------------------- #
def test_tranche_echouee_puis_recuperee_renvoyee_une_fois(client: TestClient) -> None:
    """Tranche 1 failed, cursor moves to 3; tranche 1 turns done -> the next poll
    (client lists it as missing) returns its turns once; the following poll
    (client now has it) does not return it again. The cursor never freezes."""
    jeton = "b2-tardive-echec"
    with SessionLocal() as db:
        db.add_all([_job(jeton, 0, "done", "t0"), _job(jeton, 1, "failed"),
                    _job(jeton, 2, "done", "t2")])
        db.commit()

    r = _poll(client, jeton, 0)
    assert _remarques(r) == ["t0", "t2"] and r["next_since"] == 3
    assert [t["position"] for t in r["tranches"]] == [0, 2]

    # Still failed: asking for it returns nothing, and the cursor stays put.
    r = _poll(client, jeton, 3, "1")
    assert r["turns"] == [] and r.get("tranches", []) == [] and r["next_since"] == 3

    _terminer(jeton, 1, {"turns": [_tour("t1")], "identity": {}})
    r = _poll(client, jeton, 3, "1")
    # Story 3.7 : chaque tranche porte aussi son empreinte `rev` (entier).
    assert [(t["position"], t["turns"]) for t in r["tranches"]] == [(1, [_tour("t1")])]
    assert isinstance(r["tranches"][0]["rev"], int)
    assert _remarques(r) == ["t1"] and r["next_since"] == 3

    r = _poll(client, jeton, 3)
    assert r["turns"] == [] and r.get("tranches", []) == [] and r["next_since"] == 3


def test_tranche_perimee_finie_apres_le_curseur_est_rattrapee(client: TestClient) -> None:
    """A stale pending tranche does not hold the cursor; when it finishes later
    it is returned on request, alongside new tranches past the cursor."""
    jeton = "b2-tardive-perimee"
    with SessionLocal() as db:
        vieux = _job(jeton, 0, "pending")
        vieux.created_at = datetime(2000, 1, 1)
        db.add_all([vieux, _job(jeton, 1, "done", "t1")])
        db.commit()
    r = _poll(client, jeton, 0)
    assert _remarques(r) == ["t1"] and r["next_since"] == 2

    _terminer(jeton, 0, {"turns": [_tour("t0")], "identity": {}})
    with SessionLocal() as db:
        db.add(_job(jeton, 2, "done", "t2"))
        db.commit()
    r = _poll(client, jeton, 2, "0")
    assert [t["position"] for t in r["tranches"]] == [0, 2]
    assert r["next_since"] == 3


@pytest.mark.parametrize("brut", ["x", "-1", "²", "5", "1,,", " 1 ", "9" * 5000])
def test_manquantes_invalides_ou_hors_borne_ignorees(client: TestClient, brut: str) -> None:
    """Garbage never 500s; positions >= since are never fetched as 'late'."""
    jeton = "b2-tardive-garde-" + str(abs(hash(brut)))
    with SessionLocal() as db:
        db.add_all([_job(jeton, 1, "done", "t1"), _job(jeton, 5, "done", "t5")])
        db.commit()
    r = _poll(client, jeton, 3, brut)
    positions = [t["position"] for t in r.get("tranches", [])]
    assert positions == ([1, 5] if brut.strip(" ,") == "1" else [5])


def test_manquantes_invalides_ne_consomment_pas_le_plafond(client: TestClient) -> None:
    """Review 2026-11-17: the 500-entry cap is applied AFTER filtering, so junk
    entries placed first cannot push a real late position out of the request."""
    jeton = "b2-tardive-plafond"
    with SessionLocal() as db:
        db.add_all([_job(jeton, 1, "done", "t1"), _job(jeton, 5, "done", "t5")])
        db.commit()
    r = _poll(client, jeton, 3, ",".join(["x"] * 600 + ["1"]))
    assert [t["position"] for t in r.get("tranches", [])] == [1, 5]


def test_ecran_structure_inclut_une_tranche_finie_tardivement(client: TestClient) -> None:
    """record.html: `/segment-jobs/answers` has no cursor, so a late tranche is
    merged as soon as it is done (no fix needed on that screen -- pinned here)."""
    jeton = "b2-tardive-answers"
    with SessionLocal() as db:
        db.add_all([
            _job(jeton, 0, "done", kind="answers", answers={"answers": {"7": {"text": "r0", "verbatims": []}}}),
            _job(jeton, 1, "failed", kind="answers"),
            _job(jeton, 2, "done", kind="answers", answers={"answers": {"9": {"text": "r2", "verbatims": []}}}),
        ])
        db.commit()
    _terminer(jeton, 1, {"answers": {"8": {"text": "r1", "verbatims": []}}})
    r = client.get("/interviews/segment-jobs/answers", params={"session_token": jeton}).json()
    assert {k: v["text"] for k, v in r["answers"].items()} == {"7": "r0", "8": "r1", "9": "r2"}


# --------------------------------------------------------------------------- #
# Client side: the WHOLE record_libre.html script under node
# --------------------------------------------------------------------------- #
_SCENARIO = r"""
const polls = [];
const appels = [];
g.setInterval = function (fn, ms) { if (fn && fn.name === 'pollRepartition') polls.push(fn); return 1; };
g.recFetch = function (url) {
  if (String(url).indexOf('/interviews/segment-jobs/turns') === 0) {
    return new Promise(function (ok) { appels.push({ url: String(url), ok: ok }); });
  }
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } });
};
"""

_APRES = r"""
async function tick() { for (let i = 0; i < 30; i++) await Promise.resolve(); }
function tour(r) { return { interlocuteur: 'A', question: null, remarque: r, section_title: null }; }
function repondre(i, data) { appels[i].ok({ ok: true, json: function () { return Promise.resolve(data); } }); }
function html() { return String(elements['rec-repartition'] ? elements['rec-repartition'].innerHTML : ''); }
(async () => {
  const out = { erreur: erreur, polls: polls.length };
  const poll = polls[0];
  // 1) tranche 0 and 2 done, tranche 1 failed: cursor jumps to 3.
  poll(); await tick();
  repondre(0, { turns: [tour('t0'), tour('t2')], next_since: 3,
                tranches: [{ position: 0, turns: [tour('t0')] }, { position: 2, turns: [tour('t2')] }] });
  await tick();
  out.html1 = html();
  // 2) tranche 1 still failed: asked as missing, nothing comes back.
  poll(); await tick();
  out.url2 = appels[1].url;
  repondre(1, { turns: [], tranches: [], next_since: 3 });
  await tick();
  // 3) tranche 1 recovered: returned, must be inserted BETWEEN t0 and t2.
  poll(); await tick();
  out.url3 = appels[2].url;
  repondre(2, { turns: [tour('t1')], tranches: [{ position: 1, turns: [tour('t1')] }], next_since: 3 });
  await tick();
  out.html3 = html();
  // 4) nothing missing any more; even a server re-sending tranche 2 must not duplicate it.
  poll(); await tick();
  out.url4 = appels[3].url;
  repondre(3, { turns: [tour('t2')], tranches: [{ position: 2, turns: [tour('t2')] }], next_since: 3 });
  await tick();
  out.html4 = html();
  console.log(JSON.stringify(out));
})();
"""


def _jouer_libre() -> dict:
    if shutil.which("node") is None:
        pytest.fail("node est requis : preuve par execution du script de l'ecran.")
    js = script_inline(LIBRE)
    # The session token comes from Jinja (neutralised to "" by script_inline):
    # give it a value so the real `pollRepartition` actually polls.
    assert js.count('var sessionToken = "";') == 1, "sessionToken declaration changed shape"
    js = js.replace('var sessionToken = "";', 'var sessionToken = "jeton-b2";')
    corps = (
        "jouer(%s);\n%s\ntry { jouer(%s); } catch (e) { erreur = e.name + ': ' + e.message; }\n%s\n"
        % (json.dumps(REC_DRAFT.read_text(encoding="utf-8")), _SCENARIO, json.dumps(js), _APRES)
    )
    script = _PRELUDE % {"hote": json.dumps(_HOTE), "corps": corps}
    fd, chemin = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        res = subprocess.run(["node", chemin], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
    finally:
        os.unlink(chemin)
    assert res.returncode == 0, res.stderr[:3000]
    return json.loads(res.stdout.strip().splitlines()[-1])


def _ordre(html: str) -> list:
    """Markers t0/t1/t2 in display order (one entry per occurrence)."""
    trouves = []
    for m in ("t0", "t1", "t2"):
        motif, depart = " : " + m + "<", 0
        while (i := html.find(motif, depart)) != -1:
            trouves.append((i, m))
            depart = i + 1
    return [m for _, m in sorted(trouves)]


def test_ecran_libre_tranche_tardive_inseree_a_sa_place_sans_doublon() -> None:
    r = _jouer_libre()
    assert r["erreur"] is None, r["erreur"]
    assert r["polls"] == 1, "pollRepartition no longer registered via setInterval"

    assert _ordre(r["html1"]) == ["t0", "t2"]
    assert "manquantes=1" in r["url2"] and "since=3" in r["url2"], r["url2"]
    assert "manquantes=1" in r["url3"], r["url3"]

    assert _ordre(r["html3"]) == ["t0", "t1", "t2"], r["html3"]
    assert "manquantes" not in r["url4"], r["url4"]
    for m in ("t0", "t1", "t2"):
        assert r["html4"].count(" : " + m + "<") == 1, r["html4"]
    assert _ordre(r["html4"]) == ["t0", "t1", "t2"]
