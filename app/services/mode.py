"""Mode courant de l'outil (démo / réel), lu depuis le cookie `mode` posé par la
1ère page (`/`). Pendant Python du `mode.js` de VSCode1 — extrait ici (plutôt
qu'inline dans les routeurs) pour être testable unitairement.

`demo` => on ne voit et ne crée que des missions fictives (`is_demo=True`) ;
toute autre valeur, dont l'absence de cookie => mode réel (`is_demo=False`) — on
ne bascule JAMAIS en démo par défaut.
"""
from __future__ import annotations

from starlette.requests import Request

MODE_COOKIE = "mode"


def est_mode_demo(request: Request) -> bool:
    """True si le cookie `mode` vaut exactement `demo`, False sinon (réel par
    défaut, y compris cookie absent ou valeur inconnue)."""
    return request.cookies.get(MODE_COOKIE) == "demo"
