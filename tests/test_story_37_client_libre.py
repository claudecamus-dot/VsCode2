"""Story 3.7 -- petits defauts du client d'enregistrement libre (AC3.7.1 a AC3.7.7).

Tout le JS est EXECUTE (node) : `rec_draft.js` et `rec_audio_source.js` dans un bac
dedie, `record_libre.html` par son script inline COMPLET (harnais partage). Aucune
assertion d'ordre n'est faite sur le texte brut d'un template.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import InterviewSegmentJob

try:
    from _harnais_ordre_js import _HOTE, _PRELUDE, REC_DRAFT, RACINE, jouer_ecran, script_inline
except ImportError:  # pragma: no cover
    from tests._harnais_ordre_js import _HOTE, _PRELUDE, REC_DRAFT, RACINE, jouer_ecran, script_inline

LIBRE = RACINE / "app" / "templates" / "interviews" / "record_libre.html"
AUDIO = RACINE / "app" / "static" / "rec_audio_source.js"


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


def _node(script: str) -> dict:
    if shutil.which("node") is None:
        pytest.fail("node est requis : preuve par execution du JS.")
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


# --------------------------------------------------------------------------- #
# AC3.7.1 (A-12) et AC3.7.2 (A-13) : rec_draft.js, plusieurs onglets
# --------------------------------------------------------------------------- #
_DRAFT = r"""
const store = {};
const local = {
  get length() { return Object.keys(store).length; },
  key: function (i) { return Object.keys(store)[i] || null; },
  getItem: function (k) { return k in store ? store[k] : null; },
  setItem: function (k, v) { store[k] = String(v); },
  removeItem: function (k) { delete store[k]; }
};
function onglet(opts) {
  opts = opts || {};
  const ss = Object.assign({}, opts.ss || {}); const win = { localStorage: local, sessionStorage: {
    getItem: function (k) { return k in ss ? ss[k] : null; }, setItem: function (k, v) { ss[k] = String(v); } },
    performance: { getEntriesByType: function () { return [{ type: opts.nav || 'navigate' }]; } } };
  delete require.cache[require.resolve(%(chemin)s)];
  const m = require(%(chemin)s);
  const enveloppe = { _ss: ss };
  Object.keys(m).forEach(function (n) {
    enveloppe[n] = typeof m[n] === 'function'
      ? function () { global.window = win; return m[n].apply(null, arguments); } : m[n];
  });
  return enveloppe;
}
const T0 = 1000000000000;
const out = {};
const A = onglet(), B = onglet(), C = onglet();
A.sauver(7, { transcript: 'texte A' }, T0);
B.sauver(7, { transcript: 'texte B' }, T0 + 1000);
out.a_garde_le_sien = A.chargerPropre(7, T0 + 2000).transcript;
out.b_garde_le_sien = B.chargerPropre(7, T0 + 2000).transcript;
out.deux_cles = Object.keys(store).filter(function (k) { return k.indexOf('i2d:brouillon-libre:7') === 0; }).length;
// onglet neuf : propose le plus recent des autres onglets
out.vivants_non_proposes = C.charger(7, T0 + 2000) === null;   // A et B battent encore
const orphelin = C.charger(7, T0 + 60000);
out.orphelin = orphelin && orphelin.transcript;
out.orphelin_pas_dans_sauver = C.sauver(7, orphelin, T0 + 3000) && !('_cle' in JSON.parse(store[Object.keys(store).filter(function (k) { return k.indexOf('i2d:brouillon-libre:7') === 0; }).pop()]));
C.adopter(7, orphelin);
out.b_retire_apres_adoption = B.chargerPropre(7, T0 + 60000) === null;
out.a_intact = A.chargerPropre(7, T0 + 60000).transcript;
out.est_propre = [C.estPropre(7, orphelin), C.estPropre(7, C.chargerPropre(7, T0 + 60000))];
// M3 : onglet DUPLIQUE (sessionStorage copie, battement frais) -> identifiant neuf ; rechargement -> meme
const Z = onglet(); Z.battre();
const idZ = Z._ss['i2d:brouillon-onglet'];
const D = onglet({ ss: Z._ss, nav: 'navigate' }); D.battre();
out.dup_navigate = D._ss['i2d:brouillon-onglet'] !== idZ;
const R = onglet({ ss: Z._ss, nav: 'reload' }); R.battre();
out.reload_garde_l_id = R._ss['i2d:brouillon-onglet'] === idZ;
// elagage : au plus 5 brouillons d'autres onglets conserves
for (let i = 0; i < 8; i++) onglet().sauver(21, { transcript: 'o' + i }, T0 + i);
onglet().sauver(21, { transcript: 'propre' }, T0 + 100);
out.elagage = Object.keys(store).filter(function (k) { return k.indexOf('i2d:brouillon-libre:21') === 0; }).length;
// cle d'avant 3.7 (sans onglet) toujours lue
store['i2d:brouillon-libre:9'] = JSON.stringify({ transcript: 'heritage', savedAt: T0 });
out.heritage = (onglet().charger(9, T0 + 1) || {}).transcript;
// A-13 : meme ordre (missionId, jeton, now) pour marquerEnvoye et effacerSiEnvoye
A.marquerEnvoye(7, 'tok-a', T0 + 5000);
const e = A.chargerPropre(7, T0 + 5001);
out.ordre = [e.envoiJeton, e.envoyeLe];
// effacerSiEnvoye depuis un AUTRE onglet (la fiche s'ouvre ailleurs) purge le bon brouillon
out.purge_autre_onglet = onglet().effacerSiEnvoye(7, 'tok-a', T0 + 6000);
out.a_purge = A.chargerPropre(7, T0 + 6001) === null;
out.mauvais_jeton = (function () {
  A.sauver(7, { transcript: 'x' }, T0); A.marquerEnvoye(7, 'tok-1', T0 + 1);
  return onglet().effacerSiEnvoye(7, 'tok-2', T0 + 2) === false && A.chargerPropre(7, T0 + 3) !== null;
})();
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def draft() -> dict:
    return _node(_DRAFT % {"chemin": json.dumps(str(REC_DRAFT))})


def test_37_1_deux_onglets_n_ecrasent_pas_leur_brouillon(draft: dict) -> None:
    assert draft["a_garde_le_sien"] == "texte A" and draft["b_garde_le_sien"] == "texte B"
    assert draft["deux_cles"] == 2


def test_37_1_un_onglet_neuf_reprend_le_plus_recent_puis_l_adopte(draft: dict) -> None:
    assert draft["vivants_non_proposes"] is True, "B2 : le brouillon d'un onglet vivant ne se propose pas"
    assert draft["est_propre"] == [False, True]
    assert draft["orphelin"] == "texte B"
    assert draft["orphelin_pas_dans_sauver"] is True, "`_cle` ne doit pas etre ecrite dans le brouillon"
    assert draft["b_retire_apres_adoption"] is True
    assert draft["a_intact"] == "texte A", "l'adoption ne doit pas toucher le brouillon d'un autre onglet"


def test_37_1_la_cle_d_avant_la_story_est_encore_lue(draft: dict) -> None:
    assert draft["heritage"] == "heritage"


def test_37_2_marquer_envoye_et_effacer_si_envoye_ont_le_meme_ordre(draft: dict) -> None:
    assert draft["ordre"][0] == "tok-a" and isinstance(draft["ordre"][1], int)
    assert draft["purge_autre_onglet"] is True and draft["a_purge"] is True
    assert draft["mauvais_jeton"] is True


# --------------------------------------------------------------------------- #
# AC3.7.3 (A-14), AC3.7.6 (A-19) : le script COMPLET de record_libre.html
# --------------------------------------------------------------------------- #
def test_37_3_segments_perdus_restaures_sont_a_relancer_et_survivent_au_brouillon() -> None:
    perdus = [{"id": 1, "reason": "reseau", "blocking": True, "attente": False, "segId": "s1"},
              {"id": 2, "reason": "delai", "blocking": False, "attente": True, "segId": "s2"}]
    avant = ("jouer(" + json.dumps(
        "recDraft.sauver(19, " + json.dumps({"transcript": "abc [segment 1]", "sessionToken": "tk",
                                              "segmentsPerdus": perdus}) + ")") + ");")
    sortie = jouer_ecran(LIBRE, avant=avant, apres=r"""
const out = { erreur: erreur, messages: [], champs: {} };
el('rec-draft-restore')._on.click();
bac.confirm = function (m) { out.messages.push(m); return false; };
el('record-form')._on.submit({ target: el('record-form'), preventDefault: function () {} });
const cles = Object.keys(store).filter(function (k) { return k.indexOf('i2d:brouillon-libre:19') === 0; });
out.cles = cles.length;
out.persistes = JSON.parse(store[cles[0]]).segmentsPerdus.length;
console.log(JSON.stringify(out));
""")
    assert sortie["erreur"] is None, sortie["erreur"]
    assert any("1 segment(s) à relancer" in m for m in sortie["messages"]), sortie["messages"]
    assert sortie["cles"] == 1 and sortie["persistes"] == 2, sortie


def test_37_6_piste_muette_et_contexte_suspendu_alertent_l_ecran() -> None:
    avant = "let capt = null; bac.i2dAudioSource = { on: function (o) { capt = o; }, supported: function () { return false; }, tabActive: function () { return false; }, release: function () {} };"
    sortie = jouer_ecran(LIBRE, avant=avant, apres=r"""
const out = { erreur: erreur, branche: !!capt && typeof capt.issue === 'function' };
capt.issue('muted');
out.muted = { texte: el('rec-nospeech-text').textContent, visible: !el('rec-nospeech-banner').hidden };
capt.issue('resume');
out.resume = el('rec-nospeech-text').textContent;
capt.issue(null);
out.leve = { texte: el('rec-nospeech-text').textContent, cache: el('rec-nospeech-banner').hidden };
console.log(JSON.stringify(out));
""")
    assert sortie["erreur"] is None, sortie["erreur"]
    assert sortie["branche"] is True, "l'ecran n'enregistre pas de rappel `issue`"
    assert "micro est coupé" in sortie["muted"]["texte"] and sortie["muted"]["visible"] is True
    assert "contexte audio suspendu" in sortie["resume"]
    assert sortie["leve"] == {"texte": "", "cache": True}


_AUDIO = r"""
const fs = require('fs'), vm = require('vm');
function piste(muted) {
  const p = { muted: muted, readyState: 'live', _l: {}, stop: function () {},
    addEventListener: function (n, f) { (p._l[n] = p._l[n] || []).push(f); } };
  return p;
}
const clics = [];
function monter(opts) {
  const issues = [];
  const pm = piste(opts.muted);
  const flux = { getAudioTracks: function () { return [pm]; }, getTracks: function () { return [pm]; } };
  function Ctx() {
    this.state = 'suspended'; this._l = {};
    this.addEventListener = function () {};
    const ctxSelf = this;
    this.resume = function () {
      if (opts.resumePendant) return new Promise(function () {});
      if (opts.resumeKo && !ctxSelf.clique) return Promise.reject(new Error('refuse'));
      ctxSelf.state = 'running'; return Promise.resolve(); };
    const noeud = function () { return { connect: function () {}, fftSize: 0, getByteTimeDomainData: function () {} }; };
    this.createMediaStreamDestination = function () { return { stream: {} }; };
    this.createMediaStreamSource = noeud; this.createAnalyser = noeud;
    this.close = function () {};
  }
  const win = { AudioContext: Ctx };
  const sandbox = { window: win, document: { addEventListener: function (n, f) { if (n === 'click') clics.push(f); } }, navigator: { mediaDevices: {
      getDisplayMedia: function () {}, getUserMedia: function () { return Promise.resolve(flux); } } },
    setInterval: function () { return 1; }, clearInterval: function () {},
    setTimeout: function (f, ms) { return setTimeout(f, ms >= 5000 ? 10 : ms); }, clearTimeout: clearTimeout,
    console: console, Promise: Promise, Uint8Array: Uint8Array, Error: Error, Math: Math };
  vm.runInNewContext(fs.readFileSync(%(chemin)s, 'utf-8'), sandbox);
  win.i2dAudioSource.on({ issue: function (k) { issues.push(k); } });
  return { src: win.i2dAudioSource, issues: issues, piste: pm };
}
(async () => {
  const out = {};
  const a = monter({ muted: true });
  await a.src.acquire(false);
  out.muet_au_depart = a.issues.slice();
  a.piste._l.unmute[0](); out.apres_unmute = a.issues.slice();
  a.piste._l.mute[0](); out.remute = a.issues.slice();
  const b = monter({ muted: false, resumeKo: true });
  await b.src.preflightMic();
  await new Promise(function (r) { setTimeout(r, 20); });
  out.resume_ko = b.issues.slice();
  const c = monter({ muted: false });
  await c.src.preflightMic();
  await new Promise(function (r) { setTimeout(r, 20); });
  out.resume_ok = c.issues.slice();
  const d = monter({ muted: true, resumeKo: true });
  await d.src.preflightMic();
  await new Promise(function (r) { setTimeout(r, 20); });
  out.deux_causes = d.issues.slice();
  d.piste._l.unmute[0]();
  out.muted_leve_resume_reste = d.issues.slice();
  const e = monter({ muted: false, resumePendant: true });
  await e.src.preflightMic();
  await new Promise(function (r) { setTimeout(r, 60); });
  out.resume_pendant = e.issues.slice();
  e.src.release();
  out.apres_release = e.issues.slice();
  const f = monter({ muted: false, resumeKo: true });
  await f.src.preflightMic();
  await new Promise(function (r) { setTimeout(r, 20); });
  out.avant_clic = f.issues.slice();
  console.log(JSON.stringify(out));
})();
"""


def test_37_6_la_source_audio_signale_piste_muette_et_resume_en_echec() -> None:
    s = _node(_AUDIO % {"chemin": json.dumps(str(AUDIO))})
    assert s["muet_au_depart"] == ["muted"]
    assert s["apres_unmute"] == ["muted", None] and s["remute"] == ["muted", None, "muted"]
    assert s["resume_ko"] == ["resume"]
    assert s["resume_ok"] == [], "un contexte qui repart sans incident ne signale rien"
    assert s["deux_causes"] == ["muted"] and s["muted_leve_resume_reste"] == ["muted", "resume"]
    assert s["resume_pendant"] == ["resume"] and s["apres_release"] == ["resume", None]
    assert s["avant_clic"] == ["resume"], s["avant_clic"]


# --------------------------------------------------------------------------- #
# AC3.7.7 (B-2 reste) : serveur (empreinte, sans_ligne) + client (aperçu live)
# --------------------------------------------------------------------------- #
def _tour(r: str) -> dict:
    return {"interlocuteur": "A", "question": None, "remarque": r, "section_title": None}


def _job(jeton: str, pos: int, statut: str, rem: str | None = None) -> InterviewSegmentJob:
    return InterviewSegmentJob(session_token=jeton, position=pos, kind="libre_turns", status=statut,
                               text=f"t{pos}", turns_result={"turns": [_tour(rem)], "identity": {}} if rem else None)


def _poll(client: TestClient, jeton: str, since: int, manq: str | None = None,
          verif: str | None = None) -> dict:
    params = {"session_token": jeton, "since": since}
    if manq is not None:
        params["manquantes"] = manq
    if verif is not None:
        params["verif"] = verif
    r = client.get("/interviews/segment-jobs/turns", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_37_7_le_serveur_annonce_l_empreinte_d_une_tranche_reextraite() -> None:
    client, jeton = TestClient(app), "s37-rev"
    with SessionLocal() as db:
        db.add_all([_job(jeton, 0, "done", "t0"), _job(jeton, 1, "done", "t1")])
        db.commit()
    r = _poll(client, jeton, 0)
    rev0 = {t["position"]: t["rev"] for t in r["tranches"]}
    assert set(rev0) == {0, 1} and "revs" not in r
    assert "revs" not in _poll(client, jeton, 2), "pas de verif demandee : aucune relecture"
    r = _poll(client, jeton, 2, verif="0,1")
    assert r["revs"] == {"0": rev0[0], "1": rev0[1]}
    with SessionLocal() as db:   # re-extraction : meme position, nouveau resultat
        job = db.scalar(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == jeton, InterviewSegmentJob.position == 0))
        job.turns_result = {"turns": [_tour("t0 reecrit apres coup")], "identity": {}}
        db.commit()
    r = _poll(client, jeton, 2, verif="0,1")
    assert r["revs"]["0"] != rev0[0] and r["revs"]["1"] == rev0[1]
    r = _poll(client, jeton, 2, "0")
    assert [t["turns"][0]["remarque"] for t in r["tranches"]] == ["t0 reecrit apres coup"]


def test_37_7_le_serveur_dit_quelles_positions_demandees_n_ont_aucune_ligne() -> None:
    client, jeton = TestClient(app), "s37-sans-ligne"
    with SessionLocal() as db:
        db.add_all([_job(jeton, 0, "done", "t0"), _job(jeton, 2, "done", "t2"), _job(jeton, 3, "failed")])
        db.commit()
    r = _poll(client, jeton, 4, "1,3")
    assert r["sans_ligne"] == [1], "3 a une ligne (failed), 1 n'en a aucune"
    assert "sans_ligne" not in _poll(client, jeton, 4)


_SCENARIO = r"""
const polls = [], appels = [];
g.setInterval = function (fn) { if (fn && fn.name === 'pollRepartition') polls.push(fn); return 1; };
g.recFetch = function (url) {
  if (String(url).indexOf('/interviews/segment-jobs/turns') === 0) {
    return new Promise(function (ok) { appels.push({ url: String(url), ok: ok }); });
  }
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } });
};
"""
_OUTILS = r"""
async function tick() { for (let i = 0; i < 30; i++) await Promise.resolve(); }
function tour(r) { return { interlocuteur: 'A', question: null, remarque: r, section_title: null }; }
function repondre(i, data) { appels[i].ok({ ok: true, json: function () { return Promise.resolve(data); } }); }
function html() { return String(elements['rec-repartition'] ? elements['rec-repartition'].innerHTML : ''); }
async function sonder(data) { polls[0](); await tick(); const i = appels.length - 1; const url = appels[i].url; repondre(i, data); await tick(); return url; }
"""
_SANS_LIGNE = r"""
(async () => {
  const out = { erreur: erreur, urls: [] };
  out.urls.push(await sonder({ turns: [], next_since: 3, tranches: [
    { position: 0, turns: [tour('t0')], rev: 'a' }, { position: 2, turns: [tour('t2')], rev: 'c' }] }));
  for (let i = 0; i < 17; i++) out.urls.push(await sonder({ turns: [], tranches: [], next_since: 3, sans_ligne: [1] }));
  console.log(JSON.stringify(out));
})();
"""
_REEXTRAITE = r"""
(async () => {
  const out = { erreur: erreur, urls: [] };
  out.urls.push(await sonder({ turns: [], next_since: 2, tranches: [
    { position: 0, turns: [tour('ancien')], rev: 'a' }, { position: 1, turns: [tour('t1')], rev: 'b' }] }));
  out.urls.push(await sonder({ turns: [], next_since: 2, revs: { '0': 'a', '1': 'b' } }));
  out.urls.push(await sonder({ turns: [], next_since: 2, revs: { '0': 'z', '1': 'b' } }));
  out.urls.push(await sonder({ turns: [], next_since: 2, revs: { '0': 'z', '1': 'b' },
    tranches: [{ position: 0, turns: [tour('nouveau')], rev: 'z' }] }));
  out.html = html();
  out.urls.push(await sonder({ turns: [], next_since: 2, revs: { '0': 'z', '1': 'b' } }));
  console.log(JSON.stringify(out));
})();
"""


def _jouer_apercu(apres: str) -> dict:
    js = script_inline(LIBRE)
    assert js.count('var sessionToken = "";') == 1
    js = js.replace('var sessionToken = "";', 'var sessionToken = "jeton-37";')
    corps = (
        "jouer(%s);\n%s\ntry { jouer(%s); } catch (e) { erreur = e.name + ': ' + e.message; }\n%s\n%s\n"
        % (json.dumps(REC_DRAFT.read_text(encoding="utf-8")), _SCENARIO, json.dumps(js), _OUTILS, apres)
    )
    return _node(_PRELUDE % {"hote": json.dumps(_HOTE), "corps": corps})


def test_37_7_une_position_sans_ligne_n_est_plus_redemandee_sans_fin() -> None:
    r = _jouer_apercu(_SANS_LIGNE)
    assert r["erreur"] is None, r["erreur"]
    # M6 : toujours redemandee pendant ~70 s (POST relance a 15 s puis 45 s), puis abandonnee
    assert all("manquantes=1" in u for u in r["urls"][1:15]), r["urls"]
    assert all("manquantes" not in u for u in r["urls"][15:]), r["urls"]


def test_37_7_une_tranche_reextraite_apres_reception_est_rafraichie_une_fois() -> None:
    r = _jouer_apercu(_REEXTRAITE)
    assert r["erreur"] is None, r["erreur"]
    u = r["urls"]
    assert "manquantes" not in u[1] and "manquantes" not in u[2]
    assert "manquantes=0" in u[3], u
    assert " : nouveau<" in r["html"] and " : ancien<" not in r["html"], r["html"]
    assert "manquantes" not in u[4], "la tranche rafraichie ne doit pas etre redemandee"


def test_37_4_l_echec_terminal_de_la_suite_automatique_est_classe_echec() -> None:
    """A-16 : « La suite automatique n'a pas pu demarrer » n'etait pas dans la liste fermee."""
    import re
    js = script_inline(LIBRE)
    m = re.search(r"var STATUT_ECHEC_MOTIFS = \[.*?\];", js, re.S)
    assert m, "STATUT_ECHEC_MOTIFS introuvable"
    r = _node(m.group(0) + r"""
const est = function (t) { return STATUT_ECHEC_MOTIFS.some(function (x) { return t.indexOf(x) !== -1; }); };
console.log(JSON.stringify({
  suite: est('La suite automatique n’a pas pu démarrer ; recharge la page ou relance l’extraction.'),
  neutre: est('Enregistrement en cours…') }));
""")
    assert r == {"suite": True, "neutre": False}


def test_37_m3_onglet_duplique_prend_un_identifiant_neuf_pas_un_rechargement(draft: dict) -> None:
    assert draft["dup_navigate"] is True and draft["reload_garde_l_id"] is True


def test_37_elagage_borne_les_brouillons_des_autres_onglets(draft: dict) -> None:
    assert draft["elagage"] <= 6, draft["elagage"]


def test_37_m7_une_reextraction_de_meme_longueur_est_detectee() -> None:
    client, jeton = TestClient(app), "s37-meme-longueur"
    with SessionLocal() as db:
        db.add(_job(jeton, 0, "done", "abcd"))
        db.commit()
    avant = _poll(client, jeton, 1, verif="0")["revs"]["0"]
    with SessionLocal() as db:
        job = db.scalar(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == jeton, InterviewSegmentJob.position == 0))
        job.turns_result = {"turns": [_tour("wxyz")], "identity": {}}   # meme longueur
        db.commit()
    apres = _poll(client, jeton, 1, verif="0")["revs"]["0"]
    assert apres != avant


def test_37_m8_la_verification_est_bornee_et_ignore_les_tranches_non_terminees() -> None:
    client, jeton = TestClient(app), "s37-borne"
    with SessionLocal() as db:
        db.add_all([_job(jeton, i, "done", f"t{i}") for i in range(30)] + [_job(jeton, 30, "failed")])
        db.commit()
    r = _poll(client, jeton, 31, verif=",".join(str(i) for i in range(31)))
    assert len(r["revs"]) <= 20, len(r["revs"])
    assert "30" not in r["revs"]
    assert "revs" not in _poll(TestClient(app), "", 5, verif="0,1"), "sans jeton : rien n'est lu"


# --------------------------------------------------------------------------- #
# B1, M4, identite : le script COMPLET (aucune doublure de persistDraft)
# --------------------------------------------------------------------------- #
_CHAMPS = r"""
const champs = {};
const _q = g.document.querySelector;
g.document.querySelector = function (sel) {
  const m = /name="([a-z_]+)"/.exec(String(sel));
  if (m) { if (!champs[m[1]]) champs[m[1]] = { value: '' }; return champs[m[1]]; }
  return doublure;
};
"""


def _brouillon_etranger(**extra) -> str:
    brouillon = {"transcript": "texte d'un autre onglet", "sessionToken": "tk", "savedAt": int(time.time() * 1000)}
    brouillon.update(extra)
    return ("jouer(" + json.dumps("window.localStorage.setItem('i2d:brouillon-libre:19:autre', "
            + json.dumps(json.dumps(brouillon)) + ")") + ");")


def test_37_b1_un_quota_plein_ne_supprime_pas_la_seule_copie() -> None:
    avant = _brouillon_etranger()
    sortie = jouer_ecran(LIBRE, avant=avant, apres=r"""
const out = { erreur: erreur };
const ecrire = bac.window.localStorage.setItem;
bac.window.localStorage.setItem = function (k, v) {
  if (k.indexOf('i2d:brouillon-libre') === 0) throw new Error('QuotaExceededError');
  ecrire(k, v);
};
el('rec-draft-restore')._on.click();
out.cles = Object.keys(store).filter(function (k) { return k.indexOf('i2d:brouillon-libre') === 0; });
console.log(JSON.stringify(out));
""")
    assert sortie["erreur"] is None, sortie["erreur"]
    assert sortie["cles"] == ["i2d:brouillon-libre:19:autre"], sortie["cles"]


def test_37_m4_et_identite_l_adoption_garde_journal_et_identite() -> None:
    avant = _brouillon_etranger(journal=[{"t": 5, "type": "backup-ko"}], identite={
        "interviewee_name": "Alice", "interviewee_role": "DSI", "interviewee_entity": "ACME",
        "interview_date": "2026-11-01"})
    sortie = jouer_ecran(LIBRE, avant=avant, apres=_CHAMPS + r"""
champs.interview_date = { value: '2026-11-18' };   // saisie deja faite : jamais ecrasee
const out = { erreur: erreur };
el('rec-draft-restore')._on.click();
out.champs = { n: champs.interviewee_name.value, r: champs.interviewee_role.value,
               e: champs.interviewee_entity.value, d: champs.interview_date.value };
const cles = Object.keys(store).filter(function (k) { return k.indexOf('i2d:brouillon-libre') === 0; });
out.cles = cles.length;
const etat = JSON.parse(store[cles[0]]);
out.journal = etat.journal; out.identite = etat.identite;
console.log(JSON.stringify(out));
""")
    assert sortie["erreur"] is None, sortie["erreur"]
    assert sortie["champs"] == {"n": "Alice", "r": "DSI", "e": "ACME", "d": "2026-11-18"}, sortie
    assert sortie["cles"] == 1
    assert sortie["journal"] == [{"t": 5, "type": "backup-ko"}], sortie["journal"]
    assert sortie["identite"]["interviewee_name"] == "Alice"


def test_37_oublier_une_copie_d_un_autre_onglet_la_retire_et_le_texte_le_dit() -> None:
    sortie = jouer_ecran(LIBRE, avant=_brouillon_etranger(), apres=r"""
const out = { erreur: erreur, texte: el('rec-draft-text').textContent };
el('rec-draft-discard')._on.click();
out.cles = Object.keys(store).filter(function (k) { return k.indexOf('i2d:brouillon-libre') === 0; });
console.log(JSON.stringify(out));
""")
    assert sortie["erreur"] is None, sortie["erreur"]
    assert "autre onglet" in sortie["texte"], sortie["texte"]
    assert sortie["cles"] == []


def test_37_m9_seuls_les_perdus_bloquants_restaures_comptent() -> None:
    avant = _brouillon_etranger(segmentsPerdus=[
        {"id": 2, "reason": "d", "blocking": False, "attente": True, "segId": "s2"}])
    sortie = jouer_ecran(LIBRE, avant=avant, apres=r"""
const out = { erreur: erreur, messages: [] };
el('rec-draft-restore')._on.click();
bac.confirm = function (m) { out.messages.push(m); return false; };
el('record-form')._on.submit({ target: el('record-form'), preventDefault: function () {} });
console.log(JSON.stringify(out));
""")
    assert sortie["erreur"] is None, sortie["erreur"]
    assert not any("segment(s) à relancer" in m for m in sortie["messages"]), sortie["messages"]
