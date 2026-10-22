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
  console.log(JSON.stringify({ pendant: pendant, pending: pendingBackups, posts: posts.length, statut: backupStatusEl.textContent }));
})();
"""
_OK = "{ ok: true, status: 200, data: { path: 'a.webm' } }"
_KO = "{ ok: false, status: 500, data: { error: 'x' } }"


def test_backup_chemin_echec_qui_leve_libere_quand_meme_le_gate():
    # echec definitif (5xx x4) ; offrirBackupEnLocal leve : le gate doit tomber a 0
    out = _lancer_backup(0, offrir=99, reponses="[" + ",".join([_KO] * 4) + "]")
    assert out["pending"] == 0, out
    assert "NON SAUVEGARD" in out["statut"], out  # la perte est DITE, pas un ancien « nouvelle tentative »


def test_backup_throw_apres_ecriture_du_chemin_ne_reposte_pas():
    out = _lancer_backup(0, brancher=1, reponses="[" + _OK + "]")
    assert out["posts"] == 1 and out["pending"] == 0, out


def test_backup_5xx_puis_relance_reussie_compte_une_fois():
    out = _lancer_backup(0, reponses="[" + _KO + "," + _OK + "]")
    assert out["posts"] == 2 and out["pending"] == 0 and out["pendant"] == 1, out


_SCENARIO_VIDE = r"""
(async () => {
  uploadBackup({ type: 'audio/webm', size: 0 }, 0, undefined);
  await tick();
  console.log(JSON.stringify({ pending: pendingBackups, posts: posts.length, seen: seen.length, statut: backupStatusEl.textContent }));
})();
"""


def test_backup_blob_vide_n_est_pas_poste_et_ne_tient_pas_le_gate():
    # J1 : 0 octet -> 422 serveur -> rejoue 3 fois puis « perdu » a tort.
    src = _sans_commentaires(RECORD.read_text(encoding="utf-8"))
    script = _SCRIPT_BACKUP % {
        "boom": 0, "boomBrancher": 0, "boomOffrir": 0,
        "rec_retry": (_ROOT / "app" / "static" / "rec_retry.js").read_text(encoding="utf-8"),
        "fonction": _fonction(src, "uploadBackup"),
    } + _SCENARIO_VIDE
    out = _node(script)
    assert out["posts"] == 0, out
    assert out["pending"] == 0, out
    assert "PERDU" not in out["statut"] and "NON" not in out["statut"], out


def test_settle_updatesubmitstate_qui_leve_n_empeche_pas_le_job_final():
    # J2 : updateSubmitState leve -> maybeSubmitFinalJob jamais appele -> reliquat jamais soumis.
    src = _sans_commentaires(RECORD.read_text(encoding="utf-8"))
    script = (_SCRIPT % {
        "boom": 0,
        "rec_retry": (_ROOT / "app" / "static" / "rec_retry.js").read_text(encoding="utf-8"),
        "fonction": _fonction(src, "uploadSegment"),
    }).replace("function updateSubmitState() {}",
               "let armed = false; function updateSubmitState() { if (armed) throw new Error('boomUpd'); }") + r"""
(async () => {
  let settled = 0;
  uploadSegment({ type: 'audio/webm' }, 0, undefined, undefined, function () { settled++; });
  await tick();
  armed = true;
  pend[0](OK); await tick();
  console.log(JSON.stringify({ pending: pendingSegments, onSettle: settled, submits: submits, textes: textes.length }));
})();
"""
    out = _node(script)
    assert out == {"pending": 0, "onSettle": 1, "submits": 1, "textes": 1}, out


def test_backup_affichage_qui_leve_ne_pretend_pas_que_la_copie_locale_a_echoue():
    # J3 : la copie locale est gardee ; seule l'affectation du texte leve -> pas de « NON SAUVEGARDE ».
    src = _sans_commentaires(RECORD.read_text(encoding="utf-8"))
    script = _SCRIPT_BACKUP % {
        "boom": 0, "boomBrancher": 0, "boomOffrir": 0,
        "rec_retry": (_ROOT / "app" / "static" / "rec_retry.js").read_text(encoding="utf-8"),
        "fonction": _fonction(src, "uploadBackup"),
    }
    script = script.replace(
        "const backupStatusEl = { textContent: '' }",
        "const backupStatusEl = { _t: '', get textContent() { return this._t; }, "
        "set textContent(v) { if (v.indexOf('NON transmise') >= 0) throw new Error('boomAff'); this._t = v; } }")
    script += _SCENARIO_BACKUP_SEQ.replace("%%REP%%", "[" + ",".join([_KO] * 4) + "]")
    out = _node(script)
    assert out["pending"] == 0, out
    assert "NON SAUVEGARD" not in out["statut"], out
