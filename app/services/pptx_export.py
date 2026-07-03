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
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches

from ..models import Mission
from . import pptx_deck as D

MARGIN = 0.6


def _dims(prs: Presentation) -> tuple[float, float]:
    return Emu(prs.slide_width).inches, Emu(prs.slide_height).inches


def _clear_slides(prs: Presentation) -> None:
    """Retire toutes les slides d'une présentation chargée depuis un template
    client — on ne veut hériter que masters/layouts/thème. Sans ça, un
    template qui est un vrai exemple de deck (pas un .potx vierge) ferait
    apparaître tout son contenu d'origine avant le nôtre. `python-pptx`
    n'expose pas de suppression de slide côté API publique ; on vide
    directement la liste XML des slides (limite connue/acceptée : les parts
    des anciennes slides restent dans l'archive, inutilisées — pas de risque
    de fuite visible, juste un fichier un peu plus lourd)."""
    sld_id_lst = prs.slides._sldIdLst
    for sld_id in list(sld_id_lst):
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
    slide = prs.slides.add_slide(_pick_layout(prs))
    w_in, h_in = _dims(prs)
    # Le placeholder de titre natif hérite police/couleur/position du
    # template — préféré à une zone de texte dessinée à la main dès qu'il
    # existe sur le layout choisi.
    if slide.shapes.title is not None:
        slide.shapes.title.text = title
    else:
        D.add_text(
            slide, MARGIN, 0.35, w_in - 2 * MARGIN, 0.7,
            [(title, {"size": D.TYPE["title"], "bold": True, "color": D.INK})],
        )
    return slide, w_in, h_in


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
) -> None:
    lines = _bullet_lines(text) or ["—"]

    if size is None:
        def budget_ok(taille, _lignes_max):
            total = sum(D.estimer_lignes(line, w, taille) for line in lines)
            return total * _per_line_height_in(taille) <= h

        size, _ = D.ajuster_police(lines, w, size_max, size_min, budget_ok=budget_ok)

    paragraphs = [(f"•  {line}", {"size": size, "color": D.INK, "space_after": 4}) for line in lines]

    if anchor == MSO_ANCHOR.MIDDLE:
        total_lines = sum(D.estimer_lignes(line, w, size) for line in lines)
        content_h = min(h, total_lines * _per_line_height_in(size))
        box_t = t + max(0.0, (h - content_h) / 2)
        D.add_text(slide, l, box_t, w, content_h, paragraphs)
    else:
        D.add_text(slide, l, t, w, h, paragraphs)


# --------------------------------------------------------------------------- #
# Slides
# --------------------------------------------------------------------------- #
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
    slide, w_in, h_in = _new_slide(prs, "Sommaire")
    lines = [
        (f"{i:02d}   {label}", {"size": D.TYPE["h2"], "color": D.INK, "space_after": 14})
        for i, label in enumerate(sections, start=1)
    ]
    D.add_text(slide, MARGIN + 0.3, 1.6, w_in - 2 * (MARGIN + 0.3), h_in - 2.2, lines)


def _slide_synthese_categorie(prs: Presentation, label: str, content: str) -> None:
    slide, w_in, h_in = _new_slide(prs, f"Synthèse globale — {label}")
    top = 1.5
    _add_bulleted_text(
        slide, MARGIN + 0.3, top, w_in - 2 * (MARGIN + 0.3), h_in - top - 0.5, content,
        anchor=MSO_ANCHOR.MIDDLE, size_max=20, size_min=D.TYPE["small"],
    )


def _slide_axes_overview(prs: Presentation, axes: list, palette: list[str]) -> None:
    slide, w_in, h_in = _new_slide(prs, "Les recommandations sont construites autour de ces axes")
    top = 1.5
    band_h = h_in - top - 0.5
    row_h = min(1.1, (band_h - 0.15 * (len(axes) - 1)) / max(1, len(axes)))
    y = top
    for i, axis in enumerate(axes):
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
        y += row_h + 0.15


def _slide_matrice_effort_valeur(prs: Presentation, axes: list) -> None:
    slide, w_in, h_in = _new_slide(prs, "Matrice effort / valeur")
    recos = [(f"{i + 1}.{j + 1} {r.title[:20]}", r) for i, axis in enumerate(axes) for j, r in enumerate(axis.recommendations)]

    chart_data = XyChartData()
    for name, reco in recos:
        series = chart_data.add_series(name)
        series.add_data_point(reco.complexite, reco.valeur)

    top = 1.5
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
    slide, w_in, h_in = _new_slide(prs, f"{index} — {reco.title}")
    top = 1.4
    left_w = w_in * 0.34
    right_x = MARGIN + left_w + 0.4
    right_w = w_in - right_x - MARGIN
    band_h = h_in - top - 0.4

    # Colonne gauche : objectif / acteurs / jauges valeur-complexité / résultats
    y = top
    D.add_text(slide, MARGIN, y, left_w, 0.3, [("OBJECTIF", {"size": D.TYPE["small"], "bold": True, "color": D.MUTED})])
    D.add_text(slide, MARGIN, y + 0.3, left_w, 0.9, [(reco.objectif or "—", {"size": D.TYPE["body"], "color": D.INK})])
    y += 1.3
    D.add_text(slide, MARGIN, y, left_w, 0.3, [("ACTEURS", {"size": D.TYPE["small"], "bold": True, "color": D.MUTED})])
    D.add_text(slide, MARGIN, y + 0.3, left_w, 0.6, [(reco.acteurs or "—", {"size": D.TYPE["body"], "color": D.INK})])
    y += 1.0
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
    if remaining > 0.4:
        D.add_text(slide, MARGIN, y, left_w, 0.3, [("RÉSULTATS ATTENDUS", {"size": D.TYPE["small"], "bold": True, "color": D.MUTED})])
        _add_bulleted_text(slide, MARGIN, y + 0.3, left_w, remaining - 0.3, reco.resultats_attendus, size=D.TYPE["small"])

    # Colonne droite : proposition de valeur / plan d'actions
    D.add_text(slide, right_x, top, right_w, 0.3, [("PROPOSITION DE VALEUR", {"size": D.TYPE["small"], "bold": True, "color": D.MUTED})])
    D.add_text(slide, right_x, top + 0.3, right_w, 0.9, [(reco.proposition_valeur or "—", {"size": D.TYPE["body"], "bold": True, "color": D.INK, "italic": True})])
    plan_top = top + 1.4
    D.add_text(slide, right_x, plan_top, right_w, 0.3, [("PLAN D'ACTIONS", {"size": D.TYPE["small"], "bold": True, "color": D.MUTED})])
    _add_bulleted_text(slide, right_x, plan_top + 0.3, right_w, top + band_h - plan_top - 0.3, reco.plan_actions)


# --------------------------------------------------------------------------- #
# Façade
# --------------------------------------------------------------------------- #
def build_presentation(
    mission: Mission,
    template_path: Path | None = None,
    include_sommaire: bool = True,
    include_synthese: bool = True,
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

    # Ancre la palette catégorielle des axes sur la couleur de marque du
    # template injecté, sans jamais remplacer toute la palette par elle
    # (une palette catégorielle reste plus lisible pour distinguer N axes).
    brand_accent = D.theme_colors(prs).get("accent1")
    palette = ([brand_accent] + D.PALETTE) if brand_accent else D.PALETTE

    _slide_title(prs, mission)

    gs = mission.global_synthesis
    axes = list(mission.recommendation_axes)
    selected_axes = [a for a in axes if include_axis_ids is None or a.id in include_axis_ids]

    sections = []
    if include_synthese and gs and gs.has_content:
        sections.append("Synthèse globale")
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

    if axes and include_axes_overview:
        _slide_axes_overview(prs, axes, palette)
    if axes and include_matrix:
        _slide_matrice_effort_valeur(prs, axes)
    for i, axis in enumerate(axes):
        if axis not in selected_axes:
            continue
        for j, reco in enumerate(axis.recommendations):
            _slide_recommendation(prs, axis, f"{i + 1}.{j + 1}", reco)

    return prs
