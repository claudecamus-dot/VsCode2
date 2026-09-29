"""Constats qualifiés consensus / écart (I2) — écriture à l'import et lecture
pour l'écran. Voir docs/reflexions/spec-restitution-defendable.md.

Le décompte « N sur M » ne vient JAMAIS du texte importé ni d'un modèle :
N = entretiens rattachés au constat ET contributifs, M = entretiens
contributifs (`interviews_contributifs`, la même définition que la couverture
de mission). Un nom qu'on ne sait pas rattacher à un entretien unique n'entre
pas dans N, et il est gardé en clair pour que l'écran le dise.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import Mission, MissionConstat
from app.services.synthese_material import consensus_non_etaye, interviews_contributifs


def _cle_nom(nom: str | None) -> str:
    return " ".join((nom or "").split()).casefold()


_ET_RE = re.compile(r"\s+et\s+", re.IGNORECASE)


def _noms_rattachables(noms: list[str], par_nom: dict[str, list]) -> list[str]:
    """« Dupont et Fils » reste UN nom s'il désigne un entretien ; sinon
    « Alix et Bao » se lit comme deux personnes. Tenter le segment entier
    d'abord évite de casser un nom composé pour rattraper une liste."""
    out: list[str] = []
    for nom in noms:
        if _cle_nom(nom) in par_nom or not _ET_RE.search(nom):
            out.append(nom)
        else:
            out.extend(p for p in _ET_RE.split(nom) if p.strip())
    return out


def apply_constats_import(db: Session, mission: Mission, constats: list[dict]) -> None:
    """Remplace les constats NON édités de la mission par ceux du fichier.

    Liste vide = rubrique absente : rien n'est touché (même règle que la
    synthèse globale, un import sans la rubrique n'efface rien). Un constat
    édité à la main est conservé, et TOUTE ligne importée de même axe et même
    libellé est alors ignorée, quel que soit son type : la requalification à
    la main l'emporte. Entre lignes du fichier, en revanche, un consensus et
    un écart au même libellé font deux constats."""
    if not constats:
        return
    par_nom: dict[str, list] = {}
    for iv in mission.interviews:
        par_nom.setdefault(_cle_nom(iv.interviewee_name), []).append(iv)

    gardes = [c for c in mission.constats if c.edite]
    for c in list(mission.constats):
        if not c.edite:
            mission.constats.remove(c)
    # Deux gardes distinctes. Face à un constat ÉDITÉ, le type est ignoré : sa
    # requalification à la main l'emporte sur celle du fichier. Entre lignes du
    # fichier, le type compte : un consensus et un écart au même libellé sont
    # deux lectures à montrer, pas un doublon à taire.
    edites = {(c.axe_key, _cle_nom(c.libelle)) for c in gardes}
    vus: set[tuple[str, str, str]] = set()

    position = max((c.position for c in gardes), default=-1) + 1
    for item in constats:
        cle = (item["axe_key"], _cle_nom(item["libelle"]))
        if cle in edites or (*cle, item["type"]) in vus:
            continue
        vus.add((*cle, item["type"]))
        porteurs, non_rattaches = [], []
        for nom in _noms_rattachables(item["noms"], par_nom):
            candidats = par_nom.get(_cle_nom(nom), [])
            if len(candidats) == 1:
                if candidats[0] not in porteurs:
                    porteurs.append(candidats[0])
            elif _cle_nom(nom) not in {_cle_nom(n) for n in non_rattaches}:
                # 0 = inconnu dans la mission, 2+ = homonymes : dans les deux
                # cas, choisir serait inventer qui a parlé.
                non_rattaches.append(nom)
        constat = MissionConstat(
            axe_key=item["axe_key"], type=item["type"], libelle=item["libelle"],
            position=position, noms_non_rattaches=", ".join(non_rattaches),
        )
        constat.interviews = porteurs
        mission.constats.append(constat)
        position += 1
    db.flush()


def constats_par_axe(mission: Mission) -> dict[str, list[dict]]:
    """`{axe_key: [{constat, porteurs, n, m, non_etaye}]}` dans l'ordre des
    positions. `non_etaye` ne vaut que pour un consensus."""
    contributifs = {iv.id for iv in interviews_contributifs(mission)}
    m = len(contributifs)
    out: dict[str, list[dict]] = {}
    for c in mission.constats:
        # Les noms affichés sont EXACTEMENT ceux que N compte : un porteur
        # dont l'entretien ne nourrit pas la synthèse n'apparaît pas à côté
        # d'un chiffre qui l'ignore.
        porteurs = [iv for iv in c.interviews if iv.id in contributifs]
        n = len(porteurs)
        out.setdefault(c.axe_key, []).append({
            "constat": c,
            "porteurs": [iv.interviewee_name for iv in porteurs],
            "n": n,
            "m": m,
            "non_etaye": c.type == "consensus" and consensus_non_etaye(n, m),
        })
    return out
