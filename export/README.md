# Export — agents Superviseur & Orchestrateur

Bundle Markdown auto-suffisant des deux agents de pilotage du projet Interview-to-Deck,
destiné à être **repris** dans un autre projet. Ne contient que les fichiers `.md`
(définitions de skill, conception, catalogue, playbooks) — pas les scripts `.py`
ni les hooks (cf. « Dépendances non incluses » plus bas).

Exporté le 2026-07-20 depuis `c:\Users\claude.camus\Documents\VSCode2`.

## Contenu

```
export/
  agent-supervisor/
    SKILL.md          définition du skill (étage 2 — diagnostic LLM à la demande)
    conception.md     doc de conception (incréments A/B/C) — le « pourquoi »
  agent-orchestrator/
    SKILL.md          définition du skill (point d'entrée travail multi-étapes)
    conception.md     doc de conception (étages O-A/O-B/O-C) — le « pourquoi »
    catalogue.md      recommandations de routage (versionné)
    playbooks/
      FORMAT.md                    format déclaratif des playbooks
      dev-verifie.md               implémentation → tests → vérif réelle → revue
      revue-design-parallele.md    fan-out Explore ≤4 puis consolidation
      export-ppt-verifie.md        génération deck → vérif pptx obligatoire
      cycle-produit-bmad.md        cycle produit→dev BMAD (généré, statut jamais-joué)
```

## Comment reprendre dans un autre projet

1. **Skills** — copier `agent-supervisor/SKILL.md` et `agent-orchestrator/SKILL.md`
   respectivement dans `.claude/skills/agent-supervisor/` et
   `.claude/skills/agent-orchestrator/` du projet cible.
2. **Catalogue + playbooks** — copier `catalogue.md` dans `.claude/orchestration/`
   et le dossier `playbooks/` dans `.claude/orchestration/playbooks/`.
   Adapter le catalogue au périmètre du projet cible (les recommandations de routage
   citent des skills spécifiques à Interview-to-Deck).
3. **Conception** — `conception.md` sont des docs de référence (rationale, options
   écartées) ; les déposer dans `docs/reflexions/` du projet cible ou les garder ici
   comme documentation d'accompagnement.

## Dépendances NON incluses (scripts / hooks)

Ces `.md` sont la couche « connaissance/comportement ». Pour un transplant *exécutable*,
il faut aussi porter, depuis le projet source :

- **Superviseur (étage 1, collecte déterministe)** : `.claude/supervision/scan_transcripts.py`,
  `write_diagnostic.py`, les hooks `PostToolUse`/`SessionStart`, et les données machine
  (`usage.jsonl`, `state.json`, `diagnostic.json` — gitignorées).
- **Orchestrateur** : `.claude/orchestration/log_run.py`, `git_agents_inventory.py`,
  `generate_bmad_playbook.py`, le hook `UserPromptSubmit` (`orchestrator_gate.py`),
  et `routing-hints.json` (généré par le scan superviseur).

Sans ces scripts/hooks, les SKILL.md restent lisibles et applicables manuellement,
mais la boucle automatique (mesure d'usage → hints de routage → diagnostic) ne tourne pas.
