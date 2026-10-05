"""Pilote d'un navigateur Chromium RÉEL (Edge ou Chrome, headless) via le
Chrome DevTools Protocol — la couche « test utilisateur » du projet.

Pourquoi un vrai navigateur : le 2026-09-08 le site a été mis en service avec
un 403 « Origine non autorisée » sur Supprimer et Démarrer, dès les premiers
clics. La suite était verte : `TestClient` reçoit un `Origin` injecté par le
conftest, et aucun test ne rejouait une soumission de formulaire telle qu'un
navigateur l'envoie (sous `Referrer-Policy: no-referrer`, Chromium met
`Origin: null` sur les POST de formulaire — invisible sans navigateur).

Sans Playwright ni Selenium dans le venv (choix du projet, cf. skill
`run-dev-server`) : Edge/Chrome headless piloté par CDP avec le client
`websockets` que `uvicorn[standard]` installe déjà. Rien à ajouter aux
dépendances.

Quatre leçons payées en mise au point (revues adversariales des 2026-09-08 et
2026-09-10, chacune suivie d'une reproduction), qui expliquent la forme du code :

- **Le processus lancé n'est pas le navigateur.** Sur Windows, `msedge.exe`
  se relance lui-même et le lanceur sort en ~1,5 s avec le code 0 : un
  `Popen.kill()` ne tue qu'un lanceur déjà mort. Chaque run laissait un
  navigateur entier derrière lui — 51 processus orphelins mesurés après une
  journée, et les lancements suivants ne répondaient plus (« CDP
  injoignable »). D'où : port `0` + fichier `DevToolsActivePort` écrit par le
  navigateur (on s'attache à CELUI qu'on a lancé, jamais à un autre qui
  répondrait sur un port deviné), `Browser.close` par CDP, puis l'arbre de
  processus du VRAI pid, obtenu par `SystemInfo.getProcessInfo`.
- **Un clic est un clic.** `element.click()` passe sur un élément invisible,
  recouvert ou hors écran ; un utilisateur non. Le clic part par
  `Input.dispatchMouseEvent` aux coordonnées de l'élément, après avoir
  vérifié que c'est bien lui qu'on touche à ce point.
- **Les évènements n'arrivent que quand quelqu'un lit la socket.** Réponses
  réseau et exceptions JS ne sont comptées que pendant une lecture :
  `drainer()` avant toute assertion « aucune erreur sur le parcours ».
- **Une session CDP muette n'est pas une session morte, et l'inverse.** Le
  keepalive de `websockets` est coupé (`_OPTIONS_WS`) : mesuré meilleur, sans
  que la cause de la panne qu'il corrige soit établie — la constante dit
  exactement ce qui est mesuré et ce qui ne l'est pas, parce qu'une première
  version affirmait un mécanisme que la bibliothèque dément. Conséquence sur
  la forme du code : `_run` traite MAINTENANT deux échecs distincts, la
  connexion fermée et le délai expiré, et les enrichit tous deux du même
  `_etat_navigateur()` — sans quoi couper le keepalive troquait un message
  diagnosticable contre un `TimeoutError` nu.

API synchrone minimale, pensée pour des tests qui lisent comme un parcours :

    nav = Navigateur(executable, profil)
    nav.naviguer(url)                     # attend le chargement
    nav.remplir("input[name=name]", "x")
    nav.cliquer_et_attendre("button[type=submit]")   # clic réel + navigation
    nav.cliquer("[data-tab=entretiens]")             # clic réel sans navigation
    nav.url(), nav.texte(), nav.erreurs(), nav.exceptions
    nav.fermer()

Les `confirm()` sont acceptés automatiquement (c'est ce que fait l'utilisateur
qui clique Supprimer).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

_CANDIDATS_WINDOWS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
_CANDIDATS_PATH = [
    "microsoft-edge", "microsoft-edge-stable", "google-chrome",
    "google-chrome-stable", "chromium", "chromium-browser", "msedge",
]
_TAILLE_MAX_MESSAGE = 50 * 1024 * 1024
# Requêtes lancées par le navigateur lui-même, que l'app ne sert pas : un
# 404 attendu, pas un bug de fonctionnement (à retirer le jour où un favicon
# est servi).
_CHEMINS_NAVIGATEUR = {"/favicon.ico"}
# Mesuré le 2026-09-08 sur Edge 152 : avec un `--user-data-dir` de 178 ou
# 188 caractères, le navigateur sort en 1,6 s avec le code 0, journal VIDE,
# sans jamais écrire DevToolsActivePort ; à 140 il démarre, à 56 en 3 s. Le
# `tmp_path` de pytest sur ce poste fait 178 caractères — d'où la fixture qui
# crée un profil court sous le temporaire système. On refuse ici plutôt que
# d'attendre 30 s pour rendre « CDP injoignable » sans cause.
_LONGUEUR_MAX_PROFIL = 140
# Démarrage : un profil neuf coûte plus qu'une commande. Mesuré le 2026-09-08
# sur le même poste, avec les mêmes drapeaux : 1,6 s à 3 s la plupart du
# temps, et plus de 30 s par moments — le temps que le premier lancement
# d'Edge installe ses extensions intégrées (cinq pages de fond : Microsoft
# Voices…), et tente ses réseaux de fond. D'où deux mesures : les drapeaux
# ci-dessous (ceux de Puppeteer : rien à installer, rien à contacter) et un
# délai de démarrage distinct, plus long, du délai des commandes.
_DELAI_DEMARRAGE_S = 60.0
# Options communes aux DEUX sessions CDP (navigateur et page) — en constante
# pour qu'une troisième connexion ne puisse pas les oublier.
#
# `ping_interval=None` (2026-09-10) coupe le keepalive de `websockets` (16.0
# ici : ping toutes les 20 s, connexion fermée si le pong tarde 20 s de plus).
# CE QUI EST MESURÉ, et rien de plus : sur le code d'avant,
# `test_le_tour_de_table_se_rejoue_autant_de_fois_que_necessaire` a échoué 2
# fois sur 2 en `ConnectionClosedError: no close frame received or sent`
# (suite complète 1358 s ; fichier seul 615 s), navigateur TOUJOURS VIVANT et
# journal vide ; avec ce réglage le même fichier rend « 2 passed in 96,68 s ».
#
# CE QUI N'EST PAS PROUVÉ — écrit ici parce qu'une première version de ce
# commentaire l'affirmait à tort, et qu'une revue adversariale l'a démenti sur
# le code de la bibliothèque : le MÉCANISME reste inconnu. Un dépassement de
# keepalive poserait `close_sent` (`asyncio/connection.py`, `protocol.fail(1011,
# "keepalive ping timeout")`) et le message serait « sent 1011 … keepalive ping
# timeout » ; « no close frame received OR SENT » n'apparaît que si aucun des
# deux côtés n'a envoyé de close, donc sur une coupure brutale du transport.
# Deux pistes restent ouvertes, non tranchées : (1) l'échec durait PLUS
# longtemps que le succès (615 s contre 97 s), ce qu'un échec-rapide de socket
# n'explique pas — un blocage applicatif épuisant les budgets de polling
# (`_DELAI_TRANSCRIPTION_S`) le ferait ; (2) `max_queue=16` met le transport en
# pause (`pause_reading`) quand personne ne draine, ce qui est le cas pendant
# les `time.sleep` du test et entre deux commandes.
# Le réglage est donc conservé parce qu'il est EMPIRIQUEMENT meilleur (et sans
# risque : voir `_run`, où chaque commande reste bornée par `self._delai`,
# 30 s, soit moins que les 40 s du keepalive — celui-ci n'a jamais été le
# premier détecteur), pas parce que la cause serait comprise. Trancher
# demanderait un run avec `logging.getLogger("websockets")` en DEBUG.
_OPTIONS_WS = {"max_size": _TAILLE_MAX_MESSAGE, "ping_interval": None}
_DRAPEAUX_LANCEMENT = [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
    "--window-size=1280,1600",
    # Rien à installer ni à contacter au premier lancement d'un profil neuf.
    "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    "--disable-component-update", "--disable-background-networking",
    "--disable-sync", "--disable-default-apps", "--metrics-recording-only",
    "--mute-audio", "--password-store=basic", "--use-mock-keychain",
    # Lancé depuis un processus ÉLEVÉ (VS Code ouvert en administrateur, donc
    # Claude Code, donc pytest), Edge refuse de tourner élevé et se relance
    # dé-élevé via le shell — ce qui marche tant que la session Windows est
    # déverrouillée, et meurt en silence dès qu'elle est verrouillée (mesuré
    # le 2026-09-08 : « Edge is running elevated: 1 » → « RunDeElevated:
    # Started process » → fils absent, LogonUI présent). Ce drapeau garde le
    # navigateur dans notre processus, verrouillé ou non ; sans élévation il
    # est sans effet.
    "--do-not-de-elevate",
    # Le navigateur choisit son port et l'écrit dans DevToolsActivePort.
    "--remote-debugging-port=0",
]


def trouver_navigateur() -> str | None:
    """Chemin d'un Chromium utilisable, ou None. `E2E_NAVIGATEUR` force le
    choix (CI, poste sans Edge) — et un chemin forcé qui n'existe pas est une
    ERREUR, pas un skip : une faute de frappe dans la CI rendrait la suite
    verte sans jamais jouer le parcours."""
    force = os.environ.get("E2E_NAVIGATEUR")
    if force:
        if not Path(force).exists():
            raise RuntimeError(f"E2E_NAVIGATEUR pointe sur un fichier absent : {force}")
        return force
    for chemin in _CANDIDATS_WINDOWS:
        if Path(chemin).exists():
            return chemin
    for nom in _CANDIDATS_PATH:
        trouve = shutil.which(nom)
        if trouve:
            return trouve
    return None


def port_libre() -> int:
    """Un port TCP libre à l'instant de l'appel (pour uvicorn). Le navigateur,
    lui, n'en a pas besoin : il choisit le sien (`--remote-debugging-port=0`)
    et l'écrit dans `DevToolsActivePort`."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _attendre_fin_processus(pid: int, delai_s: float) -> bool:
    """Vrai si le processus `pid` est terminé avant `delai_s`."""
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return True  # déjà parti
        try:
            return kernel32.WaitForSingleObject(handle, int(delai_s * 1000)) == 0
        finally:
            kernel32.CloseHandle(handle)
    limite = time.monotonic() + delai_s
    while time.monotonic() < limite:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(0.1)
    return False


def _tuer_arbre(pid: int) -> None:
    """Dernier recours : l'arbre entier du navigateur (renderers, GPU,
    crashpad), pas seulement le pid."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        return
    import signal

    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _tuer_par_profil(profil: Path) -> None:
    """Arrête tout processus du navigateur dont la ligne de commande cite ce
    profil (le navigateur relancé et ses enfants la portent tous)."""
    motif = str(profil)
    if sys.platform == "win32":
        # `$_.ProcessId -ne $PID` : le motif figure dans la ligne de commande du
        # powershell qui filtre — sans cette clause il se tuait lui-même au
        # milieu du pipeline, et la survie d'un enfant dépendait de l'ordre
        # d'énumération (revue du 2026-09-09, A4).
        script = (
            "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID "
            f"-and $_.CommandLine -like '*{motif.replace(chr(39), chr(39) * 2)}*' }} "
            "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        return
    subprocess.run(
        ["pkill", "-9", "-f", motif],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )


_ATTENTE_RELANCE_S = 1.0  # pause avant l'unique relance (<= 2 s)


class Navigateur:
    """Une session de navigateur headless pilotée par CDP (un seul onglet)."""

    def __init__(self, executable: str, profil: Path, delai_s: float = 30.0):
        import websockets  # fourni par uvicorn[standard]

        self.profil = profil
        self.reponses: list[dict] = []        # {"status", "url", "type"}
        self.echecs_reseau: list[dict] = []   # {"url", "erreur", "type"} (loadingFailed)
        self.exceptions: list[str] = []       # descriptions Runtime.exceptionThrown
        self.erreurs_cdp: list[str] = []      # réponses en erreur non attendues
        self.pid_navigateur: int | None = None
        self._urls_requetes: dict[str, str] = {}
        self._chargements = 0
        self._id = 0
        self._delai = delai_s
        self._proc: subprocess.Popen | None = None
        self._journal = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None              # session de la page
        self._ws_navigateur = None   # session du navigateur (pid, fermeture)
        if len(str(profil)) > _LONGUEUR_MAX_PROFIL:
            raise ValueError(
                f"chemin de profil trop long ({len(str(profil))} car., max "
                f"{_LONGUEUR_MAX_PROFIL}) : Edge sortirait sans rien dire — "
                f"utiliser un dossier court sous le temporaire système : {profil}"
            )
        profil.mkdir(parents=True, exist_ok=True)
        (profil / "DevToolsActivePort").unlink(missing_ok=True)
        # Une seule relance (décision du 2026-10-05) : le handshake websocket CDP
        # expire par intermittence en CI (run 37053848441). Pas de boucle.
        try:
            self._lancer_et_connecter(executable, profil, websockets)
            return
        except FileNotFoundError:
            raise  # exécutable absent : une relance n'y changerait rien
        except Exception as premiere:
            premier_echec = (
                f"{type(premiere).__name__}: {premiere}\n"
                + self._journal_navigateur()[-2000:]
            )
        # `fermer()` tue l'arbre du premier navigateur (pid, profil, lanceur)
        # AVANT la relance : aucun orphelin.
        self.fermer()
        self._proc = None
        self.pid_navigateur = None
        time.sleep(_ATTENTE_RELANCE_S)
        (profil / "DevToolsActivePort").unlink(missing_ok=True)
        try:
            self._lancer_et_connecter(executable, profil, websockets)
        except Exception as seconde:
            raise RuntimeError(
                f"{type(seconde).__name__}: {seconde}\n"
                "--- journal du PREMIER échec (une relance a été tentée) ---\n"
                + premier_echec
            ) from seconde

    def _lancer_et_connecter(self, executable, profil: Path, websockets) -> None:
        # Tout ce qui suit peut échouer APRÈS le lancement : on ferme alors ce
        # qui a été ouvert avant de relever l'erreur, sinon le navigateur survit
        # au test (et la fixture, qui n'a pas encore l'objet, ne peut rien).
        try:
            self._journal = open(
                profil / "navigateur.log", "w", encoding="utf-8", errors="replace"
            )
            self._proc = subprocess.Popen(
                [executable, *_DRAPEAUX_LANCEMENT, f"--user-data-dir={profil}", "about:blank"],
                stdout=self._journal, stderr=subprocess.STDOUT,
            )
            ws_navigateur, port = self._attendre_devtools()
            self._loop = asyncio.new_event_loop()
            self._ws_navigateur = self._run(
                websockets.connect(ws_navigateur, **_OPTIONS_WS)
            )
            info = self._run(self._cmd_sur(self._ws_navigateur, "SystemInfo.getProcessInfo"))
            for processus in info.get("processInfo", []):
                if processus.get("type") == "browser":
                    self.pid_navigateur = int(processus["id"])
            self._ws = self._run(
                websockets.connect(self._cible_page(port), **_OPTIONS_WS)
            )
            self._run(self._cmd("Network.enable"))
            # Credentials de l'app (app/auth.py, defaut ferme depuis le
            # 2026-09-19) : sans en-tete, chaque navigation du parcours e2e
            # tomberait sur la page de connexion en 401. On passe par le meme
            # Bearer que les TestClient plutot que de rejouer le formulaire de
            # connexion a chaque scenario.
            _secret = os.environ.get("APP_AUTH_PASSWORD")
            if _secret:
                self._run(self._cmd(
                    "Network.setExtraHTTPHeaders",
                    headers={"Authorization": "Bearer " + _secret},
                ))
            self._run(self._cmd("Page.enable"))
            self._run(self._cmd("Runtime.enable"))
        except BaseException:
            self.fermer()
            raise

    # --- cycle de vie -------------------------------------------------------
    def _journal_navigateur(self) -> str:
        try:
            return (self.profil / "navigateur.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _attendre_devtools(self) -> tuple[str, int]:
        """Le navigateur écrit `DevToolsActivePort` (port choisi + chemin de la
        session navigateur) dans SON profil : c'est la seule façon d'être sûr
        de s'attacher au processus qu'on vient de lancer."""
        fichier = self.profil / "DevToolsActivePort"
        debut = time.monotonic()
        while time.monotonic() - debut < _DELAI_DEMARRAGE_S:
            try:
                lignes = fichier.read_text(encoding="utf-8").split()
                if len(lignes) >= 2 and lignes[0].isdigit():
                    port = int(lignes[0])
                    self.duree_demarrage_s = time.monotonic() - debut
                    return f"ws://127.0.0.1:{port}{lignes[1]}", port
            except OSError:
                pass
            time.sleep(0.1)
        code = self._proc.poll() if self._proc is not None else None
        etat = "toujours actif" if code is None else f"sorti avec le code {code}"
        raise RuntimeError(
            "CDP injoignable : le navigateur n'a pas écrit DevToolsActivePort en "
            f"{_DELAI_DEMARRAGE_S:.0f}s (lanceur : {etat}).\n" + self._journal_navigateur()[-2000:]
        )

    def _cible_page(self, port: int) -> str:
        limite = time.monotonic() + self._delai
        while time.monotonic() < limite:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json", timeout=2
                ) as r:
                    pages = [c for c in json.load(r) if c.get("type") == "page"]
                # L'onglet qu'on a demandé (about:blank) d'abord ; à défaut le
                # premier — jamais une page d'extension ou de bienvenue.
                for cible in sorted(pages, key=lambda c: c.get("url") != "about:blank"):
                    return cible["webSocketDebuggerUrl"]
            except Exception:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"aucun onglet exposé par le navigateur sur le port {port}")

    def fermer(self) -> None:
        loop = self._loop
        # 1. Fermeture propre : Browser.close arrête l'arbre entier.
        if loop is not None and self._ws_navigateur is not None:
            try:
                loop.run_until_complete(
                    asyncio.wait_for(self._cmd_sur(self._ws_navigateur, "Browser.close"), 5)
                )
            except Exception:
                pass
        for ws in (self._ws, self._ws_navigateur):
            if loop is not None and ws is not None:
                try:
                    loop.run_until_complete(asyncio.wait_for(ws.close(), 5))
                except Exception:
                    pass
        self._ws = self._ws_navigateur = None
        # 2. Le VRAI pid : on lui laisse le temps, puis on force s'il traîne.
        if self.pid_navigateur and not _attendre_fin_processus(self.pid_navigateur, 5.0):
            _tuer_arbre(self.pid_navigateur)
        # 2 bis. Par le profil, toujours : un enfant (crashpad, renderer) peut
        # survivre au navigateur, et si celui-ci n'a jamais répondu (démarré
        # trop tard, pid inconnu) c'est le seul lien certain entre ce test et
        # ses processus. Sans effet quand il ne reste rien.
        _tuer_par_profil(self.profil)
        # 3. Le lanceur (d'ordinaire déjà sorti).
        if self._proc is not None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=10)
            except Exception:
                pass
        if loop is not None:
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
        if self._journal is not None:
            try:
                self._journal.close()
            except Exception:
                pass
            self._journal = None

    # --- plomberie CDP ------------------------------------------------------
    def _run(self, coro):
        import websockets

        try:
            return self._loop.run_until_complete(asyncio.wait_for(coro, self._delai))
        except websockets.exceptions.ConnectionClosed as exc:
            # La socket CDP s'est fermée : le navigateur est parti (crash, tué)
            # ou a fermé l'onglet. Dire lequel, avec son journal — sans ça il ne
            # reste que « no close frame received » (suite du 2026-09-09).
            raise RuntimeError(
                f"connexion CDP fermée ({exc}) — " + self._etat_navigateur()
            ) from exc
        except TimeoutError as exc:
            # Socket OUVERTE mais plus personne au bout : le navigateur est figé
            # (renderer bloqué) ou n'a jamais répondu à cette commande. Le même
            # enrichissement que ci-dessus, sinon il ne reste qu'un TimeoutError
            # nu sans pid ni journal — c'est-à-dire exactement le diagnostic
            # gagné le 2026-09-09, reperdu le 2026-09-10 par `ping_interval=
            # None` : sans keepalive, ce cas ne se présente PLUS comme une
            # fermeture de connexion mais comme une expiration de délai (revue
            # adversariale du 2026-09-10, R2). Le budget est le même qu'avant
            # (`self._delai`, 30 s, déjà plus court que les 40 s du keepalive) :
            # on ne perd pas la borne, on récupère le message.
            raise RuntimeError(
                f"CDP muet en {self._delai:.0f}s — " + self._etat_navigateur()
            ) from exc

    def _etat_navigateur(self) -> str:
        """Pid, vivacité et fin du journal du navigateur — la matière qui rend
        un échec CDP diagnosticable au lieu de laisser une exception nue."""
        vivant = (
            self.pid_navigateur is not None
            and not _attendre_fin_processus(self.pid_navigateur, 0.0)
        )
        return (
            f"navigateur pid {self.pid_navigateur} "
            f"{'toujours vivant' if vivant else 'terminé'} ; journal :\n"
            + self._journal_navigateur()[-2000:]
        )

    async def _envoyer(self, method: str, **params) -> int:
        self._id += 1
        await self._ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        return self._id

    async def _cmd_sur(self, ws, method: str, **params) -> dict:
        """Commande sur une session dont on n'écoute pas les évènements
        (session navigateur)."""
        self._id += 1
        ident = self._id
        await ws.send(json.dumps({"id": ident, "method": method, "params": params}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == ident:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} : {msg['error']}")
                return msg.get("result", {})

    async def _traiter(self, msg: dict) -> None:
        methode = msg.get("method")
        if methode is None:
            # Réponse à un envoi sans attente (handleJavaScriptDialog) : un
            # refus ici est un défaut du pilote, pas du site — on le garde.
            if "error" in msg:
                self.erreurs_cdp.append(str(msg["error"]))
            return
        params = msg.get("params", {})
        if methode == "Network.requestWillBeSent":
            self._urls_requetes[params.get("requestId", "")] = (
                params.get("request", {}).get("url", "")
            )
        elif methode == "Network.responseReceived":
            rep = params["response"]
            self.reponses.append({
                "status": rep["status"], "url": rep["url"], "type": params.get("type"),
            })
        elif methode == "Network.loadingFailed":
            # Une ressource jamais reçue (bloquée par la CSP, connexion
            # refusée) n'a pas de statut HTTP : sans ce cas, elle passerait
            # pour « aucune erreur ». Les annulations (navigation qui
            # interrompt un chargement) ne sont pas des échecs.
            url = self._urls_requetes.get(params.get("requestId", ""), "?")
            if not params.get("canceled") and urlsplit(url).path not in _CHEMINS_NAVIGATEUR:
                self.echecs_reseau.append({
                    "url": url, "erreur": params.get("errorText"), "type": params.get("type"),
                })
        elif methode == "Runtime.exceptionThrown":
            details = params["exceptionDetails"]
            self.exceptions.append(
                details.get("exception", {}).get("description")
                or details.get("text", "exception JS")
            )
        elif methode == "Page.loadEventFired":
            self._chargements += 1
        elif methode == "Page.javascriptDialogOpening":
            # confirm()/alert() : l'utilisateur qui clique Supprimer confirme.
            await self._envoyer("Page.handleJavaScriptDialog", accept=True)

    async def _cmd(self, method: str, **params) -> dict:
        ident = await self._envoyer(method, **params)
        while True:
            msg = json.loads(await self._ws.recv())
            if msg.get("id") == ident:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} : {msg['error']}")
                return msg.get("result", {})
            await self._traiter(msg)

    async def _attendre_chargement(self, avant: int) -> None:
        while self._chargements <= avant:
            msg = json.loads(await self._ws.recv())
            await self._traiter(msg)
        # Les scripts de la page ont tourné avant `load` (inline et différés) ;
        # on ramasse seulement ce qui est déjà arrivé sur la socket.
        await self._drainer()

    async def _drainer(self) -> None:
        """Consomme les évènements déjà arrivés, jusqu'à 0,2 s de silence."""
        while True:
            try:
                msg = json.loads(await asyncio.wait_for(self._ws.recv(), 0.2))
            except TimeoutError:
                return
            await self._traiter(msg)

    # --- API de parcours ----------------------------------------------------
    def drainer(self) -> None:
        """À appeler avant toute assertion sur `reponses`/`exceptions` : ces
        listes ne se remplissent que pendant une lecture de la socket."""
        self._run(self._drainer())

    def capturer(self, chemin: Path | str, *, page_entiere: bool = True) -> Path:
        """Capture PNG de la page courante, rendue par CE navigateur.

        C'est le seul moyen de VOIR un écran de l'app depuis que
        l'authentification est fermée par défaut (2026-09-19) : le mode
        `--screenshot` d'Edge ouvre une session neuve et ne sait porter ni
        cookie ni en-tête, il ne ramène donc que la page de connexion. Ici la
        session est déjà authentifiée (Bearer posé à l'init).
        """
        res = self._run(self._cmd(
            "Page.captureScreenshot", format="png", captureBeyondViewport=page_entiere))
        if "data" not in res:
            # Sans ce garde, une commande CDP en echec sortait en KeyError nu —
            # illisible dans le diagnostic d'un e2e casse (revue 2026-09-28).
            raise RuntimeError(f"capture d'ecran refusee par le navigateur : {res}")
        chemin = Path(chemin)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_bytes(base64.b64decode(res["data"]))
        return chemin

    def poser_entetes(self, entetes: dict[str, str]) -> None:
        """REMPLACE les en-têtes supplémentaires de la session ({} les retire
        tous). Sert au parcours de connexion : le harnais pose un Bearer à
        l'init (voir __init__), or prouver que le FORMULAIRE connecte exige un
        navigateur anonyme — avec le Bearer, la page s'ouvrirait même si le
        formulaire était cassé."""
        self._run(self._cmd("Network.setExtraHTTPHeaders", headers=entetes))

    def naviguer(self, url: str) -> None:
        avant = self._chargements
        res = self._run(self._cmd("Page.navigate", url=url))
        if res.get("errorText"):
            raise RuntimeError(f"navigation vers {url} : {res['errorText']}")
        self._run(self._attendre_chargement(avant))

    def evaluer(self, expression: str):
        res = self._run(self._cmd(
            "Runtime.evaluate", expression=expression, returnByValue=True,
            awaitPromise=True,
        ))
        if "exceptionDetails" in res:
            raise RuntimeError(
                f"JS : {res['exceptionDetails'].get('text')} — {expression[:120]}"
            )
        resultat = res.get("result", {})
        if resultat.get("type") == "undefined":
            # Sinon `url()`/`texte()` rendraient None et l'assertion du test
            # casserait trois lignes plus loin sur un TypeError sans rapport.
            raise RuntimeError(f"évaluation sans valeur (undefined) : {expression[:120]}")
        self._run(self._drainer())
        return resultat.get("value")

    def remplir(self, selecteur: str, valeur: str) -> None:
        """Pose une valeur dans un champ et dispatche `input` puis `change`.

        PIÈGE À CONNAÎTRE (2026-09-27) : seuls ces deux événements sont émis,
        JAMAIS `keyup`. Or plusieurs autosaves htmx du dépôt écoutent
        `hx-trigger="keyup changed delay:700ms, blur"` SANS `change` — les macros
        de la synthèse globale (`templates/synthese/_global_panel.html`) et les
        quadrants SWOT / champs de l'aperçu (`templates/synthese/apercu.html`).
        Sur ces champs, `remplir` n'envoie aucune requête : le test reste VERT en
        n'ayant rien enregistré, et prouve donc l'inverse de ce qu'il annonce.

        Pour ces champs, passer par `_saisir_au_clavier` de
        `tests/test_e2e_premiers_clics.py`, qui ajoute le `keyup` final — ou
        vérifier soi-même le `hx-trigger` du champ visé avant d'appeler ceci.
        """
        ok = self.evaluer(
            "(function(){var e=document.querySelector(" + json.dumps(selecteur) + ");"
            " if(!e) return false; e.value=" + json.dumps(valeur) + ";"
            " e.dispatchEvent(new Event('input',{bubbles:true}));"
            " e.dispatchEvent(new Event('change',{bubbles:true})); return true;})()"
        )
        assert ok, f"champ introuvable : {selecteur}"

    def choisir_fichier(self, selecteur: str, chemin: Path) -> None:
        """Pose un fichier sur un `<input type=file>` comme le sélecteur natif
        (DOM.setFileInputFiles) — la boîte de dialogue système n'existe pas en
        headless ; la soumission, elle, reste un clic réel."""
        racine = self._run(self._cmd("DOM.getDocument", depth=0))["root"]["nodeId"]
        noeud = self._run(self._cmd("DOM.querySelector", nodeId=racine, selector=selecteur))
        assert noeud.get("nodeId"), f"champ fichier introuvable : {selecteur}"
        self._run(self._cmd("DOM.setFileInputFiles", files=[str(chemin)],
                            nodeId=noeud["nodeId"]))
        self.drainer()

    def _point_de_clic(self, selecteur: str) -> tuple[float, float]:
        """Centre de l'élément après l'avoir amené à l'écran — et la preuve
        que c'est bien lui (ou un de ses descendants) qui est sous ce point.
        Un bouton masqué par un onglet inactif ou recouvert par un bandeau
        n'est pas cliquable par un utilisateur : le test doit le dire, pas le
        contourner."""
        etat = self.evaluer(
            "(function(){var e=document.querySelector(" + json.dumps(selecteur) + ");"
            " if(!e) return null;"
            " e.scrollIntoView({block:'center', inline:'center'});"
            " var r=e.getBoundingClientRect();"
            " var x=r.left+r.width/2, y=r.top+r.height/2;"
            " var t=document.elementFromPoint(x, y);"
            " return {x:x, y:y, w:r.width, h:r.height,"
            "  dessus: !!(t && (t===e || e.contains(t)))};})()"
        )
        assert etat is not None, f"élément introuvable : {selecteur}"
        assert etat["w"] > 0 and etat["h"] > 0, (
            f"élément invisible (taille nulle) : {selecteur}"
        )
        assert etat["dessus"], (
            f"élément recouvert ou hors écran, un utilisateur ne peut pas le cliquer : {selecteur}"
        )
        return etat["x"], etat["y"]

    def _clic_souris(self, x: float, y: float) -> None:
        self._run(self._cmd("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y))
        for genre in ("mousePressed", "mouseReleased"):
            self._run(self._cmd(
                "Input.dispatchMouseEvent", type=genre, x=x, y=y,
                button="left", clickCount=1,
            ))

    def cliquer(self, selecteur: str) -> None:
        """Clic réel (souris, aux coordonnées de l'élément) SANS attendre de
        navigation — onglet, bouton JS."""
        x, y = self._point_de_clic(selecteur)
        self._clic_souris(x, y)
        self.drainer()

    def cliquer_et_attendre(self, selecteur: str) -> None:
        """Clic réel puis attente de la navigation qu'il déclenche —
        soumission de formulaire, lien."""
        avant = self._chargements
        x, y = self._point_de_clic(selecteur)
        self._clic_souris(x, y)
        try:
            self._run(self._attendre_chargement(avant))
        except TimeoutError:
            raise AssertionError(
                f"aucune navigation en {self._delai:.0f}s après le clic sur {selecteur}"
            ) from None

    def url(self) -> str:
        return self.evaluer("location.href")

    def texte(self) -> str:
        return self.evaluer("document.body ? document.body.innerText : ''")

    def erreurs_http(self) -> list[dict]:
        """Réponses 4xx/5xx du parcours, hors requêtes propres au navigateur."""
        return [
            r for r in self.reponses
            if r["status"] >= 400 and urlsplit(r["url"]).path not in _CHEMINS_NAVIGATEUR
        ]

    def erreurs(self) -> list[dict]:
        """Tout ce qui, côté réseau, n'a pas abouti : 4xx/5xx ET ressources
        jamais reçues. Appeler `drainer()` avant pour lire l'état à jour."""
        return self.erreurs_http() + list(self.echecs_reseau)
