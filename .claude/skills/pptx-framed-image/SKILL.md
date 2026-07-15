---
name: pptx-framed-image
description: Insert an image into a PowerPoint template "frame" so it takes the frame's EXACT shape (rounded/diagonal corners) by cloning the frame's prstGeom onto the picture — not by rounding the PNG in PIL. Ships a real-photo fetcher (Openverse, CC0, no API key) plus a procedural fun/nature (summer) placeholder-image generator as an offline fallback. Use when filling the OCTO "cadre blanc" visual placeholders (the « ici mettre une Photo » frames, round2DiagRect), when inserted images don't respect the frame's rounded borders, or when you need on-brand imagery without paying for a stock-photo API key.
---

# pptx-framed-image

Fills a template's shaped photo frame with an image that follows the frame's
corners exactly. Prefers a real royalty-free photo (Openverse, CC0) over a
generated placeholder — see Step 4 — with the procedural summer/nature
generator kept as an offline fallback.

## Why geometry-cloning, not PIL rounding

The OCTO "cadre blanc" frame is **not** a plain rounded rectangle: it's a
`round2DiagRect` (adj1=50000, adj2=0) — one diagonal pair of corners rounded,
the other pair square. Rounding the PNG with a uniform PIL radius can never
match that, so the corners poke out or leave white gaps.

Instead: place the picture at the frame's rendered bounds, then **clone the
frame's own `<a:prstGeom>` onto the picture's `<p:spPr>`**. PowerPoint clips the
image to the identical preset shape (same preset, same adjustments) — correct
for `round2DiagRect` and any other preset, with no pixel guessing.

## Instructions

### Step 1 — read the frame's geometry from the layout
The frame lives in a slide *layout* as a group whose inner shape carries the
`prstGeom`. Read its rendered bounds (from the group) and geometry (from the
inner shape):

```python
import sys; sys.path.insert(0, ".claude/skills/pptx-framed-image/scripts")
from framed_image import frame_geometry, place_image_in_frame, round2diag_geom

# group_shape = the layout group that positions the frame
left, top, width, height, geom = frame_geometry(group_shape, "Google Shape;213;p17")
```

If you already know the preset (OCTO cadre blanc), skip the lookup and build it:
`geom = round2diag_geom(50000, 0)` with bounds
`FR_63 = (6270019, 304800, 2593200, 3705000)` (visuel à droite) /
`FR_67 = (571494, 306825, 2593200, 3705000)` (visuel à gauche).

### Step 2 — crop the image to the frame aspect FIRST
Clipping preserves aspect; it does not letterbox. Crop the source to the frame's
`width/height` ratio (≈ 0.700 for the cadre blanc) before inserting, so the
preset clip lands cleanly with no distortion. A **stretched** picture (source
aspect ≠ frame aspect, `stretch fillRect`) is the #1 "image looks off in the
frame" bug. Do it in one call:

```python
from framed_image import cover_crop_to_aspect
aspect = width / height                      # EMU ratio, ≈ 0.700
cover_crop_to_aspect("photo_raw.jpg", "photo.png", aspect)   # center cover-crop
```

### Step 3 — insert and clip
```python
place_image_in_frame(slide, "photo.png", left, top, width, height, geom=geom)
```
The picture is dropped at the exact frame bounds and clipped to `geom`.

### Step 3b — audit the frame region BEFORE trusting the render
The framed image's visible border/edge comes from the **layout/master**, not the
picture. When you resize or refill a frame you expose whatever the old picture
used to cover — the frame shape's own `<a:ln>` border, a stray
`straightConnector1` on the edge, a teardrop poking one pixel past the bounds.
These read as a *partial black border / sliver / stray line*, and a glance at the
render misses them (dark photo content hides thin lines).

```python
from framed_image import frame_obstructions
for o in frame_obstructions(slide, left, top, width, height):
    print(o["source"], o["id"], o["name"], o["reason"])   # empty list == clean
```
Fix each hit at its source in the layout: set the frame shape's line to `noFill`
to drop a border (match a clean reference frame that has fill + `ln=noFill`), or
delete/hide the stray connector. A layout is often shared by 2+ slides — check
`layout -> slides` before editing so you don't disturb another slide.

### Step 4 — fill the frame: a real photo first, a generated placeholder as fallback
Prefer a **real royalty-free photo** over a procedurally generated one — flat
vector "landscapes" read as cheap next to real client-facing content (found
by comparing a generated deck against the real REX reference deck that
inspired this skill: its chapter/content frames are all real photography).

```python
from stock_images import fetch_to
# aspect_ratio: "square" | "wide" | "tall" — pick the one matching the frame
fetch_to("mountains.jpg", "mountains landscape", seed=0, aspect_ratio="square",
          manifest_path="_img/manifest.json")   # provenance log, not required by the license
```
Source: **Openverse** (`api.openverse.org`), filtered to `license=cc0` (public
domain — zero attribution required). No API key needed for read access — this
*is* the API's documented behavior (verified by repeating a query and getting
a stable, non-cached result each time), unlike an earlier dead end below.

If there's no network access (offline session, sandboxed run), fall back to
the procedural generator instead of failing the whole slide:

```python
from nature_images import generate_to
# size = frame aspect; corners are added by the clip in Step 3, NOT here
generate_to("sunset.png", "sunset", 900, 1286, seed=0)
```
Scenes: `sunset`, `ocean`, `mountains`, `forest`, `tropical`, `meadow`.

> **Screen every result before using it — keyword search has no judgment.**
> A query like `"ocean waves aerial"` returned, among clean seascapes, an
> aerial beach photo crowded with sunbathers (a `people` tag was present but
> easy to miss without reading it); a bikini/beach shot is not what a
> professional consulting deck needs. Fetching an image is not the same as
> approving it — render/open every candidate and look before wiring it into
> the deck, same discipline as the rest of this skill's "verify by real
> render" rule. Prefer queries that describe the *shot* over the *place*
> (`"ocean waves aerial"` pulled tourist beaches; a more specific phrase or a
> different `seed` away from the top few results usually finds a clean,
> people-free option — check `tags` in the API response for a `people` flag
> as a first pass, but confirm by eye, tags aren't a reliable filter alone).

> **Dead end, worth remembering:** an earlier attempt used the Pexels API
> (`api.pexels.com`) without a key and got a real-looking result for one query
> — real rate-limit headers and all. It was a **stale Cloudflare cache hit**
> (`cf-cache-status: HIT`, the exact same photo every time) on that one
> specific query string, not a working no-key path: a different query
> immediately returned `401 Missing API key`. **Lesson: a single successful
> no-key request to a paid API is not proof of access — repeat it with a
> query that can't already be cached (or a cache-busting param) before
> building on it.** Openverse's no-key access held up under that same test.

### Step 5 — verify by real render
Geometry checks are not enough for visuals: render the deck to images
(LibreOffice, or PowerPoint COM on Windows) and look — confirm every frame is
filled, corners follow the frame, nothing overflows. Pairs with the
`pptx-verify` skill.

## Known pitfalls (all hit in real use — the audit catches them)
- **Stretched image** — source aspect ≠ frame aspect + `stretch fillRect`. Fix
  with `cover_crop_to_aspect` (Step 2), not by resizing the picture.
- **Frame border** — the frame shape on the layout carries the visible outline
  (`<a:ln>`) and fill, *not* the picture. Two frames that look different usually
  differ only in this `a:ln`. To match a clean frame: fill + `ln=noFill`.
- **Stray edge line** — a `straightConnector1` decoration sitting on the frame
  edge shows once the picture no longer overlaps it. Delete it from the layout.
- **White mask / z-order** — a template white rectangle placed *in front* of the
  frame clips an enlarged picture's corner. Move it behind the picture (it still
  masks its layout target). `frame_obstructions` scans layout+master; front-of-
  picture slide masks are a separate manual z-order check.

## Tests
`python .claude/skills/pptx-framed-image/tests/test_framed_image.py` (9 tests)
checks: the built preset's adjustments; that the inserted picture carries the
frame preset (exactly one geometry child, schema-ordered after `xfrm`) at the
frame bounds; that it stays inside the slide; that `geom=None` leaves a plain
rect; that every nature scene renders at the requested size; that
`cover_crop_to_aspect` hits the frame aspect with no stretch; and that
`frame_obstructions` flags a stray edge line / frame border while ignoring a
shape fully covered by the frame.

## Environment notes
- Windows here has PowerPoint COM (only reliable pptx→image route) and
  LibreOffice, but no poppler/pdftoppm (no PDF→PNG).
- Pure Pillow + python-pptx; no network, no image API.
