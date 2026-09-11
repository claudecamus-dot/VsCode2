# Catalogue de patterns — deck de formation OCTO Academy (fondamentaux IA générative)

Source : `FDXIA - Fondamentaux de l'IA Générative - 2026_03_19 - Première version.pdf`,
deck de formation OCTO Academy, 235 pages, gabarit 10×5.625in — même socle de thème que
`catalogue-restitution.md`/`template-octo.md`. Extraction par rendu PyMuPDF (planches-
contact de repérage puis pages isolées en zoom ×3) et lecture visuelle + échantillonnage
de pixels ciblé pour les couleurs. Couverture vérifiée : 235/235 pages passées par 15
planches-contact (grille 4×4), 26 pages candidates examinées en détail, 2026-09-04.

Ce catalogue est pertinent pour toute slide à vocation **pédagogique/technique**
(explication de concept, schéma d'architecture, exemple annoté) — un genre distinct des
decks de soutenance/proposition commerciale des deux autres catalogues de cette skill.

## Palette

Réutilise le socle navy/cyan de `template-octo.md` (`#0E2356` navy, `#00D2DD`/`#00AFCB`
cyan, échelle de slates `#3E4F78`/`#586586`/`#6E7B9A`/`#9FA7BB`/`#B7BDCC`/`#E7E9EE`,
`#FF0000` rouge alerte — valeurs confirmées identiques par échantillonnage pixel), étendu
d'une **palette secondaire de diagrammes de flux/nœuds** (absente d'OCTO_GROUND,
systématique dès qu'il s'agit de schématiser un pipeline ou un agent) : vert pâle
`#D9EAD3` (nœud de traitement/LLM), pêche `#FCE5CD` (nœud d'entrée/sortie), bleu pâle
`#CFE2F3` (nœud de décision/capacité). Plus quelques teintes rares : cyans pâles
`#B6E8F0`/`#DDF4F8` (diagrammes emboîtés), corail `#E7859F` (pictogrammes d'impact
environnemental), fond crème `~#F5F1EA` (une seule slide, rupture assumée pour signaler
un contenu « capture d'exemple »).

## Index situation → pattern

| Situation | Pattern (n°) |
| --- | --- |
| Expliquer une hiérarchie de concepts emboîtés | Diagramme de portée emboîtée (1) |
| Montrer un renversement conceptuel (avant/après logique) | Diagramme d'inversion de paradigme (2) |
| Dérouler une frise chronologique dense à paragraphes | Frise « pierre tombale » (3) |
| Grouper visuellement un sous-ensemble d'étapes | Pipeline sous accolade-parapluie (4) |
| Commenter un schéma technique repris tel quel | Diagramme annoté par lignes de rappel colorées (5) |
| Montrer une architecture à blocs répétés en relation N:1 | Tour de blocs à connexions en éventail (6) |
| Montrer une filiation technique par branches | Arbre généalogique ramifié encadré (7) |
| Illustrer une notion par des exemples courts groupés | Panneau d'exemples à pastilles de catégorie (8) |
| Montrer un exemple réel de sortie machine, annoté | Transcript annoté par légendes numérotées (9) |
| Présenter côte à côte 2 schémas externes complémentaires | Diptyque de schémas à réglette (10) |
| Trancher un compromis binaire | Liste avantages/inconvénients à pictos pouce (11) |
| Montrer une convergence d'entrées vers un concept central | Diagramme de convergence (12) |
| Comparer 3 niveaux d'un même spectre | Cartes de comparaison à 3 niveaux reliées par maillon (13) |
| Présenter un concept de prompt engineering répété en série | Slide-image à onglet pivoté + bulle de prompt (14) |
| Lister N qualités sans hiérarchie autour d'un objet central | Diagramme radial en fleur (15) |
| Présenter un framework à 3 composants fixes, répété en série | Triptyque à icône-question en pied de colonne (16) |
| Montrer une échelle de maturité avec détail par palier | Bandeau de maturité segmenté à panneaux (17) |
| Enseigner un flux en le construisant progressivement | Pipeline construit progressivement + bulle d'exemple (18) |
| Zoomer sur chaque composant d'un système cyclique, en série | Schéma-boucle avec panneau de zoom en recouvrement (19) |
| Montrer l'ajout progressif de capacités à un composant | Éventail à connecteurs courbes + accent unique (20) |
| Expliquer la structure d'un format de fichier technique | Explorateur de fichiers façon IDE + aperçu annoté (21) |
| Étayer une affirmation par des preuves réelles vérifiables | Collage de captures-preuves authentiques (22) |
| Présenter une échelle à N niveaux de sévérité/priorité | Pyramide pseudo-3D à connecteurs courbes (23) |
| Présenter un tableau chiffré avec valeurs à mettre en avant | Tableau à en-têtes pictographiques + anneau d'emphase (24) |
| Positionner N éléments sur UN seul axe de comparaison | Nuage d'étiquettes sur axe-continuum (25) |

---

## 1. Diagramme de portée emboîtée (poupées russes en escalier)

- **Situation/intention** : expliquer une hiérarchie de concepts emboîtés (IA ⊃ ML ⊃ DL
  ⊃ IA générative) et localiser un exemple connu dans cette hiérarchie.
- **Type** : bandes rectangulaires décroissantes empilées en cascade, chaque niveau
  restant visible sur toute sa hauteur derrière le suivant.
- **Composition** (p.30) : 4 `ROUNDED_RECTANGLE` de largeur décroissante (~9.6in →
  ~6.8in, hauteur ~0.85-1.1in) empilées en escalier haut→bas, décalées vers la
  droite/le bas, coin supérieur-gauche de chaque niveau toujours visible (post-it
  empilés). Dégradé du plus clair au plus foncé (`#B6E8F0` → `#DDF4F8` → gris clair →
  `#9FA7BB` slate au niveau le plus profond, seul niveau à porter un paragraphe
  explicatif). Niveau le plus profond entouré d'un contour rouge fin débordant
  légèrement, avec label rouge gras flottant hors-cadre.
- **Efficace parce que** : l'emboîtement visuel fait comprendre « est un sous-ensemble
  de » sans flèche ; le cadre rouge + label flottant localise un exemple concret sans
  retoucher le diagramme générique.

## 2. Diagramme d'inversion de paradigme (flux miroir avant/après)

- **Situation/intention** : expliquer un renversement conceptuel (ex. programmation
  classique vs machine learning) en montrant le MÊME schéma deux fois avec des
  entrées/sorties permutées.
- **Type** : deux flux identiques empilés verticalement, une variable échangeant de
  position (entrée ↔ sortie) d'un flux à l'autre.
- **Composition** (p.26) : 2 rangées identiques, chacune 2 pilules cyan pleines
  (`#00AFCB`, ~1.5×0.55in) → flèche → 1 rectangle navy plein (~3.3×1.6in) → flèche → 1
  pilule cyan de sortie. Rangée du bas : une variable passe en entrée (flèche
  pointillée, signalant l'incertitude) et une autre devient la SORTIE, encadrée d'un
  contour rouge sans fill pour souligner ce qui a changé de côté.
- **Efficace parce que** : répéter EXACTEMENT le même gabarit en ne permutant que la
  position d'un ou deux éléments rend un renversement conceptuel abstrait immédiatement
  visible par la symétrie rompue.

## 3. Frise chronologique en cartes « pierre tombale »

- **Situation/intention** : dérouler une frise chronologique dense où chaque étape
  porte un paragraphe descriptif complet, pas juste une date.
- **Type** : N cartes en forme de pierre tombale (bandeau d'en-tête soudé à un corps à
  bas arrondi) posées sur un axe temporel épais, reliées par un connecteur vertical fin.
- **Composition** (p.28, variante compacte p.218) : bandeau d'en-tête (~1.55×0.55in,
  dégradé gris selon importance, année+titre gras centré) soudé à un corps à bas
  demi-cercle (~1.55×3.3in, fill blanc, contour navy fin), texte descriptif aligné
  gauche. Ligne verticale navy fine vers un nœud (double cercle concentrique) posé sur
  une frise horizontale épaisse traversant toute la largeur, terminée par une flèche.
- **Efficace parce que** : la forme « pierre tombale » donne à chaque étape une
  identité de fiche complète — la tige vers l'axe ancre visuellement chaque paragraphe
  à sa date, même avec beaucoup de texte.

## 4. Étapes de pipeline sous accolade-parapluie

- **Situation/intention** : montrer une succession d'étapes techniques ET signaler
  qu'un sous-ensemble d'entre elles appartient conceptuellement à une même famille.
- **Type** : conteneurs égaux reliés par des flèches, chapeautés d'une accolade qui
  n'embrasse qu'une partie d'entre eux, avec un label au-dessus.
- **Composition** (p.43) : 3 conteneurs gris clair très arrondis (~3.1×3.9in), titre
  gras centré, contenant chacun un diagramme de référence miniature. Flèches épaisses
  grises entre les conteneurs. Au-dessus des 2 derniers conteneurs SEULEMENT, une
  accolade en freeform bleue (courbe en U inversé, style calligraphique, ~5.3in),
  surmontée du label bleu gras.
- **Efficace parce que** : l'accolade groupe visuellement un sous-ensemble d'étapes
  sans dupliquer de cadre englobant ni recolorer les boîtes.

## 5. Diagramme technique annoté par lignes de rappel colorées

- **Situation/intention** : expliquer un schéma technique déjà publié (repris tel quel)
  sans le redessiner, en pointant seulement les zones à commenter.
- **Type** : diagramme de référence embarqué + rectangles d'annotation reliés par une
  flèche colorée à la zone qu'ils expliquent, code couleur cohérent par thème.
- **Composition** (p.59, schéma Transformer reproduit) : diagramme complet au
  centre-droit. 2 rectangles fill blanc, contour épais coloré (rouge « Encoder », vert
  « Decoder »), texte gras assorti, positionnés hors du diagramme. Chacun relié à sa
  zone par une ligne de la MÊME couleur que son contour, diagonale libre.
- **Efficace parce que** : le code couleur (bordure texte = bordure zone visée =
  couleur de ligne) crée un lien visuel instantané sans numérotation ni légende séparée.

## 6. Tour de blocs répétés avec connexions en éventail

- **Situation/intention** : montrer qu'une architecture empile N fois le même bloc, et
  qu'un bloc particulier communique avec PLUSIEURS instances de l'autre pile.
- **Type** : deux tours de rectangles identiques empilés, reliées par des flèches
  montantes internes ET des flèches transversales en éventail depuis un seul bloc
  source vers tous les blocs de l'autre tour.
- **Composition** (p.60) : 2 colonnes de 6 rectangles identiques chacune (rose pâle/
  contour rouge sombre « ENCODER », vert pâle/contour vert sombre « DECODER »), flèches
  verticales internes. Le bloc ENCODER du haut envoie 6 flèches en éventail vers CHACUN
  des 6 blocs DECODER.
- **Efficace parce que** : répéter un unique gabarit de bloc N fois exprime « N couches
  identiques » sans légender chaque niveau ; l'éventail depuis un seul bloc montre une
  relation « tous-avec-un » en un coup d'œil.

## 7. Arbre généalogique ramifié à branches encadrées

- **Situation/intention** : montrer une filiation technique de N variantes issues d'une
  racine commune, regroupées par branche et par usage.
- **Type** : arbre à 1 racine → nœuds intermédiaires → branches parallèles de
  descendants, chaque branche encadrée et annotée par son usage.
- **Composition** (p.62) : racine = rectangle gris clair contenant 2 sous-blocs
  colorés reliés, flèches descendantes vers 3 colonnes de rectangles colorés
  représentant des modèles, empilés en paires reliées par un tiret quand deux modèles
  partagent un niveau de filiation. Les 3 colonnes chacune entourée d'un contour rouge
  sans fill. Rectangles d'annotation d'usage reliés par une ligne rouge diagonale.
- **Efficace parce que** : le cadre rouge autour de chaque colonne fait lire « famille »
  avant même de lire les noms ; l'annotation d'usage reliée à la famille entière évite
  de répéter la légende N fois.

## 8. Panneau d'exemples encadré à pastilles de catégorie

- **Situation/intention** : illustrer une notion abstraite par une série d'exemples
  concrets courts, sans les mélanger au texte explicatif principal.
- **Type** : colonne de texte explicatif + grand panneau encadré séparé listant des
  paires « catégorie (pilule) → exemple(s) ».
- **Composition** (p.71) : conteneur fill blanc, contour navy épais, grand titre centré
  gras en tête. Empilement de paires : pilule fill slate (texte blanc gras) à gauche,
  1-2 lignes d'exemple en gras navy à droite, espacement généreux entre paires.
- **Efficace parce que** : isoler les exemples dans un cadre à part laisse le texte
  explicatif rester dense sans que les exemples paraissent secondaires.

## 9. Transcript d'exemple annoté par légendes numérotées

- **Situation/intention** : montrer un exemple réel de sortie/raisonnement d'un modèle
  et commenter des lignes précises sans surcharger le rendu du transcript lui-même.
- **Type** : bloc(s) de texte façon « sortie machine » (bordé, italique) + petites
  légendes numérotées colorées positionnées à côté, sans ligne de connexion.
- **Composition** (p.86) : 2 blocs empilés contour cyan épais, texte intérieur gras
  italique respectant les sauts de ligne source. À droite de chaque bloc, numéro +
  courte légende colorée, SANS ligne ni flèche — la proximité positionnelle fait le
  lien.
- **Efficace parce que** : le contour cyan uniforme fait lire les blocs comme « ce que
  la machine a réellement produit » ; les légendes sans ligne de rappel gardent le
  transcript lisible.

## 10. Diptyque de schémas de référence à réglette

- **Situation/intention** : présenter côte à côte 2 schémas externes complémentaires
  (vue globale + vue détaillée) sans les fusionner en un diagramme retouché.
- **Type** : deux images/diagrammes encadrés côte à côte, séparés par une réglette
  verticale, chacun sous-titré.
- **Composition** (p.92) : 2 diagrammes reproduits fidèlement, séparés par une ligne
  verticale fine navy centrée. Sous chaque diagramme, légende centrée, texte souligné
  décrivant précisément ce que montre CE schéma.
- **Efficace parce que** : la réglette centrale suffit à dire « deux vues différentes
  du même sujet » ; la légende soulignée fait office de source/preuve.

## 11. Liste avantages/inconvénients à pictogrammes pouce

- **Situation/intention** : trancher un compromis (avantages/limites) de façon binaire
  et immédiatement identifiable.
- **Type** : 2 colonnes symétriques, chacune surmontée d'un grand pictogramme pouce
  (haut/bas) + titre, puis liste à puces à accroche grasse.
- **Composition** (p.93) : icônes ligne-art « pouce » levé/baissé, détail rectangulaire
  accent cyan sur le poignet, suivies du titre gras. Liste à puces rondes, motif
  « accroche grasse : complément » répété.
- **Efficace parce que** : le pictogramme pouce est un langage universel qui évite un
  code couleur vert/rouge parfois moins accessible.

## 12. Diagramme de convergence vers un concept central

- **Situation/intention** : montrer que plusieurs entrées de nature différente
  convergent vers UN concept/composant central unique.
- **Type** : N icônes périphériques reliées par des lignes convergentes vers une forme
  centrale pleine.
- **Composition** (p.94) : 3 icônes ligne-art dans un cercle gris pâle contour fin,
  disposées en triangle lâche, chacune reliée par une ligne fine diagonale (flèche
  seulement au point d'arrivée) convergeant TOUTES vers le même point d'un grand
  rectangle fill cyan plein.
- **Efficace parce que** : converger plusieurs flèches vers un seul point renforce
  visuellement l'idée de « fusion en un espace commun ».

## 13. Cartes de comparaison à 3 niveaux reliées par un maillon

- **Situation/intention** : comparer 3 niveaux d'un même spectre (positions sur un
  axe), pas 3 alternatives indépendantes.
- **Type** : 3 cartes à en-tête, quasi jointives, reliées par un petit connecteur
  circulaire à cheval sur la frontière de deux cartes consécutives.
- **Composition** (p.95) : 3 cartes quasi identiques (contour navy fin, fill blanc),
  bandeau d'en-tête délimité par une ligne fine, titre centré gras. Entre chaque paire
  de cartes adjacentes, un petit disque plein cyan posé à cheval sur le bord vertical
  de séparation — comme un maillon de chaîne.
- **Efficace parce que** : le petit disque à cheval sur la jointure suffit à dire
  « c'est un dégradé, pas 3 choix isolés » sans flèche continue ni fond dégradé.

## 14. Slide-image à onglet latéral pivoté et bulle de prompt

- **Situation/intention** : présenter, de façon répétée sur une série de slides, un
  concept de prompt engineering illustré par une image générée, en montrant le prompt
  exact utilisé sans qu'il domine visuellement l'image.
- **Type** : image pleine largeur en bas, étiquette de contexte pivotée verticalement en
  marge, bulle de prompt encadrée en haut.
- **Composition** (p.100, série p.97-106) : mot-clé (« Prompt ») tourné à 90°, gras,
  plaqué contre le bord du cadre-bulle (onglet de classement). Bulle contenant le texte
  littéral du prompt, partie « rôle » en cyan gras italique, reste en navy gras
  italique. Image générée sous la bulle, en carré quasi plein.
- **Efficace parce que** : l'étiquette pivotée fait reconnaître immédiatement le type de
  contenu même en scannant vite plusieurs slides similaires.

## 15. Diagramme radial en fleur (hub + pétales)

- **Situation/intention** : lister N qualités/arguments en faveur d'un objet central
  unique, sans hiérarchie entre eux.
- **Type** : cercle central plein entouré d'un anneau de N ovales-pétales à contour
  seul, légèrement chevauchants, façon fleur.
- **Composition** (p.110) : cercle central plein cyan portant un logo/pictogramme,
  entouré de 8 ovales contour seul disposés en cercle régulier, chacun chevauchant
  légèrement le cercle central et ses 2 voisins immédiats — pas d'espace ni de
  connecteur explicite.
- **Efficace parce que** : le chevauchement physique des pétales avec le centre
  remplace toute flèche de connexion.

## 16. Triptyque de framework à icône-question en pied de colonne

- **Situation/intention** : présenter un framework méthodologique à 3 composants fixes
  qui se répète identique à travers plusieurs slides avec un contenu différent.
- **Type** : 3 colonnes séparées par une réglette pointillée, chacune = grande icône
  bicolore + label + contenu libre + question-titre en pied de colonne.
- **Composition** (p.149) : 3 colonnes séparées par des lignes pointillées ; icône
  ligne-art bicolore en haut de chaque colonne, label majuscule gras cyan dessous, bloc
  de texte libre, puis tout en bas une question centrée gras navy qui NOMME la fonction
  de la colonne.
- **Efficace parce que** : poser la question en PIED de colonne (plutôt qu'en tête) fait
  relire la colonne comme une réponse une fois arrivé en bas — effet de bouclage.

## 17. Bandeau de maturité segmenté à panneaux de détail

- **Situation/intention** : montrer une progression/échelle de maturité à N paliers ET,
  pour chaque palier, donner un mini-schéma illustrant concrètement ce niveau.
- **Type** : bandeau segmenté horizontal en dégradé de teinte + sous-étiquette
  d'accroche + panneau pointillé à diagramme miniature, un triplet complet par palier.
- **Composition** (p.153) : bandeau de 4 segments jointifs, dégradé gris-slate
  clair→foncé, titre blanc gras centré. Au-dessus, UNIQUEMENT sur les 3 derniers
  segments, un ruban fin en dégradé cyan signalant un sous-ensemble concerné. Sous
  chaque segment, ligne verticale vers légende puis panneau contour POINTILLÉ contenant
  un mini-diagramme.
- **Efficace parce que** : le ruban cyan qui ne couvre QUE 3 segments sur 4 fait une
  deuxième affirmation par-dessus la première sans texte additionnel.

## 18. Pipeline construit progressivement avec bulle d'exemple

- **Situation/intention** : enseigner un concept de flux en le CONSTRUISANT
  progressivement sur plusieurs slides consécutives (une slide = le diagramme + 1
  nouveau nœud), en illustrant chaque nouvelle étape par un exemple concret.
- **Type** : diagramme de flux horizontal à nœuds de forme/couleur codée par rôle,
  complété nœud par nœud à travers une série de slides, avec un encart d'exemple
  rattaché au dernier nœud ajouté.
- **Composition** (p.166, série p.161-167) : pilules fill pêche pour les nœuds
  terminaux, rectangles fill vert pâle pour les nœuds de traitement, bleu pâle pour les
  nœuds de décision. Flèche pleine pour le chemin principal, pointillée pour les
  branches secondaires. Le dernier nœud ajouté relié par une ligne diagonale à un grand
  encart (contour navy, fill blanc) avec pictogramme + label « Prompt » et texte réel.
- **Efficace parce que** : garder rigoureusement le MÊME code couleur sur toute la
  série permet de lire un diagramme de plus en plus complexe sans réapprendre une
  légende à chaque nouvelle slide.

## 19. Schéma-boucle réutilisé avec panneau de zoom en recouvrement

- **Situation/intention** : enseigner un système cyclique fixe en zoomant
  successivement sur CHAQUE composant à travers une série de slides, sans redessiner le
  diagramme de base à chaque fois.
- **Type** : un diagramme de référence identique répété sur plusieurs slides,
  partiellement RECOUVERT par un grand panneau flottant qui zoome sur le composant en
  cours de discussion.
- **Composition** (p.188, série p.177-192) : diagramme de base (boucle) dans le tiers
  gauche de la slide. Un grand rectangle (contour navy épais, fill blanc) posé PAR-DESSUS
  la partie droite du diagramme (masquant partiellement un bloc), contenant sous-titre
  centré et un exemple structuré en blocs imbriqués à bordure colorée.
- **Efficace parce que** : le panneau qui RECOUVRE une partie du schéma de base crée un
  effet de loupe/rideau ; garder le même diagramme identique à travers la série
  construit une mémoire visuelle qui rend chaque slide plus rapide à lire.

## 20. Diagramme en éventail à connecteurs courbes et accent unique

- **Situation/intention** : montrer qu'un composant central s'est vu ajouter, au fil des
  versions, de plus en plus de capacités périphériques de même nature, et signaler
  LAQUELLE vient d'être ajoutée.
- **Type** : hub central relié par des connecteurs COURBES à un nuage scatter de boîtes
  de capacité, une seule connexion mise en accent couleur.
- **Composition** (p.199, série p.198-205) : flux central IN→AGENT→OUT sur une ligne
  horizontale haute. Sous « AGENT », 6-8 rectangles bleu pâle dispersées en éventail
  lâche (positions irrégulières), chacune reliée par une ligne courbe (bézier) fine
  grise convergeant vers un point d'ancrage unique. UNE seule connexion redessinée en
  épais cyan vif.
- **Efficace parce que** : les courbes (plutôt que des coudes à 90°) évitent l'effet
  organigramme rigide ; l'unique connecteur en accent applique le mécanisme « un sur N
  en accent » au TRAIT de connexion, plus discret qu'une boîte entière recolorée.

## 21. Explorateur de fichiers façon IDE avec aperçu de document annoté

- **Situation/intention** : expliquer la structure d'un format de fichier/dossier
  technique en montrant à la fois son arborescence ET le contenu annoté d'un des
  fichiers qui la compose.
- **Type** : mini panneau « explorateur de fichiers » à gauche (arborescence façon
  terminal) relié à un aperçu de document coloré par section à droite.
- **Composition** (p.204, seule slide à fond crème du deck, rupture assumée pour
  signaler une « capture d'exemple ») : à gauche, panneau listant des fichiers en police
  monospace avec préfixes `├`/`└` d'arborescence, chaque type de fichier coloré
  différemment. À droite, panneau document scindé en 2 zones colorées horizontalement
  (en-tête de métadonnées, corps), texte monospace réaliste.
- **Efficace parce que** : reproduire les codes visuels d'un IDE rend un format de
  fichier abstrait immédiatement reconnaissable pour un public technique.

## 22. Collage de captures-preuves authentiques

- **Situation/intention** : étayer une affirmation de risque/tendance par des preuves
  RÉELLES et vérifiables plutôt que par des icônes ou des statistiques.
- **Type** : collage libre de captures d'écran authentiques regroupées par
  sous-catégorie, tailles et positions hétérogènes, légendées.
- **Composition** (p.208, variante p.233) : titre de sous-catégorie en gras cyan, 1 à 3
  images RÉELLES insérées sans cadre supplémentaire (habillage d'origine visible —
  bandeau, date, logo média), tailles variables, positions non alignées sur une grille
  commune.
- **Efficace parce que** : garder l'habillage d'origine des captures est ce qui les
  rend crédibles comme PREUVES et non comme illustrations.

## 23. Pyramide en volume pseudo-3D à connecteurs courbes

- **Situation/intention** : présenter une échelle à N niveaux de sévérité/priorité où
  chaque niveau a un régime de traitement différent à détailler en texte.
- **Type** : pyramide tronquée en volume (faces éclairées différemment) + connecteurs
  courbes vers des étiquettes de niveau et une description externe.
- **Composition** (p.216, 4 niveaux) : pyramide composée de 4 troncs empilés en volume
  isométrique (face avant plus claire, face latérale plus sombre), du sommet (le plus
  petit, navy foncé) à la base (la plus large, slate). Chaque niveau relié par une ligne
  COURBE en S vers un label de la MÊME couleur que le niveau.
- **Efficace parce que** : le volume pseudo-3D donne à chaque niveau un « poids »
  physique cohérent avec la notion de sévérité ; le connecteur courbe évite que les
  lignes ne se chevauchent.

## 24. Tableau de données à en-têtes pictographiques et anneau d'emphase

- **Situation/intention** : présenter un tableau de données chiffrées où certaines
  valeurs méritent d'être mises en avant sans construire un vrai graphique.
- **Type** : tableau à en-têtes de colonne illustrés par un pictogramme + anneau
  décoratif non chiffré derrière les valeurs les plus significatives.
- **Composition** (p.225) : ligne d'en-tête fill slate, pictogramme ligne-art bicolore
  centré au-dessus du libellé de colonne. Lignes de corps : cellules de valeur en
  pourcentage gras navy ; DERRIÈRE les valeurs jugées notables, un ovale sans fill,
  contour gris clair, façon anneau de jauge incomplet, purement décoratif.
- **Efficace parce que** : le pictogramme en en-tête rend le tableau scannable même
  sans lire les libellés ; l'anneau décoratif imite une jauge circulaire pour attirer
  l'œil sans construire de vrais graphiques.

## 25. Nuage d'étiquettes dispersées sur un axe-continuum

- **Situation/intention** : donner une vue d'ensemble impressionniste d'un écosystème
  dense d'outils/options, positionnés qualitativement sur UN seul axe de comparaison
  (pas 2, contrairement à une matrice), sans précision de coordonnées.
- **Type** : étiquettes de texte à fond surligné, dispersées en nuage au-dessus d'une
  flèche horizontale à 2 pôles nommés, hauteur = seule variable de positionnement.
- **Composition** (p.176) : ~20 étiquettes de nom d'outil, texte gras navy sur fond
  rectangulaire plat gris-bleu clair (façon surlignage, sans bordure), positionnées à
  des hauteurs variées au-dessus d'une ligne horizontale grise fine avec flèche, 2
  labels de pôle en cyan gras aux extrémités.
- **Efficace parce que** : le fond « surligneur » laisse les mots se chevaucher comme un
  vrai nuage ; l'unique axe horizontal simplifie la lecture sans juger un 2e critère.
