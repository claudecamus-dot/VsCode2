"""Refus "occupé" (503 code busy) d'un segment de transcription — EXÉCUTION
RÉELLE du JS, lot 3 atelier-dev (2026-09-16).

Pendant de `tests/test_record_reseau.py::test_le_refus_occupe_ne_consomme_pas_le_budget_des_echecs_reels`,
dont l'étage « câblage » ne tient que par la présence et l'ordre de chaînes dans
le template — CLAUDE.md l'impose explicitement : « toute assertion d'ORDRE
d'exécution JS passe par le harnais qui exécute réellement le script ». Le test
structurel ne peut RIEN dire sur les bugs de MINUTAGE trouvés par la revue
adversariale du 2026-09-16 et corrigés dans la foulée : `delayMs` non borné,
patience totale doublée par un `recFetch` reparti pour un délai plein, et
surtout la fenêtre de patience `busyUntil` ré-armée par un détour via un échec
transitoire au lieu d'être préservée. Ce module EXÉCUTE `uploadSegment` (et ses
fonctions imbriquées `retryOrGiveUp`/`retryBusyOrGiveUp`) sous node, avec une
horloge et un `recFetch` pilotés à la main — ce qui est vérifié est le délai
RÉELLEMENT passé à `setTimeout`/`recFetch`, l'issue réelle (relance ou
abandon), et l'état du compteur `pendingSegments`, pas la présence d'un mot
dans le HTML.

Aucun accès base : ce module ne touche ni `DB_PATH` ni `SessionLocal`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

try:  # `tests/` sur sys.path (mode d'import par défaut de pytest)
    from test_repartition_live import _fonction, _sans_commentaires
except ImportError:  # `tests` importable comme paquet
    from tests.test_repartition_live import _fonction, _sans_commentaires

RACINE = Path(__file__).resolve().parent.parent
LIBRE = RACINE / "app" / "templates" / "interviews" / "record_libre.html"
STRUCTURE = RACINE / "app" / "templates" / "interviews" / "record.html"
LES_DEUX = [LIBRE, STRUCTURE]

# Script PLAT (pas de vm.createContext) : l'extraction de `uploadSegment` est
# collée telle quelle comme code RÉEL du script, ses identifiants libres
# (recGeneration, TRANSCRIBE_TIMEOUT_MS, recFetch…) résolus par les bindings
# top-level ci-dessous — un closure Node ordinaire, pas un bac à sable vm.
_SCRIPT = r"""
const timers = [];              // {fn, delai} passés à setTimeout, JAMAIS auto-exécutés
const fetchs = [];              // {delai} = 3e argument réellement passé à recFetch
const appels = { keepLostSegment: [], appendTranscript: [], onSettle: 0 };
let clock = 0;
const reponses = %(reponses)s;  // queue consommée dans l'ordre par recFetch
function FormData() { this.champs = {}; }
FormData.prototype.append = function () {};
function File(parts, name, opts) { this.name = name; this.type = opts && opts.type; }
let recGeneration = 0, backupGeneration = 0, pendingSegments = 0;
const TRANSCRIBE_TIMEOUT_MS = %(timeout)s;
const SEGMENT_RETRY_DELAYS_MS = [2000, 6000];
const statusEl = { textContent: '' };
function updateSubmitState() {}
function maybeSubmitFinalJob() {}
function keepLostSegment(blob, reason, lostId, blocking) {
  appels.keepLostSegment.push({ reason: reason, blocking: blocking });
}
function appendTranscript(t) { appels.appendTranscript.push(t); }
function replaceLostMarker() {}
function resetNoSpeech() {}
function noteNoSpeech() {}
function recFetch(url, opts, delai) {
  fetchs.push({ delai: delai });
  const r = reponses.shift();
  if (!r) throw new Error('scenario epuise : plus de reponse en file');
  if (r.reseau_ko) return Promise.reject(new Error('reseau KO'));
  return Promise.resolve({
    ok: r.ok, status: r.status, json: function () { return Promise.resolve(r.data); }
  });
}
const Date = { now: function () { return clock; } };
function setTimeout(fn, delai) { timers.push({ fn: fn, delai: delai }); return timers.length; }
function clearTimeout() {}
async function tick() { for (let i = 0; i < 20; i++) await Promise.resolve(); }

%(fonction)s

%(scenario)s
"""


def _extraire(ecran: Path) -> str:
    contenu = _sans_commentaires(ecran.read_text(encoding="utf-8"))
    return _fonction(contenu, "uploadSegment")


def _node(script: str) -> dict:
    if shutil.which("node") is None:
        # ÉCHEC, pas skip : seule preuve par exécution de ce correctif de
        # minutage. Un skip rendrait la suite verte sans rien avoir vérifié.
        pytest.fail(
            "node est requis pour exécuter réellement le JS de "
            "uploadSegment/retryBusyOrGiveUp (https://nodejs.org). Un skip "
            "rendrait la suite verte sans la moindre preuve d'exécution."
        )
    res = subprocess.run(
        ["node", "--input-type=commonjs", "-e", script],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert res.returncode == 0, res.stderr[:3000]
    return json.loads(res.stdout.strip())


def _lancer(ecran: Path, reponses: list[dict], timeout_ms: int, scenario: str) -> dict:
    script = _SCRIPT % {
        "reponses": json.dumps(reponses),
        "timeout": timeout_ms,
        "fonction": _extraire(ecran),
        "scenario": scenario,
    }
    return _node(script)


# --------------------------------------------------------------------------- #
# 1. Deux refus "occupé" puis un succès : délais bornés, compteur équilibré,
#    jamais de passage par le budget des échecs réels.
# --------------------------------------------------------------------------- #

_SCENARIO_DEUX_OCCUPES_PUIS_SUCCES = r"""
(async () => {
  const out = {};
  uploadSegment({ type: 'audio/webm' }, 0, null, 0, function () { appels.onSettle++; });
  await tick();
  out.appel1_delai_fetch = fetchs[0].delai;          // 1er appel : délai plein
  out.pending_apres_appel1 = pendingSegments;
  out.minuteur1_delai = timers[0].delai;              // retry_after_s=5 -> 5000
  out.message1 = statusEl.textContent;

  clock += timers[0].delai;
  timers[0].fn(); await tick();
  out.appel2_delai_fetch = fetchs[1].delai;           // borné au reliquat, pas au plein
  out.pending_apres_appel2 = pendingSegments;         // jamais recompté

  clock += timers[1].delai;
  timers[1].fn(); await tick();                       // 3e réponse : succès
  out.appel3_delai_fetch = fetchs[2].delai;
  out.pending_final = pendingSegments;                // retombé à 0
  out.transcript = appels.appendTranscript;
  out.onSettle_appele = appels.onSettle;
  out.jamais_abandonne = appels.keepLostSegment.length === 0;
  out.aucun_4e_minuteur = timers.length === 2;         // pas de relance après succès
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module", params=LES_DEUX, ids=lambda p: p.name)
def deux_occupes_puis_succes(request) -> dict:
    reponses = [
        {"ok": False, "status": 503, "data": {"code": "busy", "retry_after_s": 5}},
        {"ok": False, "status": 503, "data": {"code": "busy", "retry_after_s": 5}},
        {"ok": True, "status": 200, "data": {"text": "bonjour"}},
    ]
    return _lancer(request.param, reponses, 900000, _SCENARIO_DEUX_OCCUPES_PUIS_SUCCES)


def test_le_premier_appel_part_avec_le_delai_plein(deux_occupes_puis_succes: dict) -> None:
    assert deux_occupes_puis_succes["appel1_delai_fetch"] == 900000


def test_le_delai_de_relance_suit_retry_after_s(deux_occupes_puis_succes: dict) -> None:
    assert deux_occupes_puis_succes["minuteur1_delai"] == 5000
    assert "occupée" in deux_occupes_puis_succes["message1"]


def test_le_compteur_pending_ne_recompte_pas_les_relances_occupe(deux_occupes_puis_succes: dict) -> None:
    assert deux_occupes_puis_succes["pending_apres_appel1"] == 1
    assert deux_occupes_puis_succes["pending_apres_appel2"] == 1
    assert deux_occupes_puis_succes["pending_final"] == 0


def test_le_delai_de_la_2e_requete_est_borne_au_reliquat_pas_au_plein(deux_occupes_puis_succes: dict) -> None:
    # Reliquat = 900000 - 5000 = 895000, strictement < TRANSCRIBE_TIMEOUT_MS :
    # sans le plafond (revue bmad-code-review 2026-09-16), ce serait 900000.
    assert deux_occupes_puis_succes["appel2_delai_fetch"] == 895000


def test_un_succes_apres_deux_occupes_aboutit_normalement(deux_occupes_puis_succes: dict) -> None:
    assert deux_occupes_puis_succes["transcript"] == ["bonjour"]
    assert deux_occupes_puis_succes["onSettle_appele"] == 1
    assert deux_occupes_puis_succes["jamais_abandonne"] is True
    assert deux_occupes_puis_succes["aucun_4e_minuteur"] is True


# --------------------------------------------------------------------------- #
# 2. Occupé proche de l'expiration -> échec transitoire -> occupé : la fenêtre
#    de patience doit rester celle du PREMIER appel, pas être réarmée par le
#    détour transitoire (revue bmad-code-review 2026-09-16, finding 3).
# --------------------------------------------------------------------------- #

_SCENARIO_FENETRE_PRESERVEE = r"""
(async () => {
  const out = {};
  uploadSegment({ type: 'audio/webm' }, 0, null, 0, function () { appels.onSettle++; });
  await tick();
  // Occupé, patience = 20000. retry_after_s=15 -> delayMs = min(20000,60000,15000)=15000.
  out.minuteur1_delai = timers[0].delai;

  clock += timers[0].delai;  // clock = 15000
  timers[0].fn(); await tick();
  // 2e réponse : échec transitoire (réseau KO) -> retryOrGiveUp -> relance à
  // SEGMENT_RETRY_DELAYS_MS[0] = 2000, en propageant (ou pas, selon le bug)
  // la fenêtre `busyUntil` du tout premier appel.
  out.minuteur2_delai = timers[1] ? timers[1].delai : null;

  clock += timers[1].delai;  // clock = 17000
  timers[1].fn(); await tick();
  // 3e réponse : occupé à nouveau. Reliquat RÉEL (si busyUntil préservé à
  // 20000) = 3000ms ; reliquat FAUSSÉ (si busyUntil ré-armé à 15000+20000) =
  // 20000ms. Le délai réellement passé à setTimeout distingue les deux.
  out.minuteur3_delai = timers[2] ? timers[2].delai : null;

  clock += (timers[2] ? timers[2].delai : 0);  // clock = 20000 si fenêtre préservée
  if (timers[2]) { timers[2].fn(); await tick(); }
  // 4e réponse : occupé une 4e fois, pile à l'expiration de la fenêtre
  // D'ORIGINE (20000). Si `busyUntil` a été préservé : abandon immédiat
  // (`keepLostSegment`), aucun 5e minuteur. Si la fenêtre a été ré-armée par
  // le détour transitoire : encore du temps devant soi, nouvelle relance.
  out.abandonne = appels.keepLostSegment.length > 0;
  out.nb_minuteurs_total = timers.length;
  out.nb_appels_fetch = fetchs.length;
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module", params=LES_DEUX, ids=lambda p: p.name)
def fenetre_preservee(request) -> dict:
    reponses = [
        {"ok": False, "status": 503, "data": {"code": "busy", "retry_after_s": 15}},
        {"reseau_ko": True},
        {"ok": False, "status": 503, "data": {"code": "busy", "retry_after_s": 15}},
        {"ok": False, "status": 503, "data": {"code": "busy", "retry_after_s": 15}},
    ]
    return _lancer(request.param, reponses, 20000, _SCENARIO_FENETRE_PRESERVEE)


def test_le_detour_transitoire_ne_reamorce_pas_la_fenetre_de_patience(fenetre_preservee: dict) -> None:
    # Preuve par exécution du finding 3 (bmad-code-review 2026-09-16) : sans
    # la propagation de `busyUntil` dans le retry transitoire, ce test
    # échouerait ici — `nb_minuteurs_total` vaudrait 4 (une relance de plus,
    # la fenêtre ré-armée ayant encore du temps devant elle) et `abandonne`
    # vaudrait `False`.
    assert fenetre_preservee["minuteur3_delai"] == 3000, (
        "le 3e délai \"occupé\" doit refléter le reliquat de la fenêtre "
        "D'ORIGINE (3000ms), pas une fenêtre ré-armée par le détour transitoire"
    )
    assert fenetre_preservee["abandonne"] is True, (
        "à l'expiration de la fenêtre D'ORIGINE, le segment doit être "
        "abandonné (bandeau, relance manuelle) — pas relancé indéfiniment"
    )
    assert fenetre_preservee["nb_minuteurs_total"] == 3, (
        "aucune 4e relance programmée : la fenêtre de patience est épuisée"
    )
    assert fenetre_preservee["nb_appels_fetch"] == 4
