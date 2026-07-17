# Catalogue des agents — routage orchestrateur (v1, incrément O-A)

> Utilisé par la skill `agent-orchestrator` pour composer ses plans. Descriptions et
> recommandations maintenues à la main ; les **statuts d'usage vivants** (invocations,
> dates, jamais-utilisés) sont dans la page générée par le superviseur :
> `docs/wiki/technical/agents-supervision.md` — toujours la vérifier avant de router vers
> un agent « jamais utilisé ». Statuts ci-dessous : instantané du 2026-07-17.
> Conception : `docs/reflexions/agent-orchestrateur.md`.

## Skills projet

| Skill | Quand l'utiliser | Mode typique | Modèle | Statut |
| --- | --- | --- | --- | --- |
| `run-dev-server` | Lancer/screenshoter l'app, vérifier un changement UI réel | Synchrone | (session) | Éprouvé (×9) |
| `revue-increment` | Definition-of-done : fin d'incrément, avant commit | Synchrone, étape terminale obligatoire des plans de dev | (session) | Jamais invoquée — à réhabiliter via l'orchestrateur |
| `pptx-framed-image` | Remplir les cadres photo d'un template PPT | Synchrone | (session) | Jamais utilisée |
| `slide-text-polish` | Lint de la qualité rédactionnelle des slides | Synchrone | (session) | Jamais utilisée |

## Skills globaux clés

| Skill | Quand l'utiliser | Mode typique | Modèle | Statut |
| --- | --- | --- | --- | --- |
| `roadmap-keeper` | Mettre à jour/rendre la roadmap | Synchrone | (session) | Éprouvé (×5) |
| `pptx-deck` / `pptx-verify` | Générer un deck / le vérifier en rendu réel | Synchrone, toujours en paire | (session) | Éprouvés |
| `restitution-deck-design` | Deck techniquement correct mais visuellement pauvre | Synchrone | (session) | Jamais utilisée |
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
| **BMAD (35 skills après tri arbitré le 2026-07-17)** | Tri arbitré (`docs/reflexions/tri-skills-bmad.md`) : 11 retirés (4 dépréciés + 7 redondants dev/qualité), 35 conservés dont les 5 dépendances de `revue-increment` et le cycle produit (futur playbook O-B). Ne router que sur demande explicite de l'utilisateur, en passant par `bmad-help`. |
| **OpenHub (`.opencode/`)** | Hors périmètre O-A (décision n°4 de la réflexion) — ne pas router. |

## Playbooks (incrément O-B)

Workflows récurrents pré-composés — la skill cherche un playbook matchant **avant** de
composer à vide. Format : `.claude/orchestration/playbooks/FORMAT.md`.

| Playbook | Quand | Source | Statut |
| --- | --- | --- | --- |
| `dev-verifie` | Dev/correction : tests + vérif réelle (conditionnelle aux fichiers touchés) + `revue-increment` avant commit | Manuel | Éprouvé |
| `revue-design-parallele` | Revue multi-angles en fan-out d'`Explore` (≤4) puis consolidation — pattern US9.12 | Manuel | Éprouvé |
| `cycle-produit-bmad` | Cycle produit BMAD (brief→PRD→archi→epics→dev→review), clos par `revue-increment` | `generate_bmad_playbook.py` (regénérer, ne pas éditer) | Jamais joué — sur demande explicite |
