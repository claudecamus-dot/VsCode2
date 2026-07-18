---
updated: 2026-07-18
generated-by: .claude/supervision/scan_transcripts.py (superviseur d'agents, étage 1)
---

# Supervision des agents — tableau de bord d'usage

> ⚠️ **Page générée automatiquement** (hook SessionStart → `.claude/supervision/scan_transcripts.py`).
> **Ne pas éditer à la main** — toute modification serait écrasée au prochain scan.
> Conception et phasage : [../../reflexions/agent-superviseur.md](../../reflexions/agent-superviseur.md).

Dernier scan : 2026-07-18T12:46:25+02:00 · **20 sessions** (transcripts) · **33** invocations de skills · **31** lancements de sous-agents.

## Skills — usage réel

| Skill | Famille | Invocations | Première | Dernière |
| --- | --- | --- | --- | --- |
| `run-dev-server` | projet | 9 | 2026-07-03 | 2026-07-17 |
| `update-config` | (builtin/session) | 6 | 2026-07-03 | 2026-07-16 |
| `roadmap-keeper` | global | 5 | 2026-06-25 | 2026-07-15 |
| `run` | (builtin/session) | 3 | 2026-06-29 | 2026-07-03 |
| `pptx-deck` | global | 2 | 2026-07-02 | 2026-07-03 |
| `skill-creator` | global | 2 | 2026-07-03 | 2026-07-03 |
| `agent-orchestrator` | projet | 1 | 2026-07-17 | 2026-07-17 |
| `agent-supervisor` | projet | 1 | 2026-07-18 | 2026-07-18 |
| `claude-api` | (builtin/session) | 1 | 2026-06-29 | 2026-06-29 |
| `init` | (builtin/session) | 1 | 2026-07-03 | 2026-07-03 |
| `pptx-verify` | global | 1 | 2026-07-03 | 2026-07-03 |
| `revue-increment` | projet | 1 | 2026-07-18 | 2026-07-18 |

## Sous-agents

| Sous-agent | Lancements | Premier | Dernier |
| --- | --- | --- | --- |
| `Explore` | 15 | 2026-06-30 | 2026-07-18 |
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

1. **Trier les skills BMAD** : 39 installés, 0 invocation à ce jour — décider lesquels garder, customiser ou désinstaller.
2. **Skills projet sans usage** : `pptx-framed-image`, `slide-text-polish` — vérifier pertinence et déclencheurs.

## Diagnostic qualitatif (étage 2 — `agent-supervisor`)

_Diagnostic à jour._

1. **Changement PPT livré sans passage pptx-verify depuis le 2026-07-03** — Au prochain changement pptx_export.py/pptx_deck.py, suivre l'étape pptx-verify du playbook dev-verifie sur un export réel

---

_Étage O-C (croisement modèle × tâche × reprises, exploitation de `runs.jsonl`) : voir `.claude/orchestration/routing-hints.json`, régénéré à chaque session._
