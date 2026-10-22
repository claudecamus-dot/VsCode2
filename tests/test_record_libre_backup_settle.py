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
function noterEvenement() {} function signalerMissionAbsente() {} function persistDraft() {}
function garderTrancheEnLocal() {} function refreshBackupStatus() {}
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


def _lancer(boom_submit: int, boom_list: int) -> dict:
    src = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    script = _SCRIPT % {
        "boomSubmit": boom_submit,
        "boomList": boom_list,
        "rec_retry": (_ROOT / "app" / "static" / "rec_retry.js").read_text(encoding="utf-8"),
        "fonction": _fonction(src, "uploadBackup"),
    } + _SCENARIO
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
