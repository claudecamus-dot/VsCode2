---
name: deck-design-review
description: Slide-by-slide design review of THIS project's generated restitution deck — build the real export, render EVERY slide, and walk each slide type against its own design contract (cover, sommaire, chapter heads, exec, synthèse, difficultés, SWOT, verbatims, axes, priority matrix, fiches reco). Use before declaring any deck design change done, when the user reports the exported PPT "n'est pas au niveau", or as the review step of the export-ppt-verifie playbook.
---

# deck-design-review — la revue de design du deck ENTIER

`pptx-verify` dit **comment** regarder (rendre + zoomer + checklist générique) ;
`restitution-deck-design` dit **ce qui fait pro** en général. Ce skill ajoute ce
qui manquait (constat 2026-07-22, plusieurs allers-retours « toujours KO ») : le
**contrat par slide de CE deck**, pour que chaque type de slide soit revu contre
SA définition — pas une impression d'ensemble.

## 0. Sur le BON artefact, TOUTES les slides

- Exporter depuis **l'app qui tourne** (`curl …/missions/{id}/export/pptx`), pas
  seulement un `build_presentation()` local — et comparer les deux si le doute
  existe : s'ils diffèrent, le serveur est périmé
  ([[feedback-stale-dev-server-root-cause]], [[feedback-verify-the-real-app-export-all-slides]]).
- Rendre **toutes** les slides (`render-pptx.ps1`), pas un échantillon. Les
  slides de suite (« … (suite) ») comptent : leur multiplication est un signal de
  layout trop gourmand, pas un détail.

## 1. Contrat par type de slide

| Slide | Contrat (au rendu) |
| --- | --- |
| Couverture | Layout de marque « 40 - Couverture » : photo pleine page, overlay navy, teardrop, titre = nom de mission, date. Jamais le repli dessiné sur un deck OCTO. |
| Sommaire | 4 chapitres, pastilles teardrop numérotées 01-04 colorées, sections listées sous chacun. Parité stricte avec l'onglet aperçu web. |
| Tête de chapitre | Layout « 50 - Chapitre » : numéro DANS l'encart logo (pill), titre coloré + sous-titre italique, vraie photo clippée au teardrop. Le numéro est une exigence PERSISTANTE ([[feedback-persistent-user-request-draw-dont-omit]]). |
| Executive summary | Format VSCode3 : claim navy + sous-claim italique + cartes à liseré coloré, ancrées en bas. |
| Synthèse (×5) | Carte de puces à liseré + **vraie photo** (jamais l'aplat procédural — cache `_photo`/`_proc` séparés) clippée round2DiagRect + encart gris « à retenir ». |
| Difficultés | Cartes ambre chip-numérotées, insert citation teal si verbatim lié. |
| Matrice SWOT | Contrat du skill **swot-matrix** : axes Interne/Externe × Favorable/Défavorable, cellules teintées, zéro vide blanc. |
| Paroles d'acteurs | Cartes-citations dimensionnées au contenu, liseré teal, attribution. |
| Axes (vue d'ensemble) | Une rangée par axe, couleur d'identité = palette. |
| Matrice de priorisation | Contrat du skill **priority-matrix** : quadrants de sens teintés, bulles couleur-d'axe numérotées, AUCUN chart Excel, aucune bulle masquée. |
| Fiches reco | Encarts arrondis OCTO : carte gauche à liseré couleur d'axe (objectif/acteurs/jauges/résultats), encart gris « proposition », carte « plan ». Liseré = couleur de l'axe partout (fiche ↔ matrice ↔ vue d'ensemble). |

## 2. Transversal (tout le deck, à chaque revue)

1. **Police** : police du THÈME partout (Arial sur OCTO) — jamais une police non
   installée forcée (elle rend en substitution). Vérif rapide :
   `typeface=` compté dans le zip → une seule famille attendue.
2. **Échelle** : titres à `D.TYPE["title"]` (20), aucun point-size littéral hors
   `D.TYPE` ; espacement cohérent de slide en slide (restitution-deck-design §2).
3. **Couleur = un seul métier** : identité (palette d'axes) vs sémantique
   (vert/rouge/ambre) jamais mélangées.
4. **Composants identiques partout** : add_card/add_encart/add_badge — un
   composant qui diffère d'une slide à l'autre est un défaut.
5. **Chrome** : rien ne touche le badge n° de page (bas-droite), le logo, le
   pied vertical. Zoomer ces zones (crop) au moindre doute.
6. **Images** : réelles (Openverse) et photographiques — une illustration/clipart
   qui se glisse dans une requête générique est un défaut (biaiser la requête
   « photography »), le procédural n'est acceptable qu'offline.
7. **Slides de suite** : leur nombre doit rester stable après un changement de
   layout — s'il augmente, le layout a perdu de la place utile.

## 3. Boucle

Rendre → défauts listés par n° de slide (crops si subtil) → corriger → re-rendre
→ **re-regarder** (jamais « corrigé » sans re-rendu). Les invariants découverts
deviennent des tests dans `tests/test_deck_qualite.py` (section « revue de design
automatisée ») — le test verrouille le défaut trouvé, l'œil trouve le suivant.
Pour un changement d'intention de design : validation UTILISATEUR sur le rendu
réel avant commit (pptx-verify §6, [[feedback-non-convergence-user-is-oracle]]).
