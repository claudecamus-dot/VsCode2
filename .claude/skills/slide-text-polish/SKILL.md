---
name: slide-text-polish
description: Raise the writing quality of every slide's text — turn labels into claims, tighten bullets, kill filler, expand cryptic abbreviations, keep punctuation and parallel structure consistent. Ships a linter (slide_lint) with tests. Use when generating or reviewing a deck's copy (REX, restitution, synthesis) and you want each slide to read crisply rather than as a wall of vague bullets.
---

# slide-text-polish

Two layers: **principles** to rewrite by, and a **linter** to catch regressions.

## Principles (rewrite each slide against these)

1. **Title = a claim, not a label.** "Contexte" → "Pourquoi un export PowerPoint
   directement dans l'outil ?". The audience should get the takeaway from the
   title alone.
2. **One idea per bullet, ≤ ~2 lines.** If a bullet needs "et"/"—" to stitch two
   ideas, split it or cut one. Aim ≤ 200 characters.
3. **Lead with the point (BLUF).** Put the conclusion first, the justification
   after: "Ça marche : un clic produit le support" — not the reverse.
4. **Cut filler.** Drop "afin de", "il est important de", "au niveau de", "en
   termes de"… They add length, never information.
5. **Concrete over vague.** Numbers, names, verbs of action. "2 équipes, 7
   répondants" beats "plusieurs contributeurs".
6. **Expand a cryptic abbreviation on first use**, then reuse it: "US6.4",
   "python-pptx" are fine; an unknown acronym gets a gloss in parentheses once.
7. **Parallel structure.** Bullets in a list share grammatical shape and don't
   all start with the same word. Terminal punctuation is consistent (all end
   with "." or none do).
8. **No cryptic abbreviations in client deliverables** — write "écart-type", not
   "é-t".
9. **≤ 6 bullets per slide.** More means it's two slides.

## Linter

```bash
python .claude/skills/slide-text-polish/scripts/slide_lint.py deck.json
# deck.json = [ {"title": "...", "bullets": ["...", "..."]}, ... ]
```
Or in-process:
```python
import sys; sys.path.insert(0, ".claude/skills/slide-text-polish/scripts")
from slide_lint import lint_deck, format_findings
print(format_findings(lint_deck(slides)))   # slides = list of {title, bullets}
```
Codes: `WEAK_TITLE`, `LONG_BULLET`, `TOO_MANY`, `FILLER`, `MIXED_PUNCT`,
`ABBREV`, `DUP_LEAD`. Exit code is non-zero when anything is flagged (usable in CI).

## Workflow
1. Draft the deck's copy as a list of `{title, bullets}`.
2. Rewrite each slide against the principles above.
3. Run the linter; fix every finding (or justify leaving it).
4. Only then render the deck. Pairs with `pptx-deck` (layout), `pptx-verify`
   (visual render check), and `pptx-framed-image` (image frames).

## Tests
`python .claude/skills/slide-text-polish/tests/test_slide_lint.py` — 9 cases
covering each rule (clean slide passes; long bullet, too many bullets, filler,
mixed punctuation, unexplained vs expanded abbreviation, weak title, duplicate
lead word, deck-level index aggregation).
