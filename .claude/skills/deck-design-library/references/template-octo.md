# Le template PowerPoint OCTO — objet flotte

> État daté le 2026-07-08 (VSCode1, contre `template ppt/template.pptx`),
> repris le 2026-09-02 depuis `VSCode1/export/template-octo.md`. Fiche
> FACTUELLE (pas de principes de design ici — voir `catalogue-restitution.md`
> et la skill `restitution-deck-design` pour ça).

## 1. Un seul fichier, trois emplacements

Même template `.pptx` (~2,5 Mo), **md5 identique mesuré** sur les trois
copies de la flotte : `ef9f8c805133…`, 2 669 240 octets.

| Projet | Chemin |
| --- | --- |
| VSCode1 | `template ppt/template.pptx` |
| VSCode2 | `app/assets/template-octo.pptx` |
| VSCode3 | `docs/cadrage-ppt/template-octo.pptx` |

Toute modification du template dans un projet (nouveau layout, couleur de
thème changée) dérive de facto de la flotte — vérifier le md5 avant de
supposer qu'un des trois est encore identique aux deux autres.

## 2. Identité

| Élément | Valeur |
| --- | --- |
| Marque | OCTO Technology — Part of Accenture |
| Format slide | **10,0 × 5,625 in** (16:9) |
| Police de marque | **Outfit** (poids nommés, voir §4) |
| Nb de layouts | **34** sur le master principal |

## 3. Palette du thème (10 slots)

Source de vérité couleur — lue via `pptx_deck.theme_colors(prs)`, jamais
codée en dur.

| Slot thème | Hex | Rôle OCTO |
| --- | --- | --- |
| `dk1` | `#0E2356` | **Navy** — texte principal, titres, fonds dark |
| `lt1` | `#FFFFFF` | Blanc — fonds light, corps de cards |
| `accent3` | `#00D2DD` | **Cyan** — accent, dots, labels actifs |
| `dk2` | `#3E4F78` | slate 700 — texte secondaire fort |
| `lt2` | `#586586` | slate 600 — sous-titres, labels, texte muted |
| `accent1` | `#6E7B9A` | slate 500 — texte tertiaire, copyright |
| `accent2` | `#9FA7BB` | slate 400 — icônes inactives, séparateurs |
| `accent4` | `#B7BDCC` | slate 300 — bordures légères |
| `accent5` | `#CFD3DD` | slate 200 — bordures de cards standard |
| `accent6` | `#E7E9EE` | slate 100 — fonds d'encarts, alternances |

⚠️ Le `fontScheme` du thème déclare Arial (repli générique, pas la charte) —
la vraie police vit sur les placeholders, d'où la détection par placeholder.

## 4. Police — Outfit, en poids nommés

| Variante | Où | Poids |
| --- | --- | --- |
| `Outfit` | corps, titres réguliers | 400 |
| `Outfit Light` | sous-titres de couverture | 300 |
| `Outfit Medium` | titre de couverture | 500 |
| `Outfit SemiBold` | titres de contenu, corps gras | 600 |

## 4bis. Calibration typographique mesurée (Outfit)

Mesuré sur un rendu PowerPoint réel du template partagé (2026-09-17, sur
VScode6) : ~14,8 caractères/pouce à 10,5pt (56 caractères tiennent sur
3,78 in), hauteur de ligne ~0,19 in à interligne 1.1.

Les défauts de la skill `pptx-deck` (`cpi_ref=11.0` dans `estimer_lignes`,
`cpi_pessimiste=10.7` dans `verifier_debordements_texte`) sont calibrés sur
une police plus large qu'Outfit et surestiment la hauteur d'environ 30 % sur
ce gabarit — effet mesuré sur VScode6 : blocs de texte budgétés ~40 % trop
hauts, cartes aux trois quarts vides, 28 faux débordements signalés sur un
deck correct.

**Ces calibrations sont des DÉFAUTS, pas des constantes universelles** — à
re-mesurer sur un rendu réel avant usage sur un nouveau gabarit. Sur CE
gabarit (Outfit, template OCTO), la valeur mesurée est `cpi_ref=14.0` /
`cpi_pessimiste=14.0`, pas les défauts de la skill — les défauts eux-mêmes
ne sont pas changés (ils servent d'autres gabarits).

## 5. Indices de layouts utilisés

| Usage | Indice | Nom | Placeholders |
| --- | --- | --- | --- |
| Couverture | 8 | `40 - Couverture [1]` | idx0 titre · idx1 sous-titre · idx2 « OCTO Technology » · idx3 date |
| Slides de contenu | 5 | `04 - Titre seul` | idx0 titre (garde logo/pied de page/n° slide) |
| Intercalaire de chapitre | 2 | `50 - Chapitre [1]` | idx0 titre (+ sous-titre en 2e paragraphe) · idx1 numéro ; cadre photo teardrop OBLIGATOIRE à remplir |

Layouts « cadre blanc » (idx 15–22, ex. `63 - Titre, contenu et visuel à
droite - cadre blanc`) : cadres photo à coins diagonaux (`round2DiagRect`,
texte gabarit « ici mettre une Photo ») — voir la skill `pptx-framed-image`.

### 5bis. Deux pièges du layout Chapitre

Implémentation de référence : VSCode3 `docs/cadrage-ppt/generate_deck.py::slide_chapitre`.
Variante à lire quand la cible n'est pas le layout 50 : VSCode4
`scripts/generate_deck_ohc.py::slide_chapitre` (repositionne un layout 51
pour imiter la géométrie mesurée de VSCode3).

- **Numéro de chapitre** : 17pt, marges à zéro, sans puce, ancrage `MIDDLE`
  — sinon il est renvoyé à la ligne hors de son encart de 0,55 in.
- **Cadre photo teardrop** : DOIT être rempli via `pptx-framed-image`
  (`_remplir_cadre` avec repli Openverse à nom distinct) — sinon le texte
  gabarit « ici mettre une Photo » reste visible au rendu final.

_(finding hub:deck-design-library/template-octo.md, vu le 2026-09-17 sur
VScode6 : ces deux défauts avaient déjà été résolus deux fois séparément,
chez VSCode3 puis VSCode4, sans que le canon du hub le documente.)_

## 6. Chrome protégé (convention de placement, pas une cote mesurée)

- Zone de contenu (layout « Titre seul ») : ≈ `top 1.15 in` → `bottom 5.45 in`,
  marge latérale ≈ `0.55 in`. Ce sont les valeurs de travail de VSCode1, pas une
  mesure du template : le placeholder titre du layout 5 est à `left = 0.615 in`
  (mesuré le 2026-09-02) — aligner à 0,55 in décale de 1,7 mm du titre. Mesurer
  sur le layout réel avant d'en faire une constante.
- Logo OCTO — coin haut-gauche.
- Pied de page vertical gauche — « OCTO | PART OF ACCENTURE© … ».
- Badge n° de slide — pastille navy, coin bas-droit ; tout contenu pleine
  largeur du bas doit s'arrêter à `x ≈ 9.15 in` pour ne pas le recouvrir.

## 7. Script de re-vérification (recopié tel quel)

```bash
cd app && python - <<'PY'
from pptx import Presentation; from pptx.util import Emu
import sys; sys.path.insert(0,'scripts'); import pptx_deck as D
p = Presentation('../template ppt/template.pptx')
print('dims', round(Emu(p.slide_width).inches,3), round(Emu(p.slide_height).inches,3))
print('police', D.police_marque(p)); print('theme', D.theme_colors(p))
print('layouts', len(p.slide_masters[0].slide_layouts), 'slides', len(p.slides))
PY
```

Adapter le chemin du template et le `sys.path` au projet courant — le script
suppose l'arborescence VSCode1 (`app/scripts/pptx_deck.py`).

## Rampes de teintes pour schémas (publiées par le gabarit, relevées le 2026-09-23)

Une slide de palette d'un deck de formation OCTO publie deux rampes à 10 paliers,
**réservées aux schémas** (secteurs, niveaux, séries) — jamais au texte courant :

- **Rampe navy** (100 % → 10 %) : `#0E2356` `#263967` `#3E4F78` `#586586` `#6E7B9A`
  `#8691AB` `#9FA7BB` `#B7BDCC` `#CFD3DD` `#E7E9EE` — reprend les slots du thème plus
  deux intermédiaires (`#263967`, `#8691AB`).
- **Rampe cyan** (100 % → 10 %) : `#00D2DD` `#3CD7E0` `#5BDDE4` `#72DFE7` `#8AE4EB`
  `#9EE9ED` `#B2EEF2` `#C6F1F5` `#DAF6F9` `#EBFAFB`.
- Turquoises profonds hors rampe, fréquents dans les schémas : `#00AFCB`, `#00A3BE`,
  `#59C3D5` ; ardoise des connecteurs : `#627091`.
