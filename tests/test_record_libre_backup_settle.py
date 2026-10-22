"""EH-5 (jumeau libre) : uploadBackup de record_libre.html, JS EXECUTE sous node.

(a) updateSubmitState leve apres settle() -> pendingBackups -1 (truthy, gate casse).
(b) refreshBackupList leve APRES l'acquittement mais AVANT settle() -> re-POST d'une
    tranche deja rangee -> position en double dans backupSegments (audio_segments).
"""
from __future__ import annotations

from pathlib import Path

try:
    from test_repartition_live import _fonction, _node, _sans_commentaires
except ImportError:  # pragma: no cover
    from tests.test_repartition_live import _fonction, _node, _sans_commentaires

_ROOT = Path(__file__).resolve().parent.parent
LIBRE = _ROOT / "app" / "templates" / "interviews" / "record_libre.html"

_SCRIPT = r"""
const timers = []; const pend = []; const posts = []; const window = {};
function FormData() {} FormData.prototype.append = function () {};
function File() {}
function setTimeout(fn, d) { timers.push({ fn: fn, delai: d }); return timers.length; }
async function tick() { for (let i = 0; i < 30; i++) await Promise.resolve(); }
function recFetch() {
  posts.push(1);
  return new Promise(function (res) {
    pend.push(function (r) { res({ ok: r.ok, status: r.status, json: function () { return Promise.resolve(r.data); } }); });
  });
}
let pendingBackups = 0, backupGeneration = 1, nextBackupPosition = 0, backupConfirmes = 0;
let armed = false, boomSubmit = %(boomSubmit)s, boomList = %(boomList)s;
const UPLOAD_TIMEOUT_MS = 10000000;
const backupStatusEl = { textContent: '' }, backupPathHidden = { value: '' }, audioSegmentsHidden = { value: '' };
const backupSegments = [], backupPerdus = [];
let boomSignal = %(boomSignal)s, boomGarder = %(boomGarder)s, garderAppels = 0;
function noterEvenement() {} function signalerMissionAbsente() { if (boomSignal > 0) { boomSignal--; throw new Error('boomSignal'); } } function persistDraft() {}
function garderTrancheEnLocal() { garderAppels++; if (boomGarder > 0) { boomGarder--; throw new Error('boomGarder'); } } function refreshBackupStatus() {}
function refreshBackupList() { if (boomList > 0) { boomList--; throw new Error('boomList'); } }
function updateSubmitState() { if (armed && boomSubmit > 0) { boomSubmit--; throw new Error('boomSubmit'); } }
%(rec_retry)s
const RETRY_PLAN = window.recRetryPlan;
%(fonction)s
"""

_SCENARIO = r"""
(async () => {
  uploadBackup({ type: 'audio/webm', size: 1 }, 0, undefined, undefined);
  await tick();
  const pendant = pendingBackups;
  armed = true;
  let n = 0;
  const ok = function () { n++; return { ok: true, status: 200, data: { path: 'f' + n + '.webm' } }; };
  pend[0](ok()); await tick();
  for (let k = 0; k < 5 && timers.length > k; k++) { timers[k].fn(); await tick(); if (pend[k + 1]) { pend[k + 1](ok()); await tick(); } }
  console.log(JSON.stringify({ pendant: pendant, pending: pendingBackups, posts: posts.length,
    positions: backupSegments.map(function (s) { return s.position; }) }));
})();
"""


def _lancer(boom_submit: int, boom_list: int, boom_signal: int = 0, boom_garder: int = 0, reponses=None) -> dict:
    src = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    script = _SCRIPT % {
        "boomSubmit": boom_submit,
        "boomList": boom_list,
        "boomSignal": boom_signal,
        "boomGarder": boom_garder,
        "rec_retry": (_ROOT / "app" / "static" / "rec_retry.js").read_text(encoding="utf-8"),
        "fonction": _fonction(src, "uploadBackup"),
    } + (_SCENARIO if reponses is None else _SCENARIO_SEQ.replace("%%REP%%", reponses))
    return _node(script)


def test_libre_settle_qui_leve_ne_double_decremente_pas():
    out = _lancer(boom_submit=1, boom_list=0)
    assert out["pendant"] == 1
    assert out["pending"] == 0, out
    assert out["posts"] == 1, out
    assert out["positions"] == [0], out


def test_libre_throw_avant_settle_ne_reposte_pas_et_pas_de_position_en_double():
    out = _lancer(boom_submit=0, boom_list=1)
    assert out["pending"] == 0, out      # le gate est libere malgre le throw
    assert out["posts"] == 1, out        # pas de re-POST d'une tranche acquittee
    assert out["positions"] == [0], out  # pas de position en double


def test_libre_nominal_inchange():
    out = _lancer(boom_submit=0, boom_list=0)
    assert out == {"pendant": 1, "pending": 0, "posts": 1, "positions": [0]}


_SCENARIO_SEQ = r"""
(async () => {
  const REP = %%REP%%;
  uploadBackup({ type: 'audio/webm', size: 1 }, 0, undefined, undefined);
  await tick();
  const pendant = pendingBackups;
  for (let k = 0; k < REP.length; k++) {
    if (!pend[k]) { if (timers[k - 1]) { timers[k - 1].fn(); await tick(); } }
    if (pend[k]) { pend[k](REP[k]); await tick(); }
  }
  console.log(JSON.stringify({ pendant: pendant, pending: pendingBackups, posts: posts.length,
    garder: garderAppels, perdus: backupPerdus.length, statut: backupStatusEl.textContent,
    positions: backupSegments.map(function (s) { return s.position; }) }));
})();
"""
_OK = "{ ok: true, status: 200, data: { path: 'a.webm' } }"
_KO = "{ ok: false, status: 500, data: { error: 'x' } }"
_KO4 = "[" + ",".join([_KO] * 4) + "]"


def test_libre_chemin_echec_qui_leve_libere_le_gate_et_garde_une_fois():
    out = _lancer(0, 1, reponses=_KO4)  # refreshBackupList leve dans le chemin d'echec
    assert out["pending"] == 0, out
    assert out["garder"] == 1, out


def test_libre_garder_qui_leve_libere_le_gate():
    out = _lancer(0, 0, boom_garder=1, reponses=_KO4)
    assert out["pending"] == 0, out
    assert "NON SAUVEGARD" in out["statut"], out  # la perte est affichee malgre le throw


def test_libre_erreur_avant_acquittement_relance_bien():
    # signalerMissionAbsente leve AVANT backupSegments.push : le re-POST est correct
    out = _lancer(0, 0, boom_signal=1, reponses="[" + _OK + "," + _OK + "]")
    assert out["posts"] == 2 and out["pending"] == 0 and out["positions"] == [0], out


def test_libre_5xx_puis_relance_reussie_compte_une_fois():
    out = _lancer(0, 0, reponses="[" + _KO + "," + _OK + "]")
    out.pop("statut")
    assert out == {"pendant": 1, "pending": 0, "posts": 2, "garder": 0, "perdus": 0, "positions": [0]}, out


def test_libre_blob_vide_n_est_pas_poste_ni_position_ni_perte():
    # J1 : tranche finale de 0 octet -> pas de POST, pas de position, pas de « perte ».
    scen = r"""
(async () => {
  uploadBackup({ type: 'audio/webm', size: 0 }, 0, undefined, undefined);
  await tick();
  console.log(JSON.stringify({ pending: pendingBackups, posts: posts.length, pos: nextBackupPosition,
    perdus: backupPerdus.length, garder: garderAppels, segs: backupSegments.length, statut: backupStatusEl.textContent }));
})();
"""
    src = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    script = "let recordingActive = false;\n" + _SCRIPT % {
        "boomSubmit": 0, "boomList": 0, "boomSignal": 0, "boomGarder": 0,
        "rec_retry": (_ROOT / "app" / "static" / "rec_retry.js").read_text(encoding="utf-8"),
        "fonction": _fonction(src, "uploadBackup"),
    } + scen
    out = _node(script)
    assert "Aucune donnée audio" in out.pop("statut"), out  # J1b : signal visible
    assert out == {"pending": 0, "posts": 0, "pos": 0, "perdus": 0, "garder": 0, "segs": 0}, out


def test_libre_settle_segment_updatesubmitstate_qui_leve_n_empeche_pas_le_job_final():
    # J2 (jumeau libre) : uploadSegment de record_libre.html.
    src = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    script = r"""
const timers = []; const pend = []; const textes = []; const window = {};
function FormData() {} FormData.prototype.append = function () {};
function File() {}
function setTimeout(fn, d) { timers.push({ fn: fn, delai: d }); return timers.length; }
async function tick() { for (let i = 0; i < 30; i++) await Promise.resolve(); }
function recFetch() {
  return new Promise(function (res) {
    pend.push(function (r) { res({ ok: r.ok, status: r.status, json: function () { return Promise.resolve(r.data); } }); });
  });
}
let pendingSegments = 0, backupGeneration = 1, recordingActive = false, armed = false, submits = 0;
const TRANSCRIBE_TIMEOUT_MS = 10000000, BUSY_BACKOFF_MAX_MS = 1, BUSY_BACKOFF_BASE_MS = 1;
const statusEl = { textContent: '' };
const Date = { now: function () { return 0; } };
function appendTranscript(t) { textes.push(t); } function replaceLostMarker() {} function resetNoSpeech() {}
function noteNoSpeech() {} function keepLostSegment() {} function refreshReplayStatus() {} function copieServeurAcquittee() { return false; }
function updateSubmitState() { if (armed) throw new Error('boomUpd'); }
function maybeSubmitFinalJob() { submits++; }
""" + (_ROOT / "app" / "static" / "rec_retry.js").read_text(encoding="utf-8") + r"""
const RETRY_PLAN = window.recRetryPlan;
""" + _fonction(src, "uploadSegment") + r"""
(async () => {
  let settled = 0;
  uploadSegment({ type: 'audio/webm' }, 0, undefined, undefined, function () { settled++; });
  await tick();
  armed = true;
  pend[0]({ ok: true, status: 200, data: { text: 'hello' } }); await tick();
  console.log(JSON.stringify({ pending: pendingSegments, onSettle: settled, submits: submits, textes: textes.length }));
})();
"""
    out = _node(script)
    assert out == {"pending": 0, "onSettle": 1, "submits": 1, "textes": 1}, out


def test_libre_affichage_qui_leve_ne_pretend_pas_que_la_copie_locale_a_echoue():
    # J3 : refreshBackupList leve ALORS QUE garderTrancheEnLocal a reussi -> pas de « NON SAUVEGARDEE ».
    out = _lancer(0, 1, reponses=_KO4)
    assert out["garder"] == 1 and out["pending"] == 0, out
    assert "NON SAUVEGARD" not in out["statut"], out
