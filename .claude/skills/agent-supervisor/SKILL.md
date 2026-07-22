---
name: agent-supervisor
description: Superviseur des agents, étage 2 (diagnostic LLM) — analyse les données déterministes de l'étage 1 (usage, runs d'orchestration, signaux git/mémoire), qualifie les KO répétés, l'inefficacité, les agents morts et les vérifications manquantes, challenge les agents avec des propositions de changement concrètes (champ proposition — l'humain arbitre, jamais auto-appliqué), puis écrit diagnostic.json (fusionné dans le wiki et routing-hints.json par le scan). À lancer depuis revue-increment, ou quand le hook SessionStart signale « diagnostic agent-supervisor a lancer ou perime » (cadence 14 j ou 3 orchestrations non couvertes).
---

# Superviseur d'agents — étage 2 (diagnostic qualitatif)

Conception : `docs/reflexions/agent-superviseur.md`. L'étage 1 (scan déterministe, 0 token)
mesure ; cet étage **qualifie** — et l'humain tranche. Sortie unique :
`.claude/supervision/diagnostic.json`, écrit via le script validé (jamais à la main).

## Règles absolues

- **Jamais les JSONL bruts** : ne pas ouvrir `~/.claude/projects/.../*.jsonl` ni
  `usage.jsonl` en lecture intégrale — l'étage 1 les a déjà agrégés. Les transcripts
  contiennent du contenu d'interviews clients : analyse strictement locale.
- **Pas de constat sans preuve** : chaque finding porte une `preuve` objective (comptage,
  erreur, reprise, correction utilisateur, revert git). Un ressenti n'est pas un
  diagnostic — c'est le garde-fou anti-auto-complaisance (le LLM évalue des actions
  produites par le même modèle).
- **5 constats max**, priorisés — un rapport que personne ne lit rejoint les skills
  jamais utilisés.
- Le diagnostic **propose**, il n'applique rien : pas de désinstallation, pas de
  modification de skill, pas d'édition du wiki (généré par le scan).

## Méthode — 4 lectures ciblées, puis écrire

### 1. Lire les données étage 1 (agrégats, pas de volumineux)

- `.claude/supervision/state.json` — compteurs/dates par skill et sous-agent.
- `.claude/orchestration/routing-hints.json` — éprouvés, jamais-utilisés, en-sommeil,
  vérifications oubliées, stats plan-vs-réel par playbook/agent.
- `.claude/orchestration/runs.jsonl` — court (une ligne par orchestration) : lire les
  runs récents, regarder `resultat`, `reprises`, `notes` (dont `resolution:` — trous du
  catalogue).

### 2. Croiser avec les signaux hors étage 1

- `git log --oneline -30` : séries de fix sur le même fichier, reverts, commits
  « corrige/retire » rapprochés = reprises d'actions KO.
- `MEMORY.md` (l'index seulement) : les leçons `feedback_*` déjà capitalisées — ne pas
  re-diagnostiquer ce qui y est déjà, citer la mémoire comme preuve si un constat la
  confirme sur données récentes.
- La table des playbooks (`.claude/orchestration/playbooks/` + catalogue) : statut
  `jamais-joue` ancien = candidat playbook mort.

### 3. Qualifier — les questions à poser aux données

| Catégorie | Question | Exemple de preuve |
| --- | --- | --- |
| `ko-repete` | Quel agent/skill échoue ou est repris plusieurs fois ? | `runs.jsonl` : reprises ≥ 2 sur la même étape ; série de commits fix |
| `inefficacite` | Où le coût dépasse-t-il la valeur (mauvais modèle, fan-out inutile, skill inline pour tâche lourde) ? | run `partiel` avec 4 sous-agents pour une sortie non utilisée |
| `agent-mort` | Quoi désinstaller/mettre en sommeil (jamais utilisé ou > 30 j malgré rappels) ? | `jamais_utilises` + date d'install ; `en_sommeil` |
| `interaction` | Quel enchaînement échoue entre agents (sortie de N inutilisable par N+1) ? | même étape relancée dans plusieurs runs ; `resolution:` récurrente |
| `verification-manquante` | Quelle vérification réelle est systématiquement sautée ? | `verifications_oubliees` ; commit touchant `pptx_export.py` sans passage `pptx-verify` |
| `non-convergence` (évol 2026-07-22) | Un même livrable est-il **rejeté à répétition** par l'utilisateur sans converger ? | même playbook/livrable rejoué ≥ 3 tours + corrections « toujours KO / pas traité » ; série de fix + un **revert** sur le même fichier. **Constat CRITIQUE** — proposition type : passer en **mode acceptance** (l'utilisateur est l'oracle, sur l'artefact EXACT qu'il ouvre ; demander le défaut précis) au lieu d'un énième correctif deviné. La cause est un dispositif où le même modèle évalue ce qu'il produit — l'auto-validation ne remplace pas la validation utilisateur. |

Ne retenir que ce qui est **actionnable** (une recommandation concrète par constat) et
**pas déjà couvert** par un TODO déterministe du scan (ex. « trier BMAD » y est déjà —
inutile de le dupliquer, sauf pour le préciser).

### 3 bis. Challenger (incrément C) — du constat à la proposition concrète

Pour chaque constat qui le justifie, ajouter un champ `proposition` : **le changement
précis** qu'un humain peut accepter ou refuser d'un coup d'œil — pas « améliorer X »
mais le diff d'intention : nouveau `description`/déclencheur d'une skill (via
`skill-creator`, ou `bmad-customize` pour les BMAD), étape/contrat de playbook à amender,
skill à désinstaller ou mettre en sommeil, brief type à imposer à l'entrée d'un
sous-agent relancé plusieurs fois. Sources : les signaux d'interaction du scan
(`prudence` déterministe = échecs répétés en orchestration ; `trous_catalogue` =
résolutions ad hoc récurrentes) et les stats plan-vs-réel par playbook.

**Gouvernance stricte** : le superviseur *propose* (la `proposition` part dans le wiki
avec le constat), l'humain *arbitre*, l'orchestrateur *applique* la version validée —
jamais d'auto-modification, même « évidente ».

### 4. Écrire le diagnostic, puis propager

```bash
py .claude/supervision/write_diagnostic.py '{"findings": [{"categorie": "ko-repete", "cible": "pptx-verify", "priorite": 3, "titre": "…", "preuve": "…", "recommandation": "…"}]}'
```

(JSON aussi accepté sur stdin. `cible` sur `ko-repete`/`inefficacite` alimente la liste
`prudence` de routing-hints — l'orchestrateur route avec prudence explicite sur ces
cibles.) Puis relancer le scan pour propager wiki + hints :

```bash
py .claude/supervision/scan_transcripts.py
```

Enfin, restituer à l'utilisateur les constats en une ligne chacun avec leur preuve —
c'est lui qui arbitre les suites (désinstaller, customiser via `skill-creator`/
`bmad-customize`, amender un playbook). Une leçon durable sur la façon de travailler →
mémoire `feedback_*` (cf. `revue-increment` §5).
