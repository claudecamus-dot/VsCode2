---
name: agent-orchestrator
description: Orchestrateur des agents et skills du projet — qualifie une demande de travail, compose un plan (cascade / parallèle / asynchrone, modèle par étape), l'exécute en s'appuyant sur le catalogue et les données du superviseur, puis journalise le run. À charger quand une demande de travail implique plusieurs étapes dépendantes, plusieurs agents/skills, ou des vérifications obligatoires — ou quand la grille injectée par le hook UserPromptSubmit route ici.
---

# Agent orchestrateur (étages O-A + O-B + O-C)

Conception : `docs/reflexions/agent-orchestrateur.md`. Données de routage :
`.claude/orchestration/catalogue.md` (recommandations),
`.claude/orchestration/routing-hints.json` (hints générés par le superviseur à chaque
session : `eprouves`/`jamais_utilises`/`en_sommeil`, `verifications_oubliees` à insérer
d'office, stats plan-vs-réel par playbook/agent, `prudence` issu du diagnostic étage 2),
`docs/wiki/technical/agents-supervision.md` (tableau de bord humain des mêmes données) et
`.claude/orchestration/playbooks/` (workflows récurrents — format dans `playbooks/FORMAT.md`).

## Méthode — 5 étapes

### 1. Qualifier (silencieux, jamais mentionné à l'utilisateur si exécution directe)

- **Exécution directe** (pas d'orchestration, pas de journal) : une seule étape, un seul
  agent/skill évident, micro-tâche, question, correction en cours de tâche.
- **Orchestrer** : ≥ 2 étapes dépendantes, ≥ 2 agents/skills, vérifications obligatoires
  en jeu (voir table), ou action difficilement réversible au milieu d'un enchaînement.

### 2. Composer le plan

**D'abord, chercher un playbook.** Si la demande matche les `declencheurs` d'un playbook
de `.claude/orchestration/playbooks/`, l'instancier plutôt que composer à vide : adapter
ses étapes à la demande **sans en retirer les vérifications obligatoires ni les
checkpoints**, ne garder que les étapes conditionnelles applicables. Playbooks actuels :

| Playbook | Pour | Statut |
| --- | --- | --- |
| `dev-verifie` | Implémentation/correction avec tests + vérif réelle + revue-increment avant commit | Éprouvé |
| `export-ppt-verifie` | Livrable = le deck de restitution : génération + enrichissements conditionnels (cadres photo, polish, design) + `pptx-verify` obligatoire | Éprouvé (colonne vertébrale) |
| `revue-design-parallele` | Revue multi-angles d'un livrable en fan-out puis consolidation | Éprouvé |
| `cycle-produit-bmad` | Cycle produit BMAD complet (généré depuis le CSV) — **sur demande explicite uniquement** | Jamais joué |

Sinon composition libre depuis le catalogue + `routing-hints.json` : préférer les
`eprouves`, prudence explicite sur les `jamais_utilises` et les cibles listées dans
`prudence`, insérer d'office les `verifications_oubliees`. Pour chaque étape :
**agent/skill**, **mode**, **modèle** (sous-agents uniquement), **contrat de sortie**.
Suivre le plan avec TodoWrite. Règle de mode — *la dépendance de données décide* :

| Mode | Quand | Garde-fous |
| --- | --- | --- |
| Synchrone (cascade) | L'étape suivante a besoin du résultat | Contrat de sortie vérifié avant de continuer |
| Parallèle (fan-out) | Étapes indépendantes en lecture/analyse | ≤ 4 sous-agents, jamais d'écritures concurrentes sur les mêmes fichiers, consolidation obligatoire |
| Asynchrone (arrière-plan) | Long, autonome, non bloquant | Attendre la notification — ne JAMAIS anticiper/fabriquer le résultat ; 1 seul chantier async lourd à la fois |
| Irréversible (commit, suppression, publication) | — | Toujours synchrone + confirmation utilisateur, hooks/permissions jamais contournés |

**Aucun agent/skill ne couvre le besoin ?** Ne pas improviser sans le signaler — escalade
en trois temps, dans cet ordre :

1. **Mémoire git** : `py .claude/orchestration/git_agents_inventory.py` inventorie tous
   les agents/skills que git connaît — **présents et supprimés** (un agent adapté a pu
   être retiré lors d'un nettoyage, ex. les 26 agents `openhub_clone` supprimés le
   2026-07-16). `--json` pour la version structurée.
2. **Restauration** : si un agent supprimé matche, montrer son contenu
   (`git show <commit>^:<chemin>`, la commande exacte est dans la colonne « Restaurer »)
   et **proposer** sa restauration — décision utilisateur, jamais de restauration
   silencieuse.
3. **Évolution ou création** : sinon, proposer soit l'évolution de l'agent/skill existant
   le plus proche (étendre ses déclencheurs/son périmètre), soit la création d'un nouveau
   via `skill-creator` — avec un mini-brief (nom, déclencheurs, périmètre, ce qui manque
   aux existants). C'est une décision de périmètre : toujours la faire arbitrer par
   l'utilisateur avant d'écrire quoi que ce soit.

Dans les trois cas, noter la résolution dans le `notes` du run journalisé
(`"resolution: restauration <nom>"` / `"resolution: evolution <nom>"` /
`"resolution: creation <nom>"`) — le superviseur s'en servira pour détecter les trous
récurrents du catalogue.

### 3. Valider

Présenter le plan à l'utilisateur **seulement si** : > 3 sous-agents, coût manifestement
élevé, ou étape irréversible/hors périmètre de la demande. Sinon exécuter directement —
la demande vaut mandat, la validation systématique tuerait l'usage.

### 4. Exécuter

Après chaque étape, vérifier son **contrat de sortie** (artefact attendu présent, test
vert, vérification réelle faite). Échec → **une** relance ciblée, puis escalade à
l'utilisateur avec l'état réel. Vérifications obligatoires à insérer d'office dans les
plans (leçons payées du projet — mémoires `feedback_*`) :

| Si le plan touche… | Alors le plan contient… |
| --- | --- |
| Template Jinja / CSS / JS | Screenshot via `run-dev-server` (pas seulement pytest) |
| `pptx_export.py` / `pptx_deck.py` | `pptx-verify` (rendu réel — python-pptx est un parseur tolérant) |
| **Livrable consommé par l'utilisateur** (deck exporté, écran) | Produire l'**artefact EXACT qu'il ouvre** (export réel de la route de l'app — `GET …/export/pptx` — **pas** un `build_presentation` maison), le rendre **ENTIER** (toutes les slides, pas 2-3), et le faire **VALIDER par l'utilisateur** avant tout « fait » (évol 2026-07-22, boucle deck non convergente — `feedback-verify-the-real-app-export-all-slides`) |
| Fin d'incrément / avant commit | `revue-increment` en étape terminale |
| Exploration volumineuse | Sous-agent `Explore`, jamais la session principale |
| Skills BMAD | Uniquement sur demande explicite, via `bmad-help` (statut « à trier ») |

**Règle de non-convergence (évol 2026-07-22).** Si le MÊME livrable est rejeté par
l'utilisateur **≥ 3 tours** (« toujours KO », « pas traité »), la boucle ne converge pas :
**STOP l'itération à l'aveugle** — ne pas re-deviner le défaut. Reproduire l'artefact
utilisateur exact (§ ligne ci-dessus) ET **demander à l'utilisateur de pointer le défaut
précis** (numéro de slide, capture, écran) avant de retoucher quoi que ce soit. Re-deviner
produit l'oscillation (ex. barre d'accent ajoutée puis retirée, numéro navy-block puis
pill) ; l'oracle, c'est l'utilisateur sur SON artefact.

### 5. Journaliser

À la fin du run (succès **ou** échec), une ligne dans `.claude/orchestration/runs.jsonl` :

```bash
py .claude/orchestration/log_run.py '{"demande": "résumé court", "qualification": "orchestre", "playbook": "dev-verifie", "plan": [{"etape": "revue design", "agent": "Explore", "mode": "parallele", "modele": "haiku"}], "resultat": "succes", "reprises": 0, "notes": ""}'
```

(JSON aussi accepté sur stdin. `qualification` : `orchestre` | `direct-signale` ;
`resultat` (issue **discriminante** — pas un `succes` réflexe, constat superviseur
2026-07-21 « 29/29 succes ne porte aucun signal ») : `succes` = livrable produit ET
toutes les exigences explicites de la demande couvertes ET vérifications obligatoires
faites **ET, pour un livrable consommé par l'utilisateur, validé PAR l'utilisateur sur
l'artefact exact** ; `en-attente-validation` = livrable design-intent produit et
auto-vérifié mais **pas encore validé par l'utilisateur** — état par défaut d'un livrable
utilisateur tant que le « OK » n'est pas donné (évol 2026-07-22 : ne JAMAIS logger `succes`
sur une auto-évaluation d'un livrable que l'utilisateur doit approuver) ; `partiel` = au
moins une exigence non livrée, une vérification obligatoire sautée, OU une escalade non
résolue à la remise (commit/PR bloqué renvoyé à l'utilisateur) ; `echec` = objectif non
atteint / run abandonné ; `playbook` : nom du playbook instancié ou `null` en composition libre. Les exécutions directes ne se journalisent pas — le journal
trace les orchestrations, pas la conversation.)

## Politique de modèle (sous-agents uniquement)

La session principale — donc les skills inline — reste sur le modèle choisi par
l'utilisateur : l'orchestrateur peut **proposer** une bascule (`/model`), jamais l'imposer.

| Modèle | Pour | Exemple |
| --- | --- | --- |
| Haiku | Fan-out mécanique : recherches simples, extraction, inventaires | 4 × Explore sur des questions factuelles |
| Sonnet | Défaut dev : exploration de code, implémentation standard, revue ciblée | general-purpose sur une feature bornée |
| Opus / Fable | Structurant : architecture, plan complexe, revue adversariale, arbitrage | Plan, revue de conception |

Arbitrage par défaut (décision n°6) : qualité d'abord sur le structurant, économe sur le
fan-out — le superviseur croisera modèle × tâche × reprises pour ajuster poste par poste.
