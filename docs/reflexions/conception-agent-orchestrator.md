# Conception — agent orchestrateur (le POURQUOI)

> **Origine** : VSCode2, `export/agent-orchestrator/conception.md`, dernier commit `0b414f9` du 2026-07-20 — le fichier n'est plus dans l'arbre de VSCode2 (export/ supprimé), il se lit par `git -C ~/Documents/VSCode2 show 0b414f9:export/agent-orchestrator/conception.md`.
> **Reprise au hub** : 2026-09-02 — reprise **partielle**, assumée telle.
> **Repris** : §1 problème/objectif, §2 pas d'imbrication de sous-agents, §3 ce qui existe déjà, §5 règle des modes, §8c politique de modèle (le rationale seul), §9 risques et garde-fous, §11 décisions avec leur statut RÉEL au hub.
> **Écarté** : les statuts d'incréments datés O-A/O-B/O-C, le phasage §10, §12 (mémoire git des agents) et §13 (playbook `export-ppt-verifie`) — journal de projet VSCode2, pas du rationale ; et, jugés déjà portés par le QUOI opérationnel sans rationale propre : §4 (les 3 briques — c'est la structure de `.claude/orchestration/`), §6 (boucle orchestrateur↔superviseur — c'est le § 2 bis de la skill et le §6 du document superviseur), §7 et §8a-b (restitution et métriques — voir la décision n°2 ci-dessous, qui dit pourquoi la métrique n'existe pas).
> Ce document explique le **POURQUOI** ; le QUOI opérationnel vit dans la skill `.claude/skills/agent-orchestrator/SKILL.md`.

La numérotation est celle de la source, trous compris : `§5` ici est bien `§5` là-bas, et
les renvois déjà écrits (`.claude/orchestration/playbooks/FORMAT.md`) restent vrais.

## 1. Point de départ : l'orchestrateur de fait existe déjà — mais sans discipline

La session principale Claude Code **est** déjà un orchestrateur : elle peut lancer des
sous-agents en parallèle, enchaîner des skills en séquence, faire continuer un sous-agent
existant (`SendMessage`), travailler en arrière-plan. Ce qui manque n'est pas un moteur —
c'est :

- une **discipline de routage** : quels agents pour quelle demande, dans quel ordre, avec
  quels garde-fous (sans elle : improvisation au fil de la session) ;
- des **contrats entre étapes** : ce que l'étape N doit produire pour que l'étape N+1 soit
  exploitable — c'est exactement l'axe « interactions entre agents » que le superviseur
  avait identifié comme challenge ;
- une **traçabilité** : sans journal de « quel plan a été exécuté, qu'est-ce qui a marché »,
  le superviseur n'a rien à évaluer.

L'objectif tient donc en une phrase : transformer une capacité déjà présente en pratique
disciplinée, mesurable et corrigeable.

## 2. Contrainte structurante (de juillet) : pas d'imbrication de sous-agents

**Contrainte de la source, levée depuis.** En juillet 2026, dans VSCode2, un sous-agent ne
pouvait pas lancer d'autres sous-agents. Au hub ce n'est plus vrai : `agent-supervisor` et
`bmad-revue` déclarent l'outil `Agent` dans leur frontmatter (`.claude/agents/`), et la
revue de ce document même a tourné dans un sous-agent qui en a lancé deux (2026-09-02).
La conclusion, elle, tient toujours, pour une autre raison — le pilotage doit rester là où
l'utilisateur arbitre (R4), donc dans la session principale. Conséquence architecturale,
identique à celle du superviseur :
**l'orchestrateur n'est pas un agent de fond ni un sous-agent, c'est une skill de projet**
qui charge dans la session principale une méthode de décomposition, de routage et de
pilotage — plus des données versionnées (catalogue, playbooks) et un journal d'exécution.

C'est ce qui explique la forme du dispositif au hub : une skill (`agent-orchestrator`), un
hook de qualification, des fichiers de données — et non un démon. Un sous-agent
`agent-orchestrator` existe bien au hub, mais pour *déléguer une orchestration entière hors
du contexte principal*, pas pour orchestrer depuis l'intérieur : il est d'ailleurs en
sommeil (`.claude/orchestration/catalogue.md:59`).

## 3. Ce qui existe déjà et qu'il ne faut pas dupliquer

Règle de fond : l'orchestrateur se place **au-dessus** de l'outillage existant, jamais à
côté. La table de la source est conservée pour son raisonnement — les cibles, elles, sont
celles de VSCode2 ; au hub, la vérité est dans `catalogue.md`.

| Existant | Ce qu'il fait | Position de l'orchestrateur |
| --- | --- | --- |
| `bmad-help` + `_bmad/_config/bmad-help.csv` | Recommande **le prochain** skill BMAD (une étape) ; le CSV encode déjà un DAG (`preceded-by`, `followed-by`, `required`, `outputs`) | Réutiliser le CSV comme source de playbook ; déléguer le conseil ponctuel, garder l'**exécution multi-étapes** |
| `bmad-party-mode` | Table ronde multi-personas dans une conversation | Mode « délibération » ponctuel, pas un pipeline |
| `bmad-dev-auto` | Une itération de boucle de dev non assistée | Brique invocable *par* un playbook, pas un concurrent |
| `revue-increment` | Definition-of-done de fin d'incrément | Étape terminale obligatoire des playbooks de dev (réponse au constat « jamais invoquée ») |
| Superviseur étage 1 | Mesure l'usage réel, remonte les TODO agents | Fournisseur de données de routage **et** consommateur du journal d'orchestration |
| Plan mode / TodoWrite / AskUserQuestion | Planification et checkpoints natifs du harness | S'appuyer dessus, ne pas réinventer d'UI |

Le corollaire tient toujours : sans catalogue, le routage parmi des dizaines de skills reste
de la devinette — et les parties factuelles du catalogue (usage, statut, coût) doivent être
générées, seules les descriptions d'intention étant écrites à la main (cf. §9, dérive).

## 5. Choix du mode d'exécution — la règle

| Mode | Quand | Garde-fou |
| --- | --- | --- |
| **Cascade** (séquence stricte) | La sortie de N est l'entrée de N+1 | Contrat de sortie vérifié avant de continuer — sinon l'erreur se propage et s'amplifie (une PRD fausse → des stories fausses → du code faux) |
| **Workflow** (cascade + branches + checkpoints) | Pipeline long, décisions intermédiaires, actions peu réversibles | Checkpoint humain aux embranchements et avant toute étape irréversible (commit, suppression, publication) |
| **Parallèle** (fan-out) | Étapes réellement indépendantes (lectures, revues par angle, explorations) | Jamais d'écritures concurrentes sur les mêmes fichiers ; budget explicite (N agents = N contextes froids facturés) ; consolidation obligatoire en fin |
| **Asynchrone** (arrière-plan) | Tâche longue et autonome dont le résultat n'est pas bloquant | Notification à la fin, **jamais** de résultat anticipé ou fabriqué avant réception ; nombre de chantiers async plafonné ; pas d'écritures concurrentes avec le premier plan |

> « **La dépendance de données décide** — si l'étape suivante a besoin du résultat, c'est
> synchrone (bloquant) ; sinon asynchrone ou parallèle ; et l'irréversibilité impose
> synchrone + checkpoint humain, quel que soit le reste. »

C'est cette phrase, et non la table, qui est la règle : la table n'en est qu'une projection.
C'est elle aussi que `playbooks/FORMAT.md` invoque pour le champ `mode` d'une étape.

## 8c. Politique de modèle — le rationale (la table vit dans la skill)

Primitive réelle : le lancement d'un sous-agent accepte un **paramètre de modèle**. La
contrainte honnête à poser d'emblée est que ce choix ne s'applique qu'aux **sous-agents** :
la session principale — donc les skills exécutées inline — tourne sur le modèle choisi par
l'utilisateur (`/model`) ; l'orchestrateur peut le *proposer*, pas l'imposer.

Le raisonnement derrière la répartition (fan-out mécanique → petit modèle ; dev courant →
modèle moyen ; structurant → grand modèle) est un pari sur le **coût d'une reprise** : sur
une décision structurante, le surcoût du gros modèle est inférieur au coût de la refaire.
D'où le garde-fou explicite contre le biais « moins cher partout » : **la qualité se mesure
par les reprises et les contrats ratés, pas par la facture seule**.

Ce qui rend la politique révisable plutôt que dogmatique : le catalogue porte un modèle
recommandé, chaque étape de playbook déclare le sien, le journal enregistre le modèle
**réellement** utilisé — le superviseur peut alors croiser modèle × type de tâche × taux de
reprise, et trancher poste par poste sur données. La table opérationnelle vit dans
`.claude/skills/agent-orchestrator/SKILL.md` (§ « Politique de modèle ») : elle est censée
bouger, ce document non.

## 9. Risques et garde-fous

- **Sur-orchestration** — le risque n°1, aggravé par le point d'entrée unique (la
  qualification tourne sur *chaque* prompt). Garde-fou : l'étape « qualifier »
  court-circuite l'orchestration sous un seuil de complexité, et reste une **décision
  silencieuse**, jamais un plan affiché pour une micro-tâche. Si « corrige cette typo »
  déclenche un plan, la taxe tue l'usage.
- **Coût tokens du parallèle** — chaque sous-agent repart de zéro. Garde-fou : budget
  déclaré dans le plan, fan-out plafonné, parallèle réservé aux étapes qui produisent des
  synthèses courtes.
- **Playbooks morts** — le sort des skills BMAD (43 sur 46 jamais invoquées, mesure du
  2026-09-02) guette les playbooks (écrits puis jamais
  joués). Garde-fou : ne créer un playbook qu'après avoir exécuté le workflow au moins une
  fois à la main avec succès ; le superviseur remonte les playbooks sans exécution.
- **Propagation d'erreur en cascade** — traitée par les contrats de sortie (§5) :
  vérification déterministe de préférence (fichier attendu présent, tests verts, schéma
  respecté), LLM en dernier recours.
- **Perte de contrôle utilisateur** — l'orchestrateur enchaîne des actions : il doit rester
  dans les rails du harness (permissions, hooks, checkpoints aux étapes irréversibles),
  jamais les contourner « parce que le plan le prévoit ».
- **Dérive du catalogue** — même problème que le wiki, même remède : les parties factuelles
  sont générées, seules les intentions sont écrites à la main.

## 11. Décisions — et leur statut RÉEL au hub le 2026-09-02

1. **Périmètre BMAD** (orchestrer le cycle produit BMAD d'emblée, ou attendre le tri des
   46 skills ?). **Dépassée en mieux** : ni « d'emblée » ni « après élagage » — arbitrage
   utilisateur du 2026-07-30, les skills BMAD *font partie du workflow* et c'est
   l'orchestrateur qui les déclenche quand le besoin matche, sous deux régimes cumulant le
   coût ET l'écriture (d'office pour une passe bornée qui ne rend qu'un rapport ;
   annoncé-puis-validé dès qu'elle coûte cher ou écrit un fichier réel). Porté par
   `.claude/skills/agent-orchestrator/SKILL.md:249` (§ 2 quinquies).
2. **Seuil de qualification** (à partir de quand une demande mérite orchestration ?).
   **OUVERTE.** Un critère *qualitatif* est écrit — `SKILL.md:20-23` : ≥ 2 étapes
   dépendantes, ≥ 2 agents/skills, vérifications obligatoires en jeu, ou action peu
   réversible au milieu d'un enchaînement. Mais la calibration « sur quelques cas réels »
   promise n'a jamais eu lieu, et la métrique qui devait la nourrir (ratio demandes
   orchestrées / court-circuitées / passées à côté) n'existe pas : `log_run.py:15` accepte
   bien `qualification: orchestre | direct-signale`, or sur les **105 runs** de
   `runs.jsonl` mesurés le 2026-09-02, **105 sont `orchestre` et 0 `direct-signale`**. Une
   exécution directe n'est jamais journalisée : le ratio est non mesurable par construction,
   donc le seuil ne peut pas être arbitré sur données.
3. **Checkpoints par défaut** (plan systématiquement validé, ou seulement au-delà d'un
   seuil ?). **Portée par `SKILL.md:636-640`**, conforme à la recommandation : présenter le
   plan seulement au-delà d'un seuil (> 3 sous-agents, coût manifestement élevé, étape
   irréversible ou hors périmètre de la demande) — « la demande vaut mandat, la validation
   systématique tuerait l'usage ».
4. **OpenHub** (intégrer les agents `.opencode/` ?). **OUVERTE, et sans objet en l'état.**
   Mesuré le 2026-09-02 : pas de répertoire `.opencode/` au hub, pas de `data/app.db`,
   aucune occurrence d'« openhub » dans `catalogue.md` ni dans `.claude/skills/`. Le seul
   reste est du **code hérité mort** : `openhub_stats()`
   (`.claude/supervision/scan_transcripts.py:749`) lit un `REPO/data/app.db` inexistant
   (chemin défini l. 80-82). La décision n'a jamais été prise ici — elle a été vidée de son
   objet par le changement de contexte (le hub ne porte pas l'application qui produisait
   ces agents). Reste à trancher : garder ce code inerte, ou le retirer du canon.
5. **Niveau d'interception du point d'entrée unique** (hook dès le premier incrément, ou
   invocation manuelle le temps de calibrer le seuil ?). **Portée** : le hook existe et
   tourne — `.claude/settings.json:68-74` câble `UserPromptSubmit` sur
   `.claude/hooks/orchestrator_gate.py`, qui injecte la grille de qualification silencieuse.
   Recommandation suivie.
6. **Politique de modèle par défaut** (économe d'abord, ou qualité d'abord ?). **Portée par
   `SKILL.md:687-699`**, qui nomme explicitement « décision n°6 » : qualité d'abord sur le
   structurant, économe sur le fan-out. Le second temps annoncé — laisser les données du
   superviseur trancher poste par poste — est outillé comme *question de diagnostic*
   (`.claude/skills/agent-supervisor/SKILL.md:101`, « modèle × tâche inadapté ») ; aucun
   verdict par poste n'a été mesuré à ce jour (non mesuré).
