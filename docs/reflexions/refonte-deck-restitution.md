# Cadrage — Refonte complète du deck de restitution + espace démo/réel

> Statut : **cadrage à valider** (2026-07-22). Aucune ligne de code produit avant
> arbitrage. Fait suite à la demande « revoir complètement le deck PPT, reprendre la
> charte VSCode3/4, design + texte + visuels/encarts + têtes de chapitre + sommaire
> quali + simuler des interviews + espace démo vs réel avec cinématique de génération +
> rules de contrôle du rendu ». 6 chantiers, découpés en paliers indépendants ci-dessous.

## 1. Objectif

Le deck actuel (`pptx_export.build_presentation`) est **fonctionnel mais générique** :
il part d'un 16:9 vierge (ou d'un template client uploadé), dessine des cartes correctes
mais sans identité de marque, sans structure narrative (pas de couverture éditoriale, pas
d'intercalaires de chapitre, sommaire plat), sans visuels. La cible : un deck de
restitution **à la hauteur des vraies restitutions OCTO** (cf. les 6 versions de decks
`VSCode4/Exports/*.pptx` et le deck 32 slides `VSCode3/docs/cadrage-ppt/…-synthese.pptx`),
**construit sur le vrai template de marque**, avec chapitres, visuels encadrés, texte
travaillé — plus un **espace démo** pour montrer tout le parcours (interviews simulées →
génération) sans toucher aux vraies missions.

## 2. Sources de référence (repérées, factuel)

| Source | Ce qu'elle apporte | Chemin |
| --- | --- | --- |
| **Template OCTO réel** | masters/layouts/thème de marque (16:9, police Outfit, palette navy/cyan/slate), layouts nommés **Couverture**, **Chapitre** (cadre photo teardrop), **Visuel-droite/cadre blanc**, **Titre seul**, **Vide** | `VSCode3/docs/cadrage-ppt/template-octo.pptx` |
| **Générateur de référence** | 1952 l. montrant l'usage concret des layouts : couverture éditoriale, executive summary, slides claim+puces+**visuel encadré**, **3 têtes de chapitre** avec image + couleur de chapitre, personas, dot-scale maturité, chips | `VSCode3/docs/cadrage-ppt/generate_deck.py` |
| **Decks de restitution réels** | 6 versions itérées d'un vrai livrable OCTO (dispositif d'écoute RH) — repère visuel design + **texte** (titres-as-claim, encarts, rythme) | `VSCode4/Exports/*.pptx` |
| **Charte** (déjà mirrorée ici) | principes visuels : couleurs **du thème** (pas de constantes), police Outfit, **règles dures** (pas de dégradé/ombre, headers de card cyan/navy, corps blanc, ≥10,5 pt, rayon uniforme), grammaire de composants | `docs/vscode1-export/design-system-octo.md`, `template-octo.md`, `ppt-toolkit.md` |
| **Skills déjà présents ici** | `pptx-framed-image` (remplit un cadre photo au `prstGeom` exact + fetch d'image libre de droits avec repli procédural offline), `slide-text-polish` (lint copy : titre-claim, longueur de puce, filler) | `.claude/skills/` (greffés 2026-07-15, **non câblés**) |

## 3. État des lieux VSCode2 (écart à la cible)

| Brique | Existe déjà | Manque |
| --- | --- | --- |
| `pptx_deck.py` | `theme_colors(prs)`, `add_card`, `add_gauge`, `add_hbar`, `_no_shadow`, échelle typo `TYPE` | **`police_marque`/`set_police`** (détection + application Outfit), helpers `_surtitre` (label uppercase + filet) |
| `build_presentation` | paramètre `template_path` (sait démarrer d'un template), `verifier_geometrie` | **aucun template OCTO livré**, n'utilise **pas les layouts nommés** (couverture/chapitre/visuel), repositionne le titre à la main faute de placeholder de marque |
| Planches | Titre, Sommaire (plat), Exec Summary, Synthèse 5 cat., Difficultés, SWOT, Verbatims, Axes, Matrice, fiches reco | couverture éditoriale, **sommaire quali**, **têtes de chapitre**, slides **visuel+encart**, couleurs **du thème** (aujourd'hui navy/ambre/teal en dur), police de marque |
| Skills | `pptx-framed-image`, `slide-text-polish` présents | **non câblés** dans le générateur |
| Contrôle rendu | `verifier_geometrie` (formes hors-slide), `pptx-verify` (rendu réel), `FIELD_SHAPE` (parité aperçu/PPT) | pas de **check charte** (couleurs/typo/règles dures), `slide-text-polish` non intégré au pipeline |

**Bonne nouvelle** : le socle (`build_presentation(template_path=…)` + `theme_colors`) existe.
La refonte est surtout un **enrichissement**, pas une réécriture.

## 4. Cible design — chantiers 1 à 3

### Chantier 1 — Charte + template OCTO (socle)

- **Livrer** `template-octo.pptx` dans le repo (copie depuis VSCode3) + son md compagnon
  (`docs/vscode1-export/template-octo.md` déjà présent) ; le versionner.
- `build_presentation` **défaut = ce template** (au lieu du 16:9 vierge) ; un template
  client uploadé reste prioritaire (`mission.pptx_template_path`).
- Porter **`police_marque`/`set_police`** dans `pptx_deck.py` (détection Outfit sur les
  placeholders) et l'appliquer à tout texte dessiné.
- Remplacer les **couleurs chrome en dur** (navy/ambre/teal) par la lecture du thème
  (`theme_colors`) : navy = `dk1`, accent cyan = `accent3`, slate = `lt2`/`accent4-6`.
  Garder les couleurs **sémantiques** (vert/rouge/or SWOT, ambre difficultés) comme
  **données**, pas chrome (principe dataviz de la charte).
- Respecter les **règles dures** : pas de dégradé/ombre, headers de card cyan/navy, corps
  blanc, rayon uniforme.

### Chantier 2 — Structure narrative (couverture, sommaire quali, chapitres)

**Couverture** (layout « 40 - Couverture ») — remplit les placeholders de marque :

```
┌───────────────────────────────────────────────┐
│ [photo de couverture + overlay sombre natifs]  │
│                                                 │
│   RESTITUTION D'AUDIT                (surtitre) │
│   Audit Data & Organisation                     │
│   — DSI Groupe                       (titre)    │
│                                                 │
│   OCTO Technology · Juillet 2026     (crédit)   │
└───────────────────────────────────────────────┘
```

**Sommaire quali** (vs la liste plate actuelle) — regroupé en **chapitres** avec pastille
couleur + intitulé narratif, pas juste « 01 Executive Summary » :

```
   SOMMAIRE

   ●  01 · Ce qu'il faut retenir        (Executive Summary)
   ●  02 · Le diagnostic                (Synthèse, Difficultés, SWOT)
   ●  03 · La parole des équipes        (Verbatims)
   ●  04 · La trajectoire proposée      (Axes, matrice, reco)
```

**Têtes de chapitre** (layout « 50 - Chapitre ») — un intercalaire par chapitre, **grand
titre + numéro + image encadrée** (cadre teardrop rempli via `pptx-framed-image`), couleur
de chapitre reprise sur le sommaire :

```
┌───────────────────────────────────────────────┐
│                              ╭──────────╮       │
│   02                         │  photo   │       │
│                              │ encadrée │       │
│   Le diagnostic              ╰──────────╯       │
│   ▁▁▁ (filet couleur chapitre)                  │
└───────────────────────────────────────────────┘
```

→ le deck passe de N slides plates à **4 chapitres** rythmés, chacun ouvert par un
intercalaire, avec un fil narratif (retenir → diagnostiquer → écouter → agir).

### Chantier 3 — Visuels, encarts, refonte texte

- **Slides visuel+encart** (layout « 63 - cadre blanc ») pour au moins l'Executive
  Summary et une slide « vision/enjeux » : claim à gauche, **visuel encadré à droite**,
  **encart** (bande cyan « message à retenir » — déjà le pattern de l'exec summary
  actuel, à généraliser).

  ```
  ┌─────────────────────────────────┬───────────┐
  │ UNE DSI EXPERTE MAIS FREINÉE     │ ╭───────╮ │
  │                                  │ │ photo │ │
  │ • Silos métier/DSI (14 entret.)  │ │encadr.│ │
  │ • Gouvernance data immature      │ ╰───────╯ │
  │ • Socle humain solide            │           │
  ├─────────────────────────────────┴───────────┤
  │ ▐ Clarifier la gouvernance avant d'outiller ▌│ (encart cyan)
  └──────────────────────────────────────────────┘
  ```

- **Refonte texte** : passer chaque titre de slide au crible `slide-text-polish`
  (titre = **claim**, pas étiquette : « Le diagnostic » → « Trois freins structurels
  ralentissent la DSI » ; puces courtes, sans filler). Les titres IA-générés (exec
  summary headline, difficultés) sont déjà des claims — étendre au reste.
- **Encarts** réutilisables : bande « so-what », pastille « chiffre-clé », encart citation
  (déjà fait sur Difficultés — teal).

## 5. Chantier 4 — Simulation d'un jeu d'interviews (démo)

Un **jeu de données démo réaliste** : 1 mission « démo » avec ~6-8 interviews (libre +
structuré), transcriptions plausibles, tours de parole, verbatims, synthèse globale,
SWOT/difficultés/exec-summary/reco pré-remplis — de quoi montrer **tout le parcours** sans
IA ni saisie. Deux options d'implémentation (à arbitrer) :

- **(a) Seed déterministe** : un script `seed_demo.py` peuple une mission démo (comme les
  scripts de seed de test, mais persistant). Simple, mais fige le contenu.
- **(b) Fixtures JSON rejouables** : des fichiers `demo/interviews/*.json` chargés à la
  demande, régénérables. Plus souple pour enrichir la démo.

Recommandation : **(a)** pour le palier 1 (rapide, suffisant pour la cinématique), migrer
vers (b) si la démo doit grossir.

## 6. Chantier 5 — Espace démo vs réel + cinématique de génération

**Le plus gros chantier — nouveau feature produit, mérite son propre sous-cadrage.**
Intention : un **mode démo** cloisonné du réel, où l'on peut parcourir les interviews
simulées, rejouer tout le parcours (capture → analyse → synthèse → export), et voir une
**cinématique** de la génération du PPT (les slides qui se construisent).

### 6.1 Cloisonnement démo/réel — **RECTIFIÉ (2026-07-22) : voir `espace-demo-reel.md`**

> **Rectification (2026-07-22)** : après lecture du code VSCode1, la décision
> ci-dessous (« bases séparées ») reposait sur une lecture erronée de VSCode1 —
> ce qu'il sépare par base distincte, ce sont ses **environnements** (dev/preprod/
> prod), pas la démo/réel. Sa vraie démo/réel est un **flag `est_demo` même base +
> cookie + 1ère page + bandeau**. Arbitrage rouvert et tranché dans le sous-cadrage
> dédié `docs/reflexions/espace-demo-reel.md` : **flag `Mission.is_demo` (same-DB)**,
> démo modifiable/rejouable. Le texte ci-dessous est conservé pour mémoire.

### 6.1-bis (obsolète) Cloisonnement démo/réel — ~~DÉCIDÉ : bases séparées dès le départ (modèle VSCode1)~~

Arbitrage utilisateur (2026-07-22) : **séparer l'espace démo de l'espace réel dès le
départ, identique à VSCode1**. VSCode1 sépare ses environnements par **bases distinctes +
lancement dédié** — `data/dev/app.db` (port 3000), `data/preprod/app.db` (3001),
`data/prod/app.db` (3002). On applique le même principe à la démo :

- **Base démo dédiée** : `data/demo/app.db` vs le `data/app.db` réel, sélectionnée au
  lancement (l'app supporte déjà l'override `APP_DB_PATH`). Un mode `demo` (script/env de
  lancement dédié, ex. `uvicorn … ` avec `APP_DB_PATH=data/demo/app.db`) → isolation
  **totale** démo/réel, aucun risque de mélange, réinitialisation = régénérer la base démo.
- **Bandeau « MODE DÉMO »** permanent quand la base démo est active, pour lever toute
  ambiguïté.
- La base démo est **peuplée par le seed démo (P4)** ; elle peut rester consultable +
  rejouable sans polluer le réel.

À préciser au **sous-cadrage P5** : bascule (deux ports comme VSCode1, ou variable au
lancement), et si la démo est éditable ou en lecture seule (le seed la rend de toute façon
reproductible). Le principe *bases séparées dès le départ* est acté.

### 6.2 Cinématique de génération du PPT — **DÉCIDÉ : storyboard scripté**

L'export est aujourd'hui synchrone (clic → `.pptx`). La « cinématique » = montrer la
construction. Arbitrage utilisateur (2026-07-22) : **storyboard scripté**.

- Une page `/demo/.../cinematique` qui **rejoue les étapes du parcours en narration
  guidée** avec le contenu démo : capture → transcription → analyse (tours) → synthèse
  globale → « chapitre 1 généré », « chapitre 2 généré »… → deck prêt. Chaque étape est
  une séquence scriptée (texte + visuel de l'artefact produit), déroulée automatiquement
  ou au clic.
- S'appuie sur les artefacts déjà rendus côté web (aperçu des planches) comme illustrations
  d'étape, mais la trame est **narrative et scriptée** (un storyboard), pas un simple
  diaporama d'images ni un rendu PPT→PNG côté serveur (écartés : dépendance moteur de rendu
  non garantie en prod).
- Détail (séquencement, contenu de chaque étape, degré d'animation) au **sous-cadrage P5**.

### 6.3 Parcours démo

`/demo` → liste des interviews simulées → détail d'un entretien (tours, analyse) →
synthèse globale → **bouton « Générer le deck (cinématique) »** → révélation animée →
téléchargement du `.pptx` démo. Bandeau « MODE DÉMO » permanent, bouton « réinitialiser ».

## 7. Chantier 6 — Rules de contrôle du rendu

Formaliser les garde-fous (certains existent, les rendre systématiques) :

1. **`verifier_geometrie`** (existe) — aucune forme hors-slide. Déjà appelé en fin de
   `build_presentation`.
2. **Check charte** (nouveau) — un `verifier_charte(prs)` : couleurs chrome ∈ thème, police
   = police de marque, pas d'ombre/dégradé, headers de card cyan/navy. Lève si violation.
3. **`slide-text-polish`** (skill présent, à câbler) — lint copy sur `{titre, puces}` de
   chaque planche : titre-claim, longueur de puce, filler, abréviations.
4. **`pptx-verify`** (skill présent) — **gate obligatoire** au rendu réel avant tout
   « livré » (déjà la discipline, à inscrire dans `export-ppt-verifie`).
5. **Parité aperçu/PPT** (`FIELD_SHAPE`) — étendre aux nouvelles planches.

→ ces règles s'intègrent au playbook `export-ppt-verifie` et à `revue-increment` §2.

## 8. Découpage en paliers (indépendants, ordre recommandé)

| Palier | Contenu | Dépend de | Coût | Risque |
| --- | --- | --- | --- | --- |
| **P1 — Socle charte/template** | livrer template-octo, défaut build_presentation, police Outfit, couleurs du thème, règles dures | — | Moyen | Faible (socle éprouvé côté VSCode3) |
| **P2 — Structure narrative** | couverture, sommaire quali, têtes de chapitre (layout dédié), regroupement en 4 chapitres | P1 | Moyen | Moyen (design → validation pptx-verify + variantes) |
| **P3 — Visuels + encarts + texte** | slides visuel+encart (layout 63 + pptx-framed-image), refonte titres via slide-text-polish, encarts | P1 (P2 idéalement) | Moyen | Moyen (fetch image + fidélité) |
| **P4 — Jeu d'interviews démo** | seed démo (mission + ~6-8 interviews + tout le contenu de restitution) | — (indépendant) | Faible | Faible |
| **P5 — Espace démo/réel + cinématique** | flag is_demo, `/demo`, consultation lecture seule, cinématique de génération | P4 (contenu), P1-3 (deck cible) | **Élevé** | **Élevé** (nouveau feature — sous-cadrage dédié recommandé) |
| **P6 — Rules de contrôle du rendu** | verifier_charte, câblage slide-text-polish, gate pptx-verify, parité FIELD_SHAPE | P1 (charte à contrôler) | Faible | Faible (transverse, peu coûteux) |

**Chemin critique conseillé** : **P1 → P2 → P3** (le deck refondu, visible vite),
**P6** en parallèle (transverse, verrouille la qualité), puis **P4 → P5** (la démo, une
fois le deck cible en place à montrer). P5 fera l'objet d'un **sous-cadrage** avant code.

## 9. Arbitrages — **TRANCHÉS (2026-07-22)**

Le cap du cadrage est **validé**. Décisions utilisateur :

1. **3 fixes deck non commités** → **commit en baseline** avant la refonte (vérifiés,
   indépendants).
2. **Template** → **OCTO en défaut**, un template client uploadé (`pptx_template_path`)
   **reste prioritaire**.
3. **Cinématique (P5)** → **storyboard scripté** (cf. §6.2).
4. **Cloisonnement démo (P5)** → **bases séparées dès le départ, modèle VSCode1** (base
   démo dédiée `data/demo/app.db`, cf. §6.1).
5. **Images (P3)** → **fetch en ligne (Openverse CC0) + repli procédural offline**.

Reste à faire : lancer **P1** (socle charte/template) après le commit baseline. **P5**
(espace démo + cinématique) fera l'objet d'un **sous-cadrage dédié** avant code.

---

*Lié : `docs/vscode1-export/design-system-octo.md`, `template-octo.md`, `ppt-toolkit.md` ;
sources `VSCode3/docs/cadrage-ppt/` (template + generate_deck), `VSCode4/Exports/` (decks
réels) ; skills `pptx-framed-image`, `slide-text-polish`, `pptx-verify`, playbook
`export-ppt-verifie`.*
