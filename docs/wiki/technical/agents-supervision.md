---
updated: 2026-07-20
generated-by: .claude/supervision/scan_transcripts.py (superviseur d'agents, étage 1)
---

# Supervision des agents — tableau de bord d'usage

> ⚠️ **Page générée automatiquement** (hook SessionStart → `.claude/supervision/scan_transcripts.py`).
> **Ne pas éditer à la main** — toute modification serait écrasée au prochain scan.
> Conception et phasage : [../../reflexions/agent-superviseur.md](../../reflexions/agent-superviseur.md).

Dernier scan : 2026-07-20T21:27:37+02:00 · **28 sessions** (transcripts) · **53** invocations de skills · **36** lancements de sous-agents.

## Skills — usage réel

| Skill | Famille | Invocations | Première | Dernière |
| --- | --- | --- | --- | --- |
| `run-dev-server` | projet | 15 | 2026-07-03 | 2026-07-20 |
| `agent-orchestrator` | projet | 6 | 2026-07-17 | 2026-07-20 |
| `update-config` | (builtin/session) | 6 | 2026-07-03 | 2026-07-16 |
| `agent-supervisor` | projet | 5 | 2026-07-18 | 2026-07-20 |
| `roadmap-keeper` | global | 5 | 2026-06-25 | 2026-07-15 |
| `revue-increment` | projet | 4 | 2026-07-18 | 2026-07-20 |
| `run` | (builtin/session) | 3 | 2026-06-29 | 2026-07-03 |
| `pptx-deck` | global | 2 | 2026-07-02 | 2026-07-03 |
| `pptx-verify` | global | 2 | 2026-07-03 | 2026-07-18 |
| `skill-creator` | global | 2 | 2026-07-03 | 2026-07-03 |
| `bmad-code-review` | BMAD | 1 | 2026-07-20 | 2026-07-20 |
| `claude-api` | (builtin/session) | 1 | 2026-06-29 | 2026-06-29 |
| `init` | (builtin/session) | 1 | 2026-07-03 | 2026-07-03 |

## Sous-agents

| Sous-agent | Lancements | Premier | Dernier |
| --- | --- | --- | --- |
| `Explore` | 18 | 2026-06-30 | 2026-07-18 |
| `general-purpose` | 10 | 2026-07-15 | 2026-07-20 |
| `claude` | 4 | 2026-07-16 | 2026-07-16 |
| `Plan` | 3 | 2026-07-06 | 2026-07-17 |
| `claude-code-guide` | 1 | 2026-07-03 | 2026-07-03 |

## Jamais utilisés

**projet** — 2/6 jamais invoqués :

`pptx-framed-image`, `slide-text-polish`

**BMAD** — 38/39 jamais invoqués :

<details><summary>Voir la liste</summary>

`bmad-advanced-elicitation`, `bmad-agent-analyst`, `bmad-agent-architect`, `bmad-agent-dev`, `bmad-agent-pm`, `bmad-agent-tech-writer`, `bmad-agent-ux-designer`, `bmad-architecture`, `bmad-brainstorming`, `bmad-check-implementation-readiness`, `bmad-checkpoint-preview`, `bmad-correct-course`, `bmad-create-epics-and-stories`, `bmad-create-story`, `bmad-customize`, `bmad-dev-auto`, `bmad-dev-story`, `bmad-document-project`, `bmad-domain-research`, `bmad-editorial-review-prose`, `bmad-editorial-review-structure`, `bmad-forge-idea`, `bmad-generate-project-context`, `bmad-help`, `bmad-index-docs`, `bmad-market-research`, `bmad-party-mode`, `bmad-prd`, `bmad-prfaq`, `bmad-product-brief`, `bmad-retrospective`, `bmad-review-adversarial-general`, `bmad-review-edge-case-hunter`, `bmad-shard-doc`, `bmad-sprint-planning`, `bmad-sprint-status`, `bmad-technical-research`, `bmad-ux`

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
- **`ai_common.py`** (2026-07-19) : Proposition retenue telle quelle : règle explicite ajoutée à revue-increment/SKILL.md §2 (correctif timeout/perf IA -> mesurer à la taille maximale réellement configurée, pas un prompt jouet) — capitalisée en mémoire feedback-ai-timeout-fix-verify-at-configured-scale.
- **`revue-increment`** (2026-07-20) : Proposition retenue telle quelle : règle ajoutée à revue-increment/SKILL.md §2 — un correctif appliqué en réponse à une revue externe (bmad-code-review, sous-agent adversarial) doit être relu lui-même avant commit, la revue d'origine n'ayant validé que le code d'avant. Confirmé le jour même : un 4e bug a été trouvé ainsi (Palier 2), non vu par les 2 sous-agents de revue.
- **`revue-increment`** (2026-07-20) : Proposition retenue telle quelle : règle ajoutée à revue-increment/SKILL.md §4 — si les docs auto-générées de supervision (agents-supervision.md, wiki.html) apparaissent modifiées en git status en fin de séance, les inclure dans le commit plutôt que les laisser dériver (constaté : 4 commits consécutifs sans les inclure).
- **`bmad-code-review`** (2026-07-20) : Proposition retenue telle quelle : angle mort de mesure documenté dans catalogue.md — les sous-skills bmad-review-adversarial-general/bmad-review-edge-case-hunter, invoquées par des sous-agents via prompt naturel, n'apparaissent pas dans state.json ; absence de trace n'implique pas absence d'exécution.
- **`revue-design-parallele`** (2026-07-20) : Proposition ① du diagnostic 2026-07-20 retenue et appliquée : garde déterministe ajoutée au contrat de l'étape consolidation (playbook revue-design-parallele) — quand le fan-out sert à une énumération exhaustive AVANT suppression/renommage, un grep -r final de chaque identifiant retiré est obligatoire et prime sur les rapports des sous-agents (extraits, non exhaustifs). Preuve : réf bmad-spec (bmad-architecture:29) ratée par un fan-out de 3 Explore, rattrapée seulement au grep final avant un git rm (runs 2026-07-18).
- **`revue-increment`** (2026-07-20) : Règle définie sur demande (analyse superviseur 2026-07-20, mode d'échec « tests indiqués OK alors qu'ils sont KO ») : revue-increment/SKILL.md §2 + contrat 'tests' du playbook dev-verifie — le verdict pass/fail se lit sur la ligne de synthèse RÉELLE de pytest, jamais sur un résumé filtré du proxy rtk (déjà mal reporté un run, mémoire feedback-rtk-pytest-false-no-tests-collected) ni un [100%] de sortie tronquée ; et une suite verte qui MOCKE l'intégration modifiée (ex. extract_turns_from_text, appels Ollama/Whisper) exige au moins un passage réel de bout en bout avant « livré ».
- **`revue-increment`** (2026-07-20) : Règle définie sur demande (analyse superviseur 2026-07-20, mode d'échec « dev avec bug ou ne respectant pas la demande ») : revue-increment/SKILL.md §1 (conformité exigence par exigence — chaque point explicite de la demande coché contre le diff, toute exigence écartée dite dans « Reste ») + §7 (demande référençant un état introuvable -> clarifier avant de coder, run 12) + contrat 'implementation' du playbook dev-verifie. Le volet « bug malgré tests verts » reste couvert par le seuil bmad-code-review (>5 fichiers produit / logique à risque) et la relecture du correctif lui-même (arbitrages 2026-07-19/20).

## Diagnostic qualitatif (étage 2 — `agent-supervisor`)

_Diagnostic à jour._

1. **Le playbook export-ppt-verifie -- seule justification arbitree de conserver 4 skills PPT a zero invocation -- n a jamais ete joue, sa premisse reste non exercee** — Ce n est PAS un signal agent-mort (playbook jeune, aucune tache PPT depuis) et la conservation des skills est deja arbitree -- ne pas re-litiguer cette decision. Mais l arbitrage a cree une dependance sur un mecanisme jamais exerce : la premiere tache d export PPT est le seul moment qui validera (ou non) que ces skills gagnent leur place via ce playbook. · **Proposition** : Noter dans catalogue.md a l entree export-ppt-verifie : c est la route obligatoire de TOUTE tache d export PPT (ne pas composer un plan PPT ad hoc) ; tant que le playbook reste jamais-joue, traiter la conservation des 3 skills PPT comme non verifiee -- si une tache PPT est un jour traitee sans passer par ce playbook, rouvrir les 4 arbitrages du 2026-07-18 avec cette donnee nouvelle.

---

_Étage O-C (croisement modèle × tâche × reprises, exploitation de `runs.jsonl`) : voir `.claude/orchestration/routing-hints.json`, régénéré à chaque session._
