"""Slides du diagnostic et de la parole des équipes : synthèse globale par
catégorie, executive summary, matrice SWOT, difficultés, verbatims.
Extrait de pptx_export.py (découpage du gros module, finding audit
2026-07-24) — code déplacé tel quel."""
from __future__ import annotations

import hashlib
import re

from pptx import Presentation
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

from ...models import MATURITE_NIVEAUX, score_maturite
from .. import pptx_deck as D
from .base import (
    _SYNTH_VIS_W,
    MARGIN,
    _add_bulleted_text,
    _add_measured_field,
    _bullet_lines,
    _dims,
    _label_axe_vertical,
    _new_slide,
    _per_line_height_in,
)
from .images import _FRAMED_OK, _image_dans_zone

# Enrichissement synthèse (ask design 2026-07-22) : pattern claim + visuel + encart
# des decks OCTO réels (VSCode4). Scène/requête photo par axe d'étude (repli procédural
# offline, comme les têtes de chapitre) — clé = la `key` de l'axe, JAMAIS son libellé :
# depuis que les axes sont configurables (2026-07-27), le libellé est renommable et
# seule la key est stable (même invariant que le stockage du contenu).
# Scènes NATURE (comme les têtes de chapitre) : rendu procédural fiable hors ligne
# ET vraie photo Openverse en prod — cohérent avec l'imagerie de marque du deck.
# (scène, requête photo, seed distinct pour varier des intercalaires).
_SYNTHESE_VISUEL = {
    # « photography » dans la requête : Openverse mélange photos et illustrations —
    # sans ce biais, une requête générique peut renvoyer un clipart (constat
    # pptx-verify 2026-07-22 : « mountains landscape » → illustration Fuji).
    "contexte": ("mountains", "mountain landscape photography", 11),
    "culture_adn": ("forest", "forest sunlight nature photography", 12),
    "forces_succes": ("sunset", "sunset sky photography", 13),
    "points_amelioration": ("ocean", "ocean waves photography", 14),
    "aspirations": ("sunset", "sunrise horizon photography", 15),
}
# Scènes disponibles pour un axe SUR MESURE (nature_images.SCENES) — l'ordre fixe
# tient l'invariant de reproductibilité : même axe, même image d'un export à l'autre.
_SCENES_AU_CHOIX = ("mountains", "forest", "sunset", "ocean", "tropical", "meadow")


def _visuel_axe(key: str) -> tuple:
    """(scène, requête, seed) pour un axe. Un axe SUR MESURE n'est pas dans la table :
    il tire une scène et un seed DÉRIVÉS de sa key, plutôt que de retomber sur une
    constante partagée.

    Avant le 2026-07-28, la table était indexée par libellé et le repli valait
    `("mountains", "mountains landscape", 11)` pour tout le monde : renommer un axe
    (ce que la fonctionnalité autorise explicitement) ou en ajouter deux donnait
    autant de slides de synthèse portant LA MÊME photo — une imagerie répétée à
    l'identique se lit comme décorative, alors que la charte veut du sens
    (deck-design-library, principes transversaux).

    Portée exacte de la garantie : scène, seed ET requête varient avec la key, donc
    deux axes sur mesure ne reçoivent pas la même image. La SCÈNE seule peut se
    répéter — il n'y en a que six — mais la requête photo porte alors un mot tiré de
    la key, donc une page de résultats différente. Un repli reste un repli : une image
    vraiment porteuse de sens passe par `_SYNTHESE_VISUEL`."""
    connu = _SYNTHESE_VISUEL.get(key)
    if connu:
        return connu
    # Empreinte stable entre processus (`hash()` d'une str est randomisé par PYTHONHASHSEED).
    # Scène et seed sont tirés de TRANCHES DISTINCTES : avec une seule empreinte et
    # `6 | 900`, la scène était entièrement déterminée par le seed — l'espace des
    # signatures tombait à 900 au lieu de 6×900, avec des collisions dès quelques axes.
    empreinte = hashlib.md5((key or "").encode("utf-8")).hexdigest()
    scene = _SCENES_AU_CHOIX[int(empreinte[:8], 16) % len(_SCENES_AU_CHOIX)]
    seed = 20 + int(empreinte[8:16], 16) % 997
    # Le mot-clé issu de la key varie AUSSI la requête photo : sans lui, les six scènes
    # ne donnaient que six requêtes, donc deux axes de même scène tapaient la même page
    # de résultats et pouvaient recevoir le même cliché.
    mot = re.sub(r"[^a-z]+", " ", (key or "").lower()).split()
    qualifiant = f"{mot[0]} " if mot else ""
    return scene, f"{scene} {qualifiant}nature photography".replace("  ", " "), seed


def _slide_synthese_categorie(prs: Presentation, label: str, content: str, key: str) -> None:
    """Slide de catégorie de synthèse pour l'axe `key` (dont `label` est le libellé
    courant, renommable — d'où le paramètre SANS valeur par défaut : un appel à trois
    arguments, comme avant le 2026-07-28, doit échouer bruyamment plutôt que de faire
    silencieusement retomber toutes les slides sur le même visuel de repli).

    ENRICHIE (claim + visuel + encart) : puces à
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

    # Sans encart, -0.60 (pas -0.50) : le visuel de droite descendait sur le badge
    # n° de page du master (y≈5.09, x≈9.25) — signalé par verifier_chrome_gabarit.
    zone_bottom = (band_t - band_gap) if retenir else (h_in - 0.60)
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
        scene, requete, seed = _visuel_axe(key)
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
    # -0.60 (pas -0.45) : la cellule bas-droite recouvrait le badge n° de page du
    # master (y≈5.09, x≈9.25) — signalé par verifier_chrome_gabarit.
    area_h = h_in - area_t - 0.60
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

    # `strict=True` : les deux suites sont de taille fixe (4 quadrants SWOT, 4
    # cellules de la matrice 2x2). Un décalage serait un défaut de code, et il
    # dessinerait une matrice amputée en silence — mieux vaut qu'il lève.
    for (key, label, color), (col, row) in zip(_SWOT_QUADRANTS, cells, strict=True):
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


# --------------------------------------------------------------------------- #
# Grille de maturité par pilier (incr.10 palier 3) — TABLE dessinée robuste au
# texte FR long (pas un tableau natif : hauteurs de ligne maîtrisées), une ligne
# par pilier : intitulé | jauge 3 segments + niveau nommé | justification. Couleur
# = SÉMANTIQUE du score (rouge → ambre → vert, jamais une couleur d'axe), légende
# de l'échelle en pied de slide, pagination au-delà de ce qu'une page loge.
# --------------------------------------------------------------------------- #
_MAT_CPI_LAYOUT = D.CPI_LAYOUT  # calibration mesurée au rendu réel (cf. slides_trajectoire)
_MAT_CPI_BOITE = D.CPI_PESSIMISTE  # hauteur pessimiste des boîtes (verifier_debordements_texte)
# Colonnes (reprises par FIELD_SHAPE, base.py) : jauge au contenu = 3 segments
# (0.22 + 0.05) + 0.06 + « 2 · Structuré » en small gras (~1.2 in pessimiste).
_MAT_COL_PILIER = 2.5
_MAT_SEG_W, _MAT_SEG_GAP = 0.22, 0.05
_MAT_COL_JAUGE = 2.1


def couleur_maturite(score: int) -> str:
    """0 rouge, 1 ambre, 2 vert clair, 3 vert — couleurs sémantiques du deck."""
    return (D.WARN, D.GOLD, D.melanger_blanc(D.OK, 0.35), D.OK)[score_maturite(score)]


def _couleur_texte_maturite(score: int) -> str:
    # GOLD / vert clair < 4.5:1 sur blanc : variante foncée pour le TEXTE (WCAG).
    return (D.WARN, "#8a6508", D.OK, D.OK)[score_maturite(score)]


def _slide_maturite(prs: Presentation, maturites) -> None:
    lignes = [m for m in maturites if (getattr(m, "pilier", "") or "").strip()]
    if not lignes:
        return
    title = "Grille de maturité par pilier"
    w_in, h_in = _dims(prs)
    s = D.TYPE["small"]
    lh = _per_line_height_in(s)
    x0 = MARGIN
    col_p = _MAT_COL_PILIER
    col_s = _MAT_COL_JAUGE  # au contenu : 3 segments + « 2 · Structuré » (revue)
    gap = 0.2
    x_s = x0 + col_p + gap
    x_j = x_s + col_s + gap
    w_j = w_in - MARGIN - x_j
    pad_v = 0.06
    head_h = 0.28
    leg_h = 0.30

    def plan(m, max_j: int, max_p: int = 2):
        p = D.tronquer_a_lignes(m.pilier.strip(), col_p, s, max_p, cpi_ref=_MAT_CPI_LAYOUT)
        j = (m.justification or "").strip()
        j = D.tronquer_a_lignes(j, w_j, s, max_j, cpi_ref=_MAT_CPI_LAYOUT) if j else ""
        n = max(min(max_p, D.estimer_lignes(p, col_p, s, cpi_ref=_MAT_CPI_LAYOUT)),
                min(max_j, D.estimer_lignes(j, w_j, s, cpi_ref=_MAT_CPI_LAYOUT)) if j else 1)
        return m, p, j, max(0.42, n * lh + 2 * pad_v), max(max_j, max_p)

    # Capacité estimée sur un titre à une ligne (content_top ≈ 1.25 sur OCTO) ; le
    # rendu recoupe sur le content_top réel de chaque page et reporte le surplus.
    capacite = (h_in - 0.60 - leg_h) - (1.25 + head_h)
    # Justification jusqu'à 3 lignes si TOUTE la grille tient alors sur une page
    # (peu de piliers : la place existe) ; sinon 2 lignes, page pleine (revue).
    plans = [plan(m, 3) for m in lignes]
    if sum(pl[3] for pl in plans) > capacite:
        plans = [plan(m, 2) for m in lignes]
    pages = D.paginer_items(plans, lambda pl: pl[3], capacite_in=capacite)
    reste: list = []
    k = 0
    while pages or reste:
        page = reste + (pages.pop(0) if pages else [])
        k += 1
        n_pages = k + len(pages)
        suffix = f" ({k}/{n_pages})" if n_pages > 1 else ""
        slide, w_in, h_in, top = _new_slide(prs, title + suffix)
        bas = h_in - 0.60 - leg_h
        y = top + head_h
        tenus = []
        def bas_boites(pl, y0):
            # Bas des boîtes de texte à hauteur PESSIMISTE (ce que PowerPoint peut
            # réellement occuper) — c'est lui, pas la hauteur de mise en page, qui
            # ne doit pas franchir `bas` (revue : la ligne « tient » en layout mais
            # sa boîte descendait sous la bande).
            _m, p, j, rh, n_max = pl
            if n_max == 1:
                return y0 + rh
            hp = max(D.estimer_lignes(p, col_p, s, cpi_ref=_MAT_CPI_BOITE),
                     D.estimer_lignes(j, w_j, s, cpi_ref=_MAT_CPI_BOITE) if j else 1) * lh
            return y0 + max(rh, pad_v + hp)

        for pl in page:
            if max(y + pl[3], bas_boites(pl, y)) > bas:
                if tenus:
                    break
                # 1re ligne de page trop haute (gabarit client au titre bas) :
                # tronquée à UNE ligne plutôt que de déborder sur la légende.
                pl = plan(pl[0], 1, 1)
            tenus.append(pl)
            y += pl[3]
        reste = page[len(tenus):]
        for x, w, lab in ((x0, col_p, "PILIER"), (x_s, col_s, "MATURITÉ"),
                          (x_j, w_j, "JUSTIFICATION")):
            D.add_text(slide, x, top, w, head_h - 0.06,
                       [(lab, {"size": D.TYPE["tiny"], "bold": True, "color": D.MUTED})])
        D.add_rect(slide, x0, top + head_h - 0.03, w_in - 2 * MARGIN, 0.015, fill=D.INK)
        y = top + head_h
        for m, p, j, rh, n_max in tenus:
            sc = score_maturite(m.score)
            h_box = min(rh - 2 * pad_v if n_max == 1 else 9.0, bas - (y + pad_v))
            D.add_text(slide, x0, y + pad_v, col_p,
                       max(h_box, 0.2) if n_max == 1 else
                       max(rh - 2 * pad_v, D.estimer_lignes(p, col_p, s, cpi_ref=_MAT_CPI_BOITE) * lh),
                       [(p, {"size": s, "bold": True, "color": D.INK})],
                       anchor=MSO_ANCHOR.MIDDLE if n_max == 1 else MSO_ANCHOR.TOP)
            # Jauge 3 segments (score = segments pleins) + niveau nommé. Score 0 :
            # segments vides CERCLÉS de la couleur du 0 — même rouge que la légende
            # et que le libellé « 0 · Absent » (revue : trois gris muets contredisaient
            # la légende rouge).
            seg_w, seg_h, seg_gap = _MAT_SEG_W, 0.13, _MAT_SEG_GAP
            sy = y + pad_v + (lh - seg_h) / 2
            for i in range(3):
                plein = i < sc
                D.add_rect(slide, x_s + i * (seg_w + seg_gap), sy, seg_w, seg_h,
                           fill=couleur_maturite(sc) if plein else D.TRACK,
                           line=couleur_maturite(0) if sc == 0 else None, line_w=0.75,
                           rounded=True, radius=0.5)
            lx = x_s + 3 * (seg_w + seg_gap) + 0.06
            D.add_text(slide, lx, y + pad_v, x_s + col_s - lx, lh,
                       [(f"{sc} · {MATURITE_NIVEAUX[sc]}",
                         {"size": s, "bold": True, "color": _couleur_texte_maturite(sc)})])
            if j:
                D.add_text(slide, x_j, y + pad_v, w_j,
                           max(h_box, 0.2) if n_max == 1 else
                           max(rh - 2 * pad_v, D.estimer_lignes(j, w_j, s, cpi_ref=_MAT_CPI_BOITE) * lh),
                           [(j, {"size": s, "color": D.INK})],
                           anchor=MSO_ANCHOR.MIDDLE if n_max == 1 else MSO_ANCHOR.TOP)
            y += rh
            D.add_rect(slide, x0, y - 0.0075, w_in - 2 * MARGIN, 0.0075, fill=D.LINE)
        # Légende de l'échelle (un score nu ne se lit pas) : pas PROPORTIONNEL à la
        # largeur estimée de chaque libellé (revue : pas fixe = trous inégaux).
        t = D.TYPE["tiny"]
        ly = h_in - 0.60 - leg_h + 0.06
        D.add_text(slide, x0, ly, 0.75, 0.22,
                   [("Échelle :", {"size": t, "bold": True, "color": D.MUTED})])
        lx = x0 + 0.75
        for sc, lab in MATURITE_NIVEAUX.items():
            txt = f"{sc} {lab}"
            tw = len(txt) / (_MAT_CPI_LAYOUT * 10.5 / t) + 0.12  # pas (mise en page)
            boite = len(txt) / (_MAT_CPI_BOITE * 10.5 / t) + 0.15  # boîte pessimiste
            D.add_dot(slide, lx, ly + 0.035, 0.11, couleur_maturite(sc))
            D.add_text(slide, lx + 0.16, ly, max(tw, boite), 0.22,
                       [(txt, {"size": t, "color": D.MUTED})])
            lx += 0.16 + tw + 0.22


def _slide_base_analyse(prs: Presentation, couverture_mission, couverture_themes) -> None:
    """« Base de l'analyse » — sur quelle matière REELLE repose le diagnostic.

    Incrément I1 de `docs/reflexions/spec-restitution-defendable.md`. En
    restitution, la question qui fait tomber un constat est « combien de
    personnes ont dit ça ? ». Cette slide y répond par des chiffres CALCULÉS
    (jamais produits par le modèle) : le ratio d'entretiens qui nourrissent
    la synthèse, et la couverture thème par thème.

    Formes transposées de `deck-design-library` (catalogue restitution) :
    pattern 3 pour la carte-chiffre en accent plein — « un sur N en accent »,
    le seul aplat de la slide — et pattern 15 pour les lignes à libellé propre,
    ici une barre de proportion par thème plutôt qu'une échelle de niveaux.

    `att == 0` (thème sans question, ou mission sans entretien structuré) rend
    un tiret : afficher « 0/0 » avec une barre vide ferait lire un « rien à
    mesurer » comme une couverture nulle, donc comme un défaut.
    """
    couv, total = couverture_mission
    lignes = list(couverture_themes or [])
    if not total and not lignes:
        return

    accent = (D.theme_colors(prs).get("accent3") or "#00D2DD")
    s = D.TYPE["small"]
    pad = 0.24
    stat_w = 2.5
    rh_min, rh_max = 0.34, 0.52

    # Une page tant qu'il reste des thèmes ; le nombre de lignes par page vient
    # de la place REELLE sous le titre, qui varie avec son repli (cf. _new_slide).
    reste = lignes or [None]
    page_k, pages_total = 0, None
    while reste or page_k == 0:
        page_k += 1
        suffix = f" ({page_k}/{pages_total})" if pages_total and pages_total > 1 else ""
        slide, w_in, h_in, top = _new_slide(prs, "Base de l'analyse" + suffix)
        bas = h_in - 0.60
        # Claim sous le titre (principe « titre = sujet, sous-titre = claim ») :
        # il dit ce que la slide PROUVE, pas ce qu'elle montre.
        D.add_text(slide, MARGIN + 0.3, top, w_in - 2 * (MARGIN + 0.3), 0.26,
                   [("Chiffres calculés sur les entretiens de la mission — aucun n'est estimé.",
                     {"size": s, "color": D.MUTED})])
        top += 0.36
        avail = max(0.0, bas - top)

        # Carte-chiffre : l'unique aplat de la slide (hiérarchie n°1). Chiffre et
        # libellé dans UNE boîte centrée : en deux boîtes calées sur une fraction
        # de la hauteur, le 44 pt mordait sur son libellé (vu au rendu réel).
        D.add_rect(slide, MARGIN + 0.3, top, stat_w, avail, fill=accent,
                   rounded=True, radius=0.06)
        ratio = f"{couv}/{total}" if total else "—"
        D.add_text(
            slide, MARGIN + 0.3 + 0.12, top + pad, stat_w - 0.24, avail - 2 * pad,
            [(ratio, {"size": D.TYPE["kpi"], "bold": True, "color": "#ffffff"}),
             ("entretiens nourrissent la synthèse",
              {"size": s, "color": "#ffffff", "space_before": 6})],
            anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER,
        )

        liste_l = MARGIN + 0.3 + stat_w + 0.3
        liste_w = w_in - MARGIN - 0.3 - liste_l
        D.add_card(slide, liste_l, top, liste_w, avail, accent)
        D.add_text(slide, liste_l + pad, top + pad * 0.5, liste_w - 2 * pad, 0.3,
                   [("Couverture par thème — interviewés ayant répondu "
                     "/ entretiens structurés", {"size": D.TYPE["tiny"], "color": D.MUTED})])

        y = top + pad * 0.5 + 0.34
        dispo = max(0.0, (top + avail - pad) - y)
        capacite = max(1, int(dispo // rh_min))
        page = [x for x in reste[:capacite] if x is not None]
        reste = reste[capacite:]
        if pages_total is None:
            pages_total = 1 + (len(reste) + capacite - 1) // capacite if reste else 1
        rh = min(rh_max, dispo / max(1, len(page))) if page else rh_min
        # Lignes centrées dans la carte : à `rh` plafonné, peu de thèmes
        # laissaient un vide franc sous la dernière (vu au rendu réel).
        y += max(0.0, (dispo - rh * len(page)) / 2)

        lib_w = liste_w * 0.42
        val_w = 0.62
        bar_l = liste_l + pad + lib_w + 0.14
        bar_w = max(0.3, (liste_l + liste_w - pad - val_w - 0.14) - bar_l)
        for label, rep, att in page:
            libelle = D.tronquer_a_lignes(str(label), lib_w, s, 1)
            D.add_text(slide, liste_l + pad, y, lib_w, rh,
                       [(libelle, {"size": s, "color": D.INK})], anchor=MSO_ANCHOR.MIDDLE)
            if att:
                D.add_hbar(slide, bar_l, y + rh / 2 - 0.055, bar_w, 0.11,
                           rep / att, accent)
                valeur, couleur = f"{rep}/{att}", D.INK
            else:
                valeur, couleur = "—", D.MUTED
            D.add_text(slide, liste_l + liste_w - pad - val_w, y, val_w, rh,
                       [(valeur, {"size": s, "bold": True, "color": couleur})],
                       anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.RIGHT)
            y += rh
        if not reste:
            break
