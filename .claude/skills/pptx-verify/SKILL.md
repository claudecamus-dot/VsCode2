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

## Step 1bis — Form check (warning only, 0 token)

```bash
py .claude/skills/pptx-verify/scripts/forme_check.py deck.pptx          # warns, exit 0
py .claude/skills/pptx-verify/scripts/forme_check.py deck.pptx --strict # exit 1 if any warning
```

Warns on shapes off the slide, stretched or alt-less images, a title that is not a title
placeholder, empty slides and work-in-progress marks ("à valider", "V0", TODO). Also warns on
**residual template content** (`GABARIT_RESIDUEL`: "JJ.MM.26", "NOM DU CLIENT", "(calculé)", "- WIP" at the
end of a line, "à définir", "TBD", template instructions; `PLACEHOLDER_VIDE`: an unfilled placeholder and
nothing else on the slide besides its title; `CORPS_VIDE`: nothing but the title, no image, table, chart or
drawn schema; `TITRE_DOUBLON`; `SUITE_EN_TITRE`). The title text never counts as body, so deleting the empty
placeholder does not silence the slide. A section/cover layout, a divider whose body is a number, and the
titles "Annexe", "Merci", "Questions", "Sommaire", "Agenda" are exempt. Those are the dominant defects of 2
of the 3 weak decks reviewed on 2026-10-13 (Pitch: 12 of 16 slides flagged; Offre: 11 of 32 before
tightening, 8 after), invisible to the density measures because the template's shapes exist. A committee of
4 voices checked the first version against the rendered slides: 3 false alarms on Offre (9, 16, 21: text
visible, an invisible second placeholder) and several false blocks led to the tightening above; all new
codes stay WARNINGS, never a hard export gate. Known gaps: intermediate dividers all numbered "01"; internal
notes in the deliverable (Offre 30); a template frame holding only an image (Pitch 11); composition defects
(label-only titles, card walls, meaningless colour, off-topic separator images, which are most of what
makes the "dispositif" deck weak): those stay on the rendered review. Small explicit
fonts, walls of text, too many font sizes and missing speaker notes are reported as information
only: on 3 decks the user judged (2 good, 1 weak), these measures ran backwards (the good decks
were denser, with smaller fonts) and separate nothing. It does **not** see contrast, alignment or
composition, and it reads explicit font sizes only (sizes inherited from the template are
ignored): a deck with zero warnings can still be unreadable. **Never report a deck as good from
this check; Step 2 stays mandatory.** Thresholds live in `scripts/forme_check_seuils.json`; while
`etalonne` is `false` they are provisional starting values, not a quality bar. Calibration
results and the rule they led to: `docs/redaction/etalonnage-forme-decks.md` (hub-only note).
Never use `--strict` as a quality gate.

## Step 1quater — Density regression and motif inventory (0 token, information only)

Non-regression ceilings and inventories, not charter norms (thresholds in `scripts/*_deck_seuils.json`).

```bash
py .claude/skills/pptx-verify/scripts/densite_deck.py deck.pptx --top=10   # densest slides
py .claude/skills/pptx-verify/scripts/motifs_deck.py deck.pptx --meme-gabarit 3   # motifs, radii, same-layout slides
```

## Step 1quinquies — Render guard and selective render (0 token)

- `py .claude/skills/pptx-verify/scripts/garde_rendu.py deck.pptx --mini-police 10 [--strict] [--baseline f.json]`: exit 1 on off-slide shape or text overflow; warns on overlap, contrast < 4.5, small font, hidden slides; output capped at 20 lines + total.
- `py .claude/skills/pptx-verify/scripts/rendu_slide.py deck.pptx 7 13 [--dossier=D] [--dpi=80]`: LibreOffice PNG of the listed slide numbers only (hidden slides keep their number); reading the PNGs is the token cost: read only the touched slides, dpi 80; exit 2 on an unknown rank; one full render stays mandatory before delivery.
- `py tests/test_garde_livrable_baseline.py --regenerer`: freezes known guard defects in `tests/baselines/*.json` (helper `tests/garde_baseline.py`); the test fails on a NEW defect and on a vanished entry (debt paid: remove it); keys carry no slide number (code, shape, text hash), so inserting a slide does not shift the debt.
- `forme_check_seuils.json` keeps its provisional thresholds; the hub decks' distribution is recorded in its `observation` block only (not a verdict).

## Step 1ter — Charter, weight, integrity (strict, 0 token)

```bash
py .claude/skills/pptx-verify/scripts/lint_charte.py deck.pptx --charte acn   # exit 1 on off-charter colour/font, 2 if charter unresolved
py .claude/skills/pptx-verify/scripts/poids_deck.py deck.pptx                 # WARN 12 MB or 200 KB/slide, BLOCK 25 MB
py .claude/skills/pptx-verify/scripts/integrite_pptx.py deck.pptx             # OPC relations, 0 problem
```

Opt-in, never by default: `embarquer_police.py deck.pptx --faces faces.json --dossier-polices DIR --sortie out.pptx` embeds TTF fonts as EOT (dry-run unless `--appliquer`; input never modified; exit 0/1/2). Limits: fsType=4 opens read-only for a recipient without the font; fsType=2 is refused unless `--accepter-licence`; the embedding licence is yours to validate; the result is never opened in real PowerPoint by this tool.

`generateur_squelette.py --sortie` runs all three itself. The charter is the reference deck's
(`charte/<nom>/tokens.json`, derived by `deriver_tokens_charte.py`), never the deck compared with itself.
These checks never replace Step 2.

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

## Python-first helpers (no LLM tokens)

- `planche_contact.py --sortie P.png --dossier DIR [--rangs 3,5]` assembles the `sNNN.png` renders of `rendu_slide.py` into ONE contact sheet: read one image, open full size only the doubtful slides.
- `autocorriger_dense.py DECK --sortie OUT [--max-car 700] [--rapport R.json]` shrinks overflowing text down to 10.5 pt, then splits what stays too dense onto a `(suite)` copy ONLY for text-only slides (a slide with frames, arrows, images, groups or SmartArt is reported `RESTE_DENSE`, manual split); prints and records what changed, never touches the source deck (exit 1 if some slide stays dense).

## Iteration rule — render only the touched slides

When iterating on user feedback (playbook `export-ppt-iteration`), render ONLY the slides
touched with `render-pptx.ps1 -Slides <list>` (PowerPoint COM). The LibreOffice fallback
`render-pptx.sh` has no list option, only a contiguous range (`-f first -l last`): render the
smallest range covering the touched slides. Then compare with `render_diff.py` against the
baseline render (behaviour on a partial candidate folder not yet verified: check that the
slides missing from the candidate are not reported as changed). The `-Slides` list comes from
the "recueillir les retours" step (comments, shape changes, text changes).
A full render is done only before delivery. The mandatory checks are unchanged: only their
scope narrows to the touched slides during iteration. A deck built from scratch
(`export-ppt-verifie`) gets the full render.

**Font caveat (not adopted, TO VALIDATE).** The theme font (Graphik) is not embedded: PowerPoint and
LibreOffice substitute it differently, so text that fits in the control render can overflow on the
user's machine, and "overflow / squashed" feedback is often not reproducible here. Embedding the font
is only a lead (licence, fsType embeddable bit to check first). For such feedback, the playbook
offers variants A/B/C to the user (step `strategie-forme`); entry checks (`~$` file, base, kinds of
differences) run first in `export-ppt-iteration`.

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

**Leave a trace of the looking**: list the PNG paths you actually opened (for a run
journalled with `log_run.py`, put them in `notes` as `rendus: <chemins>`). A render nobody
opened is not a verification.

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
