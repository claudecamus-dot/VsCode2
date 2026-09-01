---
name: agent-orchestrator
description: Orchestrateur des agents et skills du projet — qualifie une demande de travail, compose un plan (cascade / parallèle / asynchrone, modèle par étape), l'exécute en s'appuyant sur le catalogue et les données du superviseur, puis journalise le run. Lance réellement du multi-agents via l'outil Agent (fan-out parallèle dans un même message, arrière-plan notifié, SendMessage pour continuer un sous-agent, isolation worktree pour les écritures concurrentes, modèle par agent). Sait aussi APPLIQUER une recommandation arbitrée du superviseur (findings de diagnostic.json des deux volets — usage des agents ET pratiques test/dev/revue/design) via le playbook evolution-flotte, puis enregistrer l'arbitrage. Traite la commande « adopte <trouvaille> » (verbe d'arbitrage de la veille) : applique la regle_proposee au référentiel/scan et l'action_corrective aux projets concernés, passe l'entrée de veille.json en adopte (ou ecarte) et trace l'arbitrage. CONVOQUE les 11 salles de table ronde du hub (§ 2 septies) quand la demande pose un choix à instruire — refonte, adoption, partition d un chantier, faux consensus — au lieu d un travail à exécuter : la salle délibère et rend un compte rendu qui alimente le plan, elle ne modifie aucun fichier. Route les 46 skills BMAD installées par besoin détecté (table de § 2 quinquies : d'office pour les passes de lecture/critique qui rendent un rapport — revue, recherche, rétrospective ; annoncé-puis-validé dès qu'une skill coûte cher OU écrit un fichier réel — PRD, architecture, stories, code, documentation) et dispose pour cela de sous-agents porteurs de l'outil Skill — bmad-revue, bmad-doc, bmad-recherche, bmad-cadrage, bmad-livraison. Atteignable de trois façons : cette skill, le sous-agent agent-orchestrator (délégation d'une orchestration entière), ou la commande /orchestre. À charger quand une demande implique plusieurs étapes/agents, des vérifications obligatoires, ou « applique/traite la reco du superviseur » — ou quand la grille du hook UserPromptSubmit route ici.
---

# Agent orchestrateur (étages O-A + O-B + O-C)

Données de routage :
`.claude/orchestration/catalogue.md` (recommandations),
`.claude/orchestration/routing-hints.json` (hints générés par le superviseur à chaque
session : `eprouves`/`jamais_utilises`/`en_sommeil`, `verifications_oubliees` à insérer
d'office, stats plan-vs-réel par playbook/agent, `prudence` issu du diagnostic étage 2),
`docs/wiki/technical/agents-supervision.md` (tableau de bord humain des mêmes données) et
`.claude/orchestration/playbooks/` (workflows récurrents — format dans `playbooks/FORMAT.md`).

<!-- SOCLE-PROVENANCE: socle : c6ce467 du 2026-09-01 -->
> **Socle généré** — tout ce qui suit `## Méthode` vient du hub de supervision (`c6ce467`, 2026-09-01) et sera **réécrit** à la prochaine propagation.
> Le chapitre « Portée sur ce projet » ci-dessous, lui, n'est jamais réécrit : c'est le travail local.

## Portée sur ce projet

**Une skill maison prime sur une skill BMAD générique dès que le canal est celui du deck ou
de l'écran.** Ne pas plaquer un pattern générique là où le projet a déjà son outil.

**Vérifications obligatoires propres à ce dépôt** — elles s'ajoutent à celles du socle :

| Si le plan touche… | Alors le plan contient… |
| --- | --- |
| Template Jinja / CSS / JS | Screenshot via `run-dev-server` (pas seulement pytest) |
| `app/services/pptx_export/**` / `pptx_deck.py` | `pptx-verify` (rendu réel — python-pptx est un parseur tolérant). Cardinalité ou libellé rendus VARIABLES → rendre un cas **non par défaut** |
| `pptx_export/slides_diagnostic.py` (porte `_slide_swot`) | Skill `swot-matrix` chargée AVANT de dessiner (matrice 2×2 réelle, pas quatre cartes) |
| `pptx_export/slides_trajectoire.py` (porte `_slide_matrice_effort_valeur`) | Skill `priority-matrix` chargée AVANT de dessiner (matrice dessinée, jamais un scatter Excel natif) |
| Une slide dessinée ou retouchée, quelle qu'elle soit | Skill `deck-design-library` consultée AVANT (partir de l'intention pour choisir la forme) |
| Cadres photo / images encadrées (têtes de chapitre) | Skill `pptx-framed-image` (prstGeom cloné sur l'image, pas un arrondi PIL) |

`deck-design-review` et `slide-text-polish` pour la revue d'un deck, `pdf-quality` dès qu'un
PDF est produit.

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
| `evolution-flotte` | Modifier un AUTRE projet de la flotte (corrige/rattache/déploie/propage sur VSCodeN) — cadrage sur l'état réel, commit scopé au périmètre | Éprouvé |
| `dev-verifie` | Implémentation/correction avec tests + vérif réelle + revue finale avant commit | Importé, à confirmer |
| `export-ppt-verifie` | Livrable = un deck PPT : génération + enrichissements conditionnels (cadres photo, polish, design) + `pptx-verify` obligatoire | Importé, à confirmer |
| `revue-design-parallele` | Revue multi-angles d'un livrable en fan-out puis consolidation | Importé, à confirmer |

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

### 2 ter. Lancer réellement du multi-agents (mécanique de l'outil Agent)

Les modes ci-dessus se CONCRÉTISENT par l'outil `Agent` (Task) — pas par une
description d'intention. Les gestes exacts :

- **Fan-out parallèle** : plusieurs appels `Agent` **dans le même message** =
  lancement concurrent. Un appel par message = cascade involontaire (le 2e ne part
  qu'à la fin du 1er). Chaque sous-agent part avec un contexte VIERGE : son prompt
  doit être un **brief autoportant** — chemins absolus, exigence vérifiable, format
  de réponse attendu (« données brutes », pas de prose), et le rappel qu'il rend un
  RÉSULTAT (son texte final), pas un message à l'utilisateur.
- **Arrière-plan** : `run_in_background: true` (défaut) rend la main immédiatement,
  la notification arrive à la fin — ne jamais écrire le résultat à sa place ; s'il
  faut le résultat pour continuer, `run_in_background: false` (synchrone).
- **Continuer un sous-agent** : `SendMessage` avec son agentId (rendu à la fin de
  son run) relance LE MÊME agent avec son contexte intact — toujours préférable à
  re-briefer un agent neuf quand on itère sur le même sujet (revue → contre-revue).
- **Modèle par agent** : paramètre `model` de l'appel (haiku/sonnet/opus) selon la
  politique § modèle ci-dessous — le fan-out mécanique en haiku, la revue en sonnet,
  le structurant en opus ; omis = modèle de la session.
- **Écritures concurrentes** : deux sous-agents ne modifient JAMAIS les mêmes
  fichiers en parallèle. Si le plan l'exige, `isolation: "worktree"` (worktree git
  jetable par agent) ou sérialiser les étapes d'écriture — les lectures/analyses,
  elles, se parallélisent sans limite autre que ≤ 4.
- **Type d'agent** : `Explore` pour chercher/inventorier (lecture seule, économe),
  `general-purpose` pour agir (outils complets), `Plan` pour concevoir une stratégie
  d'implémentation. Le type se choisit par la nature de l'étape, pas par habitude.
  **Types maison** (`.claude/agents/`, créés le 2026-07-30) — tous porteurs de l'outil
  `Skill`, donc leurs invocations sont *comptées* par l'étage 1 :

  | Sous-agent | Pour | Modèle |
  | --- | --- | --- |
  | `bmad-revue` | Revue de code/diff, critique adversariale, cas limites, revue rédactionnelle, rétrospective (§ 2 quinquies) | opus |
  | `bmad-doc` | Documentation brownfield, index, découpage, rédaction technique | sonnet |
  | `bmad-recherche` | Recherche technique / domaine / marché, idéation | sonnet |
  | `bmad-cadrage` | Brief, PRD, PRFAQ, SPEC, architecture, UX — régime **proposé** | opus |
  | `bmad-livraison` | Epics/stories, sprint, implémentation, tests e2e — régime **proposé** | sonnet |
  | `veille-agentic` | Veille agentic sur cadence (§ 2 sexies) — écrit `veille.json`, n'adopte rien | sonnet |
  | `agent-supervisor` | Diagnostic étage 2 délégué — s'appuie sur `bmad-revue` et `veille-agentic` pour prouver ses findings, écrit `diagnostic.json`, n'applique rien | opus |
  | `agent-orchestrator` | Cet orchestrateur lui-même : déléguer une orchestration ENTIÈRE hors du contexte principal | hérité |
- **Consolidation obligatoire** : un fan-out sans étape de synthèse qui recroise les
  résultats (doublons, contradictions, trous) n'est pas un plan — c'est du bruit
  distribué. La consolidation est une étape à part entière du plan journalisé.

**Sous-agents ou agent team ?** (veille 2026-07-29, doc officielle Anthropic). Les
sous-agents restent le DÉFAUT : ils rendent un résultat au demandeur et ne se parlent
jamais entre eux — coût bas, contexte principal préservé. Une *agent team* (équipiers
qui se messagent via une liste de tâches partagée) ne se justifie que si les
travailleurs doivent **se coordonner ou se contredire entre eux** : revue multi-angles
avec débat, hypothèses concurrentes qu'on veut voir se réfuter, chantier transverse où
chacun possède sa couche. Elle est **expérimentale, désactivée par défaut**
(`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) et son coût croît linéairement avec le
nombre d'équipiers — chacun est une session Claude complète. Garde-fous officiels si
elle est retenue : 3-5 équipiers, 5-6 tâches par équipier, **partition stricte des
fichiers** (deux équipiers sur le même fichier = écrasement), démarrer par des tâches
de recherche/revue. Le plan journalisé doit **justifier le véhicule choisi** — un
fan-out de sous-agents non justifié comme team est le défaut attendu, pas un manque.

**Fan-out manuel ou dynamic workflow ?** (veille 2026-08-31, adoptée le jour même.) Le
fan-out de l'outil `Agent` reste le défaut : il tient dans un message, se lit dans le
plan journalisé, et couvre les ≤ 4 sous-agents que ce hub dispatche d'ordinaire. Passé
cette taille, il montre ses limites — les appels sont réécrits à la main à chaque
relance, et rien ne recroise mécaniquement les résultats entre eux. Un **dynamic
workflow** (outil `Workflow`, skill `workflow-authoring`) est un script réexécutable
qui orchestre des dizaines de sous-agents et **rejoue les étapes inchangées depuis leur
cache** : il se justifie quand (1) le plan dépasse une poignée de sous-agents, (2) les
résultats doivent être **vérifiés les uns contre les autres** (chaque finding d'une
passe re-vérifié par un agent dédié), ou (3) la même campagne sera relancée après
correction. Deux garde-fous : il ne se lance que sur **opt-in explicite de
l'utilisateur** (mot-clé « ultracode », demande d'orchestration multi-agents, ou skill
qui l'ordonne) — jamais sur la seule initiative de l'orchestrateur, parce qu'il coûte
cher ; et le plan journalisé doit **dire pourquoi** le véhicule a été choisi, exactement
comme pour les agent teams.

**Aucun agent/skill ne couvre le besoin ?** Ne pas improviser sans le signaler — escalade
en trois temps, dans cet ordre :

1. **Mémoire git** : `py .claude/orchestration/git_agents_inventory.py` inventorie tous
   les agents/skills que git connaît — **présents et supprimés** (un agent adapté a pu
   être retiré lors d'un nettoyage). `--json` pour la version structurée.
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

### 2 bis. Agir sur une recommandation du superviseur

Le superviseur *propose* (findings de `diagnostic.json`, avec un champ `proposition`),
l'utilisateur *arbitre*, **l'orchestrateur applique la version validée** — c'est la
boucle propose→arbitre→applique. Quand la demande est « applique la reco X », « traite le
finding Y », « corrige le point de pratique Z » (ou plus large : « traite tout ») :

1. **Lire les propositions** dans `.claude/supervision/diagnostic.json` (les mêmes que la
   section « Pratiques, couverture & risques » et les findings du wiki). Chaque finding
   porte `categorie`, `cible`, `titre`, `preuve`, `recommandation`, `proposition`. Les
   deux volets sont traitables :
   - **Usage des agents** (`ko-repete`, `inefficacite`, `agent-mort`, `interaction`,
     `verification-manquante`, `non-convergence`) → la proposition amende un skill, un
     playbook, un contrat d'étape, ou met un agent en sommeil.
   - **Pratiques d'ingénierie** (`pratique-test`, `pratique-dev`, `pratique-revue`,
     `pratique-design`) → la proposition installe un outil (coverage, linter), câble un
     hook (revue pré-commit), greffe une skill (`deck-design-review`), ou impose un audit
     `audit-technique` sur un projet cible.
   - **Documentation** (`pratique-doc`) → remédiation via `bmad-document-project`
     (brownfield), `bmad-agent-tech-writer` (Paige), `bmad-index-docs`, ou rédaction
     directe d'un README/CLAUDE.md manquant.
   - **Cadrage produit** (`pratique-produit`) → remédiation via `bmad-product-brief`,
     `bmad-prd`, `bmad-forge-idea`, `bmad-agent-analyst`/`bmad-agent-pm` — famille
     `bmad-cadrage`, régime **proposé** (§ 2 quinquies) : l'orchestrateur annonce le
     livrable de cadrage visé et attend le feu vert avant de lancer.
2. **N'appliquer QUE l'arbitré.** Si l'utilisateur n'a pas explicitement validé, présenter
   la proposition et demander l'arbitrage — jamais d'auto-application, même « évidente »
   (gouvernance stricte, identique côté superviseur). « Traite tout » vaut arbitrage de
   l'ensemble des findings ouverts.
3. **Choisir le véhicule d'exécution** selon la cible de la proposition :
   - proposition qui touche **un autre projet de la flotte** (installer un linter sur
     VSCode2, greffer une skill sur VSCode4…) → instancier le playbook **`evolution-flotte`**
     (cadrage sur l'état réel → modif scopée → vérifs → commit limité au périmètre → wiki
     → journal).
   - proposition qui touche **ce projet-ci** (un skill/playbook/script local) → édition
     directe suivie de la vérification adaptée (py_compile, JSON valide, test).
4. **Enregistrer l'arbitrage** une fois appliqué : `.claude/supervision/arbitrages.json`
   (champ `cible` = celle du finding, `decision` = « ACCEPTÉ + APPLIQUÉ : <ce qui a été
   fait> »). Le scan clôt alors le finding (le wiki cesse de l'afficher en alerte). Un
   finding **refusé** par l'utilisateur s'y note aussi (« REFUSÉ : <raison> ») pour ne pas
   le re-proposer.

Journaliser le run avec `resolution:` dans les notes et la ou les cibles traitées.

### 2 quater. La commande `adopte` — arbitrer une trouvaille de veille

`adopte <trouvaille>` (ou « adopte la pratique X », « adopte l'entrée Y ») est **le
verbe d'arbitrage de la veille**, symétrique de « applique le finding » pour le
diagnostic. La veille *propose* (entrées de `.claude/veille/veille.json`, statut
`nouveau`/`etudie`), l'utilisateur *adopte*, **l'orchestrateur applique** — puis trace.
Une entrée `ecarte` se refuse de la même façon (« écarte X »), avec sa raison.

**Ce que la commande déclenche, dans l'ordre :**

1. **Retrouver l'entrée** dans `.claude/veille/veille.json` par titre, url ou mot-clé.
   Ambiguë ou absente → demander laquelle, ne jamais deviner : adopter la mauvaise
   pratique coûte plus cher que la question.
2. **Cadrer sur l'état RÉEL** (R1) : la trouvaille peut être déjà satisfaite, ou l'être
   autrement. Vérifier dans le code des projets concernés (`projets_concernes`) avant
   d'écrire quoi que ce soit. Correction minimale > refonte.
3. **Appliquer les deux débouchés** que porte l'entrée, quand ils existent :
   - `regle_proposee` → **règle d'analyse** : l'inscrire au référentiel
     `docs/wiki/technical/criteres-pratiques.md`, et si elle est mesurable à froid,
     l'outiller dans `scripts/scan_projets.py` (nouveau marqueur, 0 token) avec ses
     tests de non-régression. C'est ce qui fait passer un critère ⬜ en ✅.
   - `action_corrective` → **le correctif lui-même** : sur un autre dépôt, via le
     playbook `evolution-flotte` (cadrage réel → modif scopée → vérifs → commit scopé) ;
     sur le hub, édition directe + vérification adaptée.
   Une entrée de type `agent`/`skill`/`outil`/`framework` (volet 1) n'a pas ces champs :
   l'adoption y est une **installation ou une greffe** sur les projets concernés, à
   cadrer explicitement — jamais un `git clone` exécuté sans lecture préalable.
4. **Vérifier par les faits**, comme tout chantier : tests réels du projet cible, rendu
   regardé si UI, mesure du scan re-jouée si la règle est outillée.
5. **Tracer**, deux écritures distinctes et toutes deux obligatoires :
   - `statut` de l'entrée → `adopte` (ou `ecarte` + raison), avec en fin de
     `pertinence` un crochet daté disant ce qui a réellement été fait ;
   - une entrée dans `arbitrages.json` à la cible `veille:<slug>` — sans elle, le
     wiki continuera d'afficher la trouvaille comme en attente de décision.
6. **Journaliser** le run avec `resolution: adoption <nom>` dans les notes.

**Garde-fous.** Jamais d'exécution de code téléchargé pendant l'adoption (la veille
observe, l'adoption intègre du code LU). Jamais d'activation d'une capacité
expérimentale par défaut : documenter le critère de choix vaut adoption, poser la
variable d'environnement est une décision séparée. Et une pratique déjà généralisée sur
la flotte ne s'« adopte » pas : elle se constate — le dire plutôt que produire un diff
cosmétique.

### 2 quinquies. Router vers les skills BMAD

BMAD-METHOD est installé ici (v6.10.0, core + bmm) : **46 skills** couvrant cadrage
produit, conception, planification, implémentation, revue, documentation et recherche.
Jusqu'au 2026-07-30 elles étaient réservées à la « demande explicite, via `bmad-help` » —
résultat mesuré par l'étage 1 : **0 invocation sur 113 sessions**, et un TODO
`agent-mort` ouvert au wiki. La règle a changé (arbitrage utilisateur du 2026-07-30) :
**elles font partie du workflow**, et c'est l'orchestrateur qui les déclenche quand le
besoin matche — plus besoin que l'utilisateur les nomme.

**Deux régimes de déclenchement, deux critères cumulatifs : le coût ET l'écriture.**

- **D'office** — la skill est bornée *et* ne produit qu'un rapport : une passe de
  lecture ou de critique, sans cascade et sans toucher au disque. L'orchestrateur
  l'insère dans le plan comme n'importe quelle autre étape, sans demander.
- **Proposé** — la skill remplit au moins l'une de ces conditions :
  1. elle ouvre un **workflow multi-étapes** produisant des artefacts structurants
     (PRD, architecture, epics, code) ou mobilise plusieurs personas — le coût ;
  2. elle **écrit, déplace ou restructure un fichier réel** — même vite, même bien.
  L'orchestrateur **annonce l'étape et attend le feu vert**.

Le second critère est arrivé après coup (finding `orchestrateur:regime-office-ecriture`,
diagnostic du 2026-07-30, arbitré le jour même). La première version ne pesait que le
coût, et laissait donc partir sans arbitrage `bmad-document-project`, `bmad-index-docs`,
`bmad-shard-doc` et `bmad-agent-tech-writer` — quatre skills qui écrivent dans le dépôt.
Or **R4 ne parle pas de coût, il parle d'auto-application** : une écriture non arbitrée
la viole, qu'elle prenne dix secondes ou dix minutes. Le régime ne juge donc pas la
qualité d'une skill — il dit qui autorise la dépense *et* qui autorise le diff.

**Où ces skills ont un objet.** Le hub ne produit pas de livrable applicatif : sur
lui-même, seules les familles revue / documentation / recherche / rétro ont du sens.
Cadrage, conception, planification et implémentation visent **les projets de la flotte**
(VSCode1 et VSCode2 ont du code, VSCode3 et VSCode4 des decks) — donc via le playbook
`evolution-flotte`, avec son commit scopé (R2). Router `bmad-sprint-planning` sur le hub
produirait un artefact sans lecteur.

<!-- BMAD-ROUTAGE:START — table verrouillée par tests/test_orchestration_bmad.py :
     toute skill bmad-* installée doit y figurer (ou dans la liste des dépréciées),
     et le sous-agent porteur cité doit exister dans .claude/agents/. -->

| Besoin détecté dans la demande | Skill BMAD | Sous-agent porteur | Déclenchement |
| --- | --- | --- | --- |
| Revoir un diff, une PR, du code écrit dans la séance | `bmad-code-review` | `bmad-revue` | d'office |
| Critiquer un livrable non-code (plan, note, décision) | `bmad-review-adversarial-general` | `bmad-revue` | d'office |
| Chercher les cas limites non traités d'un code ou d'une spec | `bmad-review-edge-case-hunter` | `bmad-revue` | d'office |
| Améliorer la qualité rédactionnelle d'un texte | `bmad-editorial-review-prose` | `bmad-revue` | d'office |
| Réorganiser / élaguer la structure d'un document | `bmad-editorial-review-structure` | `bmad-revue` | d'office |
| Faire relire un changement par un humain (checkpoint) | `bmad-checkpoint-preview` | `bmad-revue` | d'office |
| Approfondir une sortie récente (socratique, prémortem, red team) | `bmad-advanced-elicitation` | `bmad-revue` | d'office |
| Rétrospective de fin d'epic ou d'incrément | `bmad-retrospective` | `bmad-revue` | d'office |
| S'orienter dans le catalogue BMAD, choisir la bonne skill | `bmad-help` | `bmad-revue` | d'office |
| Documenter un projet existant (brownfield) pour le contexte IA | `bmad-document-project` | `bmad-doc` | proposé |
| Créer / rafraîchir l'index d'un dossier de docs | `bmad-index-docs` | `bmad-doc` | proposé |
| Découper un document trop gros en sections navigables | `bmad-shard-doc` | `bmad-doc` | proposé |
| Rédiger ou curer de la documentation technique (Paige) | `bmad-agent-tech-writer` | `bmad-doc` | proposé |
| Recherche technique sur une techno, un framework, une archi | `bmad-technical-research` | `bmad-recherche` | d'office |
| Recherche sur un domaine métier ou un secteur | `bmad-domain-research` | `bmad-recherche` | d'office |
| Recherche marché, concurrence, clients | `bmad-market-research` | `bmad-recherche` | d'office |
| Idéation cadrée sur un problème ouvert | `bmad-brainstorming` | `bmad-recherche` | d'office |
| Brief produit initial | `bmad-product-brief` | `bmad-cadrage` | proposé |
| PRD — créer, éditer ou valider | `bmad-prd` | `bmad-cadrage` | proposé |
| PRFAQ Working Backwards (concept client-first) | `bmad-prfaq` | `bmad-cadrage` | proposé |
| Durcir une idée par interrogation adverse | `bmad-forge-idea` | `bmad-cadrage` | proposé |
| Distiller une intention en noyau SPEC machine | `bmad-spec` | `bmad-cadrage` | proposé |
| Analyse métier et exigences (Mary) | `bmad-agent-analyst` | `bmad-cadrage` | proposé |
| Cadrage produit conduit par un PM (John) | `bmad-agent-pm` | `bmad-cadrage` | proposé |
| Architecture technique (colonne d'invariants) | `bmad-architecture` | `bmad-cadrage` | proposé |
| Conception système conduite par un architecte (Winston) | `bmad-agent-architect` | `bmad-cadrage` | proposé |
| Specs UX, patterns d'interaction | `bmad-ux` | `bmad-cadrage` | proposé |
| Design UX/UI conduit par une designer (Sally) | `bmad-agent-ux-designer` | `bmad-cadrage` | proposé |
| Écrire les règles IA du projet (project-context.md) | `bmad-generate-project-context` | `bmad-cadrage` | proposé |
| Table ronde multi-personas / focus group | `bmad-party-mode` | `bmad-cadrage` | proposé |
| Customiser une skill BMAD (party, personas, overrides de config) | `bmad-customize` | `bmad-cadrage` | proposé |
| Découper des exigences en epics et stories | `bmad-create-epics-and-stories` | `bmad-livraison` | proposé |
| Écrire une story prête à implémenter | `bmad-create-story` | `bmad-livraison` | proposé |
| Construire le plan de sprint depuis les epics | `bmad-sprint-planning` | `bmad-livraison` | proposé |
| État du sprint, risques à surfacer | `bmad-sprint-status` | `bmad-livraison` | proposé |
| Changement significatif en cours de sprint | `bmad-correct-course` | `bmad-livraison` | proposé |
| Vérifier que PRD/UX/archi/epics sont prêts pour l'implémentation | `bmad-check-implementation-readiness` | `bmad-livraison` | proposé |
| Implémenter une story déjà spécifiée | `bmad-dev-story` | `bmad-livraison` | proposé |
| Boucle de développement non surveillée (une itération) | `bmad-dev-auto` | `bmad-livraison` | proposé |
| Implémenter directement une intention / un correctif | `bmad-quick-dev` | `bmad-livraison` | proposé |
| Exécution d'histoire conduite par un dev senior (Amelia) | `bmad-agent-dev` | `bmad-livraison` | proposé |
| Générer des tests e2e sur une feature existante | `bmad-qa-generate-e2e-tests` | `bmad-livraison` | proposé |

**Le gel de `bmad-customize` est LEVÉ** (arbitrage utilisateur du 2026-07-31). L'arbitrage
`skills-jamais-utilisees` du 2026-07-27 avait posé « aucune customisation jusqu'à la v7 » :
la customisation attendait une version qui n'est toujours pas sortie (v6.10.0 vérifiée le
2026-07-30 sur l'API GitHub, aucun tag `v7*`). La décision est de **rester en v6 et de
customiser dès maintenant** plutôt que d'attendre indéfiniment — un gel conditionné à un
événement qui ne vient pas est un gel définitif qui ne dit pas son nom.

Ce que la levée change, et ce qu'elle ne change pas :

- `bmad-customize` **est routable**, en régime **proposé** — elle écrit un fichier réel
  (`_bmad/custom/<skill>.toml` ou `.user.toml`) : l'orchestrateur annonce l'étape et attend
  le feu vert, comme pour toute écriture (R4 s'applique en entier, il n'a jamais parlé de v6
  ou de v7).
- Une customisation reste une **modification de fichier de configuration** : elle passe par
  la skill, jamais par une édition manuelle de `customize.toml` (marqué « DO NOT EDIT —
  overwritten on every update »), et jamais par un script qui l'écrirait automatiquement.
- La **migration** vers la v7, quand elle sortira, redevient une décision à part entière :
  les overrides écrits en v6 devront être re-vérifiés à ce moment-là.

**Jamais routées** — **dépréciées par BMAD** (v6.10.0 les a consolidées ; retirées en v7) :
`bmad-create-prd`, `bmad-edit-prd`, `bmad-validate-prd` → utiliser `bmad-prd` ;
`bmad-create-architecture` → utiliser `bmad-architecture`. Si l'utilisateur les nomme,
router vers la skill canonique et le dire.

<!-- BMAD-ROUTAGE:END -->

**Faut-il toujours passer par le sous-agent porteur ?** Non — le porteur sert à
*isoler* un travail BMAD long dans un contexte à lui, ou à en paralléliser plusieurs.
Quand la session principale est déjà sur le sujet et que la skill est bornée
(`bmad-advanced-elicitation` sur ce qu'on vient d'écrire, `bmad-help` pour trancher),
l'invoquer **inline** est plus direct et compte pareil au tableau de bord. La règle :
> une skill BMAD dont le travail tient dans la conversation courante s'invoque inline ;
> une skill qui va lire beaucoup de fichiers ou produire un gros artefact part en
> sous-agent, brief autoportant compris (§ 2 ter).

**Porteur indisponible : dégrader, jamais abandonner l'étape.** Le registre des types
d'agents est chargé au **démarrage de session** — un sous-agent créé pendant la séance
peut ne pas être adressable tout de suite (constaté le 2026-07-30 : `subagent_type:
agent-supervisor` refusé dans la session même qui venait d'écrire le fichier ; les 8
types sont apparus plus tard dans la séance). Un `subagent_type` invalide ne justifie
donc pas de sauter l'étape :

1. **Invoquer la skill inline** (outil `Skill`) — le travail est fait, et l'invocation
   est comptée exactement pareil par l'étage 1.
2. Si l'isolement du contexte est vraiment nécessaire, dispatcher `general-purpose` avec
   le contenu du mandat du porteur en brief, **et les interdits recopiés explicitement**
   (un `general-purpose` a tous les outils : les garde-fous structurels du porteur —
   par exemple l'absence de `Write`/`Edit` du superviseur — deviennent de simples
   consignes, ce qui doit être dit dans le brief et dans le journal).
3. **Tracer** dans les notes du run : `resolution: porteur-indisponible <nom>`. C'est le
   signal qui dira au superviseur si le problème est ponctuel ou structurel.

### 2 sexies. Lancer la veille sur cadence — chercher les pistes qu'on n'a pas demandées

Les findings du superviseur et les demandes de l'utilisateur ne couvrent qu'un angle :
ce que la flotte sait déjà d'elle-même. La veille couvre l'autre — **les pratiques
agentic, agents, skills et playbooks publics que le dispositif ignore encore**. Une
flotte peut être parfaitement cohérente avec elle-même et en retard de six mois sur
l'état de l'art. C'est pourquoi la veille n'attend pas une demande : elle a une cadence,
et c'est l'orchestrateur qui la tient.

**Quand la lancer** (l'un de ces déclencheurs suffit) :

| Déclencheur | Vérification avant de lancer |
| --- | --- |
| Le hook SessionStart signale « veille a lancer ou perimee » (> 3 j) | Rien à vérifier — le hook a déjà lu `derniere_veille` |
| Fin d'un chantier, avant de considérer l'incrément livré | Lire `.claude/veille/veille.json` : si `derniere_veille` < 3 j, **ne pas relancer** — dire qu'elle est fraîche |
| Avant de créer un agent, une skill ou un playbook maison | Toujours : réécrire ce qui existe en public, mieux maintenu, est une perte sèche |
| Le superviseur a besoin de l'état de l'art pour prouver un finding | Synchrone dans ce cas (le diagnostic attend le résultat) |
| L'utilisateur demande des pistes d'amélioration, des évolutions, des bonnes pratiques | Toujours : c'est la demande même de la veille |

**Comment la lancer.** Sous-agent `veille-agentic` (outil `Agent`), qui porte l'outil
`Skill` et charge la méthode lui-même :

- **En arrière-plan par défaut** (`run_in_background: true`) : une veille lit beaucoup de
  sources et dure. Elle n'a aucune dépendance avec le chantier courant, donc elle ne doit
  jamais le bloquer — mais **attendre la notification** avant d'en parler : ne jamais
  écrire à sa place ce qu'elle « aura trouvé » (règle du mode asynchrone, § 2 ter).
- **Synchrone** (`run_in_background: false`) uniquement quand le résultat est nécessaire
  pour continuer — typiquement quand `agent-supervisor` l'appelle pour prouver un écart.
- **Un seul chantier de veille à la fois.** Deux veilles concurrentes écriraient toutes
  les deux `veille.json` : écrasement garanti.

**Ce qui suit le retour de la veille**, dans l'ordre — et c'est là que la plupart des
dispositifs de veille meurent :

1. **Régénérer le wiki** (`py scripts/scan_projets.py`) : la section 3 « Veille agentic »
   affiche les trouvailles et leur statut. Une veille écrite mais non propagée est
   invisible.
2. **Présenter les trouvailles à l'utilisateur**, une ligne chacune avec sa
   `regle_proposee` et son `action_corrective`. Elles arrivent en statut `nouveau` : ce
   sont des **propositions**, pas des décisions.
3. **Ne rien adopter de sa propre initiative.** L'adoption est la commande `adopte`
   (§ 2 quater) — un arbitrage utilisateur, tracé dans `arbitrages.json`. Appliquer une
   trouvaille sans arbitrage viole R4 aussi sûrement qu'appliquer un finding.
4. **Surveiller le pourrissement.** Une trouvaille qui reste `nouveau` plus de 7 jours est
   un signal à remonter : la veille a produit une règle que personne n'a arbitrée, donc
   payée pour rien. Le superviseur en fait un finding (`cible` = `veille:<slug>`) — la
   même leçon que les documents de réflexion, dont les propositions ne sont pas
   arbitrables tant qu'elles ne passent pas par `diagnostic.json`.

### 2 septies. Convoquer une salle — faire délibérer AVANT de planifier

Le hub porte **11 salles** de table ronde (`_bmad/custom/bmad-party-mode.toml`), rendues
dans l'onglet Dispositif du wiki avec leur casting et leur commande. Jusqu'au 2026-08-31
l'orchestrateur ne les connaissait pas : sa seule ligne était le renvoi générique
`bmad-party-mode` de la table BMAD, en régime « proposé ». Résultat mesuré — **aucune
salle n'était convoquée sur une demande utilisateur** : le mode d'emploi vivait dans le
générateur du wiki, invisible du plan. C'est la demande utilisateur du 2026-08-31
(« je n'ai pas l'impression qu'elles soient lancées lors de mes demandes ») qui a ouvert
cette section.

**Ce qu'une salle est, et n'est pas.** Une salle DÉLIBÈRE : elle rend un compte rendu —
points tranchés, désaccords restants, et qui-fait-quoi. Elle **ne modifie aucun fichier**,
ne committe pas, ne décide pas à la place de l'humain. Sa sortie ALIMENTE le plan de
l'orchestrateur ; elle ne le remplace pas. Une salle qui produirait un diff serait un
sous-agent mal briefé, pas une table ronde.

**Quand la convoquer — d'office.** Dès que la demande porte sur un **choix à instruire**
plutôt qu'un travail à exécuter, et qu'une situation ci-dessous matche : convoquer, en
l'annonçant en une ligne (quelle salle, pourquoi elle). Les marqueurs sont le doute, la
pluralité d'options, le désaccord ou l'absence de problème bien posé — « je ne sais pas
par où commencer », « faut-il adopter », « ça ne ressemble à rien », « est-ce prêt »,
« pourquoi ça coûte », « je n'arrive pas à formuler », « tout le monde est d'accord trop
vite ». À l'inverse, **ne pas convoquer** quand la demande est une exécution nette
(« corrige ce bug », « régénère le wiki », « solde les runs ») : une salle y ajouterait un
tour de parole et zéro information.

**Comment.** `/bmad-party-mode --party <salle> --mode subagent`, en énonçant le sujet
juste après. Le mode compte : `session` fait jouer toutes les voix par une seule, donc
**aucun débat réel** — `subagent` donne à chaque persona son propre contexte, et c'est la
seule façon qu'elles se contredisent. Deux tours au minimum : positions indépendantes,
puis confrontation. Depuis le wiki, le bouton « En débattre » lance exactement la même
chose sans terminal.

**Coût.** Une salle en `subagent` = une session par voix, soit 3 à 5 sessions. C'est le
prix du désaccord réel ; il ne se paie que sur un vrai choix. Une seule salle à la fois.

<!-- SALLES-ROUTAGE:START — table verrouillée par tests/test_salles_routage.py : toute
     salle citée ici doit exister dans _bmad/custom/bmad-party-mode.toml, et toute salle
     du TOML doit être routée ici (sinon elle est inatteignable depuis une demande). -->

| La demande ressemble à… | Salle | Ce qu'elle apporte |
| --- | --- | --- |
| « ce bug touche trois couches, par où commencer ? », partition d'un chantier de code, structure d'un code existant à faire évoluer | `atelier-dev` | Le Charpentier pose la structure et les frontières AVANT qu'on réparte les fichiers, les trois dev nomment leur périmètre exclusif, le Relecteur dit ce qui bloquera en revue |
| « on adopte cette pratique ou pas ? », arbitrer un finding, revue périodique du dispositif | `conseil-flotte` | Vigie l'état de l'art, Argus les mesures, Quincaillier l'existant, Garde-fou le coût de maintenance |
| « ce deck est correct mais ne ressemble à rien », concevoir/contrôler une restitution | `atelier-deck` | Maquettiste la fabrication, Contrôleur le gabarit, Sally le regard de celui qui reçoit |
| « est-ce prêt à passer en production ? », environnements, secrets, exploitation | `mise-en-service` | Aiguilleur les environnements, Passerelle ce qui sort du poste, Archiviste la doc, Garde-fou les tests |
| « pourquoi ma consommation a doublé ? », cette dépense a-t-elle acheté quelque chose | `revue-consommation` | Jauge les chiffres, Argus les runs joués, Quincaillier les outils qui tournent pour rien |
| « un nouveau projet arrive, personne ne le connaît » | `accueil-projet` | Salle open-cast : elle génère les voix du cadrage, sans relais écrit d'avance |
| « ce code me paraît risqué sans que je sache dire pourquoi » | `code-review-crew` | Cinq angles distincts (sécurité, contradiction, cas limites, artisanat, livrer) qui se disputent |
| « j'ai une intuition, pas encore une question », refonte, organisation de l'information, navigation, simplification | `atelier-idees` | Le Cadreur pose le problème avant les solutions, Portevoix parle pour l'usager absent, Wildcard ouvre les options, Splinter casse l'accord facile |
| « il faudrait relire tout ça à froid », inspection périodique, chasse aux fonctionnalités que plus personne n'utilise | `inspection-critique` | Quatre axes tenus séparés — bugs latents, design, expérience de celui qui s'en sert, et ce qui n'est jamais utilisé ; part d'un périmètre et de mesures d'usage, pas d'un diff |
| « où tournent nos environnements et combien ça coûte ? », infrastructure, secrets, reprise après incident | `socle-technique` | Le parc décrit avant d'être corrigé, les risques triés par risque et non par facilité ; tient l'infrastructure dans la durée là où la mise en service est un guichet par release |
| « tout le monde est d'accord trop vite et ça me met mal à l'aise » | `anti-consensus-club` | Elle casse le faux consensus, ouvre des options, arrête les boucles à vide |

<!-- SALLES-ROUTAGE:END -->

**Le manifeste de fonctionnement.** Chaque salle porte aussi son protocole — mode et
nombre de tours, déroulé (qui parle quand), traitement du désaccord, règle d'arrêt, et
interdits. Même charpente pour les onze, ce qui permet de comparer deux salles et de
reconnaître celle qui dérive de son propre mode d'emploi. **Le lire avant de convoquer** :
c'est lui qui dit si le premier tour interdit les solutions (`atelier-idees`), si les voix
doivent lire séparément avant de se parler (`code-review-crew`), ou si le premier tour est
un état des lieux et non une proposition (`socle-technique`). Un déroulé non respecté
produit une salle qui a l'air d'avoir siégé sans avoir délibéré.

**Le contrat de la salle — ses entrants, sa recette.** Depuis le 2026-09-01 chaque
salle porte, dans le TOML et rendue au wiki, quatre choses que l'orchestrateur doit
traiter comme des obligations et non comme de la documentation :

1. **Les entrants sont une condition de convocation, pas une suggestion.** Une salle
   réunie sans la matière qu'elle réclame (le diff exact, l'état réel du code, la spec
   ou l'ADR touché, les mesures de la période) délibère sur du vide et rend un avis qui
   a l'air d'un résultat. **Rassembler les entrants AVANT de convoquer** ; s'il en manque
   un qu'on ne peut pas produire, le dire dans le brief de la salle plutôt que de laisser
   les voix combler le trou par de la vraisemblance.
2. **La qualité requise se vérifie sur le compte rendu**, avant de le remonter : c'est
   le critère écrit par la salle elle-même, donc le seul qu'elle ne puisse pas contester.
3. **Le sortant nomme un producteur qui n'est jamais la salle.** Elle déclare le livrable
   (un deck, un plan de partition, un arbitrage, une fiche de cadrage) et QUI le produit
   — un playbook, un porteur BMAD, l'auteur du diff. Enchaîner sur ce producteur fait
   partie du plan ; s'arrêter au compte rendu, c'est la dépense sans achat.
4. **La recette est bloquante.** Chaque salle écrit les points que son livrable aval devra
   passer. L'orchestrateur **ne clot pas le run** tant qu'ils ne sont pas joués : une
   recette non vérifiée vaut `partiel`, jamais `succes`. C'est ce qui empêche le contrat
   d'être décoratif — la salle ne produit rien, mais ce qu'elle exige est opposable.

Le régime a été arbitré le 2026-09-01 : **déclaratif + recette vérifiable**. L'option
« la salle produit elle-même son livrable » a été écartée parce qu'elle aurait cassé
l'invariant « ne modifie aucun fichier », c'est-à-dire la garde de R4 contre une
auto-application collective.

**Après la salle.** Son compte rendu est une ENTRÉE du plan, à traiter comme le résultat
d'une étape : reprendre la partition proposée en fan-out, garder les désaccords restants
comme points d'arbitrage utilisateur, et journaliser la salle dans le `plan` du run
(`agent` = la salle, `mode` = `parallele`). Une salle tenue puis oubliée est une dépense
sans achat.

**Restituer une salle — la décision d'abord, le débat ensuite.** Une salle délibère pour
que quelqu'un tranche ; sa restitution est donc un document de DÉCISION, pas un compte
rendu de séance. Règle posée le 2026-08-31 après que la salle a rejeté sa propre
restitution (« c'est le vocabulaire de la salle qui vient de se tenir, pas celui de la
personne qui doit décider ») :

1. **Ouvrir par la question à trancher**, en une phrase, dans les mots de la tâche — pas
   par le contexte, pas par la méthode, pas par une formule qui suppose d'avoir assisté
   au débat.
2. **Les options en regard, avec les mêmes colonnes** : ce qu'on fait · ce que ça coûte ·
   ce qu'on saura · quand on le saura. Une option sans « ce qu'on saura » n'est pas une
   option, c'est une préférence.
3. **Dire ce qu'on recommande, et pourquoi** — une salle qui rend N possibilités
   équivalentes a sous-traité sa part du travail à celui qui décide.
4. **Ne jamais laisser la mise en page fabriquer une symétrie** : trois encadrés de même
   taille disent « trois hypothèses de même poids », et c'est faux dès que l'une porte un
   test qui la réfuterait et pas les autres. Le poids visuel doit suivre le poids réel.
5. **Citer chaque voix sans la corriger** : garder les conditions qu'elle a posées. Une
   option promue en effaçant sa réserve (« je l'abandonne si on veut trancher aujourd'hui »)
   n'est plus la sienne — c'est une déformation, même flatteuse.
6. **Les désaccords restants sont le livrable**, pas un reliquat : les nommer, dire ce qui
   les départagerait, et si c'est mesurable à froid, le mesurer AVANT de restituer (R6).

Le reste — transcription, ordre des tours, qui a bougé — vient après, pour qui veut
vérifier. Personne ne décide en lisant un dialogue.

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
| Template/CSS/JS/écran | Rendu réel regardé (screenshot ou app lancée), pas seulement pytest |
| Génération d'un export PPT | `pptx-verify` (rendu réel — python-pptx est un parseur tolérant) |
| **Livrable consommé par l'utilisateur** (deck exporté, écran) | Produire l'**artefact EXACT qu'il ouvre** (l'export réel, pas une fonction de démo maison), le rendre **ENTIER** (toutes les slides/pages, pas un extrait), et le faire **VALIDER par l'utilisateur** avant tout « fait » |
| Fin d'incrément / avant commit | Revue finale en étape terminale (relecture diff + exigences recochées) |
| Exploration volumineuse | Sous-agent `Explore`, jamais la session principale |
| Skills BMAD | Le régime de § 2 quinquies : **d'office** seulement si la skill est bornée ET ne rend qu'un rapport ; **annoncé et validé** dès qu'elle coûte cher (PRD, archi, stories, code) **ou qu'elle écrit un fichier réel** (documentation, index, découpage) |

**Règle de non-convergence.** Si le MÊME livrable est rejeté par l'utilisateur **≥ 3
tours** (« toujours KO », « pas traité »), la boucle ne converge pas : **STOP l'itération
à l'aveugle** — ne pas re-deviner le défaut. Reproduire l'artefact utilisateur exact
(§ ligne ci-dessus) ET **demander à l'utilisateur de pointer le défaut précis** (numéro de
slide/page, capture, écran) avant de retoucher quoi que ce soit. Re-deviner produit
l'oscillation ; l'oracle, c'est l'utilisateur sur SON artefact.

### 5. Journaliser

À la fin du run (succès **ou** échec), une ligne dans `.claude/orchestration/runs.jsonl` :

```bash
py .claude/orchestration/log_run.py '{"demande": "résumé court", "qualification": "orchestre", "playbook": "dev-verifie", "plan": [{"etape": "revue design", "agent": "Explore", "mode": "parallele", "modele": "haiku"}], "resultat": "succes", "reprises": 0, "notes": ""}'
```

(JSON aussi accepté sur stdin. `qualification` : `orchestre` | `direct-signale` ;
`resultat` (issue **discriminante** — pas un `succes` réflexe, un journal où tout est
`succes` ne porte aucun signal) : `succes` = livrable produit ET toutes les exigences
explicites de la demande couvertes ET vérifications obligatoires faites **ET, pour un
livrable consommé par l'utilisateur, validé PAR l'utilisateur sur l'artefact exact** ;
`en-attente-validation` = livrable produit et auto-vérifié mais **pas encore validé par
l'utilisateur** — état par défaut d'un livrable utilisateur tant que le « OK » n'est pas
donné (ne JAMAIS logger `succes` sur une auto-évaluation d'un livrable que l'utilisateur
doit approuver) ; `partiel` = au moins une exigence non livrée, une vérification
obligatoire sautée, OU une escalade non résolue à la remise (commit/PR bloqué renvoyé à
l'utilisateur) ; `echec` = objectif non atteint / run abandonné ; `playbook` : nom du
playbook instancié ou `null` en composition libre. Les exécutions directes ne se
journalisent pas — le journal trace les orchestrations, pas la conversation.)

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
