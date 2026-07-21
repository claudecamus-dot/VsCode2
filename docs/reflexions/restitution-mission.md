# Étude — Restitution de mission : consolider les entretiens en un deck (difficultés / améliorations / SWOT / verbatims), charte OCTO ou client

Étude de conception (pas d'implémentation) demandée le 2026-07-21 : une fois **tous les
entretiens d'une mission finalisés**, produire la restitution — consolider sur la base des
entretiens et des **verbatims** les **axes de difficultés**, les **axes d'améliorations**,
une **matrice SWOT**, le tout **exportable en PPT** selon la **charte OCTO** (ou une charte
client). Porte sur trois plans : le **contenu** à restituer, le **design UX/UI**, et les
**slides** à générer. S'appuie sur la trame de questions existante, la grille de maturité du
projet frère (VSCode1, mirroir `docs/vscode1-export/`) et un brainstorm. Fait suite au
pipeline de synthèse/recommandations déjà en place.

## Relance (2026-07-21) — état construit et cadrage du rapport complet

Reprise du cadrage à l'aune de l'existant : les **Paliers 1 (SWOT) et 2 (verbatims) sont
livrés** (commits `e874cee`/`eff8b90` puis `a3ef3a1`). Cette section fait l'état des lieux du
**rapport tel qu'il est produit aujourd'hui**, ses écarts vs l'étude ci-dessous, et les
décisions restantes pour un rapport *complet*. Le reste du document (§0-§6) est l'étude
initiale, conservée comme cadrage d'origine — vérifié contre le code le 2026-07-21.

### A. État construit vs l'étude

| Élément de l'étude (§3/§6) | État | Écart notable |
| --- | --- | --- |
| `_slide_swot` (2×2, IA, O/M externe) | **Livré** (Palier 1) | Réglage 5.1 tranché = génération IA (`generate_swot`, `MissionSwot`). +7 correctifs de revue `bmad-code-review` (troncature quadrant, coercion JSON liste/dict, parité sommaire). |
| `_slide_verbatims` « Paroles d'acteurs » | **Livré** (Palier 2) | **Approche légère** retenue : sélection **manuelle** (cases à cocher), pas de modèle citation neuf — référence par ids sur `Mission.restitution_verbatim_ids`, cartes dimensionnées au contenu. |
| `_slide_difficultes` + insert citation (§3.2) | **Non livré** | Le Palier 2 léger a sauté la planche « difficultés » dédiée et ses inserts citation ; l'« insert citation prévu de longue date » reste non construit. |
| Sélection **IA** des verbatims représentatifs | **Non livré** | Manuel pour l'instant (l'utilisateur coche 2-4 citations). |
| `_slide_maturite` (Palier 3) | **Non livré** | Todo ; décision de paradigme en attente (voir E). |

### B. Le rapport produit aujourd'hui (fonctionnel)

`build_presentation` génère, dans l'ordre, derrière bascules `include_*` (défaut : inclus si
la matière existe) :
**Titre → Sommaire → Synthèse** (1 slide / catégorie non vide, 5 max) **→ Matrice SWOT**
(`include_swot`) **→ Paroles d'acteurs** (`include_verbatims`) **→ Axes d'améliorations**
(overview) **→ Matrice effort/valeur** (nuage XY natif) **→ Détail par recommandation**
(jauges). Garde-fou géométrie final (`D.verifier_geometrie`) ; template client hérité ou deck
OCTO vierge 16:9 (`mission.pptx_template_path`).

Éditeur aperçu (`apercu.html`) : un onglet par slide, avec **onglet SWOT** (4 zones + aperçu
2×2 live + génération/régénération IA protégée par `confirm`) et **onglet Verbatims** (cases à
cocher + planche live « Paroles d'acteurs »), parité sommaire aperçu↔PPT tenue. Cases
d'inclusion export : « Matrice SWOT », « Paroles d'acteurs ».

### C. Cadrage design (conventions réellement construites)

- **SWOT** : grille 2×2, couleurs sémantiques (vert/rouge/bleu/ambre en repli OCTO, dérivables
  du thème du template), liseré de carte, pagination anti-troncature d'un quadrant long.
- **Verbatims** : cartes-citations **dimensionnées au contenu** (pas étirées à `area_h/n`,
  évite les vides), liseré teal, troncature propre avant débordement.
- **Charte** : template client (thème/masters/logo) ou deck OCTO vierge (Navy `#0E2356` / Cyan
  `#00D2DD`, pas de dégradé ni d'ombre, différenciation par bordure). Les skills PPT
  (`pptx-framed-image`, `slide-text-polish`, `restitution-deck-design`) restent le chemin
  d'enrichissement du playbook `export-ppt-verifie` — **jamais encore exercées sur ce deck**.

### D. Écarts vers le « rapport complet »

1. **Planche « Difficultés » dédiée + inserts citation** (§3.2) — non construite. Aujourd'hui
   les difficultés vivent dans la slide Synthèse (`points_amelioration`) et les axes de reco ;
   pas de planche « top difficultés hiérarchisées, chacune avec un verbatim en encadré ». C'est
   le principal manque *fonctionnel* vs la vision initiale. Coût : une slide + un onglet +
   un sélecteur de verbatim par difficulté (le sélecteur était déjà prévu §4.1).
2. **Sélection IA des verbatims** — manuelle aujourd'hui ; un tri auto (1-2 citations
   représentatives par difficulté/quadrant) allégerait la charge, mais le manuel est défendable
   (l'humain juge la représentativité). Priorité faible.
3. **Palier 3 maturité** — voir E.
4. **Passe design/rendu sur le rapport entier** — le deck a été vérifié *slide par slide*
   (`pptx-verify` sur SWOT, sur verbatims) mais jamais traversé de bout en bout par
   `restitution-deck-design` sur une mission réelle complète. Gain de **finition** (cohérence
   slide-à-slide, hiérarchie visuelle), pas une correction.

### E. Décisions à trancher (arbitrage utilisateur)

- **Palier 3 maturité — go/no-go.** Grille 0-3 par pilier (= thème de trame), *complémentaire*
  du nuage effort/valeur 1-5 (axes différents, cf. §5.2), pas un remplacement. À ne lancer que
  si une lecture « maturité » a de la valeur pour la restitution visée — **ajout de paradigme,
  pas une correction**. Si go : commencer par une **table** (robuste au texte FR long des
  thèmes) avant le radar.
- **Planche « Difficultés » + inserts citation — construire ?** Oui si le rapport doit
  *hiérarchiser* les difficultés (au-delà de leur présence dans la Synthèse) et donner enfin
  corps à l'insert citation ; sinon l'existant (Synthèse + SWOT + Paroles d'acteurs + axes)
  couvre déjà « difficultés/améliorations » comme un recadrage d'affichage.
- **Sélection IA des verbatims — un incrément ?** Reco : différer (manuel suffisant).
- **Aside code-mort** : `Recommendation.status_label` (renvoie `self.status`, colonne
  inexistante sur `Recommendation` — copiée de `GlobalSynthesis`) reste à retirer, signalé par
  l'étude d'origine.

**Synthèse de la relance** : le rapport livre déjà l'essentiel du neuf demandé (SWOT +
verbatims restitués). Pour un rapport *complet* au sens de l'étude, l'unique manque fonctionnel
franc est la **planche « Difficultés » hiérarchisées + inserts citation** ; la **maturité**
(Palier 3) est un choix de paradigme à confirmer ; le reste est **finition** (passe design de
bout en bout). Aucune de ces trois pistes n'est engagée sans arbitrage — cette relance est un
cadrage, pas une implémentation.

### F. Enseignements des decks exemples OCTO (analyse 2026-07-21)

Analyse d'un corpus de decks OCTO réels (deux répertoires `Downloads/Exemples…`, ~30 fichiers) —
en majorité des **propositions commerciales d'assessment** (démarche, équipe, budget, références
OCTO), et **deux vraies restitutions** décortiquées : `Quality Assessment` (52 slides, structure
de restitution complète, slides rendues en PNG via PowerPoint COM) et `EPI Assessment Infra`
(annexes « SWOT / Recommandations », « Synthesis with green/red lights »). *N.B. : contenu client
confidentiel — seuls les **patterns** de structure et de design sont repris ici, jamais les
données client.*

**F.1 Structure-type d'une restitution d'assessment OCTO** :
Cover → **Sommaire numéroté** (01, 02…) → **Executive Summary** → **Méthode d'assessment** (le
cadre) → **Findings** catégorisés et *benchmarkés* contre un « state of the art » → **Recommandations**
(cartes d'action priorisées) → **Analyse détaillée** par sujet (derrière intercalaires numérotés)
→ SWOT / Synthèse « feux ». Le rapport du projet couvre déjà Synthèse / SWOT / Axes / Reco ; il lui
**manque l'Executive Summary, les intercalaires de section, et une synthèse « feux »**.

**F.2 Conventions design confirmées au rendu réel** :
- **Palette** : Navy `#0E2356` (titres/texte) + **Cyan `#00D2DD`** (accent), cartes gris clair,
  blanc — exactement le design system OCTO déjà décrit au §4.2, **confirmé visuellement**.
- **Titres** : CAPITALES navy précédées d'un **petit trait cyan à gauche** (le « souligné »).
- **Bandes « key message » cyan** : chaque slide-clé se clôt sur une **bande pleine largeur cyan**,
  texte blanc gras, portant le « so what » / message à retenir. Pattern fort **absent du générateur**.
- **Findings / reco en cartes titrées** : titre gras + détail en *italique* + **icône sémantique**
  (loupe, visage triste/content, boucle de feedback, post-its, le « O » mascotte). Le générateur
  actuel est en **prose sans icônes**.
- **Benchmark** : findings cadrés contre une norme (« state of the art > 50% », « maturity model »).
- **Intercalaires de section** : slide pleine couleur, **gros numéro + chevron `>`** + titre blanc.
- **Chrome de pied** : tagline « OCTO TECHNOLOGY > THERE IS A BETTER WAY », logo « O », **numéro de
  slide dans un triangle d'angle coloré**.

**F.3 Ce que ça change pour le cadrage** :
1. **Executive Summary** *(nouveau ; fort, peu coûteux)* — slide d'ouverture « so what » (constat
   + message-clé) après le Sommaire ; le pattern d'ouverture le plus systématique des restitutions
   OCTO, absent du rapport.
2. **Bandes « key message »** *(design)* — un composant `pptx_deck` « bande cyan pleine largeur »
   pour le message à retenir de la Synthèse / SWOT / Difficultés.
3. **Synthèse « feux » vert/ambre/rouge** *(nouveau)* — une slide de statut par thème (vue chez EPI :
   « Synthesis with green/red lights »). **Adjacente à la maturité** : un score 0-3 se rend
   naturellement en feux — les deux pistes convergent, à concevoir ensemble.
4. **Benchmark → renforce la maturité (Palier 3)** — les restitutions OCTO benchmarkent réellement
   (maturity models, state-of-the-art %). La grille 0-3 par pilier n'est pas un ajout exotique mais
   **un pattern OCTO courant** ; l'argument « go » de la décision E gagne du poids.
5. **Planche « Difficultés » en cartes + icônes + benchmark** — le gap fonctionnel D.1/E doit suivre
   le pattern « findings » observé (cartes titrées, détail italique, icône, bande « so what »), pas
   une simple liste.
6. **Icônes + intercalaires + chrome** *(finition)* — le deck actuel est plus minimal ; la passe
   `restitution-deck-design` (D.4) devrait viser ces conventions.

**Reprise des décisions (E, mise à jour par F)** : la **maturité** est *renforcée* (benchmark =
pattern OCTO) et **converge** avec une synthèse « feux » ; deux **pistes neuves** à fort ratio
valeur/coût émergent — **Executive Summary** et **bandes key-message** — que je recommande de
prioriser *avant* la maturité (ouverture et « so what » percutants, quasi sans risque). La planche
**Difficultés** reste le manque fonctionnel franc, à construire au pattern « findings » ci-dessus.

## 0. Ce qui existe déjà et sur quoi s'appuyer (beaucoup)

La restitution n'est pas un chantier vierge : le squelette est déjà là.

- **Consolidation des entretiens → 5 catégories** (`app/services/synthese_ai.py`,
  `GLOBAL_SCHEMA` l.190-203) : `contexte`, `culture_adn`, `forces_succes`,
  `points_amelioration`, `aspirations`. `generate_global_synthesis()` (l.333-362) agrège
  **tous** les entretiens — structurés (`material_by_theme` = question → réponse + verbatims,
  `routers/synthese.py:86-126`) **et** libres (`material_libre` = `Interview.repartition` 5
  catégories, `synthese.py:129-136`) — en **map-reduce** (découpe aux frontières de
  thème/entretien, fusion dédiée `GLOBAL_REDUCE_SYSTEM`). Stocké sur `GlobalSynthesis` (1:1
  mission, `status` empty/generated/edited). **→ « difficultés » ⊂ `points_amelioration`,
  « améliorations/aspirations » ⊂ `aspirations` : la matière est déjà extraite.**
- **Axes d'améliorations = recommandations** (`RecommendationAxis` 3-4 axes transverses →
  `Recommendation`, `models.py:409-461`) avec `objectif`, `acteurs`, `valeur`/`complexite`
  (1-5), `proposition_valeur`, `plan_actions`, `resultats_attendus`. Générés **depuis la
  synthèse globale** (pas depuis les réponses brutes, `_build_reco_prompt` l.491-505). **→
  les « axes d'améliorations » existent déjà comme axes de reco.**
- **Deck PPT** (`app/services/pptx_export.py`) : `_slide_title`, `_slide_sommaire`,
  `_slide_synthese_categorie` (1 slide/catégorie), `_slide_axes_overview`,
  `_slide_matrice_effort_valeur` (nuage XY natif `complexite × valeur`), `_slide_recommendation`
  (détail + jauges). `build_presentation()` (l.508-584) : **template client** (`mission.
  pptx_template_path` → hérite thème/masters/logo) **ou** deck vierge 16:9 stylé `pptx_deck`,
  avec bascules `include_*` par groupe de slides. Garde-fou géométrie final obligatoire
  (`D.verifier_geometrie`). **→ « charte OCTO ou client » : déjà supporté par le mécanisme de
  template.**
- **Éditeur web « aperçu »** (`app/templates/synthese/apercu.html`) : 1 onglet par slide,
  panneau **deux colonnes** (formulaire autosave HTMX à gauche, **aperçu visuel live** à
  droite), « fit hints » qui prédisent le rendu réel (`pptx_export.FIELD_SHAPE` +
  `D.ajuster_police`). **→ l'UX d'édition slide-par-slide existe, extensible.**
- **Verbatims capturés mais JAMAIS restitués** : `Verbatim` (`models.py:295-315`,
  `question_id` + `quote`) alimente le *prompt* de synthèse (sous-bloc « Verbatims : » par
  thème) mais **n'apparaît nulle part dans le deck ni l'aperçu** — `grep verbatim
  pptx_export.py` = 0. Le docstring du modèle mentionne pourtant « les inserts *citation* du
  deck » : **fonctionnalité prévue, jamais construite.** C'est le principal gisement neuf.
- **Trame de questions** (`Mission → Trame → Theme → Question`, `models.py:106-170` ;
  `qtype` open/scale/choice, `help_text`) : arbre peu profond thème → question. Pas de trame
  *seed* ; `examples/trames/Guide d'Interview.docx` est la cible d'import réel
  (`app/importers/docx_trame.py`). **→ les thèmes de trame sont les « piliers » naturels d'une
  éventuelle grille de maturité.**
- **Grille de maturité** (inspiration, `docs/vscode1-export/`, projet frère VSCode1 — *pas*
  ce projet) : piliers notés **0-3**, rendus en **radar** (polygones natifs) avec superposition
  « évolution vs n-1 », en-têtes « MATURITÉ PAR PILIER ». Paradigme **différent** du nuage
  effort/valeur (1-5) d'ici — candidat *complémentaire*, pas remplaçant.

**Conclusion du §0 : le neuf demandé se réduit à 3 briques** — (a) une **SWOT**, (b) la
**restitution des verbatims** (l'insert citation prévu), (c) *optionnellement* une **grille de
maturité par pilier**. Les « axes de difficultés/améliorations » sont surtout un **recadrage
d'affichage** de matière déjà extraite, pas une nouvelle génération.

## 1. Le contenu à restituer — reprojeter les 5 catégories en blocs de restitution

Les 4 blocs demandés se dérivent de matière existante ; seule la SWOT et les citations
demandent une brique nouvelle.

| Bloc demandé | Source déjà présente | Neuf à produire |
| --- | --- | --- |
| **Axes de difficultés** | `points_amelioration` (+ tensions de `culture_adn`) | Recadrage : hiérarchiser/nommer les difficultés, y accrocher 1 verbatim |
| **Axes d'améliorations** | `RecommendationAxis` (déjà transverses) + `aspirations` | Recadrage / réutilisation directe de `_slide_axes_overview` |
| **Matrice SWOT** | Forces←`forces_succes`, Faiblesses←`points_amelioration` ; Opportunités/Menaces partiellement dans `aspirations` | **Génération dédiée** (cf. §5 : O/M ne se déduisent pas des 5 cat.) |
| **Verbatims** | `Verbatim.quote` (capturés, non affichés) | **Sélection + affichage** (insert citation prévu, jamais fait) |

## 2. Consolidation depuis les entretiens & verbatims

Rien à réinventer côté agrégation : les nouveaux blocs se nourrissent **de la même matière**
que la synthèse globale (`material_by_theme` + `material_libre` + verbatims), déjà collectée et
map-reduite. Deux ajouts de modèle seulement.

**2.1 La SWOT — un nouvel objet 1:1 mission** (parallèle à `GlobalSynthesis`) :

```python
class MissionSwot(Base):           # 1:1 Mission, comme GlobalSynthesis
    forces: Mapped[str]            # interne +
    faiblesses: Mapped[str]        # interne -
    opportunites: Mapped[str]      # externe +
    menaces: Mapped[str]           # externe -
    status: Mapped[str]            # empty | generated | edited
```

Générée par un `generate_swot()` calqué sur `generate_global_synthesis` (même map-reduce,
schéma `SWOT_SCHEMA` à 4 clés, `SWOT_SYSTEM` demandant des puces factuelles + « ne pas
inventer ») **nourri de la synthèse globale déjà produite** (comme les recommandations le
sont) plutôt que des réponses brutes — moins de tokens, plus cohérent. Repli démo
déterministe (`generate_demo_swot`, cf. `generate_demo_recommendations`).

**2.2 Les verbatims comme preuve** — surface l'existant. Deux options de rattachement :

- **Léger (recommandé pour démarrer)** : à la génération, l'IA **sélectionne** 1-2 verbatims
  représentatifs par bloc (difficulté / quadrant SWOT) parmi ceux déjà en base, et on stocke
  leurs `id` dans un champ JSON du bloc (`{"verbatim_ids": [...]}`). Aucun nouveau modèle, on
  réutilise `Verbatim`. La citation affichée = `Verbatim.quote`.
- **Lourd** : un modèle `RestitutionCitation` taggant verbatim → bloc, édité à la main. Plus
  de contrôle éditorial, plus de surface. À réserver si le pick automatique déçoit.

## 3. Les slides à générer (PPT)

Insérées dans le flux `build_presentation`, chacune derrière une bascule `include_*`
(comportement par défaut : incluses si la matière existe), respectant le garde-fou géométrie.

1. **`_slide_swot`** — quadrant 2×2 (Forces / Faiblesses / Opportunités / Menaces), puces
   courtes, couleurs sémantiques *dérivées du thème du template* (vert/rouge/bleu/ambre en
   repli OCTO). Le plus demandé, le plus autonome.
2. **`_slide_difficultes`** — top difficultés hiérarchisées (depuis `points_amelioration`),
   chacune avec **1 verbatim** en encadré citation (l'insert prévu). Sévérité/fréquence
   optionnelle en pastille.
3. **Axes d'améliorations** — réutilise `_slide_axes_overview` (déjà là), éventuellement
   renommé dans le sommaire.
4. **`_slide_verbatims`** (« Paroles d'acteurs ») — une planche de citations représentatives
   (2-4), format « transcription éditée » (préfixe interlocuteur teal/navy, cf.
   `interview_pdf_export._dialogue_flowables` réutilisable comme référence visuelle).
5. **`_slide_maturite`** *(optionnel, §6 Palier 3)* — **radar 0-3 par pilier** (piliers =
   thèmes de trame), superposition « cible » / « actuel ». Question ouverte héritée de
   `docs/vscode1-export/points-amelioration-ppt.md` : **radar** (silhouette globale, mais
   labels d'axes longs qui wrappent) vs **table** (pilier + barre de score + delta, pas de
   troncature) vs **barres groupées**. Recommandation : commencer par la **table** (robuste au
   texte français long des thèmes), radar en évolution ultérieure.

`build_presentation` gagne `include_swot`, `include_difficultes`, `include_verbatims`,
`include_maturite` (mêmes cases que `apercu.html`). La **matrice effort/valeur** existante
reste (elle répond à « quoi prioriser », orthogonale à la SWOT).

## 4. Design UX/UI

**4.1 Éditeur aperçu** — étendre le pattern existant (1 onglet/slide, deux colonnes
form+preview+fit-hint), pas en inventer un :

- Onglet **SWOT** : 4 zones de texte (une par quadrant) + aperçu 2×2 live.
- Onglet **Difficultés** : liste éditable + **sélecteur de verbatim** par difficulté (menu
  puisant dans les verbatims de la mission).
- Onglet **Verbatims** : sélection des citations représentatives (cases à cocher sur la liste
  des verbatims capturés).
- Onglet **Maturité** (Palier 3) : par pilier, un curseur 0-3 (actuel/cible).
- Les cases d'inclusion de l'export (`apercu.html` l.49-76) gagnent SWOT / Difficultés /
  Verbatims / Maturité.

**4.2 Charte OCTO ou client** — le mécanisme existe (`mission.pptx_template_path`) ; les
nouvelles slides doivent :

- **puiser les couleurs dans le thème du template** quand il est fourni (comme la palette
  catégorielle s'ancre sur `accent1`, `pptx_export.py:531-535`) ;
- **retomber sur le design system OCTO** en deck vierge : Navy `#0E2356` (titres/texte), Cyan
  `#00D2DD` (accent), slates pour bordures/fonds ; **pas de dégradé, pas d'ombre**
  (différenciation par bordure), police Outfit, labels de section en CAPITALES soulignées
  (cf. `docs/vscode1-export/design-system-octo.md`). Les cadres photo « ici mettre une Photo »
  (`round2DiagRect`) d'un template client se remplissent via la skill `pptx-framed-image`.
- Vérification obligatoire du rendu réel par la skill **`pptx-verify`** (python-pptx est un
  parseur tolérant — un deck qui parse peut ne pas s'ouvrir dans PowerPoint), et passe design
  par **`restitution-deck-design`** — c'est le playbook `export-ppt-verifie`.

## 5. Deux réglages à trancher

**5.1 SWOT : génération IA ou dérivation déterministe ?** Une dérivation mécanique
(Forces←`forces_succes`, Faiblesses←`points_amelioration`) est **tentante mais fausse** : la
SWOT n'est pas isomorphe aux 5 catégories — surtout **Opportunités/Menaces**, qui sont un
regard *externe* (marché, concurrence, risques réglementaires/techniques) largement absent des
catégories internes ; `aspirations` n'en couvre qu'une partie. Un mapping 1:1 produirait une
SWOT bancale (2 quadrants pleins, 2 vides). → **Génération IA dédiée** (schéma SWOT, nourri de
la synthèse globale + un cadrage explicite « O/M = regard externe »), avec repli démo. Coût :
un appel Ollama de plus, petit (il consomme la synthèse déjà consolidée, pas les entretiens
bruts — même profil que les recommandations, ~un appel court).

**5.2 Échelle de maturité 0-3 vs scoring reco 1-5.** Deux axes **différents** à ne pas
confondre : `valeur`/`complexite` (1-5) notent *une recommandation* (quoi prioriser) ; la
maturité (0-3) note *un pilier* (où en est l'organisation). Les garder distincts — la grille de
maturité est une slide **complémentaire**, pas une refonte du scoring. Le 0-3 vient du modèle
OCTO frère ; le mapping piliers = thèmes de trame est direct mais suppose que la trame couvre
bien des dimensions de maturité (vrai pour un audit, à valider sur `examples/trames/`).

## 6. Phasage proposé (si ce chantier est lancé)

Trois paliers indépendants et livrables séparément — dans l'ordre valeur/risque.

- **Palier 1 — SWOT** *(risque faible, valeur forte ; ~un incrément)* : `MissionSwot` +
  `generate_swot` (map-reduce calqué sur la synthèse) + `_slide_swot` + onglet aperçu SWOT +
  bascule d'inclusion. Autonome : livre une vraie SWOT restituable même si 2 et 3 ne se font
  jamais. Vérif : `pptx-verify` obligatoire (nouvelle slide).
- **Palier 2 — Verbatims restitués** *(risque faible-moyen)* : sélection (auto) de 1-2
  verbatims par difficulté/quadrant + `_slide_verbatims` + inserts citation sur
  `_slide_difficultes` — construit enfin l'« insert citation » prévu de longue date. UX :
  sélecteur de verbatim dans l'éditeur.
- **Palier 3 — Grille de maturité par pilier** *(chantier plus lourd, complémentaire)* :
  modèle piliers/score 0-3 + `_slide_maturite` (commencer par une **table**, radar
  vectorisé ensuite) + onglet aperçu. Décision radar-vs-table à trancher au moment du build
  (cf. `points-amelioration-ppt.md`). Ne se lance que si la valeur d'une lecture « maturité »
  est confirmée avec l'utilisateur — c'est un ajout de paradigme, pas une correction.

Le **Palier 1 seul** apporte déjà l'essentiel du neuf demandé (la SWOT), les « axes de
difficultés/améliorations » étant très largement couverts par la synthèse `points_amelioration`
et les axes de reco existants — leur restitution est d'abord un **recadrage d'affichage**.

---

*Note technique repérée en passant (hors périmètre de cette étude) : `Recommendation.
status_label` (`models.py:458-460`) référence `self.status`, colonne inexistante sur
`Recommendation` — code mort (copié depuis `GlobalSynthesis`), à retirer lors d'un prochain
passage.*
