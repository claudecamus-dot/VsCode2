"""Import et transcription de fichiers audio hors enregistrement direct.

Extrait de `interviews.py` (2026-09-12, constat d'audit risque technique —
fichier à 3611 lignes / 100 fonctions) : ce sous-ensemble est le SEUL bloc du
fichier sans aucune dépendance vers `Interview`/`Question`/`Answer`/`Verbatim`
ni vers les helpers de CRUD d'entretien — il ne connaît que `Mission` (pour
nommer le fichier importé) et `AudioFileJob`. Aucune autre route ni aucun
autre module du dépôt n'appelle ces fonctions directement (vérifié par grep
avant extraction) ; seul le chemin d'URL compte, et il est inchangé.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from ..db import RECORDINGS_DIR, get_session
from ..models import AudioFileJob, Mission
from ..services import audio_transcribe, mission_backups
from ..services.audio_file_jobs import (
    is_audio_file_job_stale,
    purge_stale_audio_file_jobs,
    run_audio_file_job,
)
from ..uploads import UploadTropVolumineux, ecrire_audio_borne, lire_upload_audio_borne

logger = logging.getLogger(__name__)

router = APIRouter(tags=["interviews"])


@router.post("/audio/transcribe-segment")
async def transcribe_segment(file: UploadFile = File(...)):
    """Transcrit un segment audio autonome (utilisé par la rotation de
    segments de record.html) — endpoint sans état, indépendant de toute
    mission/entretien. Même contrat d'erreur `{"error": ...}` que
    `transcribe_notes` : jamais de `{"detail": ...}` ni de 500 brute."""
    try:
        # Borne mémoire AVANT tout décodage (finding audit-technique securite
        # du 2026-09-09) : `await file.read()` nu matérialisait tout le corps de
        # la requête d'un coup sur une route non authentifiée — un envoi de
        # plusieurs Go faisait tomber le processus. Plafond AUDIO, pas celui des
        # documents (cf. `uploads.MAX_AUDIO_UPLOAD_BYTES`).
        contenu = await lire_upload_audio_borne(file)
        # Whisper est CPU-bound : hors de la boucle d'événements (finding perf audit
        # 2026-07-24 — un endpoint async qui transcrit en direct bloquait TOUTES les
        # autres requêtes pendant plusieurs minutes).
        text = await asyncio.to_thread(audio_transcribe.transcribe_audio, contenu)
    except UploadTropVolumineux as exc:
        # 413, pas 5xx : le JS de record.html relance automatiquement les seuls
        # `status >= 500`. Un refus de taille est définitif pour ces octets —
        # les rejouer trois fois ne ferait que renvoyer le même volume. Le blob
        # part dans le bandeau « segments perdus » avec ce message.
        return JSONResponse({"error": str(exc)}, status_code=413)
    except audio_transcribe.NoSpeechError as exc:
        # `code` structuré : l'écran d'enregistrement compte les segments
        # consécutifs sans parole pour alerter sur la source audio (entretien
        # à distance dont le micro n'entend pas le casque, mauvais périphérique)
        # — un matching sur le message français serait fragile.
        return JSONResponse({"error": str(exc), "code": "no_speech"}, status_code=422)
    except audio_transcribe.TranscriptionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception:
        # Message FIXE, jamais `str(exc)` : sur ce chemin l'exception est
        # quelconque (OSError de whisper, disque plein…) et son texte porte
        # les chemins absolus du poste. Le détail complet reste dans le
        # journal serveur, où il sert au diagnostic sans être publié au
        # client (finding audit-technique securite du 2026-09-04).
        logger.exception("Échec inattendu de la transcription d'un segment")
        return JSONResponse(
            {"error": "Échec inattendu de la transcription de ce segment."},
            status_code=500,
        )
    return JSONResponse({"text": text})


@router.post("/audio/transcribe-file")
async def transcribe_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session_token: str = Form(""),
    mission_id: int = Form(0),
    db: Session = Depends(get_session),
):
    """Importe un fichier audio déjà enregistré et lance sa transcription
    BLOC PAR BLOC en tâche de fond (2026-07-27).

    Remplace, pour ce cas, l'appel synchrone unique à `/audio/transcribe-segment`
    (qui ne rendait la main qu'une fois le fichier ENTIER transcrit — rien à
    l'écran pendant des dizaines de minutes, et aucune extraction IA démarrée
    avant la fin). Le client récupère les blocs au fil de l'eau
    (`transcribe_file_status`) et soumet, bloc par bloc, les mêmes jobs
    d'extraction que pendant un enregistrement micro : à partir du texte, un
    fichier importé se comporte exactement comme un direct.

    `mission_id` sert au NOM du fichier, et c'est structurel (2026-09-01) : le
    fichier importé est de l'audio d'entretien au même titre qu'un
    enregistrement, il doit donc porter la même convention
    `{mission}_...` — c'est elle, et elle seule, qui le rend visible dans
    l'onglet Backup de la mission (`mission_backups.lister_backups` cherche par
    `glob("{mission.id}_*")`). Sans ce préfixe, le fichier n'était atteignable
    par AUCUN écran : la seule façon de s'en débarrasser était la suppression
    automatique, exactement ce que la règle « l'audio ne se supprime que par une
    action sur le site » interdit."""
    # TOUTES les validations AVANT d'écrire le moindre octet (revue du
    # 2026-09-01). Tant que le serveur nettoyait derrière lui, écrire puis
    # refuser était sans conséquence ; depuis qu'il ne supprime plus rien, la
    # moindre requête refusée laisserait de l'audio DÉFINITIF sur le disque —
    # répétable sans limite, et invisible si le refus porte précisément sur le
    # `mission_id` qui conditionne la visibilité. On ne conserve donc que
    # l'audio d'une requête acceptée.
    if not session_token.strip():
        # Le jeton scope la lecture du statut (le seul endpoint qui renvoie du
        # contenu d'entretien) : sans lui, n'importe quel `job_id` — ils sont
        # séquentiels — rendrait la transcription d'autrui.
        return JSONResponse(
            {"error": "Session d'enregistrement absente."}, status_code=400
        )
    if mission_id <= 0:
        # Refus FRANC plutôt que repli silencieux : un `mission_id` absent ou
        # nul produisait un fichier sans préfixe, que l'onglet Backup ne montre
        # à personne — donc que plus aucune action du site ne peut supprimer.
        # Le cas réel visé est un onglet resté ouvert avec le JS d'avant ce
        # déploiement : mieux vaut une erreur lisible qu'un fichier fantôme.
        return JSONResponse(
            {"error": "Mission absente : recharge la page avant d'importer."},
            status_code=400,
        )
    if db.get(Mission, mission_id) is None:
        # Sinon le fichier porterait le préfixe d'une mission inexistante :
        # visible d'aucun écran, donc indestructible.
        return JSONResponse({"error": "Mission introuvable."}, status_code=404)
    try:
        # Même point unique que `save_record_backup` (constat `B4-SIBLING`) : ce
        # chemin-ci portait la logique recopiée SANS le garde-fou B4, donc un
        # nom comme « reunion. » y produisait encore un fichier que l'inventaire
        # global proposait à la suppression PENDANT que le job le lisait.
        suffix = mission_backups.suffixe_sur(file.filename, ".audio")
        # `import_` conservé APRÈS le préfixe de mission : le fichier reste
        # reconnaissable comme un import dans la liste, et le garde-fou de
        # `get_record_backup` (ni « / » ni « .. ») passe comme pour un
        # enregistrement.
        filename = f"{mission_id}_import_{int(time.time())}_{uuid.uuid4().hex[:8]}{suffix}"
        # Streaming par blocs vers le disque (même raison que save_record_backup) :
        # un entretien de 1h30-3h ne doit pas passer entièrement en RAM. BORNÉ
        # depuis le 2026-09-10 : cette route n'est pas authentifiée, et sans
        # plafond un envoi de plusieurs Go remplissait le disque — dernière
        # jambe du constat sécurité, les chemins en MÉMOIRE étant bornés depuis
        # la veille.
        await asyncio.to_thread(
            ecrire_audio_borne, file.file, RECORDINGS_DIR / filename
        )
    except UploadTropVolumineux as exc:
        # 413 et message EXPLICITE, comme le chemin mémoire de ce même fichier
        # (audit-technique sécurité du 2026-09-13). `ecrire_audio_borne` lève
        # `EcritureAudioTropVolumineuse` — une sous-classe de
        # `UploadTropVolumineux` — mais le `except Exception` ci-dessous
        # l'attrapait et répondait « Échec de l'import du fichier audio. » en
        # 500 : le plafond était annoncé au client sous un code qui signifie
        # « panne serveur », et le message qui dit POURQUOI (« Fichier audio
        # trop volumineux (plafond 100 Mo) ») était perdu.
        #
        # Le message est écrit par le projet (`uploads.py`) et ne porte aucun
        # chemin : le publier ne contredit pas la règle « message FIXE, jamais
        # str(exc) », qui vise le texte d'exceptions TIERCES. C'est d'ailleurs
        # déjà ce que fait la route sœur en mémoire, quinze lignes plus haut,
        # avec sa raison écrite : un refus de taille est DÉFINITIF pour ces
        # octets, et un 5xx invite le client à rejouer le même volume.
        return JSONResponse({"error": str(exc)}, status_code=413)
    except Exception:
        # Même règle qu'au-dessus : l'écriture disque échoue avec un message
        # qui contient RECORDINGS_DIR en absolu.
        logger.exception("Échec de l'import du fichier audio à transcrire")
        return JSONResponse(
            {"error": "Échec de l'import du fichier audio."}, status_code=500
        )

    try:
        purge_stale_audio_file_jobs(db)
        job = AudioFileJob(
            session_token=session_token[:64],
            filename=filename,
            status="pending",
            block_seconds=audio_transcribe.FILE_BLOCK_S,
            blocks=[],
        )
        db.add(job)
        db.commit()
        db.refresh(job)
    except Exception:
        # Fichier déjà écrit mais aucun job pour le référencer. Il n'est PLUS
        # supprimé (2026-09-01) : la revue adversariale du 2026-07-27 le
        # retirait parce que rien ne pouvait plus le retrouver — ce n'est plus
        # vrai depuis qu'il porte le préfixe de mission, il apparaît en
        # orphelin dans l'onglet Backup. Entre « un fichier à supprimer d'un
        # clic » et « l'audio d'un entretien détruit par le serveur », la règle
        # du projet tranche : l'audio ne se supprime que par une action du site.
        logger.exception("Création du job de transcription de fichier impossible")
        return JSONResponse(
            {
                "error": "Le fichier a bien été reçu, mais sa transcription "
                "n'a pas pu être lancée."
            },
            status_code=500,
        )
    background_tasks.add_task(run_audio_file_job, job.id)
    return JSONResponse(
        {"job_id": job.id, "status": "pending", "block_seconds": job.block_seconds}
    )


@router.post("/audio/transcribe-file/retry")
def transcribe_file_retry(
    background_tasks: BackgroundTasks,
    job_id: int = Form(...),
    session_token: str = Form(""),
    db: Session = Depends(get_session),
):
    """Relance un import échoué AU BLOC qui a échoué (2026-07-29).

    Un bloc peut échouer sans que le fichier soit en cause (worker de
    transcription tué par manque de mémoire, typiquement) : jusqu'ici la seule
    issue était de ré-importer le fichier et de re-transcrire depuis le début,
    en repayant les dizaines de minutes déjà passées. Les blocs déjà obtenus
    sont conservés et `run_audio_file_job` repart de `len(job.blocks)`.

    Même garde que `transcribe_file_status` : le jeton de session doit
    correspondre, sinon un `job_id` (séquentiel) relancerait l'import d'autrui.
    """
    job = db.get(AudioFileJob, job_id)
    if job is None or not session_token.strip() or job.session_token != session_token:
        return JSONResponse({"error": "Import introuvable."}, status_code=404)
    if job.status != "failed" and not is_audio_file_job_stale(job):
        # Rien à relancer : un job en cours finira, un job abouti n'a plus son
        # fichier. Le client ne propose le bouton que sur échec ; cette garde
        # couvre un double-clic ou un onglet resté ouvert.
        #
        # Un job PÉRIMÉ est relançable bien que son statut en base soit resté
        # `pending`/`running` (revue adversariale 2026-07-29) : c'est le cas
        # même du serveur redémarré en cours de transcription — `/status` le
        # rapporte `failed` et propose la reprise, que cette garde refusait
        # ensuite alors que le fichier est là et la reprise légitime.
        return JSONResponse(
            {"error": "Cet import n'est pas en échec.", "status": job.status},
            status_code=409,
        )
    if job.total_blocks and len(job.blocks or []) >= job.total_blocks:
        # Tous les blocs ont déjà été transcrits : l'échec ne vient pas d'un
        # bloc manquant (fichier sans parole, typiquement). Relancer ne
        # rejouerait rien — `iter_transcribe_blocks` n'a plus aucun bloc à
        # produire — et re-échouerait à l'identique, indéfiniment. On le dit.
        # Le fichier RESTE (2026-09-01) : « plus rien ne justifie de le garder »
        # était un jugement du serveur sur l'audio de l'utilisateur. Un fichier
        # sans parole exploitable pour Whisper reste une réunion enregistrée,
        # et c'est précisément le cas où l'on veut pouvoir réécouter pour
        # comprendre ce qui a échoué. Il reste listé dans l'onglet Backup.
        db.commit()
        return JSONResponse(
            {
                "error": "Tout le fichier a déjà été transcrit : l'échec ne vient "
                "pas d'un bloc interrompu. Reprends depuis un autre fichier.",
                "status": "failed",
            },
            status_code=409,
        )
    if not (RECORDINGS_DIR / job.filename).is_file():
        return JSONResponse(
            {
                "error": "Le fichier audio importé n'est plus disponible — "
                "ré-importe-le pour reprendre la transcription."
            },
            status_code=410,
        )
    reprise_au_bloc = len(job.blocks or [])
    # UPDATE CONDITIONNEL et non lire-puis-écrire (audit-technique robustesse du
    # 2026-09-13). La garde au-dessus est un test Python sur une ligne lue au
    # début de la fonction : deux POST concurrents — double-clic sur
    # « Relancer », onglet dupliqué, rejeu réseau — la franchissent TOUS DEUX et
    # programmaient chacun un `run_audio_file_job` sur le même `job.id`. Les deux
    # tâches ouvrent alors leur propre session et font
    # `job.blocks = list(job.blocks or []) + [text]` : blocs dupliqués et mises à
    # jour perdues DANS LA TRANSCRIPTION, plus un `total_blocks` incohérent avec
    # le curseur `since` du client. Ce n'est pas un risque théorique importé
    # d'ailleurs : le chemin frère `retranscrire` le dit déjà de lui-même, et le
    # patron correct est posé deux fois dans le dépôt (synthese.py:272,
    # interviews.py:1108) — il manquait ici.
    #
    # Comparaison-et-échange sur (status, created_at) et non sur `status` seul :
    # un job PÉRIMÉ est relançable en étant resté `pending`/`running` en base
    # (serveur redémarré), donc `status` seul laisserait passer les deux
    # concurrents. Le gagnant re-date `created_at` ; le perdant, qui a lu
    # l'ancienne date, ne matche plus rien.
    pris = db.execute(
        update(AudioFileJob)
        .where(
            AudioFileJob.id == job.id,
            AudioFileJob.status == job.status,
            AudioFileJob.created_at == job.created_at,
        )
        .values(
            status="pending",
            error=None,
            # `created_at` sert d'horloge à `is_audio_file_job_stale` (et à la
            # purge des 7 jours) : sans ce réarmement, un job relancé plus de
            # 3 h après l'import initial serait déclaré « ne répond plus » au
            # premier poll, alors que la reprise vient de démarrer.
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
    ).rowcount
    db.commit()
    if pris != 1:
        # Un concurrent a déjà repris ce job : on ne programme PAS une seconde
        # tâche. Même code que la garde « pas en échec » au-dessus — de la place
        # de l'appelant, c'est la même situation : il n'y a rien à relancer.
        return JSONResponse(
            {"error": "Cet import est déjà en cours de relance.", "status": "pending"},
            status_code=409,
        )
    background_tasks.add_task(run_audio_file_job, job.id)
    return JSONResponse(
        {"job_id": job.id, "status": "pending", "reprise_au_bloc": reprise_au_bloc}
    )


def _mission_du_job_absente(job, db) -> bool:
    """La mission de ce job d'import a-t-elle disparu depuis son démarrage ?

    Rend `False` dès que la question n'a pas de réponse FIABLE — nom sans
    préfixe lisible, ou retranscription (`filenames`, dont les tranches sont
    déjà rattachées à un entretien existant). Le silence est le bon défaut dans
    le doute : un `true` à tort afficherait un bandeau alarmant et
    verrouillerait l'enregistrement sur une mission parfaitement vivante, ce qui
    est un dégât certain contre un risque hypothétique.
    """
    nom = job.filename or ""
    if job.filenames or not nom:
        return False
    mission_id = mission_backups.mission_id_du_fichier(nom)
    if mission_id is None:
        return False
    return db.get(Mission, mission_id) is None


@router.get("/audio/transcribe-file/status")
def transcribe_file_status(
    job_id: int,
    session_token: str = "",
    since: int = 0,
    db: Session = Depends(get_session),
):
    """Blocs transcrits d'un fichier importé depuis le curseur `since` —
    interrogé en boucle par l'écran d'enregistrement.

    `session_token` est EXIGÉ et vérifié : cet endpoint est le seul à renvoyer
    du contenu d'entretien, et les `job_id` sont séquentiels. `since` évite de
    re-transférer toute la transcription à chaque tick (3 s) : sur un entretien
    de 3 h le volume cumulé devenait quadratique. Un job resté bloqué
    (redémarrage serveur) est rapporté `failed` plutôt que de faire poller
    l'écran indéfiniment."""
    job = db.get(AudioFileJob, job_id)
    if job is None or not session_token or job.session_token != session_token:
        raise HTTPException(status_code=404, detail="Import introuvable.")
    blocks = list(job.blocks or [])
    status, error = job.status, job.error or ""
    if is_audio_file_job_stale(job):
        status = "failed"
        error = error or (
            "La transcription de ce fichier ne répond plus (serveur redémarré ?) "
            "— relance l'import."
        )
    payload = {
        "status": status,
        # Curseur : seuls les blocs non encore consommés par le client.
        "blocks": blocks[max(0, since):],
        "done": len(blocks),
        "total": job.total_blocks,
        "error": error,
        # Nom du fichier importé (2026-09-01), pour que le client le RATTACHE à
        # l'entretien via `audio_segments`. Sans ce rattachement, l'audio
        # importé survit désormais sur le disque mais reste un orphelin :
        # `_tranches_audio` ne le voit pas, donc « Relancer la transcription »
        # ne peut pas rejouer depuis lui — or c'est précisément le geste qu'on
        # veut rendre possible quand un défaut de transcription est découvert
        # après coup. Vide pour une retranscription (`filenames`), dont les
        # tranches sont déjà rattachées.
        "filename": (job.filename or "") if not job.filenames else "",
        # D1-F3 (revue du 2026-09-02) : la mission peut disparaître PENDANT
        # l'import — un entretien libre en cours est `_draft_vide`, donc
        # « Nettoyer les brouillons vides » cliqué dans un autre onglet emporte
        # sa mission pendant les 40 min de transcription. `transcribe_file`
        # vérifie la mission à l'ENTRÉE et n'en reparle plus ; l'écran
        # réarmait donc « Enregistrer l'entretien » à la fin de l'import, et le
        # clic rendait 404 en détruisant 1 h 30 de transcription. Exactement le
        # mode d'échec que D1 déclare fermer, sur le chemin qu'il n'avait pas
        # regardé. `AudioFileJob` ne porte pas de `mission_id` : on le lit sur
        # le préfixe du nom, seule source disponible ici.
        "mission_absente": _mission_du_job_absente(job, db),
    }
    if status == "done" and since >= len(blocks):
        # Job consommé : sa colonne `blocks` porte la transcription complète
        # d'un entretien. La purge périodique ne s'exécute qu'au prochain
        # import — sur un poste où l'on importe rarement, le texte serait resté
        # des mois en base (revue adversariale 2026-07-27). On le supprime dès
        # que le client a tout récupéré.
        #
        # Un job ÉCHOUÉ est au contraire CONSERVÉ (revue adversariale
        # 2026-07-29) : c'est lui qui porte les blocs déjà transcrits et le
        # fichier encore sur disque, donc la reprise au bloc échoué. Le
        # supprimer ici tuait la relance dans le cas NOMINAL — l'échec survient
        # en transcrivant le bloc suivant, donc sans nouveau bloc à livrer, donc
        # avec `since == len(blocks)`. `purge_stale_audio_file_jobs` (7 j)
        # reste le filet qui efface blocs ET fichier si la relance n'a pas lieu.
        #
        # DELETE CONDITIONNEL et non `db.delete(job)` (audit-technique
        # robustesse du 2026-09-13). Cette suppression part d'une route GET
        # sondée toutes les 3 s sur la seule foi d'une lecture faite 50 lignes
        # plus haut. Si un tick dépasse l'intervalle client et qu'un second
        # arrive avant la réponse du premier, les deux lisent le même job et les
        # deux le suppriment : le flush du second levait `StaleDataError`
        # (« DELETE ... expected to delete 1 row(s); 0 were matched ») et
        # l'écran recevait une 500 JUSTE APRÈS une transcription réussie. Un
        # rejeu réseau ou un prefetch navigateur suffisait. Le DELETE Core
        # n'affecte alors aucune ligne et le perdant rend sa réponse
        # normalement — même geste que les UPDATE conditionnels sur `rowcount`
        # déjà posés ailleurs (`synthese.py`, `interviews.py`). `payload` est
        # déjà construit : le perdant sert la même charge utile que le gagnant.
        db.execute(delete(AudioFileJob).where(AudioFileJob.id == job.id))
        db.commit()
    return JSONResponse(payload)
