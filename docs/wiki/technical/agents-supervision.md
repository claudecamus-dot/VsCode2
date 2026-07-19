---
updated: 2026-07-19
generated-by: .claude/supervision/scan_transcripts.py (superviseur d'agents, étage 1)
---

# Supervision des agents — tableau de bord d'usage

> ⚠️ **Page générée automatiquement** (hook SessionStart → `.claude/supervision/scan_transcripts.py`).
> **Ne pas éditer à la main** — toute modification serait écrasée au prochain scan.
> Conception et phasage : [../../reflexions/agent-superviseur.md](../../reflexions/agent-superviseur.md).

Dernier scan : 2026-07-19T14:15:45+02:00 · **21 sessions** (transcripts) · **43** invocations de skills · **34** lancements de sous-agents.

## Skills — usage réel

| Skill | Famille | Invocations | Première | Dernière |
| --- | --- | --- | --- | --- |
| `run-dev-server` | projet | 12 | 2026-07-03 | 2026-07-19 |
| `update-config` | (builtin/session) | 6 | 2026-07-03 | 2026-07-16 |
| `roadmap-keeper` | global | 5 | 2026-06-25 | 2026-07-15 |
| `agent-orchestrator` | projet | 4 | 2026-07-17 | 2026-07-18 |
| `revue-increment` | projet | 3 | 2026-07-18 | 2026-07-19 |
| `run` | (builtin/session) | 3 | 2026-06-29 | 2026-07-03 |
| `agent-supervisor` | projet | 2 | 2026-07-18 | 2026-07-19 |
| `pptx-deck` | global | 2 | 2026-07-02 | 2026-07-03 |
| `pptx-verify` | global | 2 | 2026-07-03 | 2026-07-18 |
| `skill-creator` | global | 2 | 2026-07-03 | 2026-07-03 |
| `claude-api` | (builtin/session) | 1 | 2026-06-29 | 2026-06-29 |
| `init` | (builtin/session) | 1 | 2026-07-03 | 2026-07-03 |

## Sous-agents

| Sous-agent | Lancements | Premier | Dernier |
| --- | --- | --- | --- |
| `Explore` | 18 | 2026-06-30 | 2026-07-18 |
| `general-purpose` | 8 | 2026-07-15 | 2026-07-17 |
| `claude` | 4 | 2026-07-16 | 2026-07-16 |
| `Plan` | 3 | 2026-07-06 | 2026-07-17 |
| `claude-code-guide` | 1 | 2026-07-03 | 2026-07-03 |

## Jamais utilisés

**projet** — 2/6 jamais invoqués :

`pptx-framed-image`, `slide-text-polish`

**BMAD** — 39/39 jamais invoqués :

<details><summary>Voir la liste</summary>

`bmad-advanced-elicitation`, `bmad-agent-analyst`, `bmad-agent-architect`, `bmad-agent-dev`, `bmad-agent-pm`, `bmad-agent-tech-writer`, `bmad-agent-ux-designer`, `bmad-architecture`, `bmad-brainstorming`, `bmad-check-implementation-readiness`, `bmad-checkpoint-preview`, `bmad-code-review`, `bmad-correct-course`, `bmad-create-epics-and-stories`, `bmad-create-story`, `bmad-customize`, `bmad-dev-auto`, `bmad-dev-story`, `bmad-document-project`, `bmad-domain-research`, `bmad-editorial-review-prose`, `bmad-editorial-review-structure`, `bmad-forge-idea`, `bmad-generate-project-context`, `bmad-help`, `bmad-index-docs`, `bmad-market-research`, `bmad-party-mode`, `bmad-prd`, `bmad-prfaq`, `bmad-product-brief`, `bmad-retrospective`, `bmad-review-adversarial-general`, `bmad-review-edge-case-hunter`, `bmad-shard-doc`, `bmad-sprint-planning`, `bmad-sprint-status`, `bmad-technical-research`, `bmad-ux`

</details>

**global** — 1/5 jamais invoqués :

`restitution-deck-design`

## TODO agents (constats automatiques)

_(aucun constat — rien à signaler sur les données actuelles)_

## Arbitrages enregistrés

_Constats clos par décision humaine (`.claude/supervision/arbitrages.json`) — l'usage réel reste mesuré ci-dessus._

- **`famille:BMAD`** (2026-07-18) : Tri exécuté : 7 skills retirés, 39 conservés (routage sur demande explicite via bmad-help) — docs/reflexions/tri-skills-bmad.md, commit f604c39.
- **`pptx-framed-image`** (2026-07-18) : Conservée malgré zéro invocation — reliée au playbook export-ppt-verifie (étape conditionnelle cadres-photo).
- **`slide-text-polish`** (2026-07-18) : Conservée malgré zéro invocation — reliée au playbook export-ppt-verifie (étape conditionnelle polish-texte).
- **`restitution-deck-design`** (2026-07-18) : Conservée malgré zéro invocation — reliée au playbook export-ppt-verifie (étape conditionnelle design-review).
- **`bmad-code-review`** (2026-07-19) : Proposition retenue telle quelle : seuil explicite ajouté à revue-increment/SKILL.md (>5 fichiers produit ou logique à risque -> bmad-code-review obligatoire, sinon revue inline) — la délégation implicite ne se déclenchait jamais en pratique.

## Diagnostic qualitatif (étage 2 — `agent-supervisor`)

_Diagnostic à jour — rien à signaler, tous les constats précédents ont été arbitrés._

---

_Étage O-C (croisement modèle × tâche × reprises, exploitation de `runs.jsonl`) : voir `.claude/orchestration/routing-hints.json`, régénéré à chaque session._
