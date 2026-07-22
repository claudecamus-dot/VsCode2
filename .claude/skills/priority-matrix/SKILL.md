---
name: priority-matrix
description: Render a value/effort prioritization matrix as a DRAWN decision tool (tinted meaning-labeled quadrants + axis-colored bubbles), never as a native Excel scatter chart. Use whenever building or fixing the "Matrice de priorisation — valeur / effort" slide of this project's PPT export (`_slide_matrice_effort_valeur` in pptx_export.py), or any time a prioritization/scatter slide renders as tiny grey markers with a cryptic legend.
---

# priority-matrix — a prioritization matrix that reads as a decision

The native PowerPoint XY scatter (`XL_CHART_TYPE.XY_SCATTER`) was tried and
failed review ("le rendu est très mauvais", 2026-07-22): microscopic grey Excel
markers, a legend of cryptic symbols (◆ ■ ▲ ✕ ✳), points stacked invisibly on
top of each other, zero quadrant semantics, off-brand styling python-pptx can
barely control. A prioritization matrix is a *decision tool*, not a data plot —
draw it with shapes.

## The contract

1. **Four tinted quadrants whose MEANING is written on them.** The reader must
   get the decision at a glance, without decoding coordinates:
   - haut-gauche (valeur haute / effort faible) : `QUICK WINS` — vert `#1e6b34`
   - haut-droite (valeur haute / effort fort) : `CHANTIERS DE FOND` — bleu `#2c5cc5`
   - bas-gauche (valeur basse / effort faible) : `OPPORTUNISTES` — gris `#6b7280`
   - bas-droite (valeur basse / effort fort) : `À DIFFÉRER` — ambre `#b8860b`
   Tint = `melanger_blanc(color, ~0.93)`, border a mid-tint, label **small** bold
   in a dark tint of the quadrant colour, tucked in the outer corner — labels
   short enough to fit ONE line at `small` in a half-grid (that's why «
   CHANTIERS DE FOND », not « STRUCTURANTS » which wrapped behind the bubbles),
   and the bubble rows map into `ph - 0.38` so a valeur=5 row never covers them.

2. **One bubble per recommendation, identity-colored by AXIS.** Bubble colour =
   the axis's palette colour — the *same* palette as the axes-overview slide
   (identity colour, cf. restitution-deck-design §3; quadrant colours carry the
   semantics, bubbles carry identity — never mix the two jobs). The reco number
   (`1.1`) goes *inside* the bubble (white, bold, tiny); full titles live in the
   legend and on the fiches.

3. **Co-located bubbles fan out.** Scores are 1–5 integers — collisions are the
   norm, not the exception (that's what made the scatter unreadable). Group by
   `(complexité, valeur)` and offset each member horizontally
   (`(k - (n-1)/2) * (d + gap)`), clamped inside the plot area.

4. **Axes labeled in words, no numeric ticks.** `Complexité (effort) →` under
   the plot, `Valeur (impact) →` rotated in the left gutter (same
   `_label_axe_vertical` pattern as the SWOT — keep its unrotated bbox on-slide
   for `verifier_geometrie`). The 1–5 scores are on the fiches; ticks here are
   noise. Map score s to position with the `(s − 0.5)/5` linear rule so 1 and 5
   don't sit on the border.

5. **Legend by axis on the right**, not by point: a colour chip + `Axe N —
   titre` (bold), then its recos `N.M titre` (muted, truncated to one line,
   `tronquer_a_lignes`). Stop before the plot bottom rather than overflow.

## Keep it coupled

- Same slide-count/behaviour contract as before: the matrix always covers ALL
  axes (it's a synthesis slide, `include_axis_ids` never filters it).
- Sommaire entry and `apercu.html` (checkbox + web sommaire parity) must carry
  the same section name as the slide — grep `Matrice` when renaming.
- One type scale (`D.TYPE`), flat shapes only (`_no_shadow`), theme font via
  `set_police`/`add_text` (never a hardcoded uninstalled font).

## Done means

`pytest -q` green, `verifier_geometrie` clean, **pptx-verify render eyeballed**:
quadrant labels legible, no bubble overlap, bubbles distinguishable from
quadrant tints, legend not truncated mid-word, nothing colliding with the
page-number badge.
