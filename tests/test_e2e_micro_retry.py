"""Test utilisateur : la REPRISE d'un segment du micro direct et l'alerte de
complétude d'un import, dans un vrai navigateur (lot « transcription fiable »,
commit 4 ; règle P5 du projet).

Ce que ce module joue, sur l'écran RÉEL d'enregistrement libre (vrai uvicorn,
base temporaire, faux micro Chromium qui rejoue un clip de parole) :

- un 422 `code: "transient"` puis un 200 -> le texte s'insère, AUCUN bandeau
  « segments perdus », et la ligne discrète « 1 segment rejoué automatiquement »
  est visible ;
- 4 réponses 422 transient de suite (initial + 3 relances à 2 s / 6 s / 15 s) ->
  le bandeau BLOQUANT apparaît, le blob est gardé ;
- l'état nominal n'affiche ni compteur ni bandeau ;
- un import de fichier dont le job est `done` mais `incomplet` -> bandeau de
  complétude persistant ; `complet` -> rien.

Le serveur de transcription est SIMULÉ côté page (interception de `fetch` pour
les seules routes `/audio/transcribe-segment`, `/audio/transcribe-file` et son
`/status`) : le critère ici est le comportement de l'ÉCRAN face à des réponses
précises, que le vrai Whisper ne produit pas à la demande. Le reste (pages,
CSRF, authentification, uploads de sauvegarde) est réel.

record.html (mode guidé) n'est PAS rejoué ici : son parcours exige une mission,
une trame et des questions semées ; il est couvert par le harnais d'exécution
tests/test_micro_direct_retry_execution.py (même `uploadSegment` extrait).

Variable d'environnement facultative `E2E_CAPTURES_DIR` : dossier où déposer des
captures d'écran des états (pour les REGARDER).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

_OBLIGATOIRE = bool(os.environ.get("E2E_OBLIGATOIRE"))
try:
    import websockets  # noqa: F401 — fourni par uvicorn[standard]
except ModuleNotFoundError:
    if _OBLIGATOIRE:
        raise RuntimeError(
            "E2E_OBLIGATOIRE est posé mais `websockets` (uvicorn[standard]) est absent"
        ) from None
    pytest.skip("websockets (uvicorn[standard]) requis", allow_module_level=True)

import navigateur_cdp  # noqa: E402
from e2e_serveur import serveur_uvicorn  # noqa: E402
from navigateur_cdp import Navigateur, trouver_navigateur  # noqa: E402

_RACINE = Path(__file__).resolve().parents[1]
_WAV = _RACINE / "tests" / "fixtures" / "audio" / "tts_anglais_synthetique.wav"
_NAVIGATEUR = trouver_navigateur()
_CAPTURES = os.environ.get("E2E_CAPTURES_DIR")

if _NAVIGATEUR is None and _OBLIGATOIRE:
    raise RuntimeError("E2E_OBLIGATOIRE est posé mais aucun navigateur Chromium n'est trouvé")

pytestmark = pytest.mark.skipif(
    _NAVIGATEUR is None,
    reason="aucun navigateur Chromium (Edge/Chrome) trouvé — E2E_NAVIGATEUR pour en forcer un",
)

_TRANSIENT = [422, {"code": "transient", "error": "Échec de la transcription : worker interrompu"}]
_OK = [200, {"text": "texte rejoué avec succès"}]
_INCOMPLET = {"statut": "incomplet", "blocs_vides": [1], "tranches_ignorees": [],
              "blocs_rejoues": [], "duree_conteneur_s": 600, "duree_decodee_s": 600, "ecart_s": 0}
_COMPLET = {"statut": "complet", "blocs_vides": [], "tranches_ignorees": [],
            "blocs_rejoues": [], "duree_conteneur_s": 600, "duree_decodee_s": 598, "ecart_s": 2}


@pytest.fixture(scope="module")
def serveur(tmp_path_factory: pytest.TempPathFactory):
    with serveur_uvicorn(tmp_path_factory.mktemp("e2e-micro-retry"), AI_PROVIDER="ollama", OLLAMA_TIMEOUT="5") as base:
        yield base


@pytest.fixture
def nav(monkeypatch: pytest.MonkeyPatch):
    assert _WAV.exists(), _WAV
    monkeypatch.setattr(navigateur_cdp, "_DRAPEAUX_LANCEMENT", [
        *navigateur_cdp._DRAPEAUX_LANCEMENT,
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        f"--use-file-for-fake-audio-capture={_WAV}",
    ])
    profil = Path(tempfile.mkdtemp(prefix="e2e-mic-retry-"))
    navigateur = None
    try:
        navigateur = Navigateur(_NAVIGATEUR, profil / "profil")
        yield navigateur
    finally:
        if navigateur is not None:
            navigateur.fermer()
        shutil.rmtree(profil, ignore_errors=True)


def _attendre(nav: Navigateur, expression: str, delai_s: float, quoi: str):
    limite = time.monotonic() + delai_s
    while time.monotonic() < limite:
        valeur = nav.evaluer(expression)
        if valeur:
            return valeur
        time.sleep(0.5)
    raise AssertionError(
        f"{quoi} : toujours rien après {delai_s:.0f}s — page {nav.url()} ; statut « "
        f"{nav.evaluer('(document.getElementById(\"rec-status\")||{}).textContent||\"\"')} » ; "
        f"exceptions {nav.exceptions}"
    )


def _capturer(nav: Navigateur, nom: str) -> None:
    if _CAPTURES:
        nav.capturer(Path(_CAPTURES) / f"{nom}.png", page_entiere=False)


def _ouvrir_ecran_libre(nav: Navigateur, base: str) -> None:
    nav.naviguer(base + "/")
    nav.cliquer_et_attendre("form[action='/mode/reel'] button[type=submit]")
    nav.cliquer_et_attendre("form[action='/entretiens/libre/nouveau'] button[type=submit]")
    assert nav.url().rstrip("/").endswith("/record-libre"), nav.url()


def _simuler_transcription(nav: Navigateur, segments: list, statut: dict | None = None) -> None:
    """Intercepte `fetch` pour les seules routes de transcription : la liste
    `segments` est consommée dans l'ordre (la dernière réponse se répète)."""
    nav.evaluer("""(function (plan, statut) {
      var reel = window.fetch.bind(window);
      window.__stub = { segments: 0, status: 0 };
      function rep(status, corps) {
        return Promise.resolve(new Response(JSON.stringify(corps),
          { status: status, headers: { 'Content-Type': 'application/json' } }));
      }
      window.fetch = function (url, opts) {
        var u = String(url);
        if (u.indexOf('/audio/transcribe-segment') >= 0) {
          var r = plan[Math.min(window.__stub.segments++, plan.length - 1)];
          return rep(r[0], r[1]);
        }
        if (u.indexOf('/audio/transcribe-file/status') >= 0) { window.__stub.status++; return rep(200, statut); }
        if (u.indexOf('/audio/transcribe-file') >= 0) return rep(200, { job_id: 'j1' });
        return reel(url, opts);
      };
      return true;
    })(%s, %s)""" % (json.dumps(segments), json.dumps(statut or {})))


def _parler_puis_arreter(nav: Navigateur) -> None:
    assert not nav.evaluer("document.getElementById('rec-start').disabled")
    nav.remplir("input[name=interviewee_name]", "Personne Test")
    nav.cliquer("#rec-start")
    _attendre(nav, "!document.getElementById('rec-stop').hidden", 30, "Démarrer (micro accordé)")
    time.sleep(4.0)
    nav.cliquer("#rec-stop")


def test_un_422_transient_puis_un_succes_insere_le_texte_sans_bandeau_d_echec(serveur: str, nav: Navigateur) -> None:
    _ouvrir_ecran_libre(nav, serveur)
    _simuler_transcription(nav, [_TRANSIENT, _OK])
    _parler_puis_arreter(nav)

    texte = _attendre(nav, "document.getElementById('rec-transcript').value.trim()", 60, "texte rejoué inséré")
    assert "texte rejoué avec succès" in texte
    assert nav.evaluer("window.__stub.segments") == 2, "un 422 transient puis un seul rejeu"
    assert nav.evaluer("document.getElementById('rec-lost-banner').hidden") is True, "aucun segment perdu"
    rejoue = _attendre(
        nav, "(function(){var e=document.getElementById('rec-replay-status');"
             "return e.hidden ? '' : e.textContent;})()", 10, "compteur de segments rejoués",
    )
    assert rejoue.strip() == "1 segment rejoué automatiquement", rejoue
    # Neutre : le compteur n'est PAS dans le bandeau rouge des segments perdus.
    assert nav.evaluer("document.getElementById('rec-lost-banner').contains("
                       "document.getElementById('rec-replay-status'))") is False
    _capturer(nav, "segment_rejoue")


def test_trois_422_transient_de_suite_finissent_dans_le_bandeau_bloquant_sans_perdre_le_blob(
    serveur: str, nav: Navigateur,
) -> None:
    _ouvrir_ecran_libre(nav, serveur)
    _simuler_transcription(nav, [_TRANSIENT])  # la dernière réponse se répète : toujours 422 transient
    _parler_puis_arreter(nav)

    # 2 s + 6 s + 15 s de temporisation réelle avant l'abandon : large, pas serré.
    _attendre(nav, "!document.getElementById('rec-lost-banner').hidden", 90, "bandeau des segments perdus")
    assert nav.evaluer("window.__stub.segments") == 4, "tentative initiale + 3 relances"
    banniere = nav.evaluer("document.getElementById('rec-lost-banner').innerText")
    assert "segment" in banniere
    # Le blob est gardé (une ligne avec son bouton de relance manuelle) et
    # bloque la sortie : « Enregistrer » reste grisé tant que la parole n'a pas
    # été rejouée ou abandonnée.
    assert nav.evaluer("document.querySelectorAll('#rec-lost-list button').length") >= 1
    assert nav.evaluer("document.getElementById('rec-submit').disabled") is True
    # UN segment rejoué trois fois = « 1 segment rejoué » (le compteur compte les segments,
    # pas les relances : « 3 segments rejoués » était faux, revue du commit 4).
    assert nav.evaluer("document.getElementById('rec-replay-status').textContent") == "1 segment rejoué automatiquement"
    _capturer(nav, "bandeau_bloquant")


def test_etat_nominal_n_affiche_ni_compteur_ni_bandeau(serveur: str, nav: Navigateur) -> None:
    _ouvrir_ecran_libre(nav, serveur)
    _simuler_transcription(nav, [_OK])
    _parler_puis_arreter(nav)
    _attendre(nav, "document.getElementById('rec-transcript').value.trim()", 60, "texte inséré")
    assert nav.evaluer("document.getElementById('rec-replay-status').hidden") is True
    assert nav.evaluer("document.getElementById('rec-replay-status').textContent") == ""
    assert nav.evaluer("document.getElementById('rec-lost-banner').hidden") is True
    assert nav.evaluer("document.getElementById('rec-completude-banner').hidden") is True
    _capturer(nav, "nominal")


def _importer_un_fichier(nav: Navigateur, completude: dict) -> None:
    statut = {"status": "done", "done": 3, "total": 3, "blocks": ["un", "deux", "trois"],
              "filename": "import.wav", "completude": completude}
    _simuler_transcription(nav, [_OK], statut)
    nav.choisir_fichier("#rec-test-upload", _WAV)
    nav.cliquer("#rec-test-upload-btn")


def test_un_import_termine_mais_incomplet_affiche_un_bandeau_persistant(serveur: str, nav: Navigateur) -> None:
    _ouvrir_ecran_libre(nav, serveur)
    _importer_un_fichier(nav, _INCOMPLET)
    _attendre(nav, "!document.getElementById('rec-completude-banner').hidden", 30, "bandeau de complétude")
    texte = nav.evaluer("document.getElementById('rec-completude-banner').innerText")
    assert "Transcription incomplète" in texte and "bloc sans texte : 2" in texte, texte
    assert "un deux trois" not in texte and "import.wav" not in texte, "ni texte transcrit ni chemin"
    # Persistant : la ligne de statut est réécrite par d'autres chemins juste
    # après « Fichier transcrit » (constaté en réel : « Enregistrement terminé »),
    # le bandeau, lui, vit dans son propre élément.
    time.sleep(2.0)
    assert nav.evaluer("document.getElementById('rec-completude-banner').hidden") is False
    _capturer(nav, "completude_incomplet")


def test_un_import_complet_n_affiche_aucune_alerte(serveur: str, nav: Navigateur) -> None:
    _ouvrir_ecran_libre(nav, serveur)
    _importer_un_fichier(nav, _COMPLET)
    _attendre(nav, "window.__stub.status >= 1", 30, "poll du statut de l'import (job done)")
    time.sleep(2.0)  # laisse le traitement du `done` s'exécuter
    assert nav.evaluer("document.getElementById('rec-completude-banner').hidden") is True
