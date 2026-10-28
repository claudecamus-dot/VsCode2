"""File séquentielle des segments en direct + backoff « occupé » (record_libre.html),
JS EXÉCUTÉ sous node, 2026-10-19.

Mesuré par l'utilisateur (enregistrement Teams) : 118 POST /audio/transcribe-segment en
503 « occupé » contre 3 en 200 — un segment toutes les 60 s partait sans attendre
l'accusé du précédent. Les fonctions RÉELLES (enqueueSegment, pumpSegments,
resetSegmentQueue, uploadSegment, copieServeurAcquittee, updateSubmitState) sont extraites
du template et exécutées avec un `recFetch`/horloge/timers pilotés à la main.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    from test_repartition_live import _fonction, _node, _sans_commentaires
except ImportError:  # pragma: no cover
    from tests.test_repartition_live import _fonction, _node, _sans_commentaires

LIBRE = Path(__file__).resolve().parent.parent / "app" / "templates" / "interviews" / "record_libre.html"
_FONCTIONS = ["enqueueSegment", "pumpSegments", "resetSegmentQueue", "uploadSegment",
              "copieServeurAcquittee", "updateSubmitState", "keepLostSegment", "lostMarker",
              "retryLostSegments", "refreshLostBanner", "maybeSubmitFinalJob"]

_SCRIPT = r"""
const timers = []; const fetchs = []; const pend = [];
let clock = 0, inflight = 0, maxInflight = 0;
const textes = [];
function FormData() {} FormData.prototype.append = function () {};
function File(p, n, o) { this.type = o && o.type; }
const Date = { now: function () { return clock; } };
function setTimeout(fn, d) { timers.push({ fn: fn, delai: d }); return timers.length; }
function clearTimeout() {}
async function tick() { for (let i = 0; i < 30; i++) await Promise.resolve(); }
function recFetch(url, opts, delai) {
  fetchs.push({ url: url, headers: opts.headers, delai: delai });
  inflight++; maxInflight = Math.max(maxInflight, inflight);
  return new Promise(function (res) {
    pend.push(function (r) {
      inflight--;
      res({ ok: r.ok, status: r.status, json: function () { return Promise.resolve(r.data); } });
    });
  });
}
async function repondre(i, r) { pend[i](r); await tick(); }
const BUSY = { ok: false, status: 503, data: { code: 'busy', retry_after_s: 60 } };
const OK = function (t) { return { ok: true, status: 200, data: { text: t } }; };

let backupGeneration = 0, pendingSegments = 0, sessionToken = 'tokA';
let segQueue = [], segInFlight = false, segEpoch = 0, segmentSeq = 0, waitingSegments = [];
const TRANSCRIBE_TIMEOUT_MS = %(timeout)d;
const BUSY_BACKOFF_BASE_MS = 30000, BUSY_BACKOFF_MAX_MS = 240000;
let backupConfirmes = %(acquitte)d, sessionRestauree = false, pendingBackups = 0;
let backupSegments = %(backups)s, backupPerdusLocaux = [], backupLocalAcquitte = false;
let transcriptSoFar = '', pendingSegmentJobSubmits = 0, finalizing = false, recordingActive = false;
let missionAbsente = false, finalizing2 = 0, lostSegments = [], lostRetryBlocking = 0;
const statusEl = { textContent: '' }, submitBtn = { disabled: null }, pdfBtn = { disabled: null };
let coveredLen = 0; const finals = []; function submitSegmentJob() { finals.push([lostRetryActive, lowQueue.length, textes.length]); }
function newSessionToken() { return 'n'; } const sessionTokenHidden = {};
var lostMarkerTexts = {};
let nextLostSegmentId = 1, lostRetryActive = 0, lowQueue = [], lowEnVol = 0;
const LOST_LIST_MAX = 8;
function perdus() { return lostSegments.filter(function (x) { return !x.attente; }); }
function mkEl() { return { ch: [], h: {}, className: '', textContent: '', appendChild: function (c) { this.ch.push(c); },
  addEventListener: function (n, f) { this.h[n] = f; } }; }
const document = { createElement: mkEl };
const window = { confirm: function () { return true; } };
function markLostSegmentAbandoned() {}
const lostBannerEl = { hidden: true }, lostTextEl = { textContent: '' };
const lostRetryBtn = { disabled: null, title: '' };
const lostListEl = { ch: [], appendChild: function (c) { this.ch.push(c); } };
Object.defineProperty(lostListEl, 'textContent', { set: function () { this.ch = []; } });
function appendTranscript(t) { textes.push(t); transcriptSoFar += t; }
function replaceLostMarker(id, t) { textes.push('R' + id + ':' + t); } function resetNoSpeech() {} function noteNoSpeech() {}

// Le VRAI plan de reprise (static/rec_retry.js), exécuté tel quel (fusion fiab).
%(rec_retry)s
const RETRY_PLAN = window.recRetryPlan;
let segmentsRejoues = 0;
function refreshReplayStatus() {}

%(fonctions)s

%(scenario)s
"""


def _lancer(scenario: str, *, timeout: int = 10_000_000, acquitte: bool = False) -> dict:
    src = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    script = _SCRIPT % {
        "timeout": timeout,
        "acquitte": 1 if acquitte else 0,
        "backups": json.dumps([{"position": 0}] if acquitte else []),
        # Sans commentaires : le script passe par `node -e` (limite de longueur de
        # ligne de commande Windows, WinError 206 atteinte le 2026-10-28).
        "rec_retry": _sans_commentaires((LIBRE.parents[2] / "static" / "rec_retry.js").read_text(encoding="utf-8")),
        "fonctions": "\n".join(_fonction(src, f) for f in _FONCTIONS),
        "scenario": scenario,
    }
    return _node(script)


# (i) jamais plus d'un POST en vol, 5 segments émis, serveur occupé puis libre.
_S_FILE = r"""
(async () => {
  const out = {};
  for (let i = 0; i < 5; i++) enqueueSegment({ type: 'audio/webm' });
  await tick();
  out.fetchs_apres_5_segments = fetchs.length;
  out.pending_apres_5 = pendingSegments;
  await repondre(0, BUSY);                       // le serveur refuse : la tête attend, les autres aussi
  out.fetchs_pendant_busy = fetchs.length;
  clock += timers[0].delai; timers[0].fn(); await tick();   // relance de la tête (fetch #2)
  await repondre(1, BUSY);
  clock += timers[1].delai; timers[1].fn(); await tick();   // fetch #3
  out.fetchs_avant_succes = fetchs.length;
  await repondre(2, OK('t1'));
  for (let k = 3; k < 7; k++) await repondre(k, OK('t' + (k - 1)));
  out.max_en_vol = maxInflight;
  out.textes = textes;
  out.pending_final = pendingSegments;
  out.fetchs_total = fetchs.length;
  console.log(JSON.stringify(out));
})();
"""


def test_au_plus_un_post_en_vol_et_ordre_des_textes_conserve():
    out = _lancer(_S_FILE)
    assert out["fetchs_apres_5_segments"] == 1
    assert out["pending_apres_5"] == 5            # file + en vol
    assert out["fetchs_pendant_busy"] == 1
    assert out["fetchs_avant_succes"] == 3        # trois essais de la TÊTE, aucun autre segment
    assert out["max_en_vol"] == 1
    assert out["textes"] == ["t1", "t2", "t3", "t4", "t5"]
    assert out["pending_final"] == 0
    assert out["fetchs_total"] == 7


# (ii) backoff : retry_after_s=60 -> 60, 120, 240, 240 s ; plafond 240 s même si le serveur demande plus.
_S_BACKOFF = r"""
(async () => {
  const out = { delais: [] };
  enqueueSegment({ type: 'audio/webm' });
  await tick();
  for (let i = 0; i < 4; i++) {
    await repondre(i, BUSY);
    out.delais.push(timers[i].delai);
    clock += timers[i].delai; timers[i].fn(); await tick();
  }
  await repondre(4, { ok: false, status: 503, data: { code: 'busy', retry_after_s: 300 } });
  out.delai_serveur_300 = timers[4].delai;
  console.log(JSON.stringify(out));
})();
"""


def test_backoff_occupe_60_120_240_plafonne():
    out = _lancer(_S_BACKOFF)
    assert out["delais"] == [60000, 120000, 240000, 240000]
    assert out["delai_serveur_300"] == 240000


# (iii) patience épuisée : sauvegarde acquittée -> « en attente », sinon « perdu » bloquant.
_S_EPUISE = r"""
(async () => {
  const out = {};
  transcriptSoFar = 'x';
  enqueueSegment({ type: 'audio/webm' });
  await tick();
  for (let i = 0; i < 10 && pend[i]; i++) {
    await repondre(i, BUSY);
    if (timers[i]) { clock += timers[i].delai; timers[i].fn(); await tick(); }
  }
  out.attente = waitingSegments.length;
  out.perdus = perdus().length;
  out.perdu_bloquant = perdus().length ? perdus()[0].blocking : null;
  out.pending = pendingSegments;
  out.fetchs = fetchs.length;
  updateSubmitState();
  out.disabled = submitBtn.disabled;
  out.msg = statusEl.textContent;
  console.log(JSON.stringify(out));
})();
"""


def test_busy_persistant_avec_sauvegarde_acquittee_met_en_attente_sans_griser():
    out = _lancer(_S_EPUISE, timeout=200_000, acquitte=True)
    assert out["attente"] == 1 and out["perdus"] == 0
    assert out["pending"] == 0
    assert out["disabled"] is False
    assert "1 segment en attente de transcription" in out["msg"]
    assert "l’audio est sauvegardé" in out["msg"]
    # pas retenté en boucle : budget 200 s = 4 tentatives (0, 60, 180, 200 s)
    assert out["fetchs"] <= 5


def test_busy_persistant_sans_sauvegarde_reste_perdu_et_bloquant():
    out = _lancer(_S_EPUISE, timeout=200_000, acquitte=False)
    assert out["attente"] == 0 and out["perdus"] == 1
    assert out["perdu_bloquant"] is True
    assert out["disabled"] is True


# (iv) X-Segment-Id : stable pour les retries d'un blob, distinct entre segments.
_S_ID = r"""
(async () => {
  const out = {};
  enqueueSegment({ type: 'audio/webm' });
  enqueueSegment({ type: 'audio/webm' });
  await tick();
  await repondre(0, BUSY);
  clock += timers[0].delai; timers[0].fn(); await tick();   // retry du segment 1
  await repondre(1, OK('a'));                                // segment 2 démarre
  await repondre(2, OK('b'));
  out.ids = fetchs.map(function (f) { return f.headers['X-Segment-Id']; });
  console.log(JSON.stringify(out));
})();
"""


def test_x_segment_id_stable_par_blob_et_distinct_entre_segments():
    out = _lancer(_S_ID)
    assert out["ids"] == ["tokA-1", "tokA-1", "tokA-2"]


# (v) reset : vide la file, ignore l'ancien envoi en vol, débloque la nouvelle session.
_S_RESET = r"""
(async () => {
  const out = {};
  for (let i = 0; i < 3; i++) enqueueSegment({ type: 'audio/webm' });
  await tick();
  backupGeneration++; sessionToken = 'tokB'; resetSegmentQueue();   // comme « Recommencer »
  out.file_apres_reset = segQueue.length;
  out.pending_apres_reset = pendingSegments;                        // reste l'envoi en vol
  enqueueSegment({ type: 'audio/webm' });                           // nouvelle session
  await tick();
  out.fetch_nouvelle_session_part = fetchs.length === 2;
  await repondre(0, OK('ancien'));                                  // l'ancien se règle : texte jeté
  out.textes = textes.slice();
  out.pending_apres_ancien = pendingSegments;
  await repondre(1, OK('neuf'));
  out.textes_final = textes.slice();
  out.pending_final = pendingSegments;
  out.ids = fetchs.map(function (f) { return f.headers['X-Segment-Id']; });
  console.log(JSON.stringify(out));
})();
"""


def test_reset_vide_la_file_et_debloque_la_nouvelle_session():
    out = _lancer(_S_RESET)
    assert out["file_apres_reset"] == 0
    assert out["pending_apres_reset"] == 1
    assert out["fetch_nouvelle_session_part"] is True
    assert out["textes"] == []                    # texte de la session abandonnée jeté
    assert out["pending_apres_ancien"] == 1
    assert out["textes_final"] == ["neuf"]
    assert out["pending_final"] == 0
    assert out["ids"] == ["tokA-1", "tokB-1"]


# C2 : pendant l'enregistrement, la copie serveur n'est PAS acquittée -> perdu bloquant.
_S_C2 = r"""
(async () => {
  recordingActive = true;
  enqueueSegment({ type: 'audio/webm' });
  await tick();
  for (let i = 0; i < 10 && pend[i]; i++) {
    await repondre(i, BUSY);
    if (timers[i]) { clock += timers[i].delai; timers[i].fn(); await tick(); }
  }
  console.log(JSON.stringify({ attente: waitingSegments.length, perdus: perdus().length,
    bloquant: perdus().length ? perdus()[0].blocking : null }));
})();
"""


def test_pendant_l_enregistrement_la_copie_serveur_n_est_pas_acquittee():
    out = _lancer(_S_C2, timeout=200_000, acquitte=True)
    assert out == {"attente": 0, "perdus": 1, "bloquant": True}


# C3 : une exception dans le traitement du résultat ne relance pas et ne double pas le règlement.
_S_C3 = r"""
(async () => {
  let fois = 0;
  appendTranscript = function (t) { if (!fois++) throw new Error('boom'); textes.push(t); };
  enqueueSegment({ type: 'audio/webm' });
  await tick();
  console.error = function () {};
  await repondre(0, OK('t'));
  await tick();
  console.log(JSON.stringify({ pending: pendingSegments, fetchs: fetchs.length, timers: timers.length,
    garde: lostSegments.map(function (x) { return [x.reason, x.blocking]; }) }));
})();
"""


def test_exception_dans_le_traitement_ne_double_pas_le_reglement_ni_ne_reemet():
    out = _lancer(_S_C3)
    assert out["pending"] == 0 and out["fetchs"] == 1 and out["timers"] == 0
    assert out["garde"] == [["texte reçu non ajouté", False]]  # texte reçu jamais perdu sans trace


# C6 : la tentative finale après la dernière attente garde un timeout normal ET l'état
# « en attente » est atteint.
_S_C6 = r"""
(async () => {
  enqueueSegment({ type: 'audio/webm' });
  await tick();
  for (let i = 0; i < 10 && pend[i]; i++) {
    await repondre(i, BUSY);
    if (timers[i]) { clock += timers[i].delai; timers[i].fn(); await tick(); }
  }
  console.log(JSON.stringify({ attente: waitingSegments.length, perdus: perdus().length,
    delais: fetchs.map(function (f) { return f.delai; }) }));
})();
"""


def test_tentative_finale_a_un_timeout_normal_et_l_attente_est_atteinte():
    out = _lancer(_S_C6, timeout=200_000, acquitte=True)
    assert out["attente"] == 1 and out["perdus"] == 0
    assert min(out["delais"][1:]) >= 120000


# Segment « en attente »: conservé (blob + segId), visible dans la liste, relançable ; les boutons
# restent accessibles pendant l'enregistrement (relance en file derrière le direct).
_S_ATTENTE = r"""
(async () => {
  const out = {};
  enqueueSegment({ type: 'audio/webm', tag: 'b1' });
  await tick();
  for (let i = 0; i < 10 && pend[i]; i++) {
    await repondre(i, BUSY);
    if (timers[i]) { clock += timers[i].delai; timers[i].fn(); await tick(); }
  }
  const e = lostSegments[0];
  out.conserve = [lostSegments.length, e.attente, e.blocking, e.segId, e.blob.tag];
  out.liste = lostListEl.ch.length;
  out.bandeau_cache = lostBannerEl.hidden;
  out.texte = lostTextEl.textContent;
  const nf = fetchs.length;
  recordingActive = true;                                   // l'enregistrement reprend
  enqueueSegment({ type: 'audio/webm', tag: 'direct' });    // un direct part (tokA-2)
  await tick();
  refreshLostBanner();
  out.global_actif_pendant_enreg = lostRetryBtn.disabled === false;
  const btn = lostListEl.ch[0].ch[1];
  out.indiv_actif_pendant_enreg = btn.disabled === false;
  btn.h.click();                                             // relance individuelle
  out.apres_clic_grises = [lostRetryBtn.disabled, lostListEl.ch.length];
  out.fetch_relance_attend_le_direct = fetchs.length === nf + 1;   // seul le direct est parti
  await repondre(nf, OK('direct'));                           // le direct finit -> la relance part
  out.fetch_relance_part_ensuite = fetchs.length === nf + 2;
  out.ids = fetchs.slice(nf).map(function (f) { return f.headers['X-Segment-Id']; });
  await repondre(nf + 1, OK('rejoue'));
  out.vide = [lostSegments.length, waitingSegments.length, lostRetryBtn.disabled];
  out.textes = textes.slice(-2);
  console.log(JSON.stringify(out));
})();
"""


def test_segment_en_attente_conserve_visible_relancable_et_boutons_accessibles():
    out = _lancer(_S_ATTENTE, timeout=200_000, acquitte=True)
    assert out["conserve"] == [1, True, False, "tokA-1", "b1"]
    assert out["liste"] == 1 and out["bandeau_cache"] is False
    assert "en attente de transcription" in out["texte"]
    assert out["global_actif_pendant_enreg"] and out["indiv_actif_pendant_enreg"]
    assert out["apres_clic_grises"] == [True, 0]  # en cours: boutons grisés, entrée sortie de la liste
    assert out["fetch_relance_attend_le_direct"] is True
    assert out["fetch_relance_part_ensuite"] is True
    assert out["ids"] == ["tokA-2", "tokA-1"]       # même segId à la relance (dédup serveur)
    assert out["vide"] == [0, 0, True]               # sorti de la liste, plus rien à relancer
    assert out["textes"] == ["direct", "R1:rejoue"]


_S_GLOBALE = r"""
(async () => {
  keepLostSegment({ tag: 'a' }, 'occupé', undefined, false, 'tokA-7', true);
  keepLostSegment({ tag: 'b' }, 'occupé', undefined, false, 'tokA-8', true);
  const avant = [lostSegments.length, waitingSegments.length];
  retryLostSegments();                                       // « Relancer tous »
  await tick();
  await repondre(0, OK('x')); await repondre(1, OK('y'));
  console.log(JSON.stringify({ avant: avant, apres: [lostSegments.length, waitingSegments.length],
    ids: fetchs.map(function (f) { return f.headers['X-Segment-Id']; }), max: maxInflight,
    bouton: lostRetryBtn.disabled }));
})();
"""


def test_relance_globale_vide_les_segments_en_attente_un_envoi_a_la_fois():
    out = _lancer(_S_GLOBALE, acquitte=True)
    assert out == {"avant": [2, 2], "apres": [0, 0], "ids": ["tokA-7", "tokA-8"], "max": 1, "bouton": True}


# J1: une exception dans updateSubmitState ne fige ni la file directe ni le compte des relances.
_S_J1 = r"""
(async () => {
  console.error = function () {};
  enqueueSegment({ type: 'audio/webm' }); enqueueSegment({ type: 'audio/webm' });
  await tick();
  const reel = updateSubmitState; let n = 0;
  updateSubmitState = function () { if (!n++) throw new Error('boom'); return reel(); };
  await repondre(0, OK('a'));
  const out = { fetchs: fetchs.length, enVol: segInFlight, pending: pendingSegments };
  await repondre(1, OK('b'));
  out.final = [segInFlight, pendingSegments];
  console.log(JSON.stringify(out));
})();
"""


def test_exception_dans_update_submit_state_libere_quand_meme_l_emplacement():
    out = _lancer(_S_J1)
    assert out == {"fetchs": 2, "enVol": True, "pending": 1, "final": [False, 0]}


# J2: une relance manuelle en « occupé » pendant l'enregistrement rend l'emplacement au direct.
_S_J2 = r"""
(async () => {
  keepLostSegment({ tag: 'a' }, 'x', undefined, true, 'tokA-9');
  recordingActive = true;
  retryLostSegments();
  await tick();
  await repondre(0, BUSY);                       // relance refusée « occupé »
  const out = { timers: timers.length, rendue: [lostSegments.length, lostSegments[0] && lostSegments[0].blocking],
    blocage: lostRetryBlocking, actif: lostRetryActive };
  enqueueSegment({ type: 'audio/webm' });         // un segment direct arrive
  await tick();
  out.ids = fetchs.map(function (f) { return f.headers['X-Segment-Id']; });
  console.log(JSON.stringify(out));
})();
"""


def test_relance_manuelle_occupee_pendant_l_enregistrement_rend_l_emplacement():
    out = _lancer(_S_J2)
    assert out["timers"] == 0                       # ne dort pas dans l'emplacement
    assert out["rendue"] == [1, True]               # de nouveau relançable, toujours bloquant
    assert out["blocage"] == 0 and out["actif"] == 0
    assert out["ids"] == ["tokA-9", "tokA-1"]       # le direct part tout de suite


# J3: un drain d'une session abandonnée ne contamine pas la nouvelle.
_S_J3 = r"""
(async () => {
  keepLostSegment({ tag: 'a' }, 'x', undefined, true, 'tokA-8');
  keepLostSegment({ tag: 'b' }, 'x', undefined, true, 'tokA-9');
  retryLostSegments();                             // « Relancer tous »
  await tick();
  backupGeneration++; sessionToken = 'tokB'; resetSegmentQueue();   // « Recommencer »
  lostSegments = []; lostRetryBlocking = 1;        // état de la NOUVELLE session
  await repondre(0, OK('vieux'));
  console.log(JSON.stringify({ fetchs: fetchs.length, bas: lowQueue.length, actif: lostRetryActive,
    blocage: lostRetryBlocking }));
})();
"""


def test_drain_d_une_session_abandonnee_ne_contamine_pas_la_nouvelle():
    out = _lancer(_S_J3)
    assert out == {"fetchs": 1, "bas": 0, "actif": 0, "blocage": 1}


# M4: un reset avec une relance BLOQUANTE en vol remet aussi `lostRetryBlocking` à 0 (la garde
# d'époque interdit ensuite tout décrément : l'alerte de sortie resterait levée à tort).
_S_M4 = r"""
(async () => {
  keepLostSegment({ tag: 'a' }, 'x', undefined, true, 'tokA-8');
  retryLostSegments();
  await tick();
  const avant = [lostRetryActive, lostRetryBlocking];
  backupGeneration++; sessionToken = 'tokB'; resetSegmentQueue();   // « Recommencer »
  await repondre(0, OK('vieux'));                                    // décompte refusé par la garde d'époque
  console.log(JSON.stringify({ avant: avant, actif: lostRetryActive, blocage: lostRetryBlocking }));
})();
"""


def test_reset_remet_a_zero_le_blocage_des_relances_en_vol():
    out = _lancer(_S_M4)
    assert out["avant"] == [1, 1]
    assert out == {"avant": [1, 1], "actif": 0, "blocage": 0}


# JA: exception de traitement avant toute conservation -> le blob est gardé (bloquant), pas perdu.
_S_JA = r"""
(async () => {
  console.error = function () {};
  noteNoSpeech = function () { throw new Error('boom'); };
  enqueueSegment({ type: 'audio/webm', tag: 'b' });
  await tick();
  await repondre(0, { ok: false, status: 422, data: { code: 'no_speech', error: 'muet' } });
  console.log(JSON.stringify({ garde: lostSegments.map(function (x) { return [x.reason, x.blocking, x.blob.tag]; }),
    pending: pendingSegments, enVol: segInFlight }));
})();
"""


def test_exception_avant_conservation_garde_le_blob():
    out = _lancer(_S_JA)
    assert out["garde"] == [["erreur de traitement", True, "b"]]
    assert out["pending"] == 0 and out["enVol"] is False


# JB: uploadSegment qui lève en SYNCHRONE ne fige pas la file; le segment est conservé et le suivant part.
_S_JB = r"""
(async () => {
  console.error = function () {};
  let n = 0;
  File = function () { if (!n++) throw new Error('boom'); this.type = 'x'; };
  enqueueSegment({ type: 'audio/webm', tag: 'a' }); enqueueSegment({ type: 'audio/webm', tag: 'b' });
  await tick();
  console.log(JSON.stringify({ fetchs: fetchs.length, enVol: segInFlight, pending: pendingSegments,
    garde: lostSegments.map(function (x) { return [x.reason, x.blocking, x.blob.tag, x.segId]; }) }));
})();
"""


def test_exception_synchrone_d_envoi_ne_fige_pas_la_file():
    out = _lancer(_S_JB)
    assert out["fetchs"] == 1 and out["enVol"] is True and out["pending"] == 1
    assert out["garde"] == [["erreur d’envoi", True, "a", "tokA-1"]]


# JC: le job final ne part qu'après la DERNIÈRE relance d'un drain.
_S_JC = r"""
(async () => {
  keepLostSegment({ tag: 'a' }, 'x', undefined, true, 'tokA-8');
  keepLostSegment({ tag: 'b' }, 'x', undefined, true, 'tokA-9');
  transcriptSoFar = 'texte';
  retryLostSegments();
  await tick();
  await repondre(0, OK('un'));
  const apres_premiere = finals.length;
  await repondre(1, OK('deux'));
  console.log(JSON.stringify({ apres_premiere: apres_premiere, finals: finals, textes: textes.slice(-2) }));
})();
"""


def test_job_final_attend_la_derniere_relance_du_drain():
    out = _lancer(_S_JC)
    assert out["apres_premiere"] == 0
    assert len(out["finals"]) >= 1
    assert out["finals"][0][0] == 0 and out["finals"][0][1] == 0     # drain terminé
    assert out["textes"] == ["R1:un", "R2:deux"]


# JD: un élément de lowQueue d'une génération périmée n'immobilise pas lostRetryActive.
_S_JD = r"""
(async () => {
  keepLostSegment({ tag: 'a' }, 'x', undefined, true, 'tokA-8');
  keepLostSegment({ tag: 'b' }, 'x', undefined, true, 'tokA-9');
  recordingActive = true;
  enqueueSegment({ type: 'audio/webm' });              // direct en vol: les relances restent en file
  await tick();
  retryLostSegments();
  backupGeneration++;                                   // génération changée SANS reset de file
  await repondre(0, OK('d'));
  console.log(JSON.stringify({ actif: lostRetryActive, bas: lowQueue.length, fetchs: fetchs.length }));
})();
"""


def test_element_de_generation_perimee_ne_coince_pas_le_compteur():
    out = _lancer(_S_JD)
    assert out["actif"] == 0 and out["bas"] == 0 and out["fetchs"] == 1


def test_le_flux_direct_passe_par_la_file_et_les_sites_de_reset_la_vident():
    src = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    assert "enqueueSegment(blob);" in _fonction(src, "startSegment")
    assert src.count("resetSegmentQueue();") == 2   # démarrage d'une session + « Recommencer »
