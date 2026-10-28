"""Reprise des segments du micro direct et alerte de complétude — EXÉCUTION
RÉELLE du JS (lot « transcription fiable », commit 4).

Critère utilisateur : « une transcription de la totalité de l'audio sans
segment failed du 1er coup ». Le serveur qualifie désormais `code: "transient"`
les 422 dont l'audio est intact mais le traitement a échoué (worker Whisper tué,
flux vide) ; le client ne rejouait pourtant que les >= 500 et les erreurs
réseau, et rangeait ces 422 dans le bandeau « segments perdus » bloquant.

Trois étages, tous EXÉCUTÉS sous node (jamais un `.index()` sur le texte brut
d'un template : aveugle à la portée d'une fermeture JS) :

1. le plan pur `static/rec_retry.js` (classification statut/code, paliers de
   délais, texte de l'alerte de complétude) ;
2. `uploadSegment` extrait TEL QUEL de record_libre.html ET de record.html,
   rejoué avec une horloge et un `recFetch` pilotés à la main (le harnais, sa
   fidélité et ses fixtures viennent de test_transcription_occupee_execution.py,
   qui charge lui aussi le VRAI rec_retry.js) ;
3. `pollFileImport` extrait des deux écrans : l'alerte de complétude n'apparaît
   que pour un job `done` ET `incomplet`.

Le clic réel dans un navigateur est joué par tests/test_e2e_micro_retry.py
(record_libre.html seulement ; record.html n'est couvert que par ce harnais —
son parcours exige une mission, une trame et des questions semées).
"""
from __future__ import annotations

import json

import pytest

try:  # `tests/` sur sys.path (mode d'import par défaut de pytest)
    from test_repartition_live import _fonction, _node, _sans_commentaires
    from test_transcription_occupee_execution import (
        LES_DEUX, REC_RETRY, _lancer, _var_generation,
    )
except ImportError:  # `tests` importable comme paquet
    from tests.test_repartition_live import _fonction, _node, _sans_commentaires
    from tests.test_transcription_occupee_execution import (
        LES_DEUX, REC_RETRY, _lancer, _var_generation,
    )


# --------------------------------------------------------------------------- #
# 1. Le plan pur
# --------------------------------------------------------------------------- #

_SCRIPT_PLAN = r"""
const window = {};
%(rec_retry)s
const P = window.recRetryPlan;
const out = {};
out.classer = {
  busy: P.classer(503, { code: 'busy' }),
  cinq_cents: P.classer(500, {}),
  cinq_zero_trois_sans_code: P.classer(503, {}),
  transient: P.classer(422, { code: 'transient' }),
  no_speech: P.classer(422, { code: 'no_speech' }),
  sans_code: P.classer(422, {}),
  code_inconnu: P.classer(422, { code: 'autre_chose' }),
  data_null: P.classer(422, null),
  quatre_cent_treize: P.classer(413, { code: 'transient' }),
  quatre_cent_quatre: P.classer(404, {}),
  cinq_zero_trois_null: P.classer(503, null),
  quatre_deux_deux_undefined: P.classer(422, undefined),
  transient_en_chaine: P.classer('422', { code: 'transient' }),
  busy_en_chaine: P.classer('503', { code: 'busy' }),
};
out.fige = {
  reseau: Object.isFrozen(P.delaisReseau), transient: Object.isFrozen(P.delaisTransient),
  plan: Object.isFrozen(P),
};
out.erreur = { timeout: P.classerErreur({ recTimeout: true }), reseau: P.classerErreur(new Error('x')) };
function suite(nature) {
  const s = []; for (let a = 0; a < 6; a++) s.push(P.delaiPour(nature, a)); return s;
}
out.delais = {
  retry: suite('retry'), transient: suite('transient'), busy: suite('busy'),
  no_speech: suite('no_speech'), definitif: suite('definitif'), timeout: suite('timeout'),
};
out.essais = { retry: P.nbEssais('retry'), transient: P.nbEssais('transient') };
out.msg = {
  nul: P.messageCompletude(null),
  indefini: P.messageCompletude(undefined),
  complet: P.messageCompletude({ statut: 'complet', blocs_vides: [1], ecart_s: 300 }),
  non_evaluable: P.messageCompletude({ statut: 'non_evaluable', blocs_vides: [1] }),
  incomplet: P.messageCompletude({
    statut: 'incomplet', blocs_vides: [0, 4], tranches_ignorees: [2],
    blocs_rejoues: [3], duree_conteneur_s: 3600, duree_decodee_s: 3490, ecart_s: 110,
  }),
  incomplet_vide: P.messageCompletude({ statut: 'incomplet' }),
  valeurs_invalides: P.messageCompletude({
    statut: 'incomplet', blocs_vides: [null, 'x', 3, '', 4],
    tranches_ignorees: [{}, 2], ecart_s: Infinity, duree_conteneur_s: 'abc',
  }),
  beaucoup_de_tranches: P.messageCompletude({
    statut: 'incomplet', tranches_ignorees: [1,2,3,4,5,6,7,8,9,10,11,12],
  }),
  // Cas réel (interview 20) : 3 blocs sans texte, écart de 6 s DANS la tolérance.
  ecart_dans_la_tolerance: P.messageCompletude({
    statut: 'incomplet', blocs_vides: [59], duree_conteneur_s: 4026.76,
    duree_decodee_s: 4020.78, ecart_s: 5.98,
  }),
  ecart_sur_la_borne: P.messageCompletude({
    statut: 'incomplet', blocs_vides: [0], duree_conteneur_s: 600, ecart_s: 12,
  }),
  ecart_au_dela: P.messageCompletude({
    statut: 'incomplet', blocs_vides: [0], duree_conteneur_s: 600, ecart_s: 13,
  }),
  plancher_de_2_s: P.messageCompletude({
    statut: 'incomplet', blocs_vides: [0], duree_conteneur_s: 60, ecart_s: 2,
  }),
  incomplet_beaucoup: P.messageCompletude({
    statut: 'incomplet', blocs_vides: [0,1,2,3,4,5,6,7,8,9,10,11],
  }),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def plan() -> dict:
    return _node(_SCRIPT_PLAN % {"rec_retry": REC_RETRY.read_text(encoding="utf-8")})


def test_classification_du_422_transient_et_des_autres_statuts(plan: dict) -> None:
    c = plan["classer"]
    assert c["transient"] == "transient"
    assert c["busy"] == "busy"
    assert c["cinq_cents"] == "retry"
    assert c["cinq_zero_trois_sans_code"] == "retry", "un 503 sans code busy est un échec réel"
    assert c["no_speech"] == "no_speech"
    # Définitifs comme aujourd'hui : sans code, code inconnu, corps absent, 413,
    # et un `transient` porté par un AUTRE statut que 422.
    for cle in ("sans_code", "code_inconnu", "data_null", "quatre_cent_treize", "quatre_cent_quatre"):
        assert c[cle] == "definitif", cle


def test_classification_tolere_statut_en_chaine_et_corps_absent(plan: dict) -> None:
    c = plan["classer"]
    assert c["transient_en_chaine"] == "transient", "« 422 » en chaîne ne doit pas devenir définitif"
    assert c["busy_en_chaine"] == "busy"
    assert c["cinq_zero_trois_null"] == "retry", "503 sans corps lisible = échec réel"
    assert c["quatre_deux_deux_undefined"] == "definitif"


def test_les_delais_exposes_sont_figes(plan: dict) -> None:
    assert plan["fige"] == {"reseau": True, "transient": True, "plan": True}


def test_alerte_ignore_les_valeurs_inexploitables_du_serveur(plan: dict) -> None:
    texte = plan["msg"]["valeurs_invalides"]
    for parasite in ("NaN", "Infinity", "[object", "null", "undefined"):
        assert parasite not in texte, parasite
    assert "passages sans texte : 4, 5" in texte, "3 et 4 (0-based) -> 4 et 5 ; null/'x'/'' écartés"
    assert "partie audio ignorée : 2" in texte
    assert "il manque" not in texte


def test_la_liste_des_tranches_ignorees_est_plafonnee(plan: dict) -> None:
    texte = plan["msg"]["beaucoup_de_tranches"]
    assert "parties audio ignorées : 1, 2, 3, 4, 5, 6, 7, 8, 9, 10…" in texte


def test_un_delai_maximal_n_est_jamais_rejoue(plan: dict) -> None:
    assert plan["erreur"] == {"timeout": "timeout", "reseau": "retry"}
    assert plan["delais"]["timeout"] == [None] * 6


def test_paliers_de_delais_reseau_inchanges_et_troisieme_palier_transient(plan: dict) -> None:
    d = plan["delais"]
    assert d["retry"] == [2000, 6000, None, None, None, None]
    assert d["transient"] == [2000, 6000, 15000, None, None, None]
    assert sum(x for x in d["transient"] if x) == 23000, "plafond total ≈ 23 s hors durée de requête"
    assert plan["essais"] == {"retry": 3, "transient": 4}


def test_busy_no_speech_et_definitif_n_ont_aucun_budget_borne(plan: dict) -> None:
    for nature in ("busy", "no_speech", "definitif"):
        assert plan["delais"][nature] == [None] * 6, nature


def test_alerte_de_completude_seulement_pour_incomplet(plan: dict) -> None:
    m = plan["msg"]
    for cle in ("nul", "indefini", "complet", "non_evaluable"):
        assert m[cle] == "", cle
    assert m["incomplet"].startswith("Transcription incomplète :")
    assert m["incomplet_vide"].startswith("Transcription incomplète")


def test_alerte_n_affirme_un_manque_d_audio_que_hors_tolerance(plan: dict) -> None:
    """L'incomplétude peut venir de blocs sans texte alors que l'écart de durée est
    dans la tolérance (max(2 s, 2 %), comme `bilan_completude`) : dire alors « il
    manque environ 6 s d'audio » serait faux."""
    m = plan["msg"]
    assert "passage sans texte : 60" in m["ecart_dans_la_tolerance"]
    assert "il manque" not in m["ecart_dans_la_tolerance"]
    assert "il manque" not in m["ecart_sur_la_borne"], "12 s sur 600 s = 2 % : pas > tolérance"
    assert "il manque" not in m["plancher_de_2_s"], "plancher de 2 s : 2 s n'est pas > 2 s"
    assert "il manque environ 13 s" in m["ecart_au_dela"]


def test_alerte_de_completude_positions_1_based_et_sans_jargon(plan: dict) -> None:
    texte = plan["msg"]["incomplet"]
    assert "passages sans texte : 1, 5" in texte, "blocs_vides [0, 4] affichés en 1-based"
    assert "partie audio ignorée : 2" in texte, "rangs de parties déjà 1-based, non décalés"
    assert "1 min 50 s" in texte and "1 h" not in texte
    for jargon in ("blocs_vides", "null", "undefined", "NaN", "[object"):
        assert jargon not in texte
    assert "…" in plan["msg"]["incomplet_beaucoup"], "liste plafonnée"


# --------------------------------------------------------------------------- #
# 2. uploadSegment : retry du 422 transient, dans les DEUX écrans
# --------------------------------------------------------------------------- #

_T422 = {"ok": False, "status": 422, "data": {"code": "transient", "error": "Échec de la transcription"}}
_OK = {"ok": True, "status": 200, "data": {"text": "bonjour"}}

_SCENARIO_GENERIQUE = r"""
(async () => {
  const out = {};
  uploadSegment({ type: 'audio/webm' }, 0, null, 0, function () { appels.onSettle++; });
  await tick();
  out.delais_programmes = [];
  // Rejoue les minuteurs un par un, dans l'ordre, tant qu'il en reste.
  for (let i = 0; i < 10 && timers[i]; i++) {
    out.delais_programmes.push(timers[i].delai);
    out['statut_avant_' + i] = statusEl.textContent;
    clock += timers[i].delai;
    timers[i].fn(); await tick();
  }
  out.nb_fetch = fetchs.length;
  out.transcript = appels.appendTranscript;
  out.perdus = appels.keepLostSegment;
  out.rejoues = segmentsRejoues;
  out.pending = pendingSegments;
  out.onSettle = appels.onSettle;
  console.log(JSON.stringify(out));
})();
"""


def _jouer(ecran, reponses, scenario=_SCENARIO_GENERIQUE):
    return _lancer(ecran, reponses, 900000, scenario)


@pytest.fixture(scope="module", params=LES_DEUX, ids=lambda p: p.name)
def ecran(request):
    return request.param


def test_un_422_transient_puis_un_succes_insere_le_texte_et_compte_un_rejeu(ecran) -> None:
    r = _jouer(ecran, [_T422, _OK])
    assert r["delais_programmes"] == [2000]
    assert r["transcript"] == ["bonjour"]
    assert r["perdus"] == [], "aucun segment perdu : le retry a réussi"
    assert r["rejoues"] == 1
    assert r["pending"] == 0 and r["onSettle"] == 1
    assert r["nb_fetch"] == 2
    assert "(2/4)" in r["statut_avant_0"], r["statut_avant_0"]


def test_trois_422_transient_de_suite_rejoue_trois_fois_puis_bandeau_bloquant(ecran) -> None:
    r = _jouer(ecran, [_T422] * 4)
    assert r["delais_programmes"] == [2000, 6000, 15000]
    assert r["nb_fetch"] == 4
    assert r["rejoues"] == 1, "trois relances d'UN segment = un segment rejoué, pas trois"
    assert len(r["perdus"]) == 1 and r["perdus"][0]["blocking"] is True, (
        "budget épuisé : le blob est gardé dans le bandeau BLOQUANT (seule copie de cette parole)"
    )
    assert r["transcript"] == []
    assert r["pending"] == 0 and r["onSettle"] == 1


def test_pending_est_tenu_pendant_les_relances_du_transient(ecran) -> None:
    r = _jouer(ecran, [_T422, _OK], r"""
(async () => {
  const out = {};
  uploadSegment({ type: 'audio/webm' }, 0, null, 0, function () { appels.onSettle++; });
  await tick();
  out.pending_pendant_l_attente = pendingSegments;   // 1 : « Enregistrer » reste grisé
  clock += minuteur(0).delai;
  minuteur(0).fn(); await tick();
  out.pending_final = pendingSegments;
  console.log(JSON.stringify(out));
})();
""")
    assert r["pending_pendant_l_attente"] == 1
    assert r["pending_final"] == 0


def test_no_speech_reste_non_bloquant_sans_retry(ecran) -> None:
    r = _jouer(ecran, [{"ok": False, "status": 422, "data": {"code": "no_speech", "error": "silence"}}])
    assert r["delais_programmes"] == []
    assert r["nb_fetch"] == 1 and r["rejoues"] == 0
    assert len(r["perdus"]) == 1 and r["perdus"][0]["blocking"] is False


@pytest.mark.parametrize("reponse", [
    {"ok": False, "status": 422, "data": {"error": "Aucun enregistrement reçu."}},
    {"ok": False, "status": 422, "data": {"code": "inconnu", "error": "x"}},
    {"ok": False, "status": 413, "data": {"error": "trop gros"}},
], ids=["422-sans-code", "422-code-inconnu", "413"])
def test_les_autres_refus_restent_definitifs_et_bloquants(ecran, reponse) -> None:
    r = _jouer(ecran, [reponse])
    assert r["delais_programmes"] == [], "aucune relance automatique"
    assert r["nb_fetch"] == 1 and r["rejoues"] == 0
    assert len(r["perdus"]) == 1 and r["perdus"][0]["blocking"] is True


def test_le_budget_5xx_est_inchange_deux_relances_puis_perdu(ecran) -> None:
    r = _jouer(ecran, [{"ok": False, "status": 500, "data": {"error": "boom"}}] * 3)
    assert r["delais_programmes"] == [2000, 6000]
    assert r["nb_fetch"] == 3 and r["rejoues"] == 1
    assert len(r["perdus"]) == 1 and r["perdus"][0]["blocking"] is True


def test_un_echec_reseau_puis_succes_compte_un_rejeu(ecran) -> None:
    r = _jouer(ecran, [{"reseau_ko": True}, _OK])
    assert r["delais_programmes"] == [2000] and r["rejoues"] == 1
    assert r["transcript"] == ["bonjour"]


def test_un_delai_maximal_n_est_pas_rejoue_ni_compte(ecran) -> None:
    if ecran.name == "record_libre.html":
        # Incident 2026-10-27 : sans copie acquittée, record_libre suit le plan
        # UNE relance (revue du 2026-10-28, précédée d'un préchauffage) puis garde
        # le segment perdu, bloquant — aucun 3e envoi.
        to = {"reseau_ko": True, "recTimeout": True}
        r = _jouer(ecran, [to, to, to])
        assert r["delais_programmes"] == [2000] and r["rejoues"] == 1
        assert r["nb_fetch"] == 2
        assert len(r["perdus"]) == 1 and r["perdus"][0]["blocking"] is True
        return
    r = _jouer(ecran, [{"reseau_ko": True, "recTimeout": True}])
    assert r["delais_programmes"] == [] and r["rejoues"] == 0
    assert len(r["perdus"]) == 1 and r["perdus"][0]["blocking"] is True


def test_les_rejeux_occupe_ne_sont_pas_comptes(ecran) -> None:
    busy = {"ok": False, "status": 503, "data": {"code": "busy", "retry_after_s": 1}}
    r = _jouer(ecran, [busy, busy, _OK])
    assert r["transcript"] == ["bonjour"]
    assert r["rejoues"] == 0, "« occupé » est la file normale du verrou, pas un échec masqué"


def test_un_recommencer_pendant_l_attente_du_transient_ne_relance_rien(ecran) -> None:
    gen = _var_generation(ecran)
    r = _jouer(ecran, [_T422, _OK], r"""
(async () => {
  const out = {};
  uploadSegment({ type: 'audio/webm' }, 0, null, 0, function () { appels.onSettle++; });
  await tick();
  out.minuteur_programme = timers.length === 1;
  %s++;  // « Recommencer » pendant l'attente
  clock += minuteur(0).delai;
  minuteur(0).fn(); await tick();
  out.nb_fetch = fetchs.length;
  out.rejoues = segmentsRejoues;
  out.pending = pendingSegments;
  out.transcript = appels.appendTranscript;
  console.log(JSON.stringify(out));
})();
""" % gen)
    assert r["minuteur_programme"] is True
    assert r["nb_fetch"] == 1, "aucune relance fantôme après l'abandon de la session"
    assert r["rejoues"] == 0
    assert r["pending"] == 0 and r["transcript"] == []


_R500 = {"ok": False, "status": 500, "data": {"error": "boom"}}
_R503_SANS_CODE = {"ok": False, "status": 503, "data": {"error": "x"}}


def test_suite_mixte_transient_transient_puis_5xx_s_arrete(ecran) -> None:
    """Budget PARTAGÉ (documenté dans rec_retry.js) : deux relances déjà payées par des
    transient épuisent le budget d'un 5xx — un segment ne dépasse jamais 3 relances."""
    r = _jouer(ecran, [_T422, _T422, _R503_SANS_CODE])
    assert r["delais_programmes"] == [2000, 6000]
    assert r["nb_fetch"] == 3
    assert len(r["perdus"]) == 1 and r["perdus"][0]["blocking"] is True


def test_suite_mixte_5xx_5xx_puis_transient_gagne_la_troisieme_relance(ecran) -> None:
    r = _jouer(ecran, [_R500, _R500, _T422, _OK])
    assert r["delais_programmes"] == [2000, 6000, 15000]
    assert r["transcript"] == ["bonjour"] and r["perdus"] == []
    assert r["rejoues"] == 1, "un seul segment rejoué"


def test_un_segment_rejoue_deux_fois_est_compte_une_fois(ecran) -> None:
    r = _jouer(ecran, [_R500, _R500, _OK])
    assert r["delais_programmes"] == [2000, 6000] and r["rejoues"] == 1


def test_une_relance_manuelle_d_un_segment_perdu_n_est_pas_comptee(ecran) -> None:
    """`lostId` renseigné = relance lancée par l'utilisateur depuis le bandeau : elle
    n'est pas « automatique », donc hors du compteur discret."""
    manuel = _SCENARIO_GENERIQUE.replace(
        "uploadSegment({ type: 'audio/webm' }, 0, null, 0,",
        "uploadSegment({ type: 'audio/webm' }, 0, 7, 0,")
    assert manuel != _SCENARIO_GENERIQUE
    r = _jouer(ecran, [_R500, _OK], manuel)
    assert r["nb_fetch"] == 2 and r["rejoues"] == 0


# --------------------------------------------------------------------------- #
# 3. pollFileImport : l'alerte n'apparaît que pour `done` ET `incomplet`
# --------------------------------------------------------------------------- #

_SCRIPT_POLL = r"""
const window = {};
%(rec_retry)s
const RETRY_PLAN = window.recRetryPlan;
let %(gen_var)s = 0;
let fileImportTimer = null;
const NET_TIMEOUT_MS = 1000;
const sessionToken = 't';
const backupPathHidden = { value: '' };
const statusEl = { textContent: '' };
const completudeEl = { hidden: true };
const completudeTextEl = { textContent: '' };
const reponse = %(reponse)s;
let timer = null;
function setTimeout(fn) { timer = fn; return 1; }
function recFetch() {
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve(reponse); } });
}
function signalerMissionAbsente() {}
function appendTranscript() {}
function submitSegmentJob() {}
function enregistrerAudioImporte() {}
function showFileRetry() {}
function finishFileImport() {}
let failedFileImport = null;
function hideFileRetry() {}
async function tick() { for (let i = 0; i < 20; i++) await Promise.resolve(); }

%(afficher)s
%(masquer)s
%(poll)s

(async () => {
  pollFileImport('job', 0, 0, 0);
  timer(); await tick();
  console.log(JSON.stringify({
    visible: completudeEl.hidden === false, texte: completudeTextEl.textContent,
    statut: statusEl.textContent,
  }));
})();
"""

_INCOMPLET = {"statut": "incomplet", "blocs_vides": [2], "tranches_ignorees": [], "ecart_s": 0}


def _poll(ecran, reponse: dict) -> dict:
    contenu = _sans_commentaires(ecran.read_text(encoding="utf-8"))
    return _node(_SCRIPT_POLL % {
        "rec_retry": REC_RETRY.read_text(encoding="utf-8"),
        "gen_var": _var_generation(ecran),
        "reponse": json.dumps(reponse),
        "afficher": _fonction(contenu, "afficherCompletude"),
        "masquer": _fonction(contenu, "masquerCompletude"),
        "poll": _fonction(contenu, "pollFileImport"),
    })


def test_job_termine_et_incomplet_affiche_le_bandeau_persistant(ecran) -> None:
    r = _poll(ecran, {"status": "done", "done": 3, "blocks": [], "filename": "x.wav", "completude": _INCOMPLET})
    assert r["visible"] is True
    assert "Transcription incomplète" in r["texte"] and "3" in r["texte"], "bloc 2 (0-based) affiché en 3"
    assert "Fichier transcrit" in r["statut"], "la ligne de statut existante n'est pas remplacée"


@pytest.mark.parametrize("reponse", [
    {"status": "done", "done": 3, "completude": {"statut": "complet"}},
    {"status": "done", "done": 3, "completude": {"statut": "non_evaluable", "blocs_vides": [0]}},
    {"status": "done", "done": 3, "completude": None},
    {"status": "done", "done": 3},
    {"status": "failed", "done": 1, "error": "x", "completude": _INCOMPLET},
    {"status": "running", "done": 1, "total": 5, "completude": _INCOMPLET},
], ids=["complet", "non_evaluable", "null", "absent", "failed", "en-cours"])
def test_rien_n_est_affiche_hors_done_et_incomplet(ecran, reponse) -> None:
    assert _poll(ecran, reponse)["visible"] is False


# --------------------------------------------------------------------------- #
# 4. Page de suivi de retranscription : même règle (done ET incomplet)
# --------------------------------------------------------------------------- #

_SCRIPT_SUIVI = r"""
const window = {};
%(rec_retry)s
const recRetryPlan = window.recRetryPlan;
function el() { return { hidden: true, textContent: '', value: 0, max: 1, classList: { add() {}, remove() {} } }; }
const progressEl = el(), barEl = el(), suiteForm = el(), relancerForm = el(), abandonForm = el();
const completudeEl = el();
const statutUrl = '/x';
let echecs = 0, termine = false;
const POLL_TIMEOUT_MS = 1;
const reponse = %(reponse)s;
const window_location = {};
function setTimeout() {}
function recFetch() {
  return Promise.resolve({ status: 200, ok: true, json: function () { return Promise.resolve(reponse); } });
}
async function tick() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
%(poll)s
(async () => {
  poll(); await tick();
  console.log(JSON.stringify({ visible: completudeEl.hidden === false, texte: completudeEl.textContent }));
})();
"""


def _suivi(reponse: dict) -> dict:
    from pathlib import Path

    page = Path(REC_RETRY).parent.parent / "templates" / "interviews" / "libre_retranscription.html"
    contenu = _sans_commentaires(page.read_text(encoding="utf-8"))
    return _node(_SCRIPT_SUIVI % {
        "rec_retry": REC_RETRY.read_text(encoding="utf-8"),
        "reponse": json.dumps(reponse),
        "poll": _fonction(contenu, "poll"),
    })


_BASE_SUIVI = {"blocs": 4, "blocs_total": 4, "ia_faites": 2, "ia_total": 2, "tranches_faites": 1, "tranches_total": 1}


def test_suivi_retranscription_affiche_l_alerte_pour_done_et_incomplet() -> None:
    r = _suivi({**_BASE_SUIVI, "status": "done", "completude": _INCOMPLET})
    assert r["visible"] is True and "Transcription incomplète" in r["texte"]


@pytest.mark.parametrize("etat", [
    {"status": "done", "completude": {"statut": "complet"}},
    {"status": "done", "completude": None},
    {"status": "failed", "error": "x", "completude": _INCOMPLET},
    {"status": "running", "completude": _INCOMPLET},
], ids=["complet", "null", "failed", "en-cours"])
def test_suivi_retranscription_n_affiche_rien_sinon(etat: dict) -> None:
    assert _suivi({**_BASE_SUIVI, **etat})["visible"] is False
