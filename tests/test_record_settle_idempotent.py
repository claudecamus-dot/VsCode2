"""EH-5 : le settle() d'uploadSegment (record.html) est idempotent, JS EXECUTE sous node.

Défaut : `settle()` décrémentait pendingSegments puis appelait updateSubmitState /
maybeSubmitFinalJob / onSettle sans try/finally. Si l'un lève, le `.catch` de la chaîne
rappelle retryOrGiveUp -> relance -> second settle() : double décrément pour un seul
incrément (pendingSegments = -1, gardes `> 0` fausses). Le vrai uploadSegment est extrait
du template et joué avec recFetch/timers pilotés.
"""
from __future__ import annotations

from pathlib import Path

try:
    from test_repartition_live import _fonction, _node, _sans_commentaires
except ImportError:  # pragma: no cover
    from tests.test_repartition_live import _fonction, _node, _sans_commentaires

_ROOT = Path(__file__).resolve().parent.parent
RECORD = _ROOT / "app" / "templates" / "interviews" / "record.html"

_SCRIPT = r"""
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
const OK = { ok: true, status: 200, data: { text: 'hello' } };
let pendingSegments = 0, recGeneration = 1, segmentsRejoues = 0;
const TRANSCRIBE_TIMEOUT_MS = 10000000;
const statusEl = { textContent: '' };
const Date = { now: function () { return 0; } };
function appendTranscript(t) { textes.push(t); }
function replaceLostMarker() {} function resetNoSpeech() {} function noteNoSpeech() {}
function keepLostSegment() {} function refreshReplayStatus() {}
let submits = 0, boom = %(boom)s;
function updateSubmitState() {}
function maybeSubmitFinalJob() { submits++; if (boom > 0) { boom--; throw new Error('boom'); } }
%(rec_retry)s
const RETRY_PLAN = window.recRetryPlan;
%(fonction)s
"""

_SCENARIO = r"""
(async () => {
  let settled = 0;
  uploadSegment({ type: 'audio/webm' }, 0, undefined, undefined, function () { settled++; });
  await tick();
  const pendant = pendingSegments;
  pend[0](OK); await tick();
  // relance eventuelle declenchee par le .catch : on la joue
  for (let k = 0; k < 5 && timers.length > k; k++) { timers[k].fn(); await tick(); if (pend[k + 1]) { pend[k + 1](OK); await tick(); } }
  console.log(JSON.stringify({ pendant: pendant, pending: pendingSegments, onSettle: settled, textes: textes.length }));
})();
"""


def _lancer(boom: int) -> dict:
    src = _sans_commentaires(RECORD.read_text(encoding="utf-8"))
    script = _SCRIPT % {
        "boom": boom,
        "rec_retry": (_ROOT / "app" / "static" / "rec_retry.js").read_text(encoding="utf-8"),
        "fonction": _fonction(src, "uploadSegment"),
    } + _SCENARIO
    return _node(script)


def test_settle_qui_leve_ne_double_decremente_pas_pendingsegments():
    out = _lancer(boom=1)  # maybeSubmitFinalJob leve a la 1re fois
    assert out["pendant"] == 1
    assert out["pending"] == 0, out   # jamais -1
    assert out["onSettle"] == 1, out
    assert out["textes"] == 1, out    # pas de re-POST d'un segment deja accepte


def test_settle_nominal_inchange():
    out = _lancer(boom=0)
    assert out == {"pendant": 1, "pending": 0, "onSettle": 1, "textes": 1}


_SCRIPT_BACKUP = r"""
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
let pendingBackups = 0, recGeneration = 1, armed = false, boom = %(boom)s;
const UPLOAD_TIMEOUT_MS = 10000000;
const backupStatusEl = { textContent: '' }, backupPathHidden = { value: '' };
const audioPlayerEl = {}, audioDownloadEl = {}, audioPlaybackEl = {};
const seen = [];
function signalerMissionAbsente() {} function oublierBackupLocal() {}
let boomBrancher = %(boomBrancher)s, boomOffrir = %(boomOffrir)s;
function brancherRepliOrphelin() { if (boomBrancher > 0) { boomBrancher--; throw new Error('boomBrancher'); } }
function urlAudio(p) { return p; } function offrirBackupEnLocal() { if (boomOffrir > 0) { boomOffrir--; throw new Error('boomOffrir'); } return true; }
function updateSubmitState() {
  seen.push(pendingBackups);
  if (armed && boom > 0) { boom--; throw new Error('boom'); }
}
%(rec_retry)s
const RETRY_PLAN = window.recRetryPlan;
%(fonction)s
"""

_SCENARIO_BACKUP = r"""
(async () => {
  uploadBackup({ type: 'audio/webm' }, 0, undefined);
  await tick();
  const pendant = pendingBackups;
  armed = true;
  pend[0]({ ok: true, status: 200, data: { path: 'a.webm' } }); await tick();
  for (let k = 0; k < 5 && timers.length > k; k++) { timers[k].fn(); await tick(); if (pend[k + 1]) { pend[k + 1]({ ok: true, status: 200, data: { path: 'a.webm' } }); await tick(); } }
  console.log(JSON.stringify({ pendant: pendant, pending: pendingBackups, posts: posts.length, min: Math.min.apply(null, seen) }));
})();
"""


def _lancer_backup(boom: int, brancher: int = 0, offrir: int = 0, reponses=None) -> dict:
    src = _sans_commentaires(RECORD.read_text(encoding="utf-8"))
    script = _SCRIPT_BACKUP % {
        "boom": boom,
        "boomBrancher": brancher,
        "boomOffrir": offrir,
        "rec_retry": (_ROOT / "app" / "static" / "rec_retry.js").read_text(encoding="utf-8"),
        "fonction": _fonction(src, "uploadBackup"),
    } + (_SCENARIO_BACKUP if reponses is None else _SCENARIO_BACKUP_SEQ.replace("%%REP%%", reponses))
    return _node(script)


def test_backup_settle_qui_leve_ne_double_decremente_pas_pendingbackups():
    out = _lancer_backup(boom=1)  # updateSubmitState leve au 1er appel apres reglement
    assert out["pendant"] == 1
    assert out["pending"] == 0, out   # jamais -1 : le gate (> 0) ne s'ouvre pas trop tot
    assert out["min"] >= 0, out
    assert out["posts"] == 1, out     # pas de re-POST d'une sauvegarde deja acceptee


def test_backup_settle_nominal_inchange():
    out = _lancer_backup(boom=0)
    assert out == {"pendant": 1, "pending": 0, "posts": 1, "min": 0}


_SCENARIO_BACKUP_SEQ = r"""
(async () => {
  const REP = %%REP%%;
  uploadBackup({ type: 'audio/webm' }, 0, undefined);
  await tick();
  const pendant = pendingBackups;
  for (let k = 0; k < REP.length; k++) {
    if (!pend[k]) { if (timers[k - 1]) { timers[k - 1].fn(); await tick(); } }
    if (pend[k]) { pend[k](REP[k]); await tick(); }
  }
  console.log(JSON.stringify({ pendant: pendant, pending: pendingBackups, posts: posts.length }));
})();
"""
_OK = "{ ok: true, status: 200, data: { path: 'a.webm' } }"
_KO = "{ ok: false, status: 500, data: { error: 'x' } }"


def test_backup_chemin_echec_qui_leve_libere_quand_meme_le_gate():
    # echec definitif (5xx x4) ; offrirBackupEnLocal leve : le gate doit tomber a 0
    out = _lancer_backup(0, offrir=99, reponses="[" + ",".join([_KO] * 4) + "]")
    assert out["pending"] == 0, out


def test_backup_throw_apres_ecriture_du_chemin_ne_reposte_pas():
    out = _lancer_backup(0, brancher=1, reponses="[" + _OK + "]")
    assert out["posts"] == 1 and out["pending"] == 0, out


def test_backup_5xx_puis_relance_reussie_compte_une_fois():
    out = _lancer_backup(0, reponses="[" + _KO + "," + _OK + "]")
    assert out["posts"] == 2 and out["pending"] == 0 and out["pendant"] == 1, out
