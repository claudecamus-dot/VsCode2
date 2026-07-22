---
name: swot-matrix
description: Render a SWOT as a real 2×2 MATRIX (not four loose cards) on a restitution slide — quadrant cells that read as a matrix, the Interne/Externe × Favorable/Défavorable axes made explicit, and no empty voids. Use whenever building or fixing the SWOT slide of this project's PPT export (`_slide_swot` in pptx_export.py), or any time a SWOT "n'est pas au RDV" / looks like disconnected boxes.
---

# swot-matrix — a SWOT that reads as a matrix

A SWOT drawn as four independent cards passes `verifier_geometrie` and even a
render check, yet still fails: it reads as **four loose boxes**, not a *matrix*.
This skill is the design contract for the SWOT slide, sitting under
**restitution-deck-design** (the deck-wide system) and verified with
**pptx-verify** (render + eyeball). It exists because this project's SWOT was
repeatedly flagged "pas au RDV" for exactly two reasons — fix both, every time.

## The two failure modes (both must be gone)

1. **No matrix framing.** Four titled cards (Forces / Faiblesses / Opportunités
   / Menaces) in a 2×2 grid do *not* communicate the SWOT's two axes. A real
   SWOT matrix makes them visible:
   - **Rows = origine** : `INTERNE` (top: Forces, Faiblesses) vs `EXTERNE`
     (bottom: Opportunités, Menaces).
   - **Columns = effet sur l'objectif** : `FAVORABLE` (left: Forces,
     Opportunités) vs `DÉFAVORABLE` (right: Faiblesses, Menaces).
   The quadrant arrangement is fixed by these axes — Forces top-left,
   Faiblesses top-right, Opportunités bottom-left, Menaces bottom-right — so the
   axis labels are *true*, not decoration. Put `FAVORABLE`/`DÉFAVORABLE` above
   the columns and `INTERNE`/`EXTERNE` down the left gutter (rotated), small and
   muted, so the grid reads as a matrix at a glance.

2. **Empty voids.** SWOT bullet lists are short (3–5 items) but the quadrants are
   equal grid cells — a *white* card with 3 bullets leaves a large empty
   rectangle below them (pptx-verify's "over-stretched panel" defect). Fix:
   **fill the cell with a light tint of its quadrant colour** (`melanger_blanc`,
   ~0.90). A filled quadrant reads as an intentional matrix cell; the space
   under the bullets is then tinted, not a white void. Never stretch a white
   card to `area_h/2` and leave it mostly empty.

## Colour = meaning, one job each (cf. restitution-deck-design §3)

- Each quadrant owns one semantic colour, reused for its liseré, badge, title
  and tint — never mixed: Forces `#1e6b34` (vert = atout interne), Faiblesses
  `#b3261e` (rouge = faille interne), Opportunités `#2c5cc5` (bleu = levier
  externe), Menaces `#b8860b` (ambre = risque externe).
- The **column** axis labels reinforce the same semantics: `FAVORABLE` in green
  (`D.OK`), `DÉFAVORABLE` in red (`D.WARN`). The **row** labels stay neutral
  (`D.MUTED`) — origin is not a good/bad signal.
- Cell tint = `melanger_blanc(color, ~0.90)`; cell border = a mid tint
  (`~0.55`) of the same colour, 1 pt — never a drop shadow (OCTO charte is
  flat, `_no_shadow`).

## Layout contract (10 × 5.625 in OCTO slide)

- Left **gutter** (~0.30 in) holds the rotated `INTERNE`/`EXTERNE` labels; top
  **strip** (~0.30 in) holds `FAVORABLE`/`DÉFAVORABLE`. Grid area starts after
  both, right edge at `MARGIN`.
- Each quadrant cell: tinted rounded rect + colored left liseré (consistent with
  the deck's card style) → badge (icon) + colored bold title → bullets in `INK`,
  `paginate=True` so an over-long quadrant truncates inside its cell instead of
  spilling into its neighbour.
- Icons are the directional arrows already chosen for reliability (monochrome
  Arrows block, `bold=False` — some glyphs "tofu" in bold): `↑` force, `↓`
  faiblesse, `↗` opportunité, `↘` menace.
- One type scale only (`D.TYPE`): title `h3`, bullets `small`→`tiny`, axis
  labels `tiny`. No literal point sizes.
- **Font**: never force `Outfit` (not a system font → substituted). The deck
  uses the *theme* font (`police_theme`, Arial on OCTO) — the SWOT text inherits
  it via `add_text`/`set_police`, so it matches the rest of the deck. cf. the
  font root-cause note in CLAUDE.md.

## Keep it coupled

`FIELD_SHAPE["swot_quadrant"]` (the web-editor fit-hint) must track the cell's
real bullet width/height — if you change the gutter, gap or padding here, update
that entry too, or the "shape-aware" hint drifts from what the slide can hold
(the project's standing rule for `pptx_export.py` ↔ `FIELD_SHAPE`).

## Done means

1. `pytest -q` green (SWOT tests: `tests/test_swot.py`).
2. `verifier_geometrie` clean (no cell/label off-slide).
3. **pptx-verify render eyeballed** — the slide reads as a *matrix* (axes
   legible, quadrants tinted, no empty white voids, no collision with the
   page-number badge), not four floating cards.
4. For a design-intent change, the **user validates the real render before
   commit** (pptx-verify §6) — render it, show it, don't self-approve.
