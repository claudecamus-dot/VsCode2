# Sub-agent brief templates (versioned prompts)

The hub's sub-agent briefs ARE its prompts. Until 2026-09-29 they were retyped by hand in a
temp scratchpad for every run (`salles7_commun.md`, `lot2_bascule_brief.md`,
`correctifs_hub_commun.md`...) and lost at session end — never compared, never improved.
These templates were extracted from those real briefs; the orchestrator starts from one,
fills the `{{placeholders}}`, and writes only the task-specific part by hand.

| Template | When |
| --- | --- |
| `parallel-common.md` | common rules file shared by N executors running at the same time in one repo |
| `executor-lot.md` | one executor lot (implement + test red/green + mutant + scoped commit) |
| `review-room.md` | read-only review / deliberation room (changes no file) |
| `fleet-propagation.md` | apply a hub change to ONE fleet repo (evolution-flotte playbook) |
| `user-simulation.md` | `utilisateur-produit` walk-through of a fleet product |

Rules:
- The `gabarit` id journaled in `runs.jsonl` (field `gabarit`, read by `optimiseur.py`) is the
  template's file basename without `.md` (e.g. `executor-lot`), same as the `GABARIT :` line.
- Every template carries the mandatory clauses of `agent-orchestrator` SKILL.md § 2 ter
  (FAITS TRANSMIS, CIBLE ÉNUMÉRÉE, SendMessage `main` affordance, UNCERTAINTY, QUALITY
  CRITERIA, PROVENANCE, foreground-only, marked mutants, structured end block).
  `tests/test_prompt_templates.py` fails if one is lost. Each template also carries a stable
  line `GABARIT : <file name without .md>` right after its title: the hook journals it
  (`taille_briefs.jsonl`, field `gabarit`) and the scan counts general-purpose briefs with
  neither a gabarit nor a size (warning only). Keep the line when instantiating a template.
- `{{budget_minutes}}` / `{{writing_minutes}}` (review-room): take the numbers from the budget table of
  `agent-orchestrator/SKILL.md` § 2 ter « BUDGET » (7 / 18 / 46 min = 2 x measured class median,
  x3 at 20+ concurrent agents), never below 7 min in total; no template carries a literal budget.
- `{{max_tool_calls}}`: the size ceiling of the brief, in tool calls (line
  `TAILLE : ≤ {{max_tool_calls}} appels d'outils ; au-delà, rendre un partiel`). Provisional
  default 16 (p90 of 25 delegations, usage.jsonl 2026-10-02); `review-room.md` declares
  `BUDGET :` instead. `guard_brief_source_primaire.py` warns (never blocks) when a
  general-purpose brief has neither line (anywhere in the brief, after a separator).
- Veille R1 R3 R4 (adopted 2026-10-02). R1 stable prefix first: no placeholder on the first
  line, stable clauses before `## This run`. Indicator: identical leading characters between
  two successive briefs of one template; template-only proxy (chars before the first `{{`)
  measured 2026-10-02 (`C:/tmp/lr_prefix.py`, find("{{")) before → after: executor-lot 2 → 1484, parallel-common 2 → 2086,
  review-room 16 → 988, fleet-propagation 22 → 1646, user-simulation 32 → 1216.
- `{{effort}}` (R4, executor-lot/parallel-common): fait simple | comparaison | recherche
  complexe (Anthropic multi-agent research blog — lab blog, juge et partie). Indicator:
  run duration vs p90 10.9 min (`convergence.py --historique`). `{{base_sha}}`: commit
  of local main the orchestrator's `git worktree add` started from.
- `{{output_path}}` (R3, review-room): optional out-of-repo file for a bulky report; the room
  returns ≤ 600 tokens + the path. Indicator: tokens returned per room, 5 rooms before/after.
- NON-REGRESSION and MUTATION PROOF (executor-lot, adopted 2026-10-03, neural test 20 agents):
  replay every suite found by `grep -rl "<basename>" tests/`; every "corrigé/testé/couvert"
  claim quotes a seen-red mutation on a copy. Indicator fixed before: share of executor
  reports with a quoted seen-red mutation (0/5 Sonnet standard, 4/5 improved brief) and
  suites replayed (0/5 vs 5/5). Locked by `tests/test_prompt_templates.py`.
- Improve a template HERE (a commit = a revision) when a run shows a brief gap, instead of
  patching the scratchpad copy. No evaluation set exists yet for these prompts: their
  quality is not measured, only their clauses.
- Kit source: after editing, `py .claude/dispositif/export_agentic.py` then `--check`.
