"""Retranscription d'un entretien déjà enregistré depuis ses tranches audio.

Extrait mécaniquement de `interviews.py` (2026-09-23, constat d'audit
risque technique : 3385 lignes / 56 routes). Code déplacé tel quel ; URL,
ordre d'enregistrement des routes et nom de logger inchangés.
"""
from __future__ import annotations

import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import RECORDINGS_DIR, get_session
from ..models import (
    AudioFileJob,
    Interview,
    InterviewTurn,
)
from ..services import audio_transcribe
from ..services.audio_file_jobs import (
    is_audio_file_job_stale,
    purge_stale_audio_file_jobs,
    reprendre_job_echoue_ou_perime,
    run_audio_file_job,
    tranches_extraction,
)
from ..services.interview_export import (
    transcript_of,
)
from ..services.interview_segment_jobs import (
    delete_segment_jobs,
    merge_segment_turns,
    recover_stalled_or_failed_jobs,
    segment_jobs_status,
)
from ..templating import templates


from .interviews_commun import (
    _fenetre_recuperation,
)
from .interviews_libre import (
    _parse_turns_from_form,
)
from .interviews_analyse import (
    _get_interview_libre,
    _libre_analyse_context,
)

router = APIRouter(tags=["interviews"])

# --------------------------------------------------------------------------- #
# Retranscription d'un entretien DÉJÀ enregistré, depuis ses tranches audio
# --------------------------------------------------------------------------- #
# Demande utilisateur du 2026-07-30, exigence (4) : les relances de transcription
# et de répartition Q/R doivent être disponibles EN CONSULTATION, pas seulement
# pendant l'enregistrement. Les blobs de 60 s du direct ne survivent pas à la
# fermeture de l'onglet — seules les tranches persistées (`audio_segments`,
# servies par l'onglet Backup) couvrent ce cas, d'où le choix de la piste (b) du
# TODO comme socle. Rien n'est écrasé sans revue : le résultat passe par un écran
# de confirmation, comme la régénération d'analyse ci-dessus.



def _tranches_audio(interview: Interview) -> tuple[list[str], int]:
    """Fichiers de sauvegarde audio de l'entretien, dans l'ordre chronologique
    et effectivement présents sur le disque, plus le nombre de tranches
    RÉFÉRENCÉES MAIS INTROUVABLES. Reprend aussi les entretiens d'avant la
    segmentation (`audio_backup_path` seul).

    Le compte d'absents n'est pas cosmétique : une tranche supprimée depuis
    l'onglet Backup produit une transcription TROUÉE qui remplacerait ensuite
    une transcription complète, sans que rien ne signale le trou sur l'écran de
    revue (revue adversariale 2026-07-30)."""
    references = _noms_audio(interview)
    presents = [n for n in references if (RECORDINGS_DIR / n).is_file()]
    return presents, len(references) - len(presents)


def _rang_segment(seg: dict) -> int:
    """Clé de tri coercitive d'une tranche : `position` vient d'un champ caché
    client, une valeur non entière ne doit pas faire lever le tri (elle prend
    simplement le rang 0, donc l'ordre d'insertion — `sorted` est stable)."""
    try:
        return int(seg.get("position", 0))
    except (TypeError, ValueError):
        return 0


def _noms_audio(interview: Interview) -> list[str]:
    """Fichiers de sauvegarde RÉFÉRENCÉS par cet entretien, dans l'ordre
    chronologique — sans vérifier leur présence sur le disque (c'est le rôle de
    `_tranches_audio`, qui s'appuie sur cette liste)."""
    segments = interview.audio_segments or []
    # `isinstance` AVANT le tri : `sorted(..., key=lambda s: s.get(...))`
    # s'exécute d'abord et lèverait `AttributeError` (500) sur un
    # `audio_segments` contenant autre chose qu'un dict — ce champ vient d'un
    # champ caché client (`_parse_audio_segments` ne valide que « c'est une
    # liste »).
    # Le CONTENU est validé autant que le conteneur (revue adversariale
    # 2026-07-30) : `filename` non-`str` faisait lever le test de traversée plus
    # bas (`"/" not in 123`), et des `position` de types mixtes faisaient lever
    # le tri — 500 sur les 4 routes de retranscription, sans issue dans l'UI
    # pour l'entretien ainsi enregistré.
    dicts = [
        seg
        for seg in segments
        if isinstance(seg, dict) and isinstance(seg.get("filename"), str) and seg["filename"]
    ]
    noms = [seg["filename"] for seg in sorted(dicts, key=_rang_segment)]
    if not noms and interview.audio_backup_path:
        noms = [interview.audio_backup_path]
    # Déduplication en conservant l'ordre de première apparition : deux entrées
    # `audio_segments` peuvent pointer le MÊME fichier sur les entretiens
    # enregistrés avant le suffixe aléatoire de `save_record_backup` (deux
    # tranches uploadées dans la même seconde s'écrasaient). Sans elle, la même
    # demi-heure d'audio est transcrite deux fois et le tour de table proposé
    # porte un bloc entier en double.
    vus: set[str] = set()
    uniques = [n for n in noms if not (n in vus or vus.add(n))]
    # Même garde de traversée de chemin que `get_record_backup` /
    # `delete_record_backup` sur ce même champ : un nom qui s'en écarte n'est
    # pas un fichier de tranche, on ne le lit pas.
    return [n for n in uniques if "/" not in n and "\\" not in n and ".." not in n]


def _job_retranscription(db: Session, interview_id: int) -> AudioFileJob | None:
    """Job de retranscription courant de cet entretien (le plus récent)."""
    return db.scalars(
        select(AudioFileJob)
        .where(AudioFileJob.interview_id == interview_id)
        .order_by(AudioFileJob.id.desc())
    ).first()


def _job_retranscription_de_cet_entretien(
    db: Session, interview: Interview
) -> AudioFileJob | None:
    """Job de retranscription courant, à condition qu'il porte bien de l'audio
    de CET entretien.

    Sans cette vérification, un job pouvait proposer de remplacer le contenu
    d'un entretien par la transcription d'un AUTRE (constat 2026-07-30, trouvé
    en nettoyant les données de vérification) : la colonne `interview_id` est
    ajoutée par migration additive, donc **sans clause REFERENCES** sur les
    bases existantes (SQLite ne sait pas l'ajouter après coup) — aucun
    `ON DELETE CASCADE` n'emporte le job quand l'entretien est supprimé. Or
    SQLite **recycle** l'identifiant libéré (vérifié : supprimer la dernière
    ligne puis insérer rend le même `rowid`), donc le prochain entretien créé
    héritait du job orphelin comme « job courant » — sa transcription était
    alors proposée à l'application sur un entretien qui n'a jamais produit cet
    audio. `retranscrire_start` comparait déjà ses tranches ; les écrans de
    suivi, de statut et de revue, non."""
    job = _job_retranscription(db, interview.id)
    if job is None or not _job_porte_l_audio(job, interview):
        return None
    return job


def _job_porte_l_audio(job: AudioFileJob, interview: Interview) -> bool:
    """Ce job porte-t-il de l'audio de CET entretien ?

    Critère : une INTERSECTION non vide avec les fichiers RÉFÉRENCÉS par
    l'entretien — pas une inclusion, et pas une égalité.

    L'inclusion (première version de cette garde) était trop stricte : le seul
    chemin de suppression offert à l'utilisateur, l'onglet Backup, retire aussi
    la RÉFÉRENCE (`delete_record_backup` réécrit `audio_segments`). Supprimer
    une tranche pendant une retranscription rendait donc étranger un job
    parfaitement légitime — écran de suivi disparu sans un mot, statut en 404,
    bouton « Abandonner » injoignable, et le clic suivant détruisait des heures
    de calcul abouti (les deux chasseurs de la revue du 2026-07-30 l'ont
    reproduit indépendamment). L'égalité stricte, elle, avait le même défaut sur
    le chemin `retranscrire_start` dès qu'un fichier disparaissait du disque.

    Un job dont les tranches ont rétréci reste manifestement le sien ; seul un
    job DISJOINT est étranger — et c'est exactement ce que produit le recyclage
    d'identifiant décrit ci-dessus, les noms de fichiers étant préfixés par la
    mission et horodatés."""
    fichiers = set(job.filenames or [])
    return bool(fichiers and fichiers & set(_noms_audio(interview)))


def _oublier_job_retranscription(db: Session, job: AudioFileJob) -> None:
    """Retire un job de retranscription et les tranches d'extraction qu'il a
    créées. Le fichier audio, lui, appartient à l'entretien : jamais supprimé —
    depuis 2026-09-01 aucun chemin automatique du dépôt n'efface d'audio."""
    delete_segment_jobs(db, job.session_token)
    db.delete(job)
    db.commit()


@router.post("/interviews/{interview_id}/retranscrire")
def retranscrire_start(
    interview_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Relance la transcription de l'entretien depuis ses tranches audio
    persistées, en tâche de fond (`run_audio_file_job`, bloc par bloc avec
    reprise au bloc échoué), puis l'extraction des tours tranche par tranche."""
    interview = _get_interview_libre(db, interview_id)
    tranches, _absentes = _tranches_audio(interview)
    if not tranches:
        context = _libre_analyse_context(interview)
        context["error"] = (
            "Aucun enregistrement audio disponible pour cet entretien : la "
            "transcription ne peut pas être relancée."
        )
        return templates.TemplateResponse(
            request, "interviews/libre_analyse.html", context
        )

    precedent = _job_retranscription(db, interview_id)
    if precedent is not None:
        perime = is_audio_file_job_stale(precedent)
        # Même critère de propriété que les écrans de consultation
        # (`_job_porte_l_audio`) et NON plus l'égalité stricte à la liste des
        # fichiers PRÉSENTS : une tranche disparue du disque — ou retirée depuis
        # l'onglet Backup — faisait tomber l'égalité, donc détruisait un résultat
        # abouti non revu (des heures de calcul) alors que le `confirm()` du
        # bouton ne parle que du contenu déjà enregistré.
        if precedent.status == "done" and _job_porte_l_audio(precedent, interview):
            # Résultat calculé et JAMAIS revu (l'utilisateur a quitté l'écran de
            # revue, ou re-clique sur le bouton) : le détruire pour repartir de
            # zéro jetterait potentiellement des heures de calcul sans le dire.
            # On le ramène sur son écran de suivi, d'où « Voir le résultat »
            # reste accessible — et « Abandonner ce résultat » permet de repartir
            # de zéro explicitement (revue adversariale 2026-07-30).
            return RedirectResponse(
                f"/interviews/{interview_id}/retranscrire", status_code=303
            )
        if precedent.status in ("pending", "running") and not perime:
            # Déjà en cours (double clic, onglet resté ouvert) : on renvoie sur
            # l'écran de suivi plutôt que de lancer un second passage IA.
            return RedirectResponse(
                f"/interviews/{interview_id}/retranscrire", status_code=303
            )
        if (precedent.status == "failed" or perime) and _job_porte_l_audio(
            precedent, interview
        ):
            # REPRISE au bloc/à la tranche interrompus : `run_audio_file_job`
            # repart de `files_done`/`len(blocks)` et l'extraction saute les
            # tranches déjà structurées. Recréer un job neuf ici jetterait tout
            # ce travail et repaierait les dizaines de minutes déjà passées —
            # exactement ce que la reprise de l'import évite depuis le
            # 2026-07-29.
            #
            # UPDATE CONDITIONNEL et non lire-puis-écrire (audit-technique
            # robustesse du 2026-09-13, même finding que
            # `interviews_audio.transcribe_file_retry` — les deux copies de
            # cette logique portaient le même défaut). Factorisé dans
            # `reprendre_job_echoue_ou_perime` (2026-09-16) : un seul endroit à
            # toucher désormais pour ce patron.
            if reprendre_job_echoue_ou_perime(db, precedent):
                background_tasks.add_task(run_audio_file_job, precedent.id)
            # Perdant de la course : la reprise EST lancée, par l'autre requête.
            # Même écran que le gagnant — de la place de l'utilisateur qui a
            # double-cliqué, il ne s'est rien passé d'autre qu'une reprise.
            return RedirectResponse(
                f"/interviews/{interview_id}/retranscrire", status_code=303
            )
        # Job abouti (résultat non appliqué) ou tranches audio différentes :
        # on repart de zéro plutôt que de mélanger deux passages.
        _oublier_job_retranscription(db, precedent)

    purge_stale_audio_file_jobs(db)
    job = AudioFileJob(
        session_token=uuid.uuid4().hex,
        filename="",
        filenames=tranches,
        interview_id=interview_id,
        status="pending",
        block_seconds=audio_transcribe.FILE_BLOCK_S,
        blocks=[],
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    background_tasks.add_task(run_audio_file_job, job.id)
    return RedirectResponse(
        f"/interviews/{interview_id}/retranscrire", status_code=303
    )


@router.get("/interviews/{interview_id}/retranscrire")
def retranscrire_ecran(
    interview_id: int, request: Request, db: Session = Depends(get_session)
):
    """Écran de suivi : la retranscription tourne en tâche de fond, cet écran
    interroge son avancement puis propose la revue du résultat."""
    interview = _get_interview_libre(db, interview_id)
    job = _job_retranscription_de_cet_entretien(db, interview)
    if job is None:
        return RedirectResponse(f"/interviews/{interview_id}", status_code=303)
    _, absentes = _tranches_audio(interview)
    return templates.TemplateResponse(
        request,
        "interviews/libre_retranscription.html",
        {
            "interview": interview,
            "mission": interview.mission,
            "job": job,
            "nb_tranches": len(job.filenames or []),
            "tranches_absentes": absentes,
        },
    )


@router.post("/interviews/{interview_id}/retranscrire/abandonner")
def retranscrire_abandonner(interview_id: int, db: Session = Depends(get_session)):
    """Jette le résultat d'une retranscription sans l'appliquer. Sortie explicite
    du cas « un résultat abouti attend d'être revu » : sans elle, le bouton
    « Retranscrire » renvoyant désormais sur ce résultat plutôt que de l'écraser,
    rien ne permettrait plus de relancer un passage neuf."""
    interview = _get_interview_libre(db, interview_id)
    job = _job_retranscription(db, interview.id)
    if job is not None:
        _oublier_job_retranscription(db, job)
    return RedirectResponse(f"/interviews/{interview_id}", status_code=303)


@router.get("/interviews/{interview_id}/retranscrire/statut")
def retranscrire_statut(interview_id: int, db: Session = Depends(get_session)):
    """Avancement des DEUX phases (transcription audio puis extraction des
    tours), interrogé en boucle par l'écran de suivi. Endpoint dédié plutôt que
    le `/audio/transcribe-file/status` de l'import : celui-ci ne connaît pas la
    phase d'extraction, et son jeton de session n'a pas à circuler dans une page
    de consultation (l'appartenance à l'entretien suffit ici)."""
    interview = _get_interview_libre(db, interview_id)
    job = _job_retranscription_de_cet_entretien(db, interview)
    if job is None:
        return JSONResponse({"error": "Aucune retranscription en cours."}, status_code=404)
    status = job.status
    erreur = job.error or ""
    if is_audio_file_job_stale(job):
        status = "failed"
        erreur = erreur or (
            "La retranscription ne répond plus (serveur redémarré ?) — relance-la."
        )
    ia = segment_jobs_status(db, job.session_token)
    # Total RÉEL des tranches d'extraction, pas le nombre de jobs déjà créés :
    # `_extraire_tours` les crée un par un juste avant de les exécuter, donc
    # `ia["total"]` valait toujours « ce qui est fait + 1 » — la barre affichait
    # « 2/2 » avec 18 tranches restantes (revue adversariale 2026-07-30).
    # …mais tant qu'AUCUNE tranche n'existe, on rend 0 : c'est ce zéro qui dit à
    # l'écran « phase 1 en cours ». Le calculer depuis les blocs déjà transcrits
    # ferait basculer l'affichage en « extraction » dès le premier bloc.
    ia_total = (
        max(len(tranches_extraction(list(job.blocks or []))), ia["total"])
        if ia["total"]
        else 0
    )
    return JSONResponse(
        {
            "status": status,
            "error": erreur,
            "blocs": len(job.blocks or []),
            "blocs_total": job.total_blocks,
            "tranches_faites": job.files_done or 0,
            "tranches_total": len(job.filenames or []),
            "ia_faites": ia["done"],
            "ia_total": ia_total,
        }
    )


@router.post("/interviews/{interview_id}/retranscrire/appliquer")
def retranscrire_appliquer(
    interview_id: int, request: Request, db: Session = Depends(get_session)
):
    """Assemble le résultat (transcription + tours de parole) et l'affiche pour
    revue — AUCUNE écriture ici : l'entretien n'est modifié qu'à la
    confirmation, comme la régénération d'analyse."""
    interview = _get_interview_libre(db, interview_id)
    # Garde de propriété OBLIGATOIRE ici : c'est la seule route qui construit la
    # proposition d'écrasement. La première version de ce correctif l'avait
    # oubliée — les deux chasseurs de la revue du 2026-07-30 ont prouvé qu'un
    # POST direct (ou un onglet de suivi resté ouvert, dont le poll s'arrête sur
    # `done`) suffisait alors à faire remplacer le contenu d'un entretien par
    # celui d'un autre, les deux autres routes ayant beau être gardées.
    job = _job_retranscription_de_cet_entretien(db, interview)
    if job is None:
        return RedirectResponse(f"/interviews/{interview_id}", status_code=303)
    transcript = "\n\n".join(b for b in (job.blocks or []) if (b or "").strip())
    if not transcript.strip():
        context = _libre_analyse_context(interview)
        context["error"] = job.error or (
            "La retranscription n'a produit aucun texte — l'enregistrement ne "
            "porte peut-être aucune parole audible."
        )
        return templates.TemplateResponse(
            request, "interviews/libre_analyse.html", context
        )

    # Tranche par tranche, jamais sur la transcription entière : une tranche
    # échouée ou bloquée est re-traitée seule (~5 min de matière), le coût reste
    # borné au nombre de tranches à récupérer (leçon du Palier 2, 2026-07-20).
    #
    # …mais BORNÉ AUSSI EN NOMBRE (revue adversariale 2026-07-30) : cette
    # récupération est SYNCHRONE, dans la requête HTTP. La borne « nombre de
    # tranches » du Palier 2 supposait des tranches de 30 min ; ici elles font
    # 5 min, donc un entretien d'1 h 40 en compte ~20 — Ollama indisponible, ce
    # sont 20 × (timeout + relance) dans un seul POST, soit des heures de
    # requête bloquée avant une page d'erreur. On en récupère quelques-unes, et
    # l'écran de revue signale explicitement ce qui manque encore.
    status = segment_jobs_status(db, job.session_token)
    # Fenêtre partagée (revue R3-M1/M3) : mêmes garanties anti-famine que les
    # deux chemins d'enregistrement.
    recover_stalled_or_failed_jobs(
        db,
        _fenetre_recuperation(
            status["jobs"], lambda j: j.turns_result is not None
        ),
    )
    merged = merge_segment_turns(status["jobs"], None)

    # Ce qui MANQUE au résultat proposé, avant tout écrasement : une tranche
    # jamais aboutie (Ollama capricieux, serveur arrêté pendant l'extraction)
    # disparaissait EN SILENCE — `merge_segment_turns` ne retient que les jobs
    # porteurs d'un `turns_result` — et la route ne signalait que le cas 100 %
    # vide. L'utilisateur validait alors un tour de table amputé qui remplaçait
    # définitivement un tour de table complet (et corrigé à la main), sans
    # aucune trace. Le seul garde-fou était le compteur « N tours proposés
    # contre M actuellement », à repérer soi-même.
    attendues = len(tranches_extraction(list(job.blocks or [])))
    abouties = sum(1 for j in status["jobs"] if j.turns_result is not None)
    avertissements: list[str] = []
    if attendues > abouties:
        avertissements.append(
            f"{attendues - abouties} tranche(s) sur {attendues} n'ont pas pu être "
            "structurées en tours de parole : leur contenu MANQUE dans la "
            "proposition ci-dessous. Relance la retranscription pour les "
            "rattraper avant de remplacer le tour de table actuel."
        )
    if job.error:
        # Tranches audio ignorées pendant la transcription (fichier illisible,
        # supprimé) — consignées par `run_audio_file_job`.
        avertissements.append(f"Transcription incomplète : {job.error}.")
    _, absentes = _tranches_audio(interview)
    if absentes:
        avertissements.append(
            f"{absentes} tranche(s) audio référencée(s) par cet entretien sont "
            "introuvables sur le disque : la transcription relancée est trouée."
        )
    if not merged["turns"]:
        context = _libre_analyse_context(interview)
        job_error = next((j.error for j in status["jobs"] if j.error), None)
        context["error"] = job_error or (
            "Aucun tour de parole détecté dans la transcription relancée — "
            "la transcription elle-même reste téléchargeable ci-dessous."
        )
        context["retranscription_transcript"] = transcript
        return templates.TemplateResponse(
            request, "interviews/libre_analyse.html", context
        )

    ancien_transcript, ancien_reconstitue = transcript_of(interview)
    return templates.TemplateResponse(
        request,
        "interviews/libre_retranscription_review.html",
        {
            "interview": interview,
            "mission": interview.mission,
            "transcript": transcript,
            "turns": merged["turns"],
            "ancien_transcript": ancien_transcript,
            "ancien_reconstitue": ancien_reconstitue,
            "nb_tours_actuels": len(interview.turns),
            "avertissements": avertissements,
        },
    )


@router.post("/interviews/{interview_id}/retranscrire/confirmer")
def retranscrire_confirmer(
    interview_id: int,
    transcript: str = Form(""),
    turn_interlocuteur: list[str] = Form([]),
    turn_question: list[str] = Form([]),
    turn_remarque: list[str] = Form([]),
    turn_section_title: list[str] = Form([]),
    db: Session = Depends(get_session),
):
    """Écrit le résultat validé : transcription brute + tours de parole
    remplacés. Le résumé et la répartition ne sont PAS touchés (ils se
    régénèrent depuis les tours par « Régénérer l'analyse », qui a son propre
    écran de revue) — écraser en cascade sans revue serait justement le défaut
    que ces écrans évitent."""
    interview = _get_interview_libre(db, interview_id)
    turns = _parse_turns_from_form(
        turn_interlocuteur, turn_question, turn_remarque, turn_section_title
    )
    if not turns:
        # Rien à écrire plutôt qu'un entretien vidé de ses tours : le formulaire
        # de revue est arrivé sans aucune ligne (manipulation, session expirée).
        raise HTTPException(status_code=400, detail="Aucun tour de parole à enregistrer.")
    if not transcript.strip():
        # Même garde, côté transcription : l'écriture était ASYMÉTRIQUE — les
        # tours étaient TOUJOURS remplacés, `raw_transcript` seulement si le
        # champ arrivait rempli. Un champ caché vidé laissait donc un entretien
        # dont la transcription ne correspond plus à son tour de table, sans
        # aucun signal (revue adversariale 2026-07-30).
        raise HTTPException(
            status_code=400, detail="Transcription absente : rien n'a été remplacé."
        )

    for turn in list(interview.turns):
        db.delete(turn)
    db.flush()
    for position, turn in enumerate(turns):
        db.add(
            InterviewTurn(
                interview_id=interview.id,
                position=position,
                interlocuteur=turn["interlocuteur"],
                question=turn["question"],
                remarque=turn["remarque"],
                section_title=turn["section_title"],
            )
        )
    if transcript.strip():
        interview.raw_transcript = transcript.strip()
    db.commit()

    job = _job_retranscription(db, interview_id)
    if job is not None:
        _oublier_job_retranscription(db, job)
    return RedirectResponse(f"/interviews/{interview_id}", status_code=303)


@router.get("/interviews/{interview_id}/analyse/synthese")
def libre_synthese(interview_id: int):
    """Ancienne URL (contenu désormais fusionné dans /analyse, cf.
    libre_analyse ci-dessus) — conservée en redirection pour ne pas casser un
    lien existant. Le contrôle du mode (400 si pas 'libre') est fait par la
    cible de la redirection."""
    return RedirectResponse(f"/interviews/{interview_id}/analyse", status_code=308)
