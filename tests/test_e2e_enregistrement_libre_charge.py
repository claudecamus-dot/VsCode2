"""Test de CHARGE opt-in : l'enregistrement libre tient-il le temps réel avec le
modèle Whisper configuré par défaut ?

Pourquoi ce test existe : le 2026-10-27, en entretien libre, tous les segments
ont été « perdus ». Whisper `medium` mettait ~132 s par minute d'audio sur ce
poste (plus lent que le temps réel) et le 1er segment ~5 min à froid. Aucun test
existant ne le voit : `test_e2e_enregistrement_libre.py` force `tiny` et n'enregistre
que 7 s, donc un seul segment et jamais de rotation à `SEGMENT_MS` (60 s).

Ce que ce test joue : vrai uvicorn (base temporaire, jamais data/app.db), vrai
Edge/Chrome headless, faux micro rejouant de la parole française générée par
System.Speech (Windows) ; modèle Whisper = celui par défaut de l'app (on ne pose PAS
WHISPER_MODEL ; si la variable est héritée de l'environnement, elle est signalée
dans le message d'échec) ; >= 130 s d'enregistrement (>= 2 rotations), puis Arrêter.

Il affirme :
1. chaque `POST /audio/transcribe-segment` répond 200 en moins de SEGMENT_MS (60 s)
   après son envoi — condition « le modèle suit le temps réel » (latence mesurée
   par un espion sur `fetch`) ;
2. aucun marqueur ⚠ « non transcrit » / « abandonné » ne reste dans la transcription
   à l'arrêt ;
3. en cas d'échec, le message liste la latence par segment et le modèle utilisé.

Il est ROUGE sur un poste où Whisper ne suit pas : c'est voulu, il documente la
capacité du poste.

Durée attendue : ~10-15 min (130 s d'enregistrement + transcription de tous les
segments + chargement à froid du modèle). Opt-in parce que trop long et dépendant
du poste pour la suite normale ; sauté par défaut. Commande :

    RUN_WHISPER_CHARGE=1 pytest tests/test_e2e_enregistrement_libre_charge.py -s

Prérequis : Windows avec System.Speech (voix française de préférence), navigateur
Chromium, faster-whisper. Sinon sauté avec la raison.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_WHISPER_CHARGE") != "1",
    reason="test de charge Whisper (~10-15 min, dépend du poste) : RUN_WHISPER_CHARGE=1 pour l'activer",
)

try:
    import websockets  # noqa: F401 — fourni par uvicorn[standard]
except ModuleNotFoundError:
    pytest.skip("websockets (uvicorn[standard]) requis", allow_module_level=True)

import navigateur_cdp  # noqa: E402
from e2e_serveur import serveur_uvicorn  # noqa: E402
from navigateur_cdp import Navigateur, trouver_navigateur  # noqa: E402

from app.services import audio_transcribe  # noqa: E402

_NAVIGATEUR = trouver_navigateur()
SEGMENT_MS = 60000  # miroir de record_libre.html (var SEGMENT_MS)
_DUREE_ENREGISTREMENT_S = float(os.environ.get("CHARGE_DUREE_S", "130"))
_DELAI_DRAIN_S = 420.0  # après Arrêter : attente des derniers POST (échec = rouge)

_TEXTE_FR = (
    "Bonjour, merci de me recevoir. Je suis responsable des opérations depuis six ans. "
    "Notre principal défi est la coordination entre les équipes, qui travaillent chacune "
    "avec leurs propres outils. Nous perdons beaucoup de temps en réunions de suivi. "
    "Je souhaiterais un tableau de bord partagé, simple, que tout le monde consulte "
    "chaque matin. Le budget est limité, mais la direction est favorable au projet. "
)

_SPY = r"""(function(){ if(window.__spy) return 1; window.__spy=[]; var f=window.fetch;
window.fetch=function(u,o){ var p=f.apply(this,arguments);
 if(String(u).indexOf('transcribe-segment')>=0){
  var rec={t0:Date.now(),t1:null,status:null,err:null}; window.__spy.push(rec);
  p.then(function(r){ rec.t1=Date.now(); rec.status=r.status; },
         function(e){ rec.t1=Date.now(); rec.err=String(e); }); }
 return p; };
return 1; })();"""


def _generer_wav(dest: Path) -> None:
    """Parole française via System.Speech (Windows) ; saute le test sinon."""
    if os.name != "nt" or not shutil.which("powershell"):
        pytest.skip("System.Speech (Windows/PowerShell) requis pour générer l'audio de parole")
    script = dest.with_suffix(".ps1")
    texte = (_TEXTE_FR * 3).replace("'", "''")
    script.write_text(
        "Add-Type -AssemblyName System.Speech\n"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "$fr = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'fr*' } "
        "| Select-Object -First 1\n"
        "if ($fr) { $s.SelectVoice($fr.VoiceInfo.Name) }\n"
        "$s.Rate = 0\n"
        f"$s.SetOutputToWaveFile('{dest}')\n"
        f"$s.Speak('{texte}')\n"
        "$s.Dispose()\n",
        encoding="utf-8-sig",
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0 or not dest.exists() or dest.stat().st_size < 10_000:
        pytest.skip(f"System.Speech n'a pas produit d'audio : {r.stderr[-300:]!r}")


def _attendre(nav: Navigateur, expression: str, delai_s: float, quoi: str):
    limite = time.monotonic() + delai_s
    while time.monotonic() < limite:
        valeur = nav.evaluer(expression)
        if valeur:
            return valeur
        time.sleep(1)
    raise AssertionError(f"{quoi} : rien après {delai_s:.0f}s — page {nav.url()}")


def _rapport(spy: list[dict], modele: str) -> str:
    lignes = []
    for i, s in enumerate(spy, 1):
        if s.get("t1") is None:
            lignes.append(f"  segment POST #{i} : SANS RÉPONSE")
        else:
            lat = (s["t1"] - s["t0"]) / 1000
            etat = s["status"] if s.get("err") is None else f"erreur {s['err']}"
            lignes.append(f"  segment POST #{i} : {lat:.1f} s -> {etat} (seuil {SEGMENT_MS / 1000:.0f} s)")
    return f"modèle Whisper : {modele}\n" + "\n".join(lignes)


def test_whisper_suit_le_temps_reel_sur_un_enregistrement_libre(monkeypatch: pytest.MonkeyPatch) -> None:
    if _NAVIGATEUR is None:
        pytest.skip("aucun navigateur Chromium (Edge/Chrome) trouvé — E2E_NAVIGATEUR pour en forcer un")
    if not audio_transcribe.is_available():
        pytest.skip("faster-whisper non installé")
    modele = os.environ.get("WHISPER_MODEL") or f"{audio_transcribe.MODEL_SIZE} (défaut de l'app)"

    racine = Path(tempfile.mkdtemp(prefix="charge-"))
    nav = None
    try:
        wav = racine / "parole.wav"
        _generer_wav(wav)
        monkeypatch.setattr(navigateur_cdp, "_DRAPEAUX_LANCEMENT", [
            *navigateur_cdp._DRAPEAUX_LANCEMENT,
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
            f"--use-file-for-fake-audio-capture={wav}",
        ])
        srv_dir = racine / "srv"
        srv_dir.mkdir()
        # Ollama injoignable et sans attente : seule la transcription est mesurée.
        with serveur_uvicorn(srv_dir, OLLAMA_HOST="http://127.0.0.1:9", OLLAMA_TIMEOUT="5") as base:
            nav = Navigateur(_NAVIGATEUR, racine / "profil")
            nav.naviguer(base + "/")
            nav.cliquer_et_attendre("form[action='/mode/reel'] button[type=submit]")
            nav.cliquer_et_attendre("form[action='/entretiens/libre/nouveau'] button[type=submit]")
            assert nav.url().rstrip("/").endswith("/record-libre"), nav.url()
            nav.evaluer(_SPY)
            nav.remplir("input[name=interviewee_name]", "Charge")
            nav.cliquer("#rec-start")
            _attendre(nav, "!document.getElementById('rec-stop').hidden", 30, "Démarrer (micro accordé)")
            t0 = time.monotonic()
            while time.monotonic() - t0 < _DUREE_ENREGISTREMENT_S:
                time.sleep(10)
                print(f"[charge] {int(time.monotonic() - t0)} s — "
                      + json.dumps(nav.evaluer("window.__spy"))[:300], flush=True)
            nav.cliquer("#rec-stop")

            # Drain : tous les POST envoyés ont une réponse ET aucun marqueur ⚠ ne reste
            # (ou délai épuisé -> on mesure l'état tel quel).
            limite = time.monotonic() + _DELAI_DRAIN_S
            while time.monotonic() < limite:
                time.sleep(5)
                spy = nav.evaluer("window.__spy") or []
                transcript = nav.evaluer("document.getElementById('rec-transcript').value") or ""
                if (len(spy) >= int(_DUREE_ENREGISTREMENT_S // 60) and all(s["t1"] for s in spy)
                        and "⚠" not in transcript):
                    break
            spy = nav.evaluer("window.__spy") or []
            transcript = nav.evaluer("document.getElementById('rec-transcript').value") or ""
            statut = nav.evaluer("document.getElementById('rec-status').textContent") or ""
            rapport = _rapport(spy, modele)
            print("[charge] " + rapport + f"\n  statut : {statut[:200]}\n  transcription : {transcript[:300]!r}")

            assert len(spy) >= int(_DUREE_ENREGISTREMENT_S // 60), (
                f"moins de segments que de rotations attendues ({len(spy)})\n{rapport}")
            lents = [s for s in spy
                     if s.get("t1") is None or s.get("status") != 200 or (s["t1"] - s["t0"]) >= SEGMENT_MS]
            assert not lents, (
                "Whisper ne suit pas le temps réel : des segments ne sont pas répondus 200 en "
                f"moins de {SEGMENT_MS / 1000:.0f} s\n{rapport}")
            assert "⚠" not in transcript, (
                f"marqueur ⚠ « non transcrit » dans la transcription : {transcript[:500]!r}\n{rapport}")
    finally:
        if nav is not None:
            nav.fermer()
        shutil.rmtree(racine, ignore_errors=True)
