"""Test utilisateur : l'ENREGISTREMENT LIBRE de bout en bout dans un vrai
navigateur — le micro est capté, la parole est transcrite, l'entretien
s'enregistre avec son tour de table, et le tour de table (la répartition Q/R)
se rejoue autant de fois que nécessaire.

Demande utilisateur du 2026-09-09 : « sécurise via les tests que l'enregistrement
libre avec a minima la transcription fonctionne et l'enregistrement de
l'interview possible avec possibilité de rejouer après autant de fois que
nécessaire la répartition Q/R ».

Ce que ce module prouve, et avec quoi :

- le MICRO est un faux périphérique Chromium qui rejoue en boucle un clip de
  parole synthétique (`tests/fixtures/audio/tts_anglais_synthetique.wav` — TTS
  anglais « Hello, this is a test recording for the transcription pipeline »),
  par les drapeaux `--use-fake-device-for-media-stream`,
  `--use-fake-ui-for-media-stream` et `--use-file-for-fake-audio-capture` ;
  `getUserMedia`, `MediaRecorder`, la rotation des tranches et leur envoi sont
  ceux de l'écran réel ;
- la TRANSCRIPTION est la vraie (faster-whisper, modèle `tiny` pour rester
  court) : on affirme que du texte revient à l'écran et qu'il ressemble au clip ;
- l'IA (extraction des tours de parole = répartition Q/R) est un FAUX Ollama
  HTTP tenu par le test, sur lequel pointe `OLLAMA_HOST` : il répond au contrat
  `/api/chat` avec des tours numérotés (« Extraction n°K »), ce qui rend chaque
  rejeu distinguable. Le vrai modèle n'a rien à faire ici — c'est le circuit
  écran → serveur → IA → écran → base qu'on sécurise, pas la qualité du modèle
  (couverte par `tests/test_ollama_integration.py`, opt-in).

Le parcours réel, tel que l'app le mène : l'entretien libre crée une mission
BROUILLON, et « Enregistrer l'entretien » débouche sur « Finaliser la mission »
(la nommer, ou la rattacher à une existante) avant la fiche de l'entretien.

Rejeu : « ↻ Relancer la transcription (audio) » sur la fiche de l'entretien
retranscrit l'audio persisté et ré-extrait le tour de table (écran de suivi,
revue, remplacement) — joué DEUX fois de suite ; le compteur du faux Ollama et
le numéro d'extraction affiché prouvent que chaque passage est un nouveau calcul.

Prérequis et sauts : navigateur Chromium (même règle que
`test_e2e_premiers_clics.py`, `E2E_OBLIGATOIRE` compris) et `faster-whisper`
importable — sans lui le module est sauté avec sa raison. Le modèle `tiny`
(~75 Mo) est téléchargé par faster-whisper s'il manque du cache.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_OBLIGATOIRE = bool(os.environ.get("E2E_OBLIGATOIRE"))
try:
    import websockets  # noqa: F401 — fourni par uvicorn[standard]
except ModuleNotFoundError:
    if _OBLIGATOIRE:
        raise RuntimeError(
            "E2E_OBLIGATOIRE est posé mais `websockets` (uvicorn[standard]) est absent : "
            "le parcours utilisateur ne peut pas être joué"
        ) from None
    pytest.skip("websockets (uvicorn[standard]) requis", allow_module_level=True)

import navigateur_cdp  # noqa: E402
from e2e_serveur import serveur_uvicorn  # noqa: E402
from navigateur_cdp import Navigateur, trouver_navigateur  # noqa: E402

from app.services import audio_transcribe  # noqa: E402

_RACINE = Path(__file__).resolve().parents[1]
_WAV = _RACINE / "tests" / "fixtures" / "audio" / "tts_anglais_synthetique.wav"
_NAVIGATEUR = trouver_navigateur()

if _NAVIGATEUR is None and _OBLIGATOIRE:
    raise RuntimeError(
        "E2E_OBLIGATOIRE est posé mais aucun navigateur Chromium (Edge/Chrome) n'est "
        "trouvé : le parcours utilisateur ne peut pas être joué — E2E_NAVIGATEUR pour "
        "en désigner un"
    )

pytestmark = [
    pytest.mark.skipif(
        _NAVIGATEUR is None,
        reason="aucun navigateur Chromium (Edge/Chrome) trouvé — E2E_NAVIGATEUR pour en forcer un",
    ),
    pytest.mark.skipif(
        not audio_transcribe.is_available(),
        reason="faster-whisper non installé : pas de transcription à sécuriser",
    ),
]

# Durée d'enregistrement : le faux micro rejoue le clip de 4,5 s en boucle ;
# 7 s garantissent au moins un passage entier de la phrase dans la tranche.
_DUREE_ENREGISTREMENT_S = 7.0
# Une tranche de 7 s en `tiny` sur CPU tient en quelques secondes ; le premier
# appel charge le modèle (WHISPER_WARM_UP=0 côté serveur). Large, pas serré :
# un délai trop court transformerait un poste chargé en faux rouge.
_DELAI_TRANSCRIPTION_S = 240.0


# --------------------------------------------------------------------------- #
# Faux Ollama : le contrat /api/chat, rien d'autre
# --------------------------------------------------------------------------- #
class FauxOllama:
    """Répond à `POST /api/chat` comme Ollama (`{"message": {"content": <json>}}`)
    en reconnaissant le contrat demandé dans le message système : extraction
    des tours (`"turns"`) ou répartition par axes (`"repartition"`). Compte ses
    appels — c'est ce compteur qui prouve les rejeux."""

    def __init__(self) -> None:
        self.extractions = 0
        self.repartitions = 0
        self.panne = False
        self.requetes: list[dict] = []
        self._verrou = threading.Lock()
        faux = self

        class Requete(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # silence
                pass

            def _json(self, corps: dict, statut: int = 200) -> None:
                donnees = json.dumps(corps, ensure_ascii=False).encode("utf-8")
                self.send_response(statut)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(donnees)))
                self.end_headers()
                self.wfile.write(donnees)

            def do_GET(self) -> None:  # /api/tags et consorts
                self._json({"models": [{"name": "faux-modele"}]})

            def do_POST(self) -> None:
                taille = int(self.headers.get("Content-Length") or 0)
                corps = json.loads(self.rfile.read(taille) or b"{}")
                messages = corps.get("messages") or []
                if not messages:  # warm_up_ollama : chargement du modèle
                    self._json({"done": True})
                    return
                systeme = str(messages[0].get("content", ""))
                prompt = str(messages[-1].get("content", ""))
                if faux.panne and '"turns"' in systeme:
                    # Panne simulée de l'extraction (structuration différée).
                    self._json({"error": "panne simulée"})
                    return
                with faux._verrou:
                    faux.requetes.append({"systeme": systeme[:200], "prompt": prompt[:500]})
                    contenu = faux._repondre(systeme, prompt)
                self._json({
                    "model": corps.get("model"),
                    "message": {"role": "assistant", "content": json.dumps(contenu, ensure_ascii=False)},
                    "done": True,
                })

        self.serveur = ThreadingHTTPServer(("127.0.0.1", 0), Requete)
        self.hote = f"http://127.0.0.1:{self.serveur.server_address[1]}"
        self._fil = threading.Thread(target=self.serveur.serve_forever, daemon=True)
        self._fil.start()

    def _repondre(self, systeme: str, prompt: str) -> dict:
        if '"turns"' in systeme:
            self.extractions += 1
            texte = prompt.split("TRANSCRIPTION :", 1)[-1].strip() or "(transcription vide)"
            return {
                "turns": [
                    {
                        "interlocuteur": "Consultant·e",
                        "question": "Pouvez-vous vous présenter ?",
                        "section_title": f"Extraction n°{self.extractions}",
                    },
                    {"interlocuteur": "Interviewé·e", "remarque": texte},
                ],
                "identite": {"interviewee_name": "", "interviewee_role": "", "interviewee_entity": ""},
            }
        if '"repartition"' in systeme:
            self.repartitions += 1
            m = re.search(r"aux \d+ clés (.+?)\)", systeme)
            cles = m.group(1).split("/") if m else []
            return {
                "repartition": {c: f"Répartition n°{self.repartitions} — {c}" for c in cles},
                "resume": f"Résumé factice n°{self.repartitions}",
            }
        return {}

    def fermer(self) -> None:
        self.serveur.shutdown()
        self.serveur.server_close()


@pytest.fixture(scope="module")
def faux_ollama():
    faux = FauxOllama()
    try:
        yield faux
    finally:
        faux.fermer()


# --------------------------------------------------------------------------- #
# Vrai uvicorn, vraie transcription (tiny), faux Ollama
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def serveur(tmp_path_factory: pytest.TempPathFactory, faux_ollama: FauxOllama):
    with serveur_uvicorn(
        tmp_path_factory.mktemp("e2e-libre"),
        AI_PROVIDER="ollama", OLLAMA_HOST=faux_ollama.hote, OLLAMA_TIMEOUT="30",
        WHISPER_MODEL="tiny",
    ) as base:
        yield base


@pytest.fixture
def nav(monkeypatch: pytest.MonkeyPatch):
    """Navigateur au faux micro : `_DRAPEAUX_LANCEMENT` est lu à chaque
    lancement, on l'étend ici plutôt que d'ouvrir le pilote aux options."""
    assert _WAV.exists(), _WAV
    monkeypatch.setattr(navigateur_cdp, "_DRAPEAUX_LANCEMENT", [
        *navigateur_cdp._DRAPEAUX_LANCEMENT,
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        f"--use-file-for-fake-audio-capture={_WAV}",
    ])
    profil = Path(tempfile.mkdtemp(prefix="e2e-mic-"))
    navigateur = None
    try:
        navigateur = Navigateur(_NAVIGATEUR, profil / "profil")
        yield navigateur
    finally:
        if navigateur is not None:
            navigateur.fermer()
        shutil.rmtree(profil, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Parcours
# --------------------------------------------------------------------------- #
def _attendre(nav: Navigateur, expression: str, delai_s: float, quoi: str):
    """Évalue `expression` jusqu'à ce qu'elle rende une valeur vraie."""
    limite = time.monotonic() + delai_s
    while time.monotonic() < limite:
        valeur = nav.evaluer(expression)
        if valeur:
            return valeur
        time.sleep(0.5)
    raise AssertionError(
        f"{quoi} : toujours rien après {delai_s:.0f}s — page {nav.url()} ; statut « "
        f"{nav.evaluer('(document.getElementById(\"rec-status\")||{}).textContent||\"\"')} » ; "
        f"texte : {nav.texte()[:700]!r} ; erreurs {nav.erreurs()} exceptions {nav.exceptions}"
    )


def _tour_de_table(nav: Navigateur) -> str:
    """Le tour de table est ÉDITABLE : ses tours vivent dans des champs
    (`turn_section_title`, `turn_interlocuteur`, `turn_question`,
    `turn_remarque`), invisibles d'`innerText` — on lit leurs valeurs."""
    return nav.evaluer(
        "Array.from(document.querySelectorAll("
        "'input[name=turn_section_title],input[name=turn_interlocuteur],"
        "textarea[name=turn_question],textarea[name=turn_remarque]'"
        ")).map(function(e){return e.value;}).join(' | ')"
    )


def _sans_erreur(nav: Navigateur, etape: str) -> None:
    nav.drainer()
    assert not nav.erreurs(), f"{etape} : réponses en erreur {nav.erreurs()}"
    assert not nav.exceptions, f"{etape} : exceptions JS {nav.exceptions}"
    assert not nav.erreurs_cdp, f"{etape} : erreur CDP {nav.erreurs_cdp}"
    assert "Origine non autorisée" not in nav.texte(), f"{etape} : 403 CSRF rendu à l'écran"


def _enregistrer_un_entretien_libre(nav: Navigateur, base: str, nom: str) -> tuple[str, str]:
    """Depuis la première page : mode réel → Démarrer un entretien libre →
    parler (faux micro) → Arrêter → transcription et tours à l'écran →
    Enregistrer l'entretien → Finaliser la mission (la nommer) → fiche de
    l'entretien. Rend (url de la fiche, texte transcrit)."""
    nav.naviguer(base + "/")
    nav.cliquer_et_attendre("form[action='/mode/reel'] button[type=submit]")
    nav.cliquer_et_attendre("form[action='/entretiens/libre/nouveau'] button[type=submit]")
    texte = _parler_et_attendre_le_tour_de_table(nav, nom)

    nav.cliquer_et_attendre("#rec-submit")
    # Écran d'attente possible (extraction encore en vol) : il repost tout seul,
    # puis « Finaliser la mission » — le brouillon créé par l'entretien libre
    # doit être nommé (ou rattaché) avant d'arriver sur la fiche.
    _attendre(
        nav, "/\\/finaliser\\/?$/.test(location.pathname) ? location.href : ''",
        90, "écran « Finaliser la mission »",
    )
    nav.remplir("form input[name=name]", f"Mission {nom}")
    nav.cliquer_et_attendre("form:has(input[name=action][value=nommer]) button[type=submit]")
    assert re.search(r"/missions/\d+/?$", nav.url()), nav.url()
    assert f"Mission {nom}" in nav.texte()
    # La fiche de l'entretien depuis la mission.
    nav.cliquer_et_attendre("a[href^='/interviews/']")
    url = nav.url()
    assert re.search(r"/interviews/\d+/?$", url), url
    return url, texte


def _parler_et_attendre_le_tour_de_table(nav: Navigateur, nom: str, repartition_attendue: bool = True) -> str:
    """Sur l'écran d'enregistrement libre : nom, Démarrer, parler (faux micro),
    Arrêter, attendre transcription + répartition Q/R et le bouton
    « Enregistrer l'entretien ». Rend le texte transcrit."""
    assert nav.url().rstrip("/").endswith("/record-libre"), nav.url()
    assert not nav.evaluer("document.getElementById('rec-start').disabled"), (
        "Démarrer est désactivé : " + nav.evaluer("document.getElementById('rec-status').textContent")
    )
    nav.remplir("input[name=interviewee_name]", nom)

    nav.cliquer("#rec-start")
    _attendre(nav, "!document.getElementById('rec-stop').hidden", 30, "Démarrer (micro accordé)")
    time.sleep(_DUREE_ENREGISTREMENT_S)
    nav.cliquer("#rec-stop")

    texte = _attendre(
        nav, "document.getElementById('rec-transcript').value.trim()",
        _DELAI_TRANSCRIPTION_S, "transcription de la dernière tranche",
    )
    assert re.search(r"test|record|transcri|pipeline|hello", texte, re.I), (
        f"le texte transcrit ne ressemble pas au clip : {texte!r}"
    )
    if repartition_attendue:
        repartition = _attendre(
            nav, "document.getElementById('rec-repartition').innerText.trim()",
            90, "répartition Q/R (tours extraits par l'IA)",
        )
        assert "Extraction n°" in repartition, repartition
    _attendre(nav, "!document.getElementById('rec-submit').disabled", 60, "bouton Enregistrer l'entretien")
    _sans_erreur(nav, "Enregistrement libre à l'écran")
    return texte


def test_l_enregistrement_libre_transcrit_la_parole_et_s_enregistre(
    serveur: str, nav: Navigateur, faux_ollama: FauxOllama,
) -> None:
    """Point 1 et 2 de la demande : le micro est capté, la parole est
    transcrite à l'écran, la répartition Q/R apparaît, et l'entretien
    s'enregistre — avec sa transcription et son tour de table — dans une
    mission nommée."""
    url, texte = _enregistrer_un_entretien_libre(nav, serveur, "E2E Micro")
    page = nav.texte()
    assert "E2E Micro" in page
    # La transcription enregistrée est celle de l'écran (lecture seule, onglet
    # Transcription actif par défaut).
    transcrit = nav.evaluer("(document.querySelector('textarea.record-transcript')||{}).value||''")
    assert texte.split()[0] in transcrit, (texte, transcrit)
    # Le tour de table porte les tours rendus par l'IA — et la parole transcrite.
    nav.cliquer(".rec-tab[data-turntab=tours]")
    tours = _tour_de_table(nav)
    assert "Extraction n°1" in tours, tours
    assert "Pouvez-vous vous présenter ?" in tours and texte.split()[0] in tours, tours
    assert faux_ollama.extractions >= 1
    _sans_erreur(nav, "Fiche de l'entretien")


def test_le_tour_de_table_se_rejoue_autant_de_fois_que_necessaire(
    serveur: str, nav: Navigateur, faux_ollama: FauxOllama,
) -> None:
    """Point 3 : « ↻ Relancer la transcription (audio) » retranscrit l'audio
    persisté et ré-extrait le tour de table, deux fois de suite — chaque
    passage remplace le précédent par un nouveau calcul."""
    url, _ = _enregistrer_un_entretien_libre(nav, serveur, "E2E Rejeu")
    avant = faux_ollama.extractions
    for rejeu in (1, 2):
        nav.naviguer(url)
        nav.cliquer(".rec-tab[data-turntab=transcription]")
        # confirm() du bouton : accepté par le pilote, comme le ferait l'utilisateur.
        nav.cliquer_et_attendre("button[formaction$='/retranscrire']")
        assert nav.url().rstrip("/").endswith("/retranscrire"), nav.url()
        _attendre(
            nav, "!document.getElementById('retr-suite-form').hidden",
            _DELAI_TRANSCRIPTION_S, f"rejeu {rejeu} : retranscription et extraction terminées",
        )
        nav.cliquer_et_attendre("#retr-suite-form button[type=submit]")
        assert "/retranscrire" in nav.url(), nav.url()
        nav.cliquer_et_attendre("form[action$='/retranscrire/confirmer'] button.btn-primary")
        assert nav.url().rstrip("/") == url.rstrip("/"), nav.url()
        nav.cliquer(".rec-tab[data-turntab=tours]")
        tours = _tour_de_table(nav)
        attendu = avant + rejeu
        assert f"Extraction n°{attendu}" in tours, f"rejeu {rejeu} : {tours[:600]}"
        assert f"Extraction n°{attendu - 1}" not in tours, "l'ancien tour de table n'a pas été remplacé"
        _sans_erreur(nav, f"Rejeu {rejeu}")
    assert faux_ollama.extractions == avant + 2


def test_enregistrer_ouvre_la_fiche_tout_de_suite_puis_structurer_en_tache_de_fond(
    serveur: str, nav: Navigateur, faux_ollama: FauxOllama,
) -> None:
    """Structuration différée (2026-10-06) : « Enregistrer l'entretien » sur une
    mission NOMMÉE ouvre la fiche de l'entretien tout de suite, avec son badge
    de structuration — l'IA tourne ensuite en tâche de fond. Faux Ollama en
    panne d'extraction : la fiche passe en « échec » et propose « Relancer » ;
    Ollama rétabli, le clic Relancer structure l'entretien (badge « fait »,
    tour de table rempli). Jamais de vrai Ollama."""
    # Une mission nommée (premier entretien du parcours habituel).
    _enregistrer_un_entretien_libre(nav, serveur, "E2E Async 1")
    nav.naviguer(nav.url())
    mission_href = nav.evaluer(
        "document.querySelector('.breadcrumb a[href^=\"/missions/\"]:not([href=\"/missions\"])')"
        ".getAttribute('href')"
    )
    assert re.search(r"^/missions/\d+$", mission_href or ""), mission_href

    # Panne d'extraction dès l'enregistrement : ni la répartition à l'écran ni
    # la structuration de fond n'aboutissent — l'entretien s'enregistre quand
    # même, et sa fiche le dit.
    faux_ollama.panne = True
    try:
        nav.naviguer(serveur + mission_href + "/interviews/record-libre")
        _parler_et_attendre_le_tour_de_table(nav, "E2E Async 2", repartition_attendue=False)
        debut = time.monotonic()
        nav.cliquer_et_attendre("#rec-submit")
        assert re.search(r"/interviews/\d+/?(\?.*)?$", nav.url()), nav.url()
        assert time.monotonic() - debut < 30, "l'enregistrement a attendu l'IA"
        statut = nav.evaluer(
            "(document.getElementById('structuration-statut')||{dataset:{}}).dataset.statut||''"
        )
        assert statut in ("a_traiter", "en_cours", "echec"), statut
        assert nav.evaluer(
            "document.getElementById('structuration-statut').getAttribute('aria-live')"
        ) == "polite"
        # Le sondage HTMX recharge la fiche à l'arrivée sur l'état terminal.
        _attendre(
            nav,
            "(document.getElementById('structuration-statut')||{dataset:{}}).dataset.statut==='echec'",
            90, "badge « échec » (panne simulée)",
        )
        assert "Relancer" in nav.texte()
    finally:
        faux_ollama.panne = False

    avant = faux_ollama.extractions
    nav.cliquer_et_attendre("form[action$='/structurer'] button[type=submit]")
    _attendre(
        nav,
        "(document.getElementById('structuration-statut')||{dataset:{}}).dataset.statut==='fait'",
        90, "badge « fait » après Relancer",
    )
    assert faux_ollama.extractions > avant
    nav.cliquer(".rec-tab[data-turntab=tours]")
    assert "Extraction n°" in _tour_de_table(nav)
    assert not nav.evaluer(
        "!!document.querySelector('#structuration-statut[hx-trigger]')"
    ), "le sondage continue sur un état terminal"
    _sans_erreur(nav, "Structuration différée")