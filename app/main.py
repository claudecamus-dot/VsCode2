"""Point d'entrée FastAPI — Interview-to-Deck (incrément 1)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

# Charge un éventuel fichier .env à la racine du projet (clé OPENAI_API_KEY /
# MISTRAL_API_KEY selon AI_PROVIDER, SYNTHESE_MODEL, …) avant tout import qui
# lit l'environnement. Sans effet si le paquet python-dotenv ou le fichier
# .env sont absents.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR.parent / ".env")
except ModuleNotFoundError:
    pass

from .csrf import verifier_origine  # noqa: E402
from .db import init_db  # noqa: E402
from .routers import agents, entretiens, export, interviews, missions, synthese, trames  # noqa: E402
from .services import audio_transcribe  # noqa: E402
from .services.ai_common import warm_up_ollama  # noqa: E402
from .services.global_synthesis_job import reconcile_running_on_startup  # noqa: E402


def empreinte_code() -> str:
    """Empreinte du code python d'app/ : hash des (chemin, mtime_ns) de tous les
    .py. Sert la preuve de fraîcheur du serveur dev (diagnostic superviseur
    2026-07-23 : le --reload a servi plusieurs fois du code périmé — la preuve
    octets-du-statique ne couvrait pas le python)."""
    import hashlib

    h = hashlib.sha256()
    for p in sorted(BASE_DIR.rglob("*.py")):
        h.update(str(p.relative_to(BASE_DIR)).encode())
        h.update(str(p.stat().st_mtime_ns).encode())
    return h.hexdigest()[:16]


# Capturée à l'IMPORT (pas à la requête) : un worker périmé garde l'empreinte
# de SON chargement — c'est l'écart avec le disque qui prouve le stale. Un
# handler qui hasherait le disque à la requête servirait toujours du « frais ».
EMPREINTE_AU_CHARGEMENT = empreinte_code()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    reconcile_running_on_startup()
    try:
        audio_transcribe.warm_up()
    except Exception:
        pass  # le premier enregistrement réel retentera et remontera une erreur normale
    try:
        warm_up_ollama()
    except Exception:
        pass  # le premier appel IA réel retentera et remontera une erreur normale
    yield


app = FastAPI(title="Interview-to-Deck", lifespan=lifespan)
app.middleware("http")(verifier_origine)


@app.exception_handler(Exception)
async def erreur_inattendue(request: Request, exc: Exception) -> JSONResponse:
    """Filet de dernier recours : aucune route ne devrait laisser fuiter une
    exception non prévue, mais quand une régression en laisse échapper une
    (cas non couvert par un try/except ciblé), ceci évite qu'elle plante le
    worker ou renvoie une trace brute au client — 500 générique, détail
    complet journalisé côté serveur seulement (constat audit-technique
    robustesse VSCode2, 2026-09-04 : aucun `@app.exception_handler` ni
    middleware d'erreur n'existait). Sans effet sur les `HTTPException`
    (gérées par un handler Starlette plus spécifique, jamais atteintes ici)."""
    logger.exception("Exception non gérée sur %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Erreur interne inattendue."})


@app.get("/__fraicheur")
def fraicheur() -> dict:
    """Preuve de fraîcheur du code SERVI : l'empreinte capturée à l'import.
    Le vérifieur (serveur-dev.ps1, playbooks) recompile l'empreinte du DISQUE
    et compare — égalité = le python servi est bien celui du disque. Outil
    interne de dev, lecture seule, n'expose que des mtimes hashés."""
    return {"empreinte": EMPREINTE_AU_CHARGEMENT}
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

app.include_router(entretiens.router)
app.include_router(missions.router)
app.include_router(trames.router)
app.include_router(interviews.router)
app.include_router(synthese.router)
app.include_router(export.router)
app.include_router(agents.router)
