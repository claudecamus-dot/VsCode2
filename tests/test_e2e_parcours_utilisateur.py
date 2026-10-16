"""Parcours utilisateur de bout en bout, côté CONSULTANT (comité e2e du
2026-10-14) : quatre trous que `test_e2e_premiers_clics.py` ne fermait pas.

1. l'export et le TÉLÉCHARGEMENT du .pptx (le livrable) par le vrai bouton ;
2. la connexion par COOKIE de session, sans l'en-tête Bearer du harnais, puis
   la navigation sur plusieurs écrans protégés ;
3. une fiche (entretien, mission) SUPPRIMÉE alors qu'un onglet y est resté :
   ce que l'utilisateur voit réellement (page 404 lisible depuis le 2026-10-14,
   plus le JSON brut ; message d'autosave explicite) ;
4. l'entretien guidé : répondre à une question au clavier, recharger,
   relire la réponse, puis « Terminer ».

Règles tenues ici : ce que l'utilisateur VOIT (`nav.texte()` = innerText, qui
exclut le texte masqué ; jamais `textContent` pour une visibilité), boutons
cliqués à la souris (visibles ET non recouverts), polling plutôt que
`time.sleep` fixe (l'attente d'ABSENCE d'effacement sur 3,5 s est un polling
qui échoue dès le premier effacement), aucune erreur réseau/JS à chaque étape, une mission semée
par test. Ollama est injoignable exprès (`OLLAMA_HOST` sur le port 9) : aucun
test ne dépend de l'IA ni de la coupe temporaire.

Même garde que le voisin : sans navigateur Chromium le module est sauté,
sauf `E2E_OBLIGATOIRE=1` (alors c'est une ERREUR).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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

from e2e_serveur import serveur_uvicorn  # noqa: E402
from navigateur_cdp import Navigateur, trouver_navigateur  # noqa: E402

_NAVIGATEUR = trouver_navigateur()

if _NAVIGATEUR is None and _OBLIGATOIRE:
    raise RuntimeError(
        "E2E_OBLIGATOIRE est posé mais aucun navigateur Chromium (Edge/Chrome) n'est trouvé"
    )

pytestmark = pytest.mark.skipif(
    _NAVIGATEUR is None,
    reason="aucun navigateur Chromium (Edge/Chrome) trouvé — E2E_NAVIGATEUR pour en forcer un",
)

_RACINE = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Plomberie locale (recopiée de test_e2e_premiers_clics.py, qui n'est pas
# importable : pas de tests/__init__.py)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def serveur(tmp_path_factory: pytest.TempPathFactory):
    """Un vrai uvicorn, base temporaire, Ollama injoignable exprès. Rend
    `(base_url, chemin_de_la_base)` : les tests sèment et relisent la base par
    des sous-processus (`_python_sur_base`)."""
    dossier = tmp_path_factory.mktemp("e2e-parcours")
    with serveur_uvicorn(dossier, OLLAMA_HOST="http://127.0.0.1:9") as base:
        yield base, dossier / "e2e.db"


@pytest.fixture
def nav():
    """Profil sous le temporaire SYSTÈME (chemin court, cf. navigateur_cdp)."""
    profil = Path(tempfile.mkdtemp(prefix="e2e-nav-"))
    navigateur = None
    try:
        navigateur = Navigateur(_NAVIGATEUR, profil / "profil")
        yield navigateur
    finally:
        if navigateur is not None:
            navigateur.fermer()
        shutil.rmtree(profil, ignore_errors=True)


def _python_sur_base(base_db: Path, code: str, *args: str) -> str:
    env = dict(os.environ, APP_DB_PATH=str(base_db), PYTHONUTF8="1")
    res = subprocess.run([sys.executable, "-c", code, *args], cwd=_RACINE, env=env,
                         capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return res.stdout.strip().splitlines()[-1]


def _sans_erreur(nav: Navigateur, etape: str, *, sauf: tuple[str, ...] = ()) -> None:
    """Aucune réponse 4xx/5xx, ressource perdue, exception JS ou erreur CDP
    depuis le début du parcours (`sauf` : chemins dont l'erreur est ATTENDUE et
    déjà asserée sur place)."""
    nav.drainer()
    inattendues = [r for r in nav.erreurs()
                   if not any(str(r.get("url", "")).endswith(s) for s in sauf)]
    assert not inattendues, f"{etape} : réponses en erreur {inattendues}"
    assert not nav.exceptions, f"{etape} : exceptions JS {nav.exceptions}"
    assert not nav.erreurs_cdp, f"{etape} : erreur CDP {nav.erreurs_cdp}"
    assert "Origine non autorisée" not in nav.texte(), f"{etape} : 403 CSRF rendu à l'écran"


def _attendre(condition, message: str, delai_s: float = 10.0, pas_s: float = 0.2):
    """Polling : rend la première valeur vraie de `condition()`, sinon échoue."""
    fin = time.monotonic() + delai_s
    while time.monotonic() < fin:
        valeur = condition()
        if valeur:
            return valeur
        time.sleep(pas_s)
    raise AssertionError(message)


def _attendre_visible(nav: Navigateur, selecteur: str, attendu: str, delai_s: float = 10.0) -> None:
    """Attend que `attendu` soit dans le texte VISIBLE (innerText) de l'élément —
    pas `textContent`, qui inclut le texte masqué. Comparaison insensible à la
    casse : innerText applique `text-transform` (badges rendus « RÉPONDUE »)."""
    _attendre(
        lambda: nav.evaluer(
            "(function(){var e=document.querySelector(" + json.dumps(selecteur) + ");"
            " return !!(e && e.innerText.toLowerCase().indexOf(" + json.dumps(attendu.lower()) + ")>=0);})()"
        ),
        f"« {attendu} » jamais visible dans {selecteur}", delai_s,
    )


def _saisir_au_clavier(nav: Navigateur, selecteur: str, valeur: str) -> None:
    """Saisie d'un champ dont l'autosave htmx écoute `keyup` : `nav.remplir`
    n'émet que input/change (piège documenté dans navigateur_cdp.remplir), on
    termine donc par un `keyup`, comme un utilisateur qui tape."""
    nav.remplir(selecteur, valeur)
    ok = nav.evaluer(
        "(function(){var e=document.querySelector(" + json.dumps(selecteur) + ");"
        " if(!e) return false;"
        " e.dispatchEvent(new KeyboardEvent('keyup',{bubbles:true,key:'a'}));"
        " return true;})()"
    )
    assert ok, f"champ introuvable : {selecteur}"


def _statut_document(nav: Navigateur, fin_url: str) -> int:
    """Statut HTTP de la DERNIÈRE réponse de type Document dont l'URL finit par
    `fin_url` — ce que le navigateur a réellement reçu pour cette page."""
    nav.drainer()
    statuts = [r["status"] for r in nav.reponses
               if r.get("type") == "Document" and r["url"].endswith(fin_url)]
    assert statuts, f"aucune réponse Document pour …{fin_url} : {nav.reponses[-5:]}"
    return statuts[-1]


# --------------------------------------------------------------------------- #
# 1. Export et téléchargement du .pptx
# --------------------------------------------------------------------------- #
# Aucune dépendance à l'IA : la synthèse globale est SEMÉE (status generated) et
# la route d'export (`build_presentation`) est du code pur ; les photos réseau
# sont coupées par PPTX_NO_PHOTO_FETCH (posé par tests/conftest.py, hérité par
# uvicorn via os.environ).
_SEED_EXPORT = r"""
from app.db import SessionLocal, init_db
from app.models import GlobalSynthesis, Interview, Mission, Question, Theme, Trame
init_db()
db = SessionLocal()
m = Mission(name="E2E export")
db.add(m); db.flush()
tr = Trame(mission_id=m.id); db.add(tr); db.flush()
th = Theme(trame_id=tr.id, title="Gouvernance", position=0); db.add(th); db.flush()
db.add(Question(theme_id=th.id, label="Comment décidez-vous ?", position=0))
db.add(Interview(mission_id=m.id, interviewee_name="Témoin export", status="done"))
db.add(GlobalSynthesis(mission_id=m.id, status="generated",
                       contexte="Un contexte de test.", points_amelioration="- Silos entre équipes"))
db.commit()
print(m.id)
"""


def _autoriser_telechargements(nav: Navigateur, dossier: Path) -> None:
    """Le téléchargement d'un clic réel atterrit dans `dossier` (headless : pas de
    boîte « Enregistrer sous »). Commande `Browser.*` d'abord sur la session de
    la page, sinon sur celle du navigateur."""
    params = dict(behavior="allow", downloadPath=str(dossier))
    try:
        nav._run(nav._cmd("Browser.setDownloadBehavior", **params))
    except RuntimeError:
        nav._run(nav._cmd_sur(nav._ws_navigateur, "Browser.setDownloadBehavior", **params))


def test_exporter_et_telecharger_le_pptx(serveur, nav: Navigateur, tmp_path) -> None:
    """Écran de synthèse -> clic réel sur « Exporter en PPT » -> un VRAI .pptx
    arrive dans le dossier de téléchargement : zip (PK), ouvrable par python-pptx,
    au moins une slide. Le fichier est celui que le NAVIGATEUR a reçu (pas un
    second GET du test)."""
    from pptx import Presentation

    base, base_db = serveur
    mid = _python_sur_base(base_db, _SEED_EXPORT)
    telechargements = tmp_path / "dl"
    telechargements.mkdir()
    _autoriser_telechargements(nav, telechargements)

    nav.naviguer(f"{base}/missions/{mid}/synthese/apercu")
    _sans_erreur(nav, "Ouvrir l'écran d'export")
    assert "Définition des slides" in nav.texte()
    bouton = "form#pptx-config-form button[type=submit]"
    assert nav.evaluer(
        "document.querySelector(" + json.dumps(bouton) + ").innerText.trim()") == "Exporter en PPT"
    cible = nav.evaluer(
        "document.getElementById('pptx-config-form').getAttribute('action')")
    assert cible == f"/missions/{mid}/export/pptx", cible

    nav.cliquer(bouton)  # le fichier se télécharge sans quitter la page
    fichier = _attendre(
        lambda: next((p for p in telechargements.glob("*.pptx")
                      if not p.with_name(p.name + ".crdownload").exists()), None),
        f"aucun .pptx téléchargé dans {telechargements} : {list(telechargements.iterdir())}",
        delai_s=60.0,
    )
    # Le téléchargement peut être encore en cours d'écriture : attendre la taille stable.
    tailles: list[int] = []

    def _stable() -> bool:
        tailles.append(fichier.stat().st_size)
        return len(tailles) >= 2 and tailles[-1] == tailles[-2] > 0

    _attendre(_stable, "fichier .pptx jamais stabilisé", pas_s=0.3)
    octets = fichier.read_bytes()
    assert octets[:2] == b"PK", octets[:8]
    assert "restitution_" in fichier.name, fichier.name
    prs = Presentation(str(fichier))
    assert len(prs.slides) >= 1, "le .pptx téléchargé ne contient aucune slide"
    # La page est restée utilisable (le bouton se libère seul, busy.js).
    assert "Définition des slides" in nav.texte()
    _sans_erreur(nav, "Télécharger le .pptx")


# --------------------------------------------------------------------------- #
# 2. Connexion par cookie de session, sans Bearer
# --------------------------------------------------------------------------- #
_SEED_NAVIGATION = r"""
from app.db import SessionLocal, init_db
from app.models import Interview, Mission, Question, Theme, Trame
init_db()
db = SessionLocal()
m = Mission(name="E2E cookie")
db.add(m); db.flush()
tr = Trame(mission_id=m.id); db.add(tr); db.flush()
th = Theme(trame_id=tr.id, title="Gouvernance", position=0); db.add(th); db.flush()
db.add(Question(theme_id=th.id, label="Comment décidez-vous ?", position=0))
i = Interview(mission_id=m.id, interviewee_name="Témoin cookie", status="draft")
db.add(i); db.commit()
print(m.id, i.id)
"""


def _cookie_de_session(nav: Navigateur, base: str) -> list[dict]:
    res = nav._run(nav._cmd("Network.getCookies", urls=[base + "/"]))
    return [c for c in res.get("cookies", []) if c["name"] == "app_session"]


def test_le_cookie_de_session_garde_connecte_sur_plusieurs_ecrans(
    serveur, nav: Navigateur
) -> None:
    """Contrairement au reste de la suite, AUCUN Bearer : le navigateur est
    anonyme (`poser_entetes({})`). Après le formulaire de connexion, seul le
    cookie `app_session` authentifie les écrans suivants (liste des missions,
    mission, fiche d'entretien), atteints par des CLICS. Puis, cookie effacé, la
    page protégée redonne la connexion (401 + formulaire, à la même URL)."""
    base, base_db = serveur
    mid, iid = _python_sur_base(base_db, _SEED_NAVIGATION).split()
    nav.poser_entetes({})  # visiteur anonyme : sans ça le Bearer connecterait

    nav.naviguer(base + "/connexion")
    assert nav.evaluer("!!document.querySelector('.login-card')")
    assert not _cookie_de_session(nav, base), "cookie de session déjà présent avant connexion"
    nav.remplir("#mot_de_passe", os.environ["APP_AUTH_PASSWORD"])
    nav.cliquer_et_attendre("form[action='/connexion'] button[type=submit]")
    assert nav.url().rstrip("/") == base.rstrip("/"), nav.url()
    cookies = _cookie_de_session(nav, base)
    assert cookies, "le formulaire n'a posé aucun cookie app_session"
    assert cookies[0]["httpOnly"], "le cookie de session devrait être httpOnly"

    def pas_connexion(etape: str) -> None:
        assert not nav.evaluer("!!document.querySelector('.login-card')"), (
            f"{etape} : renvoyé sur la connexion malgré le cookie")
        assert "/connexion" not in nav.url(), f"{etape} : {nav.url()}"
        assert "Mot de passe" not in nav.texte(), f"{etape} : formulaire de connexion visible"

    # Écran 1 : liste des missions, par le lien de navigation.
    nav.cliquer_et_attendre("a[href='/missions']")
    pas_connexion("liste des missions")
    assert nav.url().rstrip("/").endswith("/missions"), nav.url()
    assert "E2E cookie" in nav.texte(), "la mission semée n'est pas listée"
    # Écran 2 : la mission.
    nav.cliquer_et_attendre(f"a[href='/missions/{mid}']")
    pas_connexion("mission")
    assert nav.url().rstrip("/").endswith(f"/missions/{mid}"), nav.url()
    assert "Témoin cookie" in nav.texte()
    # Écran 3 : la fiche de l'entretien.
    nav.cliquer_et_attendre(f"a[href='/interviews/{iid}']")
    pas_connexion("fiche d'entretien")
    assert nav.url().rstrip("/").endswith(f"/interviews/{iid}"), nav.url()
    assert "Comment décidez-vous ?" in nav.texte(), "la fiche n'affiche pas ses questions"
    # Écran 4 : retour mission par le fil d'Ariane (le cookie sert encore).
    nav.cliquer_et_attendre(f".breadcrumb a[href='/missions/{mid}']")
    pas_connexion("retour à la mission")
    _sans_erreur(nav, "Navigation par cookie")

    # Cookie effacé : la page protégée ne s'ouvre plus. Le serveur rend le
    # formulaire de connexion SOUS 401, à la même URL (pas de redirection :
    # app/auth.py, `exiger_authentification`).
    nav._run(nav._cmd("Network.deleteCookies", name="app_session", url=base + "/"))
    assert not _cookie_de_session(nav, base)
    nav.naviguer(f"{base}/missions/{mid}")
    assert nav.evaluer("!!document.querySelector('.login-card')"), (
        "page protégée toujours servie après effacement du cookie")
    assert "Témoin cookie" not in nav.texte(), "contenu protégé fuité sans cookie"
    assert _statut_document(nav, f"/missions/{mid}") == 401
    _sans_erreur(nav, "Cookie effacé", sauf=(f"/missions/{mid}",))


# --------------------------------------------------------------------------- #
# 3. Fiche supprimée, onglet resté ouvert
# --------------------------------------------------------------------------- #
# Ces tests étaient d'abord de CARACTÉRISATION (JSON brut observé le
# 2026-10-14) ; ils ont été INVERSÉS après l'arbitrage « 404 lisible » : ils
# EXIGENT désormais la page lisible (message, lien « Voir les missions », pas
# de `{"detail"`) et un message d'autosave explicite.
_SEED_FICHE = r"""
from app.db import SessionLocal, init_db
from app.models import Interview, Mission, Question, Theme, Trame
init_db()
db = SessionLocal()
m = Mission(name="E2E onglet resté")
db.add(m); db.flush()
tr = Trame(mission_id=m.id); db.add(tr); db.flush()
th = Theme(trame_id=tr.id, title="Gouvernance", position=0); db.add(th); db.flush()
q = Question(theme_id=th.id, label="Comment décidez-vous ?", position=0); db.add(q); db.flush()
i = Interview(mission_id=m.id, interviewee_name="Témoin fantôme", status="draft")
db.add(i); db.commit()
print(m.id, i.id, q.id)
"""

_SUPPRIMER = r"""
import sys
from app.db import SessionLocal
from app.models import Interview, Mission
db = SessionLocal()
cible = db.get(Mission if sys.argv[1] == "mission" else Interview, int(sys.argv[2]))
db.delete(cible); db.commit()
print("ok")
"""


def _verifier_page_introuvable(nav: Navigateur, base: str, message: str) -> None:
    """Texte VISIBLE (innerText) : message attendu, pas de JSON brut, lien de sortie
    visible et réellement cliquable, qui mène à la liste des missions."""
    vu = nav.texte()
    assert message in vu, vu
    assert '{"detail"' not in vu, vu
    lien = "main a[href='/missions']"
    visible = nav.evaluer(
        f"(() => {{ const a = document.querySelector({json.dumps(lien)}); if (!a) return false;"
        " const r = a.getBoundingClientRect();"
        " return r.width > 0 && r.height > 0 && a.innerText.trim() === 'Voir les missions'; })()")
    assert visible is True, "le lien « Voir les missions » est absent ou invisible"
    nav.cliquer_et_attendre(lien)
    assert nav.url().rstrip("/").endswith("/missions"), nav.url()
    assert nav.evaluer(
        "(function(){var h=document.querySelector('.page-head h1');"
        " return h && h.offsetParent !== null ? h.innerText.trim() : '';})()"
    ) == "Missions", "titre de la liste des missions absent"


def test_fiche_supprimee_ailleurs_action_de_l_ancien_onglet(serveur, nav: Navigateur) -> None:
    """L'entretien est supprimé « dans un autre onglet » (ici : directement en
    base) pendant que celui-ci reste sur la fiche. Deux gestes de l'ancien onglet :
    (a) taper une réponse (autosave htmx), (b) cliquer « Terminer »."""
    base, base_db = serveur
    mid, iid, qid = _python_sur_base(base_db, _SEED_FICHE).split()
    nav.naviguer(f"{base}/interviews/{iid}")
    assert "Témoin fantôme" in nav.texte() or nav.evaluer(
        "document.querySelector('.identity-name').value") == "Témoin fantôme"
    _sans_erreur(nav, "Ouvrir la fiche")
    assert _python_sur_base(base_db, _SUPPRIMER, "entretien", iid) == "ok"

    # (a) L'autosave de l'ancien onglet : requête htmx POST -> 404 JSON.
    champ = f"textarea[hx-post='/interviews/{iid}/answers/{qid}']"
    _saisir_au_clavier(nav, champ, "Une réponse tapée dans l'ancien onglet")
    _attendre(
        lambda: any(r["status"] == 404 and r["url"].endswith(f"/answers/{qid}")
                    for r in (nav.drainer() or nav.reponses)),
        "l'autosave n'a jamais reçu de réponse 404", delai_s=10.0)
    # Comportement corrigé le 2026-10-14 (PB) : app/static/autosave.js lit le
    # statut 404 de l'erreur htmx et dit à l'utilisateur que la fiche n'existe
    # plus (avant : « ⚠ échec de l'enregistrement », trop vague). Le message reste
    # affiché (le détail de la persistance est couvert par
    # test_autosave_fiche_supprimee_message_explicite).
    _attendre_visible(
        nav, f"#status-{qid}",
        "⚠ fiche introuvable — rien n'est enregistré. Copiez votre texte avant de quitter.")
    assert "Entretien introuvable" not in nav.texte(), "le JSON brut est visible"
    assert "enr." not in nav.texte()
    assert nav.evaluer(f"document.querySelector({json.dumps(champ)}).value") == (
        "Une réponse tapée dans l'ancien onglet"), "la saisie a disparu du champ"

    # (b) « Terminer » : navigation GET. Comportement corrigé le 2026-10-14 : le
    # navigateur ne montre plus le JSON brut mais une page lisible (handler 404).
    nav.cliquer_et_attendre("a[href$='/finish']")
    assert nav.url().endswith(f"/interviews/{iid}/finish"), nav.url()
    assert _statut_document(nav, f"/interviews/{iid}/finish") == 404
    _verifier_page_introuvable(
        nav, base, "Cet entretien n'existe plus. Il a peut-être été supprimé depuis un autre onglet.")


def test_mission_supprimee_ailleurs_clic_de_l_ancien_onglet(serveur, nav: Navigateur) -> None:
    """Même constat pour la MISSION : la page mission reste ouverte, la mission est
    supprimée ailleurs, puis on clique « + Entretien structuré »."""
    base, base_db = serveur
    mid, _iid, _qid = _python_sur_base(base_db, _SEED_FICHE).split()
    nav.naviguer(f"{base}/missions/{mid}")
    assert "E2E onglet resté" in nav.texte()
    _sans_erreur(nav, "Ouvrir la mission")
    assert _python_sur_base(base_db, _SUPPRIMER, "mission", mid) == "ok"

    nav.cliquer_et_attendre("a[href$='/interviews/new']")
    assert _statut_document(nav, f"/missions/{mid}/interviews/new") == 404
    # Comportement corrigé le 2026-10-14 (avant : JSON brut affiché).
    _verifier_page_introuvable(
        nav, base, "Cette mission n'existe plus. Elle a peut-être été supprimée depuis un autre onglet.")


# --------------------------------------------------------------------------- #
# 4. Entretien guidé : répondre, recharger, relire, terminer
# --------------------------------------------------------------------------- #
_SEED_GUIDE = r"""
from app.db import SessionLocal, init_db
from app.models import Mission, Question, Theme, Trame
init_db()
db = SessionLocal()
m = Mission(name="E2E entretien guidé")
db.add(m); db.flush()
tr = Trame(mission_id=m.id); db.add(tr); db.flush()
th = Theme(trame_id=tr.id, title="Gouvernance", position=0); db.add(th); db.flush()
q1 = Question(theme_id=th.id, label="Comment décidez-vous ?", position=0)
q2 = Question(theme_id=th.id, label="Qui arbitre les conflits ?", position=1)
db.add_all([q1, q2]); db.commit()
print(m.id, q1.id, q2.id)
"""


def test_entretien_guide_repondre_recharger_relire_terminer(serveur, nav: Navigateur) -> None:
    """Entretien structuré créé par l'écran, une réponse tapée au clavier
    (autosave htmx sur `keyup`), l'écran RECHARGÉ relit la réponse, « Terminer »
    montre la couverture (1 / 2) et la question non couverte, puis « Marquer
    l'entretien terminé » ramène à la mission où l'entretien est Terminé."""
    base, base_db = serveur
    mid, q1, q2 = _python_sur_base(base_db, _SEED_GUIDE).split()

    nav.naviguer(f"{base}/missions/{mid}")
    nav.cliquer_et_attendre("a[href$='/interviews/new']")
    nav.remplir("input[name=interviewee_name]", "Alice Guidée")
    nav.cliquer_et_attendre("form[action$='/interviews'] button[type=submit]")
    url_fiche = nav.url()
    iid = url_fiche.split("?")[0].rstrip("/").split("/")[-1]
    assert iid.isdigit(), url_fiche
    assert "Comment décidez-vous ?" in nav.texte() and "Qui arbitre les conflits ?" in nav.texte()
    assert "0/2 répondues" in nav.texte(), nav.texte()
    _sans_erreur(nav, "Créer l'entretien guidé")

    reponse = "On décide en comité mensuel, sans règle écrite."
    champ = f"textarea[hx-post='/interviews/{iid}/answers/{q1}']"
    _saisir_au_clavier(nav, champ, reponse)
    # L'indicateur VISIBLE de la question passe à « Répondue » et la couverture à 1/2.
    _attendre_visible(nav, f"#status-{q1}", "Répondue")
    _attendre_visible(nav, "#coverage", "1/2 répondues")
    _sans_erreur(nav, "Autosave de la réponse")

    # Rechargement complet : la réponse est relue À L'ÉCRAN (valeur du champ ; le
    # texte d'un textarea n'est pas dans innerText), avec statut et couverture.
    nav.naviguer(url_fiche)
    assert nav.evaluer(f"document.querySelector({json.dumps(champ)}).value") == reponse
    texte = nav.texte()
    assert "1/2 répondues" in texte, texte
    assert "répondue" in texte.lower()  # badge en majuscules à l'écran (text-transform)
    autre = f"textarea[hx-post='/interviews/{iid}/answers/{q2}']"
    assert nav.evaluer(f"document.querySelector({json.dumps(autre)}).value") == ""
    _sans_erreur(nav, "Recharger la fiche")

    # « Terminer » : récapitulatif de couverture.
    nav.cliquer_et_attendre("a[href$='/finish']")
    assert nav.url().endswith(f"/interviews/{iid}/finish"), nav.url()
    texte = nav.texte()
    assert "1 / 2 questions répondues" in texte, texte
    assert "Questions non couvertes (1)" in texte, texte
    assert "Qui arbitre les conflits ?" in texte and "à poser" in texte.lower()
    assert "Comment décidez-vous ?" not in texte.split("Questions non couvertes")[1]
    _sans_erreur(nav, "Récapitulatif de fin")

    nav.cliquer_et_attendre(f"form[action='/interviews/{iid}/finish'] button[type=submit]")
    assert nav.url().rstrip("/").endswith(f"/missions/{mid}"), nav.url()
    texte = nav.texte()
    assert "Alice Guidée" in texte and "terminé" in texte.lower(), texte
    assert "Continuer la saisie" not in texte, "l'entretien terminé propose encore de continuer"
    assert "1/2" in texte, texte
    _sans_erreur(nav, "Marquer terminé")


# --------------------------------------------------------------------------- #
# 5. Autosave : fiche supprimée / session expirée pendant la saisie (PB, PC)
# --------------------------------------------------------------------------- #
_MSG_FICHE_INTROUVABLE = (
    "⚠ fiche introuvable — rien n'est enregistré. Copiez votre texte avant de quitter.")


def _texte_slot(nav: Navigateur, selecteur: str) -> str:
    return nav.evaluer(
        "(function(){var e=document.querySelector(" + json.dumps(selecteur) + ");"
        " return e ? e.innerText : null;})()") or ""


def test_autosave_fiche_supprimee_message_explicite(serveur, nav: Navigateur) -> None:
    """Fiche supprimée ailleurs, saisie dans l'ancien onglet : le message
    explicite apparaît dans le slot de statut et RESTE (> 3 s)."""
    base, base_db = serveur
    _mid, iid, qid = _python_sur_base(base_db, _SEED_FICHE).split()
    nav.naviguer(f"{base}/interviews/{iid}")
    _sans_erreur(nav, "Ouvrir la fiche")
    assert _python_sur_base(base_db, _SUPPRIMER, "entretien", iid) == "ok"

    champ = f"textarea[hx-post='/interviews/{iid}/answers/{qid}']"
    slot = f"#status-{qid}"
    saisie = "Texte à ne pas perdre"
    _saisir_au_clavier(nav, champ, saisie)
    _attendre_visible(nav, slot, _MSG_FICHE_INTROUVABLE)
    debut = time.monotonic()
    # Toujours là après plus de 3 s (l'ancien message d'échec s'effaçait à 3 s) :
    # polling jusqu'à 4,5 s, le message doit être présent à CHAQUE lecture.
    while time.monotonic() - debut < 4.5:
        assert _MSG_FICHE_INTROUVABLE in _texte_slot(nav, slot), "message effacé"
        time.sleep(0.3)
    assert _MSG_FICHE_INTROUVABLE in nav.texte()
    assert nav.evaluer(f"document.querySelector({json.dumps(champ)}).value") == saisie
    assert nav.evaluer(
        f"document.querySelector({json.dumps(slot + ' .autosave-err')}).getAttribute('aria-live')") == "polite"


def test_autosave_session_expiree_propose_la_reconnexion(serveur, nav: Navigateur) -> None:
    """Cookie de session effacé pendant la saisie : 401 -> message avec un lien
    de reconnexion visible, cliquable, vers /connexion dans un NOUVEL onglet."""
    base, base_db = serveur
    _mid, iid, qid = _python_sur_base(base_db, _SEED_FICHE).split()
    nav.poser_entetes({})  # visiteur anonyme : seul le cookie authentifie
    nav.naviguer(f"{base}/connexion")
    nav.remplir("#mot_de_passe", os.environ["APP_AUTH_PASSWORD"])
    nav.cliquer_et_attendre("form[action='/connexion'] button[type=submit]")
    nav.naviguer(f"{base}/interviews/{iid}")
    assert "Comment décidez-vous ?" in nav.texte()
    _sans_erreur(nav, "Ouvrir la fiche par cookie")

    nav._run(nav._cmd("Network.deleteCookies", name="app_session", url=base + "/"))
    assert not _cookie_de_session(nav, base)
    champ = f"textarea[hx-post='/interviews/{iid}/answers/{qid}']"
    slot = f"#status-{qid}"
    saisie = "Saisie avant expiration"
    _saisir_au_clavier(nav, champ, saisie)
    _attendre_visible(nav, slot, "session expirée — reconnectez-vous")
    lien = f"{slot} a"
    infos = nav.evaluer(
        "(() => { const a = document.querySelector(" + json.dumps(lien) + "); if (!a) return null;"
        " const r = a.getBoundingClientRect();"
        " return {visible: r.width > 0 && r.height > 0, href: a.getAttribute('href'),"
        " target: a.getAttribute('target'), rel: a.getAttribute('rel')}; })()")
    assert infos and infos["visible"] is True, infos
    assert infos["href"] == "/connexion" and infos["target"] == "_blank", infos
    assert "noopener" in infos["rel"], infos
    # Le message persiste au-delà de 3 s et la saisie de l'onglet courant reste.
    fin = time.monotonic() + 3.5  # au-delà de l'effacement à 3 s des erreurs non persistantes
    while time.monotonic() < fin:
        assert "session expirée — reconnectez-vous" in _texte_slot(nav, slot), "message effacé"
        time.sleep(0.2)
    assert nav.evaluer(f"document.querySelector({json.dumps(champ)}).value") == saisie
    assert nav.url().endswith(f"/interviews/{iid}"), nav.url()
