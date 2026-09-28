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

# Échelle typographique du deck — « une seule source de verite » (pptx_deck) :
# les tailles servant à reconnaître un archétype sont les MÊMES que celles qui
# le dessinent. Pas de cycle : `pptx_deck` n'importe rien de ce paquet.
from ..pptx_deck import TYPE as _TYPE

logger = logging.getLogger(__name__)


class Archetype(StrEnum):
    COUVERTURE = "couverture"   # page de GARDE (ne pas confondre avec BASE_ANALYSE)
    BASE_ANALYSE = "base_analyse"   # base probante : couverture des entretiens
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
    # La page de GARDE se reconnait a son layout, bien avant d'arriver ici :
    # ce motif ne peut donc pas la capter malgre le mot « analyse ».
    (re.compile(r"^base de l'analyse\b"), Archetype.BASE_ANALYSE),
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
    # ressemble pas, son titre vient d'abord. Le 2e texte doit en plus être
    # composé à l'ÉCHELLE d'un titre face au nombre (cf.
    # `_echelle_d_intercalaire`) : « 12 » suivi de « risques identifiés » en
    # petit corps est une carte chiffre-clé, pas un intercalaire.
    if (len(normalises) == 2 and re.fullmatch(r"\d{2}", normalises[0])
            and _echelle_d_intercalaire(slide, textes[0], textes[1])):
        return Archetype.CHAPITRE
    return None


def _taille_max_pt(slide, texte: str) -> float | None:
    """Plus grande taille de police des runs des formes portant EXACTEMENT ce
    texte, ou None si la taille n'est pas MESURABLE.

    Deux choix explicites, chacun payant un défaut mesuré de la version d'avant :

    - TOUTES les formes qui portent ce texte sont mesurées, et le maximum est
      pris — la version d'avant rendait à la PREMIÈRE forme trouvée, donc un
      titre dupliqué dans une forme cachée à 8pt faisait mesurer la mauvaise.
    - la taille d'un run est sa taille EFFECTIVE, résolue le long de la chaîne
      d'héritage (cf. `_taille_effective_pt`) ;
    - une forme dont au moins un run n'a AUCUNE taille résoluble n'est pas
      mesurable : l'héritage du layout est la norme sur un gabarit client, et un
      max calculé sur les seuls runs explicites vaut moins que rien (un titre
      dont le 1er run hérite du 28pt du layout et le 2e est à 8pt rendait 8.0,
      ni None ni une vraie taille). On rend None : inconnu, pas « petit »."""
    tailles: list[float] = []
    for sh in slide.shapes:
        if not sh.has_text_frame or sh.text_frame.text != texte:
            continue
        chaine = _chaine_lst_styles(slide, sh)
        runs_pt: list[float | None] = []
        for p in sh.text_frame.paragraphs:
            p_pr = p._p.find(_A + "pPr")
            niveau = _niveau(p_pr)
            for r in p.runs:
                if r.text:
                    runs_pt.append(_taille_effective_pt(r, p_pr, niveau, chaine))
        if not runs_pt or any(t is None for t in runs_pt):
            return None
        tailles.append(max(runs_pt))
    return max(tailles) if tailles else None


# Résolution de la taille EFFECTIVE d'un run, que python-pptx n'expose pas
# (`font.size` ne lit que le run). Chaîne suivie, du plus proche au plus loin :
# run `a:rPr@sz` → paragraphe `a:pPr/a:defRPr@sz` → `a:lstStyle` de la forme →
# (placeholder seulement) `a:lstStyle` du placeholder homologue du LAYOUT (même
# idx, sinon même type) → STOP. Tailles XML en centièmes de point ; une valeur
# illisible (`sz="12.5"`, `sz="abc"`) vaut « inconnue », jamais une exception.
#
# Seuls la slide et son LAYOUT disent quelque chose de la COMPOSITION de la
# slide : un layout « Section Header » client déclare ses propres tailles sur
# ses placeholders. Le MASTER, lui, est générique au gabarit entier — ni son
# placeholder, ni `p:txStyles` (titleStyle / bodyStyle), ni `otherStyle`, ni
# `p:defaultTextStyle` ne sont consultés. Même raison pour tous : ces défauts
# s'appliquent à N'IMPORTE QUELLE paire titre + corps (44/32 dans le gabarit
# python-pptx, rapport 0.73 ; 18/18 pour otherStyle, rapport 1) et feraient
# conclure « intercalaire » sur une carte chiffre-clé posée dans des
# placeholders ou une zone de texte non stylée — le faux positif, coûteux (la
# slide sort du plan de contenu), que cette règle existe pour empêcher. Taille
# non résolue avant le master → None → refus de conclure.
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"



def _sz_pt(el) -> float | None:
    sz = el.get("sz") if el is not None else None
    if not sz:
        return None
    try:
        return int(sz) / 100
    except ValueError:  # deck fourni par l'utilisateur : illisible = inconnu
        return None


def _niveau(p_pr) -> int | None:
    """Niveau de liste d'un paragraphe (0 par défaut) ; un `lvl` illisible rend
    None : le niveau inconnu interdit de lire un lstStyle, le run reste alors
    non résolu hors de ses tailles propres."""
    if p_pr is None:
        return 0
    try:
        return int(p_pr.get("lvl", "0"))
    except ValueError:
        return None


def _sz_niveau(lst_style, niveau: int | None) -> float | None:
    if lst_style is None or niveau is None:
        return None
    return _sz_pt(lst_style.find(f"{_A}lvl{niveau + 1}pPr/{_A}defRPr"))


def _ph(el):
    """`p:ph` d'une forme (élément lxml), ou None si ce n'est pas un placeholder."""
    return el.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")


def _lst_style(el):
    return el.find(f"{_P}txBody/{_A}lstStyle")


def _ph_correspondant(arbre, type_ph: str, idx: str | None, par_idx: bool):
    """Placeholder homologue dans le layout `arbre` : même idx d'abord
    (layout), sinon même type."""
    candidats = [sp for sp in arbre.iter(f"{_P}sp") if _ph(sp) is not None]
    if par_idx and idx is not None:
        for sp in candidats:
            if _ph(sp).get("idx", "0") == idx:
                return sp
    for sp in candidats:
        if _ph(sp).get("type", "obj") == type_ph:
            return sp
    return None


def _chaine_lst_styles(slide, shape) -> list:
    """lstStyle à consulter après le paragraphe, dans l'ordre d'héritage."""
    el = shape._element
    chaine = [_lst_style(el)]
    ph = _ph(el)
    if ph is None:
        return chaine
    type_ph = ph.get("type", "obj")
    idx = ph.get("idx", "0")
    layout = slide.slide_layout
    if layout is not None:
        sp = _ph_correspondant(layout._element, type_ph, idx, par_idx=True)
        if sp is not None:
            chaine.append(_lst_style(sp))
    return chaine


def _taille_effective_pt(run, p_pr, niveau: int | None, chaine: list) -> float | None:
    t = _sz_pt(run._r.find(_A + "rPr"))
    if t is None and p_pr is not None:
        t = _sz_pt(p_pr.find(_A + "defRPr"))
    for lst in chaine:
        if t is not None:
            break
        t = _sz_niveau(lst, niveau)
    return t


# Seuil de discrimination : le RAPPORT taille de l'intitulé / taille du nombre,
# et non un seuil absolu en points (un gabarit client compose à sa propre
# échelle). Les deux valeurs réelles encadrent le seuil, et elles sont DÉRIVÉES
# de la table `TYPE` de `pptx_deck` — « une seule source de verite », que ce
# module dupliquait avec un littéral 14 :
# - intercalaire dessiné par `_slide_chapitre` : title / kpi = 20/44 ≈ 0.45 ;
# - carte chiffre-clé : légende en small contre un nombre en kpi = 10.5/44 ≈ 0.24.
# Le seuil est posé à mi-chemin des deux (≈ 0.35) : il laisse à chacune près de
# la moitié de sa marge, plutôt que de coller à l'une des deux mesures.
_RATIO_INTERCALAIRE = _TYPE["title"] / _TYPE["kpi"]
_RATIO_CARTE_CHIFFRE = _TYPE["small"] / _TYPE["kpi"]
_RATIO_MIN_INTERCALAIRE = (_RATIO_INTERCALAIRE + _RATIO_CARTE_CHIFFRE) / 2


def _echelle_d_intercalaire(slide, numero: str, libelle: str) -> bool:
    """« NN » + son intitulé sont-ils composés comme l'intercalaire que
    `_slide_chapitre` dessine (`slides_cadre.py`), et non comme une carte
    chiffre-clé ? Mesuré sur le RAPPORT des deux tailles (cf.
    `_RATIO_MIN_INTERCALAIRE`) — aucune heuristique de capitalisation, de
    longueur ni de ponctuation : un intercalaire client légitime porte des
    intitulés en minuscules, longs, ou finissant par « : », et ces règles-là
    coûtaient des intercalaires perdus à l'export.

    Une taille HÉRITÉE du LAYOUT (le cas normal d'un gabarit client) est
    résolue par `_taille_max_pt` ; la chaîne s'arrête avant le master, générique
    au gabarit (cf. le commentaire de `_sz_pt`). Si l'une des
    deux reste INCONNUE à tous les niveaux, la fonction refuse de conclure et
    rend False : un CHAPITRE à tort coûte plus cher (la slide sort du plan de
    contenu, donc de l'export) qu'un CHAPITRE manquant."""
    t_num = _taille_max_pt(slide, numero)
    t_lib = _taille_max_pt(slide, libelle)
    if t_num is None or t_lib is None or t_num <= 0:
        return False
    return t_lib / t_num >= _RATIO_MIN_INTERCALAIRE


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
