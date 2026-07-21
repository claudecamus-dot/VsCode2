# Étude — Restitution de mission : consolider les entretiens en un deck (difficultés / améliorations / SWOT / verbatims), charte OCTO ou client

Étude de conception (pas d'implémentation) demandée le 2026-07-21 : une fois **tous les
entretiens d'une mission finalisés**, produire la restitution — consolider sur la base des
entretiens et des **verbatims** les **axes de difficultés**, les **axes d'améliorations**,
une **matrice SWOT**, le tout **exportable en PPT** selon la **charte OCTO** (ou une charte
client). Porte sur trois plans : le **contenu** à restituer, le **design UX/UI**, et les
**slides** à générer. S'appuie sur la trame de questions existante, la grille de maturité du
projet frère (VSCode1, mirroir `docs/vscode1-export/`) et un brainstorm. Fait suite au
pipeline de synthèse/recommandations déjà en place.

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
