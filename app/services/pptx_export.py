"""Génération de l'export PowerPoint (évol) — restitue à l'identique de
l'aperçu web (`synthese/apercu.html`) : slide de titre, sommaire, une slide
par catégorie de synthèse globale, une vue d'ensemble des axes, une matrice
effort/valeur (graphique natif PowerPoint) puis une slide par recommandation
(gabarit fixe calqué sur un rapport de restitution réel).

Si `mission.pptx_template_path` est renseigné, la présentation démarre à
partir de ce template client (hérite thème/masters/logo) ; sinon une
présentation vierge en 16:9, stylée via `pptx_deck` (skill pptx-deck, copié
tel quel dans ce module pour ne pas dépendre du chemin d'installation du
skill — cf. son propre en-tête).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import XyChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from ..models import Mission
from . import pptx_deck as D

# --- Cadres photo (têtes de chapitre, P3) : skill pptx-framed-image (greffé,
# présent dans .claude/skills/). Import gardé — si le skill/Pillow manque, les
# intercalaires retombent proprement sur leur version texte-seul (cf. _slide_chapitre).
_FRAMED_OK = False
try:  # pragma: no cover - dépend de la présence du skill + Pillow
    import sys as _sys
    _FRAMED_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "pptx-framed-image" / "scripts"
    if str(_FRAMED_SCRIPTS) not in _sys.path:
        _sys.path.insert(0, str(_FRAMED_SCRIPTS))
    from framed_image import place_image_in_frame as _place_image_in_frame  # type: ignore
    from framed_image import cover_crop_to_aspect as _cover_crop_to_aspect  # type: ignore
    import nature_images as _nature_images  # type: ignore
    import stock_images as _stock_images  # type: ignore  # fetch Openverse (vraies photos CC0)
    _IMG_CACHE = Path(__file__).resolve().parents[2] / "data" / "pptx_chapitre_images"
    _FRAMED_OK = True
except Exception:  # skill absent, Pillow non installé, etc. -> repli texte-seul
    _FRAMED_OK = False

MARGIN = 0.6

# --------------------------------------------------------------------------- #
# Repères "forme" pour l'éditeur web par onglets (aperçu.html) — mêmes
# contraintes géométriques (largeur, hauteur max, échelle typo) que les
# fonctions de slide ci-dessous, dupliquées ici en constantes plutôt que
# recalculées dynamiquement : l'éditeur enregistre un champ à la fois, hors
# contexte d'une Presentation réelle (pas de mission/axes/inclusions connus à
# cet instant). Restent donc des repères indicatifs, pas une garantie — le
# garde-fou qui compte vraiment reste `D.verifier_geometrie()` à l'export.
# plan_actions/resultats_attendus utilisent en vrai l'espace *restant* après
# les blocs précédents (variable) ; ici on prend une estimation généreuse
# mais fixe, cohérente avec une slide "normale".
_W_IN, _H_IN = 10.0, 5.625  # dims du template OCTO de marque (16:9) — FIELD_SHAPE (hints web) aligné dessus
_LEFT_W = _W_IN * 0.34
_RIGHT_W = _W_IN - (MARGIN + _LEFT_W + 0.4) - MARGIN
# Slide de synthèse enrichie (claim + visuel + encart) : largeur de la carte de puces
# une fois la bande photo réservée à droite (2.7in), sinon pleine largeur (repli).
_SYNTH_VIS_W = 2.7
_SYNTH_AREA_W = (
    ((_W_IN - MARGIN - _SYNTH_VIS_W) - 0.3 - (MARGIN + 0.3))
    if _FRAMED_OK else (_W_IN - 2 * (MARGIN + 0.3))
)

# Template OCTO de marque, versionné (masters/layouts/thème + police Outfit) : défaut de
# build_presentation. Un template client (mission.pptx_template_path) reste prioritaire ;
# le chrome (logo/pied de page/n° de slide) survit via _pick_layout (« titre seul »).
OCTO_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "assets" / "template-octo.pptx"

FIELD_SHAPE = {
    "objectif": dict(width_in=_LEFT_W, max_h_in=1.1),
    "acteurs": dict(width_in=_LEFT_W, max_h_in=0.5),
    "resultats_attendus": dict(width_in=_LEFT_W, max_h_in=1.5),
    "proposition_valeur": dict(width_in=_RIGHT_W, max_h_in=1.6),
    "plan_actions": dict(width_in=_RIGHT_W, max_h_in=2.8),
    "reco_title": dict(width_in=_W_IN - 2 * MARGIN, size_pt=D.TYPE["title"], max_lignes=2),
    "axis_title": dict(width_in=_W_IN - 2 * MARGIN - 2.0, max_h_in=1.1, size_max=D.TYPE["h3"]),
    # Slide enrichie (carte de puces à gauche, visuel à droite, 1re puce en encart) :
    # largeur = carte réduite du visuel ; hauteur = zone au-dessus de l'encart « à retenir ».
    "synthese_categorie": dict(width_in=_SYNTH_AREA_W - 0.48, max_h_in=1.9, size_max=D.TYPE["body"]),
    # Un quadrant SWOT = ~demi-largeur de la zone de contenu ; la hauteur de la
    # zone de PUCES (pas de la carte) = row_h - titre - paddings ≈ 1.9 in sur un
    # deck vierge (cf. _slide_swot) — pas la demi-hauteur brute (~2.2), qui
    # surestimait le budget du repère de ~20 % et rendait le fit-hint trompeur.
    "swot_quadrant": dict(width_in=(_W_IN - 2 * (MARGIN + 0.3) - 0.25) / 2 - 0.36, max_h_in=1.9, size_max=D.TYPE["small"]),
    # Executive summary (piste F) : panneau pleine largeur (constat + points) et
    # bande cyan « key message » — mêmes contraintes que la slide (cf.
    # _slide_executive_summary), pour un fit-hint fidèle dans l'aperçu.
    # headline / key_message : rendus à taille FIXE (h3) et tronqués à 2 lignes par
    # _slide_executive_summary -> hint en mode size_pt/max_lignes (annonce la
    # troncature, ne PROMET pas de réduction de police que la slide ne fait pas).
    # Constat revue adversariale 2026-07-21 : l'ancien mode max_h_in promettait un
    # shrink inexistant (même classe de dérive que le fit-hint SWOT déjà corrigé).
    "es_headline": dict(width_in=_W_IN - 2 * (MARGIN + 0.3) - 0.48, size_pt=D.TYPE["h3"], max_lignes=2),
    "es_points": dict(width_in=_W_IN - 2 * (MARGIN + 0.3) - 0.48, max_h_in=2.5, size_max=D.TYPE["body"]),
    "es_key_message": dict(width_in=_W_IN - 2 * (MARGIN + 0.3) - 0.48, size_pt=D.TYPE["h3"], max_lignes=2),
    # Difficulté (planche §D.1) : libellé d'une carte, taille fixe body, tronqué à
    # 3 lignes par _slide_difficultes -> hint size_pt/max_lignes (honnête). Largeur
    # réduite du chip de rang à gauche (2*pad + rang_w 0.46 + gap 0.16 = 0.98).
    "difficulty_label": dict(width_in=_W_IN - 2 * (MARGIN + 0.3) - 0.98, size_pt=D.TYPE["body"], max_lignes=3),
}


def field_fit_hint(field_key: str, text: str) -> str:
    """Message court indiquant comment `text` sera rendu à l'export pour le
    champ `field_key` (police retenue, nombre de lignes, troncature
    éventuelle) — s'appuie sur les mêmes fonctions d'ajustement
    (`D.ajuster_police` / `D.tronquer_a_lignes` / `D.estimer_lignes`) que le
    générateur, appliquées aux contraintes de forme de `FIELD_SHAPE` (voir
    note du module). Chaîne vide si le champ est inconnu ou vide — pas de
    repère à afficher plutôt qu'un repère trompeur."""
    spec = FIELD_SHAPE.get(field_key)
    text = (text or "").strip()
    if spec is None or not text:
        return ""

    width_in = spec["width_in"]

    if "max_lignes" in spec:
        size = spec["size_pt"]
        lignes = D.estimer_lignes(text, width_in, size)
        if lignes > spec["max_lignes"]:
            return f"⚠ trop long — sera tronqué à {spec['max_lignes']} lignes à l'export"
        return f"{lignes} ligne(s) à {size:.0f}pt à l'export"

    max_h_in = spec["max_h_in"]
    size_max = spec.get("size_max", D.TYPE["body"])
    size_min = D.TYPE["tiny"]

    def budget_ok(taille, lignes_max):
        return lignes_max * _per_line_height_in(taille) <= max_h_in

    size, lignes = D.ajuster_police([text], width_in, size_max, size_min, budget_ok=budget_ok)
    if lignes * _per_line_height_in(size) > max_h_in:
        return f"⚠ très long — sera réduit à {size:.0f}pt et tronqué à l'export"
    if size < size_max - 0.5:
        return f"{lignes} ligne(s) — police réduite à {size:.0f}pt pour tenir à l'export"
    return f"{lignes} ligne(s) à {size:.0f}pt à l'export"


def _dims(prs: Presentation) -> tuple[float, float]:
    return Emu(prs.slide_width).inches, Emu(prs.slide_height).inches


def _clear_slides(prs: Presentation) -> None:
    """Retire toutes les slides d'une présentation chargée depuis un template
    client — on ne veut hériter que masters/layouts/thème. Sans ça, un
    template qui est un vrai exemple de deck (pas un .potx vierge) ferait
    apparaître tout son contenu d'origine avant le nôtre. `python-pptx`
    n'expose pas de suppression de slide côté API publique ; on vide
    directement la liste XML des slides — mais il faut aussi lâcher la
    relation (r:id) de chaque slide sur la part présentation, sans quoi le
    fichier réserialisé contient des relations pointant vers des parts
    devenues orphelines : invisible pour python-pptx (parseur tolérant),
    mais PowerPoint refuse ensuite d'ouvrir le fichier (constaté via
    l'automation COM — l'export semblait « valide » côté tests avant ça)."""
    sld_id_lst = prs.slides._sldIdLst
    for sld_id in list(sld_id_lst):
        prs.part.drop_rel(sld_id.get(qn("r:id")))
        sld_id_lst.remove(sld_id)


def _has_title_placeholder(layout) -> bool:
    try:
        return any(
            ph.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE)
            for ph in layout.placeholders
        )
    except Exception:
        return False


def _pick_layout(prs: Presentation, preferred: int = 6):
    """Choisit un layout pour du contenu personnalisé, en respectant au mieux
    le template injecté (point 6) : un layout nommé "Title Only"/"Section" (le
    logo/footer du master survit, pas de placeholder de corps qui entrerait en
    collision avec notre mise en page), sinon le layout avec titre le moins
    chargé en autres placeholders, sinon le comportement historique (repli
    toujours valide même sur un template client mal structuré)."""
    layouts = list(prs.slide_layouts)
    # « titre seul » = le layout de contenu OCTO (idx0 titre, garde logo/pied/n° de slide).
    for kw in ("titre seul", "title only", "section"):
        for layout in layouts:
            if kw in (layout.name or "").lower() and _has_title_placeholder(layout):
                return layout
    with_title = [l for l in layouts if _has_title_placeholder(l)]
    if with_title:
        return min(with_title, key=lambda l: len(l.placeholders))
    return layouts[preferred] if preferred < len(layouts) else layouts[-1]


def _new_slide(prs: Presentation, title: str):
    """Crée une slide de contenu et pose son titre. Renvoie
    `(slide, w_in, h_in, content_top)` — `content_top` est calculé à partir
    de la position/hauteur réelle du placeholder de titre (natif du
    template) et du nombre de lignes qu'occupera effectivement `title` une
    fois replié, plutôt qu'une constante suppposant un titre sur une seule
    ligne : un titre de longueur normale (~50 caractères) suffit à passer
    sur 2 lignes et, avec une position de contenu figée, à chevaucher la
    zone en dessous — ce qu'une constante ne peut pas anticiper."""
    slide = prs.slides.add_slide(_pick_layout(prs))
    w_in, h_in = _dims(prs)
    title_shape = slide.shapes.title
    # Le placeholder de titre natif hérite position/police du template —
    # préféré à une zone de texte dessinée à la main dès qu'il existe sur le
    # layout choisi. On fige sa taille de police sur D.TYPE["title"] (au lieu
    # de laisser le style hérité, potentiellement bien plus grand) : ça reste
    # cohérent avec l'unique échelle typographique du reste du deck, et ça
    # rend le nombre de lignes prévisible (donc calculable) plutôt que soumis
    # à un style de thème inconnu.
    if title_shape is not None:
        if getattr(prs, "_i2d_synthetic", False):
            # Présentation vierge (pas de template client) : le placeholder
            # de titre hérité du modèle par défaut de python-pptx est
            # dimensionné pour un slide 10x7.5in (4:3) — trop étroit une fois
            # la slide passée en 16:9. On le repositionne explicitement sur
            # CETTE slide (jamais sur le layout/master : leurs placeholders
            # sont résolus par héritage et se sont révélés instables à muter
            # directement avec python-pptx — cf. essai précédent).
            title_shape.left = Inches(MARGIN)
            title_shape.top = Inches(0.3)
            title_shape.width = Inches(w_in - 2 * MARGIN)
            title_shape.height = Inches(1.1)
        title_w_in = Emu(title_shape.width).inches if title_shape.width is not None else (w_in - 2 * MARGIN)
        title_top_in = Emu(title_shape.top).inches if title_shape.top is not None else 0.3
        title_box_h_in = Emu(title_shape.height).inches if title_shape.height is not None else 0.7
        size = D.TYPE["title"]
        max_lignes = 2
        if D.estimer_lignes(title, title_w_in, size) > max_lignes:
            title = D.tronquer_a_lignes(title, title_w_in, size, max_lignes)
        title_shape.text = title
        tf = title_shape.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.NONE
        for p in tf.paragraphs:
            for run in p.runs:
                run.font.size = Pt(size)
                run.font.bold = True
        lignes = D.estimer_lignes(title, title_w_in, size)
        needed_h = lignes * _per_line_height_in(size) + 0.15
        # Barre d'accent cyan verticale avant le titre — signature OCTO repérée sur
        # les decks de restitution réels (VSCode4). Alignée sur les lignes du titre.
        try:
            _bar_left = Emu(title_shape.left).inches - 0.16
            _bar_h = max(0.30, lignes * _per_line_height_in(size) - 0.06)
            D.add_rect(slide, max(0.12, _bar_left), title_top_in + 0.05, 0.07, _bar_h,
                       fill=(D.theme_colors(prs).get("accent3") or "#00D2DD"))
        except Exception:
            pass
        content_top = title_top_in + max(title_box_h_in, needed_h) + 0.25
    else:
        D.add_text(
            slide, MARGIN, 0.35, w_in - 2 * MARGIN, 0.7,
            [(title, {"size": D.TYPE["title"], "bold": True, "color": D.INK})],
        )
        content_top = 1.4
    return slide, w_in, h_in, content_top


def _bullet_lines(text: str) -> list[str]:
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-•").strip()
        if line:
            lines.append(line)
    return lines


def _per_line_height_in(size_pt: float) -> float:
    # Calibrage empirique (skill pptx-deck) : ~0.17in/ligne à 10.5pt, un peu
    # plus large ici pour couvrir l'espacement inter-puces (space_after).
    return size_pt * 0.017 + 4 / 72


def _add_bulleted_text(
    slide, l, t, w, h, text: str, size: float | None = None,
    anchor=MSO_ANCHOR.TOP, size_max: float = D.TYPE["body"], size_min: float = D.TYPE["tiny"],
    paginate: bool = False,
) -> list[str]:
    """Pose une liste à puces dans la zone donnée. Par défaut (`paginate=False`,
    comportement historique) la police est réduite jusqu'à `size_min` pour
    tenter de tout faire tenir, sans garantie — un texte très long peut
    déborder silencieusement de sa zone (indétectable par
    `D.verifier_geometrie`, qui ne voit que les bords des formes).

    `paginate=True` change la garantie : si même `size_min` ne suffit pas, les
    puces qui ne tiennent pas sont retenues (pas rendues) et renvoyées à
    l'appelant plutôt que de déborder — à charge pour lui de les poser sur
    une slide de continuation (voir `_emit_bullet_overflow`). Renvoie la
    liste des puces non rendues (vide si tout tient)."""
    lines = _bullet_lines(text) or ["—"]

    def budget_ok(taille, _lignes_max):
        total = sum(D.estimer_lignes(line, w, taille) for line in lines)
        return total * _per_line_height_in(taille) <= h

    if size is None:
        size, _ = D.ajuster_police(lines, w, size_max, size_min, budget_ok=budget_ok)

    overflow: list[str] = []
    if paginate and not budget_ok(size, None):
        rendered_pages = D.paginer_items(
            lines, lambda line: D.estimer_lignes(line, w, size) * _per_line_height_in(size),
            capacite_in=h,
        )
        lines = rendered_pages[0]
        overflow = [line for page in rendered_pages[1:] for line in page]

    paragraphs = [(f"•  {line}", {"size": size, "color": D.INK, "space_after": 4}) for line in lines]

    if anchor == MSO_ANCHOR.MIDDLE:
        total_lines = sum(D.estimer_lignes(line, w, size) for line in lines)
        content_h = min(h, total_lines * _per_line_height_in(size))
        box_t = t + max(0.0, (h - content_h) / 2)
        D.add_text(slide, l, box_t, w, content_h, paragraphs)
    else:
        D.add_text(slide, l, t, w, h, paragraphs)
    return overflow


def _emit_bullet_overflow(prs: Presentation, base_title: str, field_label: str, overflow_lines: list[str]) -> None:
    """Pose les puces qui n'ont pas tenu sur la slide d'origine (voir
    `_add_bulleted_text(paginate=True)`) sur une ou plusieurs slides de
    continuation pleine largeur — chacune dispose de bien plus d'espace que
    la colonne étroite d'origine, donc peut se voir attribuer sa propre
    police (recalculée, pas figée à `size_min`)."""
    remaining = "\n".join(overflow_lines)
    page_no = 1
    while remaining:
        suffix = f" {page_no}" if page_no > 1 else ""
        slide, w_in, h_in, top = _new_slide(prs, f"{base_title} (suite — {field_label}){suffix}")
        w = w_in - 2 * (MARGIN + 0.3)
        h = h_in - top - 0.5
        overflow = _add_bulleted_text(slide, MARGIN + 0.3, top, w, h, remaining, paginate=True)
        remaining = "\n".join(overflow)
        page_no += 1


# --------------------------------------------------------------------------- #
# Slides
# --------------------------------------------------------------------------- #
def _add_measured_field(
    slide, l, t, w, label: str, text: str, max_h: float,
    size_max: float = D.TYPE["body"], size_min: float = D.TYPE["tiny"],
    bold: bool = False, italic: bool = False,
) -> float:
    """Pose un libellé (petit, gras, discret) puis son contenu juste en
    dessous, en adaptant la taille de police du contenu à `max_h`
    (D.ajuster_police) et en tronquant en tout dernier recours — jamais de
    débordement dans le bloc suivant même avec une réponse d'entretien très
    longue. Renvoie la hauteur réellement occupée (libellé + contenu), à
    utiliser pour empiler le bloc suivant à la bonne position."""
    label_h = 0.3
    D.add_text(slide, l, t, w, label_h, [(label, {"size": D.TYPE["small"], "bold": True, "color": D.MUTED})])
    body = ((text or "").strip()) or "—"
    body_max_h = max(0.2, max_h - label_h)

    def budget_ok(taille, lignes_max):
        return lignes_max * _per_line_height_in(taille) <= body_max_h

    size, lignes_max = D.ajuster_police([body], w, size_max, size_min, budget_ok=budget_ok)
    if lignes_max * _per_line_height_in(size) > body_max_h:
        max_lignes = max(1, int(body_max_h / _per_line_height_in(size)))
        body = D.tronquer_a_lignes(body, w, size, max_lignes)
        lignes_max = max_lignes
    body_h = lignes_max * _per_line_height_in(size)
    D.add_text(slide, l, t + label_h, w, body_h, [(body, {"size": size, "bold": bold, "italic": italic, "color": D.INK})])
    return label_h + body_h


def _layout_by_name(prs: Presentation, *keywords: str):
    """Premier layout dont le nom contient l'un des mots-clés (insensible à la
    casse) — repérage robuste des layouts de marque OCTO (« 40 - Couverture »,
    « 50 - Chapitre ») par nom plutôt que par indice. None si aucun ne matche."""
    for layout in prs.slide_layouts:
        name = (layout.name or "").lower()
        if any(kw in name for kw in keywords):
            return layout
    return None


# Refonte P2 — structure narrative : 4 chapitres. Chaque section du deck appartient
# à un chapitre ; un intercalaire (layout « 50 - Chapitre ») ouvre chaque chapitre
# qui a du contenu, et le sommaire quali regroupe les sections sous ces intitulés
# narratifs (couleur = repère de navigation, reprise sur l'intercalaire).
_CH_RETENIR, _CH_DIAGNOSTIC, _CH_PAROLE, _CH_TRAJECTOIRE = 0, 1, 2, 3
# (intitulé, couleur, scène image de l'intercalaire — cf. _slide_chapitre P3).
_CHAPITRES = [
    ("Ce qu'il faut retenir", "#00D2DD", "sunset"),     # cyan
    ("Le diagnostic", "#0E2356", "mountains"),          # navy
    ("La parole des équipes", "#138086", "forest"),     # teal
    ("La trajectoire proposée", "#6a3d9a", "ocean"),    # violet
]
# Scène -> requête photo Openverse (vraies photos CC0, comme les decks OCTO réels /
# VSCode3-4). Repli sur la génération procédurale `nature_images` (nom = la scène).
_SCENE_REQUETE = {
    "sunset": "sunset sky",
    "mountains": "mountains landscape",
    "forest": "green forest sunlight",
    "ocean": "turquoise water",
}


def _slide_cover(prs: Presentation, mission: Mission) -> None:
    """Couverture : sur un template OCTO, remplit les placeholders du layout de
    marque « 40 - Couverture » (titre/sous-titre/date) — la mise en forme (police
    Outfit, tailles, éventuelle photo de couverture) vient du template. Repli sur un
    titre dessiné centré si le layout n'existe pas (deck synthétique sans marque)."""
    subtitle = "Synthèse transverse & recommandations"
    date_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    cover = _layout_by_name(prs, "couverture", "cover")
    if cover is not None:
        slide = prs.slides.add_slide(cover)
        phs = {ph.placeholder_format.idx: ph for ph in slide.placeholders}
        if 0 in phs:
            phs[0].text_frame.text = mission.name
        if 1 in phs:
            phs[1].text_frame.text = subtitle
        # idx2 = « OCTO Technology » (natif, laissé tel quel) ; idx3 = date.
        if 3 in phs:
            phs[3].text_frame.text = date_str
        return
    _slide_title(prs, mission, subtitle, date_str)


def _slide_title(prs: Presentation, mission: Mission, subtitle: str, date_str: str) -> None:
    slide = prs.slides.add_slide(_pick_layout(prs))
    w_in, h_in = _dims(prs)
    title_w = w_in - 2.0
    size = 32
    # Le sous-titre se place sous la hauteur RÉELLE du titre (un nom long passe sur
    # 2 lignes sur une slide étroite type OCTO 10in) plutôt qu'à un offset fixe qui
    # supposait un titre sur 1 ligne — sinon la 2e ligne chevauche le sous-titre.
    title_lines = max(1, min(3, D.estimer_lignes(mission.name, title_w - 0.2, size)))
    title_h = title_lines * _per_line_height_in(size)
    cy = h_in * 0.38
    D.add_text(
        slide, 1.0, cy, title_w, title_h,
        [(mission.name, {"size": size, "bold": True, "color": D.INK, "align": PP_ALIGN.CENTER})],
    )
    y = cy + title_h + 0.15
    D.add_text(
        slide, 1.0, y, title_w, 0.5,
        [(subtitle, {"size": D.TYPE["h2"], "color": D.MUTED, "align": PP_ALIGN.CENTER})],
    )
    D.add_text(
        slide, 1.0, y + 0.55, title_w, 0.4,
        [(date_str, {"size": D.TYPE["small"], "color": D.MUTED, "align": PP_ALIGN.CENTER})],
    )


def _slide_sommaire(prs: Presentation, ch_sections: list[list[str]]) -> None:
    """Sommaire quali (P2) : les chapitres AYANT du contenu, chacun avec sa pastille
    couleur + intitulé narratif + les sections qu'il regroupe (repère de navigation,
    couleur reprise sur l'intercalaire) — au lieu d'une liste plate de sections."""
    slide, w_in, h_in, top = _new_slide(prs, "Sommaire")
    active = [ci for ci, subs in enumerate(ch_sections) if subs]
    # Grille 2×2 de badges GOUTTE (teardrop) à contour — signature du sommaire des
    # decks OCTO réels (VSCode4) : numéro dans la goutte, intitulé narratif + sections
    # en regard. Remplissage colonne par colonne (01,02 à gauche ; 03,04 à droite).
    area_t = top + 0.2
    row_h = (h_in - 0.5 - area_t) / 2
    col_w = (w_in - 2 * MARGIN) / 2
    d = 0.92  # diamètre du badge goutte
    for idx, ci in enumerate(active):
        subs = ch_sections[ci]
        label, color = _CHAPITRES[ci][0], _CHAPITRES[ci][1]
        col, r = idx // 2, idx % 2
        cell_x = MARGIN + col * col_w
        cell_y = area_t + r * row_h
        bx = cell_x + 0.15
        by = cell_y + (row_h - d) / 2
        D.add_teardrop(slide, bx, by, d, f"{idx + 1:02d}", color, size=D.TYPE["h2"])
        tx = bx + d + 0.3
        tw = col_w - (bx - cell_x) - d - 0.3 - 0.2
        D.add_text(
            slide, tx, cell_y, max(1.0, tw), row_h,
            [
                (label, {"size": D.TYPE["h3"], "bold": True, "color": D.INK, "space_after": 4}),
                (" · ".join(subs), {"size": D.TYPE["small"], "color": D.MUTED}),
            ],
            anchor=MSO_ANCHOR.MIDDLE,
        )


def _find_teardrop_frame(shapes):
    """`(left, top, width, height, geom)` du cadre photo teardrop d'un layout
    (le layout « 50 - Chapitre » place son cadre en top-level, pas dans un groupe),
    ou None. Même principe que pptx-framed-image.frame_geometry, cas non groupé."""
    for sh in shapes:
        spPr = getattr(sh._element, "spPr", None)
        if spPr is None:
            continue
        g = spPr.find(qn("a:prstGeom"))
        if g is not None and g.get("prst") == "teardrop":
            return sh.left, sh.top, sh.width, sh.height, g
    return None


def _remplir_cadre_chapitre(slide, cadre, scene: str, seed: int = 0) -> None:
    """Remplit le cadre teardrop d'un intercalaire avec une image à l'aspect exact
    du cadre (génération procédurale offline `nature_images` — reproductible, sans
    réseau ; l'arbitrage prévoit un fetch Openverse en amont, ajoutable plus tard).
    Silencieux sur échec : l'intercalaire reste lisible sans image."""
    if not _FRAMED_OK or cadre is None:
        return
    try:
        left, top, width, height, geom = cadre
        aspect = Emu(width).inches / Emu(height).inches
        px_w = 900
        px_h = max(1, int(round(px_w / aspect)))
        _IMG_CACHE.mkdir(parents=True, exist_ok=True)
        path = _IMG_CACHE / f"{scene}_{seed}_{px_w}x{px_h}.png"
        if not path.exists():
            # Vraie photo libre de droits (Openverse CC0) — lit mieux qu'un aplat
            # procédural, aligne la charte sur les decks OCTO réels (VSCode4) ; repli
            # procédural offline si le réseau/l'API échoue (arbitrage fetch + repli).
            # PPTX_NO_PHOTO_FETCH=1 force le procédural (tests offline/déterministes, CI).
            no_fetch = os.environ.get("PPTX_NO_PHOTO_FETCH") == "1"
            aspect_ratio = "wide" if aspect > 1.15 else "tall" if aspect < 0.85 else "square"
            try:
                if no_fetch:
                    raise RuntimeError("fetch désactivé")
                brut = _IMG_CACHE / f"_brut_{scene}_{seed}.jpg"
                _stock_images.fetch_to(str(brut), _SCENE_REQUETE.get(scene, scene),
                                       seed=seed, aspect_ratio=aspect_ratio)
                _cover_crop_to_aspect(str(brut), str(path), aspect)
            except Exception:
                _nature_images.generate_to(str(path), scene, px_w, px_h, seed=seed)
        _place_image_in_frame(slide, str(path), left, top, width, height, geom=geom)
    except Exception:
        pass  # repli : intercalaire sans image, jamais un export cassé


def _slide_chapitre(prs: Presentation, numero: int, titre: str, color: str,
                    scene: str | None = None) -> None:
    """Intercalaire de chapitre. P3 : vrai layout de marque « 50 - Chapitre » —
    titre (idx0) coloré + numéro (idx1, 17pt marges à zéro comme le REX source, un
    28pt débordait le petit encart) + cadre photo teardrop rempli via
    pptx-framed-image. Repli P2 (numéro + filet + titre dessinés sur un layout de
    contenu) si le layout de marque ou le skill image manquent."""
    layout = _layout_by_name(prs, "chapitre") if _FRAMED_OK else None
    if layout is not None:
        slide = prs.slides.add_slide(layout)
        phs = {ph.placeholder_format.idx: ph for ph in slide.placeholders}
        if 0 in phs:
            title_ph = phs[0]
            title_ph.text_frame.text = titre
            for p in title_ph.text_frame.paragraphs:
                for r in p.runs:
                    r.font.color.rgb = D.rgb(color)
            # Numéro de chapitre GRAND et DESSINÉ (pas le placeholder natif idx1, trop
            # étroit — il replie « 01 » en « 0 »/« 1 » au rendu) : posé au-dessus du
            # titre, même couleur de chapitre, avec un filet. Le rendu d'un « 01 »
            # dessiné est fiable (cf. les badges du sommaire). Demande explicite et
            # répétée de l'utilisateur — le numéro DOIT figurer sur l'intercalaire.
            try:
                tl = Emu(title_ph.left).inches
                tt = Emu(title_ph.top).inches
                tw = Emu(title_ph.width).inches
                num_h = 0.95
                num_top = max(0.25, tt - num_h + 0.05)
                D.add_text(
                    slide, tl, num_top, min(tw, 3.0), num_h,
                    [(f"{numero:02d}", {"size": D.TYPE["kpi"], "bold": True, "color": color})],
                )
                D.add_rect(slide, tl + 0.04, num_top + num_h + 0.02, 0.9, 0.05, fill=color)
            except Exception:
                pass  # placeholder de titre sans géométrie exploitable : numéro sur le repli
        # Placeholder numéro natif (idx1) laissé vide : on dessine le numéro nous-mêmes
        # (ci-dessus) car cet encart est trop étroit et « tofu » le « 01 ».
        if 1 in phs:
            phs[1].text_frame.text = ""
        _remplir_cadre_chapitre(slide, _find_teardrop_frame(slide.slide_layout.shapes),
                                scene or "mountains")
        return
    slide = prs.slides.add_slide(_pick_layout(prs))
    w_in, h_in = _dims(prs)
    cy = h_in * 0.30
    D.add_text(
        slide, MARGIN + 0.3, cy, 3.0, 1.3,
        [(f"{numero:02d}", {"size": D.TYPE["kpi"], "bold": True, "color": color})],
    )
    ty = cy + 1.35
    D.add_rect(slide, MARGIN + 0.36, ty, 1.2, 0.06, fill=color)
    D.add_text(
        slide, MARGIN + 0.3, ty + 0.18, w_in - 2 * MARGIN - 0.6, 1.0,
        [(titre, {"size": D.TYPE["title"], "bold": True, "color": D.INK})],
    )


# Enrichissement synthèse (ask design 2026-07-22) : pattern claim + visuel + encart
# des decks OCTO réels (VSCode4). Scène/requête photo par catégorie (repli procédural
# offline, comme les têtes de chapitre) — clé = libellé exact passé par build_presentation.
# Scènes NATURE (comme les têtes de chapitre) : rendu procédural fiable hors ligne
# ET vraie photo Openverse en prod — cohérent avec l'imagerie de marque du deck.
# (scène, requête photo, seed distinct pour varier des intercalaires).
_SYNTHESE_VISUEL = {
    "Contexte": ("mountains", "mountains landscape", 11),
    "Culture & ADN": ("forest", "green forest sunlight", 12),
    "Forces & succès": ("sunset", "sunset sky", 13),
    "Points d'amélioration": ("ocean", "turquoise water", 14),
    "Aspirations (baguette magique)": ("sunset", "sunrise horizon sky", 15),
}


def _slide_synthese_categorie(prs: Presentation, label: str, content: str) -> None:
    """Slide de catégorie de synthèse, ENRICHIE (claim + visuel + encart) : puces à
    gauche dans une carte, photo métier à droite, 1re puce promue en encart « à
    retenir » cyan en bas — au lieu d'un titre + puces sur fond vide. Repli propre
    (carte pleine largeur, pas d'encart) si l'infra image manque ou si la catégorie
    n'a qu'une puce. Même pattern que _slide_executive_summary."""
    slide, w_in, h_in, top = _new_slide(prs, f"Synthèse globale — {label}")
    accent = (D.theme_colors(prs).get("accent3") or "#00D2DD")  # cyan OCTO
    area_l = MARGIN + 0.3
    has_vis = _FRAMED_OK
    vis_w = _SYNTH_VIS_W
    vis_l = w_in - MARGIN - vis_w
    area_w = (vis_l - 0.3 - area_l) if has_vis else (w_in - 2 * (MARGIN + 0.3))
    pad = 0.24
    band_h, band_gap = 0.9, 0.3
    band_t = h_in - 0.5 - band_h

    lines = _bullet_lines(content) or ["—"]
    # 1re puce -> encart « à retenir » si au moins 2 puces (sinon tout dans la carte).
    retenir = lines[0] if len(lines) >= 2 else None
    rest = lines[1:] if retenir else lines

    zone_bottom = (band_t - band_gap) if retenir else (h_in - 0.5)
    avail = max(0.0, zone_bottom - top)

    body = D.TYPE["body"]
    rest_text = "\n".join(rest) or "—"
    # La carte occupe TOUTE la zone (même hauteur que le visuel à droite → colonnes
    # équilibrées, pas de vide sous une carte trop courte) ; puces centrées verticalement.
    card_h = avail
    D.add_card(slide, area_l, top, area_w, card_h, accent)
    _add_bulleted_text(
        slide, area_l + pad, top + pad, area_w - 2 * pad, max(0.0, card_h - 2 * pad),
        rest_text, anchor=MSO_ANCHOR.MIDDLE, size_max=body, size_min=D.TYPE["small"],
        paginate=True,
    )

    if has_vis:
        scene, requete, seed = _SYNTHESE_VISUEL.get(label, ("mountains", "mountains landscape", 11))
        if not _image_dans_zone(slide, vis_l, top, vis_w, avail, scene, requete, seed=seed):
            D.add_rect(slide, vis_l, top, vis_w, avail, fill=accent, rounded=True, radius=0.06)

    if retenir:
        # Encart « à retenir » gris (même composant add_encart que l'executive summary
        # — cohérence de composant §5, sobriété §3/§7, motif VSCode4).
        msg = D.tronquer_a_lignes(retenir, area_w - 0.6, D.TYPE["h3"], 2)
        D.add_encart(slide, area_l, band_t, area_w, band_h, msg, accent=accent)


# SWOT : Forces/Faiblesses = interne (vert/rouge), Opportunités/Menaces =
# externe (bleu/ambre). Couleurs sémantiques prises dans D.PALETTE (design
# system : différenciation par liseré de carte, pas de dégradé/ombre).
_SWOT_QUADRANTS = [
    ("forces", "Forces", "#1e6b34"),
    ("faiblesses", "Faiblesses", "#b3261e"),
    ("opportunites", "Opportunités", "#2c5cc5"),
    ("menaces", "Menaces", "#b8860b"),
]

# Badge-icône par quadrant : flèches directionnelles (bloc Arrows, monochrome,
# rendu fiable — cf. l'usage de « → » sur les decks OCTO réels VSCode4). Sémantique
# de la grille : interne haut/bas (↑ force / ↓ faiblesse), externe haut/bas
# (↗ opportunité / ↘ menace). bold=False au badge (certains glyphes « tofu » en gras).
_SWOT_ICONS = {"forces": "↑", "faiblesses": "↓", "opportunites": "↗", "menaces": "↘"}


def _image_dans_zone(slide, left, top, width, height, scene: str, requete: str,
                     seed: int = 0) -> bool:
    """Pose une photo (Openverse CC0, repli procédural offline) cover-croppée à
    l'aspect de la zone, dans un rectangle — pattern « claim + visuel » (P3b, repéré
    sur les decks OCTO réels VSCode4). PPTX_NO_PHOTO_FETCH=1 force le procédural
    (tests). Renvoie True si posée, False sinon (silencieux — jamais un export cassé)."""
    if not _FRAMED_OK:
        return False
    try:
        aspect = width / height
        px_w = 900
        px_h = max(1, int(round(px_w / aspect)))
        _IMG_CACHE.mkdir(parents=True, exist_ok=True)
        path = _IMG_CACHE / f"zone_{scene}_{seed}_{px_w}x{px_h}.png"
        if not path.exists():
            no_fetch = os.environ.get("PPTX_NO_PHOTO_FETCH") == "1"
            ar = "tall" if aspect < 0.85 else "wide" if aspect > 1.15 else "square"
            try:
                if no_fetch:
                    raise RuntimeError("fetch désactivé")
                brut = _IMG_CACHE / f"_brutz_{scene}_{seed}.jpg"
                _stock_images.fetch_to(str(brut), requete, seed=seed, aspect_ratio=ar)
                _cover_crop_to_aspect(str(brut), str(path), aspect)
            except Exception:
                _nature_images.generate_to(str(path), scene, px_w, px_h, seed=seed)
        slide.shapes.add_picture(str(path), Inches(left), Inches(top),
                                 Inches(width), Inches(height))
        return True
    except Exception:
        return False


def _slide_executive_summary(prs: Presentation, es) -> None:
    """Slide d'ouverture « Executive Summary » (piste F restitution, 2026-07-21) :
    un panneau constat + points clés, et une bande cyan « key message » (le
    so-what) en bas — pattern relevé sur les vraies restitutions OCTO (Executive
    Summary + bande de message à retenir), cf.
    docs/reflexions/restitution-mission.md §F. Placée juste après le sommaire."""
    slide, w_in, h_in, top = _new_slide(prs, "Executive Summary")
    accent = (D.theme_colors(prs).get("accent3") or "#00D2DD")  # cyan OCTO
    area_l = MARGIN + 0.3
    # Pattern claim + visuel (P3b, repéré sur les decks OCTO réels VSCode4) : réserve
    # une bande photo à droite si l'infra image est dispo ; sinon la carte-claim reprend
    # toute la largeur (repli propre).
    has_vis = _FRAMED_OK
    vis_w = 2.7
    vis_l = w_in - MARGIN - vis_w
    area_w = (vis_l - 0.3 - area_l) if has_vis else (w_in - 2 * (MARGIN + 0.3))
    pad = 0.24
    band_h = 0.9
    band_gap = 0.3
    band_t = h_in - 0.5 - band_h
    # max(0.0, …) : sur un template client au titre bas, l'espace dispo pourrait
    # passer négatif — jamais de dimension négative à python-pptx.
    avail = max(0.0, band_t - band_gap - top)  # espace max entre le titre et la bande

    headline = (getattr(es, "headline", "") or "").strip()
    # hl_h : 2 lignes h3 max (le headline est tronqué à 2 lignes, taille fixe — un
    # headline long ou une liste Ollama aplatie déborderait sinon sur les puces,
    # constat Blind/Edge Case Hunter 2026-07-21).
    hl_h = 0.7 if headline else 0.0
    if headline:
        headline = D.tronquer_a_lignes(headline, area_w - 2 * pad, D.TYPE["h3"], 2)

    # Carte DIMENSIONNÉE AU CONTENU (comme _slide_difficultes/_slide_verbatims) plutôt
    # qu'étirée jusqu'à la bande — évite une grande carte à moitié vide quand l'exec
    # summary est court (finding pptx-verify 2026-07-22). Bornée à l'espace dispo
    # (paginate tronque au besoin) ; la bande « key message » reste ancrée en bas.
    body = D.TYPE["body"]
    pts_text = getattr(es, "points", "") or "—"
    pts_lines = _bullet_lines(pts_text) or ["—"]
    pts_h = sum(D.estimer_lignes(l, area_w - 2 * pad, body) for l in pts_lines) * _per_line_height_in(body)
    card_h = min(avail, pad + hl_h + pts_h + pad)
    hl_h = min(hl_h, card_h)  # garde le bloc constat dans la carte (template au titre bas)

    D.add_card(slide, area_l, top, area_w, card_h, accent)
    if headline:
        D.add_text(
            slide, area_l + pad, top + pad, area_w - 2 * pad, hl_h,
            [(headline, {"size": D.TYPE["h3"], "bold": True, "color": D.INK})],
        )
    # paginate=True : des points trop longs sont TRONQUÉS à la carte plutôt que
    # de déborder (verifier_geometrie ne voit pas le débordement intra-forme).
    _add_bulleted_text(
        slide, area_l + pad, top + pad + hl_h, area_w - 2 * pad,
        max(0.0, card_h - (pad + hl_h) - pad),
        pts_text,
        anchor=MSO_ANCHOR.TOP, size_max=body, size_min=D.TYPE["small"],
        paginate=True,
    )

    # Visuel à droite (claim à gauche) — photo métier cover-croppée à la zone ;
    # repli sur un bloc couleur accent net si la photo n'est pas disponible (fetch
    # KO + scène non procédurale) plutôt qu'une photo hors-sujet ou du vide.
    if has_vis:
        # Scène NATURE (repli procédural propre hors ligne, vraie photo en prod) —
        # cohérent avec l'imagerie de marque du deck ; « office » procédural rendait
        # un aplat criard (défaut relevé par la passe restitution-deck-design §7).
        if not _image_dans_zone(slide, vis_l, top, vis_w, avail, "sunset",
                                "city skyline sunrise", seed=7):
            D.add_rect(slide, vis_l, top, vis_w, avail, fill=accent, rounded=True, radius=0.06)

    key_message = (getattr(es, "key_message", "") or "").strip()
    if key_message:
        # Encart « à retenir » gris (composant unique add_encart) — sobre, liseré
        # accent, texte foncé : la couleur est un accent, pas une bande criarde.
        # Hauteur fixe -> message tronqué à 2 lignes (débordement invisible sinon).
        msg = D.tronquer_a_lignes(key_message, area_w - 0.6, D.TYPE["h3"], 2)
        D.add_encart(slide, area_l, band_t, area_w, band_h, msg, accent=accent)


def _slide_swot(prs: Presentation, swot) -> None:
    """Matrice SWOT 2×2 — une carte par quadrant (add_card, liseré coloré),
    titre coloré + puces. Grille : Forces (haut-g), Faiblesses (haut-d),
    Opportunités (bas-g), Menaces (bas-d)."""
    slide, w_in, h_in, top = _new_slide(prs, "Matrice SWOT")
    gap = 0.25
    pad = 0.18
    area_l = MARGIN + 0.3
    area_w = w_in - 2 * (MARGIN + 0.3)
    area_t = top
    area_h = h_in - top - 0.5
    col_w = (area_w - gap) / 2
    row_h = (area_h - gap) / 2
    cells = [(0, 0), (1, 0), (0, 1), (1, 1)]
    title_h = 0.4
    for (key, label, color), (col, row) in zip(_SWOT_QUADRANTS, cells):
        cl = area_l + col * (col_w + gap)
        ct = area_t + row * (row_h + gap)
        D.add_card(slide, cl, ct, col_w, row_h, color)
        # Badge-icône du quadrant devant le titre (icône par quadrant).
        badge_d = min(0.32, title_h)
        D.add_badge(slide, cl + pad, ct + pad * 0.7, badge_d, _SWOT_ICONS[key],
                    color, size=D.TYPE["small"], bold=False, radius=0.28)
        D.add_text(
            slide, cl + pad + badge_d + 0.12, ct + pad * 0.7,
            col_w - 2 * pad - badge_d - 0.12, title_h,
            [(label, {"size": D.TYPE["h3"], "bold": True, "color": color})],
            anchor=MSO_ANCHOR.MIDDLE,
        )
        # paginate=True : un quadrant trop long est TRONQUÉ à ce qui tient dans
        # la carte plutôt que de déborder silencieusement sur le quadrant voisin
        # (verifier_geometrie ne voit pas le débordement de texte intra-forme).
        # max(0.0, …) : sur un template client au titre bas, la hauteur de zone
        # pourrait passer négative (le /2 vertical du 2×2 l'amplifie) — jamais de
        # dimension négative passée à python-pptx.
        _add_bulleted_text(
            slide, cl + pad, ct + pad * 0.7 + title_h, col_w - 2 * pad,
            max(0.0, row_h - (pad * 0.7 + title_h) - pad),
            getattr(swot, key) or "—",
            anchor=MSO_ANCHOR.TOP, size_max=D.TYPE["small"], size_min=D.TYPE["tiny"],
            paginate=True,
        )


def _slide_difficultes(prs: Presentation, difficulties) -> None:
    """Planche « Difficultés identifiées » (piste F restitution) — une carte par
    difficulté (rang + constat), chacune pouvant porter un verbatim en encadré
    citation (l'« insert citation » prévu de longue date, cf.
    docs/reflexions/restitution-mission.md §D.1). Cartes empilées et DIMENSIONNÉES
    à leur contenu (comme _slide_verbatims), on s'arrête avant de déborder du
    cadre (garantit verifier_geometrie)."""
    slide, w_in, h_in, top = _new_slide(prs, "Difficultés identifiées")
    pad, gap = 0.18, 0.16
    area_l = MARGIN + 0.3
    area_w = w_in - 2 * (MARGIN + 0.3)
    area_bottom = h_in - 0.5
    accent = "#b8860b"  # ambre : signal « point d'attention »
    teal = "#138086"    # citation, cohérent avec la planche verbatims
    size = D.TYPE["body"]
    q_size = D.TYPE["small"]
    line_h = _per_line_height_in(size)
    q_line_h = _per_line_height_in(q_size)
    # Rang en chip numéroté (ambre) à gauche de la carte, au lieu du préfixe « N. »
    # dans le libellé — le texte du constat démarre après le chip (largeur réduite,
    # reflétée dans FIELD_SHAPE["difficulty_label"]).
    rang_w, rang_h = 0.46, 0.30
    lab_x = area_l + pad + rang_w + 0.16
    lab_w = area_w - 2 * pad - rang_w - 0.16
    for i, d in enumerate(difficulties, 1):
        label = (getattr(d, "label", "") or "").strip()
        if not label:
            continue
        lab_lines = min(3, max(1, D.estimer_lignes(label, lab_w, size)))
        v = getattr(d, "verbatim", None)
        quote = ""
        if v is not None and (getattr(v, "quote", "") or "").strip():
            who = (getattr(getattr(v, "interview", None), "interviewee_name", "") or "Anonyme").strip() or "Anonyme"
            quote = f"«  {v.quote.strip()}  » — {who}"
        q_lines = min(2, max(1, D.estimer_lignes(quote, lab_w, q_size))) if quote else 0
        head_block = max(rang_h, lab_lines * line_h)
        card_h = pad + head_block + (0.06 + q_lines * q_line_h if quote else 0.0) + pad
        if top + card_h > area_bottom and i > 1:  # au moins la 1re carte, sinon stop
            break
        if top + card_h > area_bottom:
            card_h = max(0.0, area_bottom - top)  # 1re carte trop haute : bornée au cadre
        D.add_card(slide, area_l, top, area_w, card_h, accent)
        D.add_chip(slide, area_l + pad, top + pad, rang_w, rang_h, str(i), accent,
                   size=D.TYPE["small"])
        D.add_text(
            slide, lab_x, top + pad, lab_w, head_block,
            [(D.tronquer_a_lignes(label, lab_w, size, lab_lines),
              {"size": size, "bold": True, "color": D.INK})],
            anchor=MSO_ANCHOR.MIDDLE,
        )
        if quote:
            D.add_text(
                slide, lab_x, top + pad + head_block + 0.06,
                lab_w, q_lines * q_line_h,
                [(D.tronquer_a_lignes(quote, lab_w, q_size, q_lines),
                  {"size": q_size, "italic": True, "color": teal})],
            )
        top += card_h + gap


def _slide_verbatims(prs: Presentation, verbatims) -> None:
    """Planche « Paroles d'acteurs » (Palier 2) — une carte-citation par
    verbatim retenu (attribution en libellé discret, citation en corps italique),
    empilées depuis le haut, chaque carte DIMENSIONNÉE À SON CONTENU (2 lignes de
    citation au plus) plutôt qu'étirée à `area_h / n` — sinon une citation d'une
    ligne laisse un grand vide dans sa carte (constat pptx-verify). Le surplus se
    reporte en blanc en bas de slide. On s'arrête avant de déborder du cadre
    (garantit le garde-fou géométrie) — l'onglet aperçu invite à 2-4 citations."""
    slide, w_in, h_in, top = _new_slide(prs, "Paroles d'acteurs")
    pad, gap = 0.18, 0.18
    label_h = 0.3
    area_l = MARGIN + 0.3
    area_w = w_in - 2 * (MARGIN + 0.3)
    area_bottom = h_in - 0.5
    size = D.TYPE["body"]
    line_h = _per_line_height_in(size)
    y = top
    for v in verbatims:
        quote = f"«  {(v.quote or '').strip()}  »"
        q_lines = min(3, max(1, D.estimer_lignes(quote, area_w - 2 * pad, size)))
        card_h = pad + label_h + q_lines * line_h + pad
        if y + card_h > area_bottom:  # ne jamais déborder du cadre
            break
        D.add_card(slide, area_l, y, area_w, card_h, "#138086")
        who = (getattr(v.interview, "interviewee_name", "") or "Anonyme").strip() or "Anonyme"
        _add_measured_field(
            slide, area_l + pad, y + pad, area_w - 2 * pad,
            label=who, text=quote, max_h=label_h + q_lines * line_h,
            size_max=size, size_min=D.TYPE["tiny"], italic=True,
        )
        y += card_h + gap


_AXES_ROW_H_MAX = 1.1
_AXES_ROW_GAP = 0.15
# En dessous de cette hauteur de ligne, le chiffre "#N" (D.TYPE["kpi"]=44pt)
# chevauche visuellement le titre de l'axe à côté — verifier_geometrie() ne
# peut pas le détecter (il ne vérifie que les bords des formes, pas le rendu
# du texte à l'intérieur) : mieux vaut paginer sur une slide suivante que
# de laisser les cartes devenir illisibles avec beaucoup d'axes.
_AXES_ROW_H_MIN = 0.75


def _axes_row_h(n: int, band_h: float) -> float:
    return min(_AXES_ROW_H_MAX, (band_h - _AXES_ROW_GAP * (n - 1)) / max(1, n))


def _slide_axes_overview(prs: Presentation, axes: list, palette: list[str]) -> None:
    title = "Les recommandations sont construites autour de ces axes"
    # Sert UNIQUEMENT à décider combien d'axes tiennent par page (1.4in ~
    # hauteur de contenu typique après un titre sur une ligne, cf. _new_slide) ;
    # chaque page recalcule ensuite sa hauteur réellement disponible à partir
    # de SON PROPRE titre (avec suffixe) une fois la slide créée, donc ce
    # découpage préalable ne peut jamais faire déborder une carte — au pire
    # (titre passé à 2 lignes à cause du suffixe) la page rend des rangées
    # un peu plus basses que prévu, jamais hors-cadre.
    w_in, h_in = _dims(prs)
    band_h_estimate = h_in - 1.4 - 0.5
    row_h_estimate = max(_AXES_ROW_H_MIN, _axes_row_h(len(axes), band_h_estimate))
    pages = D.paginer_items(
        list(enumerate(axes)), lambda _item: row_h_estimate + _AXES_ROW_GAP,
        capacite_in=band_h_estimate + _AXES_ROW_GAP,
    )
    for k, page in enumerate(pages):
        suffix = f" ({k + 1}/{len(pages)})" if len(pages) > 1 else ""
        slide, w_in, h_in, top = _new_slide(prs, title + suffix)
        band_h = h_in - top - 0.5
        row_h = _axes_row_h(len(page), band_h)
        total_h = len(page) * row_h + _AXES_ROW_GAP * (len(page) - 1)
        # Centré verticalement dans la bande plutôt que plaqué en haut : avec
        # peu d'axes (1-3) sur la page, row_h plafonne à 1.1in et laisse
        # sinon un grand vide sous les cartes.
        y = top + max(0.0, (band_h - total_h) / 2)
        for i, axis in page:
            accent = palette[i % len(palette)]
            D.add_card(slide, MARGIN, y, w_in - 2 * MARGIN, row_h, accent)
            D.add_text(
                slide, MARGIN + 0.3, y, 1.0, row_h,
                [(f"#{i + 1}", {"size": D.TYPE["kpi"], "bold": True, "color": accent})],
                anchor=MSO_ANCHOR.MIDDLE,
            )
            D.add_text(
                slide, MARGIN + 1.5, y, w_in - 2 * MARGIN - 2.0, row_h,
                [
                    (axis.title, {"size": D.TYPE["h3"], "bold": True, "color": D.INK}),
                    (f"{len(axis.recommendations)} recommandation{'s' if len(axis.recommendations) > 1 else ''}", {"size": D.TYPE["small"], "color": D.MUTED}),
                ],
                anchor=MSO_ANCHOR.MIDDLE,
            )
            y += row_h + _AXES_ROW_GAP


def _slide_matrice_effort_valeur(prs: Presentation, axes: list) -> None:
    slide, w_in, h_in, top = _new_slide(prs, "Matrice effort / valeur")
    # Libellé de légende court : la zone de légende (à droite du nuage) est étroite —
    # tronquer le titre à ~1.5in évite que PowerPoint le coupe lui-même en plein mot
    # (finding pptx-verify 2026-07-22). Le numéro (1.1, 2.1) identifie la reco, le titre
    # complet vit sur la fiche détaillée.
    recos = [(f"{i + 1}.{j + 1} {D.tronquer_a_lignes(r.title, 1.5, D.TYPE['small'], 1)}", r) for i, axis in enumerate(axes) for j, r in enumerate(axis.recommendations)]

    chart_data = XyChartData()
    for name, reco in recos:
        series = chart_data.add_series(name)
        series.add_data_point(reco.complexite, reco.valeur)

    chart_w = w_in - 2 * MARGIN
    chart_h = h_in - top - 0.5
    gf = slide.shapes.add_chart(
        XL_CHART_TYPE.XY_SCATTER, Inches(MARGIN), Inches(top), Inches(chart_w), Inches(chart_h), chart_data
    )
    chart = gf.chart
    chart.has_legend = bool(recos)
    chart.has_title = False
    try:
        chart.category_axis.minimum_scale = 0
        chart.category_axis.maximum_scale = 5.5
        chart.category_axis.axis_title.text_frame.text = "Complexité (effort)"
        chart.value_axis.minimum_scale = 0
        chart.value_axis.maximum_scale = 5.5
        chart.value_axis.axis_title.text_frame.text = "Valeur (impact)"
    except Exception:
        pass  # garde-fou : un axe non personnalisable ne doit pas casser l'export


def _slide_recommendation(prs: Presentation, axis: object, index: str, reco: object) -> None:
    slide, w_in, h_in, top = _new_slide(prs, f"{index} — {reco.title}")
    left_w = w_in * 0.34
    right_x = MARGIN + left_w + 0.4
    right_w = w_in - right_x - MARGIN
    band_h = h_in - top - 0.4

    # Colonne gauche : objectif / acteurs / jauges valeur-complexité / résultats.
    # OBJECTIF et ACTEURS adaptent leur police à un plafond (D.ajuster_police,
    # troncature en dernier recours) et renvoient leur hauteur RÉELLEMENT
    # occupée — le bloc suivant s'empile sur cette hauteur mesurée plutôt
    # qu'un décalage fixe, qui déborderait avec une réponse d'entretien plus
    # longue que le jeu de données de test habituel.
    y = top
    y += _add_measured_field(slide, MARGIN, y, left_w, "OBJECTIF", reco.objectif, max_h=1.1)
    y += 0.15
    y += _add_measured_field(slide, MARGIN, y, left_w, "ACTEURS", reco.acteurs, max_h=0.5)
    y += 0.15
    D.add_text(slide, MARGIN, y, left_w, 0.3, [("CRITÈRES DE PRIORISATION", {"size": D.TYPE["small"], "bold": True, "color": D.MUTED})])
    # Jauge dimensionnée pour que ses labels ("Valeur"/"Complexité") restent sur la
    # slide même sur un gabarit court (OCTO 5.625in) : sans ce plafond, la colonne
    # gauche (objectif+acteurs+jauges+labels) débordait le bas sur une fiche chargée
    # (verifier_geometrie 2026-07-22, adoption du template OCTO). label_h = 0.25.
    gauge_size = max(0.7, min(1.1, (h_in - 0.5) - (y + 0.35) - 0.05 - 0.25))
    D.add_gauge(slide, MARGIN, y + 0.35, gauge_size, reco.valeur / 5, D.OK)
    D.add_text(
        slide, MARGIN, y + 0.35, gauge_size, gauge_size,
        [(str(reco.valeur), {"size": D.TYPE["h3"], "bold": True, "color": D.INK, "align": PP_ALIGN.CENTER})],
        anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER,
    )
    D.add_text(slide, MARGIN, y + 0.35 + gauge_size + 0.05, gauge_size, 0.25, [("Valeur", {"size": D.TYPE["tiny"], "color": D.MUTED, "align": PP_ALIGN.CENTER})], align=PP_ALIGN.CENTER)
    gx2 = MARGIN + gauge_size + 0.3
    D.add_gauge(slide, gx2, y + 0.35, gauge_size, reco.complexite / 5, D.WARN)
    D.add_text(
        slide, gx2, y + 0.35, gauge_size, gauge_size,
        [(str(reco.complexite), {"size": D.TYPE["h3"], "bold": True, "color": D.INK, "align": PP_ALIGN.CENTER})],
        anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER,
    )
    D.add_text(slide, gx2, y + 0.35 + gauge_size + 0.05, gauge_size, 0.25, [("Complexité", {"size": D.TYPE["tiny"], "color": D.MUTED, "align": PP_ALIGN.CENTER})], align=PP_ALIGN.CENTER)
    y += 0.35 + gauge_size + 0.35
    remaining = top + band_h - y
    resultats_overflow: list[str] = []
    if remaining > 0.4:
        D.add_text(slide, MARGIN, y, left_w, 0.3, [("RÉSULTATS ATTENDUS", {"size": D.TYPE["small"], "bold": True, "color": D.MUTED})])
        resultats_overflow = _add_bulleted_text(
            slide, MARGIN, y + 0.3, left_w, remaining - 0.3, reco.resultats_attendus,
            size=D.TYPE["small"], paginate=True,
        )

    # Colonne droite : proposition de valeur / plan d'actions — même logique
    # de hauteur mesurée qu'à gauche pour PROPOSITION DE VALEUR, PLAN
    # D'ACTIONS prend ensuite tout l'espace restant réellement disponible.
    right_h = _add_measured_field(
        slide, right_x, top, right_w, "PROPOSITION DE VALEUR", reco.proposition_valeur, max_h=1.6,
        bold=True, italic=True,
    )
    plan_top = top + right_h + 0.2
    D.add_text(slide, right_x, plan_top, right_w, 0.3, [("PLAN D'ACTIONS", {"size": D.TYPE["small"], "bold": True, "color": D.MUTED})])
    plan_overflow = _add_bulleted_text(
        slide, right_x, plan_top + 0.3, right_w, top + band_h - plan_top - 0.3, reco.plan_actions, paginate=True,
    )

    base_title = f"{index} — {reco.title}"
    if resultats_overflow:
        _emit_bullet_overflow(prs, base_title, "Résultats attendus", resultats_overflow)
    if plan_overflow:
        _emit_bullet_overflow(prs, base_title, "Plan d'actions", plan_overflow)


# --------------------------------------------------------------------------- #
# Façade
# --------------------------------------------------------------------------- #
def build_presentation(
    mission: Mission,
    template_path: Path | None = None,
    include_sommaire: bool = True,
    include_executive_summary: bool = True,
    include_synthese: bool = True,
    include_difficultes: bool = True,
    include_swot: bool = True,
    include_verbatims: bool = True,
    include_axes_overview: bool = True,
    include_matrix: bool = True,
    include_axis_ids: set[int] | None = None,
) -> Presentation:
    """`include_axis_ids=None` inclut les fiches de recommandation de tous les
    axes (comportement par défaut/rétrocompatible) ; un set (même vide)
    restreint aux axes dont l'id y figure — la vue d'ensemble des axes et la
    matrice effort/valeur restent, elles, toujours complètes (ce sont des
    slides de synthèse, pas de détail par axe)."""
    if template_path and Path(template_path).exists():
        prs = Presentation(str(template_path))
        _clear_slides(prs)
    elif OCTO_TEMPLATE_PATH.exists():
        # Défaut : le template de marque OCTO (chrome + layouts + thème + Outfit).
        prs = Presentation(str(OCTO_TEMPLATE_PATH))
        _clear_slides(prs)
    else:
        prs = Presentation()
        prs.slide_width = Inches(_W_IN)
        prs.slide_height = Inches(_H_IN)
        prs._i2d_synthetic = True

    # Police de marque du template (Outfit sur OCTO) appliquée à TOUT texte dessiné
    # via pptx_deck.add_text — détectée sur les placeholders, pas le fontScheme (repli
    # Arial). None sur le deck synthétique -> héritage par défaut (inchangé).
    D.set_police(D.police_marque(prs))

    # Ancre la palette catégorielle des axes sur la couleur de marque du
    # template injecté, sans jamais remplacer toute la palette par elle
    # (une palette catégorielle reste plus lisible pour distinguer N axes).
    brand_accent = D.theme_colors(prs).get("accent1")
    palette = ([brand_accent] + D.PALETTE) if brand_accent else D.PALETTE

    _slide_cover(prs, mission)

    gs = mission.global_synthesis
    swot = mission.swot
    executive_summary = mission.executive_summary
    difficulties = [d for d in mission.difficulties if (d.label or "").strip()]
    verbatims = mission.selected_verbatims
    axes = list(mission.recommendation_axes)
    selected_axes = [a for a in axes if include_axis_ids is None or a.id in include_axis_ids]

    # Sections présentes, groupées par chapitre (P2 — structure narrative). Un
    # intercalaire ouvre chaque chapitre qui a du contenu ; le sommaire quali les liste.
    ch_sections: list[list[str]] = [[] for _ in _CHAPITRES]
    if include_executive_summary and executive_summary and executive_summary.has_content:
        ch_sections[_CH_RETENIR].append("Executive Summary")
    if include_synthese and gs and gs.has_content:
        ch_sections[_CH_DIAGNOSTIC].append("Synthèse globale")
    if include_difficultes and difficulties:
        ch_sections[_CH_DIAGNOSTIC].append("Difficultés")
    if include_swot and swot and swot.has_content:
        ch_sections[_CH_DIAGNOSTIC].append("Matrice SWOT")
    if include_verbatims and verbatims:
        ch_sections[_CH_PAROLE].append("Paroles d'acteurs")
    # « Recommandations » dès qu'il y a des axes à détailler (les fiches reco
    # s'émettent indépendamment des toggles overview/matrice) OU la vue d'ensemble.
    if (axes and include_axes_overview) or selected_axes:
        ch_sections[_CH_TRAJECTOIRE].append("Recommandations")
    if axes and include_matrix:
        ch_sections[_CH_TRAJECTOIRE].append("Matrice effort / valeur")

    if include_sommaire and any(ch_sections):
        _slide_sommaire(prs, ch_sections)

    numero = 0

    def _chapitre(ci: int) -> None:
        nonlocal numero
        numero += 1
        label, color, scene = _CHAPITRES[ci]
        _slide_chapitre(prs, numero, label, color, scene)

    # Chapitre 1 — Ce qu'il faut retenir
    if ch_sections[_CH_RETENIR]:
        _chapitre(_CH_RETENIR)
        _slide_executive_summary(prs, executive_summary)

    # Chapitre 2 — Le diagnostic
    if ch_sections[_CH_DIAGNOSTIC]:
        _chapitre(_CH_DIAGNOSTIC)
        if include_synthese and gs and gs.has_content:
            categories = [
                ("Contexte", gs.contexte),
                ("Culture & ADN", gs.culture_adn),
                ("Forces & succès", gs.forces_succes),
                ("Points d'amélioration", gs.points_amelioration),
                ("Aspirations (baguette magique)", gs.aspirations),
            ]
            for label, content in categories:
                if (content or "").strip():
                    _slide_synthese_categorie(prs, label, content)
        if include_difficultes and difficulties:
            _slide_difficultes(prs, difficulties)
        if include_swot and swot and swot.has_content:
            _slide_swot(prs, swot)

    # Chapitre 3 — La parole des équipes
    if ch_sections[_CH_PAROLE]:
        _chapitre(_CH_PAROLE)
        _slide_verbatims(prs, verbatims)

    # Chapitre 4 — La trajectoire proposée
    if ch_sections[_CH_TRAJECTOIRE]:
        _chapitre(_CH_TRAJECTOIRE)
        if axes and include_axes_overview:
            _slide_axes_overview(prs, axes, palette)
        if axes and include_matrix:
            _slide_matrice_effort_valeur(prs, axes)
        for i, axis in enumerate(axes):
            if axis not in selected_axes:
                continue
            for j, reco in enumerate(axis.recommendations):
                _slide_recommendation(prs, axis, f"{i + 1}.{j + 1}", reco)

    # Garde-fou géométrique (US7.1) : un texte trop long ou un template client
    # aux dimensions inattendues peut faire déborder une forme de la slide —
    # mieux vaut échouer bruyamment ici qu'exporter un .pptx visuellement cassé.
    problemes = D.verifier_geometrie(prs)
    if problemes:
        raise RuntimeError(
            "Export PPT : formes hors cadre détectées —\n" + "\n".join(problemes)
        )

    return prs
