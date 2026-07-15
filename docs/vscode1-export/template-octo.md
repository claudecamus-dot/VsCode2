# Spécification du template — `template ppt/template.pptx` (OCTO)

> **Rôle** : décrire fidèlement le template PowerPoint OCTO à partir duquel
> l'export de restitution est construit (`app/scripts/export-restitution-ppt.py`
> ouvre ce fichier avec `python-pptx` et dessine PAR-DESSUS ses masters/layouts).
> **Convention repo** : *tout template `.pptx` versionné dans `template ppt/`
> possède un md compagnon fidèle dans `export/`* nommé `template-<nom>.md`. Ce md
> est la référence à jour ; il se vérifie contre le fichier réel (script en fin
> de page), pas contre la mémoire.
>
> Généré/vérifié le 2026-07-08 contre `template ppt/template.pptx`.

---

## 1. Identité

| Élément | Valeur |
| --- | --- |
| Fichier | `template ppt/template.pptx` (~2,5 Mo) |
| Marque | OCTO Technology — Part of Accenture |
| Format slide | **10,0 × 5,625 in** (16:9) |
| Police de marque | **Outfit** (famille, déclinée en poids nommés — voir §3) |
| Slides d'exemple | **9** (supprimées à la génération ; masters/layouts conservés) |
| Nb de layouts | **34** sur le master principal |

## 2. Palette du thème = design system OCTO

Le nuancier du thème encode **directement la charte OCTO** (navy / cyan /
slate). C'est la source de vérité couleur : le générateur la lit via
`pptx_deck.theme_colors(prs)` — **ne pas coder ces valeurs en dur**.

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

> ⚠️ Le `fontScheme` du thème déclare **Arial** en police (major+minor). C'est
> un **repli générique**, PAS la charte : la vraie police (Outfit) vit sur les
> placeholders (§3). D'où la détection par placeholder, pas par fontScheme.

## 3. Police — Outfit, en poids nommés

La famille **Outfit** est portée par les placeholders des layouts, en variantes
de poids traitées par PowerPoint comme des familles distinctes :

| Variante | Où | Poids |
| --- | --- | --- |
| `Outfit` | corps, titres réguliers | 400 |
| `Outfit Light` | sous-titres de couverture | 300 |
| `Outfit Medium` | titre de couverture | 500 |
| `Outfit SemiBold` | titres de contenu, corps gras | 600 |

Détection robuste : `pptx_deck.police_marque(prs)` prend la famille dominante des
placeholders (suffixe de poids retiré) → `"Outfit"`, et l'applique au contenu
dessiné via `set_police()`. Le drapeau bold/italic de python-pptx s'applique
ensuite normalement. Repli : mineure du thème si aucune famille exploitable.

## 4. Layouts utilisés par l'export

Repérés **par nom** (robuste si l'ordre diffère), repli sur indice.

| Usage | Indice | Nom | Placeholders exploités |
| --- | --- | --- | --- |
| Couverture | 8 | `40 - Couverture [1]` | idx0 titre · idx1 sous-titre · idx2 « OCTO Technology » · idx3 date |
| Slides de contenu | 5 | `04 - Titre seul` | idx0 titre (garde logo / pied de page / n° de slide) |

**Layouts « cadre blanc »** (idx 15–22, ex. `63 - Titre, contenu et visuel à
droite - cadre blanc`) : contiennent des **cadres photo à coins diagonaux**
(`round2DiagRect`, « ici mettre une Photo »). Non utilisés par l'export actuel ;
exploitables via le skill `pptx-framed-image` (clone le `prstGeom` du cadre sur
la picture). Voir mémoire *reference-octo-cadre-frame-layout*.

## 5. Chrome conservé (layout « Titre seul »)

Le générateur garde le chrome natif OCTO et dessine l'infographie dans la zone
de contenu (≈ `top 1.15 in` → `bottom 5.45 in`, marge latérale `0.55 in`) :

- **Logo** OCTO — coin haut-gauche.
- **Pied de page** vertical gauche — « OCTO | PART OF ACCENTURE© … All rights reserved ».
- **Badge n° de slide** — pastille navy, coin bas-droit. ⚠️ Contrainte de layout :
  tout contenu pleine largeur du bas s'arrête à `x ≈ 9.15 in` pour ne pas le
  recouvrir (`BORD_DROIT` dans le générateur).

## 6. Dépendance & repli

- `python-pptx==1.0.2` (épinglé dans `app/requirements.txt`), `Pillow` pour les
  images encadrées. Aucune dépendance réseau.
- Template résolu par ordre de priorité : 3ᵉ argument CLI → `$TEMPLATE_PPTX` →
  ce template OCTO par défaut. **Fournir un autre template** = fournir aussi son
  md compagnon dans `export/` (convention ci-dessus) ; la police et les couleurs
  s'adaptent automatiquement (détection thème + placeholders).

## 7. Vérifier ce md contre le fichier réel

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

*Lié : [`design-system-octo.md`](design-system-octo.md) (principes visuels),
[`ppt-toolkit.md`](ppt-toolkit.md) (kit agent+skills),
[`points-amelioration-ppt.md`](points-amelioration-ppt.md) (backlog qualité).*
