# Format de playbook (incrément O-B)

Un playbook = un fichier `*.md` de ce dossier décrivant un workflow récurrent de façon
déclarative. La partie machine est un bloc ` ```json ` unique (parsé par la skill
`agent-orchestrator`) ; le reste du fichier est de la prose libre (contexte, précédents,
limites). Référence : `.claude/skills/agent-orchestrator/SKILL.md` § 2 (composition du
plan). **Le hub qui publie ce document n'a lui-même aucun test qui verrouille ce
format** — vérifié le 2026-08-31. Un dépôt qui l'importe peut avoir le sien (ex.
`tests/test_agent_orchestration.py` chez VSCode2 et VSCode3) : vérifier dans CE dépôt
avant de supposer que le bloc JSON n'est protégé que par la relecture (finding
`flotte:formulations-du-hub-publiees-telles-quelles-aux-cibles`, 2026-09-11 — un texte
écrit du point de vue du hub devient faux une fois lu ailleurs).

## Champs du bloc JSON

| Champ | Valeurs | Rôle |
| --- | --- | --- |
| `nom` | slug = nom du fichier sans `.md` | Identité du playbook |
| `description` | texte court | Ce que le workflow accomplit |
| `statut` | `eprouve` \| `jamais-joue` | Garde-fou « playbooks morts » : `eprouve` exige au moins une exécution réelle réussie du workflow (précédent cité dans la prose) ; un `jamais-joue` se propose avec prudence explicite |
| `source` | `manuel` \| `genere:<script>` | Un playbook `genere:*` ne s'édite jamais à la main — modifier le script et regénérer |
| `declencheurs` | liste de textes | Indices de matching pour l'étape « Composer » de la skill |
| `etapes` | liste ordonnée (voir ci-dessous) | Le plan lui-même |
| `regle_reprise` | texte | Toujours : une relance ciblée par étape en échec de contrat, puis escalade utilisateur avec l'état réel — jamais de boucle de retry |

## Champs d'une étape

| Champ | Valeurs | Rôle |
| --- | --- | --- |
| `id` | slug unique dans le playbook | Référence (journal, diagnostic superviseur) |
| `agent` | agent/skill du catalogue, ou `session principale` | Qui exécute |
| `mode` | `cascade` \| `parallele` \| `asynchrone` | La dépendance de données décide (§5 de `docs/reflexions/conception-agent-orchestrator.md`) |
| `modele` | `haiku` \| `sonnet` \| `opus` \| `fable` \| `(session)` | Sous-agents uniquement ; `(session)` pour tout ce qui tourne inline |
| `fan_out_max` | entier ≤ 4 | Obligatoire si `mode` = `parallele` |
| `contrat` | objet `{type, critere[, commande]}` | Vérifié avant de passer à l'étape suivante. `type` : `deterministe` (fichier attendu présent, commande verte — préférer) \| `reel` (rendu regardé par un humain/screenshot : run-dev-server, pptx-verify) \| `llm` (dernier recours) |
| `checkpoint` | `false` \| texte (raison) | Validation utilisateur obligatoire avant de continuer — toujours non-`false` avant une action irréversible (commit, suppression, publication) |

Une étape `parallele` doit être suivie d'une étape de consolidation en `cascade`
(jamais d'écritures concurrentes sur les mêmes fichiers). Un playbook de dev se termine
par l'étape `revue-increment` (leçon superviseur : « jamais invoquée » — rendue
structurelle ici).

## Jalon intermédiaire (étapes longues)

Le format n'a pas de champ dédié pour un point d'étape intermédiaire — veille adoptée
2026-09-08 (Beyond the Leaderboard, arXiv:2607.05775) : un sous-agent long (plusieurs
dizaines d'appels d'outils) risque la « behavioral state decay » (l'état pertinent
d'une décision se noie dans une trajectoire qui s'allonge) sans jalon qu'un
orchestrateur pourrait auditer en cours de route. Tant qu'aucun champ n'est ajouté au
schéma, noter l'exigence dans le `critere` du `contrat` de l'étape longue elle-même : un
point d'étape journalisable rendu par l'agent avant de poursuivre, coût tokens/latence
mis en regard du bénéfice de détection précoce.

## Exécution et journal

La skill instancie le playbook (adapte les étapes à la demande, sans en retirer les
vérifications obligatoires ni les checkpoints), le suit avec TodoWrite, vérifie chaque
contrat, et journalise le run dans `runs.jsonl` avec `"playbook": "<nom>"` dans les notes
ou le plan — c'est ce qui permettra au superviseur (étage 2 / incrément O-C) de mesurer
le taux de réussite par playbook et de remonter les playbooks jamais joués.

<!-- SOCLE-PROVENANCE: socle : 5760202 du 2026-10-03 -->
> **Socle généré** — tout ce qui PRÉCÈDE ce bandeau vient du hub de supervision (`5760202`, 2026-10-03) et sera **réécrit** à la prochaine propagation.
> Le chapitre « Portée sur ce projet » placé après ce bandeau, lui, n'est jamais réécrit : c'est le travail local.

## Portée sur ce projet

Le bloc JSON de ce format est, **dans ce dépôt**, verrouillé par un test réel :
`tests/test_agent_orchestration.py`. Le socle publié par le hub dit « aucun test de ce
hub ne verrouille ce format » — c'est vrai du hub, faux ici : toute évolution du schéma
doit rejouer cette suite avant commit.

Conception locale de l'orchestrateur : `docs/reflexions/agent-orchestrateur.md` §4
(brique 3) et §10 (incrément O-B). C'est ce document qui fait foi ici ; le socle pointe
`docs/reflexions/conception-agent-orchestrator.md`, qui est la version du hub.
