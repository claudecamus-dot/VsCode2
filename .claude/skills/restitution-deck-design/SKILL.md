---
name: restitution-deck-design
description: UI/UX design system for consulting-style restitution decks (audit synthesis, transformation recommendations, strategy reports) built with python-pptx — visual hierarchy, spacing rhythm, color-as-meaning, alignment discipline, component consistency. Companion to pptx-deck (layout helpers + geometry self-check) and pptx-verify (render-and-inspect): those two answer "does it fit on the slide and did I actually look at it", this one answers "does it read as professionally designed, or as a technically-correct wall of boxes". Use when generating or reviewing a restitution/consulting deck that passes the geometry check and renders without collisions but still looks amateurish, cluttered, visually inconsistent slide-to-slide, or fails to guide the eye to what matters.
---

> **Provenance** : rapatriée de `~/.claude/skills/restitution-deck-design` au hub le
> 2026-09-02 (arbitrage `VScode5:skills-pptx-globales-non-versionnees`).
> La copie du hub est désormais la source : corriger ICI, jamais dans
> `~/.claude/skills/`, sinon les deux divergent en silence.

# Restitution Deck Design

A deck can pass `verifier_geometrie()` and a real PowerPoint render check —
nothing off-slide, nothing overlapping — and still look like an amateur
built it. This skill is the layer above correctness: the visual system that
makes a deck read as something a McKinsey/BCG/big-4 consultant would hand a
client. It assumes you're already using **pptx-deck** for the helper
library and geometry self-check, and **pptx-verify** for the render-and-eyeball
pass; this skill tells you *what to look for and fix* in that render beyond
"nothing is broken."

## The core failure mode

Technically-correct decks generated from data usually fail on **sameness
without system**: every slide independently "looks fine" but the deck as a
whole doesn't cohere — spacing differs slide to slide, the same concept
(e.g. "high priority") is colored differently in different places, text
sizes are chosen ad hoc per slide instead of from one scale, alignment
drifts by a few points between elements that should share an edge. None of
this trips a geometry check. All of it reads, cumulatively, as unpolished.

The fix is not "add more decoration" — it's **impose a system and never
deviate from it**: one spacing rhythm, one color-to-meaning mapping, one
type scale, one alignment grid, applied identically on every slide.

## 1. Visual hierarchy — one headline per slide

Every slide should have exactly one thing the eye lands on first. In order
of visual weight: a big number/gauge > a short bold headline > supporting
bullets > captions/labels. If two elements compete for primary weight
(two equally-sized bold headlines, two equally saturated colors), the eye
doesn't know where to start and the slide reads as flat.

- Pick the ONE fact each slide exists to convey. Size and color it to win.
  Everything else on the slide should be visually quieter (smaller,
  `MUTED`/`INK` not saturated color, thinner weight).
- Don't bold everything "for emphasis" — bolding everything is the same as
  bolding nothing. Reserve bold for the 1-2 things that matter on that slide.
- A slide with 5 bullets at equal weight has no hierarchy. If all 5 matter
  equally, that itself is information — group them (cards/rows) rather than
  a flat bullet list, so the *grouping* carries meaning bullets alone don't.

## 2. Spacing rhythm — one scale, never ad hoc

Pick a small set of spacing values (e.g. `0.15 / 0.3 / 0.5 / 0.8` inches)
and use only those for gaps, padding, and margins — the way `D.TYPE` is the
one type scale, have one spacing scale too. Random values like `0.23` or
`0.37` scattered through layout code are a tell that spacing was tuned
per-slide rather than systematized, and it shows: gaps that are almost but
not quite consistent read as sloppy even when no one can articulate why.

- Card/panel internal padding: constant across every card type in the deck.
- Gap between a section label (`"OBJECTIF"`, `"CRITÈRES"`) and its content:
  constant everywhere it appears — this project's own `_add_measured_field`
  helper is the right shape for this (see below).
- Gap between stacked blocks in a column: constant, not "whatever fit."

**Concretely in this codebase**: factor repeated (label, content, gap)
triples into one helper (`_add_measured_field` in `pptx_export.py` is exactly
this pattern) instead of hand-placing `y += 1.3` / `y += 1.0` constants that
drift independently per call site.

## 3. Color as meaning, not decoration

A categorical palette (`D.PALETTE`) distinguishes N axes/categories from
each other — that's *identity* color. Separately, `D.OK` (green) / `D.WARN`
(red/amber) / `D.GOLD` should always mean the same thing everywhere they
appear in the deck: high value vs. high complexity, positive vs. risk,
done vs. pending. **Never reuse a semantic color for identity, or an
identity color for semantics** — if axis #2's categorical color happens to
be the same red as "high complexity," a reader will misread axis #2 as a
warning.

- Audit every color use in the deck: is it identity (which axis/category)
  or semantic (good/bad, high/low)? Keep the two systems visually distinct
  (e.g. identity colors are mid-saturation blues/greens/purples; semantic
  is reserved to green/red/amber).
- Muted/ink/line/track (`D.MUTED`, `D.INK`, `D.LINE`, `D.TRACK`) are
  structural, not decorative — backgrounds, borders, secondary text. Don't
  introduce a new gray outside this set; it'll read as a mismatch.
- A brand accent pulled from a client template (`D.theme_colors`) replaces
  *one* categorical slot, never the semantic green/red — a client's brand
  blue should never end up meaning "high risk."
- **Check contrast, not just hue.** WCAG: text needs **4.5:1** against its
  background; a graphical object (bar, dot, icon stroke) only needs **3:1**
  (1.4.11). A brand/identity color that fails 4.5:1 as text (a gold/amber
  around ~3.25:1, say) can still pass at 3:1 as an object — so **color a
  pastille, not the text itself**, and set the label in neutral dark ink
  (repris de VSCode1/export/points-amelioration-ppt.md #8, 2026-09-02).

> **Divergence documentée, pas une règle** : VSCode1 impose "header de card
> toujours cyan ou navy, jamais blanc/slate" (`design-system-octo.md`); §8
> ci-dessous, constaté sur VSCode3/4, utilise plutôt un header blanc =
> libellé en capitales + court filet d'accent, sans fill coloré. Les deux
> conventions existent dans la flotte OCTO — un deck en choisit une et s'y
> tient, il ne les mélange pas sur les mêmes cartes.

## 4. Alignment discipline

Elements that are conceptually related must share an exact edge —
same left margin, same baseline, same column start — even when they're
drawn by different function calls at different points in the code. Misalignment
by even 0.05in is invisible in isolation but reads as "slightly off" in
aggregate, especially across a sequence of similar slides (e.g. every
recommendation card should have its axis number start at exactly the same
x as every other recommendation card).

- Anchor every slide's content to the same `MARGIN` constant — never
  hand-tune a slightly different left inset "because it looked better" on
  one slide.
- When two elements are meant to align (a value next to a bar, a label
  above its content), compute one from the other's geometry rather than
  eyeballing two independent coordinates — see pptx-deck's checklist item
  on "value beside bar must be centered on the bar's centerline."
- Columns (left/right split layouts, card grids) should share consistent
  gutter widths — derive both columns' x-offsets from the same formula, not
  two separately-tuned constants.

## 5. Component consistency

If a "card" pattern is used in three places in the deck (axis overview row,
recommendation summary, KPI tile), it must look **identical** everywhere:
same corner radius, same border/shadow treatment, same internal padding,
same accent-bar treatment. A card that's rounded in one place and square in
another reads as two different people built the deck.

- Before adding a new visual component, check whether an existing helper
  (`D.add_card`, `D.add_rect`, `D.add_hbar`, `D.add_gauge`) already covers
  it — reuse over reinvention is itself a consistency discipline, not just
  a DRY nicety.
- If a genuinely new component is needed, add it to the shared helper
  library (pptx-deck) rather than one-off styling it inline in a single
  slide function — otherwise the *next* slide that needs something similar
  will drift from it.

## 6. Progressive disclosure — slide sequencing tells a story

A restitution deck is read start to finish, not slide-by-slide in
isolation. The sequence itself is a design decision:
title → sommaire (sets expectations) → context/synthesis (establishes the
"why") → structural overview (the axes/recommendations at a glance, before
detail) → matrix/prioritization (how to read the detail that follows) →
detail slides (one per recommendation). Each slide should assume the reader
saw the previous one — don't re-explain what the sommaire already promised,
don't show detail before the overview that frames it.

- If you're about to add a new slide type, ask where in this narrative arc
  it belongs, not just "does the data fit."
- Section-transition slides (axes overview, matrix) exist specifically to
  reset the reader's frame before a run of similar detail slides — don't
  cut them even when the deck is short; a jump straight from summary to
  12 near-identical recommendation slides has no signposting.

## 7. Restraint

The credibility of a consulting deck comes partly *from* what it doesn't
do: no gratuitous icons, no drop shadows "for depth," no gradient fills, no
decorative dividers that carry no information. Every visual element should
be traceable to a reason (this shape encodes this data; this color means
this state). If you can't articulate the reason, cut it.

- Shadows are explicitly disabled on shapes in this project's helpers
  (`_no_shadow` in `pptx_deck.py`) — keep it that way; flat design reads as
  more deliberate/current than skeuomorphic shadow-and-gradient decks.
- Icons/emoji in slide content (as opposed to the *web app's* UI, where
  they're fine) should be rare and purposeful — a gauge or bar already
  encodes "value," it doesn't also need a 🎯 next to it.

## Working with this project's generator (`app/services/pptx_export.py`)

- `D.TYPE` is the one type scale — never introduce a literal point size
  outside it.
- Spacing constants (`MARGIN`, the gaps inside `_slide_recommendation`,
  etc.) should be pulled toward a small shared set rather than accumulating
  new one-off values as slides are added.
- When adding a new slide type, write it to reuse `_new_slide`'s computed
  `content_top` (title-aware, never a hardcoded `top = 1.4`) and to reuse
  `_add_measured_field`/`_add_bulleted_text` for any text block whose length
  isn't bounded — a fixed-height text box is a latent overflow bug waiting
  for a longer-than-test-data interview answer (this bit this project once
  already: see increment 5's fix to `_slide_recommendation`).
- **Always close the loop with a real render** (pptx-verify, PowerPoint COM
  or LibreOffice) before calling a layout change done — this project found
  a case where a `.pptx` parsed fine in python-pptx and passed every
  automated test, yet PowerPoint refused to open it outright
  (`_clear_slides` not dropping the slide relationship). Structural
  correctness (opens, geometry clean) and visual correctness (looks
  designed) are both necessary and neither implies the other.

## Review checklist (apply to a fresh render, per pptx-verify)

For each slide, ask:
1. What's the one thing this slide wants me to notice first — does the
   render actually draw the eye there?
2. Does every spacing gap on this slide match the same gaps on similar
   slides elsewhere in the deck?
3. Is every color used doing a job (identity or semantic) — none of them
   decorative, none of them ambiguous with a different job's color?
4. Do elements that should align, align exactly — not just "close"?
5. Does this card/badge/gauge look pixel-identical to the same component
   elsewhere in the deck?
6. Read the deck start to finish once — does the sequence make sense
   without needing the source data to explain it?

## 8. Formats OCTO de référence (VSCode3/4) — reproduire, pas ré-inventer

Ces motifs sont **constatés sur les vrais decks OCTO** (VSCode3
`bmad-iap-cadrage-synthese.pptx`, VSCode4 `Exports/*.pptx`). **Règle d'or : quand
une tâche cite « la charte / le design de VSCode3/4 », RENDRE 2-3 slides de la
référence AVANT d'implémenter — ne jamais affirmer la conformité de mémoire.** (Payé
cher : une barre d'accent ajoutée « charte VSCode4 » puis retirée « VSCode4 n'en a
pas » ; sommaire/numéro/encarts corrigés seulement après avoir enfin rendu VSCode4.)

- **Tête de chapitre** : layout « 50 - Chapitre ». Numéro **dans le petit encart
  logo** (idx1) — pas un bloc dessiné : marges à zéro + ~17pt + centré + `buNone`
  (sinon « 01 » wrappe ou porte une puce résiduelle). Placeholder titre (idx0) =
  **titre coloré + un 2ᵉ paragraphe sous-titre italique gris**. Cadre photo teardrop
  **rempli** (jamais vide).
- **Image de contenu** : clippée au **cadre OCTO `round2DiagRect`** (2 coins
  diagonaux arrondis, 2 vifs) — **jamais un rectangle plat**. Grande, pleine hauteur,
  à droite du claim.
- **Encart « à retenir » / so-what** : **boîte gris clair** (≈`#eceef2`), texte foncé,
  fin liseré d'accent — **pas** une bande de couleur pleine criarde (la couleur est un
  accent, cf. §3/§7).
- **Carte de contenu** : blanche, fine bordure grise, **sans liseré latéral coloré** ;
  header = **libellé en capitales + court filet d'accent** (la couleur du header porte
  le SENS quand il y en a un — quadrants SWOT, sinon teal de marque).
- **Titre de slide** : claim (pas étiquette), navy, **sans barre d'accent** avant (le
  logo suffit) ; sous-titre/kicker italique gris si utile.
- **Puces de contenu** : lead-in gras (« Le constat — … », « Ce que ça coûte — … »)
  puis explication — pas des puces plates.
