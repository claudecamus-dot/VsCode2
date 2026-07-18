---
name: revue-increment
description: Boucle systématique de revue ET d'amélioration de fin d'incrément (ou de séance) pour Interview-to-Deck — ne se contente pas de constater : elle applique les correctifs et re-vérifie. Passe en revue le code produit ET la FAÇON de travailler (vérité terrain vs roadmap, vérification réelle et pas juste pytest vert, cohérence, docs de suivi, capitalisation mémoire), puis exécute les actions d'amélioration (code-review --fix / simplify / edits concrets) et re-vérifie. À lancer avant de considérer un incrément « livré », avant chaque commit de code, ou sur demande de rétrospective. Le hook SessionStart la rappelle à chaque session.
---

# Revue-et-amélioration systématique d'incrément

Ce projet a accumulé des leçons dures (voir la mémoire `feedback-*` et le
`CLAUDE.md`) : la roadmap dérive du code, `pytest` vert ne prouve pas qu'un
`.pptx` s'ouvre, du travail non commité apparaît sans que le suivi ne le
reflète. Cette boucle **systématise** l'application de ces leçons pour qu'on ne
les redécouvre pas à chaque fois — et surtout elle **agit** : une revue qui ne
produit que des constats ne vaut rien, l'objectif est un incrément amélioré et
re-vérifié.

Deux phases, dans l'ordre :

- **Phase A — Revue** (6 passes ci-dessous) : établir des constats *vérifiés*
  (un fait, pas « oui je pense »), portant sur deux niveaux — **le produit**
  (code/écrans corrects et cohérents) et **la façon de travailler** (la méthode
  a-t-elle servi l'objectif).
- **Phase B — Améliorations** : transformer chaque constat rouge en correctif
  appliqué, puis re-vérifier. Ne jamais s'arrêter à la liste de constats.

## Coexistence avec BMAD (routage — ne pas refaire ce qu'un skill BMAD fait mieux)

Depuis l'install BMAD (`_bmad/`, ~46 skills `bmad-*`), ce skill reste le
chef d'orchestre « definition of done avant commit », mais **délègue** quand un
skill BMAD est plus outillé — ne pas réimplémenter à la main :

- Revue de code adversariale (Blind Hunter / Edge Case / Acceptance) →
  **`bmad-code-review`** (Phase B, panier « qualité »).
- Rétrospective de fin d'epic (leçons, succès) → **`bmad-retrospective`**
  (recouvre la Phase A §7 « façon de travailler » à l'échelle epic).
- Changement de cap significatif en cours de sprint → **`bmad-correct-course`**.
- Revue humaine guidée d'un gros diff → **`bmad-checkpoint-preview`**.
- Perdu sur quel skill lancer → **`bmad-help`** (routeur BMAD).

`revue-increment` garde la main sur la boucle courte et transverse (vérité
terrain git, vérif réelle, docs de suivi, mémoire) ; il *appelle* les skills
ci-dessus plutôt que d'en dupliquer la logique.

## 1. Vérité terrain (avant tout le reste)

- [ ] `git status` + `git log --oneline -10` lus **maintenant** — l'état réel,
      pas ce que la roadmap/le wiki racontent (ils dérivent, cf.
      [[feedback-verify-before-trusting-roadmap]]).
- [ ] Le diff de la séance correspond exactement à ce qui était visé —
      rien d'orphelin, rien laissé à moitié, aucun fichier scratch/`_smoke.py`
      traîné dans le repo.
- [ ] Si un doc de suivi (roadmap, wiki, `CLAUDE.md`) affirme un état, il a
      été confronté au code — pas recopié de confiance.

## 2. Vérification réelle (pytest vert ≠ livré)

- [ ] `pytest -q` passe, et le compte de tests a **augmenté** si du
      comportement a été ajouté (sinon : pourquoi ?).
- [ ] Toute surface runtime touchée a été **exercée pour de vrai**, pas juste
      testée en unitaire :
  - écran / template / CSS / HTMX → skill `run-dev-server` (lancer +
    screenshot + regarder), cf. [[feedback-pptx-tests-need-a-real-render-check]]
    pour le principe « le parseur tolérant ment ».
  - export `.pptx` → skill `pptx-verify` (rendre + regarder), jamais « les
    tests passent donc c'est bon ».
- [ ] Les cas dégradés sont couverts (pas de clé IA, `mission.trame` absente,
      entrée vide, fichier corrompu) — ou explicitement documentés comme gap
      connu, pas silencieusement ignorés.

## 3. Cohérence de la matière produite

- [ ] Le code produit ressemble au code autour (densité de commentaires,
      nommage FR du domaine `trame`/`synthèse`/`verbatim`, style — pas de
      linter ici, c'est à l'œil).
- [ ] Pas de duplication d'un helper qui existait déjà (chercher avant
      d'écrire) ; pas de sur-ingénierie (abstraction pour un seul appelant).
- [ ] Couplages intentionnels tenus à jour ensemble (ex. si une constante de
      géométrie de slide change dans `pptx_export.py`, `FIELD_SHAPE` /
      `field_fit_hint()` suivent).
- [ ] Aucun chemin machine-spécifique ni secret glissé dans un fichier
      versionné (`settings.json`, code, docs).

## 4. Docs de suivi à jour (reflètent la réalité, pas l'intention)

- [ ] `.roadmap/roadmap.json` : US passées à `done`, sous-titre/compteurs
      recalés ; rendu via le skill `roadmap-keeper` si on veut le montrer.
- [ ] `CLAUDE.md` mis à jour **si** le pipeline/une convention a changé
      matériellement (pas pour un détail).
- [ ] `docs/wiki/` : seulement si dans le périmètre (souvent différé — le noter
      comme reste-à-faire plutôt que de laisser croire que c'est fait).

## 5. Capitalisation (mémoire)

- [ ] Toute friction, correction de cap, ou approche confirmée cette séance →
      un fichier mémoire `feedback-*` (avec **Pourquoi** + **Comment
      l'appliquer**), pas gardée en tête.
- [ ] Tout fait projet non dérivable du code/git (décision, contrainte,
      objectif) → mémoire `project`. Dates relatives converties en absolu.
- [ ] `MEMORY.md` : une ligne d'index ajoutée pour chaque nouveau fichier.
- [ ] Rien sauvegardé qui soit déjà dans le repo (structure, historique) ou
      seulement utile à cette conversation.

## 6. Supervision des agents (étage 2)

- [ ] Si le hook SessionStart a signalé « diagnostic agent-supervisor a lancer ou
      perime » (cadence 14 j), ou si l'incrément a beaucoup sollicité skills/sous-agents :
      lancer la skill **`agent-supervisor`** (diagnostic sur les données étage 1 —
      `state.json`, `routing-hints.json`, `runs.jsonl` — jamais les transcripts bruts),
      puis relancer le scan pour propager wiki + hints. Les constats sont restitués à
      l'utilisateur, qui arbitre.

## 7. Revue de la façon de travailler elle-même (le niveau méta)

Répondre honnêtement — c'est le cœur de cette revue, pas une formalité :

- [ ] **Angle mort évité ?** Ai-je vérifié avant d'agir (comme ici : une
      analyse « ces docs peuvent-ils fusionner » a révélé que `ONBOARDING.md`
      est piloté par un agent `.opencode` — le supprimer aurait cassé une
      convention). Où ai-je failli agir trop vite ?
- [ ] **Bon niveau d'effort ?** Ni bâclé (sauté une vérif réelle), ni
      sur-investi (agent lourd pour une tâche inline, abstraction prématurée).
- [ ] **Irréversible confirmé ?** Toute suppression/écrasement de fichier
      versionné a été soit explicitement demandée, soit proposée pour
      validation — jamais exécutée unilatéralement (cf. le refus classifier sur
      un `git rm` non demandé).
- [ ] **Une chose à changer la prochaine fois** — nommer un ajustement concret
      de méthode (un ordre d'étapes, une vérif à avancer, une question à poser
      plus tôt). S'il est durable → mémoire `feedback`.

## Phase B — Actions d'amélioration (agir, pas seulement constater)

Chaque constat rouge de la Phase A devient une action. Ne pas rendre la main
sur une simple liste de « à faire ».

1. **Trier** les constats en trois paniers :
   - *Correctifs sûrs et cadrés* (bug clair, nettoyage évident, doc à recaler)
     → appliquer maintenant.
   - *Qualité / simplification* → lancer les outils dédiés plutôt que le refaire
     à la main : `/code-review high --fix` pour les bugs, `/simplify` pour la
     réutilisation/altitude. Relire leurs changements avant de garder.
   - *Sensible ou irréversible* (suppression/écrasement de fichier versionné,
     écriture en base réelle `data/app.db`, action sortante) → **proposer**,
     ne pas exécuter unilatéralement (cf. les garde-fous classifier).
2. **Appliquer** les correctifs du premier panier + les sorties validées des
   outils du deuxième.
3. **Re-vérifier pour de vrai** ce qui a été touché — reboucler sur la Phase A
   §2 : `pytest`, plus rendu/exécution réels (`run-dev-server`, `pptx-verify`).
   Un correctif non re-vérifié n'est pas un correctif.
4. **Capitaliser** : docs de suivi recalées (Phase A §4), frictions en mémoire
   `feedback` (Phase A §5).
5. **Boucler** : si une amélioration a fait apparaître un nouveau constat,
   re-trier. Sortir quand il ne reste que des items du 3ᵉ panier (proposés à
   l'utilisateur) ou des gaps explicitement documentés.

## Verdict

Conclure par un bloc court et franc, sans enjoliver (cf. la consigne
« report outcomes faithfully ») :

```text
Revue incrément <n> — <titre>
Produit      : <livré & vérifié réellement | livré mais X non vérifié | partiel>
Améliorations: <ce qui a été appliqué + re-vérifié cette passe>
Suivi        : <roadmap/CLAUDE.md/mémoire à jour | écarts : ...>
Façon de bosser : <ce qui a marché ; l'ajustement retenu>
Reste        : <proposé à l'utilisateur / gaps connus, explicitement listés>
```

Si un item de la Phase A est rouge et n'a pas été corrigé en Phase B, il est
listé dans « Reste » (proposé ou documenté) — on ne déclare pas « fait » un
incrément avec une vérif réelle sautée ou un correctif évident non appliqué.
