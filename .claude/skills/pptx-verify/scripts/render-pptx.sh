#!/usr/bin/env bash
# render-pptx.sh — render a .pptx to per-slide PNGs on non-Windows (no PowerPoint).
#
# Pipeline: LibreOffice (soffice/libreoffice) converts .pptx -> .pdf, then poppler
# (pdftoppm) or ImageMagick (magick/convert) rasterizes the PDF pages to slide-NN.png.
# Prints the written PNG paths (Read them to eyeball the design — see SKILL.md).
#
# Usage:
#   bash render-pptx.sh -p deck.pptx -o outdir [-r dpi] [-f first] [-l last]
#   bash render-pptx.sh deck.pptx                # positional pptx, outdir=.
# Options:
#   -p, --pptx     path to the .pptx (or pass it positionally)
#   -o, --outdir   output directory (created if missing; default ".")
#   -r, --dpi      raster resolution; 150≈1500px wide, 200≈2000px (default 150)
#   -f, --first    first slide to render (1-based; default 1)
#   -l, --last     last slide to render (default: last)
set -euo pipefail

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

pptx=""; outdir="."; dpi=150; first=""; last=""
while [ $# -gt 0 ]; do
  case "$1" in
    -p|--pptx)   pptx="$2"; shift 2;;
    -o|--outdir) outdir="$2"; shift 2;;
    -r|--dpi)    dpi="$2"; shift 2;;
    -f|--first)  first="$2"; shift 2;;
    -l|--last)   last="$2"; shift 2;;
    -h|--help)   usage; exit 0;;
    -*)          echo "unknown option: $1" >&2; usage >&2; exit 2;;
    *)           if [ -z "$pptx" ]; then pptx="$1"; shift; else echo "unexpected arg: $1" >&2; exit 2; fi;;
  esac
done

[ -n "$pptx" ] || { echo "error: no .pptx given" >&2; usage >&2; exit 2; }
[ -f "$pptx" ] || { echo "error: file not found: $pptx" >&2; exit 2; }
mkdir -p "$outdir"

# Locate LibreOffice: explicit $SOFFICE, then PATH, then standard install paths
# (it is often NOT on PATH on Windows/macOS).
soffice="${SOFFICE:-}"
[ -n "$soffice" ] || soffice="$(command -v soffice || command -v libreoffice || true)"
if [ -z "$soffice" ]; then
  for cand in \
    "/c/Program Files/LibreOffice/program/soffice.exe" \
    "/c/Program Files (x86)/LibreOffice/program/soffice.exe" \
    "/Applications/LibreOffice.app/Contents/MacOS/soffice" \
    "/usr/bin/soffice" "/usr/local/bin/soffice" "/opt/libreoffice/program/soffice"; do
    [ -x "$cand" ] && { soffice="$cand"; break; }
  done
fi
[ -n "$soffice" ] || { echo "error: LibreOffice not found (set \$SOFFICE, or install it)." >&2; exit 3; }

# 1) .pptx -> .pdf
"$soffice" --headless --convert-to pdf --outdir "$outdir" "$pptx" >/dev/null
pdf="$outdir/$(basename "${pptx%.*}").pdf"
[ -f "$pdf" ] || { echo "error: PDF not produced ($pdf)" >&2; exit 4; }

# 2) .pdf -> per-slide PNGs
prefix="$outdir/slide"
if command -v pdftoppm >/dev/null 2>&1; then
  args=(-png -r "$dpi")
  [ -n "$first" ] && args+=(-f "$first")
  [ -n "$last" ]  && args+=(-l "$last")
  pdftoppm "${args[@]}" "$pdf" "$prefix"
  # pdftoppm names slide-<page>.png (variable padding) — normalize to slide-NN.png.
  for f in "$outdir"/slide-*.png; do
    [ -e "$f" ] || continue
    n="$(basename "$f")"; n="${n#slide-}"; n="${n%.png}"
    case "$n" in ''|*[!0-9]*) continue;; esac
    p="$(printf 'slide-%02d.png' "$n")"
    [ "$(basename "$f")" = "$p" ] || mv -f "$f" "$outdir/$p"
  done
  ls "$outdir"/slide-*.png | sort
elif command -v magick >/dev/null 2>&1; then
  magick -density "$dpi" "$pdf" "$prefix-%02d.png"
  ls "$outdir"/slide-*.png | sort
elif command -v convert >/dev/null 2>&1 && convert -version 2>/dev/null | grep -qi imagemagick; then
  # Guard: on Windows, `convert` is the disk tool, NOT ImageMagick — verify first.
  convert -density "$dpi" "$pdf" "$prefix-%02d.png"
  ls "$outdir"/slide-*.png | sort
else
  echo "PDF written: $pdf" >&2
  echo "note: no rasterizer (poppler 'pdftoppm' / ImageMagick) — read the PDF pages" >&2
  echo "      directly, or use the Windows render-pptx.ps1 (PowerPoint COM) for PNGs." >&2
  echo "$pdf"
fi
