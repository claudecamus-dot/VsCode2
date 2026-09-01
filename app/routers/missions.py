"""CRUD des missions (US0.2)."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import RECORDINGS_DIR, get_session
from ..models import Interview, Mission, Trame
from ..services import mission_backups
from ..services.mode import est_mode_demo
from ..templating import templates

router = APIRouter(prefix="/missions", tags=["missions"])
logger = logging.getLogger(__name__)


def _get_mission(db: Session, mission_id: int) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable.")
    return mission


def _draft_vide(mission: Mission) -> bool:
    """Brouillon abandonné sans contenu : ni entretien enregistré, ni trame
    remplie (la trame vide « Trame d'entretien » créée d'office par le parcours
    structuré ne compte pas comme du contenu). Seuls ces brouillons-là sont
    éligibles au nettoyage groupé — un brouillon avec de la matière se reprend
    via /finaliser, il ne se supprime qu'un par un, explicitement."""
    return (
        mission.is_draft
        and not mission.interviews
        and (mission.trame is None or not mission.trame.themes)
    )


@router.get("")
def list_missions(request: Request, db: Session = Depends(get_session)):
    # Filtré par le mode courant (P5a-1) : démo et réel ne se mélangent jamais
    # dans la liste. Le compteur de brouillons vides porte donc sur le seul mode
    # affiché (il itère `missions`, déjà filtré).
    missions = db.scalars(
        select(Mission)
        .where(Mission.is_demo.is_(est_mode_demo(request)))
        .order_by(Mission.created_at.desc())
    ).all()
    # Compté ici pour que le lien vers l'inventaire global soit ACTIONNABLE :
    # un lien nu vers un écran presque toujours vide ne serait jamais cliqué,
    # et c'est le seul endroit d'où l'on découvre que 75,8 Mo d'audio attendent
    # quelque part (constat C1). Le comptage relit le répertoire et les
    # références — négligeable à l'échelle de ce poste, et il n'a qu'une source
    # de vérité, la même fonction que l'écran lui-même.
    orphelins_globaux = mission_backups.lister_orphelins_globaux(RECORDINGS_DIR, db)
    return templates.TemplateResponse(
        request,
        "missions/list.html",
        {
            "missions": missions,
            "nb_brouillons_vides": sum(1 for m in missions if _draft_vide(m)),
            "nb_audio_orphelin": len(orphelins_globaux["orphelins"]),
            "mo_audio_orphelin": round(orphelins_globaux["taille_totale"] / 1048576, 1),
        },
    )


@router.post("/brouillons/nettoyer")
def nettoyer_brouillons(request: Request, db: Session = Depends(get_session)):
    """Supprime d'un coup les missions brouillon vides (abandonnées avant
    toute saisie — elles s'accumulent car chaque entrée « entretien libre/
    structuré » en crée une, cf. entretiens.py). Déclenché par un bouton
    explicite de la liste, jamais automatiquement. Borné au mode courant (P5a-1)
    — un nettoyage en réel ne touche jamais aux brouillons démo, et inversement.

    Chemin frère de `delete_mission`, et il n'emporte VOLONTAIREMENT pas
    l'audio (2026-09-01). Un brouillon `_draft_vide` est exactement l'état d'un
    enregistrement EN COURS : pendant le wizard, les tranches déjà uploadées ne
    sont référencées que par un champ caché du formulaire, l'entretien n'existe
    qu'à la confirmation. Une passe groupée sur N brouillons que l'utilisateur
    ne regarde pas un par un détruirait alors l'audio d'une séance qui tourne
    dans un autre onglet — c'est la raison d'être d'`ORPHELIN_RECENT_S`.
    L'audio de ces brouillons retombe dans l'inventaire global
    (`/missions/audio-orphelin`) avec sa date : visible, donc supprimable, sans
    que personne n'ait décidé à la place de l'utilisateur. La cascade reste
    réservée à `delete_mission`, geste explicite sur UNE mission qu'il a sous
    les yeux."""
    q = select(Mission).where(
        Mission.is_draft.is_(True), Mission.is_demo.is_(est_mode_demo(request))
    )
    for mission in db.scalars(q).all():
        if _draft_vide(mission):
            db.delete(mission)
    db.commit()
    return RedirectResponse("/missions", status_code=303)


@router.get("/new")
def new_mission(request: Request):
    return templates.TemplateResponse(request, "missions/form.html", {})


@router.post("")
def create_mission(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_session),
):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Le nom est obligatoire.")
    mission = Mission(
        name=name,
        description=description.strip() or None,
        is_demo=est_mode_demo(request),
        trame=Trame(name="Trame d'entretien"),
    )
    db.add(mission)
    db.commit()
    return RedirectResponse(f"/missions/{mission.id}", status_code=303)


@router.get("/{mission_id}/finaliser")
def finaliser_mission_form(
    mission_id: int, request: Request, db: Session = Depends(get_session)
):
    """Mission brouillon (incr.9, US9.2) née d'un entretien libre ou
    structuré à mission différée : on la nomme maintenant, ou on rattache
    ses entretiens/sa trame à une mission existante. Le rattachement n'est
    proposé que vers des missions sans trame déjà définie quand cette
    mission brouillon en porte une — évite le conflit « deux trames »
    (`Mission.trame` reste 1:1)."""
    mission = _get_mission(db, mission_id)
    if not mission.is_draft:
        return RedirectResponse(f"/missions/{mission.id}", status_code=303)

    # Même mode que le brouillon (P5a-1) : on ne rattache jamais un brouillon démo
    # à une mission réelle, ni l'inverse (le brouillon porte son propre is_demo,
    # posé à sa création selon le mode courant).
    query = select(Mission).where(
        Mission.is_draft.is_(False),
        Mission.id != mission.id,
        Mission.is_demo.is_(mission.is_demo),
    )
    if mission.trame is not None:
        query = query.where(~Mission.trame.has())
    eligible = db.scalars(query.order_by(Mission.created_at.desc())).all()

    return templates.TemplateResponse(
        request,
        "missions/finaliser.html",
        {"mission": mission, "eligible": eligible},
    )


@router.post("/{mission_id}/finaliser")
def finaliser_mission(
    mission_id: int,
    action: str = Form(...),
    name: str = Form(""),
    description: str = Form(""),
    target_mission_id: int | None = Form(None),
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    if not mission.is_draft:
        raise HTTPException(status_code=400, detail="Cette mission est déjà finalisée.")

    if action == "nommer":
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Le nom est obligatoire.")
        mission.name = name
        mission.description = description.strip() or None
        mission.is_draft = False
        db.commit()
        return RedirectResponse(f"/missions/{mission.id}", status_code=303)

    if action == "rattacher":
        target = db.get(Mission, target_mission_id) if target_mission_id else None
        if (
            target is None
            or target.is_draft
            or target.id == mission.id
            or target.is_demo != mission.is_demo  # jamais de rattachement cross-mode
        ):
            raise HTTPException(status_code=400, detail="Mission cible invalide.")
        if mission.trame is not None and target.trame is not None:
            raise HTTPException(
                status_code=400,
                detail="La mission cible a déjà une trame — rattachement impossible.",
            )

        db.execute(
            update(Interview)
            .where(Interview.mission_id == mission.id)
            .values(mission_id=target.id)
        )
        if mission.trame is not None:
            db.execute(
                update(Trame)
                .where(Trame.id == mission.trame.id)
                .values(mission_id=target.id)
            )
        db.commit()

        # Ré-interroge à froid : la mission brouillon n'a alors plus aucun
        # enfant rattaché, donc la supprimer ne déclenche aucune cascade sur
        # la trame/les entretiens qu'on vient de reparenter.
        db.expire_all()
        orphan = db.get(Mission, mission.id)
        db.delete(orphan)
        db.commit()
        return RedirectResponse(f"/missions/{target.id}", status_code=303)

    raise HTTPException(status_code=400, detail="Action inconnue.")


@router.get("/audio-orphelin")
def audio_orphelin(request: Request, db: Session = Depends(get_session)):
    """Inventaire GLOBAL des fichiers audio qu'aucun onglet Backup ne montre.

    Le rattrapage du constat C1 : l'onglet Backup d'une mission ne peut lister
    que ses propres fichiers, donc rien ne montrait ceux d'une mission
    supprimée, ni ceux nommés sans préfixe (`import_<ts>_<hex>`, convention
    d'avant le 2026-09-01). Depuis que l'audio ne se supprime que par une
    action de l'utilisateur, un fichier qu'aucun écran n'atteint ne peut plus
    jamais être supprimé : la règle produit se retournait contre elle-même.

    C'est le seul écran habilité à montrer un fichier dont on ne sait plus de
    quelle mission il vient — d'où l'affichage systématique de la RAISON de son
    orphelinat, pour ne pas présenter un fourre-tout sans logique."""
    return templates.TemplateResponse(
        request,
        "missions/audio_orphelin.html",
        {"inventaire": mission_backups.lister_orphelins_globaux(RECORDINGS_DIR, db)},
    )


def _orphelin_global(filename: str, db: Session) -> Path:
    """Résout un nom en fichier orphelin global, ou lève.

    Facteur commun de l'écoute et de la suppression : la garde doit être la
    MÊME des deux côtés, sinon l'écran servirait des fichiers qu'il ne sait pas
    supprimer, ou l'inverse. Le statut d'orphelin est RECALCULÉ à chaque appel,
    jamais cru sur parole depuis l'URL — sans quoi ces deux routes seraient un
    « sers ou supprime n'importe quel enregistrement par son nom », contournant
    toutes les gardes d'appartenance de l'onglet Backup. Un écran de nettoyage
    ne doit pas être la porte dérobée des écrans qu'il complète."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    inventaire = mission_backups.lister_orphelins_globaux(RECORDINGS_DIR, db)
    if filename not in {e["filename"] for e in inventaire["orphelins"]}:
        raise HTTPException(
            status_code=404,
            detail="Ce fichier n'est pas orphelin — passe par l'onglet Backup de "
            "sa mission.",
        )
    return RECORDINGS_DIR / filename


@router.get("/audio-orphelin/{filename}")
def get_audio_orphelin(filename: str, db: Session = Depends(get_session)):
    """Sert un orphelin global (écoute et téléchargement).

    Indispensable, pas accessoire : ces fichiers ne sont rattachés à aucune
    mission, donc `get_record_backup` les refuse — sans cette route, l'écran
    demanderait de supprimer définitivement des dizaines de mégaoctets d'audio
    d'entretien sans aucun moyen de vérifier ce qu'ils contiennent."""
    chemin = _orphelin_global(filename, db)
    if not chemin.is_file():
        raise HTTPException(status_code=404, detail="Enregistrement introuvable.")
    return FileResponse(
        chemin, media_type=mission_backups.media_type_audio(filename), filename=filename
    )


@router.post("/audio-orphelin/{filename}/delete")
def delete_audio_orphelin(filename: str, db: Session = Depends(get_session)):
    """Supprime un fichier de l'inventaire global.

    Trois gardes, dans cet ordre — la troisième est la vraie. Le nom ne doit
    pas permettre de sortir de `data/recordings/` (même filtre que
    `get_record_backup`) ; le fichier doit exister ; et il doit être RECALCULÉ
    comme orphelin global au moment de la suppression, jamais cru sur parole
    depuis l'URL. Sans ce recalcul, cette route serait un « supprime n'importe
    quel enregistrement par son nom » qui contournerait toutes les gardes
    d'appartenance de l'onglet Backup — un écran de nettoyage ne doit pas être
    la porte dérobée des écrans qu'il complète."""
    chemin = _orphelin_global(filename, db)
    try:
        chemin.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Suppression de l'orphelin %s impossible : %s", filename, exc)
        raise HTTPException(
            status_code=409,
            detail="Fichier momentanément verrouillé — réessaie dans un instant.",
        ) from exc
    return RedirectResponse("/missions/audio-orphelin", status_code=303)


@router.get("/{mission_id}")
def mission_detail(
    mission_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    mission = _get_mission(db, mission_id)
    resp = templates.TemplateResponse(
        request,
        "missions/detail.html",
        {
            "mission": mission,
            # Onglet « Backup » : les enregistrements audio vivent sur disque,
            # la base n'en porte que des références (cf. mission_backups).
            "backups": mission_backups.lister_backups(mission, RECORDINGS_DIR),
        },
    )
    # Mémorise la dernière mission consultée (défer revue UX 2026-07-23, item 18) :
    # l'écran « Démarrer » propose de la reprendre — un clic réflexe sur la nav n'y
    # fait plus perdre son fil. Cookie de session simple, brouillons exclus (leur
    # reprise passe déjà par /finaliser sur la liste des missions).
    # Limites assumées (revue adversariale 2026-07-23) : le cookie n'est posé que
    # par la fiche (les écrans profonds — synthèse, capture — ne le rafraîchissent
    # pas : la fiche est le hub par lequel on entre dans une mission), et un id
    # SQLite réutilisé après suppression peut désigner une autre mission — le
    # garde de /demarrer (existence + mode + non-brouillon) borne le risque à une
    # suggestion inexacte, jamais une erreur.
    if not mission.is_draft:
        resp.set_cookie(
            "derniere_mission", str(mission.id), max_age=60 * 60 * 24 * 30,
            samesite="lax", path="/", httponly=True,
        )
    return resp


@router.post("/{mission_id}/name")
def update_mission_name(
    mission_id: int,
    name: str = Form(...),
    db: Session = Depends(get_session),
):
    """Renomme la mission (autosave HTMX depuis la page mission) — le nom sert de
    titre au deck PPT, jusqu'ici non modifiable après création (US demandée
    2026-07-22). Un nom vide est refusé (le nom est obligatoire)."""
    mission = _get_mission(db, mission_id)
    name = name.strip()
    if not name:
        return HTMLResponse('<span class="saved error">⚠ nom obligatoire</span>')
    mission.name = name
    db.commit()
    return HTMLResponse('<span class="saved">✓ enregistré</span>')


@router.post("/{mission_id}/delete")
def delete_mission(mission_id: int, db: Session = Depends(get_session)):
    """Supprime la mission ET l'audio qu'elle possède (2026-09-01, constat C1).

    Jusqu'ici c'était un `db.delete` nu, et il fabriquait des fichiers
    indestructibles : l'onglet Backup globbe `{mission.id}_*` et exige un objet
    `Mission`, donc la mission partie, plus aucun écran ne montrait son audio.
    Tant que le serveur purgeait derrière lui c'était sans conséquence ; depuis
    que l'audio ne se supprime QUE par une action de l'utilisateur, ces
    fichiers restaient là pour toujours — 75,8 Mo mesurés dans ce cas sur
    l'installation réelle au moment du correctif.

    Supprimer l'audio ici ne contredit PAS la règle produit : c'est bien une
    action de l'utilisateur sur le site, la plus explicite qui soit — il
    supprime la mission entière. Ce qu'on retire, c'est la possibilité de
    perdre l'accès à un fichier sans jamais avoir demandé sa suppression.

    Un fichier verrouillé (lecture en cours) n'empêche pas la suppression de la
    mission : il retombe dans l'inventaire global (`/missions/audio-orphelin`),
    qui existe précisément pour rattraper ce qu'aucune mission ne réclame
    plus. Échouer ici laisserait l'utilisateur sans issue."""
    mission = db.get(Mission, mission_id)
    if mission is None:
        return RedirectResponse("/missions", status_code=303)

    # La liste se calcule AVANT le commit (elle a besoin de la mission et de ses
    # entretiens), mais les fichiers ne partent qu'APRÈS (revue adversariale du
    # 2026-09-01, constat A6). Dans l'autre ordre, un `db.commit()` qui échoue —
    # « database is locked » est réaliste ici, des jobs de fond écrivent en
    # parallèle — laissait la mission en place et son audio détruit pour de bon.
    # Cet ordre-ci ne peut rater que dans le sens réparable : un unlink en échec
    # laisse un orphelin, et l'écran « audio sans mission » existe pour ça.
    fichiers = mission_backups.fichiers_de_mission(mission, RECORDINGS_DIR)
    db.delete(mission)
    db.commit()
    for chemin in fichiers:
        try:
            chemin.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "Audio de la mission %s non supprimé (%s) : %s", mission_id, chemin.name, exc
            )
    return RedirectResponse("/missions", status_code=303)
