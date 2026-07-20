# Catalogue des agents — routage orchestrateur (v1, incrément O-A)

> Utilisé par la skill `agent-orchestrator` pour composer ses plans. Descriptions et
> recommandations maintenues à la main ; les **statuts d'usage vivants** (invocations,
> dates, jamais-utilisés) sont dans `routing-hints.json` (généré à chaque session par le
> scan superviseur, avec les stats plan-vs-réel de `runs.jsonl`) et, en version lisible,
> dans `docs/wiki/technical/agents-supervision.md` — toujours les vérifier avant de router
> vers un agent « jamais utilisé ». Statuts ci-dessous : instantané du 2026-07-18.
> Les décisions humaines qui closent un constat d'usage (skill conservée malgré
> zéro invocation, tri exécuté) sont dans `.claude/supervision/arbitrages.json`.
> Si **aucune entrée ne couvre le besoin** : inventaire git présents + supprimés via
> `py .claude/orchestration/git_agents_inventory.py`, puis proposition de
> restauration/évolution/création (procédure dans la skill, étape 2).
> Conception : `docs/reflexions/agent-orchestrateur.md`.

## Skills projet

| Skill | Quand l'utiliser | Mode typique | Modèle | Statut |
| --- | --- | --- | --- | --- |
| `run-dev-server` | Lancer/screenshoter l'app, vérifier un changement UI réel | Synchrone | (session) | Éprouvé (×9) |
| `revue-increment` | Definition-of-done : fin d'incrément, avant commit | Synchrone, étape terminale obligatoire des plans de dev | (session) | Jamais invoquée — à réhabiliter via l'orchestrateur |
| `pptx-framed-image` | Remplir les cadres photo d'un template PPT — étape conditionnelle du playbook `export-ppt-verifie` | Synchrone | (session) | Jamais utilisée — conservée (arbitrage 2026-07-18) |
| `slide-text-polish` | Lint de la qualité rédactionnelle des slides — étape conditionnelle du playbook `export-ppt-verifie` | Synchrone | (session) | Jamais utilisée — conservée (arbitrage 2026-07-18) |
| `agent-orchestrator` | Point d'entrée des demandes multi-étapes/multi-agents (routé par le hook UserPromptSubmit) | Synchrone | (session) | Éprouvé |
| `agent-supervisor` | Diagnostic qualitatif des agents (étage 2) — depuis `revue-increment` ou sur signal SessionStart | Synchrone, ≤ 1×/14 j | (session) | Éprouvé (1er diagnostic 2026-07-18) |

## Skills globaux clés

| Skill | Quand l'utiliser | Mode typique | Modèle | Statut |
| --- | --- | --- | --- | --- |
| `roadmap-keeper` | Mettre à jour/rendre la roadmap | Synchrone | (session) | Éprouvé (×5) |
| `pptx-deck` / `pptx-verify` | Générer un deck / le vérifier en rendu réel — colonne vertébrale du playbook `export-ppt-verifie` | Synchrone, toujours en paire | (session) | Éprouvés |
| `restitution-deck-design` | Deck techniquement correct mais visuellement pauvre — étape conditionnelle du playbook `export-ppt-verifie` | Synchrone | (session) | Jamais utilisée — conservée (arbitrage 2026-07-18) |
| `code-review` / `verify` / `simplify` | Revue du diff / vérification bout-en-bout / nettoyage | Synchrone, fin de plan de dev | (session) | Builtins |

## Sous-agents (seuls à accepter un choix de modèle)

| Sous-agent | Quand l'utiliser | Mode typique | Modèle conseillé | Statut |
| --- | --- | --- | --- | --- |
| `Explore` | Recherche large en lecture seule, conclusion sans les dumps | Parallèle (fan-out ≤4) ou async | Haiku/Sonnet (mécanique/standard) | Éprouvé (×12) |
| `Plan` | Concevoir une stratégie d'implémentation | Synchrone | Opus/Fable (structurant) | Éprouvé (×3) |
| `general-purpose` | Tâche multi-étapes déléguée, sortie volumineuse | Async ou synchrone | Sonnet ; Opus/Fable si structurant | Éprouvé (×7) |
| `claude-code-guide` | Questions sur Claude Code / SDK / API | Synchrone | (défaut) | Utilisé (×1) |

## Familles sous condition

| Famille | Règle de routage |
| --- | --- |
| **BMAD (39 skills après tri exécuté le 2026-07-18)** | Tri ré-arbitré après vérification pré-suppression par fan-out (`docs/reflexions/tri-skills-bmad.md`, addendum) : 7 retirés (4 dépréciés + `quick-dev`/`spec`/`qa-generate-e2e-tests`), 39 conservés dont les dépendances de `revue-increment` (y compris `bmad-review-*` invoqués par `bmad-code-review`) et le cycle produit. Ne router que sur demande explicite de l'utilisateur, en passant par `bmad-help`. |
| **OpenHub (`.opencode/`)** | Hors périmètre O-A (décision n°4 de la réflexion) — ne pas router. |

> Angle mort de mesure (constat superviseur du 2026-07-20) : les sous-skills
> invoquées par un sous-agent via un prompt en langage naturel (« Invoke the X
> skill on this diff », pattern utilisé par `bmad-code-review` pour lancer
> `bmad-review-adversarial-general`/`bmad-review-edge-case-hunter`) n'apparaissent
> pas dans `state.json`/`routing-hints.json` — seules les invocations directes de
> la session principale sont tracquées. Une absence de trace sur ces sous-skills
> ne signifie donc pas absence d'exécution : ne pas les qualifier `agent-mort`
> sur cette seule base.

## Playbooks (incrément O-B)

Workflows récurrents pré-composés — la skill cherche un playbook matchant **avant** de
composer à vide. Format : `.claude/orchestration/playbooks/FORMAT.md`.

| Playbook | Quand | Source | Statut |
| --- | --- | --- | --- |
| `dev-verifie` | Dev/correction : tests + vérif réelle (conditionnelle aux fichiers touchés) + `revue-increment` avant commit | Manuel | Éprouvé |
| `export-ppt-verifie` | Livrable = le deck : génération (`pptx-deck`) + enrichissements conditionnels (`pptx-framed-image`, `slide-text-polish`, `restitution-deck-design`) + `pptx-verify` obligatoire + `revue-increment` | Manuel | Éprouvé (colonne vertébrale) — étapes conditionnelles jamais jouées |
| `revue-design-parallele` | Revue multi-angles en fan-out d'`Explore` (≤4) puis consolidation — pattern US9.12 | Manuel | Éprouvé |
| `cycle-produit-bmad` | Cycle produit BMAD (brief→PRD→archi→epics→dev→review), clos par `revue-increment` | `generate_bmad_playbook.py` (regénérer, ne pas éditer) | Jamais joué — sur demande explicite |
