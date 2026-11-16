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
