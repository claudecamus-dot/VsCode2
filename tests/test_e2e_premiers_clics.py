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


def test_apercu_risques_et_maturite_suit_la_saisie_sans_rechargement(
    tmp_path_factory: pytest.TempPathFactory, nav: Navigateur
) -> None:
    """DANA-1 : l'aperçu des onglets Risques et Maturité suivait la saisie
    seulement après rechargement (note « rechargez »), alors que Indicateurs
    était en direct. Changer une gravité doit déplacer la bulle R1 et le texte
    doit suivre dans le registre ; changer un score doit changer la jauge et le
    libellé « n · Niveau » — dans la MÊME page (marqueur window survivant)."""
    dossier = tmp_path_factory.mktemp("e2e-live")
    base_db = dossier / "e2e.db"
    seed = _SEED_MATURITE.replace(
        "db.commit()",
        "m.risks = [MissionRisk(position=0, risque='Risque initial', gravite=2, probabilite=2)]\ndb.commit()",
    ).replace("print(m.id, m.maturites[0].id)", "print(m.id, m.maturites[0].id, m.risks[0].id)"
              ).replace("MissionMaturite, Theme", "MissionMaturite, MissionRisk, Theme")
    mid, lid, rid = _python_sur_base(base_db, seed).split()
    bulle = ("(function(){var s=document.querySelector('[data-panel=risques] .risk-cell .risk-pill');"
             " var c=s&&s.parentElement; return c?(c.dataset.g||'')+'/'+(c.dataset.p||'')+':'"
             "+s.textContent:'aucune';})()")
    jauge = ("(function(){var j=document.querySelector('[data-panel=maturite] .maturite-table .maturite-jauge');"
             " return j.querySelectorAll('i.on').length+'|'+j.parentElement.querySelector('.maturite-lib')"
             ".textContent.trim();})()")
    with serveur_uvicorn(dossier, OLLAMA_HOST="http://127.0.0.1:9") as base:
        nav.naviguer(f"{base}/missions/{mid}/synthese/apercu")
        _sans_erreur(nav, "Ouvrir l'aperçu")
        nav.evaluer("window.__meme_page = 1")
        assert "rechargez" not in nav.texte()

        nav.cliquer(".tab[data-tab='risques']")
        assert nav.evaluer(bulle) == "2/2:R1"
        nav.remplir(f"select[hx-post='/risques/{rid}/field'][hx-vals*='gravite']", "3")
        assert nav.evaluer(bulle) == "3/2:R1"
        nav.remplir(f"textarea[hx-post='/risques/{rid}/field'][hx-vals*='risque']", "Risque modifié")
        assert "Risque modifié" in nav.evaluer("document.getElementById('risk-register').textContent")
        _attendre_texte(nav, f"#risk-saved-{rid}", "enregistré")

        nav.cliquer(".tab[data-tab='maturite']")
        assert nav.evaluer(jauge).startswith("1|1 · ")
        nav.remplir(f"select[hx-post='/maturites/{lid}/field'][hx-vals*='score']", "3")
        assert nav.evaluer(jauge) == "3|3 · Maîtrisé"
        _attendre_texte(nav, f"#mat-saved-{lid}", "enregistré")
        assert nav.evaluer("window.__meme_page") == 1, "la page a été rechargée"
        _sans_erreur(nav, "Aperçu en direct")


# --------------------------------------------------------------------------- #
# Deck d'exemple (US5.2) : téléverser, voir le plan extrait, retirer.
# --------------------------------------------------------------------------- #
_SEED_APERCU = r"""
from app.db import SessionLocal, init_db
from app.models import GlobalSynthesis, Interview, Mission
init_db()
db = SessionLocal()
m = Mission(name="E2E deck d'exemple")
db.add(m); db.flush()
db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
db.add(GlobalSynthesis(mission_id=m.id, status="generated", points_amelioration="- Silos"))
db.commit()
print(m.id)
"""

_LIRE_EXEMPLE = r"""
import sys
from app.db import SessionLocal
from app.models import Mission
print(repr(SessionLocal().get(Mission, int(sys.argv[1])).pptx_exemple_path))
"""


def test_deck_exemple_televerser_puis_retirer(
    tmp_path_factory: pytest.TempPathFactory, nav: Navigateur
) -> None:
    """Choix du fichier (DOM.setFileInputFiles, le sélecteur natif n'existant pas
    en headless) puis CLIC réel sur « Utiliser ce deck d'exemple » (POST
    multipart /pptx-exemple) : le plan extrait s'affiche ; puis clic sur
    « Retirer le deck d'exemple » (POST …/retirer). Aucun 4xx CSRF, base relue."""
    dossier = tmp_path_factory.mktemp("e2e-exemple")
    base_db = dossier / "e2e.db"
    mid = _python_sur_base(base_db, _SEED_APERCU)
    exemple = Path(__file__).resolve().parents[1] / "docs" / "exemples" / "deck-restitution-exemple.pptx"
    with serveur_uvicorn(dossier) as base:
        nav.naviguer(f"{base}/missions/{mid}/synthese/apercu")
        _sans_erreur(nav, "Ouvrir l'aperçu")
        form = f"form[action='/missions/{mid}/pptx-exemple']"
        nav.choisir_fichier(f"{form} input[type=file]", exemple)
        nav.cliquer_et_attendre(f"{form} button[type=submit]")
        _sans_erreur(nav, "Téléverser le deck d'exemple")
        assert "Deck d'exemple actif" in nav.texte()
        assert "Fiches recommandation" in nav.texte()
        assert _python_sur_base(base_db, _LIRE_EXEMPLE, mid) == repr(f"{mid}.pptx")

        nav.cliquer_et_attendre(f"form[action='/missions/{mid}/pptx-exemple/retirer'] button[type=submit]")
        _sans_erreur(nav, "Retirer le deck d'exemple")
        assert "Aucun deck d'exemple" in nav.texte()
        assert _python_sur_base(base_db, _LIRE_EXEMPLE, mid) == "None"


def test_upload_template_refuse_affiche_l_ecran_et_son_message_en_400(
    tmp_path_factory: pytest.TempPathFactory, nav: Navigateur
) -> None:
    """P5 sur le code HTTP harmonisé (2026-09-27) : « Utiliser ce template » avec
    un fichier refusé répond désormais 400 et non 200. Le formulaire est un POST
    multipart ordinaire (pas htmx), donc c'est le NAVIGATEUR qui rend le corps :
    ce test prouve qu'il affiche bien l'écran d'aperçu porteur du message, et pas
    une page d'erreur. Le 400 étant attendu ici, on l'assère au lieu d'appeler
    `_sans_erreur` (qui refuse tout 4xx)."""
    dossier = tmp_path_factory.mktemp("e2e-template-400")
    base_db = dossier / "e2e.db"
    mid = _python_sur_base(base_db, _SEED_APERCU)
    mauvais = dossier / "notes.txt"
    mauvais.write_text("ceci n'est pas un pptx", encoding="utf-8")
    with serveur_uvicorn(dossier) as base:
        nav.naviguer(f"{base}/missions/{mid}/synthese/apercu")
        _sans_erreur(nav, "Ouvrir l'aperçu")
        form = f"form[action='/missions/{mid}/pptx-template']"
        nav.choisir_fichier(f"{form} input[type=file]", mauvais)
        nav.cliquer_et_attendre(f"{form} button[type=submit]")
        nav.drainer()
        texte = nav.texte()
        assert "Un fichier .pptx est attendu." in texte, texte[:400]
        assert "Export PPT" in texte  # l'écran d'aperçu, pas une page d'erreur
        assert "Origine non autorisée" not in texte and "Erreur interne" not in texte
        assert not nav.exceptions, nav.exceptions
        statuts = sorted(r["status"] for r in nav.erreurs_http()
                         if r["url"].endswith("/pptx-template"))
        assert statuts == [400], nav.erreurs_http()


# --------------------------------------------------------------------------- #
# Onglet PÉRIMÉ (constat F de l'atelier-dev) : les axes ont été régénérés/renommés
# dans une autre session, l'onglet resté ouvert autosave encore l'ANCIEN intitulé.
# Comportement documenté par 0f5bb4e (snap_axe) et gardé par cdef174 (identité
# d'abord) — jamais rejoué dans un vrai navigateur jusqu'ici.
# --------------------------------------------------------------------------- #
_SEED_AXES_PERIMES = r"""
from app.db import SessionLocal, init_db
from app.models import (GlobalSynthesis, Interview, Mission, MissionKpi,
                        RecommendationAxis)
init_db()
db = SessionLocal()
m = Mission(name="E2E onglet perime")
db.add(m); db.flush()
db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
db.add(GlobalSynthesis(mission_id=m.id, status="generated", points_amelioration="- Silos"))
# L'onglet va rendre CES intitulés : une variante de casse et d'espaces de celui
# qui survivra, et un axe qui aura disparu.
m.recommendation_axes = [
    RecommendationAxis(position=0, title="gouvernance  data"),
    RecommendationAxis(position=1, title="Axe supprime ailleurs"),
]
m.kpis = [MissionKpi(position=0, libelle="Taux de conformité", cible="", axe="")]
db.commit()
print(m.id, m.kpis[0].id)
"""

# L'AUTRE session : les axes sont régénérés — le survivant reprend son intitulé
# canonique, l'autre disparaît. L'onglet ouvert, lui, ne le sait pas.
_REGENERER_AXES = r"""
import sys
from app.db import SessionLocal
from app.models import Mission
db = SessionLocal()
m = db.get(Mission, int(sys.argv[1]))
for a in list(m.recommendation_axes):
    if a.title == "gouvernance  data":
        a.title = "Gouvernance data"
    else:
        m.recommendation_axes.remove(a)
db.commit()
print([a.title for a in m.recommendation_axes])
"""

_LIRE_AXE_KPI = r"""
import sys
from app.db import SessionLocal
from app.models import MissionKpi
print(repr(SessionLocal().get(MissionKpi, int(sys.argv[1])).axe))
"""


def test_onglet_perime_axe_variante_rapprochee_et_axe_disparu_refuse(
    tmp_path_factory: pytest.TempPathFactory, nav: Navigateur
) -> None:
    """Onglet resté ouvert pendant qu'une autre session régénère les axes :
    l'autosave de l'axe poste l'intitulé PÉRIMÉ que le `<select>` porte encore.
    Une variante de casse/espaces d'un axe réel est rapprochée (« gouvernance
    data » → « Gouvernance data », écrit en base, « enregistré » à l'écran) ;
    un axe qui ne correspond à AUCUN axe de la mission reste refusé (400 htmx,
    rien d'écrit, aucune mention d'enregistrement). Ce que voit l'utilisateur ET
    ce qui atterrit en base, pas seulement le code HTTP."""
    import json
    dossier = tmp_path_factory.mktemp("e2e-axes-perimes")
    base_db = dossier / "e2e.db"
    mid, kid = _python_sur_base(base_db, _SEED_AXES_PERIMES).split()
    with serveur_uvicorn(dossier) as base:
        nav.naviguer(f"{base}/missions/{mid}/synthese/apercu")
        _sans_erreur(nav, "Ouvrir l'aperçu")
        nav.cliquer(".tab[data-tab='kpis']")
        select = f"select[hx-post='/kpis/{kid}/field'][hx-vals*='axe']"
        # L'onglet porte bien les deux intitulés d'AVANT la régénération.
        options = nav.evaluer(
            "Array.from(document.querySelectorAll(" + json.dumps(select + " option")
            + ")).map(function(o){return o.value;})")
        assert "gouvernance  data" in options and "Axe supprime ailleurs" in options, options

        # L'autre session régénère les axes. L'onglet ouvert n'est pas rechargé.
        assert _python_sur_base(base_db, _REGENERER_AXES, mid) == repr(["Gouvernance data"])

        # 1) Variante de casse/espaces d'un axe RÉEL : rapprochée, pas refusée.
        nav.remplir(select, "gouvernance  data")
        _attendre_texte(nav, f"#kpi-saved-{kid}", "enregistré")
        _sans_erreur(nav, "Autosave de l'axe périmé rapproché")
        assert _python_sur_base(base_db, _LIRE_AXE_KPI, kid) == repr("Gouvernance data")

        # 2) Axe disparu de la mission : refusé, et la base garde la valeur snappée.
        nav.evaluer(f"document.getElementById('kpi-saved-{kid}').textContent=''")
        nav.remplir(select, "Axe supprime ailleurs")
        nav.drainer()
        refus = [r for r in nav.erreurs_http() if r["url"].endswith(f"/kpis/{kid}/field")]
        assert [r["status"] for r in refus] == [400], nav.erreurs_http()
        assert not nav.exceptions, nav.exceptions
        assert "enregistré" not in nav.evaluer(
            f"document.getElementById('kpi-saved-{kid}').textContent")
        assert _python_sur_base(base_db, _LIRE_AXE_KPI, kid) == repr("Gouvernance data")
