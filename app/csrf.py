"""Vérification d'origine anti-CSRF (finding audit-technique securite:critique,
2026-09-04, cible POST /missions/{id}/delete — 78 routes mutatives au total).

L'app ne porte AUCUNE authentification (usage local prévu), mais ça ne rend
pas le CSRF sans objet : une page web quelconque, ouverte dans un autre
onglet du même navigateur pendant que le serveur tourne sur 127.0.0.1, peut
déclencher une requête POST vers l'app — suppression de mission (avec son
audio), import destructif, etc. — sans que l'utilisateur l'ait voulue. Une
requête POST simple (formulaire, `fetch` sans en-tête custom) est une
« simple request » au sens CORS : aucun preflight ne l'empêche de partir.

Vérifie sur les méthodes qui MUTENT (POST/PUT/DELETE/PATCH) ; GET reste en
lecture, hors périmètre CSRF. FAIL-CLOSED sur Origin/Referer ABSENTS — même
arbitrage que sur VSCode1 (commit ef3ae7b, durci après revue adversariale) :
un navigateur envoie normalement Origin sur les méthodes mutantes, mais un
proxy d'entreprise ou une extension de confidentialité peut le retirer sur un
VRAI navigateur — exactement le vecteur CSRF que ce module existe pour
fermer. Un client non-navigateur légitime (curl, script interne) doit
envoyer Origin explicitement s'il appelle une route mutante ; le coût assumé
est de bloquer les rares navigateurs qui strippent les deux en-têtes.

Pendant de `app/src/csrf.js` sur VSCode1 (même famille de finding, même
patron `verifierOrigine`) — porté ici en middleware ASGI plutôt qu'Express.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

_METHODES_MUTANTES = {"POST", "PUT", "DELETE", "PATCH"}


def meme_origine(request: Request) -> bool:
    hote = request.headers.get("host")
    source = request.headers.get("origin") or request.headers.get("referer")
    if not hote or not source:
        return False
    return urlsplit(source).netloc == hote


async def verifier_origine(request: Request, call_next):
    if request.method not in _METHODES_MUTANTES or meme_origine(request):
        return await call_next(request)
    return JSONResponse(
        {"error": "Origine non autorisée (protection CSRF)."}, status_code=403
    )
