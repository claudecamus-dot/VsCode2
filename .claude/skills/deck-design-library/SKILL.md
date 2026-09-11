---
name: deck-design-library
description: Bibliothèque de patterns de design de slides extraite de decks OCTO réels — 90 représentations cataloguées par SITUATION à travers 4 fichiers par genre de deck (soutenance/restitution, proposition commerciale, formation technique, formation méthode/atelier), avec composition précise (formes, tailles, couleurs, typo) réutilisable en python-pptx. À consulter AVANT de dessiner une nouvelle slide ou d'améliorer une slide existante — quand on se demande « quelle forme donner à ce contenu ? », quand une slide est un mur de texte/de cartes sans idée directrice, ou pour varier des représentations trop uniformes.
---

# deck-design-library — quelle représentation pour quelle situation

Une bibliothèque de patterns éprouvés (decks OCTO réels — soutenance, proposition
commerciale, formation) pour choisir la FORME d'une slide à partir de son INTENTION,
puis la construire en python-pptx. Complémentaire de : `restitution-deck-design`
(principes généraux), `pptx-deck` (helpers + géométrie), `deck-design-review` (contrat
par slide de CE projet), `swot-matrix`/`priority-matrix` (patterns déjà spécialisés).

## Méthode

1. **Partir de l'intention, jamais de la forme** : qu'est-ce que la slide doit faire
   comprendre ? (diagnostiquer, comparer, dérouler dans le temps, chiffrer, incarner…)
2. **Identifier le GENRE de deck le plus proche** (soutenance/restitution, proposition
   commerciale, formation technique, formation méthode/atelier — voir tableau
   « Quel catalogue pour quel genre » ci-dessous), **chercher la situation dans son
   index** et lire l'entrée complète du pattern dans le fichier `references/` visé
   (composition précise : formes, tailles en pouces, couleurs, typo).
3. **Transposer, pas copier** : adapter les cotes au gabarit du deck cible
   (ce projet : 10×5.625in OCTO, helpers `pptx_deck` — `add_card`, `add_rect`,
   `add_badge`, `estimer_lignes`…) en respectant sa charte (pas d'ombres, police du
   thème, `verifier_geometrie` + `verifier_debordements_texte` à zéro). Couleurs,
   polices et indices de layouts exacts du template partagé (flotte) :
   `references/template-octo.md`.
4. **Vérifier au rendu réel** (`pptx-verify` / `deck-design-review`) — un pattern
   bien choisi mais mal dimensionné reste un défaut.

## Index situation → pattern

| Situation | Pattern (n° du catalogue) |
| --- | --- |
| Présenter l'équipe / le dispositif humain | Trombinoscope photos rondes (1) ; Dispositif + badges % staffing (20) |
| Restituer des verbatims / la parole recueillie | Mur de verbatims scatter sur photo (2) |
| Chiffrer des bénéfices / KPI | Grille de cartes stat 2×3, une en accent (3) |
| Diagnostic à N facteurs / freins | Flux numéroté en quinconce, badges TEAR (4) |
| Modèle d'organisation cible | Sandwich gouvernance/piliers (5) ; Flowchart bandes + flèches cyan (9) |
| Démarche séquentielle + message clé | Pilules verticales connectées + encart (6) |
| Décisions à faire prendre | Rangée de cartes sur bandeau, une en accent (7) |
| Architecture / vision en couches | Blueprint 3 bandes teintées (8) |
| Démarche en grandes étapes détaillées | Fiches-étapes à chip chevauchant (10) |
| Trajectoire macro dans le temps | Colonnes de phase × lignes de catégorie (11) |
| Planning fin (semaines) | Mini-Gantt pilules + frise de points (12) |
| Cadrage d'une phase (objectifs/prérequis/livrables) | Fiche à rubriques icône+étiquette+carte (13) |
| Processus en sous-étapes avec fil rouge | Chips numérotés + UNE carte à colonnes (14) |
| Référentiel / grille d'évaluation | Grille de maturité à libellés par ligne (15) |
| Cycle de vie continu | Bandeau chevron + 3 colonnes (16) |
| Positionnement + dynamique sur 2 axes | Matrice à vecteurs de trajectoire (17) |
| Catalogue d'offres / formations | Colonnes à en-tête plein + bandeaux transverses (18) |
| Bibliothèque dense d'outils/méthodes | Toolkit map en bandes dégradées à onglets (19) |
| Prix / proposition financière | Fiche « ticket/coupon » (21) |
| Avantage commercial / bonus | Médaillon rosette dédié (22) |

## Quel catalogue pour quel genre de deck

| Genre de deck source | Fichier | Patterns | Bon pour |
| --- | --- | --- | --- |
| Soutenance de transformation (client, anonymisé) | `references/catalogue-restitution.md` | 22 | Restituer, diagnostiquer, chiffrer, présenter une équipe, planifier |
| Proposition commerciale (transfo mode produit) | `references/catalogue-transformation-commerciale.md` | 14 | Convaincre un sponsor, cadrer une gouvernance, montrer un avant/après |
| Formation technique (fondamentaux IA générative) | `references/catalogue-formation-ia-generative.md` | 25 | Expliquer un concept, annoter un schéma technique, dérouler un pipeline |
| Formation méthode/atelier (devenir Product Owner) | `references/catalogue-formation-po.md` | 29 | Personas, story mapping, canvas d'atelier, artefacts Agile/Scrum |

Les 3 derniers fichiers ont été ajoutés le 2026-09-04 (source : 2 decks de formation
+ 1 deck de proposition commerciale fournis en PDF, plus une exploration graphique de
VSCode3 sur le même template — voir leur en-tête pour la provenance exacte et la
méthode d'extraction). Chaque fichier porte son propre index situation → pattern en
tête, sur le même principe que celui-ci.

## Principes transversaux à appliquer d'office

Détail complet en fin de `references/catalogue-restitution.md` — les 4 plus structurants :

- **Titre = sujet, sous-titre = claim** : l'idée-force vit dans le sous-titre en
  phrase complète (Minto), sauf slides de référence denses (label court).
- **« Un sur N en accent »** : dans toute série d'éléments égaux, UN SEUL reçoit un
  fill plein cyan/navy — c'est le mécanisme de hiérarchie n°1, avant la taille de police.
- **Couleur = sémantique** : navy encre, cyan accent/flux, orange surlignage d'un mot
  à enjeu, rouge argent/alerte, gris clair fond de support. Jamais décorative.
- **La densité s'absorbe par la police (jusqu'à 6-8pt sur une slide de référence),
  JAMAIS par les marges externes** (~0.6in constants).

**Vocabulaire de composants étendu** (contour vs aplat, pilule, chevron, badge à
cheval sur un bord, emphase en ligne, barre d'accent, bandeau de clôture citation) :
7 règles en tête de `references/catalogue-transformation-commerciale.md` — spécifique
au registre navy/cyan proposition commerciale, pas universel à tous les genres de deck.

## Enrichir la bibliothèque

Nouveau deck de référence disponible ? Reproduire la méthode : inventaire programmatique
python-pptx (formes/positions/couleurs par slide), catalogage par situation → composition
→ ce qui rend le pattern efficace, puis ajouter un `references/catalogue-<nom>.md` et
étendre l'index ci-dessus. Dédupliquer avec les patterns existants (noter seulement les
variantes).
