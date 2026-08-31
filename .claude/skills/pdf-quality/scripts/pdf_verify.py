"""Vérificateur de PDF — il MESURE, il ne suppose pas.

Pendant de ``pdf_report.py`` : ce script relit un ``.pdf`` déjà écrit et rend
des nombres sur les sept défauts relevés par l'audit du 2026-08-31. Il rend un
**code de sortie non nul** dès qu'un défaut bloquant est trouvé, pour être
chaînable dans une vérification avant commit.

Ce qu'il mesure
---------------
- nombre de pages, nombre de blocs et de lignes par page ;
- **taux de remplissage** par page et **plus grand blanc vertical** (mm) —
  l'audit trouvait des pages à 40 % avec 154 mm de blanc et une orpheline à 4 % ;
- **bbox sortant de la zone imprimable** (marge de sécurité paramétrable) ;
- **unicité du bord gauche** : les abscisses gauches du texte ET des tracés
  (fonds d'encadré, filets) sont regroupées ; l'audit en trouvait trois sur une
  même page (20,0 / 22,1 / 25,0 mm). Les éléments calés à droite (numéro de
  page) sont détectés et exclus, sinon ils fausseraient la mesure ;
- **polices embarquées ou non** ;
- **caractères demandés mais absents du rendu** (``--attendu``), plus les
  marqueurs ``[U+XXXX]`` que ``pdf_report.py`` laisse quand une fonte ne couvre
  pas un caractère ;
- **métadonnées** (titre, auteur, sujet, ``/Lang``), **signets** et **balisage**
  (``/StructTreeRoot``, ``/MarkInfo``).

Ce qu'il ne fait pas
--------------------
Il ne regarde pas. Un PDF peut passer tous ces contrôles et rester illisible :
contraste raté, hiérarchie absente, tableau incompréhensible. Rastérisez et
ouvrez les pages — la skill le répète parce que l'audit qui a produit ce
cahier des charges n'a rien trouvé de tout cela avant d'avoir REGARDÉ 22 pages.

Dépendance
----------
PyMuPDF (``fitz``). S'il est absent, le script le dit et sort en code 2 : il ne
simule rien.

Usage
-----
    py pdf_verify.py rapport.pdf --grille-mm 20 --attendu "Nguyễn Thị Mai"
    py pdf_verify.py rapport.pdf --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

PT_PAR_MM = 72.0 / 25.4

try:  # PyMuPDF est la seule façon fiable de mesurer une géométrie de page ici
    import fitz  # type: ignore

    FITZ_DISPONIBLE = True
    FITZ_ERREUR = ""
except ImportError as _exc:  # pragma: no cover - dépend de la machine
    fitz = None
    FITZ_DISPONIBLE = False
    FITZ_ERREUR = str(_exc)


class PyMuPDFAbsent(RuntimeError):
    """PyMuPDF n'est pas installé — on le dit, on ne simule pas la mesure."""


SEUILS_DEFAUT = {
    "securite_mm": 8.0,
    "remplissage_min": 55.0,
    "blanc_max_mm": 60.0,
    "orphelin_min": 8.0,
    "tolerance_mm": 0.4,
    "fusion_pt": 4.0,
    "largeur_trace_min_mm": 5.0,
}


def _mm(points: float) -> float:
    return points / PT_PAR_MM


def _fusionner(intervalles, tolerance: float):
    """Fusionne des intervalles [y0, y1] séparés de moins de ``tolerance``.

    La tolérance ferme les interlignes : sans elle un texte plein compterait
    ~80 % de remplissage et chaque interligne passerait pour un « blanc ».
    """
    if not intervalles:
        return []
    ordonnes = sorted(intervalles)
    fusionnes = [list(ordonnes[0])]
    for debut, fin in ordonnes[1:]:
        if debut - fusionnes[-1][1] <= tolerance:
            fusionnes[-1][1] = max(fusionnes[-1][1], fin)
        else:
            fusionnes.append([debut, fin])
    return fusionnes


def _grouper(valeurs, tolerance: float):
    """Regroupe des abscisses proches ; rend [(valeur_representative, effectif)]."""
    groupes = []
    for valeur in sorted(valeurs):
        if groupes and valeur - groupes[-1][0] <= tolerance:
            groupes[-1][1] += 1
            groupes[-1][2].append(valeur)
        else:
            groupes.append([valeur, 1, [valeur]])
    return [(round(sum(g[2]) / len(g[2]), 2), g[1]) for g in groupes]


def _elements_page(page, largeur_trace_min_pt: float):
    """Rend (lignes_texte, traces) — chacun une liste de dicts bbox + nature."""
    lignes = []
    donnees = page.get_text("dict")
    for bloc in donnees.get("blocks", []):
        if bloc.get("type") != 0:  # 0 = texte
            continue
        for ligne in bloc.get("lines", []):
            x0, y0, x1, y1 = ligne["bbox"]
            texte = "".join(s.get("text", "") for s in ligne.get("spans", []))
            if not texte.strip():
                continue
            lignes.append({"bbox": (x0, y0, x1, y1), "texte": texte, "nature": "texte"})

    traces = []
    for dessin in page.get_drawings():
        x0, y0, x1, y1 = dessin["rect"]
        if x1 - x0 <= 0 and y1 - y0 <= 0:
            continue
        traces.append(
            {
                "bbox": (x0, y0, x1, y1),
                "nature": dessin.get("type", "?"),
                "large": (x1 - x0) >= largeur_trace_min_pt,
            }
        )
    return lignes, traces


def _analyser_polices(doc):
    """Inventaire des polices, avec le drapeau « embarquée » (ext != 'n/a')."""
    vues = {}
    for numero in range(doc.page_count):
        for infos in doc[numero].get_fonts(full=False):
            xref, ext, type_, basefont, nom = infos[0], infos[1], infos[2], infos[3], infos[4]
            vues.setdefault(
                basefont,
                {
                    "basefont": basefont,
                    "type": type_,
                    "extension": ext,
                    "nom": nom,
                    "embarquee": ext not in ("n/a", "", None),
                    "pages": [],
                },
            )["pages"].append(numero + 1)
    return sorted(vues.values(), key=lambda f: f["basefont"])


def analyser(
    chemin: str,
    grille_mm: float = None,
    securite_mm: float = SEUILS_DEFAUT["securite_mm"],
    remplissage_min: float = SEUILS_DEFAUT["remplissage_min"],
    blanc_max_mm: float = SEUILS_DEFAUT["blanc_max_mm"],
    orphelin_min: float = SEUILS_DEFAUT["orphelin_min"],
    tolerance_mm: float = SEUILS_DEFAUT["tolerance_mm"],
    retrait_citation_mm: float = 0.0,
    attendus=None,
    exiger_balisage: bool = False,
) -> dict:
    """Mesure un PDF et rend le rapport complet (dict sérialisable en JSON).

    ``grille_mm`` : si fourni, on vérifie en plus que l'unique bord gauche tombe
    bien sur cette valeur — sinon on vérifie seulement qu'il est unique.
    ``attendus`` : chaînes qui DOIVENT apparaître dans le texte extrait ; c'est
    la seule façon fiable de prouver qu'un « Nguyễn Thị Mai » n'a pas été
    silencieusement mutilé en « NguyIn ThI Mai ».
    """
    if not FITZ_DISPONIBLE:
        raise PyMuPDFAbsent(
            "PyMuPDF (fitz) n'est pas installé : impossible de MESURER ce PDF. "
            f"Installation : py -m pip install pymupdf. Erreur d'import : {FITZ_ERREUR}"
        )
    if not os.path.exists(chemin):
        raise FileNotFoundError(chemin)

    tolerance_pt = tolerance_mm * PT_PAR_MM
    tolerance_droite_pt = max(tolerance_mm, 1.0) * PT_PAR_MM
    largeur_trace_min_pt = SEUILS_DEFAUT["largeur_trace_min_mm"] * PT_PAR_MM
    doc = fitz.open(chemin)
    rapport = {
        "fichier": os.path.abspath(chemin),
        "pages": doc.page_count,
        "seuils": {
            "grille_mm": grille_mm,
            "securite_mm": securite_mm,
            "remplissage_min": remplissage_min,
            "blanc_max_mm": blanc_max_mm,
            "orphelin_min": orphelin_min,
            "tolerance_mm": tolerance_mm,
        },
        "defauts": [],
    }

    def defaut(code, gravite, message, **extra):
        rapport["defauts"].append(
            dict({"code": code, "gravite": gravite, "message": message}, **extra)
        )

    # ---- collecte ---------------------------------------------------------- #
    pages = []
    for numero in range(doc.page_count):
        page = doc[numero]
        lignes, traces = _elements_page(page, largeur_trace_min_pt)
        pages.append(
            {
                "numero": numero + 1,
                "largeur_mm": round(_mm(page.rect.width), 2),
                "hauteur_mm": round(_mm(page.rect.height), 2),
                "lignes": lignes,
                "traces": traces,
                "rect": page.rect,
            }
        )

    if doc.page_count == 0:
        defaut("document_vide", "bloquant", "le PDF ne contient aucune page")

    # bande de contenu : du plus haut au plus bas élément vu dans TOUT le document
    tous_y0 = [e["bbox"][1] for p in pages for e in p["lignes"] + p["traces"]]
    tous_y1 = [e["bbox"][3] for p in pages for e in p["lignes"] + p["traces"]]
    bande_haut = min(tous_y0) if tous_y0 else 0.0
    bande_bas = max(tous_y1) if tous_y1 else 0.0
    hauteur_bande = max(bande_bas - bande_haut, 1e-6)
    rapport["bande_contenu_mm"] = [round(_mm(bande_haut), 2), round(_mm(bande_bas), 2)]

    # ---- 1. remplissage, blancs, débordements ------------------------------ #
    detail = []
    for page in pages:
        rect = page["rect"]
        elements = page["lignes"] + page["traces"]
        intervalles = [
            (max(e["bbox"][1], bande_haut), min(e["bbox"][3], bande_bas))
            for e in elements
            if e["bbox"][3] > bande_haut and e["bbox"][1] < bande_bas
        ]
        fusionnes = _fusionner(
            [(a, b) for a, b in intervalles if b > a], SEUILS_DEFAUT["fusion_pt"]
        )
        couvert = sum(b - a for a, b in fusionnes)
        remplissage = 100.0 * couvert / hauteur_bande

        blancs = []
        curseur = bande_haut
        for a, b in fusionnes:
            if a - curseur > 0:
                blancs.append(a - curseur)
            curseur = max(curseur, b)
        if bande_bas - curseur > 0:
            blancs.append(bande_bas - curseur)
        blanc_max = max(blancs) if blancs else 0.0

        hors = []
        for e in elements:
            x0, y0, x1, y1 = e["bbox"]
            if (
                x0 < securite_mm * PT_PAR_MM - 0.5
                or y0 < securite_mm * PT_PAR_MM - 0.5
                or x1 > rect.width - securite_mm * PT_PAR_MM + 0.5
                or y1 > rect.height - securite_mm * PT_PAR_MM + 0.5
            ):
                hors.append(
                    {
                        "nature": e["nature"],
                        "texte": (e.get("texte", "") or "")[:60],
                        "bbox_mm": [round(_mm(v), 2) for v in e["bbox"]],
                    }
                )

        detail.append(
            {
                "numero": page["numero"],
                "largeur_mm": page["largeur_mm"],
                "hauteur_mm": page["hauteur_mm"],
                "lignes_texte": len(page["lignes"]),
                "traces": len(page["traces"]),
                "remplissage_pct": round(remplissage, 1),
                "blanc_max_mm": round(_mm(blanc_max), 1),
                "hors_zone": hors,
            }
        )

        derniere = page["numero"] == doc.page_count
        if hors:
            defaut(
                "hors_zone_imprimable",
                "bloquant",
                f"page {page['numero']} : {len(hors)} élément(s) hors de la zone "
                f"imprimable (marge de sécurité {securite_mm} mm)",
                page=page["numero"],
                elements=hors[:5],
            )
        if not derniere and remplissage < remplissage_min:
            defaut(
                "page_creuse",
                "bloquant",
                f"page {page['numero']} : remplissage {remplissage:.1f} % "
                f"(< {remplissage_min} %) alors que ce n'est pas la dernière page",
                page=page["numero"],
                remplissage_pct=round(remplissage, 1),
            )
        if not derniere and _mm(blanc_max) > blanc_max_mm:
            defaut(
                "blanc_excessif",
                "bloquant",
                f"page {page['numero']} : {_mm(blanc_max):.1f} mm de blanc vertical "
                f"d'un seul tenant (> {blanc_max_mm} mm)",
                page=page["numero"],
                blanc_max_mm=round(_mm(blanc_max), 1),
            )
        if derniere and doc.page_count > 1 and remplissage < orphelin_min:
            defaut(
                "page_orpheline",
                "bloquant",
                f"page {page['numero']} (dernière) : remplissage {remplissage:.1f} % "
                f"(< {orphelin_min} %) — page orpheline",
                page=page["numero"],
                remplissage_pct=round(remplissage, 1),
            )
    rapport["detail_pages"] = detail

    # ---- 2. unicité du bord gauche ----------------------------------------- #
    candidats = []
    exclus_droite = 0
    exclus_retrait = 0
    for page in pages:
        elements = page["lignes"] + [t for t in page["traces"] if t["large"]]
        if not elements:
            continue
        bord_droit = max(e["bbox"][2] for e in elements)
        bord_gauche = min(e["bbox"][0] for e in elements)
        for e in elements:
            x0, x1 = e["bbox"][0], e["bbox"][2]
            # Tolérance plus large à droite : la chasse latérale du dernier glyphe
            # fait finir le bbox d'une chaîne calée à droite un peu avant la
            # position demandée, alors qu'un filet s'arrête au point exact.
            cale_a_droite = (
                abs(x1 - bord_droit) <= tolerance_droite_pt
                and x0 > bord_gauche + tolerance_pt
            )
            if cale_a_droite:
                exclus_droite += 1
                continue
            # Retrait de citation ASSUME : le texte d'un encadre peut etre en retrait
            # a l'interieur d'un fond dont le bord, lui, est bien sur la grille — c'est
            # ce qui laisse la place au filet lateral du gabarit. Ce n'est un desalignement
            # que si le FOND derive ; le texte en retrait, lui, est un choix de design.
            # Arbitrage utilisateur du 2026-08-31 (chaine PDF de VSCode2, filet ambre du
            # .docx modele client). A 0 mm — le defaut — rien n'est exclu.
            if retrait_citation_mm > 0 and e["nature"] != "trace":
                sur_grille = grille_mm if grille_mm is not None else _mm(bord_gauche)
                if abs(_mm(x0) - (sur_grille + retrait_citation_mm)) <= tolerance_mm:
                    exclus_retrait += 1
                    continue
            candidats.append(
                {
                    "x_mm": round(_mm(x0), 2),
                    "page": page["numero"],
                    "nature": e["nature"],
                    "texte": (e.get("texte", "") or "")[:40],
                }
            )
    groupes = _grouper([c["x_mm"] for c in candidats], tolerance_mm)
    rapport["bords_gauche_mm"] = [
        {"x_mm": x, "elements": n} for x, n in sorted(groupes, key=lambda g: -g[1])
    ]
    rapport["elements_cales_a_droite"] = exclus_droite
    rapport["elements_en_retrait_de_citation"] = exclus_retrait
    if len(groupes) > 1:
        exemples = {}
        for c in candidats:
            cle = min(groupes, key=lambda g: abs(g[0] - c["x_mm"]))[0]
            exemples.setdefault(cle, c)
        defaut(
            "bords_gauche_multiples",
            "bloquant",
            "bord gauche non unique : "
            + " | ".join(
                f"{x} mm ({n} élément(s), ex. {exemples[x]['nature']} "
                f"{exemples[x]['texte']!r})"
                for x, n in sorted(groupes)
            ),
            bords=[x for x, _ in sorted(groupes)],
        )
    elif groupes and grille_mm is not None and abs(groupes[0][0] - grille_mm) > tolerance_mm:
        defaut(
            "grille_decalee",
            "bloquant",
            f"bord gauche unique mais à {groupes[0][0]} mm au lieu de {grille_mm} mm",
            bords=[groupes[0][0]],
        )

    # ---- 3. polices --------------------------------------------------------- #
    polices = _analyser_polices(doc)
    rapport["polices"] = polices
    non_embarquees = [f["basefont"] for f in polices if not f["embarquee"]]
    if not polices:
        defaut("aucune_police", "avertissement", "aucune police déclarée (PDF sans texte ?)")
    elif non_embarquees:
        defaut(
            "police_non_embarquee",
            "bloquant",
            "police(s) non embarquée(s) : "
            + ", ".join(non_embarquees)
            + " — le rendu dépend de la machine du lecteur et l'encodage base-14 "
            "perd tout caractère hors Latin-1",
            polices=non_embarquees,
        )

    # ---- 4. texte : attendus et marqueurs de glyphes manquants -------------- #
    texte_complet = "\n".join(doc[i].get_text() for i in range(doc.page_count))
    rapport["caracteres_extraits"] = len(texte_complet)
    absents = []
    for attendu in attendus or []:
        if attendu and attendu not in texte_complet:
            manquants = sorted(
                {c for c in attendu if c not in texte_complet and not c.isspace()}
            )
            absents.append(
                {
                    "attendu": attendu,
                    "caracteres_absents": manquants,
                    "codepoints": [f"U+{ord(c):04X}" for c in manquants],
                }
            )
    rapport["attendus_absents"] = absents
    if absents:
        defaut(
            "texte_attendu_absent",
            "bloquant",
            "texte demandé mais absent du rendu : "
            + " | ".join(
                f"{a['attendu']!r} (caractères perdus : {', '.join(a['codepoints']) or '—'})"
                for a in absents
            ),
        )

    import re as _re

    marqueurs = sorted(set(_re.findall(r"\[U\+[0-9A-F]{4,6}\]", texte_complet)))
    rapport["marqueurs_glyphes_manquants"] = marqueurs
    if marqueurs:
        defaut(
            "glyphes_signales",
            "avertissement",
            f"{len(marqueurs)} caractère(s) non couvert(s) par la police, "
            f"remplacés par un marqueur visible : {', '.join(marqueurs[:12])}",
            marqueurs=marqueurs,
        )

    # ---- 5. métadonnées, signets, balisage ---------------------------------- #
    meta = doc.metadata or {}
    catalogue = doc.pdf_catalog()
    langue = doc.xref_get_key(catalogue, "Lang")
    struct = doc.xref_get_key(catalogue, "StructTreeRoot")
    markinfo = doc.xref_get_key(catalogue, "MarkInfo")
    signets = doc.get_toc()
    rapport["metadonnees"] = {
        "titre": meta.get("title") or "",
        "auteur": meta.get("author") or "",
        "sujet": meta.get("subject") or "",
        "mots_cles": meta.get("keywords") or "",
        "producteur": meta.get("producer") or "",
        "langue": langue[1] if langue and langue[0] != "null" else "",
    }
    rapport["signets"] = [{"niveau": s[0], "titre": s[1], "page": s[2]} for s in signets]
    rapport["balisage"] = {
        "struct_tree_root": bool(struct and struct[0] != "null"),
        "mark_info": bool(markinfo and markinfo[0] != "null"),
    }

    for champ, libelle in (("titre", "titre"), ("auteur", "auteur")):
        valeur = rapport["metadonnees"][champ]
        if not valeur or valeur.lower() in ("(anonymous)", "anonymous", "untitled"):
            defaut(
                f"metadonnee_{champ}",
                "bloquant",
                f"métadonnée {libelle} absente ou générique : {valeur!r}",
            )
    if not rapport["metadonnees"]["langue"]:
        defaut(
            "langue_absente",
            "bloquant",
            "/Lang absent du catalogue : les lecteurs d'écran ne savent pas dans "
            "quelle langue lire le document",
        )
    if not signets:
        defaut("aucun_signet", "avertissement", "aucun signet (plan) dans le document")
    if not rapport["balisage"]["struct_tree_root"]:
        defaut(
            "pdf_non_balise",
            "bloquant" if exiger_balisage else "avertissement",
            "PDF non balisé (/StructTreeRoot absent) : ordre de lecture non garanti "
            "pour l'accessibilité. reportlab ne produit pas de PDF balisé — un "
            "arbre vide serait pire que rien.",
        )

    doc.close()
    rapport["bloquants"] = sum(1 for d in rapport["defauts"] if d["gravite"] == "bloquant")
    rapport["avertissements"] = sum(
        1 for d in rapport["defauts"] if d["gravite"] == "avertissement"
    )
    rapport["verdict"] = "KO" if rapport["bloquants"] else "OK"
    return rapport


def formater(rapport: dict) -> str:
    """Rapport lisible en console — les nombres d'abord, le verdict à la fin."""
    lignes = [
        f"Fichier      : {rapport['fichier']}",
        f"Pages        : {rapport['pages']}",
        f"Bande contenu: {rapport['bande_contenu_mm'][0]} -> "
        f"{rapport['bande_contenu_mm'][1]} mm",
        "",
        "  page  lignes  tracés  remplissage  blanc max  hors zone",
    ]
    for p in rapport["detail_pages"]:
        lignes.append(
            f"  {p['numero']:>4}  {p['lignes_texte']:>6}  {p['traces']:>6}  "
            f"{p['remplissage_pct']:>10.1f}%  {p['blanc_max_mm']:>7.1f}mm  "
            f"{len(p['hors_zone']):>9}"
        )
    lignes += [
        "",
        "Bords gauche : "
        + ", ".join(
            f"{b['x_mm']} mm ({b['elements']})" for b in rapport["bords_gauche_mm"]
        )
        + f"   [{rapport['elements_cales_a_droite']} élément(s) calé(s) à droite exclus]",
        "Polices      : "
        + (
            ", ".join(
                f"{f['basefont']} {'embarquée' if f['embarquee'] else 'NON EMBARQUÉE'}"
                for f in rapport["polices"]
            )
            or "aucune"
        ),
        "Métadonnées  : "
        + ", ".join(f"{k}={v!r}" for k, v in rapport["metadonnees"].items() if v)
        or "Métadonnées  : aucune",
        f"Signets      : {len(rapport['signets'])}",
        f"Balisage     : StructTreeRoot={rapport['balisage']['struct_tree_root']}, "
        f"MarkInfo={rapport['balisage']['mark_info']}",
        "",
    ]
    if rapport["defauts"]:
        lignes.append("Défauts :")
        for d in rapport["defauts"]:
            marque = "[BLOQUANT]" if d["gravite"] == "bloquant" else "[avertis. ]"
            lignes.append(f"  {marque} {d['code']} — {d['message']}")
    else:
        lignes.append("Aucun défaut.")
    lignes.append("")
    lignes.append(
        f"Verdict : {rapport['verdict']} "
        f"({rapport['bloquants']} bloquant(s), {rapport['avertissements']} avertissement(s))"
    )
    lignes.append(
        "Rappel : ce rapport ne remplace pas l'ouverture des pages rastérisées."
    )
    return "\n".join(lignes)


def main(argv=None) -> int:
    parseur = argparse.ArgumentParser(
        description="Mesure la qualité géométrique et typographique d'un PDF.",
    )
    parseur.add_argument("pdf", help="chemin du fichier .pdf à vérifier")
    parseur.add_argument(
        "--grille-mm", type=float, default=None,
        help="abscisse gauche attendue (mm) ; sinon on vérifie seulement l'unicité",
    )
    parseur.add_argument("--securite-mm", type=float, default=SEUILS_DEFAUT["securite_mm"])
    parseur.add_argument(
        "--remplissage-min", type=float, default=SEUILS_DEFAUT["remplissage_min"]
    )
    parseur.add_argument("--blanc-max-mm", type=float, default=SEUILS_DEFAUT["blanc_max_mm"])
    parseur.add_argument("--orphelin-min", type=float, default=SEUILS_DEFAUT["orphelin_min"])
    parseur.add_argument("--tolerance-mm", type=float, default=SEUILS_DEFAUT["tolerance_mm"])
    parseur.add_argument(
        "--retrait-citation-mm", type=float, default=0.0,
        help="retrait horizontal ASSUME du texte a l'interieur d'un encadre, en mm : "
             "il n'est plus compte comme un bord gauche distinct (0 = aucun retrait tolere)")
    parseur.add_argument(
        "--attendu", action="append", default=[],
        help="chaîne qui doit apparaître dans le rendu (répétable)",
    )
    parseur.add_argument(
        "--attendus-fichier",
        help="fichier texte UTF-8 : une chaîne attendue par ligne",
    )
    parseur.add_argument(
        "--exiger-balisage", action="store_true",
        help="rend /StructTreeRoot bloquant au lieu d'un avertissement",
    )
    parseur.add_argument("--json", action="store_true", help="sortie JSON brute")
    args = parseur.parse_args(argv)

    attendus = list(args.attendu)
    if args.attendus_fichier:
        with open(args.attendus_fichier, encoding="utf-8") as f:
            attendus += [l.rstrip("\n") for l in f if l.strip()]

    try:
        rapport = analyser(
            args.pdf,
            grille_mm=args.grille_mm,
            securite_mm=args.securite_mm,
            remplissage_min=args.remplissage_min,
            blanc_max_mm=args.blanc_max_mm,
            orphelin_min=args.orphelin_min,
            tolerance_mm=args.tolerance_mm,
            retrait_citation_mm=args.retrait_citation_mm,
            attendus=attendus,
            exiger_balisage=args.exiger_balisage,
        )
    except PyMuPDFAbsent as exc:
        print(f"IMPOSSIBLE DE VÉRIFIER : {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"fichier introuvable : {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        print(formater(rapport))
    return 1 if rapport["bloquants"] else 0


if __name__ == "__main__":
    sys.exit(main())
