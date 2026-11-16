"""Story 3.3 -- ne jamais perdre la session en cours cote navigateur.

Chaque comportement est prouve par EXECUTION du JS reel de l'ecran sous node
(fonctions extraites du template, jamais `.index()` sur le texte) :

- AC3.3.2 / A-2 : le VRAI `uploadSegment` branche sur les VRAIS `rendreAlerte`,
  `noteNoSpeech`, `resetNoSpeech` (aucun stub de `resetNoSpeech`) ;
- A-7 : une reponse 2xx sans texte vaut « aucune parole », jamais un perdu bloquant ;
- A-9 : 408/429 sont relances (plan commun rec_retry.js) ;
- AC3.3.4 / A-5 : une seule requete d'apercu en vol (les deux ecrans) ;
- AC3.3.1 / A-1 : la reprise d'un brouillon est impossible pendant une session
  vivante ; A-15 : la tranche suivante prend max(position) + 1.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    from test_navigateur_file_segments import _FONCTIONS, _SCRIPT, LIBRE
    from test_repartition_live import _bloc, _fonction, _node, _sans_commentaires
    from test_tranches_et_reprise_execution import REC_DRAFT, STRUCTURE, _bac, _bloc_reprise, _js
except ImportError:  # pragma: no cover
    from tests.test_navigateur_file_segments import _FONCTIONS, _SCRIPT, LIBRE
    from tests.test_repartition_live import _bloc, _fonction, _node, _sans_commentaires
    from tests.test_tranches_et_reprise_execution import REC_DRAFT, STRUCTURE, _bac, _bloc_reprise, _js

REC_RETRY = LIBRE.parents[2] / "static" / "rec_retry.js"
PREFIXE = "⚠ Micro muet — "
PARTAGE = "⚠ Partage perdu — "

_STUBS_ALERTE = "function resetNoSpeech() {} function noteNoSpeech() {}"

_ALERTE_REELLE = r"""
let noSpeechStreak = 0; const NOSPEECH_ALERT_AFTER = 2; let titreOrigine = null;
let noSpeechActive = false, partagePerdu = false, alerteKind = null, partageErreur = '';
const NOSPEECH_TITLE_PREFIX = %(pref)s, PARTAGE_TITLE_PREFIX = %(part)s, SEGMENT_MS = 60000;
function bandeau() { return { hidden: true, textContent: '', style: {},
  getBoundingClientRect: function () { return { height: 40 }; } }; }
const noSpeechBannerEl = bandeau(), noSpeechTextEl = bandeau(), noSpeechDurationEl = bandeau(),
      noSpeechFixBtn = bandeau();
const remoteModeEl = { checked: false };
const i2dAudioSource = { supported: function () { return true; }, tabActive: function () { return false; } };
function refreshSource() {}
document.title = 'Entretien libre';
document.body = { style: {} }; document.documentElement = { style: {} };
document.querySelector = function () { return null; };
%(alerte)s
function partage_perdu() { (function () %(perdu)s)(); }
"""


def _upload(scenario: str) -> dict:
    src = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    base = _SCRIPT
    assert _STUBS_ALERTE in base, "le harnais de file a change : stubs d'alerte introuvables"
    base = base.replace(_STUBS_ALERTE, "")
    alerte = "\n".join(_fonction(src, f) for f in
                       ("majTitre", "positionnerAlerte", "rendreAlerte", "noteNoSpeech", "resetNoSpeech"))
    reel = _ALERTE_REELLE % {"pref": json.dumps(PREFIXE), "part": json.dumps(PARTAGE), "alerte": alerte,
                             "perdu": _bloc(src, src.index("tabLost: function"))}
    script = base % {
        "timeout": 10_000_000, "acquitte": 0, "backups": "[]",
        "rec_retry": _sans_commentaires(REC_RETRY.read_text(encoding="utf-8")),
        "fonctions": "\n".join(_fonction(src, f) for f in _FONCTIONS),
        "scenario": reel + scenario,
    }
    return _node_fichier(script)


def _node_fichier(script: str) -> dict:
    """Comme `_node`, mais par fichier : le script assemble depasse la limite de
    longueur de ligne de commande Windows (WinError 206) avec `node -e`."""
    import os
    import shutil
    import subprocess
    import tempfile
    if shutil.which("node") is None:
        pytest.fail("node est requis pour executer reellement le JS de l'ecran")
    fd, chemin = tempfile.mkstemp(suffix=".cjs")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        res = subprocess.run(["node", chemin], capture_output=True, text=True, encoding="utf-8", timeout=60)
    finally:
        os.unlink(chemin)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


_ETAT = ("({ cache: noSpeechBannerEl.hidden, titre: document.title, serie: noSpeechStreak, "
         "bloquants: lostSegments.filter(function (s) { return s.blocking; }).length })")
_MUET = "{ ok: false, status: 422, data: { code: 'no_speech', detail: 'Aucune parole' } }"


def test_vrai_upload_alerte_muette_relance_vieux_segment_puis_parole_courante() -> None:
    """AC3.3.2 / A-2 : 2 x 422 no_speech -> alerte ; succes d'une RELANCE (lostId)
    -> l'alerte reste ; succes du segment COURANT -> l'alerte tombe."""
    r = _upload(r"""
(async () => {
  const out = {}; recordingActive = true;
  enqueueSegment({ type: 'audio/webm' }); await tick(); await repondre(0, %(muet)s);
  enqueueSegment({ type: 'audio/webm' }); await tick(); await repondre(1, %(muet)s);
  out.muet = %(etat)s;
  uploadSegment({ type: 'audio/webm' }, 0, 1, backupGeneration, function () {}, 0, {});
  await tick(); await repondre(2, OK('vieux'));
  out.relance = %(etat)s;
  enqueueSegment({ type: 'audio/webm' }); await tick(); await repondre(3, OK('courant'));
  out.courant = %(etat)s;
  console.log(JSON.stringify(out));
})();
""" % {"muet": _MUET, "etat": _ETAT})
    assert r["muet"]["cache"] is False and r["muet"]["titre"] == PREFIXE + "Entretien libre"
    assert r["relance"]["cache"] is False and r["relance"]["serie"] == 2, r
    assert r["courant"]["cache"] is True and r["courant"]["titre"] == "Entretien libre", r


def test_vrai_upload_partage_perdu_survit_a_un_segment_parle() -> None:
    """AC3.3.2 / A-2 : partage perdu puis 200 avec texte -> l'alerte « partage » reste."""
    r = _upload(r"""
(async () => {
  const out = {}; recordingActive = true;
  partage_perdu();
  enqueueSegment({ type: 'audio/webm' }); await tick(); await repondre(0, OK('bonjour'));
  out.apres = %(etat)s; out.perdu = partagePerdu;
  console.log(JSON.stringify(out));
})();
""" % {"etat": _ETAT})
    assert r["apres"]["cache"] is False and r["apres"]["titre"] == PARTAGE + "Entretien libre", r
    assert r["perdu"] is True


def test_reponse_2xx_sans_texte_vaut_aucune_parole_non_bloquant() -> None:
    """A-7 : 200 avec texte vide, deux fois -> alerte « aucune parole », aucun perdu bloquant."""
    r = _upload(r"""
(async () => {
  const out = {}; recordingActive = true;
  const VIDE = { ok: true, status: 200, data: { text: '' } };
  enqueueSegment({ type: 'audio/webm' }); await tick(); await repondre(0, VIDE);
  enqueueSegment({ type: 'audio/webm' }); await tick(); await repondre(1, VIDE);
  out.etat = %(etat)s; out.perdus = lostSegments.length;
  console.log(JSON.stringify(out));
})();
""" % {"etat": _ETAT})
    assert r["etat"]["cache"] is False and r["etat"]["serie"] == 2, r
    assert r["etat"]["bloquants"] == 0 and r["perdus"] == 2, r


def _classer(status: int, data: dict) -> str:
    script = (_sans_commentaires(REC_RETRY.read_text(encoding="utf-8")).replace("window.recRetryPlan", "globalThis.plan")
              + "\nconsole.log(JSON.stringify(plan.classer(%d, %s)));" % (status, json.dumps(data)))
    return _node(script)


@pytest.mark.parametrize("status, attendu", [(408, "retry"), (429, "retry"), (200, "no_speech"),
                                             (204, "no_speech"), (413, "definitif"), (400, "definitif")])
def test_classer_408_429_relances_2xx_vide_aucune_parole(status: int, attendu: str) -> None:
    """A-9 / A-7 : plan commun (record.html et record_libre.html)."""
    assert _classer(status, {}) == attendu


@pytest.mark.parametrize("ecran", [LIBRE, STRUCTURE], ids=lambda p: p.name)
def test_une_seule_requete_d_apercu_en_vol(ecran: Path) -> None:
    """AC3.3.4 / A-5 : le minuteur 5 s ne relance pas tant que la precedente est en vol,
    y compris apres un echec reseau."""
    src = _sans_commentaires(ecran.read_text(encoding="utf-8"))
    script = r"""
var sessionToken = 'A', repartitionSincePosition = 0, NET_TIMEOUT_MS = 1;
function renderRepartition() {}
var appels = 0, attente = [];
function recFetch() { appels++; return new Promise(function (ok, ko) { attente.push({ ok: ok, ko: ko }); }); }
async function tick() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
%(f)s
(async () => {
  const out = {};
  pollRepartition(); pollRepartition(); pollRepartition();
  out.en_vol = appels;
  attente[0].ok({ ok: true, json: function () { return Promise.resolve({ turns: [], answers: [], next_since: 1 }); } });
  await tick();
  pollRepartition(); out.apres_reponse = appels;
  attente[1].ko(new Error('reseau')); await tick();
  pollRepartition(); out.apres_echec = appels;
  console.log(JSON.stringify(out));
})();
""" % {"f": _fonction(src, "pollRepartition")}
    assert _node(script) == {"en_vol": 1, "apres_reponse": 2, "apres_echec": 3}


def _reprise(corps: str) -> dict:
    return _bac(_js(REC_DRAFT.read_text(encoding="utf-8")) + r"""
let out = {};
Object.assign(bac, {
  MISSION_ID: 19, transcriptSoFar: '', sessionToken: 'vivant', coveredLen: 0, segmentJobPosition: 0,
  backupSegments: [], nextBackupPosition: 0, recordingActive: false,
  transcriptEl: el('t'), transcriptHidden: el('th'), sessionTokenHidden: el('st'),
  audioSegmentsHidden: el('as'), backupPathHidden: el('bp'), statusEl: el('status'),
  startBtn: el('rec-start'), updateSubmitState: function () {}, refreshSegmentTail: function () {},
  refreshBackupStatus: function () {}, refreshBackupList: function () {}, confirm: function () { return true; }
});
el('rec-draft-banner'); el('rec-draft-text'); el('rec-draft-restore'); el('rec-draft-discard');
const BLOC = %(bloc)s;
function rendre() { el('rec-draft-banner').hidden = true; jouer(BLOC); }
jouer("recDraft.sauver(19, {transcript: 'ancien texte', sessionToken: 'tok-ancien', segmentJobPosition: 4, " +
      "backupSegments: [{filename: 'a.webm', position: 0}, {filename: 'b.webm', position: 5}]})");
%(corps)s
console.log(JSON.stringify(out));
""" % {"bloc": json.dumps(_bloc_reprise()), "corps": corps})


def test_demarrer_masque_la_proposition_de_reprise() -> None:
    """AC3.3.1 / A-1 : la banniere est masquee au clic sur « Demarrer »."""
    r = _reprise("""
rendre(); out.avant = el('rec-draft-banner').hidden;
el('rec-start')._on.click(); out.apres = el('rec-draft-banner').hidden;
""")
    assert r == {"avant": False, "apres": True}


@pytest.mark.parametrize("etat", ["bac.recordingActive = true;", "bac.transcriptSoFar = 'parole vivante';"],
                         ids=["enregistrement_en_cours", "texte_present"])
def test_reprendre_est_sans_effet_sur_une_session_vivante(etat: str) -> None:
    """AC3.3.1 / A-1 : gestionnaire garde -- ni texte, ni jeton, ni position ecrases."""
    r = _reprise("rendre(); %s el('rec-draft-restore')._on.click();" % etat + """
out = { jeton: bac.sessionToken, position: bac.segmentJobPosition, segments: bac.backupSegments.length,
        cache: el('rec-draft-banner').hidden, ancien: bac.transcriptSoFar === 'ancien texte' };
""")
    assert r == {"jeton": "vivant", "position": 0, "segments": 0, "cache": True, "ancien": False}


def test_reprise_hors_session_restaure_et_numerote_apres_la_plus_haute_position() -> None:
    """Temoin du garde (la reprise marche hors session) + A-15 : max(position) + 1."""
    r = _reprise("""
rendre(); el('rec-draft-restore')._on.click();
out = { texte: bac.transcriptSoFar, jeton: bac.sessionToken, suivant: bac.nextBackupPosition };
""")
    assert r == {"texte": "ancien texte", "jeton": "tok-ancien", "suivant": 6}


# --------------------------------------------------------------------------- #
# A-8 : pages d'attente des tranches -- borne de sortie, 401, total == 0
# --------------------------------------------------------------------------- #
ATTENTES = [LIBRE.parent / "record_segment_wait.html", LIBRE.parent / "libre_segment_wait.html"]

_ATTENTE = r"""
let clock = 0; const timers = []; const reponses = []; let soumis = 0, copie = null, telecharge = 0;
const Date = { now: function () { return clock; } };
function setTimeout(fn, d) { timers.push(fn); }
function mk(id) { return { id: id, hidden: true, textContent: '', value: '', max: 0, h: {},
  classList: { add: function () {}, remove: function () {} },
  addEventListener: function (n, f) { this.h[n] = f; }, click: function () {} }; }
const els = {}; ['seg-progress', 'seg-bar', 'seg-exit', 'seg-exit-text', 'seg-exit-copy',
  'seg-exit-download', 'seg-exit-finalize'].forEach(function (i) { els[i] = mk(i); });
const champ = mk('t'); champ.value = 'texte de l entretien';
els['finalize-form'] = { submit: function () { soumis++; },
  querySelector: function (s) { return s === 'input[name="transcript"]' ? champ : null; } };
const document = { getElementById: function (i) { return els[i] || null; },
  createElement: function () { return mk('a'); }, body: { appendChild: function () {}, removeChild: function () {} } };
const navigator = { clipboard: { writeText: function (t) { copie = t; return Promise.resolve(); } } };
const URL = { createObjectURL: function () { telecharge++; return 'blob:x'; }, revokeObjectURL: function () {} };
function Blob() {} function confirm() { return true; }
function recFetch() {
  const r = reponses.shift();
  if (r === 'reseau') return Promise.reject(new Error('reseau'));
  return Promise.resolve({ ok: r.status < 300, status: r.status, json: function () { return Promise.resolve(r.data); } });
}
async function tick() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
async function suivant() { timers.shift()(); await tick(); }
const EN_COURS = { status: 200, data: { total: 3, done: 1 } };
const VIDE = { status: 200, data: { total: 0, done: 0 } };
%(prep)s
%(script)s
(async () => {
  const out = {}; await tick();
  %(corps)s
  console.log(JSON.stringify(out));
})();
"""


def _attente(ecran: Path, prep: str, corps: str) -> dict:
    import re
    brut = ecran.read_text(encoding="utf-8")
    script = brut[brut.index("<script>") + len("<script>"):brut.index("</script>")]
    script = re.sub(r"\{\{.*?\}\}", '"tok"', script)
    return _node_fichier(_ATTENTE % {"prep": prep, "script": script, "corps": corps})


_ETAT_SORTIE = "({ sortie: els['seg-exit'].hidden === false, soumis: soumis })"


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_401_propose_la_sortie_sans_soumettre(ecran: Path) -> None:
    r = _attente(ecran, "reponses.push({ status: 401, data: {} });",
                 "out.a = %s; out.progression = els['seg-progress'].textContent;" % _ETAT_SORTIE)
    assert r["a"] == {"sortie": True, "soumis": 0}
    assert "Session expirée" in r["progression"]


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_total_zero_repete_propose_la_sortie(ecran: Path) -> None:
    r = _attente(ecran, "reponses.push(VIDE, VIDE, VIDE);", """
out.deux = (await suivant(), %(e)s);
await suivant(); out.trois = %(e)s;
""" % {"e": _ETAT_SORTIE})
    assert r["deux"] == {"sortie": False, "soumis": 0}
    assert r["trois"] == {"sortie": True, "soumis": 0}


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_bornee_dans_le_temps_puis_copie_telechargement_finalisation(ecran: Path) -> None:
    r = _attente(ecran, "reponses.push(EN_COURS, EN_COURS);", """
out.avant = %(e)s;
clock = 21 * 60 * 1000; await suivant();
out.apres = %(e)s;
els['seg-exit-copy'].h.click(); await tick(); out.copie = copie;
els['seg-exit-download'].h.click(); out.telecharge = telecharge;
els['seg-exit-finalize'].h.click(); out.final = soumis;
""" % {"e": _ETAT_SORTIE})
    assert r["avant"] == {"sortie": False, "soumis": 0}
    assert r["apres"] == {"sortie": True, "soumis": 0}
    assert r["copie"] == "texte de l entretien" and r["telecharge"] == 1 and r["final"] == 1


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_echecs_reseau_repetes_proposent_la_sortie(ecran: Path) -> None:
    r = _attente(ecran, "for (let i = 0; i < 6; i++) reponses.push('reseau');", """
for (let i = 0; i < 4; i++) await suivant();
out.cinq = %(e)s;
await suivant(); out.six = %(e)s;
""" % {"e": _ETAT_SORTIE})
    assert r["cinq"] == {"sortie": False, "soumis": 0}
    assert r["six"] == {"sortie": True, "soumis": 0}


# --------------------------------------------------------------------------- #
# A-6 : rec_audio_source.js -- « Arreter » pendant le selecteur d'onglet
# --------------------------------------------------------------------------- #
REC_AUDIO = LIBRE.parents[2] / "static" / "rec_audio_source.js"

_AUDIO = r"""
const arretees = []; let ctxCrees = 0; const resoudre = {};
function piste(nom) { return { nom: nom, readyState: 'live', muted: false,
  stop: function () { arretees.push(nom); }, addEventListener: function () {} }; }
function flux(nom, audio) { const p = [piste(nom + '-a'), piste(nom + '-v')];
  return { getAudioTracks: function () { return audio ? [p[0]] : []; }, getTracks: function () { return p; } }; }
function AudioContext() { ctxCrees++; this.state = 'running'; }
AudioContext.prototype = { addEventListener: function () {}, close: function () {},
  createMediaStreamDestination: function () { return { stream: { mix: true } }; },
  createMediaStreamSource: function () { return { connect: function () {}, disconnect: function () {} }; },
  createAnalyser: function () { return { fftSize: 0 }; } };
function MediaStream() {}
const navigator = { mediaDevices: {
  getDisplayMedia: function () { return new Promise(function (ok) { resoudre.onglet = ok; }); },
  getUserMedia: function () { return new Promise(function (ok) { resoudre.micro = ok; }); } } };
const window = { AudioContext: AudioContext };
function setInterval() { return 1; } function clearInterval() {}
async function tick() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
%(src)s
const src = window.i2dAudioSource;
(async () => {
  const out = {};
  %(corps)s
  console.log(JSON.stringify(out));
})();
"""


def _audio(corps: str) -> dict:
    return _node_fichier(_AUDIO % {"src": REC_AUDIO.read_text(encoding="utf-8"), "corps": corps})


def test_partage_resolu_apres_arret_ne_reacquiert_rien() -> None:
    """A-6 : Stop pendant que le selecteur d'onglet est ouvert, puis resolution tardive
    -> pistes arretees, aucun AudioContext, la demande echoue, l'onglet n'est pas actif."""
    r = _audio("""
let erreur = null;
src.shareTab().then(function () {}, function (e) { erreur = e.message; });
src.release();
resoudre.onglet(flux('tab', true)); await tick();
Object.assign(out, { erreur: erreur, arretees: arretees, ctx: ctxCrees, actif: src.tabActive() });
""")
    assert "arrêté entre-temps" in (r["erreur"] or ""), r
    assert r["arretees"] == ["tab-a", "tab-v"] and r["ctx"] == 0 and r["actif"] is False, r


def test_micro_resolu_apres_arret_est_relache() -> None:
    """A-6 : la permission micro accordee apres « Arreter » ne garde pas le micro ouvert."""
    r = _audio("""
let erreur = null;
src.acquire(false).then(function () {}, function (e) { erreur = e.message; });
src.release();
resoudre.micro(flux('mic', true)); await tick();
Object.assign(out, { erreur: erreur, arretees: arretees });
""")
    assert "arrêté entre-temps" in (r["erreur"] or "") and r["arretees"] == ["mic-a", "mic-v"], r


def test_partage_sans_arret_reste_inchange() -> None:
    """Temoin : sans release(), le partage aboutit (onglet actif, mixage cree)."""
    r = _audio("""
let ok = false;
src.shareTab().then(function () { ok = true; });
resoudre.onglet(flux('tab', true)); await tick();
resoudre.micro(flux('mic', true)); await tick();
Object.assign(out, { ok: ok, actif: src.tabActive(), ctx: ctxCrees, arretees: arretees });
""")
    assert r == {"ok": True, "actif": True, "ctx": 1, "arretees": []}


# --------------------------------------------------------------------------- #
# Mineurs : A-11 (rec_draft.js), A-18 (rec_fetch.js)
# --------------------------------------------------------------------------- #
REC_FETCH = LIBRE.parents[2] / "static" / "rec_fetch.js"


def test_brouillon_sans_date_n_affiche_pas_nan() -> None:
    """A-11 : `savedAt` absent -> « a une date inconnue », jamais « NaN/NaN »."""
    r = _node_fichier("const window = {};\n" + REC_DRAFT.read_text(encoding="utf-8") + r"""
window.recDraft = module.exports;
const sans = window.recDraft.decision({ transcript: 'du texte' }, 1e12);
const avec = window.recDraft.decision({ transcript: 'du texte', savedAt: 1e12 }, 1e12);
console.log(JSON.stringify({ sans: sans.libelle, avec: avec.libelle }));
""")
    assert "NaN" not in r["sans"] and "à une date inconnue" in r["sans"], r
    assert "NaN" not in r["avec"] and "sauvegarde locale le " in r["avec"], r


_FETCH = r"""
const window = {}; const timers = [];
function setTimeout(fn) { timers.push(fn); return 1; } function clearTimeout() {}
function fetch(url, opts) { return new Promise(function (ok, ko) {
  opts.signal.addEventListener('abort', function () { const e = new Error('aborted'); e.name = 'AbortError'; ko(e); });
}); }
%(src)s
async function tick() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
(async () => {
  const out = {};
  const appelant = new AbortController();
  let e1 = null; window.recFetch('/x', { signal: appelant.signal }, 5000).catch(function (e) { e1 = e; });
  appelant.abort(); await tick();
  out.appelant = { name: e1 && e1.name, recTimeout: !!(e1 && e1.recTimeout) };
  let e2 = null; window.recFetch('/y', { signal: new AbortController().signal }, 5000).catch(function (e) { e2 = e; });
  timers[timers.length - 1](); await tick();
  out.delai = { recTimeout: !!(e2 && e2.recTimeout) };
  console.log(JSON.stringify(out));
})();
"""


def test_recfetch_chaine_le_signal_de_l_appelant() -> None:
    """A-18 : l'annulation par l'appelant aboutit (AbortError, pas un « delai depasse ») ;
    le minuteur interne produit toujours `recTimeout`."""
    r = _node_fichier(_FETCH % {"src": REC_FETCH.read_text(encoding="utf-8")})
    assert r == {"appelant": {"name": "AbortError", "recTimeout": False}, "delai": {"recTimeout": True}}
