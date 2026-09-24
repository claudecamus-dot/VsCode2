"""Sauvegardes audio de l'enregistrement (onglet Backup).

Extrait mécaniquement de `interviews.py` (2026-09-23, constat d'audit
risque technique : 3385 lignes / 56 routes). Code déplacé tel quel ; URL,
ordre d'enregistrement des routes et nom de logger inchangés.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import RECORDINGS_DIR, get_session
from ..models import (
    Interview,
    Mission,
)
from ..services import mission_backups
from ..uploads import (
    UploadTropVolumineux,
    ecrire_audio_borne,
)
from .interviews_commun import (
    _get_mission,
)

# Même nom de logger qu'avant le découpage : filtres et tests s'y accrochent.
logger = logging.getLogger("app.routers.interviews")

router = APIRouter(tags=["interviews"])

@router.post("/missions/{mission_id}/interviews/record/backup")
async def save_record_backup(
    mission_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    """Sauvegarde l'audio brut complet d'un entretien enregistré (filet de
    sécurité, cf. commentaire sur `Interview.audio_backup_path`) — écrit sur
    disque, hors base de données, en tâche de fond côté client.

    Sert AUSSI de « rattacher un fichier sans le transcrire » (2026-09-01) :
    c'est exactement le même geste — écrire de l'audio d'entretien sous la
    convention `{mission}_…` et rendre son nom au client, qui le référence.
    C'est l'issue qui manquait à trois constats de la revue : sans elle,
    l'unique façon de rattacher un fichier était de le faire transcrire, donc
    de dupliquer une transcription déjà complète (EC3-1) ou d'écraser la
    référence de l'enregistrement micro (IMPORT-1).

    **La mission disparue n'est PAS un refus** (D1, arbitré le 2026-09-02). Le
    404 posé la veille échangeait un orphelin récupérable contre une perte
    sèche : sur ce chemin-ci, l'onglet détient la SEULE copie de l'audio, et le
    refuser la condamne à mourir avec la page. Le scénario n'a rien d'exotique —
    un entretien libre en cours est `_draft_vide`, donc « Nettoyer les brouillons
    vides » cliqué dans un autre onglet emporte sa mission pendant qu'il
    enregistre (mesuré le 2026-09-01 : les 7 brouillons réels sont tous
    `_draft_vide`, et deux d'entre eux portent déjà de l'audio sur disque).

    On écrit donc, et on le DIT : la réponse porte `mission_absente`, sur quoi
    l'écran envoie l'utilisateur vers « Audio sans mission » — où le fichier est
    écoutable, téléchargeable, rattachable ailleurs et supprimable. C'est
    exactement ce que l'inventaire global existe pour faire.

    `transcribe_file`, lui, garde son 404 et ce n'est pas une incohérence : là
    l'utilisateur importe un fichier qu'il a toujours sur son disque, donc
    refuser ne détruit rien. Ici, refuser détruit."""
    if mission_id <= 0:
        # Refus FRANC, comme le jumeau `transcribe_file` (constat `D2-m2`). En
        # levant le 404, D1 avait rouvert ce chemin-ci à `0` et `-5` : le
        # fichier s'écrivait, et l'écran d'administration affichait une raison
        # FAUSSE (« mission n° 0 supprimée » alors qu'aucune n'a existé), ce que
        # `_raison_orphelin` s'interdit explicitement — « devant un bouton de
        # suppression, une raison fausse est pire que pas de raison ».
        #
        # C'est le seul cas où refuser ne détruit rien : aucune page légitime ne
        # poste un identifiant ≤ 0, il ne peut venir que d'un onglet resté
        # ouvert avec un JS d'avant ce déploiement. Ailleurs dans cette
        # fonction, refuser détruirait la seule copie de l'audio — d'où le 404
        # levé par D1.
        return JSONResponse(
            {"error": "Mission absente : recharge la page avant d'enregistrer."},
            status_code=400,
        )
    # Lu AVANT l'écriture : `mission_absente` doit décrire l'état au moment où
    # l'audio arrive, pas celui d'après.
    mission_absente = db.get(Mission, mission_id) is None
    try:
        # Suffixe aléatoire en plus de l'horodatage : deux tranches uploadées
        # dans la MÊME seconde (fin d'enregistrement + rotation, ou deux fetch
        # en vol) auraient sinon le même nom et l'une écraserait l'autre — d'où
        # plusieurs entrées `audio_segments` pointant sur un seul fichier
        # (« une seule tranche »). Le hex ne contient ni « / » ni « .. » : passe
        # le garde-fou de `get_record_backup`.
        # Extension tirée du fichier reçu (2026-09-01) : le magnétophone envoie
        # toujours `entretien.webm`, mais un fichier RATTACHÉ peut être un
        # `.m4a` Meet ou un `.mp3`. Forcer `.webm` sur ces octets-là produisait
        # un fichier dont l'extension ment sur le contenu — et `get_record_backup`
        # le servait en `audio/webm`, donc illisible dans le lecteur. Même
        # filtre que `transcribe_file` : ni « / » ni « .. » ne survivent, le
        # garde-fou de `get_record_backup` passe comme avant.
        suffix = mission_backups.suffixe_sur(file.filename, ".webm")
        filename = f"{mission_id}_{int(time.time())}_{uuid.uuid4().hex[:8]}{suffix}"
        # Streaming par blocs vers le disque (finding perf audit 2026-07-24) : un
        # enregistrement complet de 1h30-3h passait entièrement en RAM via
        # file.read(). copyfileobj lit/écrit en chunks ; dans un thread pour ne
        # pas bloquer la boucle sur l'I/O disque.
        # BORNÉ depuis le 2026-09-10 (même constat que `transcribe_file`) : le
        # fichier partiel est supprimé au dépassement, donc un envoi hors norme
        # ne laisse rien derrière lui.
        octets = await asyncio.to_thread(
            ecrire_audio_borne, file.file, RECORDINGS_DIR / filename
        )
        # Trace POSITIVE dans le journal serveur (incident du 2026-09-08 : zéro
        # tranche audio sur 2h d'entretien, et rien pour dire si le client n'a
        # jamais envoyé ou si le serveur n'a jamais écrit — seule l'exception
        # était journalisée). Le nom seul, jamais le chemin absolu.
        logger.info(
            "Sauvegarde audio de secours écrite : %s (%d octets, mission %s)",
            filename, octets, mission_id,
        )
        if not octets:
            # Un fichier vide n'est pas une sauvegarde : c'est exactement le
            # symptôme du 2026-09-08 (zéro octet d'audio sur 2h), à faire
            # ressortir plutôt que le laisser passer pour « écrite ».
            logger.warning("Sauvegarde audio de secours VIDE : %s (mission %s)", filename, mission_id)
    except UploadTropVolumineux as exc:
        # 413, pas 500 — jumeau exact du correctif posé sur `transcribe_file`
        # (audit-technique sécurité du 2026-09-13). Le dépassement de plafond
        # était aplati en « panne serveur », message explicite perdu. Ici
        # l'enjeu est plus direct encore qu'à l'import : l'onglet détient la
        # SEULE copie de cet audio, et le client relance automatiquement les
        # `status >= 500` — un refus de taille rendu en 500 fait donc rejouer
        # le même volume hors norme au lieu de dire à l'utilisateur ce qui ne
        # va pas.
        return JSONResponse({"error": str(exc)}, status_code=413)
    except Exception:
        logger.exception("Échec de la sauvegarde audio de secours")
        return JSONResponse(
            {"error": "Échec de la sauvegarde audio de secours."}, status_code=500
        )
    return JSONResponse({"path": filename, "mission_absente": mission_absente})


@router.get("/missions/{mission_id}/interviews/record/backup/{filename}")
def get_record_backup(mission_id: int, filename: str, db: Session = Depends(get_session)):
    """Sert un enregistrement audio sauvegardé (écoute/téléchargement) — le
    fichier était déjà écrit sur disque (`save_record_backup`) mais jamais
    exposé par une route ; il n'y avait donc rien à lier depuis le
    formulaire d'enregistrement. Ajouté suite à un signalement utilisateur
    ("le lien pour réécouter/télécharger a disparu") — l'historique git ne
    montre aucune trace d'un tel lien ayant existé dans ce dépôt.

    Même garde d'appartenance que `delete_record_backup` (revue adversariale
    2026-07-29) : sans elle, l'id de mission de l'URL n'était qu'un décor et
    n'importe quel id servait n'importe quel enregistrement."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    mission = _get_mission(db, mission_id)
    if not mission_backups.appartient_a_mission(
        filename, mission_id, mission, RECORDINGS_DIR
    ):
        raise HTTPException(
            status_code=404, detail="Enregistrement introuvable pour cette mission."
        )
    path = RECORDINGS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Enregistrement introuvable.")
    return FileResponse(
        path, media_type=mission_backups.media_type_audio(filename), filename=filename
    )


@router.post("/missions/{mission_id}/interviews/record/backup/{filename}/delete")
def delete_record_backup(
    mission_id: int,
    filename: str,
    db: Session = Depends(get_session),
):
    """Supprime un enregistrement audio de la mission (onglet « Backup ») —
    fichier sur disque ET référence en base, sinon l'écran garderait une ligne
    pointant vers un fichier disparu.

    Deux gardes, la seconde étant la vraie : le nom de fichier ne doit pas
    permettre de sortir de `data/recordings/` (même filtre que
    `get_record_backup`), et le fichier doit appartenir À CETTE mission —
    sans quoi l'id de mission de l'URL ne serait qu'un décor et n'importe
    quelle mission pourrait effacer les enregistrements des autres."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    mission = _get_mission(db, mission_id)
    if not mission_backups.appartient_a_mission(
        filename, mission_id, mission, RECORDINGS_DIR
    ):
        raise HTTPException(
            status_code=404, detail="Enregistrement introuvable pour cette mission."
        )
    # Un fichier « orphelin » au regard de CETTE mission (préfixe seul) peut
    # être référencé par l'entretien d'une AUTRE mission — entretien réattaché
    # depuis une mission brouillon dont l'id a été réutilisé (revue
    # adversariale 2026-07-29). Le supprimer ici laisserait l'autre mission
    # avec des références pendantes sans aucun moyen de l'avoir empêché.
    ailleurs = [
        itw
        for itw in db.scalars(select(Interview).where(Interview.mission_id != mission_id))
        if itw.audio_backup_path == filename
        or any(seg.get("filename") == filename for seg in (itw.audio_segments or []))
    ]
    if ailleurs:
        raise HTTPException(
            status_code=409,
            detail="Cet enregistrement est référencé par un entretien d'une autre "
            "mission — supprime-le depuis cette mission-là.",
        )

    path = RECORDINGS_DIR / filename
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        # Fichier verrouillé (lecture en cours sous Windows, typiquement) : ne
        # pas retirer la référence, sinon l'octet resterait sur le disque sans
        # plus aucun écran pour le montrer.
        logger.warning("Suppression de %s impossible : %s", filename, exc)
        raise HTTPException(
            status_code=409,
            detail="Fichier momentanément verrouillé — réessaie dans un instant.",
        ) from exc

    for interview in mission.interviews:
        segments = [
            seg for seg in (interview.audio_segments or []) if seg.get("filename") != filename
        ]
        if len(segments) != len(interview.audio_segments or []):
            # Renumérotation : `position` est un RANG affiché (« Tranche 2/3 »
            # sur l'onglet Backup et `libre_detail.html`), pas une identité.
            # Sans elle, supprimer la tranche du milieu laissait les rangs 0 et
            # 2 sur un entretien qui n'a plus que 2 tranches, soit « Tranche
            # 3/2 » à l'écran (revue adversariale 2026-07-29).
            segments = [
                {**seg, "position": rang}
                for rang, seg in enumerate(
                    sorted(segments, key=lambda s: s.get("position") or 0)
                )
            ]
            # Réassignation (pas une mutation en place) : SQLAlchemy ne détecte
            # pas la modification d'une liste JSON modifiée sur place.
            interview.audio_segments = segments
        if interview.audio_backup_path == filename:
            # `audio_backup_path` désigne la DERNIÈRE tranche : on le fait
            # retomber sur celle qui reste, pas sur None si l'entretien a
            # encore de l'audio.
            interview.audio_backup_path = segments[-1]["filename"] if segments else None
    db.commit()
    return RedirectResponse(f"/missions/{mission_id}#backup", status_code=303)
