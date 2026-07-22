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
- [ ] **Exigence utilisateur = PERSISTANTE d'un tour au suivant** (plan
      d'amélioration 2026-07-22, après le numéro de chapitre écarté 3 fois). Un
      élément explicitement demandé (surtout visuel : numéro de chapitre, sommaire,
      encart…) reste dû tant qu'il n'est pas rendu et validé — il ne se périme pas
      parce que le tour a changé de sujet. **Une contrainte technique (placeholder
      trop étroit, glyphe qui « tofu », gabarit) est une raison de le RÉSOUDRE
      — le dessiner, contourner — JAMAIS de l'omettre en le justifiant.** Tenir une
      **checklist des éléments explicitement demandés** (les reporter d'un tour à
      l'autre) et vérifier leur PRÉSENCE au rendu réel à chaque itération. cf.
      [[feedback-persistent-user-request-draw-dont-omit]].
- [ ] Si un doc de suivi (roadmap, wiki, `CLAUDE.md`) affirme un état, il a
      été confronté au code — pas recopié de confiance.

## 2. Vérification réelle (pytest vert ≠ livré)

- [ ] **Rules R1-R4 du `CLAUDE.md` (§ « Rules — revue de code & couverture de tests »)
      respectées sur CE diff** : chaque bug corrigé porte son test de régression dans le
      même commit (R1) ; chaque comportement nouveau est exercé par un test (R2) ; une
      revue de code a eu lieu avant commit — adversariale au-dessus du seuil, `/code-review`
      ou relecture ligne à ligne dite explicitement en dessous (R3) ; chaque défaut visuel
      deck corrigé est verrouillé par un invariant dans `test_deck_qualite.py` (R4).
      Inscrites comme rules le 2026-07-22 (« il y a trop d'erreur ») — elles s'appliquent
      au moment d'écrire le code, cette checklist ne fait que les re-vérifier.
- [ ] `pytest -q` passe, et le compte de tests a **augmenté** si du comportement
      a été ajouté (sinon : pourquoi ?).
- [ ] **Verdict lu sur la ligne de synthèse *réelle* de `pytest`** (`N passed`,
      `0 failed`, aucune `error`) — jamais un résumé filtré du proxy `rtk`, ni un
      `[100%]` de sortie tronquée, ni l'exit code seul (bruit de teardown Windows,
      désormais neutralisé dans `tests/conftest.py`). En cas de doute, rediriger
      toute la sortie dans un fichier et la lire.
      cf. [[feedback-pytest-windows-teardown-noise]],
      [[feedback-rtk-pytest-false-no-tests-collected]], [[feedback-bash-tmp-path-and-encoding]].
- [ ] **Une suite verte qui *mocke* l'intégration modifiée prouve la logique, pas
      le comportement.** Chemin systématiquement monkeypatché (Ollama/Whisper,
      `extract_turns_from_text`, export réel) → exiger **un passage réel de bout en
      bout** avant « livré ».
- [ ] Toute surface runtime touchée **exercée pour de vrai**, pas seulement en
      unitaire :
  - écran / template / CSS / HTMX → `run-dev-server` (screenshot regardé).
  - export `.pptx` → `pptx-verify` (rendu regardé) — python-pptx est un parseur tolérant.
  - correctif **timeout/perf/modèle/prompt IA** → **trois** exigences, pas une
    mesure isolée (leçon du 2026-07-22, où une mesure en script — pas le flux réel —
    a fait déclarer « OK » un bug toujours vivant en usage) :
    1. exercer le **vrai flux UI** (POST la route réelle, ex. `record-libre`, pas
       seulement la fonction service en isolation) contre le **vrai** Ollama ;
    2. **asserter une sortie NON VIDE et correcte** sur un échantillon réaliste — un
       modèle local rend par intermittence 0 tour / du vide même quand il « répond »
       (bug 2026-07-22 : défaut llama3.1:8b = 0 tour ; la simple mesure de *temps* ne
       l'aurait pas vu) ; le **modèle défaut** doit être vérifié *utilisable* sur le
       matériel cible (pas seulement « configurable ») ;
    3. lancer **`pytest tests/test_ollama_integration.py`** (opt-in, vrai Ollama —
       le garde-fou exécutable qui assert tours>0 ; auto-skippé sans Ollama), 2-3 fois
       pour la fiabilité (non-déterminisme).
    Mesurer aussi à la taille **maximale réellement configurée** (`OLLAMA_CHUNK_MAX_WORDS`),
    pas un prompt jouet. cf. [[feedback-ai-timeout-fix-verify-at-configured-scale]],
    [[feedback-ai-real-e2e-and-nonempty-not-just-timing]].
- [ ] **Un rendu réel prouve « ça marche », pas « ça ne régressera pas ».** Fix
      frontend ou invariant de structure (quelle classe CSS, quel panneau rendu) →
      ajouter, **en plus** du screenshot, une assertion au niveau template dans
      `pytest` (GET `TestClient`, `assert '…' in response.text`) : durable là où le
      screenshot est ponctuel. Réutiliser un nom de classe CSS existant à sémantique
      d'affichage différente = collision silencieuse, à relever au diff.
      cf. [[feedback-frontend-render-check-plus-template-regression-test]],
      [[feedback-pptx-tests-need-real-render-check]].
- [ ] Cas dégradés couverts (pas de clé IA, `mission.trame` absente, entrée vide,
      fichier corrompu) — ou documentés comme gap connu, pas silencieusement ignorés.
- [ ] Correctif en réponse à une revue externe (`bmad-code-review`, sous-agent
      adversarial) → **relire le diff du correctif lui-même** avant commit : la revue
      d'origine n'a validé que le code *d'avant*.
      cf. [[feedback-adversarial-review-then-reself-review-fixes]].
- [ ] **Au-dessus du seuil bmad-code-review** (fidélité frontend, JS de concurrence,
      > 5 fichiers produit), **l'auto-relecture n'est PAS le gate** : ne jamais
      présenter « prêt à committer » sur la seule foi de la self-review + tests verts.
      La revue adversariale tranche ; en self-review, *lister les zones à risque à lui
      soumettre*, pas conclure « rien à corriger ». Constat superviseur 2026-07-22 :
      2 fois (2026-07-21 exec summary, 2026-07-22 bug répartition) un « rien à corriger »
      — une fois adossé à un harness Node 6/6 vert + pytest vert — a précédé la
      découverte par bmad-code-review de défauts réels, dont une **régression**.
      cf. [[feedback-self-review-weak-gate-vs-adversarial-review]].

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

## Leçons capitalisées (mémoire)

Les war-stories datées qui justifient les règles ci-dessus vivent dans les mémoires
`feedback-*` (recall global : `MEMORY.md`) — la checklist les **référence**, ne les
recopie pas (constat superviseur 2026-07-21 : SKILL.md accumulait les anecdotes) :

- [[feedback-pytest-windows-teardown-noise]] — exit 1 = bruit de teardown, compter les points.
- [[feedback-rtk-pytest-false-no-tests-collected]] — le proxy rtk a déjà mal reporté un run.
- [[feedback-ai-timeout-fix-verify-at-configured-scale]] — vérifier un correctif perf IA à la taille réellement configurée.
- [[feedback-adversarial-review-then-reself-review-fixes]] — relire le correctif issu d'une revue externe.
- [[feedback-pptx-tests-need-real-render-check]] — python-pptx est un parseur tolérant.
- [[feedback-frontend-render-check-plus-template-regression-test]] — un fix frontend exige aussi un test au niveau template.

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
