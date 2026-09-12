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
from .entetes_securite import entetes_securite  # noqa: E402
from .routers import (  # noqa: E402
    agents,
    entretiens,
    export,
    interviews,
    interviews_audio,
    missions,
    synthese,
    trames,
)
from .services import audio_transcribe  # noqa: E402
from .services.ai_common import warm_up_ollama  # noqa: E402
from .services.audio_file_jobs import (  # noqa: E402
    reconcile_running_on_startup as reconcile_audio_file_jobs_on_startup,
)
from .services.global_synthesis_job import reconcile_running_on_startup  # noqa: E402
from .services.interview_segment_jobs import (  # noqa: E402
    reconcile_running_on_startup as reconcile_segment_jobs_on_startup,
)


def empreinte_code() -> str:
    """Empreinte du code python d'app/ : hash des (chemin, CONTENU) de tous les
    .py. Sert la preuve de fraîcheur du serveur dev (diagnostic superviseur
    2026-07-23 : le --reload a servi plusieurs fois du code périmé — la preuve
    octets-du-statique ne couvrait pas le python).

    Le CONTENU, et NON PLUS le `mtime_ns` (2026-09-10) — l'horodatage a été
    remplacé, pas complété ; c'est toute la propriété gagnée. L'horodatage rendait
    l'empreinte sensible à des gestes qui ne changent RIEN au code servi : un
    `touch`, une copie de travail, un formateur qui réécrit un fichier à
    l'identique — et, mesuré ce jour-là, toute édition faite PENDANT une suite
    de tests. Deux échecs fantômes en ont été fabriqués, sur des tests sans
    aucun rapport avec les fichiers touchés. Le contenu ne bouge que quand le
    code bouge, ce qui est exactement la question posée.

    Contrepartie assumée, MESURÉE le 2026-09-10 sur ce dépôt (46 fichiers,
    783 Ko) : 84 ms par appel à chaud, contre 21 ms pour la version `stat`.
    Quatre fois plus cher, donc — et c'est acceptable ici parce que cette
    fonction n'est appelée qu'AU CHARGEMENT du module
    (`EMPREINTE_AU_CHARGEMENT`) et, hors du serveur, par `scripts/serveur-dev.ps1`
    qui la relance dans un interpréteur à part pour comparer disque et servi.
    Aucun chemin servant une page ne la traverse — `/__fraicheur` lui-même rend
    la constante capturée à l'import, sans jamais rappeler cette fonction. Une
    première version de ce paragraphe citait `/__fraicheur` comme appelant :
    c'était faux, et la revue du 2026-09-10 (T11) l'a relevé.

    Commande : boucler `empreinte_code()` vingt fois après un appel de chauffe,
    et diviser. La chauffe compte : le premier appel, cache disque froid, a
    mesuré 398 ms — un chiffre que j'ai d'abord pris pour le coût réel.
    """
    import hashlib

    h = hashlib.sha256()
    for p in sorted(BASE_DIR.rglob("*.py")):
        h.update(str(p.relative_to(BASE_DIR)).encode())
        try:
            h.update(p.read_bytes())
        except OSError:
            # Fichier disparu ou illisible entre le glob et la lecture : on
            # marque le trou plutôt que de lever. Une preuve de fraîcheur qui
            # fait tomber le serveur ne protège plus rien.
            h.update(b"<illisible>")
    return h.hexdigest()[:16]


# Capturée à l'IMPORT (pas à la requête) : un worker périmé garde l'empreinte
# de SON chargement — c'est l'écart avec le disque qui prouve le stale. Un
# handler qui hasherait le disque à la requête servirait toujours du « frais ».
EMPREINTE_AU_CHARGEMENT = empreinte_code()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # Les trois réconciliations journalisent leur compte : sans cela, une
    # séance qui libère des travaux figés ne laisse aucune trace de ce qu'elle
    # a touché. Asymétrie relevée le 2026-09-10 : celle-ci jetait sa valeur de
    # retour, seule celle des tranches la journalisait, et la troisième
    # (imports audio) est créée par le même lot.
    synthese_liberees = reconcile_running_on_startup()
    if synthese_liberees:
        logging.getLogger(__name__).info(
            "%d synthèse(s) globale(s) interrompue(s) par un redémarrage, "
            "repassée(s) en erreur relançable", synthese_liberees,
        )
    # Même filet pour les tranches d'entretien : sans lui, une tranche tuée par
    # un redémarrage restait « running » à vie (incident du 2026-09-08, 3
    # tranches figées 5h30 sur un entretien réel de 2h).
    liberees = reconcile_segment_jobs_on_startup()
    if liberees:
        logging.getLogger(__name__).info(
            "%d tranche(s) d'entretien interrompue(s) par un redémarrage, "
            "repassée(s) en échec rejouable", liberees,
        )
    # Et le troisième, qui manquait : les imports/retranscriptions audio. Sans
    # lui, un job tué par un redémarrage n'était corrigé que RÉACTIVEMENT
    # (`is_audio_file_job_stale`, 3 h), donc seulement si quelqu'un revenait sur
    # l'écran qui l'interroge — constat d'audit risque technique du 2026-09-09,
    # qui nommait l'asymétrie : deux réconciliations sur trois.
    audios_liberes = reconcile_audio_file_jobs_on_startup()
    if audios_liberes:
        logging.getLogger(__name__).info(
            "%d transcription(s) de fichier audio interrompue(s) par un "
            "redémarrage, repassée(s) en échec rejouable", audios_liberes,
        )
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
# Ajouté APRÈS la garde CSRF, donc exécuté AVANT elle (Starlette empile à
# l'envers) : les en-têtes couvrent aussi la réponse 403 qu'elle renvoie.
app.middleware("http")(entetes_securite)


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
    interne de dev, lecture seule : n'expose qu'un sha256 tronqué du contenu
    des .py d'`app/` — plus des mtimes hashés depuis le 2026-09-10, ce que
    cette phrase affirmait encore (revue du 2026-09-10, T11). Aucun contenu
    n'en est déductible, mais le contrat annoncé doit dire ce qui est calculé."""
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
app.include_router(interviews_audio.router)
app.include_router(synthese.router)
app.include_router(export.router)
app.include_router(agents.router)
