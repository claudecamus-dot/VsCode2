"""Authentification de l'application — arbitrage utilisateur du 2026-09-19.

Jusqu'ici l'app n'en portait AUCUNE : `app/csrf.py` et `app/entetes_securite.py`
le disaient noir sur blanc (« usage local prévu », « l'authentification n'est pas
traitée ici : c'est un arbitrage utilisateur »). L'arbitrage est tombé — la cible
d'usage devient des **clients externes** — et les 117 routes enregistrées
(78 mutantes) servent des enregistrements d'entretiens et des transcriptions de
clients réels. Un hébergement sans authentification n'est pas un déploiement,
c'est une fuite mieux localisée.

Choix de mécanisme, pour CE dépôt et pas ailleurs :

- **Middleware ASGI global, défaut FERMÉ**, dans le même patron que
  `csrf.verifier_origine` et `entetes_securite.entetes_securite` (fonctions
  `async def (request, call_next)` empilées dans `main.py`). PAS un
  `Depends(...)` à poser sur 117 routes : une énumération qu'on oublie
  d'étendre laisse ouvert le prochain routeur ajouté. Ici, tout ce qui n'est
  pas dans `CHEMINS_PUBLICS`/`PREFIXES_PUBLICS` est fermé, y compris les 404.
- **Cookie de session signé HMAC-SHA256** (stdlib — aucune dépendance ajoutée,
  cohérent avec un `requirements.txt` épinglé). L'app est servie en
  HTML/Jinja/HTMX à un navigateur, avec des FORMULAIRES et des navigations :
  un jeton d'en-tête seul ne peut pas porter une navigation.
- **`Authorization: Bearer <mot de passe>`** accepté en plus, pour les clients
  non-navigateur (scripts internes, tests) — même secret, donc aucune surface
  supplémentaire.
- **Mot de passe absent => 503, jamais ouverture.** Une garde qui s'efface
  quand sa configuration manque ne garde rien. Le message dit quoi poser.

Le secret de signature dérive du mot de passe : changer `APP_AUTH_PASSWORD`
invalide toutes les sessions, ce qui est le comportement attendu d'une
révocation.

Ce que ce module NE fait PAS (lots suivants, délibérément hors périmètre) :
multi-utilisateur/rôles, journalisation des accès, chiffrement au repos, conteneur/hébergeur.

Ordre d'empilement (`main.py`) : `entetes_securite` s'exécute AVANT, donc la
réponse 401 porte les en-têtes de sécurité ; `csrf.verifier_origine` s'exécute
APRÈS, donc POST /connexion reste soumis au contrôle d'origine.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .templating import templates

COOKIE = "app_session"
# 12 h : une journée d'entretiens sans redemander, pas une session éternelle.
DUREE_S = 12 * 3600

# Limitation de débit de POST /connexion : au-delà de MAX_ECHECS mots de passe
# faux depuis une même adresse dans FENETRE_ECHECS_S, la connexion est refusée
# (429) jusqu'à expiration de la fenêtre. En mémoire, par processus : borne le
# brute-force en ligne, ne remplace pas un WAF.
MAX_ECHECS = 5
FENETRE_ECHECS_S = 15 * 60
_echecs: dict[str, list[float]] = {}
_verrou_echecs = threading.Lock()


def _cookie_secure(request: Request) -> bool:
    """`Secure` sur le cookie de session : forcé par APP_AUTH_COOKIE_SECURE=1,
    interdit par =0, sinon DÉTECTÉ (requête HTTPS, directe ou derrière un proxy
    qui pose X-Forwarded-Proto). Le http://127.0.0.1 local reste sans `Secure`."""
    force = os.environ.get("APP_AUTH_COOKIE_SECURE")
    if force == "1":
        return True
    if force == "0":
        return False
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return (proto or request.url.scheme) == "https"


def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


def _echecs_recents(cle: str, maintenant: float) -> list[float]:
    recents = [t for t in _echecs.get(cle, []) if maintenant - t < FENETRE_ECHECS_S]
    if recents:
        _echecs[cle] = recents
    else:
        _echecs.pop(cle, None)
    return recents


def reinitialiser_echecs() -> None:
    with _verrou_echecs:
        _echecs.clear()


# Liste EXPLICITE et courte des chemins publics. Tout le reste est fermé par
# défaut — c'est cette asymétrie qui fait la sûreté du dispositif.
CHEMINS_PUBLICS = ("/connexion",)
PREFIXES_PUBLICS = ("/static/",)


def mot_de_passe() -> str | None:
    """Lu à CHAQUE requête (pas figé à l'import) : un test, un rechargement de
    `.env` ou une rotation de secret doivent prendre effet sans réimporter le
    module."""
    valeur = os.environ.get("APP_AUTH_PASSWORD") or ""
    return valeur or None


def _cle(secret: str) -> bytes:
    return hashlib.sha256(("app-session/" + secret).encode("utf-8")).digest()


def signer(secret: str, expiration: int) -> str:
    charge = str(expiration).encode("ascii")
    signature = hmac.new(_cle(secret), charge, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(charge).decode("ascii").rstrip("=")
        + "."
        + base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    )


def _debase(valeur: str) -> bytes:
    return base64.urlsafe_b64decode(valeur + "=" * (-len(valeur) % 4))


def jeton_valide(secret: str, jeton: str | None) -> bool:
    if not jeton or "." not in jeton:
        return False
    partie_charge, _, partie_signature = jeton.partition(".")
    try:
        charge = _debase(partie_charge)
        signature = _debase(partie_signature)
        expiration = int(charge.decode("ascii"))
    except Exception:
        return False
    attendue = hmac.new(_cle(secret), charge, hashlib.sha256).digest()
    # compare_digest : comparaison de signature à temps constant.
    if not hmac.compare_digest(attendue, signature):
        return False
    return time.time() < expiration


def egal(propose: str, secret: str) -> bool:
    """Comparaison a temps constant sur les OCTETS.

    `hmac.compare_digest` sur deux `str` LEVE TypeError des qu'un caractere
    sort de l'ASCII (revue adversariale de ce lot) : un mot de passe accentue,
    ou un en-tete Authorization contenant un octet >127 (les valeurs d'en-tete
    sont decodees en latin-1 par h11), transformait le refus en 500. On encode
    donc en UTF-8 des deux cotes.
    """
    return hmac.compare_digest(propose.encode("utf-8"), secret.encode("utf-8"))


def _authentifie(request: Request, secret: str) -> bool:
    entete = request.headers.get("authorization") or ""
    if entete.lower().startswith("bearer ") and egal(entete[7:].strip(), secret):
        return True
    return jeton_valide(secret, request.cookies.get(COOKIE))


def _public(chemin: str) -> bool:
    # Un `..` dans le chemin ne peut JAMAIS rendre public : sans cette ligne,
    # `/static/..%2fmissions` (decode en `/static/../missions` dans
    # `request.url.path`) passait le prefixe public. Starlette ne servait rien
    # d'utile derriere, mais une garde ne se repose pas sur ce que le maillon
    # suivant refuse.
    if ".." in chemin:
        return False
    return chemin in CHEMINS_PUBLICS or chemin.startswith(PREFIXES_PUBLICS)


def _veut_du_html(request: Request) -> bool:
    return "text/html" in (request.headers.get("accept") or "")


async def exiger_authentification(request: Request, call_next):
    chemin = request.url.path
    if _public(chemin):
        return await call_next(request)
    secret = mot_de_passe()
    if secret is None:
        # Fermé, pas ouvert : c'est exactement le cas qu'un défaut fermé couvre.
        return JSONResponse(
            {
                "error": "Authentification non configurée : poser "
                "APP_AUTH_PASSWORD (variable d'environnement ou .env) avant "
                "d'exposer l'application."
            },
            status_code=503,
        )
    if _authentifie(request, secret):
        return await call_next(request)
    if _veut_du_html(request):
        # Navigation navigateur : on montre la page de connexion, mais sous un
        # 401 — un 200 mentirait aux tests comme aux sondes.
        return templates.TemplateResponse(
            request, "connexion.html", {"erreur": None}, status_code=401
        )
    return JSONResponse({"error": "Authentification requise."}, status_code=401)


router = APIRouter()


@router.get("/connexion", response_class=HTMLResponse)
def page_connexion(request: Request):
    return templates.TemplateResponse(request, "connexion.html", {"erreur": None})


@router.post("/connexion")
def connexion(request: Request, mot_de_passe_saisi: str = Form(alias="mot_de_passe")):
    secret = mot_de_passe()
    if secret is None:
        return JSONResponse(
            {"error": "Authentification non configurée (APP_AUTH_PASSWORD)."},
            status_code=503,
        )
    cle, maintenant = _client(request), time.time()
    with _verrou_echecs:
        bloque = len(_echecs_recents(cle, maintenant)) >= MAX_ECHECS
    if bloque:
        return templates.TemplateResponse(
            request,
            "connexion.html",
            {"erreur": "Trop de tentatives. Réessayez dans quelques minutes."},
            status_code=429,
        )
    if not egal(mot_de_passe_saisi, secret):
        with _verrou_echecs:
            _echecs_recents(cle, maintenant)
            _echecs.setdefault(cle, []).append(maintenant)
        return templates.TemplateResponse(
            request,
            "connexion.html",
            {"erreur": "Mot de passe incorrect."},
            status_code=401,
        )
    with _verrou_echecs:
        _echecs.pop(cle, None)
    reponse = RedirectResponse("/", status_code=303)
    reponse.set_cookie(
        COOKIE,
        signer(secret, int(time.time()) + DUREE_S),
        max_age=DUREE_S,
        httponly=True,
        samesite="lax",
        # `secure` seulement derrière HTTPS : en local (http://127.0.0.1) un
        # cookie `Secure` ne serait jamais renvoyé et l'app deviendrait
        # inutilisable. Le lot « hébergement » posera APP_AUTH_COOKIE_SECURE=1.
        secure=_cookie_secure(request),
    )
    return reponse


@router.post("/deconnexion")
def deconnexion():
    reponse = RedirectResponse("/connexion", status_code=303)
    reponse.delete_cookie(COOKIE)
    return reponse
