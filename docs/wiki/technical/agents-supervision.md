---
updated: 2026-07-17
generated-by: .claude/supervision/scan_transcripts.py (superviseur d'agents, étage 1)
---

# Supervision des agents — tableau de bord d'usage

> ⚠️ **Page générée automatiquement** (hook SessionStart → `.claude/supervision/scan_transcripts.py`).
> **Ne pas éditer à la main** — toute modification serait écrasée au prochain scan.
> Conception et phasage : [../../reflexions/agent-superviseur.md](../../reflexions/agent-superviseur.md).

Dernier scan : 2026-07-17T22:11:35+02:00 · **18 sessions** (transcripts) · **30** invocations de skills · **28** lancements de sous-agents.

## Skills — usage réel

| Skill | Famille | Invocations | Première | Dernière |
| --- | --- | --- | --- | --- |
| `run-dev-server` | projet | 9 | 2026-07-03 | 2026-07-17 |
| `update-config` | (builtin/session) | 6 | 2026-07-03 | 2026-07-16 |
| `roadmap-keeper` | global | 5 | 2026-06-25 | 2026-07-15 |
| `run` | (builtin/session) | 3 | 2026-06-29 | 2026-07-03 |
| `pptx-deck` | global | 2 | 2026-07-02 | 2026-07-03 |
| `skill-creator` | global | 2 | 2026-07-03 | 2026-07-03 |
| `claude-api` | (builtin/session) | 1 | 2026-06-29 | 2026-06-29 |
| `init` | (builtin/session) | 1 | 2026-07-03 | 2026-07-03 |
| `pptx-verify` | global | 1 | 2026-07-03 | 2026-07-03 |

## Sous-agents

| Sous-agent | Lancements | Premier | Dernier |
| --- | --- | --- | --- |
| `Explore` | 12 | 2026-06-30 | 2026-07-17 |
| `general-purpose` | 8 | 2026-07-15 | 2026-07-17 |
| `claude` | 4 | 2026-07-16 | 2026-07-16 |
| `Plan` | 3 | 2026-07-06 | 2026-07-17 |
| `claude-code-guide` | 1 | 2026-07-03 | 2026-07-03 |

## Jamais utilisés

**projet** — 4/5 jamais invoqués :

`agent-orchestrator`, `pptx-framed-image`, `revue-increment`, `slide-text-polish`

**BMAD** — 46/46 jamais invoqués :

<details><summary>Voir la liste</summary>

`bmad-advanced-elicitation`, `bmad-agent-analyst`, `bmad-agent-architect`, `bmad-agent-dev`, `bmad-agent-pm`, `bmad-agent-tech-writer`, `bmad-agent-ux-designer`, `bmad-architecture`, `bmad-brainstorming`, `bmad-check-implementation-readiness`, `bmad-checkpoint-preview`, `bmad-code-review`, `bmad-correct-course`, `bmad-create-architecture`, `bmad-create-epics-and-stories`, `bmad-create-prd`, `bmad-create-story`, `bmad-customize`, `bmad-dev-auto`, `bmad-dev-story`, `bmad-document-project`, `bmad-domain-research`, `bmad-edit-prd`, `bmad-editorial-review-prose`, `bmad-editorial-review-structure`, `bmad-forge-idea`, `bmad-generate-project-context`, `bmad-help`, `bmad-index-docs`, `bmad-market-research`, `bmad-party-mode`, `bmad-prd`, `bmad-prfaq`, `bmad-product-brief`, `bmad-qa-generate-e2e-tests`, `bmad-quick-dev`, `bmad-retrospective`, `bmad-review-adversarial-general`, `bmad-review-edge-case-hunter`, `bmad-shard-doc`, `bmad-spec`, `bmad-sprint-planning`, `bmad-sprint-status`, `bmad-technical-research`, `bmad-ux`, `bmad-validate-prd`

</details>

**global** — 1/5 jamais invoqués :

`restitution-deck-design`

## TODO agents (constats automatiques)

1. **Trier les skills BMAD** : 46 installés, 0 invocation à ce jour — décider lesquels garder, customiser ou désinstaller.
2. **`revue-increment` jamais invoquée** malgré le rappel SessionStart à chaque session — revoir son déclencheur (l'ancrer au flux de commit ?) ou la simplifier.
3. **Skills projet sans usage** : `agent-orchestrator`, `pptx-framed-image`, `slide-text-polish` — vérifier pertinence et déclencheurs.

---

_Étage 2 (diagnostic qualitatif LLM : KO répétés, efficacité, challenge des agents) : incrément B, pas encore construit — voir la réflexion._
