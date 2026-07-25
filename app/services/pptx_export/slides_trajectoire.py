"""Slides de la trajectoire proposée : vue d'ensemble des axes, matrice de
priorisation valeur/effort dessinée, fiches de recommandation en encarts.
Extrait de pptx_export.py (découpage du gros module, finding audit
2026-07-24) — code déplacé tel quel."""
from __future__ import annotations

from pptx import Presentation
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

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
    # Les bulles vivent SOUS la bande des libellés de quadrant (0.38 réservé en
    # haut) : à valeur=5 elles venaient sinon recouvrir le libellé (constat rendu).
    ph_bulles = ph - 0.38
    for v, membres in lignes_bulles.items():
        membres.sort(key=lambda m: (m[0], m[1]))
        by = pb - (v - 0.5) / 5 * ph_bulles - d / 2
        xs: list[float] = []
        for c, _num, _ai in membres:
            cible = pl + (c - 0.5) / 5 * pw - d / 2
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
        for x, (c, num, ai) in zip(xs, membres):
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
    D.add_card(slide, lx, pt, lw, leg_bottom - pt)
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
                besoin += D.estimer_lignes(item, tw - 0.24, t_leg, cpi_ref=10.7) * lh_leg + 0.03
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
    y = pt + lpad
    plein = False  # garde-fou ultime : coupe TOUT le reste (pas d'items suivants
    # rendus après un trou — des numéros manquants au milieu seraient trompeurs)
    for i, axis in enumerate(axes):
        color = palette[i % len(palette)]
        for j, r in enumerate(axis.recommendations):
            item = D.tronquer_a_lignes(f"{i + 1}.{j + 1}  {r.title}", tw - 0.24, t_leg, lignes_leg)
            h_item = D.estimer_lignes(item, tw - 0.24, t_leg, cpi_ref=10.7) * lh_leg
            if y + h_item > leg_bottom - lpad:
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
             # cpi PESSIMISTE (10.7) : à l'estimation nominale un texte limite
             # (~180 car.) repassait à 3 lignes au vrai rendu et sortait du
             # bandeau — hors du champ des vérificateurs (ancre MIDDLE).
             (D.tronquer_a_lignes(res_txt, scw, D.TYPE["small"], 2, cpi_ref=10.7),
              {"size": D.TYPE["small"], "color": D.INK})],
            anchor=MSO_ANCHOR.MIDDLE,
        )

    base_title = f"{index} — {reco.title}"
    if plan_overflow:
        _emit_bullet_overflow(prs, base_title, "Plan d'actions", plan_overflow)
