"""Functional tests for render_diff.py -- drive it as a real subprocess (the
way an agent invokes it from the command line), asserting on stdout and exit
codes rather than calling functions directly.

Complements test_render_diff_unit.py (which tests the functions in-process).
Run standalone: python test_render_diff_functional.py  (also pytest-discoverable)
"""
import os
import subprocess
import sys
import tempfile

from PIL import Image

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "render_diff.py")
W, H = 200, 120


def _run(*args):
    proc = subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _png(name, color=(255, 255, 255), size=(W, H)):
    path = os.path.join(tempfile.gettempdir(), f"_rdf_{name}.png")
    Image.new("RGB", size, color).save(path)
    return path


def test_identical_pair_exits_zero_and_reports_no_review():
    a = _png("identical_a")
    b = _png("identical_b")
    code, out, err = _run(a, b)
    assert code == 0, (code, out, err)
    assert "NO REVIEW NEEDED" in out, out
    assert "SKIP" in out, out
    print("ok  CLI exits 0 and prints NO REVIEW NEEDED for identical renders")


def test_differing_pair_exits_one_and_reports_review():
    a = _png("diff_a", color=(255, 255, 255))
    b = _png("diff_b", color=(0, 0, 0))  # whole image changed -> well above threshold
    code, out, err = _run(a, b)
    assert code == 1, (code, out, err)
    assert "NEEDS REVIEW" in out, out
    assert "REVIEW" in out, out
    print("ok  CLI exits 1 and prints NEEDS REVIEW for a real change")


def test_crop_dir_writes_a_crop_file_for_flagged_slide():
    dir_a = tempfile.mkdtemp(prefix="_rdf_dira_")
    dir_b = tempfile.mkdtemp(prefix="_rdf_dirb_")
    Image.new("RGB", (W, H), (255, 255, 255)).save(os.path.join(dir_a, "slide-01.png"))
    changed = Image.new("RGB", (W, H), (255, 255, 255))
    for x in range(60):
        for y in range(60):
            changed.putpixel((x, y), (0, 0, 0))
    changed.save(os.path.join(dir_b, "slide-01.png"))
    crop_dir = tempfile.mkdtemp(prefix="_rdf_crops_")

    code, out, err = _run(dir_a, dir_b, "--crop-dir", crop_dir)
    assert code == 1, (code, out, err)
    crop_path = os.path.join(crop_dir, "diff-slide-1.png")
    assert os.path.isfile(crop_path), (out, os.listdir(crop_dir))
    # crop is smaller than the full slide -- proof it zoomed in rather than copying it whole
    assert Image.open(crop_path).size < (W, H)
    print("ok  --crop-dir writes a zoomed crop for the flagged slide only")


def test_directory_mode_flags_added_and_removed_slides():
    dir_a = tempfile.mkdtemp(prefix="_rdf_dira2_")
    dir_b = tempfile.mkdtemp(prefix="_rdf_dirb2_")
    Image.new("RGB", (W, H), (255, 255, 255)).save(os.path.join(dir_a, "slide-01.png"))
    Image.new("RGB", (W, H), (255, 255, 255)).save(os.path.join(dir_b, "slide-01.png"))
    Image.new("RGB", (W, H), (255, 255, 255)).save(os.path.join(dir_a, "slide-02.png"))  # removed
    Image.new("RGB", (W, H), (255, 255, 255)).save(os.path.join(dir_b, "slide-03.png"))  # added

    code, out, err = _run(dir_a, dir_b)
    assert code == 1, (code, out, err)
    assert "REMOVED" in out and "slide 2" in out, out
    assert "ADDED" in out and "slide 3" in out, out
    print("ok  directory mode flags slides present on only one side")


def test_size_mismatch_reports_error_without_crashing():
    a = _png("sizea", size=(W, H))
    b = _png("sizeb", size=(W + 20, H))
    code, out, err = _run(a, b)
    assert code == 1, (code, out, err)
    assert "size mismatch" in out.lower(), out
    assert err == "", err  # reported on stdout, not an unhandled traceback on stderr
    print("ok  a size mismatch between renders is reported cleanly, not a crash")


def test_threshold_flag_changes_the_verdict():
    a = _png("thresh_a", color=(255, 255, 255))
    b = _png("thresh_b", color=(255, 255, 255))
    im = Image.open(b)
    for x in range(5):
        for y in range(5):
            im.putpixel((x, y), (0, 0, 0))
    im.save(b)  # 25/24000 = ~0.1% changed -> below default 0.2%, above a lowered 0.05%

    code_default, out_default, _ = _run(a, b)
    assert code_default == 0, out_default
    code_strict, out_strict, _ = _run(a, b, "--threshold", "0.0005")
    assert code_strict == 1, out_strict
    print("ok  --threshold moves the skip/review boundary as expected")


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} FUNCTIONAL TESTS PASSED")


if __name__ == "__main__":
    main()
