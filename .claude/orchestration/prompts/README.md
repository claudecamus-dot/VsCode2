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
- Every template carries the mandatory clauses of `agent-orchestrator` SKILL.md § 2 ter
  (FAITS TRANSMIS, CIBLE ÉNUMÉRÉE, SendMessage `main` affordance, UNCERTAINTY, QUALITY
  CRITERIA, PROVENANCE, foreground-only, marked mutants, structured end block).
  `tests/test_prompt_templates.py` fails if one is lost.
- Improve a template HERE (a commit = a revision) when a run shows a brief gap, instead of
  patching the scratchpad copy. No evaluation set exists yet for these prompts: their
  quality is not measured, only their clauses.
- Kit source: after editing, `py .claude/dispositif/export_agentic.py` then `--check`.
