# Catalogue de patterns — tribune interne de communauté (référence anonymisée)

Source : une tribune interne mensuelle d'un atelier OCTO, présentée par une de ses
tribus — prise de parole de communauté (mot du board, chiffres, cercles thématiques,
événements, annexe). 42 slides, gabarit OCTO 10×5.625in. Analyse du 2026-09-23 :
rendu PowerPoint COM regardé slide par slide, puis géométrie python-pptx. **Formes
seulement** : aucun texte, chiffre ni nom du deck source n'est repris ; le document
source n'est pas conservé.

Palette : thème identique à `template-octo.md`. Hors thème et récurrents : famille
cyan-teal `#00AFCB` / `#33B5CB` / `#00A3BE` ; voile bleu roi `#133B9D` et trait bleu
pâle `#94ABE2` (n°1). Les slides de gabarit (couverture, chapitres en goutte, clôture)
ne sont pas cataloguées : elles relèvent de `template-octo.md`.

---

## 1. Split-screen présentateur : photo en demi-cadre à voile duotone + pictos filaires

- **Situation/intention** : annoncer qui porte la prise de parole avec une ambiance visuelle, sans portrait.
- **Type** : split-screen image / typographie.
- **Composition** (p.3) : photo `ROUND_2_DIAG_RECTANGLE` 5.0×5.62in à (0,0) recouverte d'une forme de même cote remplie `#133B9D` (voile duotone). Semis de pictos géométriques filaires sur le voile (`TRIANGLE` 0.29in, `PLUS` 0.24in, `BLOCK_ARC` 0.35in, deux `DONUT` 0.82/0.20in), contour `#94ABE2` 0.8pt, sans remplissage. À droite, bloc texte 4.5×1.31in à (5.23,1.87), centré, sur 3 tailles : amorce 18pt normal, puis nom de l'entité 30 et 37pt gras navy `dk1`.
- **Efficace parce que** : le voile monochrome rend n'importe quelle photo compatible avec la marque ; les pictos filaires animent la moitié image sans concurrencer le texte.

## 2. Manifeste « Why » : pilule-titre couchée, onglet image et cadre-illustration emboîtés

- **Situation/intention** : poser la raison d'être d'une équipe (titre-slogan, phrase-signature, détail).
- **Type** : composition modulaire emboîtée, trois formes formant une silhouette unique.
- **Composition** (p.4) : `ROUND_2_SAME_RECTANGLE` 1.34×5.11in tournée de 90° (pilule couchée ≈5.11×1.34in) en haut à gauche, contour navy 0.8pt, titre 50pt gras navy. Accolée à droite, vignette image `ROUND_1_RECTANGLE` 1.39×1.34in à (6.16,0.39) avec contour de même cote. Dessous, cadre `ROUND_2_DIAG_RECTANGLE` 2.97×3.29in à (6.16,1.73) portant une illustration au trait. Texte à gauche 5.19×3.18in : accroche 15pt gras cyan, corps 11-12pt.
- **Efficace parce que** : même contour sur les trois formes → poids d'enseigne sans aucun aplat.

## 3. Trombinoscope en grille + colonne de cartes-chiffres navy à médaillon débordant

- **Situation/intention** : présenter une équipe nombreuse et ses indicateurs de composition.
- **Type** : grille de portraits + barre latérale de KPI. **Variante du n°1 de `catalogue-restitution.md`.**
- **Composition** (p.5) : 12 portraits `OVAL` 1.10in (contour `dk1` 0.8pt) en grille 4×3, pas ≈1.33in (h) × 1.47in (v), légende 10pt gras 2.14×0.56in sous chacun. À droite, 2 cartes `ROUND_2_DIAG_RECTANGLE` remplies `dk1` (1.66×1.27in à (7.73,1.01) et 1.66×2.38in à (7.73,2.67)), texte blanc 10pt ; chacune porte un médaillon `OVAL` blanc 0.65in avec icône, à cheval sur son bord droit.
- **Efficace parce que** : la grille rend la taille de l'équipe lisible d'un coup d'œil ; le médaillon débordant relie grille et chiffres sans trait.

## 4. Portraits en goutte (teardrop) avec rubans-étiquettes cyan

- **Situation/intention** : incarner un collectif festif ou d'accueil, certaines personnes portant une mention (arrivée, départ).
- **Type** : damier de portraits organiques + panneaux d'information.
- **Composition** (p.11) : 6 photos `TEARDROP` 1.26in en damier 2×3 (x=1.08/2.42in, y=1.39/2.71/4.03in), pointe orientée différemment d'une photo à l'autre. Rubans `ROUND_1_RECTANGLE` `#00AFCB`, texte 7pt gras blanc : horizontal 0.75×0.19in chevauchant le bas de la photo, ou vertical 0.19×0.72in collé au flanc. À droite, 2 panneaux `ROUND_2_DIAG_RECTANGLE` 4.76×1.71in contour navy, accroches 17.5pt gras cyan, corps 12pt.
- **Efficace parce que** : la goutte casse la rigidité du rond et donne un ton ludique ; le ruban marque un statut sans légende séparée.

## 5. Frise annuelle d'allocation : barres-pilules sur l'axe des mois, curseur fléché et bulle

- **Situation/intention** : expliquer comment un budget ou une ressource se répartit dans l'année, avec un point de revue daté.
- **Type** : frise d'allocation (pas un Gantt de tâches).
- **Composition** (p.12) : 12 mois en 10pt gras navy à y=1.28in. 2 pilules `ROUNDED_RECTANGLE` `#33B5CB` hautes de 0.35in (6.77in à (0.63,1.55) et 1.60in à (7.53,1.55)), texte blanc 7-9pt gras ; séparateur vertical `dk1` 2.2pt entre les deux régimes. Sous la grande pilule, cadre 6.77×0.81in en trait `#33B5CB` 0.8pt pointillé (détail en 11pt). Curseur vertical `#00A3BE` 2.2pt terminé par une pointe `TRIANGLE` `#00AFCB` 0.19×0.13in, qui mène à une bulle `RECTANGLE` `accent3` très pâle 2.41×1.54in (texte 10pt).
- **Efficace parce que** : la largeur des pilules montre la durée de chaque régime ; le curseur place la décision sur l'axe même.

## 6. Cartes de chantiers à onglet-titre navy portant une rangée d'avatars

- **Situation/intention** : faire le point sur plusieurs groupes de travail et montrer qui porte chacun.
- **Type** : grille 2×2 de cartes à onglet.
- **Composition** (p.14) : 4 cartes `ROUND_2_DIAG_RECTANGLE` contour navy 0.8pt (≈4.15×1.24 à 4.38×1.50in). En haut à gauche de chacune, un onglet `ROUND_2_DIAG_RECTANGLE` rempli `dk1` (1.57-1.92×0.50in ou 1.61×0.38in), nom 11-12pt gras blanc + porteurs 8pt. Dans le prolongement de l'onglet, 1 à 4 avatars `ROUND_2_DIAG_RECTANGLE` 0.55×0.51in accolés sans espace. Corps 11pt, sous-titres cyan, puces fléchées.
- **Efficace parce que** : l'onglet donne une identité d'équipe ; les avatars disent qui porte sans prendre la place du contenu.

## 7. Carte-annonce composite : cartouche-titre, image en coin, cartes emboîtées, pilule d'aide en pied

- **Situation/intention** : annoncer une campagne (support, échéance, lien) et renvoyer vers un contact.
- **Type** : affiche composée de cartes imbriquées en « L » autour d'une image.
- **Composition** (p.15) : image `ROUND_2_DIAG_RECTANGLE` 3.81×2.38in à (5.11,0.80) ; cartouche-titre blanc contour navy 3.69×1.22in (22pt) débordant sur l'image ; carte échéance 3.62×0.90in (13pt) sous l'image ; carte support 4.03×1.65in à gauche, en cases à cocher 10-13pt. En pied, pilule `ROUNDED_RECTANGLE` pleine `dk1` 5.37×0.37in, texte 11pt gras blanc.
- **Efficace parce que** : la pilule navy isole l'appel à l'action en dernière ligne de lecture.

## 8. Mosaïque 2×3 à tuiles hétérogènes : couleur = rôle de la tuile

- **Situation/intention** : présenter l'offre d'un cercle (formats, équipe, outil, contact) dont un élément est la ressource principale.
- **Type** : mosaïque de tuiles à rôles colorés. **Variante du n°3 de `catalogue-restitution.md`.**
- **Composition** (p.19) : tuiles `ROUNDED_RECTANGLE` 2.95×1.45in : 2 remplies `dk1` à gauche (texte blanc 8-10pt), 2 en `lt1` contour `dk1` 0.8pt au centre (l'une porte 4 avatars 0.55×0.52in). À droite, une tuile haute `accent3` 2.78×2.99in couvrant les deux rangées (titre 12pt gras, capture 2.67×0.94in, texte 8pt). Chapeau 8.35×0.80in gras navy au-dessus.
- **Efficace parce que** : chaque couleur encode une fonction ; la tuile haute dit « c'est ici qu'il faut aller » sans flèche.

## 9. Plan de communication en couloirs : pilules d'événements codées par la couleur

- **Situation/intention** : dresser le bilan d'une année de productions (événements, publications, talks) par canal.
- **Type** : frise en couloirs de pilules. **Variante du n°12 de `catalogue-restitution.md`.**
- **Composition** (p.20) : bandeau des mois `ROUND_2_DIAG_RECTANGLE` 8.17×0.27in à (1.32,1.38), libellés 9pt. 7 lignes de couloir (freeform 8.0-8.19in, contour `dk1` 0.8pt). Sur chaque ligne, pilules `ROUNDED_RECTANGLE` hautes de 0.27-0.41in, larges de 0.56-1.20in, texte 5-6pt gras ; le remplissage dit la catégorie : `dk1` = événement majeur (parfois vignette 0.29×0.21in), `accent1` = format secondaire (micro-picto 0.16in), `accent6` contour navy = publication. Pilule `accent6` sur toute la ligne = activité continue. Cartouches de groupe `ROUND_2_DIAG_RECTANGLE` ≈1.0in à gauche, compteurs 7pt.
- **Efficace parce que** : la densité de pilules par couloir montre l'effort par canal, sans légende.

## 10. Palmarès « Top N » : barres-onglets, étiquette de valeur reliée, médailles et avatars

- **Situation/intention** : classer des contenus par audience et créditer leurs auteurs.
- **Type** : classement en lignes (liste de barres, pas un graphique).
- **Composition** (p.22) : 10 lignes au pas de 0.48in. Par ligne : barre-titre `ROUND_1_RECTANGLE` haute de 0.39in (5.35-6.01in selon le titre, 10pt gras), doublée d'un tube `ROUND_2_SAME_RECTANGLE` tourné 6.06×0.39in en `lt1` contour `dk1` 0.8pt ; connecteur `dk1` 0.27in vers une étiquette de valeur `ROUND_1_RECTANGLE` 0.74×0.39in à x=6.92in (9pt) ; médaille pour les 3 premières lignes seulement ; avatars `OVAL` 0.56in à x=7.84 et 8.49in. Appel à l'action `ROUND_2_DIAG_RECTANGLE` `accent3` 2.15×0.63in en bas à droite.
- **Efficace parce que** : la valeur s'aligne dans une colonne fixe, la médaille ne marque que le podium, les avatars rendent le classement valorisant.

## 11. Annonce de programme : carte-brief, frise de production en pilules, jalon pictogramme

- **Situation/intention** : lancer une publication collective et montrer son calendrier de fabrication.
- **Type** : brief + mini-rétroplanning sous la carte.
- **Composition** (p.23) : titre-héros 32pt gras cyan + chapeau 16pt cyan (6.24×1.07in). Carte-brief `ROUND_2_DIAG_RECTANGLE` 5.35×2.73in à (0.62,1.53), contour navy, 10pt à rubriques grasses. Dessous, axe freeform 5.35in à y=4.97in portant 3 pilules `ROUNDED_RECTANGLE` `dk1` hautes de 0.39in (1.14/1.27/1.38in, 6pt gras blanc) et des repères `accent2` 0.8pt vers les mois (9pt) ; jalon final = picto 0.56in surmonté d'un libellé cyan 10pt gras. Deux photos-preuves à droite (`ROUNDED_RECTANGLE` 2.51×1.88in, `ROUND_2_DIAG_RECTANGLE` 3.54×3.37in).
- **Efficace parce que** : la même colonne donne le « quoi » et le « quand » ; le jalon en image signale le livrable.

## 12. Mur d'actualités en cartes débordantes décalées (slide « à lire »)

- **Situation/intention** : lister les réalisations récentes de plusieurs pôles, pour la lecture et non pour l'oral.
- **Type** : cartes décalées en quinconce, texte dense.
- **Composition** (p.29) : 4 cartes `ROUND_2_DIAG_RECTANGLE` blanches contour navy 0.8pt de tailles différentes (3.62×2.45, 3.26×2.15, 5.05×2.66, 4.52×2.15in), décalées en hauteur d'une colonne à l'autre (y=0.73 / 0.16 / 3.12in), certaines touchant le bord de slide. Texte 10pt, en-tête de pôle en capitales, verbes-clés en gras.
- **Efficace parce que** : le décalage évoque un fil qui défile et assume la densité ; le contour unique garde l'ordre.

## 13. Encart de consignes tri-colonne à filets partiels, ligne de risque en rouge

- **Situation/intention** : rappeler des bonnes pratiques puis nommer le risque — répété d'une slide à l'autre.
- **Type** : encart horizontal segmenté + illustration.
- **Composition** (p.36-39) : cadre `ROUND_2_DIAG_RECTANGLE` 5.65×1.59in à (0.57,2.30), contour navy 0.8pt, 3 colonnes de 1.64in séparées par 2 filets verticaux `accent4` 0.8pt hauts de 0.67in seulement ; colonne 1 = consigne 12pt gras italique, colonnes 2-3 = détail 11pt. Picto 0.38in débordant du bord droit. Sous le cadre, ligne « risque » en rouge avec puce cible. Image `ROUND_2_DIAG_RECTANGLE` 2.80×3.56in sur fond cyan à droite.
- **Efficace parce que** : les filets courts séparent sans enfermer ; l'ordre règle → justification → risque se retrouve sur chaque slide.

## 14. Affiche d'événement : panneau sombre latéral, cartes-chiffres, bandeau-sticker diagonal

- **Situation/intention** : promouvoir un événement (date, lieu, audience, thème, partenaires) avec un appel à réserver.
- **Type** : affiche en deux panneaux (la slide source suit la charte d'une marque partenaire ; transposer en navy/cyan).
- **Composition** (p.25) : panneau gauche `RECTANGLE` sombre 4.20×5.62in (accroche 22pt gras, corps 11.5pt, bouton `ROUNDED_RECTANGLE` 2.67×0.70in 13pt gras). À droite : bandeau-date `ROUNDED_RECTANGLE` 4.15×0.95in (17pt gras / 12pt) ; 3 cartes-chiffres `ROUNDED_RECTANGLE` 1.32×0.95in blanches à contour sombre 1.2pt (chiffre 22pt gras, libellé 10pt) ; bloc thème 4.15×1.15in ; colonne de logos à x≈9.05in. Bandeau `RECTANGLE` `accent3` 3.21×0.59in tourné en diagonale en travers du coin haut droit (11pt gras).
- **Efficace parce que** : le panneau sombre porte l'émotion et l'appel à l'action, le clair les faits ; le bandeau diagonal fait « sticker ».
