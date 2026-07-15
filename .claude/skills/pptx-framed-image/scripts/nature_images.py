"""Procedural nature/summer placeholder images (« L'Été de l'IA »).

No external image API — everything is drawn with Pillow so it runs anywhere.
The scenes aim for a *soft, semi-realistic* look rather than flat vector art:
layered gradients, a blurred sun glow, blurred cloud banks, atmospheric haze
that fades distant layers toward the sky colour, and a light film grain — all
achievable in pure PIL (no numpy). Each scene is a full rectangle at the
requested size; clipping to the frame's rounded shape is the caller's job
(see ``framed_image.place_image_in_frame``), so we do NOT round corners here.

Scenes: ``sunset``, ``mountains``, ``forest``, ``ocean``, ``tropical``, ``meadow``.
"""
import math
import random

from PIL import Image, ImageDraw, ImageFilter, ImageChops


# ---------------------------------------------------------------- primitives
def _vgrad(w, h, stops):
    """Vertical gradient from a list of (t, (r,g,b)) stops (t in 0..1, sorted)."""
    base = Image.new("RGB", (w, h))
    px = base.load()
    for y in range(h):
        t = y / max(1, h - 1)
        # find surrounding stops
        for i in range(len(stops) - 1):
            t0, c0 = stops[i]
            t1, c1 = stops[i + 1]
            if t <= t1 or i == len(stops) - 2:
                k = 0 if t1 == t0 else max(0.0, min(1.0, (t - t0) / (t1 - t0)))
                row = tuple(int(c0[j] + (c1[j] - c0[j]) * k) for j in range(3))
                break
        for x in range(w):
            px[x, y] = row
    return base


def _glow(w, h, cx, cy, r, color):
    """A soft radial glow (bright core fading out), returned as an RGB layer."""
    g = Image.new("RGB", (w, h), (0, 0, 0))
    dr = ImageDraw.Draw(g)
    rings = 26
    for i in range(rings, 0, -1):
        rr = r * i / rings
        f = (1 - i / rings) ** 2
        col = tuple(int(color[j] * f) for j in range(3))
        dr.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=col)
    return g.filter(ImageFilter.GaussianBlur(r * 0.10))


def _add_glow(img, cx, cy, r, color):
    img.paste(ImageChops.screen(img, _glow(img.width, img.height, cx, cy, r, color)), (0, 0))


def _clouds(img, n, y0, y1, rnd, tint=(255, 255, 255), alpha=90):
    w, h = img.size
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    dr = ImageDraw.Draw(layer)
    for _ in range(n):
        cx = rnd.uniform(-0.1, 1.1) * w
        cy = rnd.uniform(y0, y1) * h
        cw = rnd.uniform(0.18, 0.42) * w
        ch = cw * rnd.uniform(0.28, 0.42)
        for _ in range(6):
            ox = rnd.uniform(-cw / 2, cw / 2)
            oy = rnd.uniform(-ch / 3, ch / 3)
            bw = cw * rnd.uniform(0.3, 0.55)
            dr.ellipse([cx + ox - bw, cy + oy - bw * 0.5, cx + ox + bw, cy + oy + bw * 0.5],
                       fill=tuple(int(c * alpha / 255) for c in tint))
    layer = layer.filter(ImageFilter.GaussianBlur(w * 0.02))
    img.paste(ImageChops.screen(img, layer), (0, 0))


def _grain(img, sigma=10, opacity=0.06):
    noise = Image.effect_noise(img.size, sigma).convert("RGB")
    return Image.blend(img, ImageChops.overlay(img, noise), opacity)


def _haze(color, base, k):
    """Fade a colour toward ``base`` (sky) by factor k (atmospheric perspective)."""
    return tuple(int(color[j] + (base[j] - color[j]) * k) for j in range(3))


def _paste_hill(img, y_base, amp, color, rnd, blur=0.0, steps=48):
    """Draw one hill band onto ``img`` (generated once → used as its own mask)."""
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(layer)
    ph = rnd.uniform(0, 6)
    pts = [(0, y_base)]
    for i in range(steps + 1):
        x = w * i / steps
        y = y_base - amp * (0.5 + 0.5 * math.sin(ph + i / steps * math.pi * 2.2))
        pts.append((x, y))
    pts += [(w, y_base), (w, h + 5), (0, h + 5)]
    dr.polygon(pts, fill=color + (255,))
    if blur:
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
    img.paste(layer, (0, 0), layer)


# ------------------------------------------------------------------- scenes
def sunset(w, h, rnd):
    img = _vgrad(w, h, [(0.0, (86, 78, 140)), (0.35, (232, 120, 110)),
                        (0.6, (255, 170, 96)), (1.0, (255, 214, 150))])
    sky = (255, 190, 120)
    sx, sy, sr = int(w * 0.5), int(h * 0.46), int(w * 0.17)
    _add_glow(img, sx, sy, int(w * 0.5), (150, 90, 70))
    ImageDraw.Draw(img).ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=(255, 243, 205))
    _add_glow(img, sx, sy, int(w * 0.28), (120, 80, 60))
    _clouds(img, 5, 0.15, 0.5, rnd, tint=(255, 205, 180), alpha=70)
    for i, (yb, amp, col) in enumerate([(0.7, 0.05, (150, 70, 90)), (0.82, 0.05, (95, 45, 75))]):
        _paste_hill(img, int(h * yb), int(h * amp), _haze(col, sky, 0.25 - i * 0.2), rnd)
    return _grain(img)


def ocean(w, h, rnd):
    img = _vgrad(w, h, [(0.0, (120, 175, 210)), (0.45, (176, 214, 232)), (0.55, (60, 140, 190)),
                        (1.0, (22, 92, 150))])
    _add_glow(img, int(w * 0.72), int(h * 0.24), int(w * 0.42), (120, 130, 90))
    ImageDraw.Draw(img).ellipse([int(w * 0.72 - w * 0.1), int(h * 0.24 - w * 0.1),
                                 int(w * 0.72 + w * 0.1), int(h * 0.24 + w * 0.1)], fill=(255, 248, 214))
    _clouds(img, 4, 0.1, 0.38, rnd, alpha=80)
    sea = int(h * 0.52)
    dr = ImageDraw.Draw(img)
    for i in range(10):
        yy = sea + int((h - sea) * (i / 10) ** 1.4)
        shimmer = 200 - i * 8
        dr.line([(0, yy), (w, yy)], fill=(shimmer, shimmer + 20, shimmer + 30), width=max(1, h // 300))
    return _grain(img)


def mountains(w, h, rnd):
    sky = (196, 220, 236)
    img = _vgrad(w, h, [(0.0, (120, 168, 210)), (0.5, (176, 208, 230)), (1.0, (224, 236, 244))])
    _add_glow(img, int(w * 0.26), int(h * 0.24), int(w * 0.4), (110, 110, 80))
    ImageDraw.Draw(img).ellipse([int(w * 0.26 - w * 0.07), int(h * 0.24 - w * 0.07),
                                 int(w * 0.26 + w * 0.07), int(h * 0.24 + w * 0.07)], fill=(255, 250, 224))
    _clouds(img, 5, 0.08, 0.4, rnd, alpha=95)

    def ridge(ytop, ybase, color, blur, snow):
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        n = 5
        pts = [(0, ybase)]
        xs = [w * i / n for i in range(n + 1)]
        ys = [ybase - (ybase - ytop) * (0.4 + 0.6 * rnd.random()) for _ in xs]
        for x, y in zip(xs, ys):
            pts.append((x, y))
        pts += [(w, ybase), (w, h + 5), (0, h + 5)]
        dr.polygon(pts, fill=color + (255,))
        if snow:
            for x, y in zip(xs, ys):
                if y < ytop + (ybase - ytop) * 0.4:
                    dr.polygon([(x - (ybase - y) * 0.18, y + (ybase - y) * 0.18),
                                (x, y), (x + (ybase - y) * 0.18, y + (ybase - y) * 0.18)],
                               fill=(244, 248, 255, 235))
        if blur:
            layer = layer.filter(ImageFilter.GaussianBlur(blur))
        img.paste(layer, (0, 0), layer)

    ridge(int(h * 0.30), int(h * 0.9), _haze((90, 116, 150), sky, 0.55), w * 0.006, False)
    ridge(int(h * 0.24), int(h * 0.92), _haze((70, 98, 132), sky, 0.30), w * 0.003, True)
    ridge(int(h * 0.40), int(h * 0.95), _haze((54, 82, 116), sky, 0.10), 0, True)
    ImageDraw.Draw(img).rectangle([0, int(h * 0.9), w, h], fill=(78, 132, 100))
    return _grain(img)


def forest(w, h, rnd):
    sky = (200, 226, 210)
    img = _vgrad(w, h, [(0.0, (170, 214, 224)), (0.5, (196, 226, 206)), (1.0, (150, 196, 160))])
    _add_glow(img, int(w * 0.72), int(h * 0.2), int(w * 0.36), (120, 120, 70))
    _clouds(img, 3, 0.06, 0.3, rnd, alpha=70)
    ground = int(h * 0.72)
    layers = [(0.60, (150, 190, 150), w * 0.006), (0.68, (110, 168, 120), w * 0.003), (0.78, (74, 138, 96), 0)]
    for yb, col, blur in layers:
        _paste_hill(img, int(h * yb), int(h * 0.03), _haze(col, sky, 0.3), rnd, blur)
    dr = ImageDraw.Draw(img)
    r2 = random.Random(rnd.random())
    for _ in range(16):
        depth = r2.random()
        bx = r2.uniform(0.02, 0.98) * w
        th = (0.12 + 0.22 * depth) * h
        tw = th * 0.55
        ty = ground + (1 - depth) * 0.08 * h
        base = (34, 108, 62)
        col = _haze(base, sky, 0.4 * (1 - depth))
        dr.polygon([(bx - tw / 2, ty), (bx, ty - th), (bx + tw / 2, ty)], fill=col)
        dr.polygon([(bx - tw * 0.36, ty - th * 0.33), (bx, ty - th * 0.9),
                    (bx + tw * 0.36, ty - th * 0.33)], fill=_haze((44, 122, 72), sky, 0.4 * (1 - depth)))
    return _grain(img)


def tropical(w, h, rnd):
    img = _vgrad(w, h, [(0.0, (255, 206, 130)), (0.4, (255, 224, 176)), (0.55, (92, 196, 206)),
                        (1.0, (36, 150, 168))])
    _add_glow(img, int(w * 0.3), int(h * 0.32), int(w * 0.44), (150, 110, 60))
    ImageDraw.Draw(img).ellipse([int(w * 0.3 - w * 0.12), int(h * 0.32 - w * 0.12),
                                 int(w * 0.3 + w * 0.12), int(h * 0.32 + w * 0.12)], fill=(255, 246, 206))
    _clouds(img, 3, 0.08, 0.34, rnd, tint=(255, 235, 210), alpha=70)
    dr = ImageDraw.Draw(img)
    dr.rectangle([0, int(h * 0.9), w, h], fill=(238, 222, 172))  # sand
    for i in range(6):  # gentle shore waves
        yy = int(h * 0.9) - i * max(2, h // 200)
        dr.line([(0, yy), (w, yy)], fill=(210, 240, 240), width=max(1, h // 360))
    tx, ty = int(w * 0.72), int(h * 0.9)
    hx, hy = tx - int(w * 0.06), int(h * 0.38)
    dr.line([(tx, ty), (hx, hy)], fill=(88, 60, 38), width=max(3, w // 80))
    for ang in (-65, -30, 10, 50, 92):
        ex = hx + math.cos(math.radians(ang)) * w * 0.24
        ey = hy + math.sin(math.radians(ang)) * w * 0.24
        dr.line([(hx, hy), (ex, ey)], fill=(34, 132, 86), width=max(3, w // 100))
    return _grain(img)


def meadow(w, h, rnd):
    sky = (206, 230, 250)
    img = _vgrad(w, h, [(0.0, (146, 198, 240)), (0.5, (196, 224, 248)), (0.62, (150, 200, 130)),
                        (1.0, (108, 180, 108))])
    _add_glow(img, int(w * 0.75), int(h * 0.22), int(w * 0.4), (130, 120, 70))
    ImageDraw.Draw(img).ellipse([int(w * 0.75 - w * 0.08), int(h * 0.22 - w * 0.08),
                                 int(w * 0.75 + w * 0.08), int(h * 0.22 + w * 0.08)], fill=(255, 248, 210))
    _clouds(img, 5, 0.06, 0.34, rnd, alpha=95)
    for yb, col, blur in [(0.6, (150, 200, 130), w * 0.004), (0.72, (112, 184, 108), 0)]:
        _paste_hill(img, int(h * yb), int(h * 0.04), _haze(col, sky, 0.25), rnd, blur)
    dr = ImageDraw.Draw(img)
    r2 = random.Random(rnd.random())
    for _ in range(26):
        x = r2.uniform(0.02, 0.98) * w
        y = r2.uniform(0.72, 0.97) * h
        c = r2.choice([(255, 120, 120), (255, 214, 90), (245, 245, 255), (200, 130, 240)])
        rr = max(2, int(w / 130 * (0.5 + (y / h))))
        dr.ellipse([x - rr, y - rr, x + rr, y + rr], fill=c)
    return _grain(img)


SCENES = {
    "sunset": sunset, "ocean": ocean, "mountains": mountains,
    "forest": forest, "tropical": tropical, "meadow": meadow,
}


def generate(kind, w, h, seed=0):
    """Return an RGB PIL image (``w`` x ``h``) of the named scene."""
    if kind not in SCENES:
        raise ValueError(f"unknown scene {kind!r}; choose from {sorted(SCENES)}")
    return SCENES[kind](int(w), int(h), random.Random(seed)).convert("RGB")


def generate_to(path, kind, w, h, seed=0):
    generate(kind, w, h, seed).save(path)
    return path


if __name__ == "__main__":
    import os, sys
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    for i, k in enumerate(SCENES):
        generate_to(os.path.join(out, f"{k}.png"), k, 900, 1286, seed=i)
        print("wrote", k)
