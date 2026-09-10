"""Test utilisateur : les PREMIERS CLICS dans un vrai navigateur, contre un
vrai serveur.

Demande utilisateur du 2026-09-08 (« met plus de test utilisateur car le site
est lancé alors qu'il y a de gros bugs de fonctionnement dès les premiers
clics ») : le site venait d'être livré avec un 403 « Origine non autorisée »
sur Supprimer un entretien et Démarrer, alors que 760 tests étaient verts.
Cause : `Referrer-Policy: no-referrer` fait envoyer `Origin: null` par
Chromium sur les POST de formulaire — un comportement de NAVIGATEUR, que ni
`TestClient` (Origin injecté par le conftest) ni un test unitaire ne voient.

Ce module rejoue donc le parcours qu'un consultant fait en arrivant, depuis
la toute première page : choisir le mode, démarrer un entretien (structuré,
puis libre), créer une mission, y ajouter un entretien, le supprimer, ouvrir
l'écran d'enregistrement libre, supprimer la mission — chaque étape par un
CLIC réel (souris, aux coordonnées du bouton, qui doit être visible et non
recouvert), et à chaque étape aucune réponse 4xx/5xx, aucune ressource
perdue ni exception JS depuis le début du parcours.

Échec sur le code d'avant, vérifié le 2026-09-08 en remettant `no-referrer`
(et re-prouvé par la revue du 2026-09-09 via un `sitecustomize` hors dépôt) :
les trois tests reçoivent le 403 dès leur première soumission de formulaire.

Le serveur est un vrai uvicorn (`tests/e2e_serveur.py`) ; le navigateur est
Edge ou Chrome headless (`tests/navigateur_cdp.py`). Sans navigateur Chromium
sur la machine, le module est sauté avec sa raison — sauf `E2E_OBLIGATOIRE=1`
(posé par la CI, où le navigateur est présent) : alors l'absence de
navigateur, comme celle de `websockets`, est une ERREUR, pas un skip — une
suite verte qui n'a pas joué le parcours est exactement le trou que ce module
existe pour fermer.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_OBLIGATOIRE = bool(os.environ.get("E2E_OBLIGATOIRE"))
try:
    import websockets  # noqa: F401 — fourni par uvicorn[standard]
except ModuleNotFoundError:
    # La garde d'abord (revue du 2026-09-09, A3) : un `importorskip` placé
    # avant elle sautait le module en silence même sous E2E_OBLIGATOIRE.
    if _OBLIGATOIRE:
        raise RuntimeError(
            "E2E_OBLIGATOIRE est posé mais `websockets` (uvicorn[standard]) est absent : "
            "le parcours utilisateur ne peut pas être joué"
        ) from None
    pytest.skip("websockets (uvicorn[standard]) requis", allow_module_level=True)

from e2e_serveur import serveur_uvicorn  # noqa: E402
from navigateur_cdp import Navigateur, trouver_navigateur  # noqa: E402

_NAVIGATEUR = trouver_navigateur()

if _NAVIGATEUR is None and _OBLIGATOIRE:
    raise RuntimeError(
        "E2E_OBLIGATOIRE est posé mais aucun navigateur Chromium (Edge/Chrome) n'est "
        "trouvé : le parcours utilisateur ne peut pas être joué — E2E_NAVIGATEUR pour "
        "en désigner un"
    )

pytestmark = pytest.mark.skipif(
    _NAVIGATEUR is None,
    reason="aucun navigateur Chromium (Edge/Chrome) trouvé — E2E_NAVIGATEUR pour en forcer un",
)


@pytest.fixture(scope="module")
def serveur(tmp_path_factory: pytest.TempPathFactory):
    """Un vrai uvicorn sur un port libre, base temporaire, tué à la fin."""
    with serveur_uvicorn(tmp_path_factory.mktemp("e2e")) as base:
        yield base


@pytest.fixture
def nav():
    """Le profil du navigateur va sous le temporaire SYSTÈME, pas sous
    `tmp_path` : au-delà de ~150 caractères de chemin, Edge sort sans un mot
    (mesuré le 2026-09-08, `tmp_path` faisait 178 caractères sur ce poste —
    cf. `navigateur_cdp._LONGUEUR_MAX_PROFIL`). Le dossier est créé AVANT le
    `try` et supprimé dans le `finally`, que le constructeur réussisse ou non."""
    profil = Path(tempfile.mkdtemp(prefix="e2e-nav-"))
    navigateur = None
    try:
        navigateur = Navigateur(_NAVIGATEUR, profil / "profil")
        yield navigateur
    finally:
        if navigateur is not None:
            navigateur.fermer()
        shutil.rmtree(profil, ignore_errors=True)  # verrous Windows : au pire il reste


def _creer_mission(nav: Navigateur, base: str, nom: str) -> str:
    nav.naviguer(base + "/missions/new")
    nav.remplir("input[name=name]", nom)
    nav.cliquer_et_attendre("form[action='/missions'] button[type=submit]")
    url = nav.url()
    assert url.rstrip("/").split("/")[-1].isdigit(), f"pas arrivé sur la mission : {url}"
    assert nom in nav.texte()
    # « + Entretien structuré » n'apparaît qu'une fois la trame dotée d'un
    # thème : c'est aussi le vrai premier clic d'un consultant (Éditer la
    # trame → + Thème), et un formulaire de plus sur le parcours.
    nav.cliquer_et_attendre("a[href$='/trame']")
    nav.remplir("form[action$='/trame/themes'] input[name=title]", "Thème E2E")
    nav.cliquer_et_attendre("form[action$='/trame/themes'] button[type=submit]")
    assert "Thème E2E" in nav.texte()
    nav.naviguer(url)
    return url


def _sans_erreur(nav: Navigateur, etape: str) -> None:
    """Aucune erreur depuis le DÉBUT du parcours — après avoir ramassé ce qui
    est encore sur la socket, sinon l'assertion lit un état périmé."""
    nav.drainer()
    assert not nav.erreurs(), f"{etape} : réponses en erreur {nav.erreurs()}"
    assert not nav.exceptions, f"{etape} : exceptions JS {nav.exceptions}"
    assert not nav.erreurs_cdp, f"{etape} : le pilote a reçu une erreur CDP {nav.erreurs_cdp}"
    assert "Origine non autorisée" not in nav.texte(), f"{etape} : 403 CSRF rendu à l'écran"


def test_les_deux_sessions_cdp_sont_sans_keepalive(nav: Navigateur) -> None:
    """Non-régression du 2026-09-10 : les DEUX sessions CDP sont ouvertes sans
    keepalive `websockets`.

    Ce test prouve le RÉGLAGE, pas la cause. Le mécanisme de la panne qui l'a
    motivé n'est pas établi (voir `navigateur_cdp._OPTIONS_WS`, qui dit ce qui
    est mesuré et ce qui ne l'est pas) — mais le réglage, lui, est vérifiable,
    et c'est justement ce qu'un reformatage des appels `websockets.connect`
    ferait sauter en silence : la suite resterait verte et la panne
    reviendrait. Sur le code d'avant, ces deux attributs valent 20 (défaut de
    websockets), donc ce test échoue.

    Il ne demande PAS le serveur : la fixture `nav` suffit, elle lance le
    navigateur et ouvre les deux sessions dans son constructeur."""
    assert nav._ws_navigateur.ping_interval is None, (
        "session CDP du navigateur ouverte avec un keepalive : "
        f"ping_interval={nav._ws_navigateur.ping_interval}"
    )
    assert nav._ws.ping_interval is None, (
        "session CDP de la page ouverte avec un keepalive : "
        f"ping_interval={nav._ws.ping_interval}"
    )


def test_choisir_le_mode_puis_demarrer(serveur: str, nav: Navigateur) -> None:
    """Les tout premiers clics, depuis `/` : « Usage réel » (POST /mode/reel,
    nommé dans le signalement comme cassé lui aussi), puis « Définir une
    trame et démarrer », puis « Démarrer un entretien libre » — trois
    formulaires avant même d'avoir vu une mission."""
    nav.naviguer(serveur + "/")
    nav.cliquer_et_attendre("form[action='/mode/reel'] button[type=submit]")
    assert nav.url().rstrip("/").endswith("/demarrer"), nav.url()
    _sans_erreur(nav, "Choisir le mode réel")

    nav.cliquer_et_attendre("form[action='/entretiens/structure/nouveau'] button[type=submit]")
    assert nav.url().rstrip("/").endswith("/trame"), nav.url()
    _sans_erreur(nav, "Définir une trame et démarrer")

    nav.naviguer(serveur + "/demarrer")
    nav.cliquer_et_attendre("form[action='/entretiens/libre/nouveau'] button[type=submit]")
    assert nav.url().rstrip("/").endswith("/record-libre"), nav.url()
    assert nav.evaluer("!!document.getElementById('rec-start')"), "bouton Démarrer absent"
    _sans_erreur(nav, "Démarrer un entretien libre")


def test_supprimer_un_entretien(serveur: str, nav: Navigateur) -> None:
    """Le cas signalé : Supprimer un entretien depuis la page de la mission
    (formulaire POST + confirm()) doit aboutir, pas rendre un JSON 403."""
    url_mission = _creer_mission(nav, serveur, "E2E suppression")
    nav.cliquer_et_attendre("a[href$='/interviews/new']")
    nav.remplir("input[name=interviewee_name]", "Testeur E2E")
    nav.cliquer_et_attendre("form[action$='/interviews'] button[type=submit]")
    assert "/interviews/" in nav.url(), nav.url()

    nav.naviguer(url_mission)
    assert "Testeur E2E" in nav.texte()
    nav.cliquer_et_attendre("form[action^='/interviews/'][action$='/delete'] button")
    _sans_erreur(nav, "Supprimer l'entretien")
    assert "Testeur E2E" not in nav.texte(), "l'entretien est toujours listé après Supprimer"


def test_parcours_premiers_clics(serveur: str, nav: Navigateur) -> None:
    """Créer une mission → Démarrer la saisie d'un entretien → écran
    d'enregistrement libre → Supprimer la mission. Chaque clic est un clic
    de souris sur le bouton de la page ; à la fin, zéro 4xx/5xx et zéro
    exception JS sur le parcours."""
    url_mission = _creer_mission(nav, serveur, "E2E parcours")

    nav.cliquer_et_attendre("a[href$='/interviews/new']")
    nav.remplir("input[name=interviewee_name]", "Premier entretien")
    nav.cliquer_et_attendre("form[action$='/interviews'] button[type=submit]")
    assert "/interviews/" in nav.url(), nav.url()
    _sans_erreur(nav, "Démarrer la saisie")

    nav.naviguer(url_mission)
    nav.cliquer_et_attendre("a[href$='/interviews/record-libre']")
    assert nav.evaluer("!!document.getElementById('rec-start')"), "bouton Démarrer absent"
    _sans_erreur(nav, "Écran d'enregistrement libre")

    nav.naviguer(url_mission)
    # La page porte QUATRE formulaires `…/delete` (mission, entretien, deux
    # sauvegardes) : viser celui de la mission, pas le premier du document.
    nav.cliquer_et_attendre("form[action^='/missions/'][action$='/delete'] button.btn-danger")
    assert nav.url().rstrip("/").endswith("/missions"), nav.url()
    _sans_erreur(nav, "Supprimer la mission")
    assert "E2E parcours" not in nav.texte(), "la mission est toujours listée après Supprimer"
