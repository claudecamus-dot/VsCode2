"""Archétypes de slide du deck de restitution (US5.3) et plan extrait d'un deck
d'exemple (US5.2).

`Archetype` énumère les types de slide que `build_presentation` sait produire —
liste établie depuis `build.py` (une valeur par fonction `_slide_*` émise).
`classer_slide` reconnaît l'archétype d'une slide existante de façon
DÉTERMINISTE, sans IA (arbitrage utilisateur) : nom de layout, texte du titre,
formes. `extraire_plan` en dérive la suite ordonnée d'archétypes d'un deck
d'exemple, que `build_presentation(plan=...)` applique pour réordonner et filtrer
les slides existantes — jamais pour en créer de nouveaux types.

Contrat du plan (appliqué par `build_presentation`) :
- la COUVERTURE est toujours gardée — exception assumée au filtrage : un deck
  sans couverture n'est pas un livrable ;
- sommaire et intercalaires ne sont gardés que si le plan les contient ;
- les sections d'un même chapitre restent REGROUPÉES : le chapitre prend le rang
  de sa première section dans le plan, ses sections suivent l'ordre du plan ;
- un plan sans aucun archétype de CONTENU (cf. `plan_applicable`) n'est pas un
  plan : refusé à l'upload, ignoré à l'export — une seule règle partagée.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from enum import StrEnum
from pathlib import Path

from pptx import Presentation

logger = logging.getLogger(__name__)


class Archetype(StrEnum):
    COUVERTURE = "couverture"
    SOMMAIRE = "sommaire"
    CHAPITRE = "chapitre"
    EXECUTIVE_SUMMARY = "executive_summary"
    SYNTHESE = "synthese"
    DIFFICULTES = "difficultes"
    SWOT = "swot"
    MATURITE = "maturite"
    VERBATIMS = "verbatims"
    AXES = "axes"
    MATRICE_PRIORISATION = "matrice_priorisation"
    FICHE_RECO = "fiche_reco"
    KPIS = "kpis"
    RISQUES = "risques"


# Archétypes de structure : ils ne portent pas de matière, un plan fait
# seulement d'eux ne dit rien de l'ordre des sections.
STRUCTURELS = frozenset({Archetype.COUVERTURE, Archetype.SOMMAIRE, Archetype.CHAPITRE})


def plan_applicable(plan) -> bool:
    """Règle UNIQUE (upload, écran, export) : un plan compte s'il contient au
    moins un archétype de contenu."""
    return any(a not in STRUCTURELS for a in plan or [])


LIBELLES: dict[Archetype, str] = {
    Archetype.COUVERTURE: "Couverture",
    Archetype.SOMMAIRE: "Sommaire",
    Archetype.CHAPITRE: "Intercalaires de chapitre",
    Archetype.EXECUTIVE_SUMMARY: "Executive Summary",
    Archetype.SYNTHESE: "Synthèse globale",
    Archetype.DIFFICULTES: "Difficultés",
    Archetype.SWOT: "Matrice SWOT",
    Archetype.MATURITE: "Maturité par pilier",
    Archetype.VERBATIMS: "Paroles d'acteurs",
    Archetype.AXES: "Vue d'ensemble des axes",
    Archetype.MATRICE_PRIORISATION: "Matrice de priorisation",
    Archetype.FICHE_RECO: "Fiches recommandation",
    Archetype.KPIS: "Indicateurs de suivi",
    Archetype.RISQUES: "Risques et contrôles",
}

# Règles sur le titre normalisé (minuscules, sans accents, apostrophes droites,
# suffixe de pagination « (k/n) » retiré). L'ordre compte : la plus spécifique
# d'abord (« matrice des risques » avant tout « matrice »).
_REGLES_TITRE: list[tuple[re.Pattern, Archetype]] = [
    # Fiche reco d'abord : « 1.2 — Titre » (et ses continuations « (suite — …) »)
    # — le titre libre d'une reco peut contenir « maturité », « risques »…
    (re.compile(r"^\d+\.\d+\s*[—–-]"), Archetype.FICHE_RECO),
    (re.compile(r"^sommaire\b"), Archetype.SOMMAIRE),
    (re.compile(r"^executive summary\b"), Archetype.EXECUTIVE_SUMMARY),
    (re.compile(r"^synthese globale\b"), Archetype.SYNTHESE),
    (re.compile(r"^difficultes\b"), Archetype.DIFFICULTES),
    (re.compile(r"^(matrice )?swot\b"), Archetype.SWOT),
    (re.compile(r"^(grille de )?maturite\b"), Archetype.MATURITE),
    (re.compile(r"^paroles d'acteurs\b|^verbatims?\b"), Archetype.VERBATIMS),
    (re.compile(r"^matrice des risques\b|^risques et controles\b"), Archetype.RISQUES),
    (re.compile(r"^indicateurs de suivi\b|^kpis?\b"), Archetype.KPIS),
    (re.compile(r"^matrice (de priorisation|effort|valeur)\b"), Archetype.MATRICE_PRIORISATION),
    (re.compile(r"construites autour de ces axes|^axes de recommandation\b"), Archetype.AXES),
]

_SOUS_TITRE_COUVERTURE = "synthese transverse & recommandations"


def _normaliser(texte: str) -> str:
    t = unicodedata.normalize("NFKD", texte or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("’", "'").replace("\n", " ").replace("\x0b", " ").lower()
    t = re.sub(r"\s*\(\d+/\d+\)\s*$", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _textes(slide) -> list[str]:
    return [sh.text_frame.text for sh in slide.shapes
            if sh.has_text_frame and sh.text_frame.text.strip()]


def classer_slide(slide) -> Archetype | None:
    """Archétype d'une slide, ou None si aucune règle ne la reconnaît.
    0 token : nom de layout d'abord (couverture / chapitre du gabarit OCTO),
    puis titre, puis replis de forme pour le deck synthétique (sans gabarit)."""
    layout = _normaliser(slide.slide_layout.name if slide.slide_layout is not None else "")
    if "couverture" in layout or "cover" in layout:
        return Archetype.COUVERTURE
    if "chapitre" in layout:
        return Archetype.CHAPITRE

    textes = _textes(slide)
    titre_shape = slide.shapes.title
    titre = titre_shape.text_frame.text if titre_shape is not None else ""
    if not titre.strip() and textes:
        titre = textes[0]
    titre_n = _normaliser(titre)
    for motif, archetype in _REGLES_TITRE:
        if motif.search(titre_n):
            return archetype

    # Replis de forme (deck synthétique, sans layouts de marque).
    normalises = [_normaliser(t) for t in textes]
    if _SOUS_TITRE_COUVERTURE in normalises:
        return Archetype.COUVERTURE
    # Intercalaire dessiné : exactement « NN » puis son intitulé, dans cet
    # ordre — une slide de contenu portant un chiffre court (« 12 ») n'y
    # ressemble pas, son titre vient d'abord.
    if len(normalises) == 2 and re.fullmatch(r"\d{2}", normalises[0]):
        return Archetype.CHAPITRE
    return None


def extraire_plan(pptx_path) -> list[Archetype]:
    """Suite ordonnée des archétypes d'un deck d'exemple : une entrée par
    archétype, à sa PREMIÈRE apparition (5 slides de synthèse ou 12 fiches reco
    comptent pour une). Les slides non classées sont ignorées et journalisées.
    Lève une exception de python-pptx si le fichier n'est pas un .pptx lisible."""
    prs = Presentation(str(pptx_path) if isinstance(pptx_path, (str, Path)) else pptx_path)
    plan: list[Archetype] = []
    for i, slide in enumerate(prs.slides, 1):
        archetype = classer_slide(slide)
        if archetype is None:
            logger.info("extraire_plan : slide %d non classée, ignorée (%s)", i, pptx_path)
            continue
        if archetype not in plan:
            plan.append(archetype)
    return plan


def normaliser_plan(plan) -> list[Archetype]:
    """Plan fourni par un appelant → archétypes connus, dédoublonnés, dans
    l'ordre. Une valeur inconnue est ignorée et journalisée (jamais d'erreur)."""
    propre: list[Archetype] = []
    for valeur in plan or []:
        try:
            archetype = Archetype(valeur)
        except ValueError:
            logger.warning("plan de deck : archétype inconnu %r ignoré", valeur)
            continue
        if archetype not in propre:
            propre.append(archetype)
    return propre


_CACHE_PLANS: dict[tuple[str, int, int], list[Archetype]] = {}


def extraire_plan_fichier(chemin: Path) -> list[Archetype]:
    """`extraire_plan` mémoïsé par (chemin, mtime, taille) : l'écran d'aperçu le
    demande à chaque rendu, le deck ne change qu'à un nouvel envoi."""
    st = Path(chemin).stat()
    cle = (str(chemin), st.st_mtime_ns, st.st_size)
    if cle not in _CACHE_PLANS:
        if len(_CACHE_PLANS) > 64:
            _CACHE_PLANS.clear()
        _CACHE_PLANS[cle] = extraire_plan(chemin)
    return list(_CACHE_PLANS[cle])
