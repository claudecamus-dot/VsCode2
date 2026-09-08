"""Unit tests for render_diff.py -- exercise the functions directly (no
subprocess, no CLI). Synthetic PNGs only, generated in-memory with Pillow and
written to a temp dir -- no fixture files, no template needed.

Run standalone: python test_render_diff_unit.py  (also discoverable by pytest)
See test_render_diff_functional.py for the CLI/subprocess-level tests.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from PIL import Image

import render_diff as RD

W, H = 200, 120


def _save(im, name):
    path = os.path.join(tempfile.gettempdir(), f"_rd_{name}.png")
    im.save(path)
    return path


def _blank(color=(255, 255, 255)):
    return Image.new("RGB", (W, H), color)


def test_identical_images_score_zero_no_bbox():
    im = _blank()
    a, b = _save(im, "id_a"), _save(im, "id_b")
    stats = RD.diff_stats(a, b)
    assert stats["score"] == 0.0, stats
    assert stats["bbox"] is None
    print("ok  identical renders diff to score 0 with no bbox")


def test_small_change_below_threshold_is_skip():
    base = _blank()
    cand = base.copy()
    # a single 2x2 px patch changed -- well under the default 0.2% threshold
    for x in range(2):
        for y in range(2):
            cand.putpixel((10 + x, 10 + y), (0, 0, 0))
    a, b = _save(base, "small_a"), _save(cand, "small_b")
    stats = RD.diff_stats(a, b)
    assert 0 < stats["score"] < RD.DEFAULT_THRESHOLD, stats
    assert not RD.needs_review(stats["score"]), stats
    print("ok  a tiny localized change stays under the default review threshold")


def test_large_change_above_threshold_is_review():
    base = _blank()
    cand = base.copy()
    # a block covering >0.2% of the image (much bigger than the noise case)
    for x in range(40):
        for y in range(40):
            cand.putpixel((50 + x, 30 + y), (0, 0, 0))
    a, b = _save(base, "large_a"), _save(cand, "large_b")
    stats = RD.diff_stats(a, b)
    assert RD.needs_review(stats["score"]), stats
    assert stats["bbox"] is not None
    left, top, right, bottom = stats["bbox"]
    assert left <= 50 and top <= 30 and right >= 90 and bottom >= 70, stats["bbox"]
    print("ok  a block change above threshold is flagged review with a tight bbox")


def test_noise_floor_ignores_subtle_antialiasing_delta():
    base = _blank()
    # a delta of 10 (below the default noise floor of 24) across the whole image
    cand = Image.new("RGB", (W, H), (245, 245, 245))
    a, b = _save(base, "noise_a"), _save(cand, "noise_b")
    stats = RD.diff_stats(a, b, noise=24)
    assert stats["score"] == 0.0, stats
    print("ok  a whole-image delta under the noise floor scores 0")


def test_noise_floor_is_configurable():
    base = _blank()
    cand = Image.new("RGB", (W, H), (245, 245, 245))  # delta of 10
    a, b = _save(base, "cfgnoise_a"), _save(cand, "cfgnoise_b")
    stats = RD.diff_stats(a, b, noise=5)  # lower floor -> this delta now counts
    assert stats["score"] == 1.0, stats
    print("ok  lowering --noise below the delta makes it count")


def test_size_mismatch_raises():
    a = _save(_blank(), "size_a")
    b = _save(Image.new("RGB", (W + 10, H), (255, 255, 255)), "size_b")
    try:
        RD.diff_stats(a, b)
        assert False, "expected SizeMismatch"
    except RD.SizeMismatch:
        pass
    print("ok  mismatched render sizes raise SizeMismatch instead of a wrong score")


def test_save_diff_crop_expands_by_margin_and_clamps():
    cand = _blank()
    path = _save(cand, "crop_src")
    out = os.path.join(tempfile.gettempdir(), "_rd_crop_out.png")
    box = RD.save_diff_crop(path, (5, 5, 15, 15), out, margin=10)
    assert box == (0, 0, 25, 25), box  # clamped at 0 on the left/top
    assert Image.open(out).size == (25, 25)
    print("ok  save_diff_crop expands by margin and clamps to image bounds")


def test_slide_files_maps_number_to_path():
    d = tempfile.mkdtemp(prefix="_rd_slides_")
    for n in (1, 2, 10):
        _blank().save(os.path.join(d, f"slide-{n:02d}.png"))
    mapping = RD._slide_files(d)
    assert set(mapping) == {1, 2, 10}, mapping
    print("ok  _slide_files maps slide numbers to paths regardless of zero-padding")


def test_compare_dirs_flags_added_removed_and_diffed():
    dir_a = tempfile.mkdtemp(prefix="_rd_dira_")
    dir_b = tempfile.mkdtemp(prefix="_rd_dirb_")
    _blank().save(os.path.join(dir_a, "slide-01.png"))          # unchanged
    _blank().save(os.path.join(dir_b, "slide-01.png"))
    _blank((0, 0, 0)).save(os.path.join(dir_a, "slide-02.png"))  # removed in b
    changed = _blank()
    for x in range(40):
        for y in range(40):
            changed.putpixel((50 + x, 30 + y), (0, 0, 0))
    _blank().save(os.path.join(dir_a, "slide-03.png"))           # changed a lot
    changed.save(os.path.join(dir_b, "slide-03.png"))
    _blank().save(os.path.join(dir_b, "slide-04.png"))           # added in b

    results = {r["slide"]: r for r in RD.compare_dirs(dir_a, dir_b)}
    assert results[1]["status"] == "skip", results[1]
    assert results[2]["status"] == "removed", results[2]
    assert results[3]["status"] == "review", results[3]
    assert results[4]["status"] == "added", results[4]
    print("ok  compare_dirs reports skip/removed/review/added correctly across a slide set")


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} UNIT TESTS PASSED")


if __name__ == "__main__":
    main()
