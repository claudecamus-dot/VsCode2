"""États limites vus par l'utilisateur (comité e2e du 2026-10-14, triage
`.claude/triage/2026-10-14-e2e-point-de-vue-utilisateur.md`) : ce que les
parcours nominaux ne regardent pas.

1. la session SURVIT à un rechargement (cookie `app_session` HttpOnly lu par CDP) ;
2. un 401 sous HTMX (session expirée, action hors autosave) : que VOIT-on ?
3. un double envoi du formulaire « Créer » (deux requestSubmit() à 50 ms d écart, latence réseau émulée) :
   une seule mission (xfail strict : bug d'app avéré) ;
4. base neuve : /missions montre un état vide + lien de création, console propre ;
5. connexion et POST via `http://localhost:<port>` (le serveur écoute en IPv4).

Un test `xfail(strict=True)` documente un défaut de l'APP (jamais corrigé ici) :
il devient rouge dès que le défaut est réparé, signal pour retirer le marqueur.
Chaque test a son serveur et sa base (`C:/tmp/e2e_limites/<test>.db`).
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
    import websockets  # noqa: F401
except ModuleNotFoundError:
    if _OBLIGATOIRE:
        raise RuntimeError("E2E_OBLIGATOIRE est posé mais `websockets` est absent") from None
    pytest.skip("websockets (uvicorn[standard]) requis", allow_module_level=True)

from e2e_serveur import serveur_uvicorn  # noqa: E402
from navigateur_cdp import Navigateur, trouver_navigateur  # noqa: E402

_NAVIGATEUR = trouver_navigateur()
if _NAVIGATEUR is None and _OBLIGATOIRE:
    raise RuntimeError("E2E_OBLIGATOIRE est posé mais aucun navigateur Chromium n'est trouvé")
pytestmark = pytest.mark.skipif(_NAVIGATEUR is None, reason="aucun navigateur Chromium trouvé")

_RACINE = Path(__file__).resolve().parents[1]
_DOSSIER_DB = (
    Path("C:/tmp/e2e_limites") if os.name == "nt" else Path(tempfile.gettempdir()) / "e2e_limites"
)


@pytest.fixture
def serveur(request):
    """Serveur + base NEUVE par test. Rend `(base_url, chemin_db)`."""
    _DOSSIER_DB.mkdir(parents=True, exist_ok=True)
    db = _DOSSIER_DB / (request.node.name + ".db")
    for suffixe in ("", "-wal", "-shm"):
        Path(str(db) + suffixe).unlink(missing_ok=True)
    journal = Path(tempfile.mkdtemp(prefix="e2e-lim-"))
    try:
        with serveur_uvicorn(
            journal, APP_DB_PATH=str(db), OLLAMA_HOST="http://127.0.0.1:9"
        ) as base:
            yield base, db
    finally:
        shutil.rmtree(journal, ignore_errors=True)


@pytest.fixture
def nav():
    profil = Path(tempfile.mkdtemp(prefix="e2e-nav-"))
    navigateur = None
    try:
        navigateur = Navigateur(_NAVIGATEUR, profil / "profil")
        yield navigateur
    finally:
        if navigateur is not None:
            navigateur.fermer()
        shutil.rmtree(profil, ignore_errors=True)


def _python_sur_base(db: Path, code: str) -> str:
    env = dict(os.environ, APP_DB_PATH=str(db), PYTHONUTF8="1")
    res = subprocess.run([sys.executable, "-c", code], cwd=_RACINE, env=env,
                         capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return res.stdout.strip().splitlines()[-1]


_COMPTE = (
    "from app.db import SessionLocal\nfrom app.models import Mission\n"
    "db = SessionLocal()\n"
    "print(db.query(Mission).filter(Mission.name == {nom!r}).count())\n"
)
_SEED = (
    "from app.db import SessionLocal\nfrom app.models import Mission\n"
    "db = SessionLocal()\nm = Mission(name='Mission limites'); db.add(m); db.commit()\n"
    "print(m.id)\n"
)


class DefautApp(AssertionError):
    """Le DÉFAUT d'app documenté (et lui seul). `xfail(raises=DefautApp)` : tout autre
    échec (timeout d'attente, formulaire introuvable, erreur CDP) reste ROUGE au lieu de
    passer pour le bug attendu."""


def _attendre(condition, message: str, delai_s: float = 10.0, pas_s: float = 0.2):
    fin = time.monotonic() + delai_s
    while time.monotonic() < fin:
        valeur = condition()
        if valeur:
            return valeur
        time.sleep(pas_s)
    raise AssertionError(message)


def _se_connecter(nav: Navigateur, base: str) -> None:
    """Connexion par le FORMULAIRE, navigateur anonyme (sans le Bearer du harnais)."""
    nav.poser_entetes({})
    nav.naviguer(base + "/connexion")
    nav.remplir("#mot_de_passe", os.environ["APP_AUTH_PASSWORD"])
    nav.cliquer_et_attendre("form[action='/connexion'] button[type=submit]")
    assert nav.cookies(base + "/"), "aucun cookie posé par la connexion"


def _session(nav: Navigateur, base: str) -> list[dict]:
    return [c for c in nav.cookies(base + "/") if c["name"] == "app_session"]


# --------------------------------------------------------------------------- #
def test_la_session_survit_au_rechargement(serveur, nav: Navigateur) -> None:
    base, _ = serveur
    _se_connecter(nav, base)
    avant = _session(nav, base)
    assert len(avant) == 1 and avant[0]["httpOnly"], avant
    assert "app_session" not in nav.evaluer("document.cookie"), "cookie lisible par le JS"

    nav.naviguer(base + "/missions")
    nav.evaluer("(location.reload(), true)")
    _attendre(lambda: nav.evaluer("document.readyState") == "complete", "rechargement non fini")
    nav.naviguer(base + "/missions")  # rechargement dur : le cookie seul authentifie
    assert not nav.evaluer("!!document.querySelector('.login-card')"), "renvoyé à la connexion"
    assert "/connexion" not in nav.url()
    assert "Missions" in nav.texte()
    apres = _session(nav, base)
    assert len(apres) == 1 and apres[0]["value"] == avant[0]["value"], "cookie remplacé"


@pytest.mark.xfail(
    strict=True,
    raises=DefautApp,
    reason="BUG APP : 401 JSON sous htmx, aucun message visible (app/auth.py l.199-204 ; "
    "autosave.js ne traite que les erreurs d'autosave)",
)
def test_401_sous_htmx_l_utilisateur_est_averti(serveur, nav: Navigateur) -> None:
    """Session expirée puis clic sur « Exécuter » (hx-post hors autosave, page
    Agents) : l'utilisateur doit VOIR qu'il faut se reconnecter.

    DEFAUT CONSTATE : `exiger_authentification` répond un 401 JSON (htmx envoie
    Accept */*, pas text/html) ; htmx ne swappe pas les 4xx et `autosave.js`
    n'écoute que les erreurs d'autosave : le clic n'affiche AUCUN message."""
    base, db = serveur
    mid = _python_sur_base(db, _SEED)
    _se_connecter(nav, base)
    nav.naviguer(f"{base}/missions/{mid}/agents")
    sel = "form[hx-post*='/agents/'] button"
    assert nav.evaluer(f"!!document.querySelector({json.dumps(sel)})"), "aucun agent listé"
    nav.supprimer_cookie("app_session", base + "/")
    assert not _session(nav, base)
    nav.cliquer(sel)

    def refuse() -> bool:
        nav.drainer()
        return any(r["status"] == 401 and "/invoke" in r["url"] for r in nav.reponses)

    # La requête est bien partie et refusée : sinon le test ne prouve rien.
    _attendre(refuse, "la requête hx-post n'a pas reçu de 401")
    # État, pas sommeil : on laisse à l'app la chance d'afficher un message (délai
    # borné) ; sans message, l'échec reste « 401 silencieux ».
    mots = ("session expir", "reconnect", "authentification requise")
    try:
        _attendre(lambda: any(m in nav.texte().lower() for m in mots), "", delai_s=3.0)
    except AssertionError:
        pass
    visible = nav.texte().lower()
    if not any(m in visible for m in mots):
        raise DefautApp(f"401 silencieux : rien d explicite à l écran : {visible[:300]!r}")


# Écart entre les deux envois (un double-clic humain tient en < 200 ms) et latence
# réseau émulée. MESURÉ le 2026-10-16 : sans latence, 0 ms d'écart -> 1 mission (le
# navigateur annule la 1re navigation) et 50 ms -> 2 missions sur 2 runs de 3 (course
# de timing) ; une fois le 2e envoi gaté sur la base, 0 sur 4 (page déjà partie).
# AVEC 600 ms de latence : 2 missions sur 3 runs de 3 — le défaut est déterministe.
_ECART_DOUBLE_ENVOI_MS = 50
_LATENCE_MS = 600


@pytest.mark.xfail(
    strict=True,
    raises=DefautApp,
    reason="BUG APP : `missions/form.html` ne désactive pas « Créer » ni ne garde la "
    "soumission ; deux envois rapprochés (< 200 ms) créent 2 missions (reproduit à "
    "chaque run, voir le test)",
)
def test_double_clic_sur_creer_une_seule_mission(serveur, nav: Navigateur) -> None:
    """Double envoi DÉTERMINISTE côté page : un seul `evaluate` lance deux
    `requestSubmit()` du même formulaire à `_ECART_DOUBLE_ENVOI_MS` d'écart
    (timer de la page, pas de course souris) sous latence réseau émulée : la
    page est donc encore sur le formulaire au 2e envoi. On attend
    ensuite un état stable (page chargée hors du formulaire, compte en base
    inchangé sur trois lectures) avant de compter."""
    base, db = serveur
    _se_connecter(nav, base)
    nav.naviguer(base + "/missions/new")
    nav.remplir("input[name=name]", "Mission double clic")
    f_js = "document.querySelector(\"form[action='/missions']\")"
    assert nav.evaluer("!!" + f_js), "formulaire de création introuvable"

    # Latence réseau émulée : la réponse du 1er envoi tarde, la page reste sur le
    # formulaire pendant que le 2e part (c'est la situation du double-clic réel).
    nav.emuler_latence(_LATENCE_MS)
    nav.evaluer("(function(){var f=" + f_js + "; f.requestSubmit();"
                " setTimeout(function(){f.requestSubmit();}, " + str(_ECART_DOUBLE_ENVOI_MS) + ");"
                " return true;})()")

    def compte() -> int:
        return int(_python_sur_base(db, _COMPTE.format(nom="Mission double clic")))

    # Page arrivée (plus sur le formulaire, chargement terminé) ET compte stable.
    _attendre(lambda: nav.evaluer("document.readyState") == "complete"
              and not nav.url().rstrip("/").endswith("/missions/new"),
              "la création n'a jamais abouti")
    lectures: list[int] = []

    def stable() -> bool:
        lectures.append(compte())
        return len(lectures) >= 3 and len(set(lectures[-3:])) == 1

    _attendre(stable, "le nombre de missions ne se stabilise pas", delai_s=30.0, pas_s=0.3)
    nav.drainer()
    nb = lectures[-1]
    if nb != 1:
        raise DefautApp(f"{nb} missions créées par un double envoi")


def test_base_neuve_etat_vide_et_lien_de_creation(serveur, nav: Navigateur) -> None:
    base, _ = serveur
    _se_connecter(nav, base)
    nav.naviguer(base + "/missions")
    assert "Aucune mission pour le moment" in nav.texte()
    assert nav.evaluer("document.querySelector('.empty') !== null")
    nav.cliquer_et_attendre(".empty a[href='/missions/new']")
    assert nav.url().endswith("/missions/new"), nav.url()
    nav.naviguer(base + "/missions")
    nav.drainer()
    assert not nav.erreurs(), nav.erreurs()
    assert not nav.exceptions, nav.exceptions
    assert not nav.erreurs_cdp, nav.erreurs_cdp


def test_connexion_et_post_depuis_localhost(serveur, nav: Navigateur) -> None:
    """Le serveur écoute 127.0.0.1 ; `localhost` peut résoudre en ::1 d'abord,
    Chromium retombe alors sur IPv4. Origin/Referer deviennent
    http://localhost:<port> : le contrôle CSRF doit l'accepter."""
    base, db = serveur
    local = base.replace("127.0.0.1", "localhost")
    assert local != base
    nav.poser_entetes({})
    nav.naviguer(local + "/connexion")
    nav.remplir("#mot_de_passe", os.environ["APP_AUTH_PASSWORD"])
    nav.cliquer_et_attendre("form[action='/connexion'] button[type=submit]")
    assert "Origine non autorisée" not in nav.texte()
    assert _session(nav, local), "pas de cookie de session pour localhost"
    nav.naviguer(local + "/missions")
    assert not nav.evaluer("!!document.querySelector('.login-card')")
    assert "Missions" in nav.texte()
    nav.cliquer_et_attendre("a[href='/missions/new']")  # action POST réelle ensuite
    nav.remplir("input[name=name]", "Mission localhost")
    nav.cliquer_et_attendre("form[action='/missions'] button[type=submit]")
    assert "Origine non autorisée" not in nav.texte(), "403 CSRF rendu depuis localhost"
    assert "Mission localhost" in nav.texte()
    assert int(_python_sur_base(db, _COMPTE.format(nom="Mission localhost"))) == 1
    nav.drainer()
    assert not nav.erreurs(), nav.erreurs()
