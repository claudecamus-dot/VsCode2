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
  **`bmad-code-review`** (Phase B, panier « qualité ») — **obligatoire** si le diff
  touche plus de 5 fichiers produit (`app/`, hors tests/docs) ou une logique à risque
  (suppression/écrasement de données, authentification, export irréversible) ; **et
  toujours, quel que soit le nombre de fichiers, pour toute modification du JS de
  concurrence de `record_libre.html` / `record.html`** (MediaRecorder, timers de
  rotation, compteurs `pendingX`, gardes de ré-entrance/génération) — ce fichier a un
  historique de 7 bugs data-loss trouvés par revue adversariale (Palier 2 + bugs du
  2026-07-20) que l'auto-relecture avait laissés passer ; le risque y est **par
  fichier** (concurrence), pas au nombre de fichiers. En dessous de ces seuils, la revue
  inline de la Phase A suffit. Seuil > 5 fichiers ajouté le 2026-07-19 après constat du
  superviseur (étage 2) ; volet « JS d'enregistrement » ajouté le 2026-07-21 (même
  source, données run 20/22/23 : une modif de concurrence à 1 fichier était passée en
  auto-relecture seule sous le seuil).
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
- [ ] **Conformité à la demande, exigence par exigence.** Relister les points
      *explicites* de la demande initiale (chaque point numéroté, chaque
      contrainte — la demande, pas son interprétation) et cocher chacun contre le
      diff réel. Un test vert prouve que le code fait ce que le *test* attend, pas
      ce que l'*utilisateur* a demandé. Toute exigence réinterprétée, partiellement
      traitée ou écartée est dite dans « Reste », jamais laissée silencieusement
      non traitée — « livré » ne se déclare pas sur une demande à moitié couverte.
- [ ] Si un doc de suivi (roadmap, wiki, `CLAUDE.md`) affirme un état, il a
      été confronté au code — pas recopié de confiance.

## 2. Vérification réelle (pytest vert ≠ livré)

- [ ] `pytest -q` passe, et le compte de tests a **augmenté** si du
      comportement a été ajouté (sinon : pourquoi ?).
- [ ] **Le verdict pass/fail se lit sur la sortie *réelle* de `pytest`, jamais
      sur un résumé.** Confirmer `N passed` / `0 failed` / aucune `error` sur la
      ligne de synthèse elle-même — pas sur un résumé filtré du proxy `rtk` (qui
      a déjà mal reporté un run, cf.
      [[feedback-rtk-pytest-false-no-tests-collected]]), ni sur un `[100%]` de
      fin de sortie **tronquée** (le `FAILED`/`ERROR` sort en queue). En cas de
      doute : relancer via `rtk proxy pytest` (sans filtrage) ou rediriger toute
      la sortie dans un fichier et la lire (cf.
      [[feedback-bash-tmp-path-and-encoding]] pour la capture sur Windows). « OK »
      annoncé n'est pas « OK » prouvé — un `pytest` KO présenté comme vert est
      l'échec le plus coûteux (il ferme la vérification au lieu de l'ouvrir).
- [ ] **Une suite verte qui *mocke* l'intégration modifiée prouve la logique,
      pas le comportement.** Si le correctif touche un chemin systématiquement
      monkeypatché dans les tests (appels Ollama/Whisper, `extract_turns_from_text`,
      export réel), le vert ne couvre que les branches — exiger au moins **un
      passage réel de bout en bout** avant de déclarer livré (déjà la règle pour
      l'IA-timeout et le PPT ci-dessous ; ici généralisée à tout mock de
      l'intégration sous correctif).
- [ ] Toute surface runtime touchée a été **exercée pour de vrai**, pas juste
      testée en unitaire :
  - écran / template / CSS / HTMX → skill `run-dev-server` (lancer +
    screenshot + regarder), cf. [[feedback-pptx-tests-need-a-real-render-check]]
    pour le principe « le parseur tolérant ment ».
  - export `.pptx` → skill `pptx-verify` (rendre + regarder), jamais « les
    tests passent donc c'est bon ».
  - correctif de **timeout/perf IA** (Ollama ou autre) → mesurer au moins un
    appel à la taille **maximale réellement configurée** (ex.
    `OLLAMA_CHUNK_MAX_WORDS` au max, pas un prompt jouet de quelques mots) —
    un appel rapide sur un petit échantillon prouve seulement que le modèle
    répond, pas qu'il répond à temps à l'échelle de production. Leçon du
    2026-07-19 : un premier correctif de timeout Ollama vérifié sur un petit
    prompt (21s, chaud) avait conclu à tort que la chaleur du modèle
    suffisait ; à la taille réellement configurée (1800 mots), le même appel
    chaud prenait 572s — quasiment le double du timeout. Voir
    [[feedback-ai-timeout-fix-verify-at-configured-scale]].
- [ ] Les cas dégradés sont couverts (pas de clé IA, `mission.trame` absente,
      entrée vide, fichier corrompu) — ou explicitement documentés comme gap
      connu, pas silencieusement ignorés.
- [ ] Correctif appliqué en réponse à une revue externe (`bmad-code-review`,
      sous-agent adversarial) → **relire le diff du correctif lui-même** avant
      commit, pas seulement re-vérifier les points signalés — la revue
      d'origine n'a validé QUE le code *avant* correctif, jamais le correctif.
      Leçon du 2026-07-20 (Palier 2, entretien segmenté) : `bmad-code-review`
      (2 sous-agents indépendants) avait trouvé 3 bugs réels ; en les
      corrigeant, une auto-relecture avant commit — pas les sous-agents, qui
      n'avaient vu que le code d'avant — a trouvé un 4ᵉ bug introduit par le
      correctif lui-même (un job frère laissé de côté, perte silencieuse de
      contenu). Voir [[feedback-adversarial-review-then-reself-review-fixes]].

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
- [ ] Si `docs/wiki/technical/agents-supervision.md` ou `docs/wiki.html`
      apparaissent modifiés dans `git status` (régénérés par le hook
      SessionStart pendant la séance), les inclure dans le commit de fin
      d'incrément plutôt que les laisser dériver localement — constat du
      2026-07-20 : ces fichiers étaient restés modifiés sans être committés
      pendant 4 commits consécutifs, faute d'étape dédiée.

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
- [ ] **Demande vérifiée avant d'agir ?** Si la demande s'appuie sur des
      données ou un état qui ne correspondent à rien de réel (« traite le
      point 2 » alors que le diagnostic n'a qu'un point), l'avoir constaté et
      **clarifié** avant de coder, pas fabriqué une interprétation plausible.
      Leçon du 2026-07-19 (run 12) : une telle demande, clarifiée d'abord, a
      révélé un vrai bug d'affichage — deviner aurait produit du hors-sujet.
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
