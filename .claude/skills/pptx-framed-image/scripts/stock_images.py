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


# Openverse agrège des sources tierces (Wikimedia, Flickr, StockSnap…) : `img_url` est
# une donnée qu'on ne contrôle pas. Or `urllib.request.urlopen` suit le schéma `file://`
# par défaut — vérifié, il lit un fichier local — donc une entrée dont l'`url` n'est pas
# http(s) faisait recopier un fichier arbitraire du poste dans le cache d'images du deck.
# Et `r.read()` sans plafond charge toute la réponse en mémoire : un serveur tiers
# décidait de la mémoire de la machine.
#
# Les deux fermés le 2026-09-01. La garde existait déjà dans la copie VSCode3 et
# manquait aux 6 autres, dont CELLE-CI qui est la source du kit : c'est la session
# VSCode3 qui l'a signalé, en instruisant sa propre doctrine de resynchronisation —
# laquelle aurait supprimé son correctif en la réalignant sur nous.
_SCHEMES_AUTORISES = ("http://", "https://")
_TAILLE_MAX = 25 * 1024 * 1024   # 25 Mo : large pour une photo, borné pour la mémoire


def fetch_to(path, query, seed=0, aspect_ratio=None, manifest_path=None):
    img_url, creator, page_url = search_photo(query, seed=seed, aspect_ratio=aspect_ratio)
    if not img_url.lower().startswith(_SCHEMES_AUTORISES):
        raise ValueError(f"refused non-http(s) image URL from Openverse: {img_url[:80]!r}")
    req = urllib.request.Request(img_url, headers={"User-Agent": "bmad-iap-cadrage-ppt/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r, open(path, "wb") as f:
        recu = 0
        while True:
            bloc = r.read(64 * 1024)
            if not bloc:
                break
            recu += len(bloc)
            if recu > _TAILLE_MAX:
                raise ValueError(
                    f"image over {_TAILLE_MAX} bytes, download aborted: {img_url[:80]!r}")
            f.write(bloc)
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
