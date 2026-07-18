# Tri des skills BMAD — proposition d'arbitrage

> Statut : **arbitré le 2026-07-17** — l'utilisateur retient le tri recommandé **amendé** :
> conserver aussi personas/animation, recherche/idéation et docs/édition. Décision finale :
> **retirer 11** (4 dépréciés + 7 « dev/qualité redondants »), **conserver 35**.
> **Exécution en attente** : la suppression des dossiers n'a pas pu être appliquée dans la
> session du 2026-07-17 (shell bloqué par un incident hook/cwd, voir mémoire
> `feedback_hook_relative_paths_cwd_deadlock`). Commande à exécuter (racine du projet) :
> `rm -rf .claude/skills/bmad-create-prd .claude/skills/bmad-edit-prd .claude/skills/bmad-validate-prd .claude/skills/bmad-create-architecture .claude/skills/bmad-quick-dev .claude/skills/bmad-spec .claude/skills/bmad-ux .claude/skills/bmad-check-implementation-readiness .claude/skills/bmad-qa-generate-e2e-tests .claude/skills/bmad-review-adversarial-general .claude/skills/bmad-review-edge-case-hunter`
> puis `py .claude/supervision/scan_transcripts.py` pour rafraîchir les pages générées.
> Données : `docs/wiki/technical/agents-supervision.md` (usage), vérifications du 2026-07-17.
> ⚠️ **Addendum 2026-07-18 — NE PAS exécuter cette commande telle quelle** : voir la
> vérification pré-suppression ci-dessous, la liste des 11 doit être ré-arbitrée.

## Addendum 2026-07-18 — vérification pré-suppression (invalide partiellement l'arbitrage)

Vérification par fan-out orchestré (3 × sous-agent `Explore`/haiku en parallèle : périmètre
`.claude/`+tests, contenu des 35 skills gardés, `_bmad/_config`+docs — première délégation
réelle de l'orchestrateur, journalisée dans `runs.jsonl`). Constats :

**La prémisse du groupe « dev/qualité redondants » est fausse sur 2 skills** :
`bmad-code-review` n'« embarque » pas les couches adversariale et edge-case — il **invoque**
les skills standalone (`steps/step-02-review.md:19,24` → `bmad-review-adversarial-general`,
`bmad-review-edge-case-hunter`), tout comme `bmad-dev-auto` (`step-04-review.md:28,32`).
Ces 2 skills sont donc des dépendances transitives de `revue-increment` (via
`bmad-code-review`) : les supprimer casserait la definition-of-done.

**Autres références bloquantes depuis des skills conservés** :

- `bmad-check-implementation-readiness` : codé en dur dans
  `generate_bmad_playbook.py:30` (checkpoint du playbook `cycle-produit-bmad` — le CSV
  est propre, mais le générateur le réinjecterait), et dans les menus
  `customize.toml` des personas gardées `bmad-agent-pm:70` / `bmad-agent-architect:65`.
- `bmad-ux` : prérequis lu par `bmad-create-epics-and-stories`
  (`steps/step-01-validate-prerequisites.md:69,73,147`) et cœur du menu de la persona
  gardée `bmad-agent-ux-designer` (`customize.toml:60`) — garder la persona UX sans son
  workflow serait incohérent.
- `bmad-spec` : proposé par `bmad-architecture` (`SKILL.md:75`).
- `bmad-quick-dev` / `bmad-qa-generate-e2e-tests` : menus de `bmad-agent-dev`
  (`customize.toml:65,70`) — recalage de 2 lignes si suppression.

**Requalifiés non bloquants** : les références internes des 11 à eux-mêmes
(`customize.toml` de `bmad-edit-prd`/`bmad-validate-prd`), les noms utilisés comme simples
chaînes dans les tests (`test_agent_supervision.py` — donnée fabriquée ;
`test_agent_orchestration.py` — assertion d'*absence* de `bmad-ux` du playbook),
`bmad-help.csv` (aucun `preceded-by`/`required` d'un gardé vers les 11), et les compteurs
« 46 » des pages générées (recalés automatiquement au prochain scan).

**Proposition de ré-arbitrage — retirer 7 au lieu de 11** :

- Retirer : les 4 dépréciés (`bmad-create-prd`, `bmad-edit-prd`, `bmad-validate-prd`,
  `bmad-create-architecture`) + `bmad-quick-dev`, `bmad-qa-generate-e2e-tests`,
  `bmad-spec` — avec 3 recalages d'une ligne (`bmad-agent-dev/customize.toml:65,70`,
  `bmad-architecture/SKILL.md:75`).
- **Conserver** (réintégrés au tri) : `bmad-review-adversarial-general`,
  `bmad-review-edge-case-hunter` (dépendances de `bmad-code-review`/`bmad-dev-auto`),
  `bmad-check-implementation-readiness` (checkpoint du cycle produit),
  `bmad-ux` (cohérence avec la persona UX et les prérequis epics conservés).

Décision à arbitrer avant toute suppression.

## Faits

- **46 skills BMAD installés le 2026-07-16, 0 invocation** sur tout l'historique (18 sessions
  scannées) — y compris depuis l'installation.
- `_bmad-output/planning-artifacts/` et `implementation-artifacts/` sont **vides** (créés par
  l'installeur) : le cycle produit BMAD n'a jamais tourné. Les deux docs de cadrage présents
  à la racine (`cadrage-*transcription-perf.md`, 2026-07-16 au soir) ont été produits par des
  sous-agents ordinaires qui ont utilisé le dossier comme emplacement de sortie — pas par un
  workflow BMAD.
- **Coût de l'inaction** : chaque skill installé injecte sa description dans le contexte de
  chaque session (~35 tokens × 46 ≈ 1,6k tokens par tour, facturés en permanence — cf.
  discipline tokens du CLAUDE.md).
- **Dépendances dures** : la skill projet `revue-increment` délègue à 5 skills BMAD
  (`bmad-help`, `bmad-code-review`, `bmad-retrospective`, `bmad-correct-course`,
  `bmad-checkpoint-preview`) — intouchables tant que `revue-increment` existe.
- Réversibilité totale : les dossiers `.claude/skills/bmad-*/` sont versionnés (git) et
  réinstallables via l'installeur BMAD — « désinstaller » = supprimer le dossier, récupérable
  en une commande.

## Proposition (recommandée) — garder 15, retirer 31

**A. Retrait d'office — 4 skills dépréciés par BMAD lui-même** (leur propre description
annonce leur suppression en v7) : `bmad-create-prd`, `bmad-edit-prd`, `bmad-validate-prd`,
`bmad-create-architecture`.

**B. Garder — 15 :**

| Groupe | Skills | Pourquoi |
| --- | --- | --- |
| Dépendances `revue-increment` (5) | `bmad-help`, `bmad-code-review`, `bmad-retrospective`, `bmad-correct-course`, `bmad-checkpoint-preview` | Cassent la definition-of-done du projet si retirés |
| Cycle produit minimal (8) | `bmad-product-brief`, `bmad-prd`, `bmad-architecture`, `bmad-create-epics-and-stories`, `bmad-create-story`, `bmad-dev-story`, `bmad-sprint-planning`, `bmad-sprint-status` | La chaîne brief→PRD→archi→stories→dev que le playbook `cycle-produit-bmad` (incrément O-B) orchestrera — l'orchestrateur est précisément ce qui peut enfin les rendre utiles |
| Outillage du maintien (2) | `bmad-customize` (amender les skills gardés), `bmad-dev-auto` (brique asynchrone pour l'orchestrateur) | Support des deux chantiers en cours |

**C. Retirer (mise en sommeil) — 27 :**

- *Personas et animation (7)* : `bmad-agent-analyst`, `-architect`, `-dev`, `-pm`,
  `-tech-writer`, `-ux-designer`, `bmad-party-mode` — redondants avec les workflows gardés
  (les personas ne sont que des enrobages conversationnels des mêmes capacités).
- *Recherche / idéation (7)* : `bmad-brainstorming`, `bmad-domain-research`,
  `bmad-market-research`, `bmad-technical-research`, `bmad-forge-idea`, `bmad-prfaq`,
  `bmad-advanced-elicitation` — outil interne à périmètre connu, pas d'étude de marché ni
  d'idéation produit en vue.
- *Docs / édition (6)* : `bmad-document-project`, `bmad-generate-project-context`,
  `bmad-index-docs`, `bmad-shard-doc`, `bmad-editorial-review-prose`,
  `bmad-editorial-review-structure` — le wiki + CLAUDE.md + la mémoire couvrent le besoin.
- *Dev / qualité redondants (7)* : `bmad-quick-dev`, `bmad-spec`, `bmad-ux`,
  `bmad-check-implementation-readiness`, `bmad-qa-generate-e2e-tests`,
  `bmad-review-adversarial-general`, `bmad-review-edge-case-hunter` — couverts par les
  builtins (`/code-review`, `/verify`, `/simplify`), les tests pytest existants et
  `bmad-code-review` (qui embarque déjà les couches adversariale et edge-case).

**Gain attendu** : ~1,1k tokens de contexte par tour libérés, un catalogue de routage
lisible (15 skills BMAD ciblés au lieu de 46), zéro perte de capacité réellement utilisée
(0 invocation sur les 31 retirés — sur les 46, d'ailleurs).

**Limite connue** : le catalogue `_bmad/_config/bmad-help.csv` continuera de lister les
skills retirés — `bmad-help` peut donc en recommander un absent. Acceptable (message d'échec
clair, réinstallation en une commande) ; à purger du CSV si ça gêne à l'usage.

## Alternatives

1. **Retrait minimal** : seulement les 4 dépréciés (A) — gain marginal, catalogue toujours
   illisible.
2. **Statu quo** : tout garder — 1,6k tokens/tour pour 0 usage, le constat du superviseur
   reste ouvert indéfiniment.
3. **Radical** : tout retirer sauf les 5 dépendances de `revue-increment` — abandonne le
   playbook cycle produit d'O-B avant de l'avoir essayé.
