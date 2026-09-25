"""Façade de l'export PPT : `build_presentation` assemble le deck complet
(couverture, sommaire, intercalaires, slides de contenu) puis fait respecter
le garde-fou géométrique. Extrait de pptx_export.py (découpage du gros
module, finding audit 2026-07-24) — code déplacé tel quel."""
from __future__ import annotations

import logging
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

from ...models import Mission
from .. import pptx_deck as D
from .base import _H_IN, _W_IN, OCTO_TEMPLATE_PATH, _clear_slides

# Bas de dessin que les slides tenues au badge n° de page s'imposent
# (h - 0.60, cf. slides_trajectoire) — confronté au gabarit chargé à chaque build.
_PLANCHER_DESSIN_IN = _H_IN - 0.60

logger = logging.getLogger(__name__)
from .slides_cadre import (
    _CH_DIAGNOSTIC,
    _CH_PAROLE,
    _CH_RETENIR,
    _CH_TRAJECTOIRE,
    _CHAPITRES,
    _slide_chapitre,
    _slide_cover,
    _slide_sommaire,
)
from .slides_diagnostic import (
    _slide_difficultes,
    _slide_executive_summary,
    _slide_maturite,
    _slide_swot,
    _slide_synthese_categorie,
    _slide_verbatims,
)
from .slides_trajectoire import (
    _slide_axes_overview,
    _slide_kpis,
    _slide_matrice_effort_valeur,
    _slide_matrice_risques,
    _slide_recommendation,
)


def _axes(axes_etude):
    """Axes d'étude à restituer. Sans liste fournie (appelants historiques,
    tests), les 5 axes par défaut — deck inchangé."""
    if axes_etude:
        return list(axes_etude)
    from ..synthese_ai import _axes_par_defaut

    return _axes_par_defaut()


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
    include_kpis: bool = True,
    include_risques: bool = True,
    include_maturite: bool = True,
    include_axis_ids: set[int] | None = None,
    axes_etude=None,
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

    # Police effective du deck. On PRÉFÈRE la police du THÈME (fontScheme, Arial sur
    # OCTO) à celle des placeholders (Outfit) : Outfit n'étant pas installée, elle est
    # rendue en substitution — c'est la cause du « la police ne matche pas la référence »
    # (bmad-iap-cadrage-synthese utilise, lui, la police du thème). Repli sur la police
    # des placeholders puis héritage. None sur le deck synthétique (inchangé).
    if getattr(prs, "_i2d_synthetic", False):
        D.set_police(None)
    else:
        D.set_police(D.police_theme(prs) or D.police_marque(prs))

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
    kpis = [k for k in mission.kpis if (k.libelle or "").strip()]
    risks = [r for r in mission.risks if (r.risque or "").strip()]
    maturites = [m for m in mission.maturites if (m.pilier or "").strip()]
    axes = list(mission.recommendation_axes)
    selected_axes = [a for a in axes if include_axis_ids is None or a.id in include_axis_ids]

    # Sections présentes, groupées par chapitre (P2 — structure narrative). Un
    # intercalaire ouvre chaque chapitre qui a du contenu ; le sommaire quali les liste.
    # Matière de synthèse RÉELLEMENT restituable : le contenu des axes que la mission
    # étudie AUJOURD'HUI. `gs.has_content` répond sur `valeurs` + les 5 colonnes
    # historiques, donc reste vrai pour un axe supprimé depuis — le sommaire annonçait
    # alors « Synthèse globale » et l'intercalaire s'ouvrait sur zéro slide (même défaut
    # de parité que celui corrigé le 2026-07-22 sur les difficultés).
    synthese_axes = (
        [a for a in _axes(axes_etude) if (gs.contenu(a.key) or "").strip()] if gs else []
    )
    ch_sections: list[list[str]] = [[] for _ in _CHAPITRES]
    if include_executive_summary and executive_summary and executive_summary.has_content:
        ch_sections[_CH_RETENIR].append("Executive Summary")
    if include_synthese and synthese_axes:
        ch_sections[_CH_DIAGNOSTIC].append("Synthèse globale")
    if include_difficultes and difficulties:
        ch_sections[_CH_DIAGNOSTIC].append("Difficultés")
    if include_swot and swot and swot.has_content:
        ch_sections[_CH_DIAGNOSTIC].append("Matrice SWOT")
    # Maturité par pilier (incr.10 palier 3) : clôt le diagnostic, après la SWOT.
    if include_maturite and maturites:
        ch_sections[_CH_DIAGNOSTIC].append("Maturité par pilier")
    if include_verbatims and verbatims:
        ch_sections[_CH_PAROLE].append("Paroles d'acteurs")
    # « Recommandations » dès qu'il y a des axes à détailler (les fiches reco
    # s'émettent indépendamment des toggles overview/matrice) OU la vue d'ensemble.
    if (axes and include_axes_overview) or selected_axes:
        ch_sections[_CH_TRAJECTOIRE].append("Recommandations")
    if axes and include_matrix:
        ch_sections[_CH_TRAJECTOIRE].append("Matrice de priorisation")
    # Suivi (US9.27) : après les recommandations — mesurer, puis maîtriser les risques.
    if include_kpis and kpis:
        ch_sections[_CH_TRAJECTOIRE].append("Indicateurs de suivi")
    if include_risques and risks:
        ch_sections[_CH_TRAJECTOIRE].append("Risques et contrôles")

    if include_sommaire and any(ch_sections):
        _slide_sommaire(prs, ch_sections)

    numero = 0

    def _chapitre(ci: int) -> None:
        nonlocal numero
        numero += 1
        label, color, scene, sous_titre = _CHAPITRES[ci]
        _slide_chapitre(prs, numero, label, color, scene, sous_titre=sous_titre)

    # Chapitre 1 — Ce qu'il faut retenir
    if ch_sections[_CH_RETENIR]:
        _chapitre(_CH_RETENIR)
        _slide_executive_summary(prs, executive_summary)

    # Chapitre 2 — Le diagnostic
    if ch_sections[_CH_DIAGNOSTIC]:
        _chapitre(_CH_DIAGNOSTIC)
        if include_synthese:
            # Une slide par AXE de la mission (2026-07-27) : les 5 rubriques étaient
            # figées ici, un axe ajouté n'aurait jamais atteint le deck. Même liste
            # que celle qui a décidé du sommaire (parité), et la `key` accompagne le
            # libellé jusqu'à la slide : le visuel est indexé dessus, le libellé
            # étant renommable (correctif 2026-07-28).
            for axe in synthese_axes:
                _slide_synthese_categorie(prs, axe.label, gs.contenu(axe.key), axe.key)
        if include_difficultes and difficulties:
            _slide_difficultes(prs, difficulties)
        if include_swot and swot and swot.has_content:
            _slide_swot(prs, swot)
        if include_maturite and maturites:
            _slide_maturite(prs, maturites)

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
            _slide_matrice_effort_valeur(prs, axes, palette)
        for i, axis in enumerate(axes):
            if axis not in selected_axes:
                continue
            for j, reco in enumerate(axis.recommendations):
                # accent = couleur d'axe (identité) — même palette que la vue
                # d'ensemble et les bulles de la matrice de priorisation.
                _slide_recommendation(prs, axis, f"{i + 1}.{j + 1}", reco,
                                      accent=palette[i % len(palette)])
        if include_kpis and kpis:
            _slide_kpis(prs, kpis, axes, palette)
        if include_risques and risks:
            _slide_matrice_risques(prs, risks)

    # Garde-fou géométrique (US7.1) : un texte trop long ou un template client
    # aux dimensions inattendues peut faire déborder une forme de la slide —
    # mieux vaut échouer bruyamment ici qu'exporter un .pptx visuellement cassé.
    problemes = D.verifier_geometrie(prs)
    if problemes:
        raise RuntimeError(
            "Export PPT : formes hors cadre détectées —\n" + "\n".join(problemes)
        )

    # Filets « chrome du gabarit » (portés de la skill pptx-deck, audit
    # 2026-09-21) : le badge n° de page vit sur le layout/master, hors de
    # portée de verifier_geometrie. Plancher : la marge -0.60 que les slides
    # tenues au badge s'imposent (slides_trajectoire) confrontée au gabarit
    # CHARGÉ — une dérive du gabarit fait échouer l'export, comme la géométrie.
    problemes = D.verifier_plancher_de_dessin(prs, _PLANCHER_DESSIN_IN)
    if problemes:
        raise RuntimeError(
            "Export PPT : plancher de dessin décroché du gabarit —\n"
            + "\n".join(problemes)
        )
    # Recouvrement du badge n° de page hérité du gabarit : même politique que la
    # géométrie (échec bruyant). Au portage, 5 slides réelles le recouvraient
    # (synthèse sans encart, SWOT) — corrigées dans slides_diagnostic.
    problemes = D.verifier_chrome_gabarit(prs)
    if problemes:
        raise RuntimeError(
            "Export PPT : formes sur le n° de page du gabarit —\n"
            + "\n".join(problemes)
        )

    # Filet anti-corruption avant remise au routeur (0 en pratique ici : les
    # suppressions de slide de ce module passent déjà par drop_rel) — coût nul
    # dans le cas sain, garde-fou bon marché avant tout save() en aval. Le
    # retour n'est PAS ignoré (revue adversariale 2026-09-07) : un filet qui
    # répare en silence ne détecterait jamais la régression qu'il existe pour
    # attraper.
    purges = D.purger_rels_slides_orphelines(prs)
    if purges:
        logger.warning(
            "build_presentation : %d relation(s) de slide orpheline(s) purgée(s) "
            "avant export — une suppression de slide en amont a sauté drop_rel",
            purges,
        )

    return prs
