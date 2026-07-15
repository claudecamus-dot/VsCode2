"""Fetch real royalty-free photos (Openverse) for a template photo frame,
instead of a procedurally generated placeholder — flat vector "landscapes"
(see nature_images.py) read as cheap next to a real client-facing deck; a
real photo doesn't.

Source: Openverse (api.openverse.org), a public search API over openly
licensed content (Wikimedia, Flickr, StockSnap, Rawpixel...). No API key
required for read access. Filtered to ``license=cc0`` (public domain) only —
zero attribution required, so a chapter/vision slide can carry the image with
no caption. (An earlier attempt used the Pexels API without a key; that
turned out to be a stale Cloudflare cache hit on one specific query, not a
real credential path — confirmed by re-querying and getting 401 on other
terms. Openverse's no-key access is the API's actual, documented behavior,
verified by repeating the same query and getting a consistent, non-cached
result each time.)

Public API
----------
- ``search_photo(query, seed=0, aspect_ratio=None)`` -> (image_url, creator, page_url)
      Query Openverse, cc0-only, return the ``seed``-th result.
- ``fetch_to(path, query, seed=0, aspect_ratio=None, manifest_path=None)``
      Download that photo to ``path``; if ``manifest_path`` is given, append/
      update a provenance record there (query, creator, source page, license).
"""
import json
import os
import urllib.parse
import urllib.request

OPENVERSE_SEARCH = "https://api.openverse.org/v1/images/"
LICENSE_NOTE = "CC0 (domaine public) via Openverse — aucune attribution requise"


def search_photo(query, seed=0, aspect_ratio=None):
    params = {"q": query, "license": "cc0", "page_size": 20, "mature": "false"}
    if aspect_ratio:
        params["aspect_ratio"] = aspect_ratio
    url = f"{OPENVERSE_SEARCH}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "bmad-iap-cadrage-ppt/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    results = data.get("results", [])
    if not results:
        raise RuntimeError(f"no Openverse cc0 result for {query!r}")
    p = results[seed % len(results)]
    return p["url"], p.get("creator") or "inconnu", p.get("foreign_landing_url", "")


def fetch_to(path, query, seed=0, aspect_ratio=None, manifest_path=None):
    img_url, creator, page_url = search_photo(query, seed=seed, aspect_ratio=aspect_ratio)
    req = urllib.request.Request(img_url, headers={"User-Agent": "bmad-iap-cadrage-ppt/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r, open(path, "wb") as f:
        f.write(r.read())
    if manifest_path:
        _record(manifest_path, os.path.basename(path), query, creator, page_url)
    return path


def _record(manifest_path, filename, query, creator, page_url):
    entry = {
        "file": filename, "query": query, "creator": creator,
        "source": page_url, "license": LICENSE_NOTE,
    }
    manifest = []
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    manifest = [m for m in manifest if m.get("file") != filename] + [entry]
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
