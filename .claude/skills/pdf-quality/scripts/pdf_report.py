"""Génération de PDF de qualité sur gabarit — la brique de référence de la flotte.

Écrite contre un audit par exécution réelle (2026-08-31) de la seule chaîne PDF
de la flotte (VSCode2, reportlab) : 6 PDF produits, 22 pages rastérisées et
regardées. Les sept défauts mesurés ce jour-là sont le cahier des charges de ce
module ; chacun a son antidote ici et son test dans
``tests/test_pdf_quality.py``.

Ce que ce module garantit
-------------------------
1. **Aucun bloc insécable.** Un verbatim de 12 690 caractères faisait lever
   ``LayoutError`` et rendait un HTTP 500, parce qu'il était posé dans un
   ``Table`` mono-cellule — un ``Table`` ne se coupe pas au milieu d'une cellule.
   Ici l'encadré (:func:`encadre_secable`) est une *suite de Paragraph* teintés :
   reportlab sait couper un ``Paragraph`` entre deux lignes, à n'importe quelle
   longueur. Aucun ``Table``, aucun ``KeepTogether`` n'entre dans le flux de
   texte.
2. **Pas de page à 40 % ni d'orpheline à 4 %.** Corollaire du point 1 : le
   contenu coule au lieu d'être renvoyé en bloc à la page suivante. Le taux de
   remplissage se *mesure* avec ``pdf_verify.py``, il ne se déclare pas.
3. **Rien ne disparaît en silence.** Les polices base-14 de reportlab sont
   encodées en Latin-1 : « Nguyễn Thị Mai » y devient « NguyIn ThI Mai » sans
   exception ni avertissement. Ici on enregistre une police Unicode réelle
   (:func:`enregistrer_police`), et tout caractère absent de sa table est
   *signalé* (:func:`caracteres_manquants`) puis remplacé par un marqueur
   visible ``[U+XXXX]`` — jamais perdu sans trace. Le repli, quand aucune fonte
   n'est trouvée sur la machine, est documenté et remonté dans le rapport.
4. **Une seule abscisse gauche.** L'audit trouvait trois bords sur une même
   page : en-tête à 20,0 mm (canvas), texte à 22,1 mm (padding de ``Frame``
   reportlab de 6 pt jamais compensé), encadré à 25,0 mm (``colWidths=[160*mm]``
   en dur). Ici une seule constante, ``Marges.gauche_mm``, sert au canvas, au
   ``Frame`` (dont tous les paddings sont mis à zéro), aux filets et au fond des
   encadrés. Les encadrés ont un padding gauche **nul** par construction : le
   fond teinté commence exactement là où commence le texte.
5. **Métadonnées et signets.** Titre, auteur, sujet, mots-clés, ``/Lang``,
   ``displayDocTitle``, plus un plan de signets alimenté par les titres.
6. **Gabarit paramétrable.** Palette, typographie, marges, en-tête et pied sont
   des données (:class:`Gabarit`), passées en argument ou chargées d'un JSON
   (:func:`gabarit_depuis_dict`). Rien de tout cela n'est codé en dur dans le
   code de mise en page.

Ce que ce module NE garantit PAS
--------------------------------
- Le **balisage** (``/StructTreeRoot``, PDF/UA) : reportlab ne produit pas de
  PDF balisé et on refuse d'en simuler un — un ``StructTreeRoot`` vide serait un
  mensonge pour les lecteurs d'écran. ``pdf_verify.py`` le signale en
  avertissement, pas en défaut bloquant, sauf ``--exiger-balisage``.
- Les **emoji couleur** : aucune fonte monochrome ne les porte ; ils sortent en
  ``[U+1F642]``, ce qui est le comportement voulu (signaler, pas perdre).
- Le **jugement visuel**. Un vérificateur ne remplace pas le fait de regarder
  les pages rastérisées.

API publique
------------
- :class:`Gabarit`, :class:`Palette`, :class:`Typographie`, :class:`Marges`
- :data:`GABARIT_REFERENCE`, :func:`gabarit_depuis_dict`
- :func:`enregistrer_police` -> :class:`PoliceEnregistree`
- :func:`caracteres_manquants`, :func:`baliser_caracteres`
- :func:`encadre_secable`, :func:`construire_styles`
- :func:`construire_pdf` -> :class:`RapportRendu`
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, A3, LETTER, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFError
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    HRFlowable,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)

__all__ = [
    "Palette",
    "Typographie",
    "Marges",
    "Gabarit",
    "GABARIT_REFERENCE",
    "gabarit_depuis_dict",
    "PoliceEnregistree",
    "CaracteresNonRendus",
    "PoliceIntrouvable",
    "enregistrer_police",
    "caracteres_manquants",
    "baliser_caracteres",
    "construire_styles",
    "encadre_secable",
    "construire_pdf",
    "RapportRendu",
    "FORMATS",
]

# --------------------------------------------------------------------------- #
# Gabarit — tout ce qui est « design » est une donnée, jamais une constante du
# code de mise en page. C'est le manque relevé au point 6 de l'audit : la chaîne
# PPT du même projet avait un mécanisme de template client complet, la chaîne PDF
# n'avait rien.
# --------------------------------------------------------------------------- #

FORMATS = {
    "A4": A4,
    "A4-paysage": landscape(A4),
    "A3": A3,
    "LETTER": LETTER,
}


@dataclass(frozen=True)
class Palette:
    """Couleurs du gabarit, en hexadécimal ``#rrggbb``."""

    texte: str = "#1c1c1c"
    titre: str = "#0b3d5c"
    soustitre: str = "#155f83"
    accent: str = "#c8102e"
    filet: str = "#b9c4cc"
    fond_encadre: str = "#eef3f7"
    texte_discret: str = "#5a6b76"

    def couleur(self, nom: str):
        """Rend l'objet couleur reportlab d'un champ de la palette."""
        return colors.HexColor(getattr(self, nom))


@dataclass(frozen=True)
class Typographie:
    """Familles candidates et échelle typographique.

    ``familles`` est une liste ordonnée de dictionnaires
    ``{"nom": ..., "regulier": "DejaVuSans.ttf", "gras": "DejaVuSans-Bold.ttf"}``.
    La première famille effectivement trouvée sur la machine gagne ; si aucune
    n'est trouvée, :func:`enregistrer_police` replie sur Helvetica (base-14,
    Latin-1) **en le disant**.
    """

    familles: tuple = (
        {"nom": "DejaVuSans", "regulier": "DejaVuSans.ttf", "gras": "DejaVuSans-Bold.ttf"},
        {"nom": "SegoeUI", "regulier": "segoeui.ttf", "gras": "segoeuib.ttf"},
        {"nom": "NotoSans", "regulier": "NotoSans-Regular.ttf", "gras": "NotoSans-Bold.ttf"},
        {"nom": "Arial", "regulier": "arial.ttf", "gras": "arialbd.ttf"},
    )
    taille_titre: float = 17.0
    taille_soustitre: float = 12.5
    taille_corps: float = 9.8
    taille_petit: float = 8.0
    interligne: float = 1.42
    espace_apres_titre: float = 7.0
    espace_apres_paragraphe: float = 5.0


@dataclass(frozen=True)
class Marges:
    """Marges en millimètres. ``gauche_mm`` est LA grille : une seule abscisse.

    ``securite_mm`` est la marge de sécurité d'impression : rien, pas même le
    fond d'un encadré ou un filet d'en-tête, ne doit sortir de la zone
    ``[securite_mm, largeur - securite_mm]``. ``pdf_verify.py`` le vérifie.
    """

    gauche_mm: float = 20.0
    droite_mm: float = 20.0
    haut_mm: float = 24.0
    bas_mm: float = 20.0
    entete_mm: float = 12.0  # ligne de base de l'en-tête, depuis le haut
    pied_mm: float = 12.0  # ligne de base du pied, depuis le bas
    securite_mm: float = 8.0


@dataclass(frozen=True)
class Gabarit:
    """Le « template de référence ». Tout est ici, rien n'est codé en dur ailleurs."""

    nom: str = "reference"
    format_page: str = "A4"
    palette: Palette = field(default_factory=Palette)
    typo: Typographie = field(default_factory=Typographie)
    marges: Marges = field(default_factory=Marges)
    entete_gauche: str = ""
    entete_droite: str = ""
    pied_gauche: str = ""
    pied_droite: str = "Page {page} / {total}"
    filet_entete: bool = True
    filet_pied: bool = True
    # Métadonnées PDF (défaut n°5 de l'audit : title='(anonymous)')
    titre: str = "Document"
    auteur: str = "Inconnu"
    sujet: str = ""
    mots_cles: str = ""
    langue: str = "fr-FR"
    producteur: str = "pdf-quality (hub de supervision)"
    # Politique de caractères non couverts : signaler | strict | ignorer
    politique_glyphes: str = "signaler"

    # -- dérivés, en points, tous issus des mêmes nombres -------------------- #
    @property
    def taille_page(self):
        if self.format_page not in FORMATS:
            raise ValueError(
                f"format_page inconnu : {self.format_page!r} (connus : {sorted(FORMATS)})"
            )
        return FORMATS[self.format_page]

    @property
    def largeur(self) -> float:
        return self.taille_page[0]

    @property
    def hauteur(self) -> float:
        return self.taille_page[1]

    @property
    def grille_gauche(self) -> float:
        """L'UNIQUE abscisse gauche, en points. Canvas, Frame, filets, encadrés."""
        return self.marges.gauche_mm * mm

    @property
    def grille_droite(self) -> float:
        """L'unique abscisse droite (bord droit du bloc de texte), en points."""
        return self.largeur - self.marges.droite_mm * mm

    @property
    def largeur_utile(self) -> float:
        return self.grille_droite - self.grille_gauche


GABARIT_REFERENCE = Gabarit()


def gabarit_depuis_dict(donnees: dict) -> Gabarit:
    """Construit un :class:`Gabarit` depuis un dict (typiquement un JSON client).

    Les sous-sections ``palette``, ``typo`` et ``marges`` sont fusionnées avec
    les valeurs de référence : un JSON client n'a besoin de citer que ce qu'il
    change.
    """
    donnees = dict(donnees or {})
    palette = Palette(**{**vars(GABARIT_REFERENCE.palette), **donnees.pop("palette", {})})
    typo_brut = {**vars(GABARIT_REFERENCE.typo), **donnees.pop("typo", {})}
    if isinstance(typo_brut.get("familles"), list):
        typo_brut["familles"] = tuple(typo_brut["familles"])
    typo = Typographie(**typo_brut)
    marges = Marges(**{**vars(GABARIT_REFERENCE.marges), **donnees.pop("marges", {})})
    inconnus = set(donnees) - {f for f in Gabarit.__dataclass_fields__}
    if inconnus:
        raise ValueError(f"clés de gabarit inconnues : {sorted(inconnus)}")
    return Gabarit(palette=palette, typo=typo, marges=marges, **donnees)


# --------------------------------------------------------------------------- #
# Polices — défaut n°3 : « tout caractère hors Latin-1 est perdu SANS exception
# ni avertissement ». La matière première est du texte collé depuis Teams.
# --------------------------------------------------------------------------- #

REPERTOIRES_POLICES = (
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype",
    "/usr/share/fonts",
    "/Library/Fonts",
    os.path.expanduser("~/.fonts"),
)


class PoliceIntrouvable(RuntimeError):
    """Aucune famille du gabarit n'a été trouvée ET le repli est interdit."""


class CaracteresNonRendus(ValueError):
    """Des caractères du texte ne sont pas couverts par la police (politique stricte)."""

    def __init__(self, manquants):
        self.manquants = list(manquants)
        apercu = ", ".join(f"{m['caractere']!r} U+{m['codepoint']:04X}" for m in self.manquants[:8])
        super().__init__(
            f"{len(self.manquants)} caractère(s) non couvert(s) par la police : {apercu}"
        )


@dataclass(frozen=True)
class PoliceEnregistree:
    """Résultat de :func:`enregistrer_police`.

    ``repli`` vaut True quand on est retombé sur une base-14 Latin-1 : dans ce
    cas ``raison`` dit précisément pourquoi, et ``couverture`` se limite à
    Latin-1 — donc tout le reste sera signalé au lieu d'être perdu.
    """

    nom: str
    nom_gras: str
    chemin: str
    repli: bool
    raison: str
    couverture: frozenset

    def couvre(self, caractere: str) -> bool:
        return ord(caractere) in self.couverture


def _chercher_fichier_police(nom_fichier: str):
    """Cherche une fonte par chemin absolu puis dans les répertoires système."""
    if os.path.isabs(nom_fichier) and os.path.exists(nom_fichier):
        return nom_fichier
    for repertoire in REPERTOIRES_POLICES:
        if not repertoire:
            continue
        chemin = os.path.join(repertoire, nom_fichier)
        if os.path.exists(chemin):
            return chemin
    return None


def _couverture_ttf(chemin: str) -> frozenset:
    """Codepoints réellement portés par la fonte, lus dans sa table cmap."""
    face = TTFont("_sonde_couverture", chemin).face
    return frozenset(face.charToGlyph.keys())


_COUVERTURE_LATIN1 = frozenset(range(0x20, 0x7F)) | frozenset(range(0xA0, 0x100))


def enregistrer_police(gabarit: Gabarit, autoriser_repli: bool = True) -> PoliceEnregistree:
    """Enregistre la première famille Unicode disponible ; replie en le disant.

    Parcourt ``gabarit.typo.familles`` dans l'ordre. La première dont le fichier
    régulier existe et se charge est enregistrée auprès de reportlab (régulier +
    gras si le gras est trouvé, sinon le gras retombe sur le régulier).

    Si aucune n'est trouvée et ``autoriser_repli`` est vrai, on retombe sur
    ``Helvetica`` (base-14, non embarquée, encodage Latin-1) : c'est exactement
    la configuration qui a produit « NguyIn ThI Mai » dans l'audit, donc on la
    marque ``repli=True`` avec une ``couverture`` limitée à Latin-1 pour que
    :func:`baliser_caracteres` signale tout le reste.
    """
    echecs = []
    for famille in gabarit.typo.familles:
        chemin = _chercher_fichier_police(famille["regulier"])
        if not chemin:
            echecs.append(f"{famille['nom']} : {famille['regulier']} introuvable")
            continue
        try:
            pdfmetrics.registerFont(TTFont(famille["nom"], chemin))
        except (TTFError, OSError) as exc:  # fonte corrompue ou bitmap
            echecs.append(f"{famille['nom']} : {exc}")
            continue
        nom_gras = famille["nom"]
        chemin_gras = _chercher_fichier_police(famille.get("gras") or "")
        if chemin_gras:
            try:
                pdfmetrics.registerFont(TTFont(famille["nom"] + "-Gras", chemin_gras))
                nom_gras = famille["nom"] + "-Gras"
            except (TTFError, OSError) as exc:
                echecs.append(f"{famille['nom']} gras : {exc}")
        pdfmetrics.registerFontFamily(
            famille["nom"], normal=famille["nom"], bold=nom_gras,
            italic=famille["nom"], boldItalic=nom_gras,
        )
        return PoliceEnregistree(
            nom=famille["nom"],
            nom_gras=nom_gras,
            chemin=chemin,
            repli=False,
            raison="",
            couverture=_couverture_ttf(chemin),
        )

    raison = (
        "aucune famille Unicode du gabarit n'a été trouvée sur cette machine "
        f"({' ; '.join(echecs) if echecs else 'liste de familles vide'})"
    )
    if not autoriser_repli:
        raise PoliceIntrouvable(raison)
    return PoliceEnregistree(
        nom="Helvetica",
        nom_gras="Helvetica-Bold",
        chemin="",
        repli=True,
        raison=raison + " — repli sur Helvetica base-14, NON embarquée, Latin-1 seulement",
        couverture=_COUVERTURE_LATIN1,
    )


def caracteres_manquants(texte: str, police: PoliceEnregistree) -> list:
    """Liste, sans doublon et dans l'ordre d'apparition, ce que la police ne porte pas.

    Rend des dicts ``{"caractere", "codepoint", "nom", "occurrences"}``. Les
    caractères de contrôle usuels (retour ligne, tabulation) sont ignorés.
    """
    import unicodedata

    vus = {}
    for caractere in texte:
        if caractere in "\n\r\t":
            continue
        point = ord(caractere)
        if point in police.couverture:
            continue
        entree = vus.get(point)
        if entree is None:
            vus[point] = {
                "caractere": caractere,
                "codepoint": point,
                "nom": unicodedata.name(caractere, "?"),
                "occurrences": 1,
            }
        else:
            entree["occurrences"] += 1
    return list(vus.values())


def baliser_caracteres(texte: str, police: PoliceEnregistree, politique: str = "signaler"):
    """Rend ``(texte_sûr, manquants)`` selon la politique de glyphes.

    - ``"signaler"`` (défaut) : chaque caractère non couvert devient un marqueur
      ASCII visible ``[U+XXXX]``. Rien n'est perdu en silence — le lecteur du
      PDF voit qu'il manque quelque chose, et le rapport dit quoi.
    - ``"strict"`` : lève :class:`CaracteresNonRendus`.
    - ``"ignorer"`` : laisse le texte tel quel (il sera mutilé au rendu) mais
      remonte quand même la liste.
    """
    manquants = caracteres_manquants(texte, police)
    if not manquants:
        return texte, []
    if politique == "strict":
        raise CaracteresNonRendus(manquants)
    if politique == "ignorer":
        return texte, manquants
    if politique != "signaler":
        raise ValueError(f"politique_glyphes inconnue : {politique!r}")
    absents = {m["codepoint"] for m in manquants}
    return (
        "".join(c if ord(c) not in absents else f"[U+{ord(c):04X}]" for c in texte),
        manquants,
    )


# --------------------------------------------------------------------------- #
# Styles et encadré sécable — défauts n°1, 2 et 4.
# --------------------------------------------------------------------------- #

# Padding gauche NUL : c'est la clef du défaut n°4. Le fond de l'encadré et son
# texte commencent tous deux exactement sur la grille. Le confort visuel vient
# du haut, du bas et de la droite, jamais de la gauche.
# Le padding droit est compensé par un ``rightIndent`` de même valeur : reportlab
# dessine le fond en ``self.width - (leftIndent + rightIndent) + lbp + rbp``, donc
# avec rightIndent == rbp le fond s'arrête EXACTEMENT sur la grille droite au lieu
# de la dépasser de 8 pt (mesuré : le fond sortait à 192,8 mm pour une grille
# droite à 190,0 mm).
PADDING_ENCADRE = (6.0, 8.0, 6.0, 0.0)  # (haut, droite, bas, gauche), convention CSS


def construire_styles(gabarit: Gabarit, police: PoliceEnregistree) -> dict:
    """Fabrique les styles du gabarit. Aucune valeur en dur : tout vient du gabarit."""
    typo, pal = gabarit.typo, gabarit.palette

    def base(nom, taille, couleur, **kw):
        defauts = dict(
            fontName=police.nom,
            # Sans ``bulletFontName``, ParagraphStyle garde Helvetica pour les
            # puces : une base-14 NON embarquée réapparaît dans les ressources
            # dès qu'une liste est présente (mesuré par pdf_verify sur un
            # document par ailleurs 100 % DejaVu).
            bulletFontName=police.nom,
            fontSize=taille,
            leading=taille * typo.interligne,
            textColor=pal.couleur(couleur),
            alignment=TA_LEFT,
            leftIndent=0,  # jamais d'indentation : une seule abscisse
            rightIndent=0,
            firstLineIndent=0,
            spaceBefore=0,
            spaceAfter=0,
            allowOrphans=0,
            splitLongWords=1,
        )
        defauts.update(kw)
        return ParagraphStyle(nom, **defauts)

    styles = {
        "titre": base(
            "titre", typo.taille_titre, "titre",
            fontName=police.nom_gras, spaceAfter=typo.espace_apres_titre,
        ),
        "soustitre": base(
            "soustitre", typo.taille_soustitre, "soustitre",
            fontName=police.nom_gras, spaceBefore=typo.espace_apres_titre,
            spaceAfter=typo.espace_apres_paragraphe,
        ),
        "corps": base(
            "corps", typo.taille_corps, "texte",
            spaceAfter=typo.espace_apres_paragraphe,
        ),
        "petit": base("petit", typo.taille_petit, "texte_discret", spaceAfter=2),
        "encadre_titre": base(
            "encadre_titre", typo.taille_corps, "accent", fontName=police.nom_gras,
            backColor=pal.couleur("fond_encadre"),
            borderPadding=(PADDING_ENCADRE[0], PADDING_ENCADRE[1], 2.0, PADDING_ENCADRE[3]),
            rightIndent=PADDING_ENCADRE[1],
            spaceAfter=0,
        ),
        "encadre_corps": base(
            "encadre_corps", typo.taille_corps, "texte",
            backColor=pal.couleur("fond_encadre"),
            borderPadding=(2.0, PADDING_ENCADRE[1], 2.0, PADDING_ENCADRE[3]),
            rightIndent=PADDING_ENCADRE[1],
            spaceAfter=0,
        ),
        "encadre_fin": base(
            "encadre_fin", typo.taille_corps, "texte",
            backColor=pal.couleur("fond_encadre"),
            borderPadding=(2.0, PADDING_ENCADRE[1], PADDING_ENCADRE[2], PADDING_ENCADRE[3]),
            rightIndent=PADDING_ENCADRE[1],
            spaceAfter=typo.espace_apres_paragraphe,
        ),
        "puce": base(
            "puce", typo.taille_corps, "texte",
            bulletIndent=0, spaceAfter=2,
        ),
    }
    return styles


_TOKEN_LONG = re.compile(r"\S{40,}")


def _couper_mots_longs(texte: str, largeur_pt: float, nom_police: str, taille: float) -> str:
    """Coupe les « mots » plus larges qu'une ligne (URL, hash, log collé).

    ``splitLongWords`` de reportlab gère déjà l'essentiel, mais un token de
    plusieurs milliers de caractères sans espace reste la façon la plus sûre de
    faire échouer une mise en page. On insère une espace fine tous les N
    caractères, N calculé sur la largeur réelle du glyphe le plus large observé.
    """

    def _decouper(m):
        mot = m.group(0)
        if pdfmetrics.stringWidth(mot, nom_police, taille) <= largeur_pt:
            return mot
        largeur_car = max(
            pdfmetrics.stringWidth(c, nom_police, taille) for c in set(mot)
        ) or 1.0
        n = max(8, int(largeur_pt / largeur_car) - 1)
        return " ".join(mot[i : i + n] for i in range(0, len(mot), n))

    return _TOKEN_LONG.sub(_decouper, texte)


def _paragraphes(texte: str):
    """Découpe un texte brut en paragraphes (ligne vide = séparateur)."""
    blocs = [b.strip() for b in re.split(r"\n\s*\n", texte.replace("\r\n", "\n"))]
    blocs = [b for b in blocs if b]
    return blocs or [""]


def encadre_secable(
    texte: str,
    gabarit: Gabarit,
    styles: dict,
    police: PoliceEnregistree,
    titre: str = "",
) -> list:
    """Encadré teinté **sécable**, quelle que soit la longueur du texte.

    C'est l'antidote du défaut n°1 : le verbatim de 12 690 caractères de l'audit
    était un ``Table`` mono-cellule, donc insécable, donc ``LayoutError`` et HTTP
    500 dès que le contenu dépassait la hauteur d'une page (le seuil constaté
    était entre 5 016 et 12 690 caractères).

    Ici l'encadré est une **suite de ``Paragraph``** partageant le même
    ``backColor`` : reportlab coupe un ``Paragraph`` entre deux lignes, sans
    limite de longueur. Le fond se poursuit d'une page à l'autre parce que
    chaque fragment redessine le sien.

    Aucun ``KeepTogether``, aucun ``Table`` : ce sont les deux seules façons
    connues de rendre du texte insécable dans platypus.

    Rend une liste de flowables, à concaténer dans l'histoire.
    """
    largeur_texte = gabarit.largeur_utile - PADDING_ENCADRE[1] - PADDING_ENCADRE[3]
    # Balisage idempotent : ``construire_pdf`` a déjà signalé les caractères non
    # couverts, mais un appelant direct de cette fonction ne doit pas pouvoir
    # perdre du texte en silence. Les marqueurs ``[U+XXXX]`` étant en ASCII, un
    # second passage ne trouve plus rien à remplacer.
    if police is not None:
        texte, _ = baliser_caracteres(texte, police, "signaler")
        titre, _ = baliser_caracteres(titre or "", police, "signaler")
    blocs = _paragraphes(texte)
    flowables = []
    if titre:
        flowables.append(Paragraph(escape(titre), styles["encadre_titre"]))
    for i, bloc in enumerate(blocs):
        premier = (i == 0) and not titre
        dernier = i == len(blocs) - 1
        if premier and dernier:
            style = ParagraphStyle(
                "encadre_seul",
                parent=styles["encadre_corps"],
                borderPadding=PADDING_ENCADRE,
                spaceAfter=styles["encadre_fin"].spaceAfter,
            )
        elif premier:
            style = ParagraphStyle(
                "encadre_debut",
                parent=styles["encadre_corps"],
                borderPadding=(
                    PADDING_ENCADRE[0], PADDING_ENCADRE[1], 2.0, PADDING_ENCADRE[3],
                ),
            )
        elif dernier:
            style = styles["encadre_fin"]
        else:
            style = styles["encadre_corps"]
        sur = _couper_mots_longs(bloc, largeur_texte, style.fontName, style.fontSize)
        flowables.append(Paragraph(escape(sur).replace("\n", "<br/>"), style))
    return flowables


# --------------------------------------------------------------------------- #
# Document — en-tête, pied, signets, métadonnées.
# --------------------------------------------------------------------------- #


class _CanvasNumerote(Canvas):
    """Canvas à deux passes : dessine en-tête et pied en connaissant le total.

    L'en-tête et le pied sont tracés au canvas, donc au même ``x`` que le
    ``Frame`` — c'est ce qui fait tenir la promesse « une seule abscisse ».
    """

    gabarit: Gabarit = GABARIT_REFERENCE
    police: PoliceEnregistree = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages_memorisees = []

    def showPage(self):
        self._pages_memorisees.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages_memorisees)
        for etat in self._pages_memorisees:
            self.__dict__.update(etat)
            self._decorer(total)
            super().showPage()
        super().save()

    def _decorer(self, total: int):
        g, pal = self.gabarit, self.gabarit.palette
        nom = self.police.nom if self.police else "Helvetica"
        contexte = {"page": self._pageNumber, "total": total}
        self.saveState()
        self.setFont(nom, g.typo.taille_petit)
        self.setFillColor(pal.couleur("texte_discret"))
        self.setStrokeColor(pal.couleur("filet"))
        self.setLineWidth(0.4)

        y_entete = g.hauteur - g.marges.entete_mm * mm
        if g.entete_gauche:
            self.drawString(g.grille_gauche, y_entete, g.entete_gauche.format(**contexte))
        if g.entete_droite:
            self.drawRightString(g.grille_droite, y_entete, g.entete_droite.format(**contexte))
        if g.filet_entete and (g.entete_gauche or g.entete_droite):
            self.line(
                g.grille_gauche, y_entete - 3.0, g.grille_droite, y_entete - 3.0
            )

        y_pied = g.marges.pied_mm * mm
        if g.filet_pied and (g.pied_gauche or g.pied_droite):
            self.line(g.grille_gauche, y_pied + 9.0, g.grille_droite, y_pied + 9.0)
        if g.pied_gauche:
            self.drawString(g.grille_gauche, y_pied, g.pied_gauche.format(**contexte))
        if g.pied_droite:
            self.drawRightString(g.grille_droite, y_pied, g.pied_droite.format(**contexte))
        self.restoreState()


class _Document(BaseDocTemplate):
    """Document à une colonne, ``Frame`` sans padding, signets sur les titres."""

    def __init__(self, chemin, gabarit: Gabarit, police: PoliceEnregistree, **kw):
        self.gabarit = gabarit
        self.police = police
        g = gabarit
        super().__init__(
            chemin,
            pagesize=g.taille_page,
            # Sans ``initialFontName``, reportlab écrit un préambule
            # « BT /F1 12 Tf » avec la base-14 Helvetica sur CHAQUE page, et le
            # PDF déclare donc une police non embarquée même si aucun caractère
            # ne l'utilise. Mesuré : pdf_verify sortait en défaut bloquant
            # « police_non_embarquee : Helvetica » sur un document 100 % DejaVu.
            # Une TTF est « dynamique » chez reportlab : le préambule est alors
            # omis, et plus aucune base-14 n'apparaît dans les ressources.
            initialFontName=police.nom,
            initialFontSize=g.typo.taille_corps,
            leftMargin=g.grille_gauche,
            rightMargin=g.marges.droite_mm * mm,
            topMargin=g.marges.haut_mm * mm,
            bottomMargin=g.marges.bas_mm * mm,
            title=g.titre,
            author=g.auteur,
            subject=g.sujet,
            keywords=g.mots_cles,
            creator=g.producteur,
            lang=g.langue,
            displayDocTitle=1,
            **kw,
        )
        cadre = Frame(
            g.grille_gauche,
            g.marges.bas_mm * mm,
            g.largeur_utile,
            g.hauteur - g.marges.haut_mm * mm - g.marges.bas_mm * mm,
            # LES QUATRE PADDINGS À ZÉRO : c'est le 6 pt par défaut de reportlab
            # qui décalait le texte de 20,0 mm à 22,1 mm dans l'audit.
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
            id="corps",
        )
        self.addPageTemplates([PageTemplate(id="page", frames=[cadre])])
        self.signets = []

    def afterFlowable(self, flowable):
        """Alimente le plan de signets depuis les titres réellement posés."""
        if not isinstance(flowable, Paragraph):
            return
        nom_style = getattr(flowable.style, "name", "")
        niveau = {"titre": 0, "soustitre": 1}.get(nom_style)
        if niveau is None:
            return
        texte = flowable.getPlainText()
        cle = f"sig{len(self.signets)}"
        self.canv.bookmarkPage(cle)
        # La clef doit être une *str* : reportlab range le titre dans
        # ``destinationnamestotitles[clef]`` mais relit ce dictionnaire après
        # avoir décodé une clef bytes en str — passer des bytes fait retomber
        # silencieusement le libellé du signet sur la clef technique (« sig0 »).
        self.canv.addOutlineEntry(texte, cle, level=niveau, closed=0)
        self.signets.append({"titre": texte, "niveau": niveau, "page": self.page})


@dataclass
class RapportRendu:
    """Ce que la génération a réellement fait — à journaliser, pas à supposer."""

    chemin: str
    pages: int
    police: str
    police_embarquee: bool
    repli_police: bool
    raison_repli: str
    caracteres_manquants: list
    signets: list
    grille_gauche_mm: float

    def resume(self) -> str:
        lignes = [
            f"PDF   : {self.chemin}",
            f"pages : {self.pages}",
            f"police: {self.police} "
            + ("(EMBARQUÉE)" if self.police_embarquee else "(NON embarquée — repli)"),
            f"grille: bord gauche unique à {self.grille_gauche_mm:.1f} mm",
            f"signets: {len(self.signets)}",
        ]
        if self.repli_police:
            lignes.append(f"REPLI : {self.raison_repli}")
        if self.caracteres_manquants:
            apercu = ", ".join(
                f"U+{m['codepoint']:04X} {m['nom']} (x{m['occurrences']})"
                for m in self.caracteres_manquants[:10]
            )
            lignes.append(
                f"SIGNALÉ: {len(self.caracteres_manquants)} caractère(s) non rendu(s) : {apercu}"
            )
        else:
            lignes.append("glyphes: tous les caractères demandés sont couverts")
        return "\n".join(lignes)


def construire_pdf(
    chemin: str,
    contenu,
    gabarit: Gabarit = GABARIT_REFERENCE,
    autoriser_repli_police: bool = True,
) -> RapportRendu:
    """Génère le PDF et rend le rapport de ce qui s'est réellement passé.

    ``contenu`` est une liste de blocs, chacun un dict avec une clef ``type`` :

    - ``{"type": "titre", "texte": ...}``      — titre de niveau 1, signet
    - ``{"type": "soustitre", "texte": ...}``  — titre de niveau 2, signet
    - ``{"type": "corps", "texte": ...}``      — texte courant (lignes vides = paragraphes)
    - ``{"type": "encadre", "titre": ..., "texte": ...}`` — encadré SÉCABLE
    - ``{"type": "liste", "items": [...]}``    — liste à puces
    - ``{"type": "champ", "libelle": ..., "valeur": ...}`` — libellé / valeur
    - ``{"type": "filet"}``                    — filet horizontal, bord à bord
    - ``{"type": "saut"}``                     — saut de page
    - ``{"type": "espace", "mm": 4}``          — respiration verticale

    Tout le texte passe par :func:`baliser_caracteres` : aucun caractère ne
    disparaît sans être remonté dans :attr:`RapportRendu.caracteres_manquants`.
    """
    police = enregistrer_police(gabarit, autoriser_repli=autoriser_repli_police)
    styles = construire_styles(gabarit, police)
    manquants_globaux = {}

    def sur(texte: str) -> str:
        texte = "" if texte is None else str(texte)
        propre, manquants = baliser_caracteres(texte, police, gabarit.politique_glyphes)
        for m in manquants:
            entree = manquants_globaux.setdefault(m["codepoint"], dict(m, occurrences=0))
            entree["occurrences"] += m["occurrences"]
        return propre

    def para(texte, style):
        largeur = gabarit.largeur_utile
        coupe = _couper_mots_longs(texte, largeur, style.fontName, style.fontSize)
        return Paragraph(escape(coupe).replace("\n", "<br/>"), style)

    histoire = []
    for bloc in contenu:
        genre = bloc.get("type", "corps")
        if genre == "titre":
            histoire.append(para(sur(bloc["texte"]), styles["titre"]))
        elif genre == "soustitre":
            histoire.append(para(sur(bloc["texte"]), styles["soustitre"]))
        elif genre == "corps":
            for p in _paragraphes(sur(bloc["texte"])):
                histoire.append(para(p, styles["corps"]))
        elif genre == "encadre":
            histoire.extend(
                encadre_secable(
                    sur(bloc.get("texte", "")),
                    gabarit,
                    styles,
                    police,
                    titre=sur(bloc.get("titre", "")),
                )
            )
        elif genre == "liste":
            for item in bloc.get("items", []):
                histoire.append(
                    Paragraph(
                        escape(sur(str(item))),
                        styles["puce"],
                        bulletText="\u2022" if police.couvre("\u2022") else "-",
                    )
                )
            histoire.append(Spacer(0, gabarit.typo.espace_apres_paragraphe))
        elif genre == "champ":
            histoire.append(
                para(
                    f"{sur(bloc['libelle'])} : {sur(bloc.get('valeur', ''))}",
                    styles["corps"],
                )
            )
        elif genre == "filet":
            histoire.append(
                HRFlowable(
                    width="100%",  # -> exactement la largeur utile, donc la grille
                    thickness=0.6,
                    color=gabarit.palette.couleur("filet"),
                    spaceBefore=3,
                    spaceAfter=6,
                    hAlign="LEFT",
                )
            )
        elif genre == "saut":
            histoire.append(PageBreak())
        elif genre == "espace":
            histoire.append(Spacer(0, float(bloc.get("mm", 4)) * mm))
        else:
            raise ValueError(f"type de bloc inconnu : {genre!r}")

    os.makedirs(os.path.dirname(os.path.abspath(chemin)) or ".", exist_ok=True)
    doc = _Document(chemin, gabarit, police)
    canvas_classe = type(
        "_CanvasGabarit", (_CanvasNumerote,), {"gabarit": gabarit, "police": police}
    )
    doc.build(histoire, canvasmaker=canvas_classe)

    return RapportRendu(
        chemin=chemin,
        pages=doc.page,
        police=police.nom,
        police_embarquee=not police.repli,
        repli_police=police.repli,
        raison_repli=police.raison,
        caracteres_manquants=sorted(
            manquants_globaux.values(), key=lambda m: -m["occurrences"]
        ),
        signets=doc.signets,
        grille_gauche_mm=gabarit.marges.gauche_mm,
    )


if __name__ == "__main__":  # démonstration exécutable
    import json
    import sys

    destination = sys.argv[1] if len(sys.argv) > 1 else "demo.pdf"
    gab = replace(
        GABARIT_REFERENCE,
        titre="Démonstration pdf-quality",
        auteur="hub de supervision",
        sujet="brique PDF de référence",
        entete_gauche="Démonstration",
        entete_droite="pdf-quality",
        pied_gauche="Généré par pdf_report.py",
    )
    rapport = construire_pdf(
        destination,
        [
            {"type": "titre", "texte": "Démonstration"},
            {"type": "corps", "texte": "Un paragraphe courant, aligné sur la grille."},
            {"type": "encadre", "titre": "Verbatim", "texte": "Texte long. " * 800},
        ],
        gab,
    )
    print(rapport.resume())
