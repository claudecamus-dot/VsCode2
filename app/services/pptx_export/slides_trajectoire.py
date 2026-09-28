"""Slides de la trajectoire proposée : vue d'ensemble des axes, matrice de
priorisation valeur/effort dessinée, fiches de recommandation en encarts.
Extrait de pptx_export.py (découpage du gros module, finding audit
2026-07-24) — code déplacé tel quel."""
from __future__ import annotations

import copy

from pptx import Presentation
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

from ...models import criticite_risque, niveau_risque
from .. import pptx_deck as D
from .base import (
    MARGIN,
    _add_bulleted_text,
    _add_measured_field,
    _bullet_lines,
    _dims,
    _emit_bullet_overflow,
    _label_axe_vertical,
    _new_slide,
    _per_line_height_in,
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
        # -0.60 (pas -0.50) : la dernière rangée pleine largeur descendait sur le
        # badge n° de page du master (verifier_chrome_gabarit, 2026-09-23).
        band_h = h_in - top - 0.60
        row_h = _axes_row_h(len(page), band_h)
        total_h = len(page) * row_h + _AXES_ROW_GAP * (len(page) - 1)
        # Centré verticalement dans la bande plutôt que plaqué en haut : avec
        # peu d'axes (1-3) sur la page, row_h plafonne à 1.1in et laisse
        # sinon un grand vide sous les cartes.
        y = top + max(0.0, (band_h - total_h) / 2)
        for i, axis in page:
            accent = palette[i % len(palette)]
            D.add_card(slide, MARGIN, y, w_in - 2 * MARGIN, row_h, accent)
            # Pastille teardrop numérotée (signature OCTO du sommaire) plutôt que
            # « #N » nu, et les INTITULÉS des recos en 2e ligne plutôt qu'un simple
            # compte « 2 recommandations » — la rangée était à moitié vide et le
            # texte creux (constat utilisateur 2026-07-22, slide 16).
            td = min(0.62, row_h - 0.16)
            D.add_teardrop(slide, MARGIN + 0.28, y + (row_h - td) / 2, td,
                           str(i + 1), accent, size=D.TYPE["h3"])
            text_x = MARGIN + 0.28 + td + 0.3
            text_w = w_in - MARGIN - text_x - 0.3
            recos_txt = "   ·   ".join(
                f"{i + 1}.{j + 1}  {r.title}" for j, r in enumerate(axis.recommendations)
            )
            D.add_text(
                slide, text_x, y, text_w, row_h,
                [
                    (axis.title, {"size": D.TYPE["h3"], "bold": True, "color": D.INK,
                                  "space_after": 4}),
                    (D.tronquer_a_lignes(recos_txt, text_w, D.TYPE["small"], 2),
                     {"size": D.TYPE["small"], "color": D.MUTED}),
                ],
                anchor=MSO_ANCHOR.MIDDLE,
            )
            y += row_h + _AXES_ROW_GAP


# Quadrants de la matrice de priorisation (skill priority-matrix) : le SENS de
# chaque quadrant est écrit dessus — c'est ce qui transforme un nuage de points en
# outil de décision. (label, couleur) par position (colonne, ligne) de la grille.
def _fraction_score(score: int, bande: float = 0.0) -> float:
    """Position 0..1 du CENTRE d'une bulle sur son axe (0 = extrémité basse).

    Découpage PAR MOITIÉ (2026-07-28) : les scores 1-3 se répartissent dans la moitié
    basse, 4-5 dans la moitié haute. La règle linéaire précédente `(s-0.5)/5` posait le
    score 3 EXACTEMENT sur la frontière des quadrants — la bulle chevauchait deux
    quadrants de sens opposé et recouvrait leur libellé (constat de rendu). Ranger un
    3/5 dans la moitié basse ne trahit pas la donnée : c'est ce que dit la note, seuls
    4 et 5 sont « hauts ».

    `bande` (en fraction de l'axe) est réservée en TÊTE de chaque moitié, là où sont
    ancrés les libellés de quadrant — sans elle, une bulle de score 3 ou 5 vient les
    recouvrir."""
    if score <= 3:
        debut, fin, rang, nb = 0.0, 0.5, score - 1, 3
    else:
        debut, fin, rang, nb = 0.5, 1.0, score - 4, 2
    return debut + (rang + 0.5) / nb * (fin - bande - debut)


_PRIO_QUADRANTS = {
    # Libellés courts exprès : à `small` bold ils doivent tenir sur UNE ligne dans
    # une demi-grille (« CHANTIERS STRUCTURANTS » wrappait derrière les bulles).
    (0, 0): ("QUICK WINS", "#1e6b34"),          # valeur haute, effort faible
    (1, 0): ("CHANTIERS DE FOND", "#2c5cc5"),   # valeur haute, effort fort
    (0, 1): ("OPPORTUNISTES", "#6b7280"),       # valeur basse, effort faible
    (1, 1): ("À DIFFÉRER", "#b8860b"),          # valeur basse, effort fort
}


def _slide_matrice_effort_valeur(prs: Presentation, axes: list,
                                 palette: list[str]) -> None:
    """Matrice de priorisation valeur/effort DESSINÉE (skill priority-matrix) — le
    graphique scatter natif PowerPoint rendait « très mauvais » (constat utilisateur
    2026-07-22 : marqueurs Excel minuscules gris, légende cryptique ◆■▲, aucun
    quadrant). Ici : 4 quadrants teintés dont le SENS est écrit dessus, une bulle
    par reco (couleur = axe, même palette que la vue d'ensemble ; numéro dedans),
    bulles co-localisées déployées en éventail, légende par axe à droite."""
    slide, w_in, h_in, top = _new_slide(prs, "Matrice de priorisation — valeur / effort")
    # Zone de tracé (gouttière gauche pour le label d'axe Y roté, bande basse pour X).
    pl = MARGIN + 0.45
    pt = top + 0.10
    pb = h_in - 0.72
    ph = pb - pt
    pw = 3.95  # plot un peu plus étroit : la légende porte les intitulés COMPLETS
    lx = pl + pw + 0.3   # légende à droite
    lw = w_in - MARGIN - lx
    qw, qh = pw / 2, ph / 2

    # Quadrants teintés + libellé de sens dans chaque coin — en `small`, pas
    # `tiny` : lisibilité relevée par l'utilisateur (2026-07-22, slide 17).
    for (col, row), (lbl, color) in _PRIO_QUADRANTS.items():
        qx, qy = pl + col * qw, pt + row * qh
        D.add_rect(slide, qx, qy, qw, qh, fill=D.melanger_blanc(color, 0.93),
                   line=D.melanger_blanc(color, 0.70), line_w=0.75)
        D.add_text(
            slide, qx + 0.10, qy + 0.06, qw - 0.20, 0.26,
            [(lbl, {"size": D.TYPE["small"], "bold": True,
                    "color": D.melanger_blanc(color, 0.15)})],
            align=PP_ALIGN.LEFT if col == 0 else PP_ALIGN.RIGHT,
        )
    # Labels d'axes : X sous la zone, Y roté dans la gouttière gauche.
    D.add_text(slide, pl, pb + 0.08, pw, 0.3,
               [("Complexité (effort) →", {"size": D.TYPE["small"], "bold": True,
                                           "color": D.MUTED})],
               align=PP_ALIGN.CENTER)
    _label_axe_vertical(slide, MARGIN + 0.18, pt + ph / 2, min(ph, 1.5), 0.3,
                        "Valeur (impact) →")

    # Bulles : les scores sont des entiers 1-5, les collisions sont la norme — y
    # compris ENTRE scores voisins (constat pptx-verify : deux bulles de scores
    # adjacents se chevauchaient, la 2e masquait le numéro de la 1re). Résolution
    # par LIGNE (même valeur → même y, les lignes sont espacées de ph/5 > d) :
    # balayage gauche→droite qui impose un écart minimal à partir des positions
    # cibles, puis recalage si la ligne déborde à droite.
    d = 0.46  # bulle agrandie + numéro en `small` (lisibilité, 2026-07-22)
    gap = 0.06
    lignes_bulles: dict[int, list] = {}
    for i, axis in enumerate(axes):
        for j, r in enumerate(axis.recommendations):
            c = max(1, min(5, r.complexite or 3))
            v = max(1, min(5, r.valeur or 3))
            lignes_bulles.setdefault(v, []).append((c, f"{i + 1}.{j + 1}", i))
    # Une bande de 0.38 est réservée en tête de CHAQUE moitié verticale (et plus
    # seulement en haut du graphe) : c'est là que sont ancrés les libellés de
    # quadrant, que les bulles de score 5 — puis, depuis le découpage par moitié,
    # celles de score 3 — venaient recouvrir.
    bande = 0.38 / ph
    for v, membres in lignes_bulles.items():
        membres.sort(key=lambda m: (m[0], m[1]))
        by = pb - _fraction_score(v, bande) * ph - d / 2
        xs: list[float] = []
        for c, _num, _ai in membres:
            cible = pl + _fraction_score(c) * pw - d / 2
            xs.append(cible if not xs else max(cible, xs[-1] + d + gap))
        depassement = xs[-1] - (pl + pw - d - 0.02)
        if depassement > 0:  # recale toute la ligne dans la zone
            decales = [x - depassement for x in xs]
            if decales[0] < pl + 0.02:
                # La ligne ne tient pas même décalée : l'ancien clamp `max(pl+0.02, …)`
                # RE-SUPERPOSAIT toutes les bulles écrêtées au bord gauche (defer revue
                # adversariale, ≥9 recos de même valeur). Répartition uniforme bord à
                # bord : écart réduit mais centres tous distincts — numéros lisibles.
                pas = (pw - d - 0.04) / max(1, len(xs) - 1)
                xs = [pl + 0.02 + k * pas for k in range(len(xs))]
            else:
                xs = decales
        # `strict=True` : `xs` est construit à partir de `membres` juste
        # au-dessus, les longueurs sont égales par construction. Si une branche
        # future casse cet invariant, des bulles disparaîtraient sans bruit.
        for x, (_c, num, ai) in zip(xs, membres, strict=True):
            D.add_badge(slide, x, by, d, num, palette[ai % len(palette)],
                        size=D.TYPE["small"], bold=True, radius=0.5)

    # Légende ENCADRÉE (carte) portant les intitulés COMPLETS — demande 2026-07-22 :
    # « réduire la taille du texte afin qu'il apparaisse complètement et à
    # encadrer ». Taille `tiny` partout, repli sur 2 lignes max par item (mesuré,
    # jamais tronqué à 1 ligne comme avant), hauteur de chaque item MESURÉE.
    # Une ligne par RECO uniquement — pas d'intitulés d'axes (ils vivent en
    # toutes lettres sur la vue d'ensemble ; ici la pastille couleur suffit à
    # porter l'axe, comme les bulles) : c'est ce qui permet aux 8 titres de reco
    # COMPLETS de tenir (4 titres d'axes en plus faisaient sauter l'axe 4).
    # -0.60 (pas -0.50) : le chrome n° de page du master OCTO démarre à y≈5.09 /
    # x≈9.25 — à -0.50 le coin bas-droit de la carte (blanc + bordure) peignait
    # par-dessus (revue adversariale, mesuré sur le master ; même garde que la
    # fiche reco).
    leg_bottom = h_in - 0.60
    lpad = 0.12
    tx = lx + lpad
    tw = lw - 2 * lpad
    # Shrink-to-fit — JAMAIS droper une reco (à taille fixe, l'estimation
    # pessimiste s'accumulait sur 8 items et « 4.2 » sautait alors qu'il
    # restait de la place réelle) : on cherche la plus grande taille <= tiny
    # qui fait tenir TOUTES les recos à l'estimation PESSIMISTE (celle du
    # vérificateur — à l'estimation nominale, un item limite wrappait hors
    # boîte). Cascade complète (revue adversariale : l'ancien `while t_leg > 7.5`
    # sortait SANS avoir évalué 7.5, et le garde-fou du rendu dropait alors des
    # recos sur titres extrêmes) : tailles 9→7.5 à 2 lignes/item, puis dernier
    # cran 7.5 pt à 1 ligne/item (titre tronqué à l'ellipse — un titre coupé
    # vaut toujours mieux qu'une reco absente).
    dispo = (leg_bottom - lpad) - (pt + lpad)
    t_leg = D.TYPE["tiny"]
    lignes_leg = 2
    while True:
        lh_leg = _per_line_height_in(t_leg)
        besoin = 0.0
        for i, axis in enumerate(axes):
            for j, r in enumerate(axis.recommendations):
                item = D.tronquer_a_lignes(f"{i + 1}.{j + 1}  {r.title}", tw - 0.24, t_leg, lignes_leg)
                besoin += D.estimer_lignes(item, tw - 0.24, t_leg, cpi_ref=D.CPI_PESSIMISTE) * lh_leg + 0.03
            besoin += 0.02
        if besoin <= dispo:
            break
        if t_leg > 7.5:
            t_leg -= 0.5
        elif lignes_leg == 2:
            lignes_leg = 1
        elif t_leg > 6.5:
            t_leg -= 0.5  # dernier étage : 1 ligne, 7.5→6.5 (loge ~16 recos)
        else:
            break  # plafond structurel ~18 recos — au-delà le garde-fou du rendu coupe
    # Carte dimensionnée AU CONTENU (2026-07-28) : elle occupait toute la hauteur du
    # graphe quel que soit le nombre de recos, si bien qu'un deck à 3 recommandations
    # affichait trois lignes en haut d'un grand rectangle vide (défaut « panneau
    # étiré » de la checklist pptx-verify). `besoin` sort de la boucle ci-dessus à la
    # taille retenue ; il est PESSIMISTE, donc la carte ne peut pas être trop courte.
    carte_bas = pt + min(besoin + 2 * lpad, leg_bottom - pt)
    if besoin > 0:  # aucune reco = aucune légende, plutôt qu'un liseré vide
        D.add_card(slide, lx, pt, lw, carte_bas - pt)
    y = pt + lpad
    plein = False  # garde-fou ultime : coupe TOUT le reste (pas d'items suivants
    # rendus après un trou — des numéros manquants au milieu seraient trompeurs)
    for i, axis in enumerate(axes):
        color = palette[i % len(palette)]
        for j, r in enumerate(axis.recommendations):
            item = D.tronquer_a_lignes(f"{i + 1}.{j + 1}  {r.title}", tw - 0.24, t_leg, lignes_leg)
            h_item = D.estimer_lignes(item, tw - 0.24, t_leg, cpi_ref=D.CPI_PESSIMISTE) * lh_leg
            if y + h_item > carte_bas - lpad:  # la carte, pas le bas de la slide
                plein = True
                break
            D.add_rect(slide, tx, y + 0.03, 0.12, 0.12, fill=color, rounded=True, radius=0.5)
            D.add_text(slide, tx + 0.24, y, tw - 0.24, h_item,
                       [(item, {"size": t_leg, "color": D.INK})])
            y += h_item + 0.03
        if plein:
            break
        y += 0.02


def _slide_recommendation(prs: Presentation, axis: object, index: str, reco: object,
                          accent: str | None = None) -> None:
    """Fiche recommandation en ENCARTS ARRONDIS format OCTO (demande 2026-07-22 —
    les sections flottaient sur fond blanc) : colonne gauche (objectif / acteurs /
    jauges / résultats) dans une carte arrondie au liseré couleur d'AXE (identité,
    même palette que la vue d'ensemble et la matrice de priorisation) ; colonne
    droite en deux encarts empilés — PROPOSITION DE VALEUR en encart gris arrondi
    (le « so-what » de la fiche, même composant que l'exec/synthèse) puis PLAN
    D'ACTIONS en carte arrondie. OBJECTIF/ACTEURS gardent la hauteur MESURÉE
    (_add_measured_field) pour s'empiler sans déborder ; l'encart proposition est
    à hauteur FIXE (rythme identique de fiche en fiche, l'espace gris restant est
    intentionnel — même principe que les cellules teintées de la SWOT)."""
    # Titre préféré sur UNE ligne : la ligne gagnée ici est ce qui permet à la
    # carte droite de loger plan + résultats sans slide de suite systématique.
    # Un titre long est rendu en police réduite par _new_slide (jamais tronqué) ;
    # au pire il replie et le contenu descend — la pagination absorbe.
    slide, w_in, h_in, top = _new_slide(prs, f"{index} — {reco.title}", max_title_lines=1)
    accent = accent or (D.theme_colors(prs).get("accent1") or D.PALETTE[0])
    pad = 0.2
    lis = 0.05  # dégagement du liseré de carte
    # Carte gauche resserrée (3.15) au profit de la droite : les jauges 0.65×2 y
    # tiennent, et le plan (souvent UNE longue puce) a besoin de largeur.
    card_l_w = 3.15
    right_x = MARGIN + card_l_w + 0.3
    right_w = w_in - right_x - MARGIN
    # Bandeau RÉSULTATS pleine largeur en bas (encart gris, motif « à retenir »
    # des synthèses) : une longue phrase tient en 2 lignes sur ~8.7in là où elle
    # en demandait 5 dans une colonne — c'est ce qui rend la fiche tenable sans
    # slide de suite systématique (mesuré : colonne droite 1.9in dispo pour 2.4in
    # de besoin, aucune allocation ne pouvait gagner). -0.60 : badge n° de page.
    # Prédicat aligné sur le CONTENU rendu (_bullet_lines filtre les marqueurs
    # de puce vides) : « - » seul réservait 0.72in pour un bandeau « — » vide.
    a_resultats = bool(_bullet_lines(reco.resultats_attendus or ""))
    strip_h = 0.72 if a_resultats else 0.0
    band_h = h_in - top - 0.60 - strip_h - (0.12 if a_resultats else 0.0)
    plan_source = reco.plan_actions
    if a_resultats and band_h < 1.0:
        # Garde template client (defer revue adversariale) : un content_top très
        # bas rendrait les cartes inutilisables sous le bandeau — repli : pas de
        # bandeau, les résultats sont reversés en fin de plan (jamais perdus, la
        # pagination gère). Inatteignable sur le template OCTO (band_h ≈ 3.1).
        a_resultats = False
        strip_h = 0.0
        band_h = h_in - top - 0.60
        plan_source = (reco.plan_actions or "") + (
            "\nRésultats attendus : " + " — ".join(_bullet_lines(reco.resultats_attendus or ""))
        )
    bottom = top + band_h

    # ---- Colonne gauche : carte arrondie, liseré couleur d'axe ----
    D.add_card(slide, MARGIN, top, card_l_w, band_h, accent)
    lx = MARGIN + pad + lis
    lw = card_l_w - 2 * pad - lis
    y = top + pad
    y += _add_measured_field(slide, lx, y, lw, "OBJECTIF", reco.objectif, max_h=1.1)
    y += 0.10
    y += _add_measured_field(slide, lx, y, lw, "ACTEURS", reco.acteurs, max_h=0.5)
    y += 0.10
    # Chips « Valeur N/5 » / « Complexité N/5 » (couleurs sémantiques OK/WARN) au
    # lieu des jauges donut : la carte gauche a perdu ~0.8in au profit du bandeau
    # résultats — les donuts s'y écrasaient (labels hors carte, constat rendu
    # 2026-07-22). Une ligne de chips porte la même information en 0.3in.
    chip_h = 0.32
    chip_w = min(1.30, (lw - 0.15) / 2)
    label_h = 0.26
    # Label + chips posés comme UNE unité : quand la carte raccourcit (titre de
    # slide sur 2-3 lignes depuis le non-tronquage 2026-07-23, ou objectif/acteurs
    # au max), l'ancien clamp peignait les chips PAR-DESSUS le label CRITÈRES
    # (constat rendu réel). Si le bloc entier ne tient plus, le label saute (les
    # chips se suffisent) et les chips se calent au bas de la carte — jamais de
    # chevauchement, jamais de sortie de carte.
    if y + label_h + 0.06 + chip_h <= top + band_h - pad:
        D.add_text(slide, lx, y, lw, label_h, [("CRITÈRES DE PRIORISATION", {"size": D.TYPE["small"], "bold": True, "color": D.MUTED})])
        chips_y = y + label_h + 0.06
    else:
        chips_y = min(y, top + band_h - pad - chip_h)
    D.add_chip(slide, lx, chips_y, chip_w, chip_h,
               f"Valeur {reco.valeur}/5", D.OK, size=D.TYPE["tiny"])
    D.add_chip(slide, lx + chip_w + 0.15, chips_y, chip_w, chip_h,
               f"Complexité {reco.complexite}/5", D.WARN, size=D.TYPE["tiny"])

    # ---- Colonne droite : encart « proposition » + carte « plan + résultats » ----
    # prop_h 1.10 (était 1.35) : la carte droite porte TROIS blocs — au-delà, la
    # zone résultats devenait fictive (~0.1in) et son texte peignait PAR-DESSUS le
    # cadre (le « texte sort du cadre » relevé par l'utilisateur, objectivé par
    # verifier_debordements_texte).
    prop_h = 1.00
    D.add_rect(slide, right_x, top, right_w, prop_h, fill=D.ENCART_BG,
               rounded=True, radius=0.12)
    D.add_rect(slide, right_x, top, 0.06, prop_h, fill=accent, rounded=True, radius=0.5)
    _add_measured_field(
        slide, right_x + pad + lis, top + 0.10, right_w - 2 * pad - lis,
        "PROPOSITION DE VALEUR", reco.proposition_valeur, max_h=prop_h - 0.20,
        bold=True, italic=True,
    )
    plan_top = top + prop_h + 0.15
    plan_h = bottom - plan_top
    D.add_card(slide, right_x, plan_top, right_w, plan_h, accent)
    rcx = right_x + pad + lis
    rcw = right_w - 2 * pad - lis
    r_top = plan_top + pad
    r_bottom = plan_top + plan_h - pad
    D.add_text(slide, rcx, r_top, rcw, 0.26,
               [("PLAN D'ACTIONS", {"size": D.TYPE["small"], "bold": True, "color": D.MUTED})])
    # Le plan a TOUTE la carte (les résultats vivent dans le bandeau bas) —
    # shrink-first, suite en dernier recours seulement.
    plan_overflow = _add_bulleted_text(
        slide, rcx, r_top + 0.26, rcw, r_bottom - (r_top + 0.26),
        plan_source, paginate=True,
    )

    # ---- Bandeau bas pleine largeur : RÉSULTATS ATTENDUS (encart gris) ----
    # Une seule zone MIDDLE (libellé + texte dans la même boîte) : sur ~8.7in de
    # large, 2 lignes en `small` logent ~200 caractères — pas de pagination,
    # troncature à l'ellipse en tout dernier recours (FIELD_SHAPE l'annonce).
    if a_resultats:
        sy = top + band_h + 0.12
        sw = w_in - 2 * MARGIN
        D.add_rect(slide, MARGIN, sy, sw, strip_h, fill=D.ENCART_BG,
                   rounded=True, radius=0.12)
        D.add_rect(slide, MARGIN, sy, 0.06, strip_h, fill=accent, rounded=True, radius=0.5)
        scx = MARGIN + pad + lis
        scw = sw - 2 * pad - lis
        res_txt = " — ".join(_bullet_lines(reco.resultats_attendus)) or "—"
        D.add_text(
            slide, scx, sy, scw, strip_h,
            [("RÉSULTATS ATTENDUS", {"size": D.TYPE["tiny"], "bold": True,
                                     "color": D.MUTED, "space_after": 2}),
             # cpi PESSIMISTE (D.CPI_PESSIMISTE) : à l'estimation nominale un texte limite
             # (~180 car.) repassait à 3 lignes au vrai rendu et sortait du
             # bandeau — hors du champ des vérificateurs (ancre MIDDLE).
             (D.tronquer_a_lignes(res_txt, scw, D.TYPE["small"], 2, cpi_ref=D.CPI_PESSIMISTE),
              {"size": D.TYPE["small"], "color": D.INK})],
            anchor=MSO_ANCHOR.MIDDLE,
        )

    base_title = f"{index} — {reco.title}"
    if plan_overflow:
        _emit_bullet_overflow(prs, base_title, "Plan d'actions", plan_overflow)


# --------------------------------------------------------------------------- #
# Indicateurs de suivi (US9.27 b) — grille de cartes KPI (deck-design-library
# n°3 « cartes stat ») : rang + libellé, CIBLE en encre de marque, chip de l'axe
# suivi (couleur d'IDENTITÉ de l'axe, même palette que la vue d'ensemble et la
# matrice de priorisation). 6 cartes max par slide, pagination au-delà.
# --------------------------------------------------------------------------- #
_KPI_PAR_PAGE = 6
_KPI_GAP = 0.2
_KPI_PAD = 0.16
_KPI_RANG_D = 0.34
_KPI_CPI = D.CPI_PESSIMISTE  # hauteur PESSIMISTE des boîtes (filet verifier_debordements_texte)
# Calibration de MISE EN PAGE (positions, hauteur des cartes/du registre,
# troncature) ; mesure et justification : commentaire de D.CPI_LAYOUT (pptx_deck).
_LAYOUT_CPI = D.CPI_LAYOUT


def _kpi_grille(n: int) -> tuple[int, int]:
    """(colonnes, lignes) pour n cartes sur une page : 1-3 sur une rangée, 4 en
    2×2, 5-6 en 3×2 — gabarit uniforme, jamais une carte orpheline étirée."""
    if n <= 3:
        return max(1, n), 1
    if n == 4:
        return 2, 2
    return 3, 2


def _slide_kpis(prs: Presentation, kpis: list, axes: list, palette: list[str]) -> None:
    """Slide(s) « Indicateurs de suivi ». Aucun KPI à libellé non vide → aucune
    slide (jamais un cadre vide). L'axe suivi est retrouvé PAR INTITULÉ parmi les
    axes de la mission (le KPI le garde en texte, cf. `MissionKpi`) : liseré +
    pastille à la couleur d'IDENTITÉ de l'axe ; un axe inconnu est écrit sans
    liseré ni pastille (un gris ne doit pas se lire comme un axe). Rang en navy.

    Alignement : dans une rangée, les blocs (libellé, CIBLE, axe) démarrent à la
    MÊME ordonnée d'une carte à l'autre — hauteurs communes à la rangée."""
    titres_axes = {(a.title or "").strip().casefold(): i for i, a in enumerate(axes)}
    kpis = [k for k in kpis if (getattr(k, "libelle", "") or "").strip()]
    if not kpis:
        return
    pages = [kpis[i:i + _KPI_PAR_PAGE] for i in range(0, len(kpis), _KPI_PAR_PAGE)]
    title = "Indicateurs de suivi"
    navy = "#0E2356"
    s_lib = s_c = D.TYPE["small"]
    s_axe = D.TYPE["tiny"]
    lh_lib, lh_c, lh_axe = (_per_line_height_in(s) for s in (s_lib, s_c, s_axe))
    lab_h = 0.18
    cpi = _KPI_CPI
    rang = 0
    for k_page, page in enumerate(pages):
        suffix = f" ({k_page + 1}/{len(pages)})" if len(pages) > 1 else ""
        slide, w_in, h_in, top = _new_slide(prs, title + suffix)
        cols, rows = _kpi_grille(len(page))
        band_h = h_in - top - 0.60  # -0.60 : badge n° de page du master OCTO
        card_w = (w_in - 2 * MARGIN - (cols - 1) * _KPI_GAP) / cols
        iw = card_w - 2 * _KPI_PAD - 0.07
        lw = iw - _KPI_RANG_D - 0.1
        aw = iw - 0.18  # texte d'axe à droite de sa pastille
        max_lib = 3 if rows == 1 else 2
        max_c = 4
        max_axe = 2 if rows == 1 else 1  # grille 2 rangées : la place va à la cible

        def plan(k, lw=lw, aw=aw, iw=iw, max_lib=max_lib, max_c=max_c, max_axe=max_axe):
            lib = D.tronquer_a_lignes(k.libelle.strip(), lw, s_lib, max_lib, cpi_ref=_LAYOUT_CPI)
            cible = (getattr(k, "cible", "") or "").strip()
            axe = (getattr(k, "axe", "") or "").strip()
            # Axe tronqué à l'estimation PESSIMISTE : sa boîte est ancrée TOP en pied
            # de carte, elle doit tenir sans marge (pas de boîte plus haute possible).
            axe_t = D.tronquer_a_lignes(axe, aw, s_axe, max_axe, cpi_ref=cpi) if axe else ""
            return dict(
                k=k, lib=lib, cible=cible, axe=axe, axe_t=axe_t,
                n_lib=min(max_lib, max(1, D.estimer_lignes(lib, lw, s_lib, cpi_ref=_LAYOUT_CPI))),
                # boîte à la hauteur PESSIMISTE (non plafonnée) : transparente, elle
                # peut chevaucher le bloc CIBLE sans rien masquer.
                b_lib=max(1, D.estimer_lignes(lib, lw, s_lib, cpi_ref=cpi)),
                n_c=min(max_c, max(1, D.estimer_lignes(cible, iw, s_c, cpi_ref=_LAYOUT_CPI))) if cible else 0,
                n_axe=min(max_axe, max(1, D.estimer_lignes(axe_t, aw, s_axe, cpi_ref=cpi))) if axe else 0,
            )

        plans = [plan(k) for k in page]
        rangees = [plans[r * cols:(r + 1) * cols] for r in range(rows)]
        geo = []
        for rp in rangees:
            lib_h = max(_KPI_RANG_D, max(p["n_lib"] for p in rp) * lh_lib)
            n_c = max(p["n_c"] for p in rp)
            axe_h = max(p["n_axe"] for p in rp) * lh_axe
            geo.append([lib_h, n_c, axe_h])

        def hauteur(g) -> float:
            lib_h, n_c, axe_h = g
            return (2 * _KPI_PAD + lib_h + (0.10 + lab_h + n_c * lh_c if n_c else 0.0)
                    + (0.12 + axe_h if axe_h else 0.0))

        if rows == 1:
            # Carte DIMENSIONNÉE AU CONTENU (pas un panneau étiré, constat rendu réel).
            card_h = min(band_h, max(1.1, hauteur(geo[0])))
        else:
            card_h = (band_h - (rows - 1) * _KPI_GAP) / rows
            for g in geo:  # la cible prend ce que la carte loge, au moins 1 ligne
                while g[1] > 1 and hauteur(g) > card_h:
                    g[1] -= 1
        y0 = top + max(0.0, (band_h - (rows * card_h + (rows - 1) * _KPI_GAP)) / 2)
        for r_i, rp in enumerate(rangees):
            lib_h, n_c_row, axe_h = geo[r_i]
            for c_i, p in enumerate(rp):
                rang += 1
                x = MARGIN + c_i * (card_w + _KPI_GAP)
                y = y0 + r_i * (card_h + _KPI_GAP)
                ai = titres_axes.get(p["axe"].casefold()) if p["axe"] else None
                accent = palette[ai % len(palette)] if ai is not None else None
                D.add_card(slide, x, y, card_w, card_h, accent)
                ix = x + _KPI_PAD + 0.07
                iy = y + _KPI_PAD
                D.add_badge(slide, ix, iy, _KPI_RANG_D, str(rang), navy,
                            size=D.TYPE["small"], radius=0.5)
                D.add_text(slide, ix + _KPI_RANG_D + 0.1, iy + 0.04, lw,
                           max(lib_h, p["b_lib"] * lh_lib),
                           [(p["lib"], {"size": s_lib, "bold": True, "color": D.INK})])
                y_txt = iy + lib_h + 0.10
                if p["cible"] and n_c_row:
                    D.add_text(slide, ix, y_txt, iw, lab_h,
                               [("CIBLE / MESURE", {"size": D.TYPE["tiny"], "bold": True,
                                                    "color": D.MUTED})])
                    txt = D.tronquer_a_lignes(p["cible"], iw, s_c, n_c_row, cpi_ref=_LAYOUT_CPI)
                    b_c = max(1, D.estimer_lignes(txt, iw, s_c, cpi_ref=cpi))
                    D.add_text(slide, ix, y_txt + lab_h, iw, b_c * lh_c,
                               [(txt, {"size": s_c, "bold": True, "color": navy})])
                if p["axe"]:
                    ay = y + card_h - _KPI_PAD - axe_h
                    # Texte ancré TOP : la 1re ligne commence à `ay` quel que soit le
                    # nombre de lignes — la pastille se centre sur CETTE ligne (revue
                    # adversariale : en MIDDLE, elle flottait au-dessus d'un libellé
                    # sur 2 lignes). Hauteur de glyphe ≈ corps × 1.2 / 72.
                    if accent:
                        dot = 0.1
                        D.add_dot(slide, ix, ay + (s_axe * 1.2 / 72 - dot) / 2, dot, accent)
                    D.add_text(slide, ix + 0.18, ay, aw, axe_h,
                               [(p["axe_t"], {"size": s_axe, "color": D.MUTED})])


# --------------------------------------------------------------------------- #
# Matrice risques-contrôles (US9.27 c) — DESSINÉE (même doctrine que la matrice
# de priorisation, skill priority-matrix) : grille 3×3 gravité × probabilité
# teintée par CRITICITÉ (couleurs SÉMANTIQUES OK/GOLD/WARN, jamais celles des
# axes), un repère « R n » par risque dans sa cellule, et un registre encadré à
# droite (risque + contrôle existant / mesure proposée). Registre paginé : chaque
# page redessine la matrice avec SES risques — aucun risque perdu, jamais de
# registre qui déborde.
# --------------------------------------------------------------------------- #
_NIVEAUX = ("Faible", "Moyenne", "Élevée")


def _couleur_criticite(g: int, p: int) -> str:
    score = criticite_risque(g, p)  # définition unique (models), cf. MissionRisk.criticite
    if score >= 6:
        return D.WARN
    if score >= 3:
        return D.GOLD
    return D.OK


_niveau = niveau_risque  # même bornage que MissionRisk.criticite


def _couleur_texte_criticite(g: int, p: int) -> str:
    """Variante TEXTE de la couleur de criticité : l'ambre GOLD (≈ 3.3:1 sur blanc)
    passe pour une pastille (3:1) mais pas pour du texte (4.5:1, WCAG) — foncé ici."""
    col = _couleur_criticite(g, p)
    return "#8a6508" if col == D.GOLD else col


_RISK_SPACE_PT = 5  # espace avant chaque entrée du registre (sauf la 1re)


def _risk_entry(num: int, r, tw: float, size: float) -> tuple[str, str, str]:
    """(préfixe « Rn », risque, contrôle) tronqués à 2 lignes chacun — le préfixe
    fait partie de la 1re ligne, il est donc compté dans la troncature."""
    prefixe = f"R{num}   "
    corps = (r.risque or "").strip()
    # Le préfixe occupe sa place dans la 1re ligne mais n'est PAS tronquable : on
    # tronque un gabarit de même longueur (« x » insécables) + le corps, puis on
    # remet le préfixe — la coupe ne tombe que dans le corps (revue adversariale :
    # « R10   » ne doit jamais laisser un corps réduit à « … »). Garde DÉFENSIVE,
    # arbitrée le 2026-09-25 : avec le plancher actuel de tronquer_a_lignes
    # (≥ 11 car. sur 2 lignes) la version naïve ne vide jamais le corps (16 200
    # cas cherchés, 0 échec), donc aucun test rouge possible — elle protège d'un
    # futur abaissement de ce plancher.
    gabarit = "x" * len(prefixe)
    tronque = D.tronquer_a_lignes(gabarit + corps, tw, size, 2, cpi_ref=_LAYOUT_CPI)
    if not tronque.startswith(gabarit) or not tronque[len(gabarit):].strip(" …"):
        tronque = gabarit + D.tronquer_a_lignes(corps, tw, size, 1, cpi_ref=_LAYOUT_CPI)
    tete = prefixe + tronque[len(gabarit):]
    ctrl = (getattr(r, "controle", "") or "").strip()
    if ctrl:
        lib = ("Contrôle existant : " if getattr(r, "controle_type", "") == "existant"
               else "Mesure proposée : ")
        ctrl = D.tronquer_a_lignes(lib + ctrl, tw, size, 2, cpi_ref=_LAYOUT_CPI)
    return prefixe, tete[len(prefixe):], ctrl


def _risk_entry_h(num: int, r, tw: float, size: float, premier: bool) -> float:
    """Hauteur de MISE EN PAGE d'une entrée (`_LAYOUT_CPI`, mesurée au rendu réel),
    + l'espace avant l'entrée. Chaque bloc est tronqué à 2 lignes, même calibration."""
    prefixe, risque, ctrl = _risk_entry(num, r, tw, size)
    lh = _per_line_height_in(size)
    h = D.estimer_lignes(prefixe + risque, tw, size, cpi_ref=_LAYOUT_CPI) * lh
    if ctrl:
        h += D.estimer_lignes(ctrl, tw, size, cpi_ref=_LAYOUT_CPI) * lh
    return h + (0.0 if premier else _RISK_SPACE_PT / 72)


def _registre_risques(slide, x: float, y: float, w: float, h: float,
                      page: list, size: float) -> None:
    """Registre en UNE zone de texte qui s'écoule (pas une boîte par entrée) : les
    hauteurs estimées, pessimistes par contrat, ne créent plus de trous entre les
    entrées (constat rendu réel). « Rn » est un run gras en couleur de criticité
    en tête du paragraphe du risque."""
    lignes = []
    meta = []
    for k, (num, r) in enumerate(page):
        prefixe, risque, ctrl = _risk_entry(num, r, w, size)
        opts = {"size": size, "bold": True, "color": D.INK}
        if k:
            opts["space_before"] = _RISK_SPACE_PT
        lignes.append((risque, opts))
        meta.append((len(lignes) - 1, prefixe, _couleur_texte_criticite(
            _niveau(r.gravite), _niveau(r.probabilite))))
        if ctrl:
            lignes.append((ctrl, {"size": size, "color": D.MUTED}))
    # Ancrage MIDDLE dans une boîte à la hauteur de MISE EN PAGE du contenu (cf.
    # _risk_entry_h) : la boîte colle au texte, et le filet pessimiste de
    # verifier_debordements_texte ne s'applique pas — le contenu est borné par la
    # troncature en amont, comme les autres blocs MIDDLE du deck.
    box = D.add_text(slide, x, y, w, h, lignes, anchor=MSO_ANCHOR.MIDDLE)
    paras = box.text_frame.paragraphs
    for idx, prefixe, couleur in meta:
        run0 = paras[idx].runs[0]
        new_r = copy.deepcopy(run0._r)
        run0._r.addprevious(new_r)
        pre = paras[idx].runs[0]
        pre.text = prefixe
        pre.font.color.rgb = D.rgb(couleur)


def _slide_matrice_risques(prs: Presentation, risks: list) -> None:
    """Slide(s) « Matrice des risques et contrôles ». Aucun risque à libellé non
    vide → aucune slide. Numérotation R1..Rn = ordre de la liste (continue d'une
    page à l'autre)."""
    risks = [r for r in risks if (getattr(r, "risque", "") or "").strip()]
    if not risks:
        return
    numerotes = list(enumerate(risks, 1))
    title = "Matrice des risques et contrôles"
    w_in, h_in = _dims(prs)
    size = D.TYPE["tiny"]
    lpad = 0.14
    # pl + pw = MARGIN + 4.45 : bord gauche du registre, repris par FIELD_SHAPE.
    pl = MARGIN + 0.95
    pw = 3.5
    lx = pl + pw + 0.3
    lw = w_in - MARGIN - lx
    tw = lw - 2 * lpad

    def paginer(capacite: float, items: list) -> list[list]:
        pages, cur, h = [], [], 0.0
        for it in items:
            hi = _risk_entry_h(it[0], it[1], tw, size, premier=not cur)
            if cur and h + hi > capacite:
                pages.append(cur)
                cur, h = [], 0.0
                hi = _risk_entry_h(it[0], it[1], tw, size, premier=True)
            cur.append(it)
            h += hi
        if cur:
            pages.append(cur)
        return pages

    # Capacité estimée sur un titre à une ligne (content_top ≈ 1.25 sur OCTO) ; le
    # rendu de chaque page recoupe sur SON content_top réel et reporte le surplus.
    pages = paginer((h_in - 0.60) - (1.25 + 0.05) - 2 * lpad, numerotes)
    reste: list = []
    k = 0
    while pages or reste:
        page = reste + (pages.pop(0) if pages else [])
        reste = []
        k += 1
        # Total annoncé = pages planifiées ; il ne grossit que si un gabarit client
        # au titre plus bas fait reporter des risques (filet `reste` ci-dessous).
        n_pages = k + len(pages)
        suffix = f" ({k}/{n_pages})" if n_pages > 1 else ""
        slide, w_in, h_in, top = _new_slide(prs, title + suffix)
        pt = top + 0.05
        pb = h_in - 0.60 - 0.50  # ticks + libellé d'axe X sous la grille
        ph = pb - pt
        leg_bottom = h_in - 0.60
        coupe = paginer(leg_bottom - pt - 2 * lpad, page)
        page, reste = coupe[0], [it for p in coupe[1:] for it in p]
        cw, ch = pw / 3, ph / 3
        # Cellules teintées par criticité (rangée du haut = gravité élevée).
        for gi in range(3):
            g = 3 - gi
            for p in range(1, 4):
                col = _couleur_criticite(g, p)
                D.add_rect(slide, pl + (p - 1) * cw, pt + gi * ch, cw, ch,
                           fill=D.melanger_blanc(col, 0.86), line="#ffffff", line_w=1.5)
            D.add_text(slide, pl - 0.62, pt + gi * ch, 0.58, ch,
                       [(_NIVEAUX[g - 1], {"size": size, "color": D.MUTED})],
                       anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.RIGHT)
        for p in range(1, 4):
            D.add_text(slide, pl + (p - 1) * cw, pb + 0.04, cw, 0.2,
                       [(_NIVEAUX[p - 1], {"size": size, "color": D.MUTED})],
                       align=PP_ALIGN.CENTER)
        D.add_text(slide, pl, pb + 0.24, pw, 0.24,
                   [("Probabilité →", {"size": D.TYPE["small"], "bold": True,
                                       "color": D.MUTED})],
                   align=PP_ALIGN.CENTER)
        # Même ancrage que l'axe Valeur de la matrice de priorisation (MARGIN + 0.18,
        # longueur ≤ 1.3) : la boîte NON rotée reste dans la slide (verifier_geometrie).
        _label_axe_vertical(slide, MARGIN + 0.18, pt + ph / 2, min(ph, 1.3), 0.26,
                            "Gravité →")
        # Repères par cellule, en grille dans la cellule (diamètre réduit si la
        # cellule est chargée) — jamais superposés.
        par_cellule: dict[tuple[int, int], list] = {}
        for num, r in page:
            par_cellule.setdefault((_niveau(r.gravite), _niveau(r.probabilite)), []).append(num)
        for (g, p), nums in par_cellule.items():
            cx0 = pl + (p - 1) * cw
            cy0 = pt + (3 - g) * ch
            d = 0.40
            while d > 0.24:
                per_row = max(1, int((cw - 0.08) // (d + 0.05)))
                n_rows = -(-len(nums) // per_row)
                if n_rows * (d + 0.05) <= ch - 0.08:
                    break
                d -= 0.04
            per_row = max(1, int((cw - 0.08) // (d + 0.05)))
            n_rows = -(-len(nums) // per_row)
            bloc_h = n_rows * d + (n_rows - 1) * 0.05
            by0 = cy0 + max(0.04, (ch - bloc_h) / 2)
            for idx, num in enumerate(nums):
                rr, cc = idx // per_row, idx % per_row
                n_this = min(per_row, len(nums) - rr * per_row)
                row_w = n_this * d + (n_this - 1) * 0.05
                bx = cx0 + (cw - row_w) / 2 + cc * (d + 0.05)
                by = by0 + rr * (d + 0.05)
                D.add_badge(slide, bx, by, d, f"R{num}", _couleur_criticite(g, p),
                            size=D.TYPE["tiny"] if d >= 0.32 else 7, radius=0.5)
        # Registre encadré, dimensionné au contenu (calibration de mise en page).
        besoin = sum(_risk_entry_h(n, r, tw, size, premier=(i == 0))
                     for i, (n, r) in enumerate(page))
        # Bas du registre calé AU MOINS sur le bas de la grille : deux colonnes de
        # même hauteur (le registre centré dedans) se lisent comme une composition,
        # alors qu'une carte « au contenu » gardait une marge morte aléatoire — les
        # estimations de lignes restent larges au vrai rendu (constaté sur PNG).
        carte_bas = min(max(pt + besoin + 2 * lpad, pb), leg_bottom)
        D.add_card(slide, lx, pt, lw, carte_bas - pt)
        _registre_risques(slide, lx + lpad, pt + lpad, tw, carte_bas - pt - 2 * lpad,
                          page, size)
