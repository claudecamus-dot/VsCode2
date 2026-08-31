---
name: pdf-quality
description: Génère des PDF de qualité sur gabarit paramétrable (reportlab) et VÉRIFIE le résultat en le mesurant (PyMuPDF) — encadré sécable qui ne lève jamais LayoutError sur un verbatim de 12 000+ caractères, police Unicode embarquée avec signalement des caractères non couverts au lieu d'une perte silencieuse, grille à une seule abscisse gauche, métadonnées et signets. Le vérificateur `pdf_verify.py` mesure remplissage, blancs, débordements, unicité du bord gauche, polices embarquées, texte réellement rendu, métadonnées et balisage, et sort en code non nul sur défaut bloquant. À utiliser dès qu'un projet de la flotte produit un PDF (compte rendu d'entretien, rapport, export de verbatims), avant de croire qu'un PDF « qui se génère sans erreur » est correct.
---

# pdf-quality

L'équivalent PDF de ce que `pptx-deck` + `pptx-verify` sont pour les decks :
une bibliothèque de génération (`scripts/pdf_report.py`) et un vérificateur qui
mesure (`scripts/pdf_verify.py`).

## Pourquoi cette skill existe

Un audit **par exécution réelle** du 2026-08-31 a produit 6 PDF et regardé
**22 pages rastérisées** de la seule chaîne PDF de la flotte (VSCode2,
reportlab, `app/services/interview_pdf_export.py`). Sept défauts mesurés :

| # | Défaut mesuré | Chiffre constaté |
| --- | --- | --- |
| 1 | Verbatim insécable → `LayoutError` → HTTP **500** | passe à 5 016 car., casse à 12 690 |
| 2 | Pages creuses et orphelines | 40 % de remplissage, 154 mm de blanc, orpheline à 4 % |
| 3 | Tout caractère hors Latin-1 perdu **sans exception ni avertissement** | « Nguyễn Thị Mai » → « NguyIn ThI Mai » ; « Иванов » → pavés noirs ; Δ (U+0394) → ∆ (U+2206) |
| 4 | Trois bords gauche sur une même page | 20,0 mm (canvas) / 22,1 mm (padding de `Frame`) / 25,0 mm (`colWidths=[160*mm]`) |
| 5 | Ni police embarquée, ni métadonnées, ni signets, ni `/Lang`, ni `/StructTreeRoot` | `title='(anonymous)'` |
| 6 | Aucune notion de template | couleurs, tailles, marges en dur — alors que la chaîne PPT du même projet a un template client complet |
| 7 | 23 tests verts malgré tout | 78 assertions, **zéro** sur la géométrie |

La matière première de ces documents est du texte **collé depuis Teams** :
le point 3 n'est pas un cas exotique, c'est le cas courant.

## Quand l'utiliser

- Un projet de la flotte génère un PDF (compte rendu, rapport, export de
  verbatims, restitution) — reprendre `pdf_report.py` plutôt que réécrire une
  mise en page.
- Un PDF « se génère sans erreur » et on s'apprête à le déclarer bon : lancer
  `pdf_verify.py` d'abord.
- Une route HTTP qui rend un PDF tombe en 500 sur certains contenus : c'est
  presque toujours un bloc insécable (`Table` mono-cellule, `KeepTogether`).
- Un client demande une charte : c'est le rôle du `Gabarit`.

## Ce que la brique garantit

1. **Aucun bloc insécable.** L'encadré est une suite de `Paragraph` teintés,
   jamais un `Table`. Testé jusqu'à 200 000 caractères et sur un token de
   12 000 caractères sans espace.
2. **Pas de page creuse ni d'orpheline** *par construction* — et surtout,
   `pdf_verify` les **mesure** page par page (remplissage %, plus grand blanc
   vertical en mm).
3. **Rien ne disparaît en silence.** Police Unicode embarquée ; tout caractère
   non couvert est remplacé par un marqueur visible `[U+XXXX]` **et** listé
   dans `RapportRendu.caracteres_manquants`. Le repli sur base-14 (machine sans
   fonte Unicode) est explicite (`repli=True` + `raison`).
4. **Une seule abscisse gauche.** `Marges.gauche_mm` sert au canvas
   (en-tête/pied), au `Frame` (dont les 4 paddings sont mis à zéro), aux filets
   et au fond des encadrés (padding gauche **nul** par construction).
5. **Métadonnées, `/Lang`, `displayDocTitle` et signets** alimentés par les
   titres réellement posés.
6. **Gabarit paramétrable** : palette, typographie, marges, en-tête, pied,
   format de page, langue — un dict ou un JSON, jamais une constante du code.
7. **Un vérificateur qui échoue**. `pdf_verify.py` rend un code de sortie non
   nul sur défaut bloquant : chaînable avant commit.

## Ce que la brique NE garantit PAS

- **Le balisage** (`/StructTreeRoot`, PDF/UA). reportlab ne produit pas de PDF
  balisé, et on **refuse d'en simuler un** : un arbre de structure vide ferait
  croire à un lecteur d'écran qu'il y a un ordre de lecture. `pdf_verify` le
  signale en avertissement, et en défaut bloquant avec `--exiger-balisage` —
  auquel cas il faut un autre moteur (LaTeX + tagpdf, Word/LibreOffice export,
  post-traitement).
- **Les emoji couleur.** Aucune fonte monochrome ne les porte : ils sortent en
  `[U+1F642]`. C'est le comportement voulu — signaler, pas perdre.
- **La justesse éditoriale.** Un PDF parfaitement aligné peut rester illisible.
- **Le rendu sur une autre machine** si le repli base-14 s'est déclenché : le
  rapport le dit, il faut alors installer une fonte Unicode ou en embarquer une
  avec le projet.

## Limite honnête : un vérificateur ne remplace pas de REGARDER

`pdf_verify.py` mesure ce qui se mesure. Il ne voit pas un titre de la couleur
du fond, une hiérarchie absente, un tableau incompréhensible, un encadré teinté
qui coupe une phrase au mauvais endroit. L'audit qui a produit ce cahier des
charges n'a rien trouvé de tout cela **avant d'avoir rastérisé et ouvert 22
pages**. La séquence complète est donc :

```bash
# 1. générer
py mon_rapport.py

# 2. mesurer  (code de sortie non nul = défaut bloquant)
py .claude/skills/pdf-quality/scripts/pdf_verify.py sortie.pdf --grille-mm 20 \
    --attendu "Nguyễn Thị Mai" --attendu "Иванов Пётр"

# 3. RASTÉRISER ET OUVRIR — l'étape que le parseur ne remplace pas
py -c "import fitz; d=fitz.open('sortie.pdf'); [d[i].get_pixmap(dpi=115).save(f'p{i+1}.png') for i in range(min(3,d.page_count))]"
```
puis ouvrir les PNG (outil `Read` pour un agent, une visionneuse pour un
humain). Un parseur qui ne lève pas n'est pas une preuve de qualité.

## Utilisation

### Générer

```python
import sys; sys.path.insert(0, ".claude/skills/pdf-quality/scripts")
from dataclasses import replace
from pdf_report import GABARIT_REFERENCE, construire_pdf, gabarit_depuis_dict

gabarit = replace(
    GABARIT_REFERENCE,
    titre="Compte rendu d'entretien", auteur="Cabinet Exemple",
    sujet="Restitution", langue="fr-FR",
    entete_gauche="Compte rendu d'entretien", entete_droite="Cabinet Exemple",
    pied_gauche="Confidentiel", pied_droite="Page {page} / {total}",
)

rapport = construire_pdf("sortie.pdf", [
    {"type": "titre",     "texte": "Compte rendu d'entretien"},
    {"type": "champ",     "libelle": "Interlocuteur", "valeur": "Nguyễn Thị Mai"},
    {"type": "filet"},
    {"type": "soustitre", "texte": "Verbatim intégral"},
    {"type": "encadre",   "titre": "Propos recueillis", "texte": verbatim},  # sécable
    {"type": "liste",     "items": ["Pas d'instance de décision"]},
], gabarit)

print(rapport.resume())          # police, repli, caractères signalés, signets
assert not rapport.caracteres_manquants   # si on veut être intransigeant
```

Types de blocs : `titre`, `soustitre`, `corps`, `encadre`, `liste`, `champ`,
`filet`, `saut`, `espace`.

### Gabarit client depuis un JSON

```python
gabarit = gabarit_depuis_dict({
    "format_page": "LETTER", "titre": "Rapport client X", "langue": "en-US",
    "palette": {"titre": "#7a0026", "fond_encadre": "#fdf1e7"},
    "typo":    {"taille_corps": 11.5, "interligne": 1.6},
    "marges":  {"gauche_mm": 28.0, "droite_mm": 18.0},
})
```
Seules les clefs modifiées sont à citer ; une clef inconnue lève `ValueError`
(un gabarit silencieusement ignoré est pire qu'un gabarit refusé).

### Vérifier

```
py scripts/pdf_verify.py rapport.pdf [--grille-mm 20] [--securite-mm 8]
    [--remplissage-min 55] [--blanc-max-mm 60] [--orphelin-min 8]
    [--attendu "texte"] [--attendus-fichier liste.txt]
    [--exiger-balisage] [--json]
```
Codes de sortie : **0** propre, **1** défaut bloquant, **2** impossible de
mesurer (PyMuPDF absent, fichier introuvable). Le vérificateur ne simule jamais
une mesure qu'il ne peut pas faire.

## Pièges rencontrés en construisant cette skill (tous mesurés)

- **`Table` mono-cellule** = insécable = `LayoutError` garantie au-delà d'une
  page. C'est la cause racine du 500 de l'audit. Un `Paragraph`, lui, se coupe.
- **`Frame` a 6 pt de padding par défaut** sur les quatre côtés : c'est le
  22,1 mm de l'audit. Les mettre à zéro et poser le cadre sur la grille.
- **Le fond d'un `Paragraph` déborde à droite du padding** : reportlab dessine
  `self.width - (leftIndent+rightIndent) + lbp + rbp`. Sans `rightIndent = rbp`,
  le fond sortait à 192,8 mm pour une grille droite à 190,0 mm.
- **Helvetica réapparaît sans qu'on l'ait demandée**, et le PDF déclare alors
  une police non embarquée : (a) reportlab écrit un préambule
  `BT /F1 12 Tf` sur chaque page si `initialFontName` n'est pas la TTF ;
  (b) `ParagraphStyle.bulletFontName` reste `Helvetica` par défaut dès qu'il y
  a une liste à puces. Les deux ont été trouvés **par la mesure**, pas à l'œil.
- **`addOutlineEntry(titre, clef)` veut une clef `str`** : avec des `bytes`,
  reportlab retombe silencieusement sur la clef technique et tous les signets
  s'appellent « sig0 », « sig1 »…
- **Les éléments calés à droite** (numéro de page) ont légitimement un autre
  bord gauche : le vérificateur les détecte et les exclut, sinon la mesure
  d'unicité serait toujours fausse.

## Tests

```
py -m pytest .claude/skills/pdf-quality/tests/test_pdf_quality.py -q \
    --basetemp=C:/tmp/pdfquality/pt
```
27 tests, au moins un par défaut mesuré, chacun rejouant le scénario réel :
verbatim de 12 690 caractères, longueurs jusqu'à 200 000, token de 12 000
caractères sans espace, vietnamien/cyrillique/grec relus **depuis le PDF**,
emoji signalé, repli de police, unicité du bord gauche, zone imprimable,
métadonnées/signets/langue, gabarit LETTER à 28 mm, et un **PDF fautif
fabriqué exprès** (trois bords gauche, base-14, `(anonymous)`, page creuse,
orpheline, débordement) que `pdf_verify` doit détecter avec un code de sortie
non nul, en CLI comme par import.

`--basetemp` court et hors `%TEMP%` : sur ces machines, une jonction morte dans
`%TEMP%` fait planter le teardown de pytest et rend un exit 1 sur une suite
pourtant verte.

## Dépendances

- `reportlab` (génération). `py -m pip install reportlab`
- `pymupdf` (mesure). `py -m pip install pymupdf` — sans lui, `pdf_verify`
  refuse de rendre un verdict (code 2) au lieu de simuler.
- Une fonte Unicode TrueType : `DejaVuSans.ttf`, `segoeui.ttf`,
  `NotoSans-Regular.ttf` ou `arial.ttf`, cherchées dans `C:\Windows\Fonts`,
  `%LOCALAPPDATA%\Microsoft\Windows\Fonts`, `/usr/share/fonts`, `~/.fonts`.
  Aucune trouvée ⇒ repli base-14 **annoncé**, jamais silencieux.
