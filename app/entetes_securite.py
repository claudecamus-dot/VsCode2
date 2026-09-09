"""En-têtes HTTP de sécurité (finding audit-technique securite:critique du
2026-09-04, `.claude/audits/VSCode2.json` côté hub) : « Aucune authentification
ni en-tête de sécurité HTTP : FastAPI() instancié sans le moindre
add_middleware, donc ni CSP, ni X-Frame-Options, ni X-Content-Type-Options ».

Ce que ce module ferme, et ce qu'il NE ferme PAS — la distinction compte, une
protection surestimée est pire qu'absente :

- FERMÉ : le chargement d'une ressource EXTERNE. `default-src 'self'` +
  `connect-src 'self'` coupent le canal d'exfiltration (un `<img src>` ou un
  `fetch` vers un domaine tiers) qui transforme un contenu injecté en fuite de
  données d'entretien. L'app est offline-first (Ollama/Whisper locaux, htmx
  servi en local depuis 2026-07-22) : aucune ressource légitime n'est externe,
  la politique ne coûte donc rien au produit.
- FERMÉ : l'encadrement en iframe (`frame-ancestors 'none'` + X-Frame-Options),
  le sniffing de type (`nosniff`), le détournement de `<base>`, l'envoi d'un
  formulaire vers un tiers (`form-action 'self'`), les plugins (`object-src`).
- PAS FERMÉ : l'exécution d'un script INLINE injecté. Les écrans
  d'enregistrement portent des `<script>` inline volumineux et des attributs
  `style=` ; `'unsafe-inline'` est donc nécessaire tant qu'ils ne sont pas
  passés en fichiers `/static` ou en nonce. La CSP ici n'est PAS une protection
  XSS — la dire telle serait un faux sentiment de sécurité.

L'AUTHENTIFICATION, l'autre moitié du finding, n'est PAS traitée ici : l'app est
prévue en usage local mono-utilisateur, en poser une change le produit. C'est un
arbitrage utilisateur, pas une correction technique.

Ordre d'installation : ce middleware est ajouté APRÈS `csrf.verifier_origine`
dans `main.py`, donc il s'exécute AVANT lui (Starlette empile à l'envers) — les
en-têtes couvrent ainsi aussi la réponse 403 de la garde CSRF.
"""
from __future__ import annotations

CSP = (
    "default-src 'self'; "
    # 'unsafe-inline' : voir le docstring — les écrans d'enregistrement
    # dépendent de <script> inline et d'attributs style=.
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    # blob: — l'aperçu audio du magnétophone passe par URL.createObjectURL
    # (`capture.html`, `record*.html`) ; sans lui, la relecture d'un
    # enregistrement casse.
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

ENTETES = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # same-origin, PAS no-referrer (signalement utilisateur 2026-09-08 : 403
    # « Origine non autorisée » sur Supprimer, Démarrer, changement de mode —
    # tout ce qui est un FORMULAIRE). Sous `no-referrer`, la spec Fetch met
    # l'en-tête Origin à `null` sur les requêtes non-CORS (une soumission de
    # formulaire est une navigation), et retire le Referer : la garde CSRF
    # (`csrf.meme_origine`) ne voit alors plus aucune origine et refuse — alors
    # que les `fetch` (transcription, tranches) gardent leur Origin, d'où un
    # enregistrement qui marche et des boutons qui échouent. Reproduit en Edge
    # headless via CDP : `origin: null | referer: None -> 403`. `same-origin`
    # garde l'intention (les URL portent des identifiants de mission et
    # d'entretien : jamais de Referer vers un tiers) et laisse partir Origin et
    # Referer entre pages de l'app. Accepter `null` côté CSRF aurait ouvert
    # exactement ce que la garde ferme (formulaire cross-site depuis une origine
    # opaque). Test : tests/test_csrf.py.
    "Referrer-Policy": "same-origin",
}


async def entetes_securite(request, call_next):
    """Pose les en-têtes sur TOUTE réponse, y compris les erreurs.

    `setdefault` plutôt qu'écrasement : une route qui aurait une raison
    explicite de poser sa propre valeur garde la main — le middleware est un
    plancher, pas un plafond.
    """
    response = await call_next(request)
    for nom, valeur in ENTETES.items():
        response.headers.setdefault(nom, valeur)
    return response
