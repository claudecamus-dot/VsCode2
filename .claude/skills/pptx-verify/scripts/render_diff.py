"""render_diff.py -- pixel-diff gate for the pptx-verify render loop.

Problem: every design iteration re-renders the whole deck, and an eye-check
means reading every full-resolution slide PNG again -- even when only one
small region actually moved. This computes a cheap pixel diff between a
baseline render and a candidate render *first*; only slides that exceed a
noise-filtered threshold are worth spending a real (vision) look on.

This is a filter *before* the human/Claude eye-check, never a replacement for
it -- see pptx-verify/SKILL.md step 3 for the checklist a diff can't run
(cryptic labels, values not aligned to what they annotate, etc.).

Usage:
    # two single PNGs (same slide, before/after)
    python render_diff.py before.png after.png

    # two render directories (as produced by render-pptx.ps1 / render-pptx.sh),
    # compares every matching slide-NN.png pair
    python render_diff.py baseline_dir/ candidate_dir/ --crop-dir out/

Exit code: 0 if nothing needs review, 1 if at least one slide does.
"""
import argparse
import glob
import os
import re
import sys

from PIL import Image, ImageChops

# Per-channel intensity delta below which a pixel is treated as unchanged
# (deterministic rasterizers like PowerPoint COM are near pixel-identical on
# re-render of unchanged content, but leave a small margin for hinting/AA).
DEFAULT_NOISE = 24

# Fraction of pixels (post-noise-filter) that must differ before a slide is
# flagged for review. Tune per project: lower it if subtle color-only changes
# must never be missed, raise it if large stable backgrounds cause noise.
DEFAULT_THRESHOLD = 0.002


class SizeMismatch(ValueError):
    """Baseline and candidate renders are not the same pixel size."""


def _load_rgb(path):
    return Image.open(path).convert("RGB")


def diff_mask(base, cand, noise=DEFAULT_NOISE):
    """Grayscale mask (mode "L"): 255 where a pixel changed by more than
    `noise` on any RGB channel, 0 elsewhere. `base`/`cand` are same-size
    PIL Images."""
    if base.size != cand.size:
        raise SizeMismatch(
            f"size mismatch: baseline {base.size} vs candidate {cand.size} "
            "-- render both at the same width/height"
        )
    diff = ImageChops.difference(base, cand)
    r, g, b = diff.split()
    worst = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return worst.point(lambda p: 255 if p > noise else 0)


def diff_stats(base_path, cand_path, noise=DEFAULT_NOISE):
    """Compare two PNGs on disk. Returns a dict: score (0..1 fraction of
    pixels changed beyond the noise floor), bbox (changed region in px, or
    None if score == 0), size (w, h)."""
    base = _load_rgb(base_path)
    cand = _load_rgb(cand_path)
    mask = diff_mask(base, cand, noise)
    bbox = mask.getbbox()
    changed = mask.histogram()[255] if bbox else 0
    total = mask.size[0] * mask.size[1]
    return {
        "score": changed / total if total else 0.0,
        "bbox": bbox,
        "size": mask.size,
        "changed_px": changed,
        "total_px": total,
    }


def needs_review(score, threshold=DEFAULT_THRESHOLD):
    return score > threshold


def save_diff_crop(cand_path, bbox, out_path, margin=24):
    """Crop `cand_path` to `bbox` expanded by `margin` px (clamped to the
    image bounds) and save to `out_path`. Lets the reviewer zoom into just
    the changed region instead of re-reading the whole slide. Returns the
    actual (left, top, right, bottom) crop box used."""
    im = Image.open(cand_path)
    w, h = im.size
    left, top, right, bottom = bbox
    left = max(0, left - margin)
    top = max(0, top - margin)
    right = min(w, right + margin)
    bottom = min(h, bottom + margin)
    im.crop((left, top, right, bottom)).save(out_path)
    return (left, top, right, bottom)


_SLIDE_RE = re.compile(r"slide-(\d+)\.png$", re.IGNORECASE)


def _slide_files(dir_path):
    """Map slide number -> path for slide-NN.png files in dir_path."""
    found = {}
    for path in glob.glob(os.path.join(dir_path, "slide-*.png")):
        m = _SLIDE_RE.search(path)
        if m:
            found[int(m.group(1))] = path
    return found


def compare_dirs(dir_a, dir_b, noise=DEFAULT_NOISE, threshold=DEFAULT_THRESHOLD):
    """Compare matching slide-NN.png files between two render directories.
    Returns one result dict per slide number found on either side, sorted by
    slide number. A slide present on only one side is reported as
    'added'/'removed' (always worth a look, no diff possible). A size
    mismatch on a shared slide is reported as 'error', not a crash."""
    a, b = _slide_files(dir_a), _slide_files(dir_b)
    results = []
    for n in sorted(set(a) | set(b)):
        if n not in a:
            results.append({"slide": n, "status": "added", "path": b[n]})
        elif n not in b:
            results.append({"slide": n, "status": "removed", "path": a[n]})
        else:
            try:
                stats = diff_stats(a[n], b[n], noise=noise)
            except SizeMismatch as exc:
                results.append({"slide": n, "status": "error", "path": b[n], "error": str(exc)})
                continue
            status = "review" if needs_review(stats["score"], threshold) else "skip"
            results.append({"slide": n, "status": status, "path": b[n], **stats})
    return results


def _cli(argv=None):
    ap = argparse.ArgumentParser(
        description="Pixel-diff gate for the pptx-verify render loop: "
        "flags only the slides/regions worth spending a real eye-check on."
    )
    ap.add_argument("baseline", help="baseline PNG, or a render directory (slide-NN.png files)")
    ap.add_argument("candidate", help="candidate PNG, or a render directory (slide-NN.png files)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                     help=f"fraction of changed pixels above which a slide needs review (default {DEFAULT_THRESHOLD})")
    ap.add_argument("--noise", type=int, default=DEFAULT_NOISE,
                     help=f"per-channel intensity delta ignored as rendering noise (default {DEFAULT_NOISE})")
    ap.add_argument("--crop-dir", help="if set, save a zoomed crop of the changed region for every slide flagged 'review'")
    args = ap.parse_args(argv)

    both_dirs = os.path.isdir(args.baseline) and os.path.isdir(args.candidate)
    if both_dirs:
        results = compare_dirs(args.baseline, args.candidate, args.noise, args.threshold)
    else:
        try:
            stats = diff_stats(args.baseline, args.candidate, args.noise)
        except SizeMismatch as exc:
            print(f"ERROR    {exc}")
            return 1
        status = "review" if needs_review(stats["score"], args.threshold) else "skip"
        results = [{"slide": None, "status": status, "path": args.candidate, **stats}]

    any_review = False
    for r in results:
        label = f"slide {r['slide']}" if r["slide"] is not None else "diff"
        if r["status"] in ("added", "removed", "error"):
            print(f"{r['status'].upper():8} {label}  ({r.get('path')})" + (f"  {r['error']}" if r["status"] == "error" else ""))
            any_review = True
            continue
        print(f"{r['status'].upper():8} {label}  score={r['score']:.4%}  bbox={r['bbox']}")
        if r["status"] == "review":
            any_review = True
            if args.crop_dir and r["bbox"]:
                os.makedirs(args.crop_dir, exist_ok=True)
                name = f"diff-slide-{r['slide']}.png" if r["slide"] is not None else "diff.png"
                out = os.path.join(args.crop_dir, name)
                save_diff_crop(r["path"], r["bbox"], out)
                print(f"         crop -> {out}")

    print()
    print("NEEDS REVIEW" if any_review else "NO REVIEW NEEDED -- all below threshold")
    return 1 if any_review else 0


if __name__ == "__main__":
    sys.exit(_cli())
