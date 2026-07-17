# Réflexion — Agent orchestrateur (lancement des agents en cascade, workflow ou parallèle)

> Statut : **incrément O-A réalisé le 2026-07-17** — catalogue `.claude/orchestration/catalogue.md`,
> skill `agent-orchestrator`, hook UserPromptSubmit (`orchestrator_gate.py`), journal
> `runs.jsonl` (`log_run.py`), tests `tests/test_agent_orchestration.py`. Arbitrages retenus :
> recommandations des décisions §11 (hook dès O-A, modèle « qualité sur le structurant,
> économe sur le fan-out », BMAD « à trier » routé uniquement sur demande, OpenHub exclu).
> **Incrément O-B réalisé le 2026-07-17** — format de playbook
> (`.claude/orchestration/playbooks/FORMAT.md`), deux playbooks manuels `eprouve`
> (`dev-verifie`, `revue-design-parallele`) capitalisant la pratique réelle du projet,
> `cycle-produit-bmad` généré depuis `_bmad/_config/bmad-help.csv`
> (`generate_bmad_playbook.py`, statut `jamais-joue` assumé), skill mise à jour pour
> chercher un playbook avant de composer à vide, catalogue et journal (`log_run.py`)
> référencent le champ `playbook`. Tests étendus (structure, terminaison
> `revue-increment`, régénération déterministe du playbook BMAD, résolution du DAG).
> Incrément O-C (boucle superviseur) à lancer — dépend de l'incrément B du superviseur
> (`agent-superviseur.md`).
> Demande d'origine : un agent orchestrateur capable de lancer tous les agents utiles en
> cascade, en workflow ou en parallèle selon la demande, en s'assurant de la bonne
> utilisation des skills, en cohérence avec le superviseur — et qui s'associe au superviseur
> pour améliorer l'utilisation des agents.
> Complément (même jour) : l'orchestrateur devrait être le **seul point d'entrée** de toutes
> les demandes, solliciter les bons agents/skills en **synchrone, asynchrone ou parallèle**
> selon la tâche, et **proposer le modèle le plus adéquat** (Sonnet, Fable, etc.) par
> activité — traité en §8.
> Document jumeau : [agent-superviseur.md](agent-superviseur.md) (étage 1 livré le 2026-07-17).

## 1. Point de départ : l'orchestrateur de fait existe déjà — mais sans discipline

La session principale Claude Code **est** déjà un orchestrateur : elle peut lancer des
sous-agents en parallèle (précédent réel dans ce projet : US9.12, 4 agents de revue design
en parallèle), enchaîner des skills en séquence, faire continuer un sous-agent existant
(SendMessage), et travailler en arrière-plan. Ce qui manque n'est pas un moteur — c'est :

- une **discipline de routage** : quels agents pour quelle demande, dans quel ordre, avec
  quels garde-fous (aujourd'hui : improvisation au fil de la session) ;
- des **contrats entre étapes** : ce que l'étape N doit produire pour que l'étape N+1 soit
  exploitable (le superviseur a identifié les « interactions entre agents » comme axe de
  challenge — c'est exactement ce point) ;
- une **traçabilité** : aucun journal de « quel plan a été exécuté, qu'est-ce qui a marché »
  n'existe — donc rien que le superviseur puisse évaluer.

## 2. Contrainte structurante : pas d'imbrication de sous-agents

Un sous-agent ne peut pas lancer d'autres sous-agents : l'orchestration doit être pilotée
depuis la session principale. Conséquence architecturale identique à celle du superviseur :
**l'orchestrateur n'est pas un agent de fond ni un sous-agent, c'est une skill projet**
(`agent-orchestrator`, jumelle d'`agent-supervisor`) qui charge dans la session principale
une méthode de décomposition, de routage et de pilotage — plus des données versionnées
(playbooks, catalogue) et un journal d'exécution.

## 3. Ce qui existe déjà et qu'il ne faut pas dupliquer

| Existant | Ce qu'il fait | Position de l'orchestrateur |
| --- | --- | --- |
| `bmad-help` + catalogue `_bmad/_config/bmad-help.csv` | Recommande **le prochain** skill BMAD (une étape à la fois) ; le CSV encode déjà un DAG (`preceded-by`, `followed-by`, `required`, `outputs`) | Réutiliser le CSV comme source de playbook « cycle produit BMAD » ; déléguer le conseil ponctuel à `bmad-help`, l'orchestrateur gère l'**exécution multi-étapes** |
| `bmad-party-mode` | Table ronde multi-personas dans une conversation | Mode « délibération » ponctuel, pas un pipeline — hors périmètre orchestration |
| `bmad-dev-auto` | Une itération de boucle de dev non-assistée | Brique invocable *par* un playbook, pas un concurrent |
| `revue-increment` | Definition-of-done de fin d'incrément | Étape terminale obligatoire des playbooks de dev (réponse au constat superviseur « jamais invoquée ») |
| Superviseur étage 1 (`.claude/supervision/`) | Mesure l'usage réel, remonte les TODO agents | Fournisseur de données de routage + consommateur du journal d'orchestration (§6) |
| Plan mode / TodoWrite / AskUserQuestion | Planification et checkpoints natifs du harness | L'orchestrateur s'appuie dessus, il ne réinvente pas d'UI |

## 4. Architecture proposée — 3 briques

**Brique 1 — Catalogue des agents** (`.claude/orchestration/catalogue.md`, versionné,
en partie généré) : la vue unifiée que superviseur et orchestrateur partagent. Pour chaque
agent/skill des 5 familles (projet, BMAD, global, sous-agents, OpenHub) : à quoi il sert,
quand le déclencher, ce qu'il produit, son coût typique (tokens/durée, alimenté par le
superviseur), son statut (éprouvé / jamais utilisé / KO connu). Sans catalogue, le routage
reste de la devinette parmi ~60 skills.

**Brique 2 — Skill `agent-orchestrator`** : le cœur. À l'invocation sur une demande :

1. **Qualifier** la demande (simple → pas d'orchestration, exécution directe : règle
   anti-sur-orchestration, voir risques) ;
2. **Composer le plan** : étapes, agent/skill par étape, mode par étape (cascade /
   parallèle / checkpoint humain), contrats de sortie — à partir d'un playbook existant si
   la demande en matche un, sinon composition libre depuis le catalogue + les hints du
   superviseur ;
3. **Valider** le plan avec l'utilisateur (plan mode / AskUserQuestion) au-delà d'un seuil
   de coût ou d'irréversibilité ;
4. **Exécuter** en pilotant la session : sous-agents parallèles quand les étapes sont
   indépendantes, séquence quand une sortie nourrit l'entrée suivante, vérification du
   contrat de sortie après chaque étape (relance ciblée une fois, puis escalade humaine —
   jamais de boucle infinie de retry) ;
5. **Journaliser** chaque exécution dans `.claude/orchestration/runs.jsonl` (demande, plan,
   agents lancés, modes, durées, verdicts de contrat, reprises) — la matière première du
   superviseur étage 2.

**Brique 3 — Playbooks versionnés** (`.claude/orchestration/playbooks/*.md`) : workflows
récurrents décrits déclarativement (étapes, agent, mode, contrat, checkpoint). Candidats
initiaux, tirés de la pratique réelle du projet :

- `cycle-produit-bmad` — brief → PRD → architecture → epics/stories → dev-story →
  code-review (généré depuis le CSV BMAD, pas écrit à la main) ;
- `dev-verifie` — implémentation → tests → vérification réelle (`run-dev-server`
  screenshot ou `pptx-verify` selon le type de changement) → `revue-increment` ;
- `exploration-parallele` — fan-out de sous-agents Explore sur des questions indépendantes
  puis consolidation ;
- `revue-design-parallele` — le pattern US9.12 (N agents de revue en parallèle sur des
  angles distincts), aujourd'hui non capitalisé.

## 5. Choix du mode d'exécution — règles simples

| Mode | Quand | Garde-fou |
| --- | --- | --- |
| **Cascade** (séquence stricte) | La sortie de N est l'entrée de N+1 | Contrat de sortie vérifié avant de continuer — sinon l'erreur se propage et s'amplifie (une PRD fausse → des stories fausses → du code faux) |
| **Workflow** (cascade + branches + checkpoints) | Pipeline long, décisions intermédiaires, actions difficilement réversibles | Checkpoint humain aux embranchements et avant toute étape irréversible (commit, suppression, publication) |
| **Parallèle** (fan-out) | Étapes réellement indépendantes (lectures, revues par angle, explorations) | Jamais d'écritures concurrentes sur les mêmes fichiers ; budget explicite (N agents = N contextes froids facturés) ; consolidation obligatoire en fin |
| **Asynchrone** (sous-agent en arrière-plan) | Tâche longue et autonome dont le résultat n'est pas bloquant (grosse exploration, rapport, boucle `bmad-dev-auto`) — la session reste disponible pour autre chose | Notification à la fin, **jamais** de résultat anticipé/fabriqué avant réception ; nombre de chantiers async simultanés plafonné ; pas d'écritures concurrentes avec le travail au premier plan |

La règle de choix tient en une phrase : **la dépendance de données décide** — si l'étape
suivante a besoin du résultat, c'est synchrone (bloquant) ; sinon asynchrone ou parallèle ;
et l'irréversibilité impose synchrone + checkpoint humain, quel que soit le reste.

## 6. Symbiose avec le superviseur — la boucle d'amélioration

C'est le cœur de la demande « s'associer au superviseur ». Les deux se nourrissent
mutuellement, chacun par des données, pas par de la magie :

```text
superviseur (mesure) ──► routing-hints.json ──► orchestrateur (applique)
        ▲                                              │
        └────────────── runs.jsonl (journal) ◄─────────┘
```

- **Sens superviseur → orchestrateur** : le scan étage 1 génère déjà `state.json` (usage,
  dates) ; on y ajoute une sortie `routing-hints.json` : agents éprouvés à privilégier,
  jamais-utilisés à proposer avec prudence, KO connus à éviter, vérifications
  systématiquement oubliées à insérer d'office (ex. constat actuel : `revue-increment`
  jamais invoquée → tout playbook de dev se termine par elle). Les mémoires `feedback_*`
  sont la deuxième source de hints (leçons déjà capitalisées sur la façon de travailler).
- **Sens orchestrateur → superviseur** : `runs.jsonl` donne enfin au superviseur ce que
  les transcripts seuls ne disent pas — le **plan vs le réel** : quelle étape a échoué son
  contrat, combien de relances, quel mode a été efficace. L'étage 2 du superviseur
  (diagnostic LLM, incrément B de l'autre réflexion) y gagne sa métrique la plus
  actionnable : taux de réussite par playbook et par agent, au lieu de simples comptages.
- **Gouvernance des évolutions** : le superviseur *propose* (TODO agents : « l'étape X du
  playbook Y échoue 2 fois sur 3, modifier son contrat d'entrée »), l'humain arbitre,
  l'orchestrateur applique la version amendée du playbook. Ni l'un ni l'autre ne s'auto-
  modifie silencieusement.

## 7. « Bonne utilisation des skills » — règles d'accompagnement

L'orchestrateur est le point d'application naturel des règles que le projet a déjà payées
pour apprendre (mémoires, CLAUDE.md) mais qui reposent aujourd'hui sur la seule vigilance
de session : modification de template Jinja → screenshot `run-dev-server` ; modification
`pptx_export.py` → `pptx-verify` (pytest vert ne suffit pas) ; fin d'incrément →
`revue-increment` ; grosse exploration → sous-agent plutôt que pollution du contexte
principal. Encodées comme étapes de vérification dans les playbooks, elles deviennent
structurelles — et le superviseur mesure leur respect (métrique « couverture de
vérification » de l'autre réflexion).

## 8. Complément — point d'entrée unique, sync/async, choix du modèle

### 8a. « Seul point d'entrée pour toutes les demandes »

Ce que le harness permet réellement : on ne peut pas techniquement forcer chaque message à
passer par une skill — l'utilisateur parle à la session principale, et c'est très bien
(une question, une correction en cours de tâche ou un « merci » n'ont pas à être orchestrés).
Le mécanisme honnête pour un « point d'entrée par défaut » est le même que pour le
superviseur : **un hook**. Un hook `UserPromptSubmit` injecte à chaque prompt une **grille de
qualification légère** (« demande de travail multi-étapes ou multi-agents ? → charger
`agent-orchestrator` ; sinon exécution directe »). C'est déterministe, automatique, versionné
— et ça transforme le point d'entrée unique en réflexe structurel plutôt qu'en discipline
de bonne volonté (dont le sort est connu : cf. `revue-increment`).

Périmètre à assumer : le point d'entrée unique s'applique aux **demandes de travail**, pas à
chaque message. Restent hors circuit, volontairement : les invocations explicites de skills
par l'utilisateur (`/code-review`, `/run-dev-server`… — l'utilisateur sait ce qu'il veut),
la conversation, et les micro-tâches sous le seuil de qualification (§4, étape 1). La
qualification elle-même doit rester une décision silencieuse d'une seconde — si « corrige
cette typo » déclenche un plan affiché, la taxe tuera l'usage. Le superviseur mesurera le
ratio demandes orchestrées / court-circuitées / passées à côté (nouvelle métrique étage 2).

### 8b. Synchrone / asynchrone / parallèle

Traité dans la table des modes (§5, ligne « Asynchrone » ajoutée) : les primitives réelles
du harness couvrent les trois — exécution bloquante quand l'étape suivante dépend du
résultat, sous-agent en arrière-plan avec notification pour les chantiers longs et autonomes,
fan-out pour l'indépendant. La règle « la dépendance de données décide » (§5) est le critère
de routage ; le plan produit par l'orchestrateur étiquette chaque étape avec son mode.

### 8c. Proposer le modèle le plus adéquat par activité

Primitive réelle : le lancement d'un sous-agent accepte un **paramètre de modèle**
(Haiku, Sonnet, Opus, Fable). Contrainte honnête à poser d'emblée : ce choix ne s'applique
qu'aux **sous-agents**. La session principale — donc les skills exécutées inline — tourne
sur le modèle choisi par l'utilisateur (`/model`) ; l'orchestrateur peut le *proposer*
(« cette passe d'architecture mériterait Fable, veux-tu basculer ? »), pas l'imposer.

Politique de routage initiale (à calibrer sur données réelles) :

| Modèle | Activités | Logique |
| --- | --- | --- |
| **Haiku** | Fan-out mécanique : recherches simples, extraction, résumés de logs, inventaires | Volume et coût — la qualité unitaire compte peu, la consolidation rattrape |
| **Sonnet** | Défaut dev : exploration de code, implémentation standard, revues ciblées | Meilleur rapport qualité/coût pour le gros du travail |
| **Opus / Fable** | Raisonnement long : architecture, plans complexes, revue adversariale, synthèses critiques, arbitrages | Le surcoût est inférieur au coût d'une reprise sur une décision structurante |

Intégration dans les briques existantes : le **catalogue** (§4) gagne une colonne « modèle
recommandé » par agent/activité ; chaque étape de **playbook** déclare son modèle ; le
**journal** `runs.jsonl` enregistre le modèle réellement utilisé. La boucle superviseur (§6)
devient alors capable de croiser modèle × type de tâche × taux de reprise — c'est-à-dire de
vérifier *sur données* si Haiku suffit pour tel fan-out ou si les échecs de contrat s'y
concentrent. Garde-fou contre le biais « moins cher partout » : la qualité se mesure par les
reprises et les contrats ratés, pas par la facture seule. (C'est la même logique « bon modèle
pour la bonne tâche » que le produit applique déjà côté app avec `SYNTHESE_MODEL` et le choix
Ollama local vs fournisseur hébergé.)

## 9. Risques et garde-fous

- **Sur-orchestration** : le risque n°1, aggravé par le point d'entrée unique (§8a — la
  qualification tourne désormais sur *chaque* prompt). Garde-fou : l'étape « qualifier »
  court-circuite l'orchestration sous un seuil de complexité (une étape, un seul agent →
  exécution directe, point), et reste une décision silencieuse, jamais un plan affiché
  pour une micro-tâche.
- **Coût tokens du parallèle** : chaque sous-agent repart de zéro. Garde-fou : budget
  déclaré dans le plan, fan-out plafonné, parallèle réservé aux étapes qui produisent des
  synthèses courtes (discipline tokens du CLAUDE.md).
- **Playbooks morts** : le sort des 46 skills BMAD guette les playbooks (écrits puis jamais
  joués). Garde-fou : ne créer un playbook qu'après avoir exécuté le workflow au moins une
  fois à la main avec succès ; le superviseur remonte les playbooks sans exécution depuis
  N semaines.
- **Propagation d'erreur en cascade** : traité par les contrats de sortie (§5) — vérification
  déterministe de préférence (fichier attendu présent, pytest vert, schéma respecté), LLM
  en dernier recours.
- **Perte de contrôle utilisateur** : l'orchestrateur enchaîne des actions — il doit rester
  dans les rails du harness (permissions, hooks `guard_destructive_git`, checkpoints aux
  étapes irréversibles), jamais les contourner « parce que le plan le prévoit ».
- **Dérive du catalogue** : même problème que le wiki — résolu de la même façon : les
  parties factuelles (usage, statut, coût) sont générées par le superviseur, seules les
  descriptions d'intention sont éditées à la main.

## 10. Phasage proposé

- **Incrément O-A — Router et tracer (~1 j)** : catalogue des agents (première version :
  générée depuis les données superviseur + descriptions manuelles courtes), skill
  `agent-orchestrator` MVP (qualifier → composer → valider → exécuter en
  cascade/parallèle/asynchrone → journaliser `runs.jsonl`, modèle utilisé compris), hook
  `UserPromptSubmit` avec la grille de qualification (§8a), sans playbooks formels. Valeur
  immédiate : point d'entrée par défaut en place, chaque orchestration traçable, routage
  appuyé sur des données.
- **Incrément O-B — Playbooks et contrats** : format de playbook (chaque étape : agent,
  mode sync/async/parallèle, **modèle recommandé**, contrat, checkpoint), génération de
  `cycle-produit-bmad` depuis le CSV BMAD, écriture de `dev-verifie` et
  `revue-design-parallele`, contrats de sortie avec vérification déterministe + relance
  unique.
- **Incrément O-C — Boucle d'amélioration** : `routing-hints.json` généré par le scan
  étage 1, exploitation de `runs.jsonl` par le superviseur étage 2 (plan vs réel, et
  croisement modèle × tâche × reprises pour affiner la politique de modèle §8c), cycle
  de gouvernance propose→arbitre→applique. **Dépendance** : O-C n'a de sens qu'avec
  l'incrément B du superviseur (diagnostic LLM) — les deux se planifient ensemble.

## 11. Décisions à trancher avant O-A

1. **Périmètre BMAD** : l'orchestrateur doit-il d'emblée savoir jouer le cycle produit BMAD
   (46 skills jamais utilisés — c'est peut-être *l'orchestrateur* qui les rendra enfin
   utiles), ou attendre l'arbitrage du chantier « trier BMAD » remonté par le superviseur ?
   Recommandation : trancher « trier BMAD » d'abord — inutile d'orchestrer un catalogue
   qu'on va peut-être élaguer.
2. **Seuil de qualification** : à partir de quand une demande mérite orchestration
   (nombre d'étapes ? familles d'agents impliquées ? irréversibilité ?) — à calibrer en O-A
   sur quelques cas réels.
3. **Checkpoints par défaut** : plan systématiquement validé par l'utilisateur, ou seulement
   au-delà d'un seuil de coût/irréversibilité ? (Recommandation : seuil — sinon la taxe de
   validation tuera l'usage, comme le rappel `revue-increment` ignoré à chaque session.)
4. **OpenHub** : intégrer les agents `.opencode/` dès O-A ou attendre (même logique que le
   superviseur : canal différent, valeur moindre) ? Recommandation : attendre.
5. **Niveau d'interception du point d'entrée unique (§8a)** : hook `UserPromptSubmit` sur
   toutes les demandes de travail dès O-A (recommandé — c'est ce qui rend l'orchestrateur
   « par défaut » au lieu d'optionnel), ou d'abord en invocation manuelle le temps de
   calibrer le seuil de qualification ?
6. **Politique de modèle par défaut (§8c)** : économe d'abord (Haiku/Sonnet partout, montée
   en gamme sur signal de reprise) ou qualité d'abord (Opus/Fable sur tout ce qui est
   structurant, descente en gamme prouvée par les données) ? Recommandation : qualité
   d'abord sur les étapes structurantes, économe sur le fan-out — puis laisser les données
   du superviseur trancher poste par poste.
