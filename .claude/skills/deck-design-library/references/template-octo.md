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

## 5. Indices de layouts utilisés

| Usage | Indice | Nom | Placeholders |
| --- | --- | --- | --- |
| Couverture | 8 | `40 - Couverture [1]` | idx0 titre · idx1 sous-titre · idx2 « OCTO Technology » · idx3 date |
| Slides de contenu | 5 | `04 - Titre seul` | idx0 titre (garde logo/pied de page/n° slide) |

Layouts « cadre blanc » (idx 15–22, ex. `63 - Titre, contenu et visuel à
droite - cadre blanc`) : cadres photo à coins diagonaux (`round2DiagRect`,
texte gabarit « ici mettre une Photo ») — voir la skill `pptx-framed-image`.

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
