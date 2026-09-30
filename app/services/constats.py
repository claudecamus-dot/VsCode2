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

from app.models import CONSTAT_TYPE_LABELS, Mission, MissionConstat
from app.services.synthese_material import (
    consensus_non_etaye,
    ecart_majoritaire,
    interviews_contributifs,
)


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

    def resoudre(item):
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
        return porteurs, non_rattaches

    constats = [_parenthese_commentaire(item, par_nom) for item in constats]
    _remplacer_constats(db, mission, constats, resoudre)


def _parenthese_commentaire(item: dict, par_nom: dict[str, list]) -> dict:
    """« - [écart] Coût du run jugé élevé (hors licences) » : la parenthèse
    finale était lue comme des porteurs — libellé tronqué, « hors licences »
    affiché « Non rattaché », et la reco qui cite le libellé complet ne le
    retrouvait plus (revue 2026-09-29).

    Segment par segment : un GROUPE DE MOTS commençant par une minuscule, qui
    ne désigne aucun entretien, est un commentaire et rejoint le libellé ;
    tout autre segment reste un nom. « (hors licences, Alix) » garde donc Alix
    comme porteur (revue 2026-09-30 : le cas mixte restait lu en noms). Un nom
    inconnu, en capitale (« Inconnu ») ou d'un seul mot (« dupont »), reste une
    alerte « Non rattaché ». Limite assumée : « (de Villiers) » passe pour un
    commentaire si aucun entretien ne porte ce nom exact."""
    if not item.get("parenthese"):
        return item
    commentaires, noms = [], []
    for seg in item["noms"]:
        s = seg.strip()
        if (s[:1].islower() and len(s.split()) > 1
                and not any(_cle_nom(n) in par_nom for n in _noms_rattachables([s], par_nom))):
            commentaires.append(s)
        else:
            noms.append(seg)
    if not commentaires:
        return item
    # Aucun porteur restant : le texte BRUT de la parenthèse, tel qu'écrit (le
    # séparateur peut être « ; » — la reco qui cite le libellé le cite ainsi).
    # Cas mixte : seuls les commentaires sont recollés, donc avec « , ».
    texte = item["parenthese"] if not noms else ", ".join(commentaires)
    return {**item, "libelle": f"{item['libelle']} ({texte})", "noms": noms}


def apply_constats_ia(db: Session, mission: Mission, constats: list[dict]) -> bool:
    """Écrit les constats proposés par l'IA (`synthese_ai.generate_constats`).

    Même contrat que l'import : liste vide = rien n'est touché (un modèle muet
    n'efface pas des constats existants), constat `edite` jamais écrasé ni
    doublé, liens reco -> constat reposés. Les porteurs viennent d'identifiants
    d'entretien DÉJÀ validés contre la mission ; ceux que le modèle a inventés
    arrivent dans `ids_rejetes` et restent visibles en clair
    (`noms_non_rattaches`) sans jamais entrer dans le décompte N.
    Rend True si quelque chose a été écrit."""
    if not constats:  # modèle muet : les constats existants restent
        return False
    par_id = {iv.id: iv for iv in mission.interviews}

    def resoudre(item):
        porteurs = []
        for iid in item.get("interview_ids") or []:
            iv = par_id.get(iid)
            if iv is not None and iv not in porteurs:
                porteurs.append(iv)
        rejetes = [f"E{x} (hors mission)" if isinstance(x, int) else f"{x} (non reconnu)"
                   for x in item.get("ids_rejetes") or []]
        return porteurs, rejetes

    _remplacer_constats(db, mission, constats, resoudre)
    return True


def _remplacer_constats(db: Session, mission: Mission, constats: list[dict], resoudre) -> None:
    """Cœur commun import / IA : `resoudre(item) -> (porteurs, non_rattaches)`."""
    gardes = [c for c in mission.constats if c.edite]
    # Les liens reco -> constat portés par un constat qu'on va supprimer :
    # la CASCADE les efface, et un réimport limité à `## CONSTATS` (recos non
    # réécrites) laisserait les fiches sans « Fondée sur » ni alerte (revue
    # 2026-09-29). On les note par (axe, libellé, type) pour les reposer.
    liens = {}
    for axe in mission.recommendation_axes:
        for reco in axe.recommendations:
            perdus = [(c.axe_key, _cle_nom(c.libelle), c.type, c.libelle)
                      for c in reco.constats if not c.edite]
            if perdus:
                liens[reco] = perdus
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
        porteurs, non_rattaches = resoudre(item)
        constat = MissionConstat(
            axe_key=item["axe_key"], type=item["type"], libelle=item["libelle"],
            position=position, noms_non_rattaches=", ".join(non_rattaches),
        )
        constat.interviews = porteurs
        mission.constats.append(constat)
        position += 1
    db.flush()
    _reposer_liens_recos(mission, liens)


def _reposer_liens_recos(mission: Mission, liens: dict) -> None:
    """Relie chaque reco aux constats recréés de même (axe, libellé, type) ;
    ceux que le nouveau fichier ne contient plus restent visibles en clair dans
    `constats_non_rattaches` (l'écran alerte) au lieu de disparaître."""
    par_cle = {(c.axe_key, _cle_nom(c.libelle), c.type): c for c in mission.constats}
    for reco, perdus in liens.items():
        gardes = [c for c in reco.constats if c in mission.constats]
        deja = {_cle_nom(x) for x in reco.constats_non_rattaches.split(";") if x.strip()}
        introuvables = []
        for axe_key, cle, type_, libelle in perdus:
            nouveau = par_cle.get((axe_key, cle, type_))
            if nouveau is None:
                if cle not in deja:
                    introuvables.append(libelle)
                    deja.add(cle)
            elif nouveau not in gardes:
                gardes.append(nouveau)
        reco.constats = gardes
        if introuvables:
            reco.constats_non_rattaches = "; ".join(
                [x.strip() for x in reco.constats_non_rattaches.split(";") if x.strip()]
                + introuvables)


def lier_aux_constats(recommandation, libelles: list[str], mission: Mission) -> None:
    """Rattache une reco aux constats de la mission désignés par leur LIBELLÉ.

    Un libellé porté par plusieurs constats (consensus ET écart, ou deux
    axes) les lie tous : les deux lectures motivent la reco. Un libellé
    inconnu n'est pas inventé : il reste en clair dans
    `constats_non_rattaches`, que l'écran affiche."""
    par_libelle: dict[str, list[MissionConstat]] = {}
    for c in mission.constats:
        par_libelle.setdefault(_cle_nom(c.libelle), []).append(c)
    lies, inconnus = [], []
    for libelle in _recoller(libelles, par_libelle):
        trouves = par_libelle.get(_cle_nom(libelle), [])
        if not trouves:
            if _cle_nom(libelle) not in {_cle_nom(x) for x in inconnus}:
                inconnus.append(libelle)
        for c in trouves:
            if c not in lies:
                lies.append(c)
    recommandation.constats = lies
    recommandation.constats_non_rattaches = "; ".join(inconnus)


def constats_par_axe(mission: Mission) -> dict[str, list[dict]]:
    """`{axe_key: [{constat, porteurs, n, m, non_etaye, ecart_majoritaire}]}`
    dans l'ordre des positions. `non_etaye` ne vaut que pour un consensus,
    `ecart_majoritaire` que pour un écart : le décompte contredit le jugement."""
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
            "ecart_majoritaire": c.type == "ecart" and ecart_majoritaire(n, m),
        })
    return out


def texte_fondee_sur(reco, lignes: dict[int, dict]) -> str:
    """Rubrique « Fondée sur » de la fiche reco du deck : `libellé (Type, N/M)`
    par constat cité, séparés par « ; ». Mêmes chiffres que l'écran (`lignes`
    = constats_par_axe indexé par id). Les libellés non rattachés restent une
    alerte d'écran : le deck ne restitue que ce qui est étayé par la mission."""
    morceaux = []
    for c in getattr(reco, "constats", None) or []:
        ligne = lignes.get(c.id)
        type_ = CONSTAT_TYPE_LABELS.get(c.type, c.type)
        if ligne and ligne["non_etaye"]:
            # Même signal que l'écran : un consensus à 2 sur 9 ne part pas chez
            # le client comme une mesure (risque produit n°1 de la spec, l.137).
            type_ += " non étayé"
        elif ligne and ligne["ecart_majoritaire"]:
            type_ += " porté par la majorité"
        # Sans entretien exploité (M=0), l'écran masque le compte : le deck aussi.
        compte = f", {ligne['n']}/{ligne['m']}" if ligne and ligne["m"] else ""
        morceaux.append(f"{c.libelle} ({type_}{compte})")
    return " ; ".join(morceaux)


def _recoller(libelles: list[str], connus: dict) -> list[str]:
    """La puce `Constats :` sépare par « ; » — un libellé qui en contient un
    arrivait en deux fragments dont aucun ne se rattachait (revue 2026-09-29).
    On recolle les fragments VOISINS, au plus long d'abord, quand leur réunion
    désigne un constat connu ; sinon chacun reste tel quel."""
    def cle(x: str) -> str:  # « a ; b » et « a; b » : même libellé
        return _cle_nom(re.sub(r"\s*;\s*", " ; ", x))

    par_cle = {cle(k): k for k in connus}
    out, i = [], 0
    while i < len(libelles):
        for j in range(len(libelles), i + 1, -1):
            trouve = par_cle.get(cle(" ; ".join(libelles[i:j])))
            # Jamais quand CHAQUE fragment désigne déjà un constat : « a » et
            # « b » cités ensemble restent deux liens, même si « a ; b »
            # existe aussi (revue 2026-09-30 : fusion silencieuse).
            if trouve is not None and not all(cle(x) in par_cle for x in libelles[i:j]):
                out.append(trouve)
                i = j
                break
        else:
            out.append(libelles[i])
            i += 1
    return out
