"""Relance des tranches d'extraction et reprise de session — EXÉCUTION RÉELLE du JS.

Pendant de `tests/test_session_libre_reprise.py`, dont l'étage « câblage » ne
tient que par des présences de chaînes. La revue adversariale du 2026-09-08 (F8)
a tracé quatre mutations qui laissaient cette suite verte : supprimer la remise à
zéro du compteur d'échecs, décaler l'indice des délais (relance immédiate en
boucle, « dans NaN s »), forcer `coveredLen = 0` à la reprise (chaque tour
dupliqué à la fusion), et ne jamais afficher le bandeau. Ici, comme dans
`tests/test_repartition_live.py`, le code est EXTRAIT du template puis JOUÉ sous
node dans un bac à sable — ce qui est vérifié est l'ÉTAT après exécution :
compteur, délai réellement passé à `setTimeout`, curseur, texte restauré,
bandeau. Le bloc de reprise tourne contre le VRAI `rec_draft.js`, pas une
doublure.

Aucun accès base : ce module ne touche ni `DB_PATH` ni `SessionLocal`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

try:  # `tests/` sur sys.path (mode d'import par défaut de pytest)
    from test_repartition_live import _bloc, _fonction, _node, _sans_commentaires
except ImportError:  # `tests` importable comme paquet
    from tests.test_repartition_live import _bloc, _fonction, _node, _sans_commentaires

RACINE = Path(__file__).resolve().parent.parent
LIBRE = RACINE / "app" / "templates" / "interviews" / "record_libre.html"
STRUCTURE = RACINE / "app" / "templates" / "interviews" / "record.html"
REC_DRAFT = RACINE / "app" / "static" / "rec_draft.js"

# Bac à sable : stubs EXPLICITES pour ce que les scénarios observent (minuteurs,
# requêtes, éléments), doublure tolérante pour le reste de l'écran.
_SANDBOX = r"""
const vm = require('vm');
const timers = [];   // {fn, delai} — ce que submitSegmentJob passe à setTimeout
const fetchs = [];   // les POST émis
let fetchMode = 'ko';
function FormData() { this.champs = {}; }
FormData.prototype.append = function (k, v) { this.champs[k] = String(v); };
function Element(id) {
  this.id = id; this.textContent = ''; this.hidden = true; this.value = ''; this.disabled = false;
  this._on = {}; this._classe = '';
  const self = this;
  this.classList = { toggle: function (c, on) { self._classe = on ? c : ''; } };
  this.addEventListener = function (ev, fn) { self._on[ev] = fn; };
}
const elements = {};
function el(id) { if (!elements[id]) elements[id] = new Element(id); return elements[id]; }
const store = {};
const doublure = new Proxy(function () {}, {
  get: function (t, p) {
    if (p === Symbol.toPrimitive || p === 'toString' || p === 'valueOf') return function () { return ''; };
    if (p === 'length') return 0;
    return doublure;
  },
  set: function () { return true; }, apply: function () { return doublure; },
  construct: function () { return doublure; }, has: function () { return true; }
});
const reel = {
  document: { getElementById: function (id) { return elements[id] || null; } },
  window: { localStorage: {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; } } },
  module: undefined,
  console: console, JSON: JSON, Math: Math, Promise: Promise, Array: Array, String: String,
  Object: Object, Date: Date, Error: Error, FormData: FormData,
  setTimeout: function (fn, delai) { timers.push({ fn: fn, delai: delai }); return timers.length; },
  clearTimeout: function () {},
  recFetch: function (url, opts) {
    fetchs.push({ url: url, champs: opts && opts.body ? opts.body.champs : null });
    if (fetchMode === 'ko') return Promise.reject(new Error('réseau KO'));
    if (fetchMode === 'http-ko') return Promise.resolve({ ok: false });
    return Promise.resolve({ ok: true });
  }
};
const bac = new Proxy(reel, {
  has: function () { return true; },
  get: function (t, p) { return (p in t) ? t[p] : doublure; },
  set: function (t, p, v) { t[p] = v; return true; }
});
vm.createContext(bac);
function jouer(code) { return vm.runInContext(code, bac); }
async function tick() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
%(scenario)s
"""


def _bac(scenario: str) -> dict:
    return _node(_SANDBOX % {"scenario": scenario})


def _js(code: str) -> str:
    return "jouer(%s);" % json.dumps(code)


# --------------------------------------------------------------------------- #
# 1. submitSegmentJob : relance bornée, visible, par session — les deux écrans
# --------------------------------------------------------------------------- #

def _scenario_relance(ecran: Path) -> str:
    src = _sans_commentaires(ecran.read_text(encoding="utf-8"))
    fonction = _fonction(src, "submitSegmentJob")
    return _js(fonction) + r"""
(async () => {
  const out = {};
  Object.assign(bac, {
    transcriptSoFar: 'un deux trois', coveredLen: 0, pendingSegmentJobSubmits: 0,
    segmentJobPosition: 0, flightSliceEnd: -1, recuperesEnAttente: [], recordingActive: true,
    jobEchecsConsecutifs: 0, backupGeneration: 0, recGeneration: 0,
    JOB_RETRY_DELAYS_MS: [15000, 45000], NET_TIMEOUT_MS: 1000, sessionToken: 'tok',
    jobStatusEl: el('rec-job-status'),
    updateSubmitState: function () {}, refreshSegmentTail: function () {},
    persistDraft: function () {}, noterEvenement: function () {}, postRecoveredJob: function () {}
  });
  fetchMode = 'ko';
  jouer('submitSegmentJob();'); await tick();
  out.echec1_compteur = bac.jobEchecsConsecutifs;
  out.echec1_delai = timers.length ? timers[0].delai : null;
  out.echec1_message = bac.jobStatusEl.textContent;
  out.echec1_coveredLen = bac.coveredLen;
  out.echec1_en_vol = bac.pendingSegmentJobSubmits;
  timers[0].fn(); await tick();                 // la relance rejoue submitSegmentJob
  out.echec2_posts = fetchs.length;
  out.echec2_delai = timers.length > 1 ? timers[1].delai : null;
  out.echec2_texte_identique = fetchs[1].champs.text === fetchs[0].champs.text;
  timers[1].fn(); await tick();
  out.echec3_minuteurs = timers.length;         // budget épuisé : plus de relance
  out.echec3_message = bac.jobStatusEl.textContent;
  fetchMode = 'ok'; bac.jobStatusEl.textContent = 'reste';
  jouer('submitSegmentJob();'); await tick();
  out.succes_compteur = bac.jobEchecsConsecutifs;
  out.succes_coveredLen = bac.coveredLen;
  out.succes_message = bac.jobStatusEl.textContent;
  fetchMode = 'http-ko'; bac.transcriptSoFar += ' quatre';
  jouer('submitSegmentJob();'); await tick();
  out.http_ko_compteur = bac.jobEchecsConsecutifs;
  out.http_ko_delai = timers[timers.length - 1].delai;
  bac.recordingActive = false; const avant = timers.length; fetchMode = 'ko';
  bac.transcriptSoFar += ' cinq'; bac.jobEchecsConsecutifs = 0;
  jouer('submitSegmentJob();'); await tick();
  out.arret_minuteurs = timers.length - avant;  // enregistrement arrêté : aucune relance
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module", params=[LIBRE, STRUCTURE], ids=lambda p: p.name)
def relance(request) -> dict:
    return _bac(_scenario_relance(request.param))


def test_un_echec_de_creation_est_dit_et_rejoue_a_quinze_secondes(relance: dict) -> None:
    assert relance["echec1_compteur"] == 1
    assert relance["echec1_delai"] == 15000, "délai réellement passé à setTimeout"
    assert "nouvelle tentative dans 15 s" in relance["echec1_message"]
    assert relance["echec1_coveredLen"] == 0, "le curseur n'avance jamais sur échec"
    assert relance["echec1_en_vol"] == 0, "le compteur de POST en vol redescend"


def test_la_relance_rejoue_la_meme_tranche_puis_attend_quarante_cinq_secondes(relance: dict) -> None:
    assert relance["echec2_posts"] == 2
    assert relance["echec2_texte_identique"] is True
    assert relance["echec2_delai"] == 45000


def test_le_budget_epuise_garde_le_texte_et_le_dit(relance: dict) -> None:
    assert relance["echec3_minuteurs"] == 2, "deux délais = deux relances, pas une de plus"
    assert "conservé" in relance["echec3_message"]


def test_un_succes_remet_le_compteur_a_zero_et_avance_le_curseur(relance: dict) -> None:
    assert relance["succes_compteur"] == 0
    assert relance["succes_coveredLen"] == len("un deux trois")
    assert relance["succes_message"] == ""


def test_une_reponse_http_non_ok_compte_comme_un_echec(relance: dict) -> None:
    assert relance["http_ko_compteur"] == 1
    assert relance["http_ko_delai"] == 15000


def test_aucune_relance_une_fois_l_enregistrement_arrete(relance: dict) -> None:
    assert relance["arret_minuteurs"] == 0


# --------------------------------------------------------------------------- #
# 2. proposerReprise : bandeau, restauration, ignorer, discret — écran libre
# --------------------------------------------------------------------------- #

def _bloc_reprise() -> str:
    src = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    m = re.search(r"function\s+proposerReprise\s*\(", src)
    assert m, "bloc proposerReprise introuvable"
    return "(function proposerReprise() %s)();" % _bloc(src, m.start())


def _scenario_reprise() -> str:
    rec_draft = REC_DRAFT.read_text(encoding="utf-8")
    bloc = _bloc_reprise()
    return _js(rec_draft) + r"""
const out = {};
const appels = { updateSubmitState: 0, refreshSegmentTail: 0, refreshBackupList: 0 };
Object.assign(bac, {
  MISSION_ID: 19, transcriptSoFar: '', sessionToken: '', coveredLen: 0, segmentJobPosition: 0,
  backupSegments: [], nextBackupPosition: 0,
  transcriptEl: el('t'), transcriptHidden: el('th'), sessionTokenHidden: el('st'),
  audioSegmentsHidden: el('as'), backupPathHidden: el('bp'), statusEl: el('status'),
  updateSubmitState: function () { appels.updateSubmitState++; },
  refreshSegmentTail: function () { appels.refreshSegmentTail++; },
  refreshBackupStatus: function () {}, refreshBackupList: function () { appels.refreshBackupList++; },
  confirm: function () { return true; }
});
el('rec-draft-banner'); el('rec-draft-text'); el('rec-draft-restore'); el('rec-draft-discard');
const BLOC = %(bloc)s;
function rejouer() { el('rec-draft-banner').hidden = true; el('rec-draft-banner')._classe = ''; jouer(BLOC); }

// a. rien à proposer : aucun brouillon
rejouer();
out.rien_sans_brouillon = el('rec-draft-banner').hidden;

// b. brouillon en attente -> bandeau, puis « Reprendre »
jouer("recDraft.sauver(19, {transcript: 'texte restauré', sessionToken: 'tok-x', coveredLen: 5, segmentJobPosition: 2, backupSegments: [{filename: 'a.webm', position: 0}]})");
rejouer();
out.bandeau_visible = !el('rec-draft-banner').hidden;
out.libelle = el('rec-draft-text').textContent;
out.texte_avant_clic = bac.transcriptSoFar;
el('rec-draft-restore')._on.click();
out.texte_apres = bac.transcriptSoFar;
out.textarea = bac.transcriptEl.value;
out.champ_cache = bac.transcriptHidden.value;
out.coveredLen = bac.coveredLen;
out.position = bac.segmentJobPosition;
out.jeton = bac.sessionToken;
out.jeton_formulaire = bac.sessionTokenHidden.value;
out.audio_formulaire = bac.audioSegmentsHidden.value;
out.backup_suivant = bac.nextBackupPosition;
out.bandeau_cache_apres_clic = el('rec-draft-banner').hidden;
out.submit_recalcule = appels.updateSubmitState;
out.tail_recalcule = appels.refreshSegmentTail;
out.brouillon_intact_apres_reprise = jouer("recDraft.charger(19) !== null");

// c. curseur borné par le texte (brouillon incohérent)
jouer("recDraft.sauver(19, {transcript: 'abc', coveredLen: 999})");
bac.transcriptSoFar = ''; rejouer(); el('rec-draft-restore')._on.click();
out.curseur_borne = bac.coveredLen;

// d. « Ignorer » efface
jouer("recDraft.sauver(19, {transcript: 'à jeter'})");
bac.transcriptSoFar = ''; rejouer(); el('rec-draft-discard')._on.click();
out.ignore_efface = jouer("recDraft.charger(19) === null");
out.ignore_cache = el('rec-draft-banner').hidden;

// e. brouillon envoyé -> mode discret (classe posée), toujours restaurable
jouer("recDraft.sauver(19, {transcript: 'envoyé'}); recDraft.marquerEnvoye(19);");
bac.transcriptSoFar = ''; rejouer();
out.discret_visible = !el('rec-draft-banner').hidden;
out.discret_classe = el('rec-draft-banner')._classe;

// f. texte déjà rendu par le serveur : rien proposé
bac.transcriptSoFar = 'rendu par le serveur'; rejouer();
out.rien_si_texte_serveur = el('rec-draft-banner').hidden;
console.log(JSON.stringify(out));
""" % {"bloc": json.dumps(bloc)}


@pytest.fixture(scope="module")
def reprise() -> dict:
    return _bac(_scenario_reprise())


def test_rien_n_est_propose_sans_brouillon(reprise: dict) -> None:
    assert reprise["rien_sans_brouillon"] is True


def test_le_bandeau_apparait_et_rien_n_est_restaure_avant_le_clic(reprise: dict) -> None:
    assert reprise["bandeau_visible"] is True
    assert "%d caractères" % len("texte restauré") in reprise["libelle"]
    assert reprise["texte_avant_clic"] == ""


def test_reprendre_restaure_texte_curseurs_et_jeton_dans_le_formulaire(reprise: dict) -> None:
    assert reprise["texte_apres"] == "texte restauré"
    assert reprise["textarea"] == "texte restauré"
    assert reprise["champ_cache"] == "texte restauré"
    assert reprise["coveredLen"] == 5, "forcer 0 renverrait tout l'entretien en reliquat : tours dupliqués"
    assert reprise["position"] == 2
    assert reprise["jeton"] == "tok-x"
    assert reprise["jeton_formulaire"] == "tok-x"
    assert json.loads(reprise["audio_formulaire"]) == [{"filename": "a.webm", "position": 0}]
    assert reprise["backup_suivant"] == 1
    assert reprise["bandeau_cache_apres_clic"] is True
    assert reprise["submit_recalcule"] >= 1
    assert reprise["tail_recalcule"] >= 1
    assert reprise["brouillon_intact_apres_reprise"] is True, "un F5 juste après doit le reproposer"


def test_le_curseur_restaure_est_borne_par_le_texte(reprise: dict) -> None:
    assert reprise["curseur_borne"] == 3


def test_ignorer_efface_le_brouillon_et_cache_le_bandeau(reprise: dict) -> None:
    assert reprise["ignore_efface"] is True
    assert reprise["ignore_cache"] is True


def test_un_brouillon_envoye_est_propose_en_mode_discret(reprise: dict) -> None:
    assert reprise["discret_visible"] is True
    assert reprise["discret_classe"] == "rec-draft-discret"


def test_rien_n_est_propose_quand_le_serveur_a_rendu_un_texte(reprise: dict) -> None:
    assert reprise["rien_si_texte_serveur"] is True


# --------------------------------------------------------------------------- #
# 3. persistDraft : le filet hors service est DIT, une fois, sans bloquer (F4)
# --------------------------------------------------------------------------- #

def _scenario_persist() -> str:
    src = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    return _js("var brouillonIndisponibleDit = false;\n" + _fonction(src, "persistDraft")) + r"""
const out = {};
let sauverRend = true; const ecrits = [];
Object.assign(bac, {
  MISSION_ID: 19, transcriptSoFar: 'abc', sessionToken: 'tok', coveredLen: 1, segmentJobPosition: 1,
  backupSegments: [], recDraft: {
    charger: function () { return { journal: [{ t: 1 }] }; },
    sauver: function (id, etat) { ecrits.push(etat); return sauverRend; }
  }
});
el('rec-draft-status');
jouer('persistDraft();');
out.ecrit_texte = ecrits[0].transcript;
out.journal_conserve = ecrits[0].journal.length;
out.envoyeLe_absent = !('envoyeLe' in ecrits[0]);
out.silence_quand_ok = el('rec-draft-status').textContent;
sauverRend = false;
jouer('persistDraft();'); jouer('persistDraft();');
out.message = el('rec-draft-status').textContent;
out.dit_une_fois = bac.brouillonIndisponibleDit;
out.ecrits = ecrits.length;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def persist() -> dict:
    return _bac(_scenario_persist())


def test_le_brouillon_porte_le_texte_et_garde_le_journal(persist: dict) -> None:
    assert persist["ecrit_texte"] == "abc"
    assert persist["journal_conserve"] == 1
    assert persist["envoyeLe_absent"] is True, "un nouveau texte n'hérite pas du marquage « envoyé »"
    assert persist["silence_quand_ok"] == ""


def test_le_stockage_indisponible_est_dit_une_fois_sans_bloquer(persist: dict) -> None:
    assert "indisponible" in persist["message"]
    assert persist["dit_une_fois"] is True
    assert persist["ecrits"] == 3, "on continue d'essayer, on ne bloque rien"
