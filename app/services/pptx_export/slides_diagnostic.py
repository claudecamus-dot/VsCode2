"""Slides du diagnostic et de la parole des équipes : synthèse globale par
catégorie, executive summary, matrice SWOT, difficultés, verbatims.
Extrait de pptx_export.py (découpage du gros module, finding audit
2026-07-24) — code déplacé tel quel."""
from __future__ import annotations

from pptx import Presentation
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

from .. import pptx_deck as D
from .base import (
    _SYNTH_VIS_W,
    MARGIN,
    _add_bulleted_text,
    _add_measured_field,
    _bullet_lines,
    _label_axe_vertical,
    _new_slide,
    _per_line_height_in,
)
from .images import _FRAMED_OK, _image_dans_zone

# Enrichissement synthèse (ask design 2026-07-22) : pattern claim + visuel + encart
# des decks OCTO réels (VSCode4). Scène/requête photo par catégorie (repli procédural
# offline, comme les têtes de chapitre) — clé = libellé exact passé par build_presentation.
# Scènes NATURE (comme les têtes de chapitre) : rendu procédural fiable hors ligne
# ET vraie photo Openverse en prod — cohérent avec l'imagerie de marque du deck.
# (scène, requête photo, seed distinct pour varier des intercalaires).
_SYNTHESE_VISUEL = {
    # « photography » dans la requête : Openverse mélange photos et illustrations —
    # sans ce biais, une requête générique peut renvoyer un clipart (constat
    # pptx-verify 2026-07-22 : « mountains landscape » → illustration Fuji).
    "Contexte": ("mountains", "mountain landscape photography", 11),
    "Culture & ADN": ("forest", "forest sunlight nature photography", 12),
    "Forces & succès": ("sunset", "sunset sky photography", 13),
    "Points d'amélioration": ("ocean", "ocean waves photography", 14),
    "Aspirations (baguette magique)": ("sunset", "sunrise horizon photography", 15),
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
        # — cohérence de composant §5, sobriété §3/§7, motif VSCode4). Shrink-to-fit
        # AVANT troncature (batterie design 2026-07-22 : à h3 fixe, le claim était
        # coupé en plein mot sur les 5 synthèses — un « so-what » tronqué ne dit
        # plus rien) : h3 → body → small, ellipse en tout dernier recours.
        t_enc, l_enc = next(
            ((t, lm) for t, lm in ((D.TYPE["h3"], 2), (D.TYPE["body"], 2),
                                   (D.TYPE["small"], 2), (D.TYPE["small"], 3))
             if D.estimer_lignes(retenir, area_w - 0.6, t) <= lm),
            (D.TYPE["small"], 3),
        )
        msg = D.tronquer_a_lignes(retenir, area_w - 0.6, t_enc, l_enc)
        D.add_encart(slide, area_l, band_t, area_w, band_h, msg, accent=accent, size=t_enc)


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


# Couleurs des cartes de points clés de l'exec summary (format VSCode3 :
# Doctrine bleu / Méthode vert / Maturité ambre / Posture rouge).
_EXEC_CARD_COLORS = ["#2c5cc5", "#1e6b34", "#b8860b", "#b3261e"]


def _slide_executive_summary(prs: Presentation, es) -> None:
    """Slide d'ouverture « Executive Summary » (piste F restitution, 2026-07-21) :
    un panneau constat + points clés, et une bande cyan « key message » (le
    so-what) en bas — pattern relevé sur les vraies restitutions OCTO (Executive
    Summary + bande de message à retenir), cf.
    docs/reflexions/restitution-mission.md §F. Placée juste après le sommaire."""
    slide, w_in, h_in, top = _new_slide(prs, "Executive Summary")
    area_l = MARGIN + 0.3
    area_w = w_in - 2 * (MARGIN + 0.3)
    headline = (getattr(es, "headline", "") or "").strip()
    key_message = (getattr(es, "key_message", "") or "").strip()
    points = _bullet_lines(getattr(es, "points", "") or "")

    # Le GROUPE (claim + sous-claim + cartes) est CENTRÉ verticalement dans la
    # bande — claim en haut + cartes plaquées en bas laissaient un grand vide au
    # milieu (constat utilisateur 2026-07-22 « contenu mieux centré »). On mesure
    # donc chaque bloc AVANT de dessiner.
    hl = km = ""
    hl_h = km_h = cards_h = 0.0
    gap_claim = 0.14
    gap_cards = 0.4
    if headline:
        hl = D.tronquer_a_lignes(headline, area_w, D.TYPE["h2"], 2)
        hl_h = D.estimer_lignes(hl, area_w, D.TYPE["h2"]) * _per_line_height_in(D.TYPE["h2"])
    if key_message:
        km = D.tronquer_a_lignes(key_message, area_w, D.TYPE["body"], 2)
        km_h = D.estimer_lignes(km, area_w, D.TYPE["body"]) * _per_line_height_in(D.TYPE["body"])
    n = min(len(points), 4)
    gap = 0.2
    cpad = 0.18
    col_w = (area_w - gap * (n - 1)) / n if n else area_w
    if points:
        lh = _per_line_height_in(D.TYPE["small"])
        max_lines = max(2, max(D.estimer_lignes(pt, col_w - 2 * cpad, D.TYPE["small"])
                               for pt in points[:n]))
        cards_h = min(1.6, 2 * cpad + max_lines * lh + 0.1)

    band = (h_in - 0.5) - top
    total = hl_h + (gap_claim if hl and km else 0.0) + km_h + (gap_cards if points else 0.0) + cards_h
    y = top + max(0.0, (band - total) / 2)

    # Claim (headline) — navy bold, pleine largeur (format VSCode3).
    if hl:
        D.add_text(slide, area_l, y, area_w, hl_h,
                   [(hl, {"size": D.TYPE["h2"], "bold": True, "color": D.INK})])
        y += hl_h + gap_claim
    # Sous-claim (key_message) — italique gris : le « so-what ».
    if km:
        D.add_text(slide, area_l, y, area_w, km_h,
                   [(km, {"size": D.TYPE["body"], "italic": True, "color": D.MUTED})])
        y += km_h
    y += gap_cards

    # Points clés en CARTES COULEUR — signature VSCode3. Carte blanche + liseré
    # couleur, texte centré, tronqué à ce qui tient (jamais de débordement).
    if points:
        for i, pt in enumerate(points[:n]):
            cx = area_l + i * (col_w + gap)
            color = _EXEC_CARD_COLORS[i % len(_EXEC_CARD_COLORS)]
            D.add_card(slide, cx, y, col_w, cards_h, color)
            D.add_text(
                slide, cx + cpad, y + cpad, col_w - 2 * cpad, cards_h - 2 * cpad,
                [(D.tronquer_a_lignes(pt, col_w - 2 * cpad, D.TYPE["small"], max_lines),
                  {"size": D.TYPE["small"], "color": D.INK})],
                anchor=MSO_ANCHOR.MIDDLE,
            )


def _slide_swot(prs: Presentation, swot) -> None:
    """Matrice SWOT 2×2 — cf. skill `swot-matrix`. Ce n'est PAS quatre cartes
    posées côte à côte : c'est une matrice dont les deux axes sont explicites —
    lignes INTERNE (Forces/Faiblesses) / EXTERNE (Opportunités/Menaces) dans la
    gouttière gauche (labels rotés), colonnes FAVORABLE (Forces/Opportunités) /
    DÉFAVORABLE (Faiblesses/Menaces) au-dessus. Chaque quadrant est une CELLULE
    TEINTÉE (fond = melanger_blanc de sa couleur) : le fond rempli rend le vide
    sous les puces intentionnel, au lieu de la carte blanche sur-étirée que
    pptx-verify signalait. Grille figée par les axes : Forces (h-g), Faiblesses
    (h-d), Opportunités (b-g), Menaces (b-d)."""
    slide, w_in, h_in, top = _new_slide(prs, "Matrice SWOT")
    gutter = 0.30   # gouttière gauche : labels de ligne INTERNE/EXTERNE (rotés)
    axis_h = 0.30   # bandeau haut : labels de colonne FAVORABLE/DÉFAVORABLE
    gap = 0.22
    pad = 0.16
    area_l = MARGIN + gutter
    area_w = w_in - MARGIN - area_l
    area_t = top + axis_h
    area_h = h_in - area_t - 0.45
    col_w = (area_w - gap) / 2
    row_h = (area_h - gap) / 2
    cells = [(0, 0), (1, 0), (0, 1), (1, 1)]
    title_h = 0.40

    # Axe horizontal (effet sur l'objectif) : FAVORABLE (vert) / DÉFAVORABLE (rouge).
    for ci, (lbl, col) in enumerate((("FAVORABLE", D.OK), ("DÉFAVORABLE", D.WARN))):
        D.add_text(
            slide, area_l + ci * (col_w + gap), top, col_w, axis_h,
            [(lbl, {"size": D.TYPE["tiny"], "bold": True, "color": col})],
            anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER,
        )
    # Axe vertical (origine) : INTERNE / EXTERNE (neutre — l'origine n'est pas +/-).
    # `longueur` bornée (< 2×cx) pour que le cadre NON roté du label — celui que
    # verifier_geometrie contrôle — reste dans la slide ; le label roté visuel, lui,
    # tient dans la gouttière quoi qu'il arrive.
    for ri, lbl in enumerate(("INTERNE", "EXTERNE")):
        cy = area_t + ri * (row_h + gap) + row_h / 2
        _label_axe_vertical(slide, MARGIN + gutter / 2, cy, min(row_h, 1.3), gutter, lbl)

    for (key, label, color), (col, row) in zip(_SWOT_QUADRANTS, cells):
        cl = area_l + col * (col_w + gap)
        ct = area_t + row * (row_h + gap)
        # Cellule teintée + liseré coloré (style de carte du deck). Le fond rempli
        # supprime l'effet « carte blanche vide » sous des puces courtes.
        D.add_rect(slide, cl, ct, col_w, row_h,
                   fill=D.melanger_blanc(color, 0.90),
                   line=D.melanger_blanc(color, 0.55), line_w=1.0,
                   rounded=True, radius=0.05)
        D.add_rect(slide, cl, ct, 0.06, row_h, fill=color, rounded=True, radius=0.5)
        # En-tête : badge icône + titre coloré du quadrant.
        badge_d = 0.30
        hy = ct + pad
        D.add_badge(slide, cl + pad + 0.04, hy, badge_d, _SWOT_ICONS[key],
                    color, size=D.TYPE["small"], bold=False, radius=0.28)
        D.add_text(
            slide, cl + pad + 0.04 + badge_d + 0.12, hy,
            col_w - 2 * pad - badge_d - 0.16, title_h,
            [(label, {"size": D.TYPE["h3"], "bold": True, "color": color})],
            anchor=MSO_ANCHOR.MIDDLE,
        )
        # paginate=True : un quadrant trop long est TRONQUÉ à la cellule plutôt que
        # de déborder sur le voisin. max(0.0, …) : jamais négatif.
        _add_bulleted_text(
            slide, cl + pad + 0.04, ct + pad + title_h + 0.04, col_w - 2 * pad - 0.04,
            max(0.0, row_h - (pad + title_h + 0.04) - pad),
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
