# Design system OCTO — principes appliqués au deck de restitution

> **Rôle** : version **versionnée dans le repo** des principes visuels OCTO à
> respecter dans l'export PPT. Source amont (non versionnée, plus complète, mais
> orientée **HTML/CSS**) : `octo_design_system_v2.md` + `octo_slide_patterns_v2.md`
> (analyse des decks Weavenn déc. 2025 + REX Louis Vuitton mai 2026).
> **Ici on porte les *principes*, pas le HTML** : notre sortie est un `.pptx`
> construit sur le template OCTO (python-pptx), pas des slides HTML.
>
> Décision projet (2026-07-08) : **le `.pptx` OCTO fait foi** ; on enrichit le
> générateur avec ces principes de façon incrémentale. Voir
> [`points-amelioration-ppt.md`](points-amelioration-ppt.md).

---

## 1. Couleurs — venir du thème du template, pas de constantes

La palette de la charte **est déjà dans le thème** du template (voir
[`template-octo.md`](template-octo.md) §2). Règle : **lire le thème**
(`pptx_deck.theme_colors`), ne pas coder de gris génériques.

| Usage chrome / structure | Couleur | Slot thème |
| --- | --- | --- |
| Texte principal, titres | Navy `#0E2356` | `dk1` |
| Accent (dots, filets, barres d'accent) | Cyan `#00D2DD` | `accent3` |
| Texte secondaire / labels | slate 600 `#586586` | `lt2` |
| Filets / bordures de cards | slate 200/300 | `accent5` / `accent4` |
| Fonds d'encarts, pistes de barres | slate 100 `#E7E9EE` | `accent6` |

**Données (catégoriel)** : la palette **multicolore par pilier**
(`pptx_deck.PALETTE`) reste séparée du chrome — elle est **alignée sur le radar**
(barres et radar « parlent la même couleur »). Principe dataviz : couleurs de
données ≠ couleurs de marque. On ne remplace donc PAS les couleurs de pilier par
navy/cyan.

**Sémantique** (points forts / à renforcer / dispersion) : vert `OK`, rouge
`WARN`, or `GOLD` — fonctionnelles, conservées.

## 2. Règles dures (à ne jamais enfreindre)

| Interdit | Pourquoi |
| --- | --- |
| **Gradient** dans une forme/card | Charte = aplat plein uniquement |
| **Drop shadow / ombre** | Différenciation **par bordure** uniquement (`_no_shadow` déjà en place) |
| Headers de cards blancs ou slate | Toujours cyan ou navy |
| Corps de card coloré | Toujours blanc |
| Police < ~10,5 pt pour du texte lisible | Illisible à l'échelle slide |
| Coins arrondis asymétriques ad hoc | Rayon uniforme cohérent sur le deck |

> Ces deux premières lignes **corrigent une intuition courante** : « formes plus
> travaillées » en langage OCTO ne veut PAS dire dégradés/ombres — mais système
> de cards net, bordures franches, accents cyan, **icônes outline**, labels
> uppercase à letter-spacing.

## 3. Typographie

- Police : **Outfit** (détectée depuis le template), bold pour l'emphase.
- Échelle : `pptx_deck.TYPE` (source unique). Titres navy, labels uppercase en
  cyan/slate, corps navy.
- Labels de section : **UPPERCASE** + petit filet dessous (helper `_surtitre`).

## 4. Composants (grammaire OCTO transposée en python-pptx)

| Composant HTML OCTO | Équivalent deck |
| --- | --- |
| Card (header teal/navy + body blanc + border) | `pptx_deck.add_card` (liseré accent + bordure, sans ombre) |
| Label uppercase + accent | `_surtitre` (label + filet) |
| KPI / gauge | `add_gauge` (anneau doughnut) |
| Barres / jauges de progression | `add_hbar`, `add_range_bar` |
| Dots de légende | `add_dot` |

Patterns de slides OCTO (`octo_slide_patterns_v2.md`) utiles comme **inspiration
de mise en page** : `TROIS_CARDS_ETAPES`, `COMPARAISON_2_OPTIONS`, `KPI_GRID`,
`MATRICE_CONTEXTE_CARDS`. Ils sont en HTML → à traduire, pas à copier.

## 5. Ce qui est déjà conforme / ce qui reste

- ✅ Pas d'ombre (`_no_shadow`), coins arrondis cohérents, échelle typo unique,
  labels uppercase, contrôle géométrique.
- ✅ Police de marque appliquée au contenu (2026-07-08).
- ✅ Neutres sourcés du thème (navy/slate) + accent cyan (2026-07-08) — voir backlog.
- ✅ Radar vectoriel natif (formes python-pptx, plus de PNG rasterisé) (2026-07-08).
- ⏳ Icônes outline par pilier, cadres `round2DiagRect`.

*Lié : [`template-octo.md`](template-octo.md), [`ppt-toolkit.md`](ppt-toolkit.md),
[`points-amelioration-ppt.md`](points-amelioration-ppt.md).*
