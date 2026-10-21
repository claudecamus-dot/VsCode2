"""Handlers « Enregistrer » de record_libre.html, JS EXÉCUTÉ sous node (2026-10-19).

Le bloc `submit`, le `beforeunload` et le `pageshow` sont extraits du template tels
quels (pas de copie) puis exécutés avec un `window`/`document`/`confirm` factices.
Lot navigateur : bouton jamais grisé définitivement, message dédié au segment perdu,
relances de segment allongées."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
LIBRE = RACINE / "app" / "templates" / "interviews" / "record_libre.html"
RECORD = RACINE / "app" / "templates" / "interviews" / "record.html"


def _bloc(src: str, debut: str) -> str:
    """Instruction `…addEventListener(…, function (…) { … });` complète."""
    i = src.index(debut)
    j = src.index("{", src.index("function", i))
    prof = 0
    for k in range(j, len(src)):
        prof += (src[k] == "{") - (src[k] == "}")
        if prof == 0:
            fin = src.index(");", k) + 2
            return src[i:fin]
    raise AssertionError(debut)


def _fonction(src: str, nom: str) -> str:
    m = re.search(r"function\s+%s\s*\(" % nom, src)
    assert m
    i = src.index("{", m.start())
    prof = 0
    for j in range(i, len(src)):
        prof += (src[j] == "{") - (src[j] == "}")
        if prof == 0:
            return src[m.start(): j + 1]
    raise AssertionError


def _node(script: str) -> dict:
    if shutil.which("node") is None:
        pytest.fail("node est requis pour exécuter le JS de l'écran.")
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr[:2000]
    return json.loads(res.stdout.strip().splitlines()[-1])


def _scenario(*, confirm_ok: bool, en_vol: int = 0, perdus: int = 0, restauree: bool = False,
              apres: str = "", attente: int = 0, pre: str = "") -> dict:
    src = LIBRE.read_text(encoding="utf-8")
    submit = _bloc(src, "document.getElementById('record-form').addEventListener('submit'")
    unload = _bloc(src, "window.addEventListener('beforeunload'")
    pageshow = _bloc(src, "window.addEventListener('pageshow'")
    script = """
var handlers = {}, champs = {}, confirmes = 0, majEtat = 0;
var formEl = { addEventListener: function (n, f) { handlers['form:' + n] = f; },
  querySelector: function (s) { var m = /name="([^"]+)"/.exec(s); return champs[m[1]] || null; },
  appendChild: function (c) { champs[c.name] = c; } };
var document = { getElementById: function (id) { return id === 'record-form' ? formEl : null; },
  createElement: function () { return {}; } };
var window = { addEventListener: function (n, f) { handlers['win:' + n] = f; } };
var msgs = [], lostRetryActive = 0, lowEnVol = 0;
function confirm(m) { confirmes++; msgs.push(m); return %s; }
var lostSegments = %s, lostRetryBlocking = 0, pendingSegments = %d, pendingSegmentJobSubmits = 0, waitingSegments = new Array(%d).fill({});
var sessionRestauree = %s, recordingActive = false, pendingBackups = 0, backupPerdusLocaux = [],
    backupLocalAcquitte = false, envoiEnCours = false;
var sessionToken = 'tok', sessionTokenHidden = {}, newSessionToken = function () { return 'n'; };
var backupSegments = [{position: 0}, {position: 1}], nextBackupPosition = 2, MISSION_ID = 1;
var submitBtn = { disabled: false };
%s
function updateSubmitState() { majEtat++; submitBtn.disabled = false; }
%s
%s
%s
var ev = { target: formEl, prevented: false, preventDefault: function () { this.prevented = true; } };
handlers['form:submit'](ev);
var apresSubmit = { prevented: ev.prevented, disabled: submitBtn.disabled, envoi: envoiEnCours };
var e2 = { prevented: false, returnValue: null, preventDefault: function () { this.prevented = true; } };
handlers['win:beforeunload'](e2);
var boite = e2.prevented;
%s
console.log(JSON.stringify({ prevented: apresSubmit.prevented, disabled: apresSubmit.disabled,
  envoi: apresSubmit.envoi, boite: boite, confirmes: confirmes, msgs: msgs, majEtat: majEtat,
  envoiFinal: envoiEnCours, disabledFinal: submitBtn.disabled,
  attendu: champs.audio_attendu ? String(champs.audio_attendu.value) : null,
  incomplete: champs.transcription_incomplete ? String(champs.transcription_incomplete.value) : null }));
""" % (
        json.dumps(confirm_ok), json.dumps([{"blocking": True}] * perdus), en_vol, attente,
        json.dumps(restauree), pre, submit, unload, pageshow, apres,
    )
    return _node(script)


def test_confirm_refuse_n_envoie_pas_et_ne_grise_pas():
    r = _scenario(confirm_ok=False, en_vol=2)
    assert r["confirmes"] == 1
    assert r["prevented"] is True
    assert r["disabled"] is False
    assert r["envoi"] is False
    assert r["attendu"] is None  # on n'a rien posé


def test_confirm_session_restauree_refuse_ne_grise_pas():
    r = _scenario(confirm_ok=False, restauree=True)
    assert r["prevented"] is True and r["disabled"] is False and r["envoi"] is False


def test_confirm_accepte_envoie_grise_et_pose_les_champs():
    r = _scenario(confirm_ok=True, en_vol=2)
    assert r["prevented"] is False
    assert r["disabled"] is True
    assert r["envoi"] is True
    assert r["attendu"] == "2"
    assert r["incomplete"] == "1"


def test_envoi_complet_sans_segment_en_vol_ne_pose_pas_incomplete():
    r = _scenario(confirm_ok=True)
    assert r["confirmes"] == 0
    assert r["attendu"] == "2" and r["incomplete"] == ""


def test_segment_perdu_pose_incomplete():
    r = _scenario(confirm_ok=True, perdus=1)
    assert r["incomplete"] == "1" and r["envoi"] is True


def test_pas_de_boite_native_pendant_notre_envoi():
    # transcription en vol : sans envoiEnCours, beforeunload ouvrirait « Quitter le site ? »
    r = _scenario(confirm_ok=True, en_vol=2)
    assert r["boite"] is False


def test_beforeunload_protege_toujours_hors_envoi():
    # même état, mais sans passer par un submit accepté : la boîte native reste là
    src = LIBRE.read_text(encoding="utf-8")
    unload = _bloc(src, "window.addEventListener('beforeunload'")
    r = _node("""
var h = {}; var window = { addEventListener: function (n, f) { h[n] = f; } };
var lostSegments = [], lostRetryBlocking = 0, recordingActive = false, pendingBackups = 0,
    pendingSegments = 1, backupPerdusLocaux = [], backupLocalAcquitte = false, envoiEnCours = false;
%s
var e = { prevented: false, preventDefault: function () { this.prevented = true; } };
h.beforeunload(e);
console.log(JSON.stringify({ boite: e.prevented }));
""" % unload)
    assert r["boite"] is True


def test_confirm_distingue_en_cours_et_a_relancer():
    r = _scenario(confirm_ok=False, en_vol=2, attente=3)
    (msg,) = r["msgs"]
    assert "2 segment(s) en cours de transcription" in msg
    assert "3 segment(s) à relancer (en attente ou perdus)" in msg
    assert "tu pourras les relancer depuis la fiche" in msg
    solo = _scenario(confirm_ok=False, attente=1)["msgs"][0]
    assert "en cours de transcription" not in solo and "1 segment(s) à relancer" in solo


def test_confirm_compte_les_relances_du_drain_sans_doubler_celle_en_vol():
    # « Relancer tous » (3 segments): 1 en vol (déjà dans pendingSegments) + 2 dans la closure.
    r = _scenario(confirm_ok=False, en_vol=1, pre="lostRetryActive = 3; lowEnVol = 1;")
    assert "3 segment(s) en cours de transcription" in r["msgs"][0]


def test_segment_en_attente_pose_incomplete_et_demande_confirmation():
    r = _scenario(confirm_ok=True, attente=1)
    assert r["confirmes"] == 1 and r["incomplete"] == "1"


def test_pageshow_rearme_le_bouton_et_recalcule_l_etat():
    r = _scenario(confirm_ok=True, en_vol=2, apres="""
handlers['win:pageshow']({ persisted: true });
""")
    assert r["envoiFinal"] is False
    assert r["disabledFinal"] is False
    assert r["majEtat"] >= 1


# ---------------------------------------------------------------- message du segment perdu
def _etat(perdus: int, serveur: int) -> dict:
    code = _fonction(LIBRE.read_text(encoding="utf-8"), "updateSubmitState")
    return _node("""
var transcriptSoFar='x', pendingSegments=0, pendingSegmentJobSubmits=0, pendingBackups=0,
    finalizing=false, recordingActive=false, missionAbsente=false, backupLocalAcquitte=false,
    backupConfirmes=%d, sessionRestauree=false;
var lostSegments=%s, lostRetryBlocking=0;
var backupSegments=%s, backupPerdusLocaux=[];
var submitBtn={disabled:null}, pdfBtn={disabled:null}, statusEl={textContent:''};
%s
updateSubmitState();
console.log(JSON.stringify({disabled: submitBtn.disabled, msg: statusEl.textContent}));
""" % (serveur, json.dumps([{"blocking": True}] * perdus),
       json.dumps([{"filename": "a.webm", "position": 0}] * serveur), code))


def test_segment_perdu_sans_copie_serveur_message_dedie_pas_pret_a_envoyer():
    r = _etat(perdus=1, serveur=0)
    assert r["disabled"] is True
    assert r["msg"] == "Un segment n’a pas pu être transcrit : relance-le ou abandonne-le pour enregistrer."
    assert "prêt à envoyer" not in r["msg"]


def test_sans_segment_perdu_le_message_pret_a_envoyer_reste():
    r = _etat(perdus=0, serveur=0)
    assert r["disabled"] is False and "prêt à envoyer" in r["msg"]


# ---------------------------------------------------------------- relances de segment
def test_relances_de_segment_allongees_et_tranches_de_sauvegarde_inchangees():
    for chemin in (LIBRE, RECORD):
        src = chemin.read_text(encoding="utf-8")
        seg = re.search(r"var SEGMENT_RETRY_DELAYS_MS = (\[[^\]]*\]);", src)
        bak = re.search(r"var BACKUP_RETRY_DELAYS_MS = (\[[^\]]*\]);", src)
        assert seg and json.loads(seg.group(1)) == [2000, 6000, 15000, 30000], chemin.name
        assert bak and json.loads(bak.group(1)) == [2000, 6000], chemin.name
        # le budget des tranches de sauvegarde ne partage plus la constante des segments
        assert "attempt < BACKUP_RETRY_DELAYS_MS.length" in src, chemin.name


def test_relance_occupe_respecte_retry_after_et_reste_en_attente():
    """Contrat : un 503 « busy » ne consomme pas le budget de relances (`attempt`
    inchangé), ne perd pas le segment (pendingSegments tenu) et borne le délai par
    `retry_after_s` ; l'exécution de cette logique est dans
    test_transcription_occupee_execution.py."""
    src = LIBRE.read_text(encoding="utf-8")
    bloc = _fonction(src, "uploadSegment")
    assert "Number(retryAfterS) || 2" in bloc
    assert "uploadSegment(blob, attempt, lostId, gen, onSettle, busyUntil, ctx)" in bloc
    assert "result.status === 503 && result.data.code === 'busy'" in bloc
