"""Point d'entrée FastAPI — Interview-to-Deck (incrément 1)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent

# Charge un éventuel fichier .env à la racine du projet (clé ANTHROPIC_API_KEY,
# SYNTHESE_MODEL, …) avant tout import qui lit l'environnement. Sans effet si le
# paquet python-dotenv ou le fichier .env sont absents.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR.parent / ".env")
except ModuleNotFoundError:
    pass

from .db import init_db  # noqa: E402
from .routers import agents, export, interviews, missions, synthese, trames  # noqa: E402
from .services import audio_transcribe  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    try:
        audio_transcribe.warm_up()
    except Exception:
        pass  # le premier enregistrement réel retentera et remontera une erreur normale
    yield


app = FastAPI(title="Interview-to-Deck", lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

app.include_router(missions.router)
app.include_router(trames.router)
app.include_router(interviews.router)
app.include_router(synthese.router)
app.include_router(export.router)
app.include_router(agents.router)


@app.get("/")
def home() -> RedirectResponse:
    return RedirectResponse("/missions")
