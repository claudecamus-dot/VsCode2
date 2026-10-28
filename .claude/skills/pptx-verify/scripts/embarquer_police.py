"""Embarque des polices TrueType dans un .pptx (parties ppt/fonts/*.fntdata en EOT non compresse + p:embeddedFontLst).

OUTIL OPT-IN, GENERIQUE, jamais applique a un livrable sans decision. Dry-run par defaut : `--appliquer` ecrit.
Limites documentees :
- fsType=4 (apercu et impression) : PowerPoint ouvre le deck en LECTURE SEULE chez un destinataire sans la police ;
- fsType=2 (licence restreinte) ou bit 0x200 (bitmap seul) : REFUS, sauf `--accepter-licence` (decision humaine) ;
- la licence d'incorporation de chaque police reste A VALIDER par l'humain ;
- le resultat n'est JAMAIS ouvert dans PowerPoint par cet outil : `integrite_pptx` passe, seul PowerPoint dit si le fichier est accepte.
L'entree n'est jamais modifiee (ecriture dans --sortie, distinct de l'entree).

  py embarquer_police.py deck.pptx --faces faces.json --dossier-polices DIR --sortie out.pptx [--appliquer] [--accepter-licence]

faces.json : {"Famille": {"regular": "a.ttf", "bold": "b.ttf", "italic": "...", "boldItalic": "..."}} (chemins relatifs a
--dossier-polices ou absolus). Codes de sortie : 0 ok (ou dry-run propre), 1 refus (licence, integrite, rien a embarquer), 2 usage/entree invalide.
"""
import argparse
import importlib.util
import json
import os
import re
import struct
import sys
import zipfile
from xml.sax.saxutils import escape

REL_FONT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"
ROLES = ("regular", "bold", "italic", "boldItalic")
MAX_LIGNES = 20


def lire_fstype(chemin):
    from fontTools.ttLib import TTFont
    return TTFont(chemin)["OS/2"].fsType


def risque_licence(fstype):
    """'refus' (restreint/bitmap seul), 'lecture-seule' (apercu et impression), ou None."""
    if fstype & 0x2 or fstype & 0x200:
        return "refus"
    if fstype & 0x4:
        return "lecture-seule"
    return None


def en_eot(chemin_ttf):
    """Octets EOT (version 0x00020001, sans compression ni chiffrement) pour un fichier TrueType."""
    from fontTools.ttLib import TTFont
    with open(chemin_ttf, "rb") as fh:
        data = fh.read()
    f = TTFont(chemin_ttf)
    os2, name, head = f["OS/2"], f["name"], f["head"]
    chaine = lambda i: (name.getDebugName(i) or "").encode("utf-16-le")
    p = os2.panose
    panose = bytes([p.bFamilyType, p.bSerifStyle, p.bWeight, p.bProportion, p.bContrast,
                    p.bStrokeVariation, p.bArmStyle, p.bLetterForm, p.bMidline, p.bXHeight])
    italique = 1 if (os2.fsSelection & 1) else 0
    corps = b"".join([
        struct.pack("<I", 0x00020001), struct.pack("<I", 0), panose, struct.pack("<B", 1), struct.pack("<B", italique),
        struct.pack("<I", os2.usWeightClass), struct.pack("<H", os2.fsType), struct.pack("<H", 0x504C),
        struct.pack("<IIII", os2.ulUnicodeRange1, os2.ulUnicodeRange2, os2.ulUnicodeRange3, os2.ulUnicodeRange4),
        struct.pack("<II", getattr(os2, "ulCodePageRange1", 0), getattr(os2, "ulCodePageRange2", 0)),
        struct.pack("<I", head.checkSumAdjustment), b"\0" * 16, struct.pack("<H", 0),
    ])
    for i in (1, 2, 5, 4):  # famille, style, version, nom complet
        s = chaine(i)
        corps += struct.pack("<H", len(s)) + s + struct.pack("<H", 0)
    corps += struct.pack("<H", 0)  # RootStringSize = 0
    total = 8 + len(corps) + len(data)
    return struct.pack("<II", total, len(data)) + corps + data


def charger_faces(chemin):
    with open(chemin, encoding="utf-8") as fh:
        faces = json.load(fh)
    if not isinstance(faces, dict) or not faces:
        raise ValueError("faces.json : objet {famille: {role: fichier}} non vide attendu")
    for fam, roles in faces.items():
        if not isinstance(roles, dict) or not roles:
            raise ValueError(f"faces.json : '{fam}' sans roles")
        for r, f in roles.items():
            if r not in ROLES or not isinstance(f, str):
                raise ValueError(f"faces.json : role invalide '{r}' pour '{fam}' (attendus : {', '.join(ROLES)})")
    return faces


def _confiner(dossier, rel):
    """Chemin TTF obligatoirement DANS dossier (ni absolu, ni '..' qui en sort, liens resolus)."""
    if os.path.isabs(rel) or os.path.splitdrive(rel)[0]:
        raise ValueError(f"faces.json : chemin absolu refuse ({rel})")
    base = os.path.realpath(dossier)
    chemin = os.path.realpath(os.path.join(base, rel))
    try:
        dedans = os.path.commonpath([base, chemin]) == base
    except ValueError:
        dedans = False
    if not dedans:
        raise ValueError(f"faces.json : chemin hors de --dossier-polices refuse ({rel})")
    return chemin


def deja_embarque(entree):
    with zipfile.ZipFile(entree) as z:
        return b"embeddedFontLst" in z.read("ppt/presentation.xml") or any(n.startswith("ppt/fonts/") for n in z.namelist())


def planifier(entree, faces, dossier):
    """Polices utilisees dans les slides -> liste de (famille, role, chemin, fstype). Lecture seule."""
    with zipfile.ZipFile(entree) as z:
        slides = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        blob = b"".join(z.read(n) for n in slides)
    plan, manquants = [], []
    for fam in sorted(faces):
        if f'typeface="{escape(fam, {chr(34): "&quot;"})}"'.encode("utf-8") not in blob:
            continue
        for role in ROLES:
            if role not in faces[fam]:
                continue
            chemin = _confiner(dossier, faces[fam][role])
            if not os.path.isfile(chemin):
                manquants.append(chemin)
                continue
            plan.append((fam, role, chemin, lire_fstype(chemin)))
    return plan, manquants


def embarquer(entree, sortie, plan):
    """Ecrit sortie = entree + polices du plan. Retourne les parties ajoutees."""
    with zipfile.ZipFile(entree) as zin:
        pres = zin.read("ppt/presentation.xml").decode("utf8")
        rels = zin.read("ppt/_rels/presentation.xml.rels").decode("utf8")
        types = zin.read("[Content_Types].xml").decode("utf8")
        suivant = max([int(x) for x in re.findall(r'Id="rId(\d+)"', rels)] or [0]) + 1
        nouveaux, par_fam, k = {}, {}, 1
        for fam, role, chemin, _fs in plan:
            cible = f"fonts/font{k}.fntdata"
            nouveaux["ppt/" + cible] = en_eot(chemin)
            rels = rels.replace("</Relationships>", f'<Relationship Id="rId{suivant}" Type="{REL_FONT}" Target="{cible}"/></Relationships>')
            par_fam.setdefault(fam, "")
            par_fam[fam] += f'<p:{role} r:id="rId{suivant}"/>'
            suivant += 1
            k += 1
        liste = "".join(
            f'<p:embeddedFont><p:font typeface="{escape(f, {chr(34): "&quot;"})}" pitchFamily="34" charset="0"/>{r}</p:embeddedFont>'
            for f, r in par_fam.items())
        pres = re.sub(r"(<p:notesSz[^>]*/>)", lambda m: m.group(1) + "<p:embeddedFontLst>" + liste + "</p:embeddedFontLst>", pres, count=1)
        racine = re.search(r"<p:presentation [^>]*>", pres).group(0)
        nouvelle = racine
        for attr in ("embedTrueTypeFonts", "saveSubsetFonts"):
            nouvelle = re.sub(r'\s%s="[^"]*"' % attr, "", nouvelle)
        nouvelle = nouvelle.replace("<p:presentation ", '<p:presentation embedTrueTypeFonts="1" saveSubsetFonts="1" ', 1)
        pres = pres.replace(racine, nouvelle, 1)
        if 'Extension="fntdata"' not in types:
            types = types.replace("<Override ", '<Default Extension="fntdata" ContentType="application/x-fontdata"/><Override ', 1)
        with zipfile.ZipFile(sortie, "w", zipfile.ZIP_DEFLATED) as zout:
            for it in zin.infolist():
                data = zin.read(it.filename)
                if it.filename == "ppt/presentation.xml":
                    data = pres.encode("utf8")
                elif it.filename == "ppt/_rels/presentation.xml.rels":
                    data = rels.encode("utf8")
                elif it.filename == "[Content_Types].xml":
                    data = types.encode("utf8")
                zout.writestr(it, data)
            for n, d in nouveaux.items():
                zout.writestr(n, d)
    return sorted(nouveaux)


def _integrite():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "integrite_pptx.py")
    spec = importlib.util.spec_from_file_location("integrite_pptx", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.controler


def _dire(lignes):
    for ln in lignes[:MAX_LIGNES]:
        print(ln)
    if len(lignes) > MAX_LIGNES:
        print(f"... +{len(lignes) - MAX_LIGNES} ligne(s) non affichee(s)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("entree")
    ap.add_argument("--faces", required=True)
    ap.add_argument("--dossier-polices", required=True)
    ap.add_argument("--sortie")
    ap.add_argument("--appliquer", action="store_true")
    ap.add_argument("--accepter-licence", action="store_true")
    ap.add_argument("--ecraser", action="store_true", help="autorise le remplacement d'une --sortie deja existante")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    try:
        import fontTools  # noqa: F401
    except ImportError:
        print("ERREUR : fontTools requis (pip install fonttools)")
        return 2
    try:
        faces = charger_faces(a.faces)
        plan, manquants = planifier(a.entree, faces, a.dossier_polices)
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as e:
        print(f"ERREUR entree : {type(e).__name__}: {e}")
        return 2
    if deja_embarque(a.entree):
        print("REFUS : polices deja embarquees dans ce deck (embeddedFontLst ou ppt/fonts/) : pas de reembarquement")
        return 1
    lignes = [f"{fam} / {role} : {os.path.basename(ch)} (fsType={fs})" for fam, role, ch, fs in plan]
    lignes += [f"MANQUANT : {m}" for m in manquants]
    risques = [(fam, role, fs) for fam, role, _c, fs in plan if risque_licence(fs)]
    refus = [r for r in risques if risque_licence(r[2]) == "refus"]
    ro = [r for r in risques if risque_licence(r[2]) == "lecture-seule"]
    if not plan:
        _dire(lignes + ["RIEN A EMBARQUER : aucune famille de faces.json utilisee dans les slides avec un fichier present"])
        return 1
    if refus:
        lignes.append("AVERTISSEMENT LICENCE : fsType restreint (valeurs " + ", ".join(sorted({str(r[2]) for r in refus})) + ") : embarquement refuse sauf --accepter-licence")
    if ro:
        lignes.append("AVERTISSEMENT : fsType=4 (apercu/impression) : lecture seule probable chez un destinataire sans la police ; licence A VALIDER")
    _dire(lignes)
    if refus and not a.accepter_licence:
        return 1
    if not a.appliquer:
        print(f"DRY-RUN : {len(plan)} police(s) seraient embarquees. --appliquer --sortie OUT pour ecrire.")
        return 0
    if not a.sortie:
        print("ERREUR : --sortie requis avec --appliquer")
        return 2
    if os.path.exists(a.sortie):
        if os.path.samefile(a.sortie, a.entree):
            print("ERREUR : --sortie designe le deck d'entree (meme fichier, lien dur compris) : jamais modifie")
            return 2
        if not a.ecraser:
            print("ERREUR : --sortie existe deja ; --ecraser pour la remplacer")
            return 2
    elif os.path.normcase(os.path.abspath(a.sortie)) == os.path.normcase(os.path.abspath(a.entree)):
        print("ERREUR : --sortie ne doit pas etre le deck d'entree")
        return 2
    tmp = a.sortie + f".{os.getpid()}.tmp"
    try:
        ajoutes = embarquer(a.entree, tmp, plan)
        pb = _integrite()(tmp)["problemes"]
        if pb:
            os.remove(tmp)
            _dire(["INTEGRITE KO (temporaire supprime, sortie intacte) :"] + pb)
            return 1
        os.replace(tmp, a.sortie)
    except (OSError, KeyError, AttributeError, zipfile.BadZipFile) as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        print(f"ERREUR ecriture : {type(e).__name__}: {e}")
        return 2
    print(f"{len(ajoutes)} police(s) embarquee(s) dans {a.sortie}. Integrite OPC OK. NON ouvert dans PowerPoint : a verifier par l'humain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
