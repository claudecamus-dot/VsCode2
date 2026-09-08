---
name: pptx-verify
description: Verify the visual design of a generated .pptx by rendering it to images and checking the defects a geometry self-check can't catch (misaligned values, empty/over-stretched panels, collisions with template chrome, gaps from over-tall text boxes, cryptic labels). Use after generating or changing a deck, before claiming the design is good — pairs with the pptx-deck skill which builds the deck.
---

> **Provenance** : rapatriée de `~/.claude/skills/pptx-verify` au hub le 2026-09-02
> (arbitrage `VScode5:skills-pptx-globales-non-versionnees`).
> La copie du hub est désormais la source : corriger ICI, jamais dans
> `~/.claude/skills/`, sinon les deux divergent en silence.

# pptx-verify

Companion to **pptx-deck**. pptx-deck *builds* a deck and ships a geometry self-check
(`verifier_geometrie`) that only flags shapes running off the slide. That check is
necessary but **not sufficient**: a geometry-clean deck can still look broken. This
skill is the *seeing* half — render the deck for real and walk a concrete visual QA
checklist. Never report a deck as "quality / verified" from the geometry check alone.

## When to use

- Right after generating or editing a `.pptx` (restitution / synthesis / report deck).
- When asked to "verify / check / améliorer" a slide's design, or to confirm a layout
  fix actually looks right.
- Whenever you are about to claim a deck "looks good" — do this first.

## Step 1 — Geometry check (fast, catches off-slide shapes)

Run the project's deck test if it has one (it should call `D.verifier_geometrie`):

```bash
python app/scripts/test-export-ppt.py     # must print all-tests-pass + 0 problème
```

Or inline: `problemes = D.verifier_geometrie(prs); assert not problemes`. Green here
means nothing runs off the slide — it says **nothing** about whether it looks good.

If the project's test also renders to PDF (LibreOffice `--convert-to pdf`), add a
real page-count check: count pages with a raw regex `/Type\s*/Page` on the PDF
bytes (no PDF-reading dependency) and assert it equals the exported slide count —
this catches a `.pptx` that python-pptx parses without error but that no engine
would actually open cleanly (repris de VSCode1/export/ppt-toolkit.md §6, 2026-09-02).

## Step 2 — Render to images (the part that actually shows the design)

**Windows + PowerPoint (preferred here):**

```bash
pwsh scripts/render-pptx.ps1 -Pptx <deck.pptx> -OutDir <scratchpad> [-Width 1600 -Height 900] [-Slides 1,3,6]
```

Writes `slide-NN.png` (all slides, or just the `-Slides` list) and prints their paths.
Then **Read each PNG** and look at it.

**Non-Windows / no PowerPoint (LibreOffice + poppler):**

```bash
bash scripts/render-pptx.sh -p <deck.pptx> -o <outdir> [-r 150] [-f first -l last]
```

Converts `.pptx → .pdf` (LibreOffice) then rasterizes pages to `slide-NN.png`
(poppler `pdftoppm`, or ImageMagick). Prints the PNG paths; then **Read each PNG**.
`-r` is DPI (150 ≈ 1500px wide, 200 ≈ 2000px). If only LibreOffice is present it
leaves the `.pdf` — read its pages directly.

**No renderer at all:** dump each shape's text + bounds and say honestly the visual was
not eye-checked. Do not pretend.

## Step 3 — Eyeball against this checklist (defects geometry will NOT catch)

For every slide, hunt specifically for:

1. **Over-stretched panels / empty voids** — a callout/box sized to the full band but
   holding 2–3 lines reads as a big empty rectangle. Expect panels sized to content
   and centered in free space.
2. **Values not aligned to what they annotate** — a number beside a bar must be
   vertically centered on the bar's centerline, not floating above it. Check every bar.
3. **Collisions with template chrome** — geometry never flags overlap with the master's
   **page-number badge / logo / footer**. Look at the bottom-right corner (page number)
   and the footer band: is content butting into or overlapping them?
4. **Gaps from over-tall text boxes** — a dead band between stacked elements
   (e.g. heading ↔ caption) means a line-height estimate is too generous.
5. **Content flush to borders** — text touching a card/panel edge; expect internal
   padding and the content block centered.
6. **Cryptic labels** — unexplained abbreviations (`é-t`, `σ`, `n=`) the reader won't
   parse; metrics should be spelled out. Don't duplicate as text what a shape already
   shows.
7. **The usual** — overflow, cramping, inconsistent type sizes (should all come from
   one scale), color not encoding meaning, misaligned columns.
8. **Contrast (WCAG)** — text on a colored/light background must hit **4.5:1**
   (normal text); a graphical object (bar, icon, gauge stroke) only needs **3:1**
   (1.4.11). A borderline brand color (e.g. gold/goldenrod ~3.25:1) fails as text
   but passes as an object — if it's failing as text, move the color onto a
   dot/pastille and put the label in a neutral dark ink instead (repris de
   VSCode1/export/points-amelioration-ppt.md #8, 2026-09-02).

## Step 3bis — Optional: pixel-diff gate before spending eye-check tokens

On an *iteration* (you already rendered a baseline once and are re-rendering
after a small tweak), a full re-render + full eye-check re-reads every slide
image again even if only one region moved. `scripts/render_diff.py` compares
a baseline render dir/PNG against the new one and tells you which slides
actually changed beyond a noise floor, so you only read those (optionally
cropped to the changed region):

```bash
python scripts/render_diff.py baseline_dir/ candidate_dir/ --crop-dir out/
# exit 0 + "NO REVIEW NEEDED" -> nothing to look at, skip the eye-check
# exit 1 + per-slide REVIEW/SKIP/ADDED/REMOVED -> read only the flagged ones
```

Tune `--threshold` (fraction of pixels, default 0.2%) and `--noise` (per-channel
delta ignored as rendering noise, default 24) if it's over/under-flagging on
this deck. **This is a pre-filter, not a substitute** for Step 3 — it catches
"did anything change and where," not "does it read as cryptic/misaligned/empty."
A slide it flags "skip" still means "unchanged from last time I looked," not
"guaranteed good" on a first render. Tests: `tests/test_render_diff_unit.py`
(pure functions) and `tests/test_render_diff_functional.py` (CLI/subprocess).

## Step 4 — Zoom in on anything suspicious

Low-res whole-slide views hide small collisions and misalignments. Crop the region and
read the crop:

```bash
pwsh scripts/crop-png.ps1 -In <slide.png> -Out <crop.png> -X <px> -Y <px> -W <px> -H <px>
```

Render a slide at high res first (e.g. `-Width 2560 -Height 1440`) so crops are sharp.
Always crop the bottom-right corner (page-number badge) and any number-beside-a-bar.

## Step 5 — Report honestly

State which slides you actually rendered and looked at, list concrete defects with the
slide number (and a crop if subtle), and fix → re-render → re-check. If you could not
render, say so and say what you checked instead. A passing geometry check is not a
substitute for having looked.

## Step 6 — For a design-intent change, the USER validates before you commit

Having *you* looked (Steps 3–5) is necessary but still not sufficient. For any change to
**design intent** — layout, font size, an element's position / presence / scale, colour
(not a pure geometry/bug fix) — the deliverable's definition-of-done is **the user
validating the real render**, never "tests + geometry green". Two hard rules:

1. **Validate before commit.** Put the rendered result (images of the touched slides) in
   front of the user and get explicit approval **before** committing, and before saying
   "done / finalised / closed". A green geometry check and passing tests are not the user
   liking it. Committing your own visual judgement is how a design task fails to converge:
   you declare it done, the user re-opens it, repeat.
2. **Variants before choosing.** When the request is about an element's **placement /
   orientation / scale / presence** (an element with layout options), render **2–3
   variants as images** and let the user pick **before** you write the production version —
   not "implement one → commit → wait for the reaction". Real rendered mockups drive the
   *choice*, not just the after-the-fact validation. (An element that has churned
   in→out→back across turns is the tell you skipped this.)

If you catch yourself about to commit a visual change the user has not seen, stop and show
it first.

## Notes

- Write PNGs to the session scratchpad, not the user's project.
- See **pptx-deck** for the build-side helpers and the same checklist from the author's
  angle; this skill is the reviewer's angle.
