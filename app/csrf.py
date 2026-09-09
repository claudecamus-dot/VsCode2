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

import logging
import time
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

_METHODES_MUTANTES = {"POST", "PUT", "DELETE", "PATCH"}

# Journal des refus (signalement utilisateur 2026-09-08 : 403 sur « Supprimer »
# et « Démarrer » depuis un vrai navigateur, sans aucun moyen de savoir quelle
# origine avait été vue). Deux garde-fous posés par la revue adversariale du
# même jour :
# - il part sur le logger `uvicorn.error`, le seul qui ait un handler quand
#   l'app tourne sous uvicorn (le logger racine n'en a aucun : avec
#   `logging.getLogger(__name__)` la ligne tombait sur `logging.lastResort` —
#   sans niveau ni nom de logger, sur un flux différent de celui du serveur,
#   donc impossible à rapprocher de la requête refusée) ;
# - il est RÉGULÉ : un refus journalisé par `_INTERVALLE_JOURNAL_S` au plus,
#   les autres comptés puis annoncés au suivant, valeurs tronquées à
#   `_LARGEUR_MAX`. Sans ça, une page tierce qui boucle des POST vers
#   127.0.0.1 — tous refusés, donc « sans effet » — écrivait jusqu'à 16 Kio
#   par requête (taille max d'en-tête h11) dans un fichier jamais tourné, en
#   synchrone dans la boucle d'évènements : la garde transformait une attaque
#   bloquée en déni de service local.
# La réponse, elle, reste sobre : ces en-têtes ne regardent pas un tiers.
logger = logging.getLogger("uvicorn.error")
_INTERVALLE_JOURNAL_S = 5.0
_LARGEUR_MAX = 200


class _Regulateur:
    """État du journal des refus — UN par processus, volontairement global.
    Réguler par clé (origine, chemin) rendrait à l'utilisateur légitime son
    403 pendant un flot de refus tiers, mais rouvrirait l'amplification : un
    attaquant qui varie le chemin obtiendrait une ligne par requête (revue
    adversariale du 2026-09-09, A9 — écarté pour cette raison ; l'adresse
    client est journalisée pour distinguer les deux cas).

    `reinitialiser()` remet les DEUX compteurs à zéro. Pour exercer « l'intervalle
    est écoulé » en gardant le compte des refus tus, les tests posent
    `dernier = float("-inf")` (ou avancent l'horloge) — pas `reinitialiser()`."""

    def __init__(self) -> None:
        self.reinitialiser()

    def reinitialiser(self) -> None:
        self.dernier = float("-inf")
        self.tus = 0


regulateur = _Regulateur()


def _tronquer(valeur: str | None) -> str | None:
    if valeur is None or len(valeur) <= _LARGEUR_MAX:
        return valeur
    return valeur[:_LARGEUR_MAX] + f"…(+{len(valeur) - _LARGEUR_MAX} car.)"


def _journaliser_refus(request: Request) -> None:
    maintenant = time.monotonic()
    if maintenant - regulateur.dernier < _INTERVALLE_JOURNAL_S:
        regulateur.tus += 1
        return
    supplement = (
        f" — {regulateur.tus} refus non journalisé(s) depuis le précédent"
        if regulateur.tus else ""
    )
    # Chaque valeur d'origine distante est tronquée — le CHEMIN aussi : c'est
    # lui que le premier correctif laissait passer entier (revue du 2026-09-09,
    # A1 : 6 082 caractères mesurés sur un POST au chemin de 6 000).
    logger.warning(
        "CSRF : %s %s refusé — client=%r Host=%r Origin=%r Referer=%r%s",
        _tronquer(request.method), _tronquer(request.url.path),
        request.client.host if request.client else None,
        _tronquer(request.headers.get("host")),
        _tronquer(request.headers.get("origin")),
        _tronquer(request.headers.get("referer")),
        supplement,
    )
    regulateur.dernier = maintenant
    regulateur.tus = 0


def meme_origine(request: Request) -> bool:
    hote = request.headers.get("host")
    source = request.headers.get("origin") or request.headers.get("referer")
    if not hote or not source:
        return False
    return urlsplit(source).netloc == hote


async def verifier_origine(request: Request, call_next):
    if request.method not in _METHODES_MUTANTES or meme_origine(request):
        return await call_next(request)
    # Le refus DIT ce qu'il a comparé — dans le journal serveur, régulé.
    _journaliser_refus(request)
    return JSONResponse(
        {"error": "Origine non autorisée (protection CSRF)."}, status_code=403
    )
