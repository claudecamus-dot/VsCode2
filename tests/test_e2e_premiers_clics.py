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


# --------------------------------------------------------------------------- #
# Indicateurs de suivi / matrice risques-contrôles (US9.27 b/c) : les nouveaux
# POST (autosave htmx par ligne, génération IA) rejoués par de vrais clics.
# --------------------------------------------------------------------------- #
_SEED_SUIVI = r"""
import sys
from app.db import SessionLocal, init_db
from app.models import GlobalSynthesis, Interview, Mission, MissionKpi, MissionRisk
init_db()
db = SessionLocal()
m = Mission(name="E2E suivi")
db.add(m); db.flush()
db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
db.add(GlobalSynthesis(mission_id=m.id, status="generated", points_amelioration="- Silos"))
m.kpis = [MissionKpi(position=0, libelle="KPI initial", cible="")]
m.risks = [MissionRisk(position=0, risque="Risque initial", gravite=2, probabilite=2)]
db.commit()
print(m.id, m.kpis[0].id, m.risks[0].id)
"""

_LIRE_SUIVI = r"""
import sys
from app.db import SessionLocal
from app.models import MissionKpi, MissionRisk
db = SessionLocal()
k = db.get(MissionKpi, int(sys.argv[1])); r = db.get(MissionRisk, int(sys.argv[2]))
print(repr((k.cible, r.gravite, r.controle_type)))
"""


def _python_sur_base(base_db: Path, code: str, *args: str) -> str:
    import subprocess
    import sys
    env = dict(os.environ, APP_DB_PATH=str(base_db), PYTHONUTF8="1")
    res = subprocess.run([sys.executable, "-c", code, *args], cwd=Path(__file__).resolve().parents[1],
                         env=env, capture_output=True, text=True, timeout=60,
                         encoding="utf-8")  # enfant en PYTHONUTF8 : accents intacts
    assert res.returncode == 0, res.stderr
    return res.stdout.strip().splitlines()[-1]


def _attendre_texte(nav: Navigateur, selecteur: str, attendu: str, delai_s: float = 10.0) -> None:
    import json
    import time
    fin = time.monotonic() + delai_s
    while time.monotonic() < fin:
        if nav.evaluer(
            "(function(){var e=document.querySelector(" + json.dumps(selecteur) + ");"
            " return !!(e && e.textContent.indexOf(" + json.dumps(attendu) + ")>=0);})()"
        ):
            return
        time.sleep(0.2)
    raise AssertionError(f"« {attendu} » jamais apparu dans {selecteur}")


def test_indicateurs_et_risques_autosave_et_generation(
    tmp_path_factory: pytest.TempPathFactory, nav: Navigateur
) -> None:
    """Onglets Indicateurs / Risques de l'aperçu : saisie d'une cible (autosave
    htmx POST /kpis/{id}/field), choix d'une gravité et d'un type de contrôle
    (POST /risques/{id}/field), puis clic « Régénérer » (POST …/kpis/generate,
    confirm() accepté) puis « Régénérer les risques » (POST …/risques/generate) —
    Ollama injoignable exprès : la page doit rendre son
    message d'erreur en 200, jamais un 4xx CSRF. Écritures relues en base."""
    dossier = tmp_path_factory.mktemp("e2e-suivi")
    base_db = dossier / "e2e.db"
    mid, kid, rid = _python_sur_base(base_db, _SEED_SUIVI).split()
    with serveur_uvicorn(dossier, OLLAMA_HOST="http://127.0.0.1:9") as base:
        nav.naviguer(f"{base}/missions/{mid}/synthese/apercu")
        _sans_erreur(nav, "Ouvrir l'aperçu")

        nav.cliquer(".tab[data-tab='kpis']")
        nav.remplir(f"textarea[hx-post='/kpis/{kid}/field'][hx-vals*='cible']", "90 % sous 6 mois")
        _attendre_texte(nav, f"#kpi-saved-{kid}", "enregistré")
        _sans_erreur(nav, "Autosave cible KPI")

        nav.cliquer(".tab[data-tab='risques']")
        nav.remplir(f"select[hx-post='/risques/{rid}/field'][hx-vals*='gravite']", "3")
        _attendre_texte(nav, f"#risk-saved-{rid}", "enregistré")
        nav.evaluer(f"document.getElementById('risk-saved-{rid}').textContent=''")
        nav.remplir(f"select[hx-post='/risques/{rid}/field'][hx-vals*='controle_type']", "existant")
        _attendre_texte(nav, f"#risk-saved-{rid}", "enregistré")
        _sans_erreur(nav, "Autosave niveaux du risque")

        assert _python_sur_base(base_db, _LIRE_SUIVI, kid, rid) == repr(
            ("90 % sous 6 mois", 3, "existant"))

        nav.cliquer(".tab[data-tab='kpis']")
        nav.cliquer_et_attendre(f"form[action='/missions/{mid}/kpis/generate'] button[type=submit]")
        _sans_erreur(nav, "Régénérer les indicateurs")
        assert "⚠" in nav.texte(), "le message d'échec IA n'est pas rendu"

        # Revue adversariale (P5) : le second POST de génération, « Régénérer les
        # risques », cliqué lui aussi — TestClient reçoit un Origin injecté par le
        # conftest, seul ce clic prouve le CSRF réel de cette route.
        nav.cliquer(".tab[data-tab='risques']")
        nav.cliquer_et_attendre(f"form[action='/missions/{mid}/risques/generate'] button[type=submit]")
        _sans_erreur(nav, "Régénérer les risques")
        assert "⚠" in nav.texte(), "le message d'échec IA (risques) n'est pas rendu"
        # Ollama injoignable : la matrice existante n'est pas écrasée.
        assert _python_sur_base(base_db, _LIRE_SUIVI, kid, rid) == repr(
            ("90 % sous 6 mois", 3, "existant"))


# --------------------------------------------------------------------------- #
# Grille de maturité par pilier (incr.10 palier 3) : autosave + génération.
# --------------------------------------------------------------------------- #
_SEED_MATURITE = r"""
from app.db import SessionLocal, init_db
from app.models import (GlobalSynthesis, Interview, Mission, MissionMaturite, Theme, Trame)
init_db()
db = SessionLocal()
m = Mission(name="E2E maturité")
db.add(m); db.flush()
tr = Trame(mission_id=m.id); db.add(tr); db.flush()
db.add(Theme(trame_id=tr.id, title="Gouvernance", position=0))
db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
db.add(GlobalSynthesis(mission_id=m.id, status="generated", points_amelioration="- Silos"))
m.maturites = [MissionMaturite(position=0, pilier="Gouvernance", score=1, justification="")]
db.commit()
print(m.id, m.maturites[0].id)
"""

_LIRE_MATURITE = r"""
import sys
from app.db import SessionLocal
from app.models import MissionMaturite
x = SessionLocal().get(MissionMaturite, int(sys.argv[1]))
print(repr((x.score, x.justification)))
"""


def test_grille_maturite_autosave_et_generation(
    tmp_path_factory: pytest.TempPathFactory, nav: Navigateur
) -> None:
    """Onglet Maturité : choix d'un score (POST /maturites/{id}/field), saisie
    d'une justification (autosave htmx), puis « Régénérer la grille » (POST
    …/maturite/generate, confirm() accepté) — Ollama injoignable exprès : message
    d'erreur rendu en 200, jamais un 4xx CSRF, grille existante conservée."""
    dossier = tmp_path_factory.mktemp("e2e-maturite")
    base_db = dossier / "e2e.db"
    mid, lid = _python_sur_base(base_db, _SEED_MATURITE).split()
    with serveur_uvicorn(dossier, OLLAMA_HOST="http://127.0.0.1:9") as base:
        nav.naviguer(f"{base}/missions/{mid}/synthese/apercu")
        _sans_erreur(nav, "Ouvrir l'aperçu")
        nav.cliquer(".tab[data-tab='maturite']")
        nav.remplir(f"select[hx-post='/maturites/{lid}/field'][hx-vals*='score']", "3")
        _attendre_texte(nav, f"#mat-saved-{lid}", "enregistré")
        nav.evaluer(f"document.getElementById('mat-saved-{lid}').textContent=''")
        nav.remplir(f"textarea[hx-post='/maturites/{lid}/field'][hx-vals*='justification']",
                    "Pratiques pilotées")
        _attendre_texte(nav, f"#mat-saved-{lid}", "enregistré")
        _sans_erreur(nav, "Autosave maturité")
        assert _python_sur_base(base_db, _LIRE_MATURITE, lid) == repr((3, "Pratiques pilotées"))

        nav.cliquer_et_attendre(f"form[action='/missions/{mid}/maturite/generate'] button[type=submit]")
        _sans_erreur(nav, "Régénérer la grille de maturité")
        assert "⚠" in nav.texte(), "le message d'échec IA n'est pas rendu"
        assert _python_sur_base(base_db, _LIRE_MATURITE, lid) == repr((3, "Pratiques pilotées"))
