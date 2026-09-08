"""Reprise d'une session d'entretien libre après F5 / fermeture / plantage, et
visibilité des tranches d'extraction non transmises (incident du 2026-09-08).

Le 2026-09-08, un entretien réel de 2h n'a jamais donné d'entretien en base :
la transcription ne vivait que dans la mémoire JS de l'onglet, le jeton de
session n'était pas restauré à la réouverture, et le POST de création d'une
tranche d'extraction échouait en silence (aucune relance, aucun mot à l'écran).

pytest ne rend pas les templates et n'exécute pas le navigateur. Ce fichier
tient donc trois étages, du plus probant au plus faible — et le dit :

1. `rec_draft.js` est EXÉCUTÉ sous node contre un localStorage simulé :
   aller-retour, péremption à 7 jours, JSON corrompu, quota plein, journal
   borné, décision de reprise, stockage indisponible. Ce sont des faits
   d'exécution, pas des présences de chaînes.
2. Le câblage est vérifié À FROID sur les templates : chargement sans `defer`
   (la leçon de rec_fetch.js — un script différé consommé à l'analyse est
   indéfini SANS erreur au chargement), bandeau et boutons présents,
   sauvegarde du brouillon appelée là où le texte change, `.catch` de la
   création de tranche non vide. Ces assertions sont des gardes de régression
   sur la forme : elles ne prouvent pas que le navigateur exécute le bon
   chemin. Ce chemin-là a été exercé en réel (Edge headless piloté par CDP) le
   2026-09-08 — voir le commit.
3. La route de sauvegarde audio écrit désormais une trace POSITIVE dans le
   journal serveur, vérifiée par `caplog` sur un vrai POST.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
REC_DRAFT = RACINE / "app" / "static" / "rec_draft.js"
BASE_HTML = RACINE / "app" / "templates" / "base.html"
RECORD_LIBRE = RACINE / "app" / "templates" / "interviews" / "record_libre.html"
RECORD_STRUCTURE = RACINE / "app" / "templates" / "interviews" / "record.html"
# Les deux écrans d'enregistrement postent sur la même route, au même tick :
# le filet vaut pour les deux (revue du 2026-09-08, F7 — la première version ne
# couvrait que l'écran libre, la chaîne restait intacte en mode structuré).
LES_DEUX_ECRANS = [RECORD_LIBRE, RECORD_STRUCTURE]
NODE = shutil.which("node")


def teardown_module() -> None:
    """Libère le fichier de base partagé par toute la session pytest : sans
    `engine.dispose()`, le module suivant dans l'ordre de collection échoue en
    `PermissionError` sur son `DB_PATH.unlink()` Windows — mesuré le
    2026-09-08 : 35 erreurs dans `test_source_audio_distance.py` et
    `test_swot.py`, les deux fichiers qui suivent celui-ci, vertes dès que ce
    module ne tourne pas avant eux."""
    from app.db import engine

    try:
        engine.dispose()
    except Exception:
        pass

# Harnais node : localStorage simulé (avec quota et indisponibilité forçables),
# puis un scénario par invariant. Sortie : un objet {nom: bool}, un test pytest
# par clé pour qu'un échec nomme l'invariant cassé.
HARNAIS = r"""
var store = {}; var setItemThrows = false;
global.window = { localStorage: {
  getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
  setItem: function (k, v) { if (setItemThrows) throw new Error('QuotaExceededError'); store[k] = String(v); },
  removeItem: function (k) { delete store[k]; }
}};
var recDraft = require(process.argv[2]);
var out = {};
var T0 = 1000000000000;
var JOUR = 24 * 3600 * 1000;
// 1. aller-retour
out.sauve = recDraft.sauver(7, { transcript: 'bonjour', sessionToken: 'tok', coveredLen: 3 }, T0) === true;
var lu = recDraft.charger(7, T0 + 1000);
out.relu = !!lu && lu.transcript === 'bonjour' && lu.sessionToken === 'tok' && lu.coveredLen === 3 && lu.savedAt === T0;
out.autre_mission_vide = recDraft.charger(8, T0) === null;
// 2. péremption alignée sur la purge serveur (7 jours)
out.frais_a_6j = recDraft.charger(7, T0 + 6 * JOUR) !== null;
out.perime_a_8j = recDraft.charger(7, T0 + 8 * JOUR) === null;
out.perime_retire = !Object.prototype.hasOwnProperty.call(store, 'i2d:brouillon-libre:7');
// 3. JSON corrompu : null, et retiré
store['i2d:brouillon-libre:9'] = '{pas du json';
out.corrompu_null = recDraft.charger(9, T0) === null;
out.corrompu_retire = !Object.prototype.hasOwnProperty.call(store, 'i2d:brouillon-libre:9');
// 4. quota plein : false, jamais d'exception (l'enregistrement continue)
setItemThrows = true;
var leve = false; var r;
try { r = recDraft.sauver(7, { transcript: 'x' }, T0); } catch (e) { leve = true; }
out.quota_sans_exception = !leve && r === false;
setItemThrows = false;
// 5. journal borné, et qui ne touche pas au reste
recDraft.sauver(5, { transcript: 'texte', sessionToken: 'tok5' }, T0);
for (var i = 0; i < recDraft.JOURNAL_MAX + 10; i++) recDraft.noter(5, { type: 'backup-ko', i: i }, T0 + i);
var e5 = recDraft.charger(5, T0);
out.journal_borne = e5.journal.length === recDraft.JOURNAL_MAX;
out.journal_garde_les_derniers = e5.journal[e5.journal.length - 1].i === recDraft.JOURNAL_MAX + 9;
out.journal_ne_touche_pas_le_texte = e5.transcript === 'texte' && e5.sessionToken === 'tok5';
// 6. décision de reprise
out.decision_vide = recDraft.decision(null, T0) === null && recDraft.decision({ transcript: '   ' }, T0) === null;
var d1 = recDraft.decision({ transcript: 'abc', savedAt: T0 }, T0 + 60000);
out.decision_reprise = !!d1 && d1.mode === 'reprise' && /3 caract/.test(d1.libelle);
recDraft.marquerEnvoye(5, T0 + 5000);
var d2 = recDraft.decision(recDraft.charger(5, T0 + 6000), T0 + 6000);
out.decision_discrete_apres_envoi = !!d2 && d2.mode === 'discret';
out.envoye_pas_efface = recDraft.charger(5, T0 + 6000).transcript === 'texte';
out.decision_perimee = recDraft.decision({ transcript: 'abc', savedAt: T0 }, T0 + 8 * JOUR) === null;
// 7. stockage indisponible (SecurityError) : tout est inerte, rien ne lève
global.window = { get localStorage() { throw new Error('SecurityError'); } };
out.sans_stockage_charger = recDraft.charger(7) === null;
out.sans_stockage_sauver = recDraft.sauver(7, { transcript: 'x' }) === false;
var leve2 = false;
try { recDraft.effacer(7); recDraft.noter(7, { type: 'x' }); recDraft.marquerEnvoye(7); } catch (e) { leve2 = true; }
out.sans_stockage_sans_exception = !leve2;
process.stdout.write(JSON.stringify(out));
"""

INVARIANTS = [
    "sauve", "relu", "autre_mission_vide",
    "frais_a_6j", "perime_a_8j", "perime_retire",
    "corrompu_null", "corrompu_retire",
    "quota_sans_exception",
    "journal_borne", "journal_garde_les_derniers", "journal_ne_touche_pas_le_texte",
    "decision_vide", "decision_reprise", "decision_discrete_apres_envoi",
    "envoye_pas_efface", "decision_perimee",
    "sans_stockage_charger", "sans_stockage_sauver", "sans_stockage_sans_exception",
]


@pytest.fixture(scope="module")
def resultats_node(tmp_path_factory) -> dict:
    if not NODE:
        # En CI, l'étage probant ne doit pas disparaître en `skip` silencieux
        # (mémoire « suite locale verte, CI rouge 6 fois ») : sans node, la CI
        # échoue et le dit.
        if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
            pytest.fail("node absent en CI : les 20 invariants de rec_draft.js ne sont pas exécutés")
        pytest.skip("node absent : rec_draft.js ne peut pas être exécuté")
    harnais = tmp_path_factory.mktemp("rec_draft") / "harnais.js"
    harnais.write_text(HARNAIS, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(harnais), str(REC_DRAFT)],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("invariant", INVARIANTS)
def test_rec_draft_execute_sous_node(resultats_node: dict, invariant: str) -> None:
    assert invariant in resultats_node, f"le harnais n'a pas produit {invariant}"
    assert resultats_node[invariant] is True, invariant


def test_le_harnais_couvre_tous_les_invariants_listes(resultats_node: dict) -> None:
    """Un invariant ajouté au harnais sans sa ligne dans INVARIANTS ne serait
    jamais vérifié — et l'inverse échouerait déjà au test paramétré."""
    assert set(resultats_node) == set(INVARIANTS)


# ---- Câblage (gardes de forme, voir la docstring du module) ------------------


def test_rec_draft_est_charge_sans_defer_avant_le_body() -> None:
    """Même leçon que rec_fetch.js : un `defer` ici rendrait `recDraft` indéfini
    au moment où le script inline de record_libre.html l'appelle — sans erreur
    au chargement, la page « marcherait » jusqu'à la première sauvegarde."""
    html = BASE_HTML.read_text(encoding="utf-8")
    balise = re.search(r"<script[^>]*rec_draft\.js[^>]*>", html)
    assert balise is not None, "rec_draft.js n'est pas chargé par base.html"
    assert "defer" not in balise.group(0)
    # La BALISE <body> (en début de ligne), pas le mot « <body> » d'un
    # commentaire Jinja du <head>, qui la précède.
    body = re.search(r"^<body", html, re.M)
    assert body is not None
    assert balise.start() < body.start()


def test_record_libre_propose_la_reprise_et_marque_l_envoi() -> None:
    html = RECORD_LIBRE.read_text(encoding="utf-8")
    for attendu in (
        'id="rec-draft-banner"', 'id="rec-draft-restore"', 'id="rec-draft-discard"',
        "recDraft.charger(MISSION_ID)", "recDraft.decision(", "recDraft.marquerEnvoye(MISSION_ID)",
        "recDraft.effacer(MISSION_ID)",
    ):
        assert attendu in html, attendu
    # Le texte n'est jamais restauré en silence : la restauration est derrière
    # un clic, pas au chargement.
    reprise = html[html.index("function proposerReprise"):]
    assert "'rec-draft-restore').addEventListener('click'" in reprise
    # Le jeton restauré doit aussi partir dans le FORMULAIRE (`<input
    # name="session_token">`), pas seulement vivre dans la variable JS : c'est
    # lui qui, côté serveur, retrouve les tranches déjà structurées. Constaté
    # vide au premier passage en Edge headless le 2026-09-08.
    assert "sessionTokenHidden.value = sessionToken;" in reprise[:reprise.index("refreshSegmentTail();")]
    # DANS la fermeture principale du script (celle qui se referme par le
    # dernier `})();` en colonne 0) : posé après elle, le bloc s'exécutait dans
    # la portée globale où ni `transcriptSoFar` ni `MISSION_ID` n'existent —
    # ReferenceError, aucun bandeau, aucun mot (constaté en Edge headless le
    # 2026-09-08 avant correction).
    fin_fermeture = html.rindex("\n})();")
    assert html.index("function proposerReprise") < fin_fermeture
    assert html.index("recDraft.marquerEnvoye(MISSION_ID)") < fin_fermeture


def test_le_brouillon_est_sauve_partout_ou_le_texte_change() -> None:
    """Les quatre endroits où `transcriptSoFar` ou le curseur bougent : ajout
    d'un segment, substitution d'un marqueur de segment récupéré, substitution
    d'un marqueur de bloc de fichier importé (même recalage), et avancée du
    curseur de découpage (sans lui, une reprise resoumettrait des tranches
    déjà faites)."""
    html = RECORD_LIBRE.read_text(encoding="utf-8")
    append = re.search(r"function appendTranscript\(text\) \{(.*?)\n  \}", html, re.S)
    assert append and "persistDraft();" in append.group(1)
    remplace = re.search(r"function replaceLostMarker\(id, text\) \{(.*?)var delta", html, re.S)
    assert remplace and "persistDraft();" in remplace.group(1)
    bloc_fichier = html.index("// Même recalage que `replaceLostMarker` — sans lui")
    assert "persistDraft();" in html[bloc_fichier:bloc_fichier + 400]
    succes_job = html[html.index("recuperesEnAttente.forEach(postRecoveredJob);"):]
    assert "persistDraft();" in succes_job[:400]


def test_demarrer_n_ecrase_pas_un_brouillon_propose_sans_confirmation() -> None:
    """F1 (bloquant, revue du 2026-09-08) : bandeau affiché, l'utilisateur
    clique « Démarrer » sans « Reprendre » — le premier mot transcrit réécrivait
    le brouillon de 2h. Une confirmation explicite, comme « Ignorer »."""
    html = RECORD_LIBRE.read_text(encoding="utf-8")
    demarrage = html[html.index("startBtn.addEventListener('click'"):html.index("i2dAudioSource.acquire(")]
    assert "recDraft.decision(recDraft.charger(MISSION_ID)" in demarrage
    assert "enAttente.mode === 'reprise'" in demarrage
    assert "confirm(" in demarrage[demarrage.index("enAttente.mode"):]


@pytest.mark.parametrize("ecran", LES_DEUX_ECRANS, ids=lambda p: p.name)
def test_le_budget_de_relance_est_par_session(ecran: Path) -> None:
    """F2 : sans remise à zéro dans « Démarrer » ET « Recommencer », la 2e
    session du même chargement de page héritait du compteur et ne relançait
    plus rien."""
    html = ecran.read_text(encoding="utf-8")
    for debut in ("startBtn.addEventListener('click'", "resetBtn.addEventListener('click'"):
        bloc = html[html.index(debut):html.index(debut) + 3500]
        assert "jobEchecsConsecutifs = 0;" in bloc, debut
        assert "clearTimeout(jobRetryTimer);" in bloc, debut


def test_le_mode_discret_du_bandeau_a_une_regle_css() -> None:
    """F6 : `decision()` distingue « reprise » de « discret », mais sans règle
    CSS la classe n'avait aucun effet — avertissement pleine force pendant 7
    jours après chaque entretien réussi."""
    css = (RACINE / "app" / "static" / "app.css").read_text(encoding="utf-8")
    assert re.search(r"\.rec-draft-discret\s*\{[^}]*--info-bg", css)


@pytest.mark.parametrize("ecran", LES_DEUX_ECRANS, ids=lambda p: p.name)
def test_la_creation_de_tranche_ne_se_tait_plus_sur_echec(ecran: Path) -> None:
    """Avant : `.catch(function () { /* commentaire */ })` — aucune relance,
    aucun mot à l'écran. Le texte n'était pas perdu, mais le tick suivant
    fabriquait une tranche hors gabarit (timeout Ollama le 2026-09-08)."""
    html = ecran.read_text(encoding="utf-8")
    debut = html.index("recFetch('/interviews/segment-jobs'")
    catch = re.search(r"\.catch\(function \(err\) \{(.*?)\n      \}\)", html[debut:], re.S)
    assert catch is not None, "le .catch de la création de tranche n'a pas de paramètre d'erreur"
    corps = catch.group(1)
    for attendu in ("JOB_RETRY_DELAYS_MS", "jobStatusEl.textContent", "submitSegmentJob();"):
        assert attendu in corps, attendu
    # La relance est gardée par la génération ET l'enregistrement actif : une
    # session abandonnée ne doit jamais re-poster. (`backupGeneration` sur
    # l'écran libre, `recGeneration` sur l'écran structuré.)
    assert re.search(r"gen !== (backupGeneration|recGeneration) \|\| !recordingActive", corps)
    # Et le minuteur de relance meurt avec l'enregistrement.
    assert "clearTimeout(jobRetryTimer);" in html[html.index("stopBtn.addEventListener('click'"):]


def test_les_evenements_de_sauvegarde_audio_sont_journalises() -> None:
    """D4 (2026-09-08) : zéro tranche audio sur 2h d'entretien, et plus rien
    pour dire pourquoi. Rotation, envoi, succès, échec : chacun laisse une
    trace dans le brouillon local, qui survit à l'onglet."""
    html = RECORD_LIBRE.read_text(encoding="utf-8")
    for evenement in ("'backup-rotation'", "'backup-envoi'", "'backup-ok'", "'backup-ko'", "'job-ko'"):
        assert f"type: {evenement}" in html, evenement


# ---- Trace serveur positive (route de sauvegarde audio) ----------------------


def test_la_sauvegarde_audio_laisse_une_trace_positive_dans_le_journal(tmp_path, caplog, monkeypatch) -> None:
    import logging

    from fastapi.testclient import TestClient

    from app import db as app_db
    from app.main import app
    from app.routers import interviews as interviews_router

    # Écriture dans un dossier jetable : jamais dans data/recordings réel.
    monkeypatch.setattr(interviews_router, "RECORDINGS_DIR", tmp_path)
    app_db.init_db()
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="app.routers.interviews"):
        reponse = client.post(
            "/missions/0/interviews/record/backup",
            files={"file": ("entretien.webm", b"\0" * 2048, "audio/webm")},
            headers={"Origin": "http://testserver"},
        )
    # mission 0 : refus franc avant toute écriture — le test de journal porte
    # sur le chemin qui écrit, ci-dessous.
    assert reponse.status_code == 400

    from sqlalchemy import select

    from app.models import Mission

    # Mission DÉDIÉE, jamais « la première trouvée » : la base de test est
    # partagée par toute la session (chemin fixe, conftest.py), reprendre une
    # mission d'un autre module rend ce test dépendant de l'ordre de collection.
    session = app_db.SessionLocal()
    try:
        mission = Mission(name="Journal sauvegarde audio (test dédié)")
        session.add(mission)
        session.commit()
        mission_id = mission.id
    finally:
        session.close()

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.routers.interviews"):
        reponse = client.post(
            f"/missions/{mission_id}/interviews/record/backup",
            files={"file": ("entretien.webm", b"\0" * 2048, "audio/webm")},
            headers={"Origin": "http://testserver"},
        )
    assert reponse.status_code == 200, reponse.text
    nom = reponse.json()["path"]
    assert (tmp_path / nom).stat().st_size == 2048
    traces = [r.getMessage() for r in caplog.records if "Sauvegarde audio de secours écrite" in r.getMessage()]
    assert traces, [r.getMessage() for r in caplog.records]
    assert nom in traces[0] and "2048 octets" in traces[0]
    # Le nom seul : jamais le chemin absolu du poste dans le journal.
    assert str(tmp_path) not in traces[0]
