# Catalogue de patterns — deck de proposition commerciale (transfo mode produit)

Source : `OCTO_GROUND - Proposition accompagnement à la transfo mode produit à
l'échelle.pptx.pdf`, deck de proposition commerciale OCTO réel, 27 pages, gabarit
navy/cyan 10×5.625in — même socle de thème que `catalogue-restitution.md`
(`template-octo.md`). Extraction par rendu PyMuPDF (zoom ×2.5) et lecture visuelle
page par page (le fichier source est un export PDF, pas un .pptx éditable — pas
d'inventaire géométrique programmatique possible ici). 27/27 pages vues, 2026-09-04.

Trois patterns supplémentaires (10-12) proviennent d'un système de composants dérivé
de CE MÊME deck par une exploration graphique de VSCode3 (`check_slide_synthese`
v3-refonte, non commitée au moment de l'extraction) — portés ici en lecture seule,
aucune source VSCode3 modifiée.

## Palette — écarts au navy/cyan déjà résolu (`template-octo.md`)

Navy `#0E2356` et cyan accent `#00D2DD` confirmés comme fond de gamme. Écarts observés
(estimations visuelles, non mesurées au pipette sauf mention contraire) :

- Turquoise plus saturé / vert-cyan sur blocs décoratifs et la chaîne d'anneaux (p.6).
- Gris-bleu ardoise (~`#6B7A99`) pour le bandeau total des tableaux budgétaires.
- Traitement photo duotone navy + bleu pâle (mosaïque d'experts, p.15).
- 2 planches hors marque OCTO (contenu partenaire inséré tel quel, non retenues comme
  patterns catalogue) : p.20 vert vif (« School of Product »), p.25-26 noir + rouge/rose
  (« ground »).

## Index situation → pattern

| Situation | Pattern (n°) |
| --- | --- |
| Mettre en avant une parole forte d'un sponsor unique | Citation vedette à portrait en arche (1) |
| Présenter 3 enjeux/piliers qui s'articulent entre eux | Grille mosaïque interlocking à icônes sur jonctions (2) |
| Illustrer un objectif abstrait par une métaphore | Icônes-métaphores en cadre arche à coin coupé (3) |
| Représenter une méthodologie en cycle continu | Chaîne d'anneaux entrelacés (4) |
| Occuper un espace visuel dédié par une scène narrative | Grande illustration ligne-art pleine page (5) |
| Cartographier qui travaille avec qui par palier de gouvernance | Organigramme 3 étages à pilules de rôle (6) |
| Dérouler une feuille de route longue et dense | Timeline serpentine à jalons 2 lignes (7) |
| Présenter une équipe de façon éditoriale (pas trombinoscope) | Mosaïque de portraits duotone interlocking (8) |
| Chiffrer un budget en distinguant 2 enveloppes | Tableau budgétaire en-têtes bicolores + bandeau total (9) |
| Signaler un changement de partie/section avec force | Titre à cheval sur bord d'image, couleur adaptative (10) |
| Présenter en détail un intervenant nommé (CV narratif) | Fiche bio à photo encastrée dans coin de pavé texte (11) |
| Montrer concrètement ce qui change (transformation) | Comparaison avant/après 2 colonnes + grande flèche (12) |
| Dérouler une série constat→réponse en restant scannable | Chaîne de paires reliées par chevron (13) |
| Montrer une démarche à double fil (ex. orga + technique) | Roadmap à double piste en miroir (14) |

## Composants transversaux (système `check_slide_synthese` v3, dérivé de ce deck)

Vocabulaire de composants réutilisables extrait par étude page-par-page de ce deck
(rendus PNG, pas seulement XML) — à appliquer ENSEMBLE, pas isolément, sur un template
navy/cyan de type proposition commerciale :

1. **Conteneur = contour, jamais aplat.** Tout conteneur MULTI-LIGNES (carte, bloc de
   texte) est un rectangle à coins arrondis en CONTOUR SEUL (fill blanc, trait de
   couleur 1.1-1.6pt) — jamais un fill plein ni un liseré gauche seul. Le fill plein
   est réservé aux étiquettes courtes (2) et au bandeau de clôture (7).
2. **Étiquette courte = pilule pleine.** Toute étiquette 1 ligne (métadonnée, tag,
   durée, numéro) est une pilule (radius=0.5) en fill couleur unie, texte blanc ou
   navy selon contraste.
3. **Chevron = marqueur de séquence/flux.** `MSO_SHAPE.CHEVRON` (seul preset natif
   python-pptx rendant la silhouette encoche-gauche/pointe-droite SANS rotation — une
   rotation fausserait `verifier_geometrie`, qui mesure le cadre non pivoté) comme
   étiquette de titre d'étape ou petite flèche de connexion.
4. **Badge à cheval sur un bord.** Pastille ronde (pleine = acté, contour pointillé =
   optionnel) centrée SUR le bord d'une forme voisine plutôt que posée à côté reliée
   par une flèche. `verifier_geometrie` ne détecte QUE les sorties de slide, jamais les
   collisions entre 2 formes — vérifier au rendu réel.
5. **Emphase en ligne.** 1-2 mots-clés gras/colorés À L'INTÉRIEUR d'une phrase de poids
   normal, jamais une phrase entière en gras/italique monochrome (nécessite une zone de
   texte multi-runs par paragraphe).
6. **Barre verticale d'accent.** Trait fin coloré (~0.045in, coins arrondis) à gauche
   d'un chapo ou d'un libellé important, en remplacement d'un italique nu.
7. **Bandeau de clôture signature.** Fond plein (navy ou couleur du thème) + guillemet
   décoratif surdimensionné en coin (24pt, couleur accent) + point isolé après le
   dernier mot de la phrase-clé — réservé à 1-3 phrases de synthèse en fin de slide.

---

## 1. Citation vedette à portrait unique en arche

- **Situation/intention** : mettre en avant UNE seule parole forte d'un sponsor/décideur
  client, en ouverture de deck — différent d'un mur de verbatims (`catalogue-restitution.md`
  #2) qui juxtapose plusieurs contributions égales.
- **Type** : slide éditoriale portrait + pull-quote, un seul intervenant occupe toute la
  hauteur.
- **Composition** (p.2) : photo ~3.5×3.9in en rectangle à coins arrondis très
  asymétrique — rayon large en haut (quasi demi-cercle, ~1in) formant une arche, rayon
  réduit en bas (~0.15in), façon « tombstone » inversée. Une pilule pleine navy
  (`#0E2356`), ~0.4×0.8in, dépasse derrière le coin supérieur-gauche de la photo comme
  accroche décorative. À droite : citation en gras navy sur 5 lignes (~32-36pt),
  guillemets typographiques ; nom + fonction en dessous (~20pt) ; second niveau de
  citation plus petit et italique (~14pt), espace généreux.
- **Efficace parce que** : la forme en arche du portrait guide l'œil vers le haut du
  bloc de texte ; la hiérarchie à deux niveaux de citation évite le mur de texte tout
  en gardant de la substance.

## 2. Grille en mosaïque interlocking avec icônes à cheval sur les jonctions

- **Situation/intention** : présenter 3 enjeux/piliers complémentaires sans les traiter
  comme 3 colonnes cloisonnées — suggérer qu'ils s'articulent entre eux.
- **Type** : grille puzzle asymétrique (3 panneaux qui s'emboîtent, un plus grand).
- **Composition** (p.4) : panneau bas-gauche `ROUNDED_RECTANGLE` contour seul
  ~4.3×2.4in ; panneau haut-droite ~4.3×1.8in ; panneau bas-droite ~4.3×2.0in — les
  trois bords se touchent pile pour former une croix asymétrique. Icônes (~0.8×0.8in,
  style duotone outline navy+cyan) positionnées À CHEVAL sur le point de jonction entre
  deux panneaux, pas dans un panneau ni à côté.
- **Efficace parce que** : l'icône posée sur la jonction rend visuellement l'idée que
  les 3 leviers ne sont pas indépendants — une grille 3 colonnes classique ne peut pas
  suggérer ça.

## 3. Icônes-métaphores en cadre arche à coin coupé, 3 colonnes

- **Situation/intention** : illustrer des objectifs abstraits par une métaphore visuelle
  concrète plutôt qu'un pictogramme littéral.
- **Type** : 3 colonnes d'icônes métaphoriques dans un cadre à forme distinctive.
- **Composition** (p.5) : cadre contour seul ~3.6×3.3in en forme d'arche (sommet plein
  cintre) avec UN SEUL coin coupé en diagonale en bas-à-droite — silhouette d'écusson
  tronqué. Icône duotone navy+cyan ~2.2×2.2in centrée en moitié haute. Titre gras navy
  ~20pt sous le cadre, description centrée ~14pt sur 2-3 lignes.
- **Efficace parce que** : la métaphore est plus mémorisable qu'un intitulé abstrait, et
  la forme de cadre non générique signale visuellement « ceci est un symbole ».

## 4. Chaîne d'anneaux entrelacés pour un cycle continu

- **Situation/intention** : représenter une méthodologie comme un cycle continu et
  itératif, pas une séquence linéaire à sens unique.
- **Type** : diagramme de flux circulaire, famille « chaîne de maillons » — absente du
  catalogue de base (`catalogue-restitution.md` n'a qu'un flowchart bandes linéaire #9
  et un mini-Gantt #12).
- **Composition** (p.6) : 5 grands cercles contour seul (~3.3in Ø), se chevauchant
  d'environ 40% avec leur voisin, alignés horizontalement ; dans l'espace de
  chevauchement, un arc épais cyan/turquoise (~0.5in) relie chaque paire et se termine
  en pointe de flèche à l'intérieur du cercle suivant — effet chaîne de maillons.
  Icône duotone centrée dans chaque cercle (~1.3×1.3in). Sous chaque cercle, un
  rectangle contour (~1.6×1.1in) avec titre gras centré + légende 2-3 lignes.
- **Efficace parce que** : la boucle visuelle communique « processus itératif sans fin »
  bien plus directement qu'une liste à puces ou une frise linéaire.

## 5. Grande illustration métaphorique en ligne-art pleine page

- **Situation/intention** : occuper un espace visuel dédié (souvent la moitié ou un
  tiers de la slide) avec une scène narrative plutôt qu'une icône, pour incarner un
  propos sans texte additionnel.
- **Type** : illustration éditoriale composite en trait fin navy (touches cyan), entre
  pictogramme et bande dessinée.
- **Composition** : deux variantes. *Grille de vignettes* (p.7) : cadre en arche/ovale
  (~4×7.5in) subdivisé en 6 cellules inégales, chacune une mini-scène (métaphore filée
  de chaîne de production). *Scène unique en capsule* (p.14) : cadre pilule pleine
  hauteur (~3.3×7.5in, coins totalement arrondis) contenant une seule scène continue
  (métaphore du chemin/de l'ascension).
- **Efficace parce que** : donne une identité visuelle « faite main » (vs icônes stock
  génériques) ; la lecture narrative invite à s'arrêter plutôt qu'à scanner — utile pour
  les slides à faible densité textuelle.

## 6. Organigramme de gouvernance à 3 étages, correspondance de rôles en pilules

- **Situation/intention** : cartographier QUI (client) travaille avec QUI (OCTO) à
  chaque niveau de gouvernance — différent de `catalogue-restitution.md` #20 (dispositif
  + badges % staffing) qui chiffre un staffing, pas une correspondance binomiale.
- **Type** : matrice de correspondance rôle-à-rôle organisée par paliers hiérarchiques.
- **Composition** (p.9 et p.12, deux traitements) : 3 conteneurs contour empilés
  verticalement (~14×2.2in), un par palier (Stratégie/Tactique/Opérationnel). Avatar
  mascotte (~1×1.3in) posé à gauche, hors conteneur, avec label. À l'intérieur : rangées
  de pilules BICOLORES JOINTES — moitié gauche navy plein, moitié droite cyan plein,
  bord vertical commun sans espace (~9×0.5in). Variante p.12 : pilule label navy pleine
  à gauche (largeur fixe) + pilule contour plus longue à droite (mapping rôle→livrable).
- **Efficace parce que** : la pilule bicolore jointe rend visible « qui est en binôme
  avec qui » sans flèche de connexion ; la répétition du gabarit à 3 paliers sur 2
  slides consécutives crée une cohérence de lecture.

## 7. Timeline serpentine à jalons sur deux lignes

- **Situation/intention** : dérouler une feuille de route longue (plusieurs trimestres,
  une vingtaine de jalons) sur une seule slide sans l'écraser en frise illisible.
- **Type** : timeline en serpentin (boustrophédon) — absente du catalogue de base (le
  plus proche, `catalogue-restitution.md` #12 mini-Gantt, est une frise à une seule
  ligne).
- **Composition** (p.11) : ligne du temps de gauche à droite sur une première rangée,
  tourne dans le coin haut-droit via un arc, repart de droite à gauche sur une seconde
  rangée — effet « S ». Phases trimestrielles en pilules contour (~3.5×0.6in) insérées
  sur le tracé, alternant avec cercles blancs contour (jalons intermédiaires, ~0.35in)
  et cercles pleins cyan (jalons-clés). Chaque jalon relié par une ligne pointillée
  verticale courte à une étiquette texte. Bandeau contour séparé en bas résumant
  Objectifs/Livrables en légende globale.
- **Efficace parce que** : le tracé serpentin permet de caser beaucoup de jalons sur une
  seule slide 16:9 sans réduire le texte à une taille illisible.

## 8. Mosaïque de portraits duotone en puzzle interlocking

- **Situation/intention** : présenter une équipe d'experts sans recourir au
  trombinoscope en cercles alignés (`catalogue-restitution.md` #1) — rendu plus
  éditorial/agence.
- **Type** : mosaïque photo en puzzle asymétrique, traitement monochrome.
- **Composition** (p.15) : 5 portraits en duotone navy/bleu pâle, 2 rangées (3 haut,
  2 bas), coins arrondis à rayons variables et complémentaires — chaque photo
  « s'emboîte » avec sa voisine sans espace blanc, façon puzzle (~6×5.2in). Légende
  centrée sous la mosaïque. Second bloc à droite : signe « + » navy géant + cluster
  pyramidal d'avatars mascotte.
- **Efficace parce que** : le duotone unifie des photos hétérogènes en une identité de
  marque ; l'emboîtement sans espace donne une impression de collectif soudé plutôt
  qu'une liste de CV.

## 9. Tableau budgétaire à en-têtes bicolores et bandeau total

- **Situation/intention** : présenter un chiffrage détaillé en distinguant deux
  enveloppes budgétaires différentes avant de les additionner.
- **Type** : tableau natif structuré — absent du catalogue de base (le plus proche,
  `catalogue-restitution.md` #18, est une présentation en colonnes de cartes, pas un
  tableau de lignes chiffrées).
- **Composition** (p.16, répété p.17-19) : deux sous-tableaux empilés à 4 colonnes
  (intitulé/prix unitaire/quantité/prix total) — en-têtes pleins turquoise-cyan puis
  navy, texte blanc gras, codant la couleur par partenaire. Lignes de corps sur fond
  gris très clair. Bandeau plein largeur en gris-bleu ardoise sous les deux
  sous-tableaux (total HT/TTC), texte blanc gras centré, sans les colonnes
  intermédiaires.
- **Efficace parce que** : le code couleur par partenaire distingue les deux enveloppes
  sans relire les libellés ; le bandeau total en une troisième couleur neutre signale
  sans ambiguïté « ceci est la synthèse ».

## 10. Titre de section à cheval sur le bord d'une image, couleur adaptative

- **Situation/intention** : signaler un changement de partie avec un effet typographique
  fort qui ancre le titre dans le visuel plutôt que de le poser à côté.
- **Type** : traitement typographique distinctif — différent de l'emphase en ligne
  (composant transversal 5 ci-dessus, qui touche des mots-clés dans une phrase) : ici
  c'est le TITRE ENTIER, en très grand corps, dont la couleur bascule au pixel près
  selon qu'il chevauche l'image de fond ou le blanc.
- **Composition** (p.21) : bloc image occupant l'angle supérieur-gauche (~4×5in, coin
  supérieur-droit à grand rayon ~1in). Mot-titre composé en une seule police géante
  (~60pt gras) positionné pour chevaucher exactement la frontière verticale du bloc
  image : les lettres sur le fond image sont blanches, celles sur le fond blanc sont
  navy. Point cyan final. Second titre plus classique en dessous, aligné à droite du
  bloc image.
- **Efficace parce que** : l'effet ne fonctionne que positionné au pixel près sur la
  frontière — signal fort et mémorable de « nouvelle section » qui ne coûte qu'un
  changement de couleur de police.

## 11. Fiche bio individuelle, photo encastrée dans le coin d'un pavé de texte

- **Situation/intention** : présenter en détail UN intervenant nommé (CV narratif) avec
  citation d'ouverture, biographie longue et références — différent du trombinoscope
  (grille) et de la citation vedette (#1 ci-dessus, centrée sur UNE phrase, pas un
  parcours).
- **Type** : fiche bio / CV narratif en deux zones imbriquées.
- **Composition** (p.22 variante rectangulaire, p.23-24 variante circulaire) : citation
  italique navy en haut (~18pt). Photo (rectangle coin sup-droit arrondi ~3.2×3.5in, ou
  cercle ~2.8in Ø) positionnée pour occuper exactement le coin supérieur-gauche d'un
  grand conteneur contour (~9.5×4.8in) — la photo « mord » sur le bord, dont le tracé se
  creuse pour l'accueillir. Encart nom+titre sous la photo, dans une extension basse du
  même conteneur. Texte biographique à droite en 4-5 paragraphes justifiés. Ligne de
  références clients hors conteneur, précédée d'un chevron cyan plein.
- **Efficace parce que** : l'encastrement photo-dans-conteneur unifie la fiche en un
  seul objet ; la répétition exacte du gabarit sur 3 slides (seule la forme de la photo
  varie) montre un composant réutilisable, pas un one-off.

## 12. Comparaison avant/après en 2 colonnes + grande flèche de transformation

- **Situation/intention** : montrer concrètement ce qui change avec une transformation
  (« avant, on avait X ; après, on a Y ») — pattern manquant au catalogue de base malgré
  sa fréquence en deck de conseil.
- **Type** : 2 cartes contour côte à côte reliées par une grande flèche de contour.
- **Composition** (dérivé, VSCode3 `check_slide_synthese` v3-refonte, non commité) : 2
  conteneurs contour de largeur égale (composant transversal 1), étiquette pilule pleine
  (composant 2) en tête — pilule courte grise « AVANT » à gauche, pilule plus large
  cyan « APRÈS — <complément> » à droite (texte navy dessus, pas blanc : lisibilité sur
  fond clair). Items : puce « — » grise pour l'avant, « ✓ » colorée pour l'après, chaque
  item en (lead gras navy, reste normal coloré selon le camp). Entre les deux cartes :
  grosse flèche `MSO_SHAPE.RIGHT_ARROW` en CONTOUR épais (2.25pt) fill blanc, jamais un
  aplat plein. Bandeau de clôture signature (composant 7) sous les deux cartes.
- **Efficace parce que** : la carte « après » avec contour plus épais et étiquette
  pleine cyan (vs grise) hiérarchise sans dupliquer un aplat de fond ; les puces
  « —»/« ✓ » distinguent les deux camps sans dépendre de la seule couleur.

## 13. Chaîne de paires constat→réponse reliées par chevron

- **Situation/intention** : dérouler une série de « ce qu'on quitte → ce qu'on vise » en
  restant scannable, sans transformer chaque paire en diagramme de flux complet.
- **Type** : rangées de 2 pilules/cartes liées par un petit chevron horizontal.
- **Composition** (dérivé, VSCode3 v3-refonte) : chaque ligne = carte contour (constat,
  multi-lignes, composant 1) — petit chevron de connexion (composant 3, contour fin) —
  pilule pleine (réponse, texte court, composant 2). Cartes/pilules occupent chacune
  ~48% de la largeur de contenu, chevron ~6% au centre. Une ligne peut aussi être 2
  pilules pleine hauteur si les deux textes sont courts.
- **Efficace parce que** : le chevron porte le même vocabulaire graphique que le reste
  de la slide sans ajouter de poids visuel ; la hauteur de chaque ligne s'ajuste au
  texte le plus long des deux côtés.

## 14. Roadmap à double piste (organisationnel + technique en miroir)

- **Situation/intention** : montrer qu'une démarche a DEUX fils simultanés (ex.
  organisationnel et technique) qui avancent phase par phase, sans dupliquer le schéma
  en deux slides ni perdre le lien entre les deux fils.
- **Type** : rangée de N colonnes-phases (badge-chevron-titre en tête), chaque colonne
  portant un bloc « résultat » du premier fil ET un bloc « TECH » du second fil juste en
  dessous, séparés par un filet fin.
- **Composition** (dérivé, VSCode3 v3-refonte) : N conteneurs contour en colonnes égales
  (composant 1), badge numéroté à cheval sur le bord haut du chevron de titre
  (composants 4+3 combinés), pilule pleine de durée sous le titre (composant 2), texte
  de description avec emphase en ligne (composant 5), filet horizontal fin séparant le
  bloc « résultat » (gras navy centré) du bloc « TECH : … » (label gras + reste
  italique, MÊME famille de couleur MUTED dans toutes les colonnes quelle que soit la
  couleur propre de la phase — repère visuel constant). Grande flèche de connexion en
  CONTOUR reliant les badges d'une colonne à l'autre, visible seulement dans les
  interstices entre cartes.
- **Efficace parce que** : le label « TECH » répété identique dans chaque colonne crée
  un repère de lecture horizontal sans que la couleur de la phase (qui varie) ne casse
  ce repère.

---

## Hors périmètre — slides de marque partenaire (structure notée à titre indicatif)

Ces deux planches ne sont pas dans la palette OCTO : contenus de marque partenaire
insérés tels quels, non retenus comme patterns catalogue.

- **p.20 (marque « School of Product », vert vif)** : grille bento irrégulière mêlant
  tuiles chiffrées, tuiles icône seule, une tuile texte long, une tuile décorative —
  cellules inégales sur une grille 3×3 irrégulière. Structurellement intéressant
  (mélange stat+icône+texte+texture) mais hors palette et hors sujet restitution.
- **p.25-26 (marque « ground », noir + rouge/rose)** : CV structuré — bandeau
  d'en-tête noir texturé, nom en très grand rouge gras ; corps en 2 zones séparées par
  un filet vertical, labels de catégorie en rouge gras alignés à gauche, contenu à
  puces à droite ; photo ronde N&B en bas. Différent de la fiche bio (#11) : ici la
  structure est un CV factuel par catégories, pas un récit biographique.
