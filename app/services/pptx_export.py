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
_W_IN, _H_IN = 13.333, 7.5
_LEFT_W = _W_IN * 0.34
_RIGHT_W = _W_IN - (MARGIN + _LEFT_W + 0.4) - MARGIN

FIELD_SHAPE = {
    "objectif": dict(width_in=_LEFT_W, max_h_in=1.1),
    "acteurs": dict(width_in=_LEFT_W, max_h_in=0.5),
    "resultats_attendus": dict(width_in=_LEFT_W, max_h_in=1.5),
    "proposition_valeur": dict(width_in=_RIGHT_W, max_h_in=1.6),
    "plan_actions": dict(width_in=_RIGHT_W, max_h_in=2.8),
    "reco_title": dict(width_in=_W_IN - 2 * MARGIN, size_pt=D.TYPE["title"], max_lignes=2),
    "axis_title": dict(width_in=_W_IN - 2 * MARGIN - 2.0, max_h_in=1.1, size_max=D.TYPE["h3"]),
    "synthese_categorie": dict(width_in=_W_IN - 2 * (MARGIN + 0.3), max_h_in=5.0, size_max=20),
    # Un quadrant SWOT = ~demi-largeur de la zone de contenu ; la hauteur de la
    # zone de PUCES (pas de la carte) = row_h - titre - paddings ≈ 1.9 in sur un
    # deck vierge (cf. _slide_swot) — pas la demi-hauteur brute (~2.2), qui
    # surestimait le budget du repère de ~20 % et rendait le fit-hint trompeur.
    "swot_quadrant": dict(width_in=(_W_IN - 2 * (MARGIN + 0.3) - 0.25) / 2 - 0.36, max_h_in=1.9, size_max=D.TYPE["small"]),
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
    for kw in ("title only", "section"):
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


def _slide_title(prs: Presentation, mission: Mission) -> None:
    slide = prs.slides.add_slide(_pick_layout(prs))
    w_in, h_in = _dims(prs)
    cy = h_in * 0.4
    D.add_text(
        slide, 1.0, cy, w_in - 2.0, 1.0,
        [(mission.name, {"size": 32, "bold": True, "color": D.INK, "align": PP_ALIGN.CENTER})],
    )
    D.add_text(
        slide, 1.0, cy + 0.9, w_in - 2.0, 0.6,
        [("Synthèse transverse & recommandations", {"size": D.TYPE["h2"], "color": D.MUTED, "align": PP_ALIGN.CENTER})],
    )
    D.add_text(
        slide, 1.0, cy + 1.5, w_in - 2.0, 0.4,
        [(datetime.now(timezone.utc).strftime("%d/%m/%Y"), {"size": D.TYPE["small"], "color": D.MUTED, "align": PP_ALIGN.CENTER})],
    )


def _slide_sommaire(prs: Presentation, sections: list[str]) -> None:
    slide, w_in, h_in, top = _new_slide(prs, "Sommaire")
    lines = [
        (f"{i:02d}   {label}", {"size": D.TYPE["h2"], "color": D.INK, "space_after": 14})
        for i, label in enumerate(sections, start=1)
    ]
    D.add_text(slide, MARGIN + 0.3, top + 0.1, w_in - 2 * (MARGIN + 0.3), h_in - top - 0.7, lines)


def _slide_synthese_categorie(prs: Presentation, label: str, content: str) -> None:
    slide, w_in, h_in, top = _new_slide(prs, f"Synthèse globale — {label}")
    # Ancré en haut (pas MIDDLE) : un paragraphe court dans une grande bande
    # centrée verticalement laisse un vide au-dessus ET en dessous, plus
    # visible qu'un unique vide en bas — cf. skill pptx-deck, principe n°2.
    _add_bulleted_text(
        slide, MARGIN + 0.3, top, w_in - 2 * (MARGIN + 0.3), h_in - top - 0.5, content,
        anchor=MSO_ANCHOR.TOP, size_max=20, size_min=D.TYPE["small"],
    )


# SWOT : Forces/Faiblesses = interne (vert/rouge), Opportunités/Menaces =
# externe (bleu/ambre). Couleurs sémantiques prises dans D.PALETTE (design
# system : différenciation par liseré de carte, pas de dégradé/ombre).
_SWOT_QUADRANTS = [
    ("forces", "Forces", "#1e6b34"),
    ("faiblesses", "Faiblesses", "#b3261e"),
    ("opportunites", "Opportunités", "#2c5cc5"),
    ("menaces", "Menaces", "#b8860b"),
]


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
        D.add_text(
            slide, cl + pad, ct + pad * 0.7, col_w - 2 * pad, title_h,
            [(label, {"size": D.TYPE["h3"], "bold": True, "color": color})],
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
                    (f"{len(axis.recommendations)} recommandation(s)", {"size": D.TYPE["small"], "color": D.MUTED}),
                ],
                anchor=MSO_ANCHOR.MIDDLE,
            )
            y += row_h + _AXES_ROW_GAP


def _slide_matrice_effort_valeur(prs: Presentation, axes: list) -> None:
    slide, w_in, h_in, top = _new_slide(prs, "Matrice effort / valeur")
    recos = [(f"{i + 1}.{j + 1} {D.tronquer_a_lignes(r.title, 3.0, D.TYPE['small'], 1)}", r) for i, axis in enumerate(axes) for j, r in enumerate(axis.recommendations)]

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
    gauge_size = 1.1
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
    include_synthese: bool = True,
    include_swot: bool = True,
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
    else:
        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        prs._i2d_synthetic = True

    # Ancre la palette catégorielle des axes sur la couleur de marque du
    # template injecté, sans jamais remplacer toute la palette par elle
    # (une palette catégorielle reste plus lisible pour distinguer N axes).
    brand_accent = D.theme_colors(prs).get("accent1")
    palette = ([brand_accent] + D.PALETTE) if brand_accent else D.PALETTE

    _slide_title(prs, mission)

    gs = mission.global_synthesis
    swot = mission.swot
    axes = list(mission.recommendation_axes)
    selected_axes = [a for a in axes if include_axis_ids is None or a.id in include_axis_ids]

    sections = []
    if include_synthese and gs and gs.has_content:
        sections.append("Synthèse globale")
    if include_swot and swot and swot.has_content:
        sections.append("Matrice SWOT")
    if axes and include_axes_overview:
        sections.append("Recommandations")
    if axes and include_matrix:
        sections.append("Matrice effort / valeur")
    if include_sommaire:
        _slide_sommaire(prs, sections or ["Synthèse globale", "Recommandations"])

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

    if include_swot and swot and swot.has_content:
        _slide_swot(prs, swot)

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
