# Catalogue de patterns — deck de soutenance de transformation (référence anonymisée)

Source : un deck de soutenance commerciale OCTO réel — transformation vers une
organisation produit — analysé puis anonymisé (le document source n'est pas conservé ;
47 slides, gabarit OCTO 10×5.625in). Extraction par analyse géométrique/chromatique
programmatique (python-pptx, 2026-07-22) : positions, tailles, formes, couleurs résolues
depuis le thème et texte par run — pas un rendu pixel, mais exactement le niveau de
détail nécessaire pour reproduire un pattern en python-pptx.

## Palette de thème résolue (référence pour tout le catalogue)

- `DARK_1` = `#0E2356` (navy — encre principale, contours de cartes)
- `LIGHT_1` = `#FFFFFF` (blanc — fond de carte par défaut)
- `DARK_2` = `#3E4F78` / `LIGHT_2` = `#586586` (navys secondaires, connecteurs discrets)
- `ACCENT_1..2` = `#6E7B9A` / `#9FA7BB` (slate intermédiaires)
- `ACCENT_3` = `#00D2DD` (cyan — LA couleur d'accent/action/mise en avant)
- `ACCENT_4..6` = `#B7BDCC` / `#CFD3DD` / `#E7E9EE` (gris-bleus clairs — fonds de bandeaux neutres)
- Hors thème mais récurrents : `#DC5B00`/`#DC5C00` (orange — mise en exergue textuelle),
  `#FF0000`/`#FF4338` (rouge — argent/alerte), `#E1FDFA`/`#D9ECFF` (cyan/bleu très pâles —
  tuiles de fond), `#123079` (navy des pastilles %).

---

## 1. Trombinoscope d'équipe (photos rondes + étiquette rôle)

- **Situation/intention** : présenter le dispositif humain en ouverture (« qui va porter la mission »).
- **Type** : cartes-personnes en cercle/scatter, chaque personne = photo + tag nom/rôle.
- **Composition** (p.2) : 5 photos rondes 1.51×1.51in en deux rangées décalées ; anneau
  blanc (OVAL, contour navy) légèrement plus grand que la photo ; étiquette
  `ROUND_2_DIAG_RECTANGLE` blanche à contour navy (2.0×0.67in) portant Nom (12pt gras
  navy) + Rôle (10pt navy), en léger décalage diagonal (asymétrie volontaire). Forme
  `TEAR` décorative navy en écrin de la phrase-signature.
- **Efficace parce que** : le décalage diagonal casse la rigidité du trombinoscope
  classique ; la répétition du même gabarit crée l'unité.

## 2. Mur de verbatims (bulles de citation scatter sur photo pleine page)

- **Situation/intention** : restituer des constats marquants d'interviews sans les
  hiérarchiser — effet « brut de collecte ».
- **Type** : quote wall en scatter, non aligné en grille.
- **Composition** (p.4, fond image plein cadre 9.64×5.43in) : 5 bulles
  `ROUND_2_DIAG_RECTANGLE` blanches contour navy, tailles/positions hétérogènes
  (3.27×0.57 à 4.27×0.61in), posées librement sur la photo. Icône « guillemet » custom
  accrochée au coin supérieur-gauche de chaque bulle, en léger débord. Texte 12pt mix
  gras/normal : citation + attribution entre parenthèses. Point cyan minuscule
  (0.07in) en puce de départ. Guillemet fermant décoratif en bas à droite.
- **Efficace parce que** : le désordre contrôlé mime le caractère brut de la parole
  recueillie tout en gardant un gabarit de bulle unique ; le fond photo ancre dans le réel.

## 3. Grille de bénéfices chiffrés (cartes stat 2×3, une mise en avant)

- **Situation/intention** : chiffrer la proposition de valeur de façon scannable.
- **Type** : grille de cartes KPI (2×3).
- **Composition** (p.6) : 6 `ROUNDED_RECTANGLE` égales (2.6×1.74in), grille stricte ;
  chiffre-choc en tête (11pt gras navy) + phrase d'explication (11pt). 5 cartes
  blanches contour navy ; UNE carte en fill cyan plein et UNE en fill navy plein
  (texte blanc + visuel) — les deux seules ruptures marquent les items stratégiques.
- **Efficace parce que** : gabarit uniforme = chiffres comparables d'un coup d'œil ;
  les 2 ruptures colorées créent des ancres sans casser la grille.

## 4. Schéma des N freins en flux numéroté (3+2, badges + connecteur)

- **Situation/intention** : diagnostic structuré (« modèle à N facteurs »).
- **Type** : items numérotés en flux, 2 rangées en quinconce (3 haut / 2 bas),
  chaque item = badge + titre + puces.
- **Composition** (p.7) : badge numéroté `TEAR` cyan 0.44in (chiffre 14pt) →
  connecteur `LINE` vertical navy → titre gras 10.6pt → 2-3 puces 10.2pt. Rangée
  haute à x≈0.9/3.8/6.7 ; rangée basse décalée (quinconce). AUCUN cadre autour des
  unités — seul le connecteur relie badge et texte.
- **Efficace parce que** : sans cartes, la densité reste légère pour 5 items
  détaillés ; le quinconce évite l'effet tableau.

## 5. Diagramme « sandwich » organisationnel (bandeaux haut/bas + piliers centraux)

- **Situation/intention** : modèle cible d'organisation, gouvernance qui encadre.
- **Type** : 2 bandeaux pleine largeur encadrant 3 piliers centraux avec icônes.
- **Composition** (p.8) : conteneur `RECTANGLE` fin contour (8.45×3.84in) ; bandeaux
  haut/bas `ROUNDED_RECTANGLE` fill `ACCENT_6` (titre gras + 2 sous-lignes centrés) ;
  entre les deux, 3 blocs de texte centrés surmontés d'une petite photo/icône.
  Symétrie verticale stricte.
- **Efficace parce que** : la métaphore du sandwich rend la hiérarchie conceptuelle
  (encadrant vs encadré) lisible sans texte explicatif.

## 6. Flux vertical en pilules connectées + encart de mise en exergue

- **Situation/intention** : dérouler une approche séquentielle + délivrer le message clé.
- **Type** : chaîne verticale de pilules reliées par une ligne, encart-message latéral.
- **Composition** (p.9) : 5 pilules `ROUNDED_RECTANGLE` (3.11×0.64in) empilées,
  reliées par UNE `LINE` verticale continue (pas de flèches) ; titre gras 10pt + 1-2
  sous-lignes. Une pilule en fill cyan, les autres blanches contour gris (« un sur N »).
  À droite, bloc `RECTANGLE` fill `ACCENT_6` (4.39×2.45in) avec le message de synthèse
  (12pt, dernière ligne grasse), jonction par connecteur `PENTAGON`.
- **Efficace parce que** : séparer « le processus » (colonne dense) du « message »
  (bloc aéré) évite que le point clé se noie.

## 7. Rangée de cartes de décision (une mise en avant, bandeau fond)

- **Situation/intention** : lister des décisions/choix d'un comité, signaler la priorité.
- **Type** : 4 cartes égales sur bandeau de fond, une carte accentuée.
- **Composition** (p.10) : bandeau `RECTANGLE` fill `ACCENT_6` pleine largeur
  (8.77×3.05in) ; 4 cartes `ROUND_2_DIAG_RECTANGLE` égales (2.09×2.01in) à intervalle
  régulier ; carte n°1 fill cyan, les 3 autres blanches. Titre numéroté gras centré
  (10pt) + détail + phrase-clé grasse. Titre de slide overridé 18.2pt (titre-choc).
- **Efficace parce que** : le bandeau colorié regroupe les cartes en un « bloc de
  décision » unique, distinct du reste de la page.

## 8. Cartographie de blocs par bandes fonctionnelles (blueprint en 3 couches)

- **Situation/intention** : vision cible d'architecture produit/plateforme.
- **Type** : blueprint en 3 bandes horizontales superposées listant des blocs.
- **Composition** (p.11) : 3 bandes pleine largeur `RECTANGLE` sans contour
  (0.86-1.4in de haut), teintées `ACCENT_6`/`ACCENT_4`/`ACCENT_6` ; tuiles
  `ROUNDED_RECTANGLE` (1.59-2.7in) titre gras 13pt + sous-titre 10pt ; tuiles
  « Business » teintées `#E1FDFA` (cyan pâle), les autres blanches. Étiquette de
  bande verticale 8pt gras. Badge diagonal cyan « ILLUSTRATION » en coin.
- **Efficace parce que** : les bandes teintées font saisir les 3 couches sans lire
  les étiquettes ; la teinte pâle distingue subtilement une famille de tuiles.

## 9. Diagramme organisationnel en bandes trapézoïdales + tags reliés par flèches

- **Situation/intention** : mécanisme d'organisation reliant plusieurs strates par
  des flux bidirectionnels.
- **Type** : flowchart libre — bandes trapèze, pilules-rôles, flèches verticales.
- **Composition** (p.12, p.14) : bandes `FLOWCHART_MANUAL_OPERATION` (~6.8×0.6in)
  comme bandeaux d'étiquette ; pilules de rôle reliées par `DOWN_ARROW`/
  `UP_DOWN_ARROW` cyan ; nœuds centraux en cartes avec photos ; `BENT_ARROW` pour
  les liaisons en coude ; bandeau navy pleine largeur comme pivot horizontal.
- **Efficace parce que** : vocabulaire de flèches uniforme (toutes cyan) = « flux »
  compris sans légende après 1-2 occurrences.

## 10. Roadmap en étapes numérotées avec fiches détaillées (activités + livrables)

- **Situation/intention** : détailler une démarche en N grandes étapes avec contenu.
- **Type** : blocs-étapes pleine hauteur, chip numéroté au coin, contenu riche.
- **Composition** (p.16, layout « Titre seul ») : 3 cadres `ROUNDED_RECTANGLE` SANS
  fill (contour navy seul), tailles selon contenu (~4×2.6in) ; chip numéroté-titre
  `ROUND_2_SAME_RECTANGLE` cyan CHEVAUCHANT le bord supérieur du cadre (9pt gras) ;
  dedans : puces d'activité 9pt, rupture « LIVRABLES » (9pt gras) + ses puces, dont
  une ligne surlignée orange `#DC5C00` pour l'élément stratégique. Médaillon photo
  0.41in sur chaque chip.
- **Efficace parce que** : activités ET livrables dans une même carte = une étape se
  lit comme un tout ; le surlignage orange guide l'œil sans étiquette.

## 11. Trajectoire à 4 phases en colonnes (roadmap simplifiée)

- **Situation/intention** : vue macro de la trajectoire dans le temps, par phase.
- **Type** : roadmap en colonnes de phase × lignes de catégorie.
- **Composition** (p.17) : 4 pilules d'en-tête cyan de largeur croissante (noms de
  phase) ; séparateurs verticaux fins `LINE` `ACCENT_6` ; à gauche, étiquettes de
  catégorie MAJUSCULES grasses 8pt (ACTIVITÉS / LIVRABLES / ÉVÈNEMENTS) soulignées
  d'un trait traversant ; cellules = blocs 8pt (1ère ligne grasse) ; losanges
  `DIAMOND` cyan en jalons.
- **Efficace parce que** : la richesse d'un tableau sans son aspect rigide (juste
  séparateurs fins + étiquettes).

## 12. Gantt simplifié avec pastilles de jalon et frise de points

- **Situation/intention** : déroulé semaine par semaine d'un pilote.
- **Type** : mini-Gantt schématique — bandes de tâche + jalons + frise de points.
- **Composition** (p.18) : swimlanes par flux (squelette = tableau pptx invisible,
  seuls les en-têtes de mois visibles) ; pilules de tâche à largeur=durée, teintées
  par nature (cyan = active, navy = standard, `ACCENT_6` = optionnelle) ; jalons =
  paires de `ISOSCELES_TRIANGLE` (drapeau) ou `FLOWCHART_DECISION` (losange) ; en
  bas, frise de cercles 0.13in (semaines), certains cyan (comités). Texte 6pt.
- **Efficace parce que** : la frise de points donne la cadence fine sans échelle de
  dates complète ; les couleurs différencient les tâches sans légende lourde.

## 13. Fiche de cadrage « Objectifs / Prérequis / Livrables »

- **Situation/intention** : cadrer une phase de mission en rubriques standard.
- **Type** : fiche à rubriques fixes — icône ronde + étiquette + carte de contenu.
- **Composition** (p.21, p.35 — 2 variantes) : chaque rubrique = (a) icône dans
  cercle blanc contour navy 0.7in, (b) étiquette `ROUND_2_SAME_RECTANGLE` ou texte
  gras 11pt, (c) carte `ROUND_1_RECTANGLE` blanche contour navy, puces 9-10pt (item
  stratégique gras+orange). Variante A : 2 rubriques côte à côte + 1 pleine largeur
  en bas. Variante B : 2 empilées à gauche + 1 grande carte pleine hauteur à droite.
- **Efficace parce que** : la triade fixe répétée à chaque instance crée un gabarit
  reconnaissable même quand la longueur de contenu varie.

## 14. Processus en 4 étapes numérotées, colonnes de détail dans UNE carte

- **Situation/intention** : déroulé fin d'une phase en sous-étapes, avec fil rouge
  transversal (ici l'apport IA).
- **Type** : frise de chips numérotés au-dessus d'une carte unique à colonnes.
- **Composition** (p.22) : 4 chips numérotés (`TEAR` alterné avec pilules) ; en
  dessous UN `RECTANGLE` blanc (8.23×3.87in) divisé en 4 colonnes par de fins
  séparateurs `LINE` navy — pas 4 cartes ; bandeau vertical navy plein à gauche
  (étiquette de ligne) ; dans chaque colonne, puces puis « L'IA pour [verbe] : » en
  gras + détail — motif répété dans les 4 colonnes.
- **Efficace parce que** : une seule carte à séparateurs évite la répétition de
  bordures ; le sous-titre répété crée un fil rouge repérable en diagonale.

## 15. Grille de maturité / référentiel d'évaluation (framework)

- **Situation/intention** : présenter la grille méthodologique d'évaluation.
- **Type** : grille référentiel — lignes = capacités, colonnes = niveaux 0-3,
  libellé de niveau PROPRE à chaque ligne.
- **Composition** (p.23, layout « Focus ») : colonne gauche = chips de capacité bleu
  pâle `#D9ECFF` (9pt gras) ; en-têtes d'axe (« Quoi ? » / « Quels niveaux ? ») ;
  numéros 0-3 (11pt gras) répétés par ligne avec libellé qualitatif spécifique par
  capacité. Badge diagonal cyan « Illustratif ».
- **Efficace parce que** : personnaliser le libellé de niveau par ligne rend le
  référentiel actionnable ; la densité élevée est assumée par le badge Illustratif.

## 16. Cycle de vie en 3 phases sous bandeau chevron

- **Situation/intention** : phases d'un cycle de vie continu.
- **Type** : bandeau-chevron chapeautant 3 colonnes de phase.
- **Composition** (p.28) : `ISOSCELES_TRIANGLE` aplati (8.52×0.44in, `ACCENT_2`) en
  ruban horizontal portant le label général ; 3 cartes de phase (2.28×1.02in) ;
  sous-titres de transition reliés par flèches `PENTAGON` ; 3 grandes cartes de
  description (2.39×2.29in), phrase clé grasse.
- **Efficace parce que** : le chevron exprime « un même mouvement continu » avant
  toute lecture — cycle, pas silos.

## 17. Matrice de positionnement/maturité avec vecteurs de trajectoire

- **Situation/intention** : où en sont les équipes sur 2 axes + leur dynamique.
- **Type** : matrice 2 axes avec badges positionnés + connecteurs passé/futur.
- **Composition** (p.31) : axes gradués 1-5 avec 3 zones qualitatives (Insuffisant /
  Opérationnel / Avancé) ; quadrillage fin gris ; badges d'équipe 0.5in colorés par
  statut (vert `#BBFE81` = en cours, gris = non commencé) ; connecteurs `LINE`
  diagonaux : gris = évolution passée, bleu clair `#8DD3FF` = à venir ; légende
  dédiée à droite (7pt). Badge « Illustratif ».
- **Efficace parce que** : coder la temporalité par la COULEUR du connecteur évite la
  confusion de trajectoires qui se croisent ; on lit position ET dynamique.

## 18. Catalogue d'offres en colonnes thématiques + bandeau transverse

- **Situation/intention** : offre organisée par niveau/parcours + thèmes transverses.
- **Type** : colonnes à en-tête plein + bandeaux pleine largeur en pied.
- **Composition** (p.38) : 3 colonnes = fond `RECTANGLE` gris clair (2.95×2.75in)
  chapeauté d'un en-tête `ROUNDED_RECTANGLE` navy plein (texte blanc gras 10pt) ;
  pilules blanches empilées contour navy (9pt gras) ; en pied, bandeaux blancs
  pleine largeur (9.02in) pour les thèmes transverses.
- **Efficace parce que** : distinguer colonnes (progressif) et bandeaux traversants
  (transverse) évite la duplication et dit implicitement « s'applique à tous ».

## 19. Bibliothèque d'outils par phase (toolkit map à onglets imbriqués)

- **Situation/intention** : vue exhaustive d'une bibliothèque d'outils par phase.
- **Type** : 4 bandes de phase en dégradé de teinte, groupes à onglet empilés.
- **Composition** (p.39, 51 formes) : 4 bandes verticales pleine hauteur
  (1.9×4.31in) en dégradé cyan `#00AFCB`→`#80D7E5`→`#B6E8F0`→`#DDF4F8` (progression
  de phase) ; dans chaque bande, groupes « onglet de dossier » (languette
  `RECTANGLE`+`ROUNDED_RECTANGLE`) surmontant une pile de pilules blanches (7.25pt) ;
  la couleur de languette suit une échelle navy→gris INDÉPENDANTE (sous-catégorie).
- **Efficace parce que** : deux échelles de couleur superposées mais non
  concurrentes (phase macro en fond, sous-catégorie en onglet) permettent de
  naviguer à deux niveaux dans une masse autrement illisible.

## 20. Carte dispositif équipe avec badges de % de staffing

- **Situation/intention** : équipe-cœur de la mission et taux d'engagement.
- **Type** : mini-organigramme rôles — photo + étiquette + badge pourcentage.
- **Composition** (p.41) : panneau gauche sombre (`ROUND_2_DIAG_RECTANGLE` fill
  navy, texte blanc) pour l'approche ; panneau droit clair (chip cyan en
  chevauchement du bord) pour le dispositif : par personne, photo 0.68in, étiquette
  de rôle navy pleine (blanc gras 9-10pt), pastille `OVAL` `#123079` 0.4in avec le
  % en blanc gras 8pt, en chevauchement du bord de la photo (badge de notification).
  Nœud « Communauté » texte seul pour les experts ponctuels.
- **Efficace parce que** : le % incrusté sur la photo se compare d'un coup d'œil et
  distingue l'équipe-cœur staffée des experts à la demande.

## 21. Fiche financière façon « ticket/coupon »

- **Situation/intention** : prix et conditions de paiement, sérieux mais mémorable.
- **Type** : silhouette de ticket (bords à cran) contenant prix + conditions.
- **Composition** (p.43) : ticket par superposition `ROUND_1_RECTANGLE` +
  `ROUND_2_SAME_RECTANGLE` (contour navy, sans fill) avec séparation verticale
  « perforée » ; gauche : accroche (15pt) + carte « Conditions de facturation »
  (échéancier, phrase finale grasse) ; droite : montant en évidence (11-13pt gras),
  callout rouge (8pt gras `#FF0000`) et validité, avec petites icônes en regard.
- **Efficace parce que** : la métaphore du ticket rend une slide de prix aride plus
  engageante en gardant la structure montant/conditions/validité attendue.

## 22. Médaillon décoratif (rosette d'arcs) pour avantage/bonus

- **Situation/intention** : mettre en scène un avantage commercial comme un objet.
- **Type** : icône-médaillon (rosette d'`ARC` superposés + rayons) + blocs de texte.
- **Composition** (p.44, « Titre seul ») : 3 `ARC` navy superposés (2.84in) + `LINE`
  rayonnantes vers des traits courts périphériques (sceau/médaille) ; grand titre
  27pt gras navy ; carte-cadre fine sans fill structurant la moitié gauche ; 2
  blocs de texte (titre gras + 2-3 lignes 9pt).
- **Efficace parce que** : traiter l'avantage comme une slide-produit avec son
  iconographie renforce la valeur perçue.

---

## Principes transversaux (constantes du deck)

- **Grille de marge constante** : contenu entre x≈0.6-0.65in et x≈9.4in (marges
  symétriques ~0.6in sur 10×5.625in) ; titre à y≈0.4in, sous-titre/claim à
  y≈0.8-0.85in. Largeur de contenu standard : 8.77in.
- **Titre = sujet, sous-titre = message (claim)** : sur les slides narratives, le
  titre nomme le sujet (court) et le SOUS-TITRE porte l'idée-force en phrase
  complète (pyramide de Minto). Sur les slides de référence denses (« Focus »), le
  claim disparaît au profit d'un label — la densité prime.
- **Choix de layout corrélé au contenu** : « Titre et sous-titre » → narration ;
  « Focus » → référence dense (matrices, grilles, bibliothèques) sans grand claim ;
  « Titre seul » → schémas pleine page (roadmaps, fiches, médaillon) ; « Titre,
  sous-titre et contenu » → zone structurée additionnelle (Gantt, cycle de vie).
- **Forme-signature de carte** : `ROUND_2_DIAG_RECTANGLE` (coins arrondis + un coin
  coupé) = LA silhouette de carte OCTO pour contenu riche (verbatims, fiches,
  bénéfices, décisions, ticket). `ROUNDED_RECTANGLE` = éléments simples : pilules
  d'étape, étiquettes, chips, cartes de grille répétitive.
- **Règle « un sur N en accent »** : dans presque toute série d'éléments égaux
  (5 freins, 6 bénéfices, 5 étapes, 4 décisions), UN SEUL reçoit un fill plein cyan
  ou navy, les autres restent blancs/gris à contour — le mécanisme de hiérarchie le
  plus systématique du deck, plus utilisé que la taille de police.
- **Couleur = sémantique, pas décoration** : navy = encre/contours ; cyan =
  accent/CTA/connecteur/« Illustratif » ; orange = surlignage d'un mot/ligne à fort
  enjeu (jamais une carte entière) ; rouge = argent/alerte factuelle uniquement ;
  gris-bleu clair = fond de support, jamais porteur de sens. Deux échelles de
  couleur peuvent coexister si elles portent des dimensions différentes (p.39).
- **Micro-pattern du bullet** : « **accroche grasse** + complément régulier » sur la
  même puce ; sous-en-têtes internes en MAJUSCULES grasses 8-9pt comme rupture
  visuelle sans bordure.
- **Échelle typographique** : titres 18-25pt (jusqu'à 27pt sur slide très aérée) ;
  sous-titre-claim 11-13pt ; corps de carte 9-12pt ; légendes/axes/timeline 6-8pt.
  Plus la slide est dense, plus le corps descend — la densité est absorbée par la
  police, JAMAIS par la réduction des marges externes.
- **Icône photo systématique** : toute figure humaine = photo réelle détourée en
  médaillon rond ou carte à coin arrondi (jamais un pictogramme), 0.3-0.9in.
- **Badge « Illustratif »** : ruban diagonal cyan en coin sur toute slide à
  contenu-exemple/gabarit (distinct du repère de marque rond ~0.37in en haut à
  droite, constant sur les slides de contenu).
- **Densité par le layout, pas par les marges** : même les slides à 50-90 formes
  gardent les marges externes des slides épurées ; la densité est absorbée en
  interne (police plus petite, formes plus fines, moins de blanc intra-carte).
