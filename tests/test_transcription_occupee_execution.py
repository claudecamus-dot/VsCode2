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

Une 2e revue adversariale (bmad-code-review, 2026-09-16, ciblée cette fois sur
la FIDÉLITÉ du harnais lui-même — un bac à sable qui diverge du navigateur
donne un FAUX VERT, pire qu'aucun test) a trouvé et fait corriger ici : le
harnais déclarait À LA FOIS `recGeneration` ET `backupGeneration`, masquant
exactement le bug consigné en commentaire dans record.html (mauvaise variable
de génération -> ReferenceError à chaque retry, `pendingSegments` gelé) —
seule la variable que l'écran testé possède RÉELLEMENT est désormais déclarée,
l'autre reste non définie ; le chemin `timedOut` (délai réseau maximal atteint)
n'était jamais exercé ; le flag `blocking` d'un abandon n'était jamais asserté ;
le plafond de 60 s sur `delayMs` n'était jamais mis en défaut par les scénarios
existants ; la garde de génération du chemin "occupé" (un « Recommencer »
pendant l'attente) était du code mort. Quatre scénarios supplémentaires
couvrent ces trous ; ce que la revue a confirmé FIDÈLE (signature réelle de
`recFetch`, résolution de `Date.now`/`setTimeout`, arithmétique des délais)
n'a pas été retouché.

Aucun accès base : ce module ne touche ni `DB_PATH` ni `SessionLocal`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

try:  # `tests/` sur sys.path (mode d'import par défaut de pytest)
    from test_repartition_live import _fonction, _node, _sans_commentaires
except ImportError:  # `tests` importable comme paquet
    from tests.test_repartition_live import _fonction, _node, _sans_commentaires

RACINE = Path(__file__).resolve().parent.parent
LIBRE = RACINE / "app" / "templates" / "interviews" / "record_libre.html"
STRUCTURE = RACINE / "app" / "templates" / "interviews" / "record.html"
LES_DEUX = [LIBRE, STRUCTURE]


def _var_generation(ecran: Path) -> str:
    """Le SEUL compteur de génération que CET écran possède réellement.

    Fournir les deux (comme la 1re version de ce fichier) masquait le bug
    consigné en commentaire dans record.html:1129-1133 : appeler la mauvaise
    variable de génération lève `ReferenceError` dans le vrai navigateur —
    seule celle-ci est déclarée, l'autre reste non définie, pour que le
    harnais échoue de la même façon que le vrai écran si l'extraction s'est
    trompée de nom."""
    return "recGeneration" if ecran.name == "record.html" else "backupGeneration"


# Script PLAT (pas de vm.createContext) : l'extraction de `uploadSegment` est
# collée telle quelle comme code RÉEL du script, ses identifiants libres
# (recGeneration/backupGeneration, TRANSCRIBE_TIMEOUT_MS, recFetch…) résolus
# par les bindings top-level ci-dessous — un closure Node ordinaire, pas un
# bac à sable vm.
_SCRIPT = r"""
const timers = [];              // {fn, delai} passés à setTimeout, JAMAIS auto-exécutés
const fetchs = [];              // {delai} = 3e argument réellement passé à recFetch
const appels = { keepLostSegment: [], appendTranscript: [], onSettle: 0 };
let clock = 0;
const reponses = %(reponses)s;  // queue consommée dans l'ordre par recFetch
function FormData() { this.champs = {}; }
FormData.prototype.append = function () {};
function File(parts, name, opts) { this.name = name; this.type = opts && opts.type; }
let %(gen_var)s = 0;
let pendingSegments = 0;
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
  if (!r) return Promise.reject(new Error('scenario epuise : plus de reponse en file'));
  if (r.reseau_ko) {
    const err = new Error('reseau KO');
    if (r.recTimeout) err.recTimeout = true;
    return Promise.reject(err);
  }
  return Promise.resolve({
    ok: r.ok, status: r.status, json: function () { return Promise.resolve(r.data); }
  });
}
const Date = { now: function () { return clock; } };
function setTimeout(fn, delai) { timers.push({ fn: fn, delai: delai }); return timers.length; }
function clearTimeout() {}
async function tick() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
function minuteur(i) {
  if (!timers[i]) throw new Error('scenario mal forme : minuteur ' + i + ' attendu, ' + timers.length + ' present(s)');
  return timers[i];
}

%(fonction)s

%(scenario)s
"""


def _extraire(ecran: Path) -> str:
    contenu = _sans_commentaires(ecran.read_text(encoding="utf-8"))
    return _fonction(contenu, "uploadSegment")


def _lancer(ecran: Path, reponses: list[dict], timeout_ms: int, scenario: str) -> dict:
    script = _SCRIPT % {
        "reponses": json.dumps(reponses),
        "timeout": timeout_ms,
        "gen_var": _var_generation(ecran),
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
  out.minuteur1_delai = minuteur(0).delai;            // retry_after_s=5 -> 5000
  out.message1 = statusEl.textContent;

  clock += minuteur(0).delai;
  minuteur(0).fn(); await tick();
  out.appel2_delai_fetch = fetchs[1].delai;           // borné au reliquat, pas au plein
  out.pending_apres_appel2 = pendingSegments;         // jamais recompté
  out.minuteur2_delai = minuteur(1).delai;            // preuve que "occupé" ne consomme
                                                       // PAS SEGMENT_RETRY_DELAYS_MS[0]=2000

  clock += minuteur(1).delai;
  minuteur(1).fn(); await tick();                     // 3e réponse : succès
  out.appel3_delai_fetch = fetchs[2].delai;           // reliquat encore réduit d'un cran
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
    assert deux_occupes_puis_succes["minuteur2_delai"] == 5000, (
        "le 2e refus \"occupé\" doit lui aussi suivre retry_after_s (5000), "
        "PAS SEGMENT_RETRY_DELAYS_MS[0]=2000 — sinon il a consommé le budget "
        "des échecs réels que ce lot doit préserver"
    )
    assert "occupée" in deux_occupes_puis_succes["message1"]


def test_le_compteur_pending_ne_recompte_pas_les_relances_occupe(deux_occupes_puis_succes: dict) -> None:
    assert deux_occupes_puis_succes["pending_apres_appel1"] == 1
    assert deux_occupes_puis_succes["pending_apres_appel2"] == 1
    assert deux_occupes_puis_succes["pending_final"] == 0


def test_le_delai_de_la_2e_requete_est_borne_au_reliquat_pas_au_plein(deux_occupes_puis_succes: dict) -> None:
    # Reliquat = 900000 - 5000 = 895000, strictement < TRANSCRIBE_TIMEOUT_MS :
    # sans le plafond (revue bmad-code-review 2026-09-16), ce serait 900000.
    assert deux_occupes_puis_succes["appel2_delai_fetch"] == 895000
    assert deux_occupes_puis_succes["appel3_delai_fetch"] == 890000


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
  out.minuteur1_delai = minuteur(0).delai;

  clock += minuteur(0).delai;  // clock = 15000
  minuteur(0).fn(); await tick();
  // 2e réponse : échec transitoire (réseau KO) -> retryOrGiveUp -> relance à
  // SEGMENT_RETRY_DELAYS_MS[0] = 2000, en propageant (ou pas, selon le bug)
  // la fenêtre `busyUntil` du tout premier appel.
  out.minuteur2_delai = minuteur(1).delai;

  clock += minuteur(1).delai;  // clock = 17000
  minuteur(1).fn(); await tick();
  // 3e réponse : occupé à nouveau. Reliquat RÉEL (si busyUntil préservé à
  // 20000) = 3000ms ; reliquat FAUSSÉ (si busyUntil ré-armé à 15000+20000) =
  // 20000ms. Le délai réellement passé à setTimeout distingue les deux.
  out.minuteur3_delai = minuteur(2).delai;

  clock += minuteur(2).delai;  // clock = 20000 si fenêtre préservée
  minuteur(2).fn(); await tick();
  // 4e réponse : occupé une 4e fois, pile à l'expiration de la fenêtre
  // D'ORIGINE (20000). Si `busyUntil` a été préservé : abandon immédiat
  // (`keepLostSegment`, bloquant), aucun 5e minuteur. Si la fenêtre a été
  // ré-armée par le détour transitoire : encore du temps devant soi, relance.
  out.abandonne = appels.keepLostSegment.length > 0;
  out.abandon_bloquant = appels.keepLostSegment.length > 0 && appels.keepLostSegment[0].blocking;
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
    assert fenetre_preservee["abandon_bloquant"] is True, (
        "l'abandon doit être marqué BLOQUANT — c'est la seule copie de cette "
        "parole ; un abandon non bloquant laisserait le gate de soumission et "
        "`beforeunload` se rouvrir alors que le segment est réellement perdu"
    )
    assert fenetre_preservee["nb_minuteurs_total"] == 3, (
        "aucune 4e relance programmée : la fenêtre de patience est épuisée"
    )
    assert fenetre_preservee["nb_appels_fetch"] == 4


# --------------------------------------------------------------------------- #
# 3. Délai réseau maximal atteint (err.recTimeout) : abandon immédiat, PAS de
#    relance automatique des mêmes octets (R3-M5/M6) — chemin distinct de la
#    relance transitoire courte, jamais exercé par les scénarios 1 et 2.
# --------------------------------------------------------------------------- #

_SCENARIO_DELAI_RESEAU_MAXIMAL = r"""
(async () => {
  const out = {};
  uploadSegment({ type: 'audio/webm' }, 0, null, 0, function () { appels.onSettle++; });
  await tick();
  out.minuteurs_avant_abandon = timers.length;  // 0 : aucune relance programmée
  out.abandonne = appels.keepLostSegment.length > 0;
  out.abandon_bloquant = appels.keepLostSegment.length > 0 && appels.keepLostSegment[0].blocking;
  out.pending_final = pendingSegments;
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module", params=LES_DEUX, ids=lambda p: p.name)
def delai_reseau_maximal(request) -> dict:
    reponses = [{"reseau_ko": True, "recTimeout": True}]
    return _lancer(request.param, reponses, 900000, _SCENARIO_DELAI_RESEAU_MAXIMAL)


def test_le_delai_reseau_maximal_abandonne_sans_relancer_les_memes_octets(delai_reseau_maximal: dict) -> None:
    # R3-M5/M6 : la fenêtre généreuse (TRANSCRIBE_TIMEOUT_MS) a déjà été
    # attendue en entier côté `recFetch` — relancer les mêmes octets aggrave
    # une éventuelle file côté serveur. Ce chemin n'était exercé par AUCUN des
    # deux scénarios précédents (revue bmad-code-review 2026-09-16, finding 2).
    assert delai_reseau_maximal["minuteurs_avant_abandon"] == 0, (
        "un timeout réseau (err.recTimeout) ne doit programmer AUCUNE "
        "relance automatique — seule une relance MANUELLE est permise"
    )
    assert delai_reseau_maximal["abandonne"] is True
    assert delai_reseau_maximal["abandon_bloquant"] is True
    assert delai_reseau_maximal["pending_final"] == 0


# --------------------------------------------------------------------------- #
# 4. `retry_after_s` très supérieur au plafond (config serveur permissive) :
#    le client ne doit jamais dormir plus de 60 s d'affilée sur ce seul
#    signal — sans le plafond, il suivrait le serveur sans limite propre.
# --------------------------------------------------------------------------- #

_SCENARIO_PLAFOND_60S = r"""
(async () => {
  const out = {};
  uploadSegment({ type: 'audio/webm' }, 0, null, 0, function () { appels.onSettle++; });
  await tick();
  out.minuteur1_delai = minuteur(0).delai;  // min(900000, 60000, 300000) = 60000
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module", params=LES_DEUX, ids=lambda p: p.name)
def plafond_60s(request) -> dict:
    # `retry_after_s`=300 simule un `WHISPER_SEGMENT_LOCK_TIMEOUT_S` serveur
    # réglé large (audio_transcribe.py, propagé tel quel par la route) : sans
    # le plafond de 60 s, le client dormirait 5 min sur un seul refus.
    reponses = [{"ok": False, "status": 503, "data": {"code": "busy", "retry_after_s": 300}}]
    return _lancer(request.param, reponses, 900000, _SCENARIO_PLAFOND_60S)


def test_le_delai_de_relance_ne_depasse_jamais_60s_meme_si_le_serveur_demande_plus(plafond_60s: dict) -> None:
    assert plafond_60s["minuteur1_delai"] == 60000, (
        "retry_after_s=300 (5 min) doit être plafonné à 60000ms côté client — "
        "le plafond bmad-code-review 2026-09-16 n'était mis en défaut par "
        "AUCUN scénario existant (retry_after_s valait 5 ou 15 partout ailleurs)"
    )


# --------------------------------------------------------------------------- #
# 5. Garde de génération pendant l'attente "occupé" : un « Recommencer » (ou
#    un nouveau démarrage) PENDANT la fenêtre de patience ne doit ni relancer
#    la requête ni ressusciter le texte dans la nouvelle session — la fenêtre
#    élargie par ce lot rend cette garde atteignable en usage réel (un segment
#    peut désormais rester en vol plusieurs minutes), et elle était du code
#    mort dans les scénarios précédents (finding 5, bmad-code-review 2026-09-16).
# --------------------------------------------------------------------------- #

_SCENARIO_GARDE_GENERATION = r"""
(async () => {
  const out = {};
  uploadSegment({ type: 'audio/webm' }, 0, null, 0, function () { appels.onSettle++; });
  await tick();
  out.minuteur_programme = timers.length === 1;
  %(gen_var)s++;  // « Recommencer » pendant l'attente "occupé"
  clock += minuteur(0).delai;
  minuteur(0).fn(); await tick();
  out.nouvelle_requete_partie = fetchs.length > 1;  // doit rester à 1
  out.pending_apres_recommencer = pendingSegments;   // settle() doit être passé
  out.transcript_ressuscite = appels.appendTranscript.length > 0;  // doit rester vide
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module", params=LES_DEUX, ids=lambda p: p.name)
def garde_generation(request) -> dict:
    ecran = request.param
    scenario = _SCENARIO_GARDE_GENERATION % {"gen_var": _var_generation(ecran)}
    # 2e réponse jamais consommée si la garde tient : sa présence en file
    # ferait échouer `nouvelle_requete_partie` si elle était atteinte à tort.
    reponses = [
        {"ok": False, "status": 503, "data": {"code": "busy", "retry_after_s": 5}},
        {"ok": True, "status": 200, "data": {"text": "texte de la session abandonnée"}},
    ]
    return _lancer(ecran, reponses, 900000, scenario)


def test_un_recommencer_pendant_l_attente_occupe_n_envoie_pas_de_nouvelle_requete(garde_generation: dict) -> None:
    assert garde_generation["minuteur_programme"] is True
    assert garde_generation["nouvelle_requete_partie"] is False, (
        "la garde de génération (`gen !== recGeneration`/`backupGeneration`) "
        "doit empêcher la relance \"occupé\" de repartir après un "
        "« Recommencer » — sinon la requête part en concurrence avec la "
        "nouvelle session"
    )
    assert garde_generation["pending_apres_recommencer"] == 0, (
        "la chaîne doit se terminer par `settle()`, pas rester gelée"
    )
    assert garde_generation["transcript_ressuscite"] is False, (
        "le texte de la session abandonnée ne doit jamais réapparaître"
    )
