"""Incident du 2026-10-27 (entretien libre : enregistrement fait, tous les
segments « perdus ») — EXÉCUTION RÉELLE du JS livré, sous node.

Quatre points, chacun prouvé sur le code réel (rec_retry.js chargé tel quel,
fonctions extraites de record_libre.html ou script inline ENTIER) :
  (a) le marqueur d'un segment non transcrit porte la RAISON de l'échec, et
      reste lisible par `ai_common.strip_segment_markers` ;
  (b) délai maximal dépassé : 'attente' si la copie audio est acquittée par le
      serveur, sinon le plan de relance commun (`RETRY_PLAN`) ;
  (c) alerte de retard visible dès 3 segments en attente, masquée en dessous ;
  (d) préchauffage `POST /audio/warm-up` au clic sur Démarrer, une seule fois.

Aucun accès base.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    from test_repartition_live import _fonction, _node, _sans_commentaires
    from test_transcription_occupee_execution import _SCRIPT
    from _harnais_ordre_js import jouer_ecran
except ImportError:
    from tests.test_repartition_live import _fonction, _node, _sans_commentaires
    from tests.test_transcription_occupee_execution import _SCRIPT
    from tests._harnais_ordre_js import jouer_ecran

from app.services.ai_common import strip_segment_markers

RACINE = Path(__file__).resolve().parent.parent
LIBRE = RACINE / "app" / "templates" / "interviews" / "record_libre.html"
REC_RETRY = RACINE / "app" / "static" / "rec_retry.js"


def _plan(corps: str) -> dict:
    return _node("const window = {};\n" + REC_RETRY.read_text(encoding="utf-8")
                 + "\nconst P = window.recRetryPlan;\n" + corps)


# --------------------------------------------------------------------------- #
# Fonctions pures de rec_retry.js
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def pur() -> dict:
    return _plan(r"""
const out = {};
out.raisons = {
  timeout: P.raisonMarqueur('pas de réponse après 900 s', { timeout: true }),
  statut: P.raisonMarqueur('Erreur interne', { statut: 500 }),
  reseau: P.raisonMarqueur('Failed to fetch', { timeout: false }),
  brut: P.raisonMarqueur('a]b[c\nd', null),
  vide: P.raisonMarqueur('', null),
  long: P.raisonMarqueur('x'.repeat(200), null),
};
out.marqueur = P.marqueurPerdu(4, P.raisonMarqueur('', { timeout: true }));
out.marqueur_sans = P.marqueurPerdu(4, '');
out.decisions = {
  copie: P.decisionDelaiDepasse(true, 0, false, false),
  copie_relance_attente: P.decisionDelaiDepasse(true, 0, true, true),
  copie_relance_perdu: P.decisionDelaiDepasse(true, 0, true, false),
  sans_copie_0: P.decisionDelaiDepasse(false, 0, false, false),
  sans_copie_1: P.decisionDelaiDepasse(false, 1, false, false),
  sans_copie_2: P.decisionDelaiDepasse(false, 2, false, false),
};
out.retard = [0, 2, 3, 7, '5', null].map(P.messageRetard);
console.log(JSON.stringify(out));
""")


def test_raison_du_marqueur(pur: dict) -> None:
    r = pur["raisons"]
    assert r["timeout"] == "délai dépassé"
    assert r["statut"] == "refus serveur 500"
    assert r["reseau"] == "réseau"
    assert "[" not in r["brut"] and "]" not in r["brut"] and "\n" not in r["brut"]
    assert r["vide"] == "erreur inconnue"
    assert len(r["long"]) <= 60


def test_le_marqueur_avec_raison_reste_retire_par_le_serveur(pur: dict) -> None:
    assert pur["marqueur"] == "⚠ [segment 4 non transcrit (délai dépassé) — relance possible]"
    assert pur["marqueur_sans"] == "⚠ [segment 4 non transcrit — relance possible]"
    texte = "avant\n\n" + pur["marqueur"] + "\n\naprès"
    assert strip_segment_markers(texte) == "avant\n\naprès"


def test_decision_delai_depasse_avec_et_sans_copie_acquittee(pur: dict) -> None:
    d = pur["decisions"]
    assert d["copie"] == {"action": "attente"}
    assert d["copie_relance_attente"] == {"action": "attente"}
    assert d["copie_relance_perdu"]["action"] == "relance", "relance manuelle d'un vrai perdu : pas d'attente"
    assert d["sans_copie_0"] == {"action": "relance", "delai": 2000, "numero": 2, "total": 3}
    assert d["sans_copie_1"]["delai"] == 6000
    assert d["sans_copie_2"] == {"action": "perdu"}


def test_message_de_retard_au_seuil_de_trois(pur: dict) -> None:
    m = pur["retard"]
    assert m[0] == "" and m[1] == "" and m[5] == ""
    assert m[2] == ("Transcription en retard de 3 segments — elle se poursuit, "
                    "l’enregistrement n’est pas perdu")
    assert "de 7 segments" in m[3] and "de 5 segments" in m[4]


# --------------------------------------------------------------------------- #
# (b) uploadSegment de record_libre.html, exécuté : délai dépassé
# --------------------------------------------------------------------------- #

_SCENARIO = r"""
(async () => {
  const out = {};
  uploadSegment({ type: 'audio/webm' }, 0, null, 0, function () { appels.onSettle++; });
  await tick();
  out.delais = [];
  for (let i = 0; i < 10 && timers[i]; i++) {
    out.delais.push(timers[i].delai);
    clock += timers[i].delai;
    timers[i].fn(); await tick();
  }
  out.perdus = appels.keepLostSegment;
  out.transcript = appels.appendTranscript;
  out.pending = pendingSegments;
  out.onSettle = appels.onSettle;
  console.log(JSON.stringify(out));
})();
"""


def _upload(copie: bool, reponses: list[dict]) -> dict:
    script = _SCRIPT.replace(
        "function copieServeurAcquittee() { return false; }",
        "function copieServeurAcquittee() { return %s; }" % ("true" if copie else "false"),
    ).replace(
        "function keepLostSegment(blob, reason, lostId, blocking) {\n"
        "  appels.keepLostSegment.push({ reason: reason, blocking: blocking });",
        "function keepLostSegment(blob, reason, lostId, blocking, segId, attente, cause) {\n"
        "  appels.keepLostSegment.push({ reason: reason, blocking: blocking, attente: !!attente, cause: cause || null });",
    )
    assert "return %s; }" % ("true" if copie else "false") in script
    assert "attente: !!attente" in script, "le stub keepLostSegment du harnais a changé de forme"
    contenu = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    return _node(script % {
        "reponses": json.dumps(reponses), "timeout": 900000, "gen_var": "backupGeneration",
        "rec_retry": REC_RETRY.read_text(encoding="utf-8"),
        "fonction": _fonction(contenu, "uploadSegment"), "scenario": _SCENARIO,
    })


_TO = {"reseau_ko": True, "recTimeout": True}
_OK = {"ok": True, "status": 200, "data": {"text": "bonjour"}}


def test_delai_depasse_avec_copie_acquittee_passe_en_attente_pas_perdu() -> None:
    r = _upload(True, [_TO])
    assert r["delais"] == [], "copie acquittée : aucune relance des mêmes octets"
    assert len(r["perdus"]) == 1
    p = r["perdus"][0]
    assert p["attente"] is True and p["blocking"] is False
    assert p["cause"] == {"timeout": True}
    assert r["pending"] == 0 and r["onSettle"] == 1


def test_delai_depasse_sans_copie_suit_le_plan_de_relance() -> None:
    r = _upload(False, [_TO, _OK])
    assert r["delais"] == [2000], "1re relance au 1er palier RETRY_PLAN"
    assert r["transcript"] == ["bonjour"] and r["perdus"] == []
    assert r["pending"] == 0 and r["onSettle"] == 1


def test_delai_depasse_sans_copie_budget_epuise_reste_perdu_bloquant() -> None:
    r = _upload(False, [_TO, _TO, _TO])
    assert r["delais"] == [2000, 6000]
    assert len(r["perdus"]) == 1 and r["perdus"][0]["blocking"] is True
    assert r["perdus"][0]["attente"] is False
    assert r["pending"] == 0 and r["onSettle"] == 1


def test_un_refus_serveur_transmet_son_statut_au_marqueur() -> None:
    e500 = {"ok": False, "status": 500, "data": {"error": "Erreur interne"}}
    r = _upload(False, [e500, e500, e500])
    assert r["perdus"][0]["cause"] == {"statut": 500}


# --------------------------------------------------------------------------- #
# (a)(c)(d) script inline ENTIER de record_libre.html
# --------------------------------------------------------------------------- #

_AVANT = "jouer(%s);\nconst postes = [];\n" \
    "g.recFetch = function (url, o, d) { postes.push({ url: url, method: o && o.method });" \
    " return Promise.reject(new Error('404')); };\n"


def _ecran(apres: str) -> dict:
    return jouer_ecran(LIBRE, avant=_AVANT % json.dumps(REC_RETRY.read_text(encoding="utf-8")),
                       apres=apres)


def _fonctions(*noms: str) -> str:
    contenu = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    return "\n".join(_fonction(contenu, n) for n in noms)


def test_le_marqueur_pose_porte_la_raison_et_reste_retrouvable() -> None:
    r = _plan(r"""
const RETRY_PLAN = P;
var lostMarkerTexts = {}, lostSegments = [], waitingSegments = [], nextLostSegmentId = 1;
var backupGeneration = 0, transcriptSoFar = '';
function appendTranscript(t) { transcriptSoFar += t; }
function refreshLostBanner() {}
""" + _fonctions("lostMarker", "keepLostSegment") + r"""
keepLostSegment({}, 'pas de réponse après 900 s', undefined, true, 's-1', false, { timeout: true });
keepLostSegment({}, 'Failed to fetch', undefined, true, 's-2', false, { timeout: false });
keepLostSegment({}, 'nouvelle raison', 1, true, 's-1', false, { statut: 500 });  // relance : marqueur inchangé
console.log(JSON.stringify({ texte: transcriptSoFar, m1: lostMarker(1), m2: lostMarker(2) }));
""")
    assert r["m1"] == "⚠ [segment 1 non transcrit (délai dépassé) — relance possible]"
    assert r["m2"] == "⚠ [segment 2 non transcrit (réseau) — relance possible]"
    assert r["texte"] == r["m1"] + r["m2"], "lostMarker(id) = texte réellement posé (replaceLostMarker le cherche)"


def test_alerte_de_retard_affichee_a_trois_et_masquee_en_dessous() -> None:
    r = _plan(r"""
const RETRY_PLAN = P;
var segmentsRejoues = 0, pendingSegments = 3;
var replayStatusEl = { hidden: true, textContent: '' };
""" + _fonctions("refreshReplayStatus") + r"""
const out = {};
refreshReplayStatus(); out.a3 = { hidden: replayStatusEl.hidden, texte: replayStatusEl.textContent };
segmentsRejoues = 1; refreshReplayStatus(); out.a3r = replayStatusEl.textContent;
segmentsRejoues = 0; pendingSegments = 2; refreshReplayStatus();
out.a2 = { hidden: replayStatusEl.hidden, texte: replayStatusEl.textContent };
console.log(JSON.stringify(out));
""")
    assert r["a3"]["hidden"] is False
    assert "Transcription en retard de 3 segments" in r["a3"]["texte"]
    assert r["a3r"].startswith("1 segment repris automatiquement · Transcription en retard")
    assert r["a2"] == {"hidden": True, "texte": ""}


def test_la_mise_a_jour_du_compteur_rafraichit_l_alerte() -> None:
    # updateSubmitState (appelé à chaque variation de pendingSegments) recale l'alerte.
    contenu = _sans_commentaires(LIBRE.read_text(encoding="utf-8"))
    corps = _fonction(contenu, "updateSubmitState")
    r = _plan(r"""
let appels = 0;
function refreshReplayStatus() { appels++; throw new Error('stop'); }
""" + corps.replace("function updateSubmitState()", "function updateSubmitState_()", 1) + r"""
try { updateSubmitState_(); } catch (e) {}
console.log(JSON.stringify({ appels: appels }));
""")
    assert r["appels"] == 1


def test_role_status_sur_la_ligne_de_l_alerte() -> None:
    # Attribut HTML (pas d'ordre JS) : présence suffit.
    html = LIBRE.read_text(encoding="utf-8")
    assert 'id="rec-replay-status" role="status" aria-live="polite"' in html


def test_demarrer_prechauffe_une_seule_fois_et_echoue_en_silence() -> None:
    r = _ecran(r"""
(async () => {
  const out = { erreur: erreur };
  const btn = el('rec-start');
  try { btn._on.click(); } catch (e) { out.clic1 = e.name + ': ' + e.message; }
  jouer('recordingActive = false;');
  try { btn._on.click(); } catch (e) { out.clic2 = e.name + ': ' + e.message; }
  for (let i = 0; i < 20; i++) await Promise.resolve();
  out.warm = postes.filter(function (p) { return p.url === '/audio/warm-up'; });
  console.log(JSON.stringify(out));
})();
""")
    assert r["erreur"] is None, r["erreur"]
    assert r["warm"] == [{"url": "/audio/warm-up", "method": "POST"}], r
