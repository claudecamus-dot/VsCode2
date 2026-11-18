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
  backupSegments: [], nextBackupPosition: 0, recordingActive: false, finalizing: false,
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
const window = globalThis;
const Date = { now: function () { return clock; } };
function setTimeout(fn, d) { timers.push(fn); }
function mk(id) { return { id: id, hidden: true, disabled: false, textContent: '', value: '', max: 0, h: {},
  classList: { add: function () {}, remove: function () {} },
  addEventListener: function (n, f) { this.h[n] = f; }, click: function () {} }; }
const els = {}; ['seg-progress', 'seg-bar', 'seg-exit', 'seg-exit-copy', 'seg-exit-status',
  'seg-exit-download', 'seg-exit-finalize'].forEach(function (i) { els[i] = mk(i); });
let annonces = 0, annonce = '';
els['seg-exit-text'] = mk('seg-exit-text');
let cacheALEcriture = null;
Object.defineProperty(els['seg-exit-text'], 'textContent', { get: function () { return annonce; },
  set: function (v) { annonces++; annonce = v; cacheALEcriture = els['seg-exit'].hidden; } });
const champ = mk('t'); champ.value = 'texte de l entretien';
const incomplet = mk('i');
els['finalize-form'] = { submit: function () { soumis++; },
  querySelector: function (s) {
    if (s === 'input[name="transcript"]') return champ;
    if (s === 'input[name="transcription_incomplete"]') return %(a_incomplet)s ? incomplet : null;
    return null; } };
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
%(module)s
%(script)s
(async () => {
  const out = {}; await tick();
  %(corps)s
  console.log(JSON.stringify(out));
})();
"""
SEG_EXIT = LIBRE.parents[2] / "static" / "seg_wait_exit.js"


_RENDUS: dict[str, tuple[str, int]] = {}


def _rendre_attente(nom: str) -> tuple[str, int]:
    """Revue finale 3.3 (R12) : la page d'attente REELLEMENT servie (TestClient),
    pas le gabarit brut aux `{{ }}` remplaces. Rend (html, mission_id)."""
    if nom in _RENDUS:
        return _RENDUS[nom]
    import uuid

    from fastapi.testclient import TestClient

    from app.db import SessionLocal, init_db
    from app.main import app
    from app.models import InterviewSegmentJob, Mission

    init_db()
    c = TestClient(app)
    libre = nom == "libre_segment_wait.html"
    r = c.post("/entretiens/libre/nouveau" if libre else "/entretiens/structure/nouveau",
               follow_redirects=False)
    mid = int(r.headers["location"].split("/")[2])
    jeton = "s33w" + uuid.uuid4().hex[:12]
    with SessionLocal() as db:
        db.get(Mission, mid).is_draft = False
        db.add(InterviewSegmentJob(session_token=jeton, kind="libre_turns" if libre else "answers",
                                   mission_id=mid, position=0, status="pending", text="Tranche en cours."))
        db.commit()
    page = c.post(f"/missions/{mid}/interviews/{'record-libre' if libre else 'record'}",
                  data={"transcript": "Bonjour, texte de l entretien.", "session_token": jeton})
    assert page.status_code == 200 and 'id="finalize-form"' in page.text, page.text[:300]
    assert "Traitement des tranches en cours" in page.text
    _RENDUS[nom] = (page.text, mid)
    return _RENDUS[nom]


def _formulaire_finalisation(html: str) -> tuple[str, dict[str, str]]:
    """(action, champs caches) du formulaire de finalisation tel que rendu."""
    import html as _html
    import re
    debut = html.index('<form id="finalize-form"')
    bloc = html[debut:html.index("</form>", debut)]
    action = _html.unescape(re.search(r'action="([^"]+)"', bloc).group(1))
    champs = {_html.unescape(n): _html.unescape(v) for n, v in
              re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', bloc)}
    return action, champs


def _attente(ecran: Path, prep: str, corps: str) -> dict:
    html, _mid = _rendre_attente(ecran.name)
    assert '<script src="/static/seg_wait_exit.js"></script>' in html, "module partage non charge"
    debut = html.index("<script>", html.index('id="finalize-form"')) + len("<script>")
    script = html[debut:html.index("</script>", debut)]
    _action, champs = _formulaire_finalisation(html)
    return _node_fichier(_ATTENTE % {"prep": prep, "script": script, "corps": corps,
                                     "module": SEG_EXIT.read_text(encoding="utf-8"),
                                     "a_incomplet": "true" if "transcription_incomplete" in champs else "false"})


_ETAT_SORTIE = "({ sortie: els['seg-exit'].hidden === false, soumis: soumis })"


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_401_garde_copie_et_telechargement_mais_pas_finaliser(ecran: Path) -> None:
    """Revue 3.3 #2 : session expiree -> panneau visible, « Finaliser » masque ET
    desactive (le POST quitterait vers la connexion), copie/telechargement restent."""
    r = _attente(ecran, "reponses.push({ status: 401, data: {} });", """
out.a = %(e)s; out.progression = els['seg-progress'].textContent; out.message = annonce;
out.fin = { cache: els['seg-exit-finalize'].hidden, inactif: els['seg-exit-finalize'].disabled };
els['seg-exit-finalize'].h.click(); out.apres_clic = soumis;
els['seg-exit-copy'].h.click(); await tick(); out.copie = copie;
""" % {"e": _ETAT_SORTIE})
    assert r["a"] == {"sortie": True, "soumis": 0}
    assert "Session expirée" in r["progression"] and "désactivé" in r["message"]
    assert r["fin"] == {"cache": True, "inactif": True} and r["apres_clic"] == 0
    assert r["copie"] == "texte de l entretien"


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_total_zero_repete_propose_la_sortie(ecran: Path) -> None:
    r = _attente(ecran, "reponses.push(VIDE, VIDE, VIDE);", """
out.deux = (await suivant(), %(e)s);
await suivant(); out.trois = %(e)s;
""" % {"e": _ETAT_SORTIE})
    assert r["deux"] == {"sortie": False, "soumis": 0}
    assert r["trois"] == {"sortie": True, "soumis": 0}


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_bornee_dans_le_temps_puis_copie_telechargement(ecran: Path) -> None:
    r = _attente(ecran, "reponses.push(EN_COURS, EN_COURS);", """
out.avant = %(e)s;
clock = 21 * 60 * 1000; await suivant();
out.apres = %(e)s;
els['seg-exit-copy'].h.click(); await tick(); out.copie = copie;
els['seg-exit-download'].h.click(); out.telecharge = telecharge;
""" % {"e": _ETAT_SORTIE})
    assert r["avant"] == {"sortie": False, "soumis": 0}
    assert r["apres"] == {"sortie": True, "soumis": 0}
    assert r["copie"] == "texte de l entretien" and r["telecharge"] == 1


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_echecs_reseau_repetes_proposent_la_sortie(ecran: Path) -> None:
    r = _attente(ecran, "for (let i = 0; i < 6; i++) reponses.push('reseau');", """
for (let i = 0; i < 4; i++) await suivant();
out.cinq = %(e)s;
await suivant(); out.six = %(e)s;
""" % {"e": _ETAT_SORTIE})
    assert r["cinq"] == {"sortie": False, "soumis": 0}
    assert r["six"] == {"sortie": True, "soumis": 0}


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_message_ecrit_une_fois_retour_copie_garde_puis_masque_au_retour_du_suivi(ecran: Path) -> None:
    """Revue 3.3 #3 : les polls qui continuent apres le clic ne re-ecrivent pas le noeud
    role=alert ni n'effacent « copiee » ; un poll sain re-masque le panneau."""
    r = _attente(ecran, "for (let i = 0; i < 9; i++) reponses.push('reseau'); reponses.push(EN_COURS);", """
for (let i = 0; i < 5; i++) await suivant();
out.ouvert = { sortie: els['seg-exit'].hidden === false, annonces: annonces };
els['seg-exit-copy'].h.click(); await tick();
for (let i = 0; i < 3; i++) await suivant();
out.apres_polls = { annonces: annonces, statut: els['seg-exit-status'].textContent };
await suivant();
out.sain = { sortie: els['seg-exit'].hidden === false };
""")
    assert r["ouvert"] == {"sortie": True, "annonces": 1}
    assert r["apres_polls"]["annonces"] == 1 and "copiée" in r["apres_polls"]["statut"], r
    assert r["sain"] == {"sortie": False}


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_finalisation_manuelle_signale_l_incomplet_et_ne_dit_pas_termine(ecran: Path) -> None:
    """Revue 3.3 #5 + revue finale R2/R11a (les DEUX pages, page rendue) : finaliser a
    la main pendant des tranches en cours envoie transcription_incomplete=1 (champ
    present dans le formulaire rendu, sinon le harnais echoue), n'affiche pas
    « Traitement termine », et « Finaliser » reste desactive apres l'envoi, meme
    apres un poll sain."""
    r = _attente(ecran, "reponses.push(EN_COURS, EN_COURS, EN_COURS);", """
clock = 21 * 60 * 1000; await suivant();
els['seg-exit-finalize'].h.click();
Object.assign(out, { soumis: soumis, incomplet: incomplet.value, progression: els['seg-progress'].textContent,
                     desactive: els['seg-exit-finalize'].disabled });
await suivant(); out.desactive_apres_poll = els['seg-exit-finalize'].disabled;
els['seg-exit-finalize'].h.click(); out.soumis2 = soumis;
""")
    assert r["soumis"] == 1 and r["incomplet"] == "1", r
    assert r["desactive"] is True and r["desactive_apres_poll"] is True and r["soumis2"] == 1, r
    assert "Traitement terminé" not in r["progression"], r
    assert r["progression"].startswith("Finalisation avec le texte disponible"), r


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


# --------------------------------------------------------------------------- #
# Tour de correctifs (revue de code 3.3)
# --------------------------------------------------------------------------- #
def _guide(corps: str, *, remote: bool = True) -> dict:
    """record.html (mode guide) : vraies fonctions d'alerte + vrai succes de re-partage."""
    src = _sans_commentaires(STRUCTURE.read_text(encoding="utf-8"))
    code = "".join(_js(_fonction(src, f)) for f in ("noteNoSpeech", "afficherMuet", "resetNoSpeech"))
    perdu = "(function () %s)();" % _bloc(src, src.index("tabLost: function"))
    depart = src.index("then(function (flux)", src.index("function demanderPartage"))
    partage_ok = "(function (flux) %s)({});" % _bloc(src, depart)
    return _bac(code + r"""
let out = {};
Object.assign(bac, {
  noSpeechStreak: 0, NOSPEECH_ALERT_AFTER: 2, recordingActive: true,
  noSpeechBannerEl: el('b'), noSpeechTextEl: el('t'), noSpeechFixBtn: el('f'), statusEl: el('s'),
  remoteModeEl: { checked: %(remote)s }, swapSource: function () { return false; }, refreshSource: function () {},
  i2dAudioSource: { supported: function () { return true; }, tabActive: function () { return true; } }
});
const muet = (n) => { for (let k = 0; k < n; k++) jouer('noteNoSpeech()'); };
const partage_perdu = () => jouer(%(perdu)s);
const repartage_ok = () => jouer(%(ok)s);
const parole = () => jouer('resetNoSpeech()');
const etat = () => ({ cache: el('b').hidden, texte: el('t').textContent, serie: jouer('noSpeechStreak') });
%(corps)s
console.log(JSON.stringify(out));
""" % {"remote": "true" if remote else "false", "perdu": json.dumps(perdu), "ok": json.dumps(partage_ok),
       "corps": corps})


def test_mode_guide_re_partage_garde_l_alerte_muette_jusqu_a_la_parole() -> None:
    """Revue 3.3 #1 (AC3.3.3 sur record.html) : apres un re-partage reussi, l'alerte
    « aucune parole » en cours reste (texte muet, plus « partage arrete ») ; la parole la leve."""
    r = _guide("""
muet(2); partage_perdu();
out.perdu = etat();
repartage_ok(); out.repartage = etat();
parole(); out.parole = etat();
""")
    assert "partage du son" in r["perdu"]["texte"] and r["perdu"]["cache"] is False
    assert r["repartage"]["cache"] is False and "Aucune parole" in r["repartage"]["texte"], r
    assert r["repartage"]["serie"] == 2
    assert r["parole"]["cache"] is True


def test_mode_guide_re_partage_sans_serie_muette_masque_le_bandeau() -> None:
    r = _guide("partage_perdu(); repartage_ok(); out = etat();")
    assert r["cache"] is True and r["serie"] == 0


def test_demarrage_reussi_seul_masque_la_reprise() -> None:
    """Revue 3.3 #4 : un clic sur « Demarrer » ne masque rien par lui-meme (micro refuse,
    confirmation refusee : la proposition reste) ; la suite REUSSIE du demarrage la masque."""
    from test_repartition_live import _corps_du_demarrage
    demarrage = _corps_du_demarrage(_sans_commentaires(LIBRE.read_text(encoding="utf-8")))
    r = _reprise("const DEMARRAGE = %s;" % json.dumps(demarrage) + """
bac.draftBannerEl = el('rec-draft-banner');
rendre();
out.clic_seul = { ecouteur: typeof el('rec-start')._on.click, cache: el('rec-draft-banner').hidden };
try { jouer(DEMARRAGE); } catch (e) { out.erreur = String(e); }
out.apres_succes = el('rec-draft-banner').hidden;
""")
    assert r["clic_seul"] == {"ecouteur": "undefined", "cache": False}, r
    assert r["apres_succes"] is True, r


def test_vrai_upload_reponse_204_corps_vide_vaut_aucune_parole() -> None:
    """Revue 3.3 #6 : 204 sans corps -> pas de JSON.parse qui leve (chemin reseau),
    mais « aucune parole » non bloquant."""
    r = _upload(r"""
recFetch = function () { return Promise.resolve({ ok: true, status: 204,
  text: function () { return Promise.resolve(''); },
  json: function () { return Promise.reject(new SyntaxError('Unexpected end of JSON input')); } }); };
(async () => {
  const out = {}; recordingActive = true;
  enqueueSegment({ type: 'audio/webm' }); await tick();
  enqueueSegment({ type: 'audio/webm' }); await tick();
  out.etat = %(etat)s; out.perdus = lostSegments.length; out.timers = timers.length;
  console.log(JSON.stringify(out));
})();
""" % {"etat": _ETAT})
    assert r["etat"]["cache"] is False and r["etat"]["serie"] == 2, r
    assert r["etat"]["bloquants"] == 0 and r["perdus"] == 2 and r["timers"] == 0, r


def test_brouillon_dates_hors_plage_ni_nan_ni_null() -> None:
    """Revue 3.3 #7 : savedAt / envoyeLe hors plage -> « a une date inconnue »."""
    r = _node_fichier("const window = {};\n" + REC_DRAFT.read_text(encoding="utf-8") + r"""
window.recDraft = module.exports;
const a = window.recDraft.decision({ transcript: 'x', savedAt: 1e20 }, 1e12);
const b = window.recDraft.decision({ transcript: 'x', savedAt: 1e12, envoyeLe: 1e20 }, 1e12);
console.log(JSON.stringify({ a: a.libelle, b: b.libelle }));
""")
    for libelle in (r["a"], r["b"]):
        assert "NaN" not in libelle and "null" not in libelle and "date inconnue" in libelle, r


def test_release_libere_le_mutex_de_partage() -> None:
    """Revue 3.3 #8 : un partage reste en vol a l'arret ne bloque pas celui de la session suivante."""
    r = _audio("""
src.shareTab().catch(function () {});
src.release();
let erreur = null;
src.shareTab().then(function () {}, function (e) { erreur = e.message; });
await tick();
Object.assign(out, { erreur: erreur });
""")
    assert r == {"erreur": None}


def test_recfetch_retire_l_ecouteur_du_signal_appelant() -> None:
    """Revue 3.3 #9 : apres lecture du corps (ou echec reseau), plus d'ecouteur sur le
    signal de l'appelant."""
    r = _node_fichier(r"""
const window = {}; function setTimeout() { return 1; } function clearTimeout() {}
let mode = 'ok';
function fetch() { return mode === 'ok'
  ? Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } })
  : Promise.reject(new Error('reseau')); }
""" + REC_FETCH.read_text(encoding="utf-8") + r"""
function signal() { return { aborted: false, ls: [], addEventListener: function (n, f) { this.ls.push(f); },
  removeEventListener: function (n, f) { this.ls = this.ls.filter(function (g) { return g !== f; }); } }; }
(async () => {
  const s1 = signal(); const res = await window.recFetch('/a', { signal: s1 }, 1000); await res.json();
  mode = 'ko'; const s2 = signal();
  try { await window.recFetch('/b', { signal: s2 }, 1000); } catch (e) {}
  console.log(JSON.stringify({ ok: s1.ls.length, ko: s2.ls.length }));
})();
""")
    assert r == {"ok": 0, "ko": 0}


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_session_expiree_collante_jusqu_au_200_authentifie(ecran: Path) -> None:
    """Classe perte de donnees : 401 puis coupures reseau -> la raison change mais
    « Finaliser » reste masque/desactive ; un 200 authentifie retablit les regles normales."""
    r = _attente(ecran, "reponses.push({ status: 401, data: {} });"
                 " for (let i = 0; i < 7; i++) reponses.push('reseau'); reponses.push(EN_COURS, EN_COURS);", """
const fin = () => ({ cache: els['seg-exit-finalize'].hidden, inactif: els['seg-exit-finalize'].disabled,
                     sortie: els['seg-exit'].hidden === false });
out.session = fin();
for (let i = 0; i < 7; i++) await suivant();
out.coupures = fin(); out.message = annonce;
els['seg-exit-finalize'].h.click(); out.clic_bloque = soumis;
await suivant(); out.sain = fin();
clock = 21 * 60 * 1000; await suivant(); out.borne = fin();
els['seg-exit-finalize'].h.click(); out.clic_ok = soumis;
""")
    assert r["session"] == {"cache": True, "inactif": True, "sortie": True}
    assert r["coupures"] == {"cache": True, "inactif": True, "sortie": True}, r
    assert "session a expiré" in r["message"] and r["clic_bloque"] == 0, r
    assert r["sain"] == {"cache": False, "inactif": False, "sortie": False}, r
    assert r["borne"] == {"cache": False, "inactif": False, "sortie": True}, r
    assert r["clic_ok"] == 1


# --------------------------------------------------------------------------- #
# Revue finale 3.3 (2026-11-17)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_finalisation_incomplete_atteint_le_serveur_par_l_action_rendue(ecran: Path, monkeypatch) -> None:
    """R1/R2 : le formulaire REELLEMENT rendu, poste vers SA propre action avec
    transcription_incomplete=1, atteint le serveur : message INFORMATIF, jamais une
    tranche comptee (re-revue 2026-11-17 : tout est rattrape cote serveur, le
    compteur reste celui que le serveur mesure — ici 0) ; sans le drapeau, rien."""
    import html as _html

    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import interviews_record

    html, _mid = _rendre_attente(ecran.name)
    action, champs = _formulaire_finalisation(html)
    assert "transcription_incomplete" in champs, champs
    libre = ecran.name == "libre_segment_wait.html"
    if libre:
        monkeypatch.setattr(interviews_record, "_extraire_tours_libre", lambda *a, **k: {
            "turns": [{"interlocuteur": "A", "question": "Bonjour", "remarque": None, "section_title": None}],
            "identity": {}, "tranches_manquantes": 0})
    else:
        monkeypatch.setattr(interviews_record, "segment_jobs_status", lambda db, tok, **_kw: {
            "jobs": [], "total": 0, "done": 0, "failed": 0, "stale": 0, "all_done": False, "any_failed": False})
        monkeypatch.setattr(interviews_record, "extract_answers_from_text",
                            lambda questions, texte: {999999: {"text": "Une reponse", "verbatims": []}})
    c = TestClient(app)
    pages, infos = {}, {}
    for drapeau in ("", "1"):
        r = c.post(action, data={**champs, "transcription_incomplete": drapeau})
        assert r.status_code == 200, r.text[:300]
        pages[drapeau] = _html.unescape(r.text)
        # le message doit être VISIBLE dans la page rendue, dans les deux modes
        infos[drapeau] = pages[drapeau]
    info = interviews_record.FINALISATION_RATTRAPEE
    assert info not in infos[""], "temoin : sans le drapeau, aucun message"
    assert info in infos["1"], pages["1"][:3000]
    # note d'information, jamais une alerte « ⚠ » (re-revue 2026-11-17)
    assert f'<p class="muted" role="status">{info}</p>' in pages["1"], pages["1"][:3000]
    assert f"⚠ {info}" not in pages["1"]
    for page in pages.values():
        assert "tranche n'a pas pu être structurée" not in page, page[:3000]
        if not libre:
            assert '"tranches_manquantes": 0' in page, page[:3000]


def test_finalisation_incomplete_n_echecs_compte_exactement_n(monkeypatch) -> None:
    """Re-revue 2026-11-17 cas (b) : N tranches restees KO apres la recuperation
    synchrone -> exactement N (pas N+1), jobs conserves ; meme compte sans le drapeau."""
    import html as _html
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import interviews_record

    html, _mid = _rendre_attente("record_segment_wait.html")
    action, champs = _formulaire_finalisation(html)
    n = 3
    jobs = [SimpleNamespace(status="failed", text=f"tranche {i}", error="KO", position=i)
            for i in range(n)] + [SimpleNamespace(status="done", text="ok", error=None, position=n)]
    monkeypatch.setattr(interviews_record, "segment_jobs_status", lambda db, tok, **_kw: {
        "jobs": jobs, "total": len(jobs), "done": 1, "failed": n, "stale": 0,
        "all_done": False, "any_failed": True})
    monkeypatch.setattr(interviews_record, "recover_stalled_or_failed_jobs", lambda db, js: None)
    monkeypatch.setattr(interviews_record, "merge_segment_answers",
                        lambda js, tail: {999999: {"text": "Une reponse", "verbatims": []}})
    supprimes = []
    monkeypatch.setattr(interviews_record, "delete_segment_jobs", lambda db, tok: supprimes.append(tok))
    c = TestClient(app)
    for drapeau in ("", "1"):
        r = c.post(action, data={**champs, "segment_tail": "", "transcription_incomplete": drapeau})
        assert r.status_code == 200, r.text[:300]
        page = _html.unescape(r.text)
        assert f'"tranches_manquantes": {n}' in page, page[:3000]
        assert r.context.get("tranches_manquantes") == n
        assert not r.context.get("finalisation_info")
    assert supprimes == [], "jobs KO conserves pour un nouvel essai"


def test_finalisation_incomplete_sans_reponse_garde_le_message_et_les_jobs(monkeypatch) -> None:
    """Re-revue 2026-11-17 : extraction vide + drapeau -> le garde « aucune reponse
    repartie » s'applique comme avant 3aeb63c (1 tranche signalee, jobs NON supprimes)."""
    import html as _html
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import interviews_record

    html, _mid = _rendre_attente("record_segment_wait.html")
    action, champs = _formulaire_finalisation(html)
    jobs = [SimpleNamespace(status="done", text="ok", error=None, position=0)]
    monkeypatch.setattr(interviews_record, "segment_jobs_status", lambda db, tok, **_kw: {
        "jobs": jobs, "total": 1, "done": 1, "failed": 0, "stale": 0,
        "all_done": True, "any_failed": False})
    monkeypatch.setattr(interviews_record, "merge_segment_answers", lambda js, tail: {})
    supprimes = []
    monkeypatch.setattr(interviews_record, "delete_segment_jobs", lambda db, tok: supprimes.append(tok))
    r = TestClient(app).post(action, data={**champs, "segment_tail": "", "transcription_incomplete": "1"})
    assert r.status_code == 200, r.text[:300]
    page = _html.unescape(r.text)
    assert "1 tranche" in page and "pas pu être" in page, page[:3000]
    assert '"tranches_manquantes": 1' in page
    assert supprimes == []


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_deux_raisons_message_stable_et_demasque_avant_ecriture(ecran: Path) -> None:
    """R8 : borne de temps ET « aucune tranche » en meme temps -> une seule annonce,
    stable d'un poll a l'autre (priorite fixe), ecrite panneau deja visible."""
    r = _attente(ecran, "for (let i = 0; i < 8; i++) reponses.push(VIDE);", """
clock = 21 * 60 * 1000;
for (let i = 0; i < 6; i++) await suivant();
Object.assign(out, { annonces: annonces, message: annonce, cache: cacheALEcriture,
                     sortie: els['seg-exit'].hidden === false });
""")
    assert r["sortie"] is True and r["annonces"] == 1, r
    assert r["message"].startswith("L'attente dépasse"), r
    assert r["cache"] is False, r


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_session_expiree_et_borne_ne_basculent_pas(ecran: Path) -> None:
    """R8 : 401 repetes passe la borne de temps -> le message session reste, une annonce."""
    r = _attente(ecran, "for (let i = 0; i < 8; i++) reponses.push({ status: 401, data: {} });", """
clock = 21 * 60 * 1000;
for (let i = 0; i < 5; i++) await suivant();
Object.assign(out, { annonces: annonces, message: annonce });
""")
    assert r["annonces"] == 1 and r["message"].startswith("Session expirée"), r


@pytest.mark.parametrize("ecran", ATTENTES, ids=lambda p: p.name)
def test_attente_2xx_sans_total_n_est_pas_une_reponse_vide(ecran: Path) -> None:
    """R11a : un 2xx au corps inattendu (pas de `total`) ne compte pas comme « aucune tranche »."""
    r = _attente(ecran, "for (let i = 0; i < 6; i++) reponses.push({ status: 200, data: {} });", """
for (let i = 0; i < 4; i++) await suivant();
out.e = %(e)s;
""" % {"e": _ETAT_SORTIE})
    assert r["e"] == {"sortie": False, "soumis": 0}, r


def test_partage_perime_regle_apres_nouveau_partage_garde_le_mutex() -> None:
    """R3 : partage A en vol, release(), nouveau partage B ; le reglement tardif de A
    ne rouvre pas le mutex de B -> un 3e partage est refuse, aucun getDisplayMedia concurrent."""
    r = _audio("""
const demandes = []; let appelsDM = 0;
navigator.mediaDevices.getDisplayMedia = function () { appelsDM++;
  return new Promise(function (ok, ko) { demandes.push({ ok: ok, ko: ko }); }); };
src.shareTab().catch(function () {});
src.release();
src.shareTab().catch(function () {});
demandes[0].ko(new Error('refus A')); await tick();
let erreur = null;
src.shareTab().then(function () {}, function (e) { erreur = e.message; }); await tick();
Object.assign(out, { appels: appelsDM, erreur: erreur });
""")
    assert r["appels"] == 2, r
    assert "déjà en cours" in (r["erreur"] or ""), r


@pytest.mark.parametrize("corps", ["", "  \n"], ids=["vide", "blancs"])
@pytest.mark.parametrize("ecran", [LIBRE, STRUCTURE], ids=lambda p: p.name)
def test_upload_2xx_corps_vide_ou_blanc_vaut_aucune_parole(ecran: Path, corps: str) -> None:
    """R4 : sur les DEUX ecrans, un 2xx sans corps JSON (204, ou blancs seuls) ne
    leve pas dans JSON.parse : « aucune parole », ni perdu bloquant ni relance."""
    try:
        from test_transcription_occupee_execution import _lancer
    except ImportError:  # pragma: no cover
        from tests.test_transcription_occupee_execution import _lancer
    r = _lancer(ecran, [], 900000, r"""
let muets = 0;
noteNoSpeech = function () { muets++; };
recFetch = function (url, opts, delai) { fetchs.push({ delai: delai });
  return Promise.resolve({ ok: true, status: 204, text: function () { return Promise.resolve(%s); },
    json: function () { return Promise.reject(new SyntaxError('Unexpected end of JSON input')); } }); };
(async () => {
  uploadSegment({ type: 'audio/webm' }, 0, null, 0, function () { appels.onSettle++; });
  await tick();
  console.log(JSON.stringify({ bloquants: appels.keepLostSegment.filter(function (x) { return x.blocking !== false; }).length, minuteurs: timers.length,
                               fetchs: fetchs.length, muets: muets, settle: appels.onSettle }));
})();
""" % json.dumps(corps))
    assert r == {"bloquants": 0, "minuteurs": 0, "fetchs": 1, "muets": 1, "settle": 1}, r


@pytest.mark.parametrize("etat", ["bac.finalizing = true;", "bac.pendingBackups = 1;", "bac.pendingSegments = 1;"],
                         ids=["derniere_tranche_en_cours", "sauvegarde_en_vol", "segment_en_vol"])
def test_reprendre_juste_apres_arret_ne_reecrit_pas_la_session(etat: str) -> None:
    """R5 : juste apres « Arreter » (texte encore vide, derniere tranche ou envois encore
    en vol), « Reprendre » n'ecrase ni le jeton ni les parties audio."""
    r = _reprise("rendre(); %s el('rec-draft-restore')._on.click();" % etat + """
out = { jeton: bac.sessionToken, segments: bac.backupSegments.map(function (s) { return s.filename; }),
        cache: el('rec-draft-banner').hidden, texte: bac.transcriptSoFar };
""")
    assert r == {"jeton": "vivant", "segments": [], "cache": True, "texte": ""}, r
