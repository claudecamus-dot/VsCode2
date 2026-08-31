---
name: agent-orchestrator
description: Orchestrateur des agents et skills du projet — qualifie une demande de travail, compose un plan (cascade / parallèle / asynchrone, modèle par étape), l'exécute en s'appuyant sur le catalogue et les données du superviseur, puis journalise le run au moment du commit. Lance réellement du multi-agents via l'outil Agent (fan-out parallèle dans un même message, arrière-plan notifié, SendMessage pour continuer un sous-agent, isolation worktree contre les écritures concurrentes, modèle par agent, revue en contexte frais jamais confiée à un fork) et dispose de 8 sous-agents porteurs — bmad-revue, bmad-doc, bmad-recherche, bmad-cadrage, bmad-livraison, veille-agentic, agent-supervisor, agent-orchestrator. Sait APPLIQUER une recommandation arbitrée du superviseur (findings de .claude/supervision/diagnostic.json) via dev-verifie, export-ppt-verifie ou evolution-flotte selon la cible, puis enregistrer l'arbitrage dans arbitrages.json. Traite la commande « adopte <trouvaille> », verbe d'arbitrage de la veille, et tient la cadence de celle-ci. Route les 39 skills BMAD installées par besoin détecté (§ 2 quinquies : d'office pour les passes de lecture/critique qui ne rendent qu'un rapport ; annoncé-puis-validé dès qu'une skill coûte cher OU écrit un fichier réel). Atteignable de trois façons : cette skill, le sous-agent agent-orchestrator, ou la commande /orchestre. À charger quand une demande implique plusieurs étapes/agents, des vérifications obligatoires, ou « applique la reco du superviseur » — ou quand la grille injectée par le hook UserPromptSubmit route ici.
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

### 2 bis. Agir sur une recommandation du superviseur

Le superviseur *propose* (findings de `.claude/supervision/diagnostic.json`, chacun avec
un champ `proposition`), l'utilisateur *arbitre*, **l'orchestrateur applique la version
validée** — c'est la boucle propose→arbitre→applique. Quand la demande est « applique la
reco X », « traite le finding Y », « corrige le point Z » (ou plus large : « traite
tout ») :

1. **Lire les propositions** dans `.claude/supervision/diagnostic.json` — les mêmes que le
   bloc TODO agents de `docs/wiki/technical/agents-supervision.md` et de `docs/wiki.html`.
   Chaque finding porte `categorie`, `cible`, `priorite`, `titre`, `preuve`,
   `recommandation`, `proposition` (parfois `re_challenge`). Les seules catégories
   acceptées par `write_diagnostic.py` sont celles de l'**usage des agents** :
   `ko-repete`, `inefficacite`, `agent-mort`, `interaction`, `verification-manquante`,
   `non-convergence`, `autre` — ici le superviseur diagnostique le dispositif agentic, pas
   un référentiel de pratiques externe. La `proposition` amende donc typiquement un skill,
   un playbook, un contrat d'étape, un hook, un script de supervision, ou met un agent en
   sommeil. Cibles réelles du diagnostic courant : `scan_transcripts.py`, `CLAUDE.md`,
   `bmad-code-review` — des fichiers de ce dépôt, pas d'un dépôt tiers.
2. **N'appliquer QUE l'arbitré.** Si l'utilisateur n'a pas explicitement validé, présenter
   la proposition et demander l'arbitrage — jamais d'auto-application, même « évidente ».
   « Traite tout » vaut arbitrage de l'ensemble des findings ouverts.
3. **Choisir le véhicule d'exécution** selon la cible de la proposition :
   - cible **applicative** (`app/routers/`, `app/services/`, un template Jinja, du CSS/JS)
     → instancier **`dev-verifie`** : la proposition devient du code, elle passe par les
     tests et la vérification réelle, jamais par une édition sèche.
   - cible **export de deck** (`app/services/pptx_export/**`, `pptx_deck.py`) →
     **`export-ppt-verifie`**, `pptx-verify` compris — un correctif de génération ne se
     déclare pas fait sur un parseur tolérant.
   - cible **export PDF** (reportlab) → même exigence de mesure, jamais un « ça se génère
     sans erreur » : la skill `pdf-quality` est installée ici et son vérificateur se lance
     `py .claude/skills/pdf-quality/scripts/pdf_verify.py <sortie.pdf> --retrait-citation-mm 3.53`.
     Ce retrait de citation est **arbitré et conservé** : sans lui, le vérificateur signale à
     tort un bord gauche multiple sur les PDF de ce projet.
   - cible **dispositif local** (un skill de `.claude/skills/`, un playbook, un hook, un
     script de `.claude/supervision/` ou `.claude/orchestration/`) → édition directe suivie
     de la vérification adaptée : `py -m py_compile` sur un script, JSON rechargé sur une
     donnée, test ciblé du fichier touché.
   - cible **dans un AUTRE dépôt** (le hub qui source une partie de ce dispositif, un
     projet voisin) → playbook **`evolution-flotte`**, présent dans
     `.claude/orchestration/playbooks/` : cadrage sur l'état réel de la cible → modif
     scopée → vérifs → **commit limité au périmètre**, sans jamais embarquer ni écraser du
     travail non commité qui n'est pas le nôtre.
4. **Enregistrer l'arbitrage** une fois appliqué, dans
   `.claude/supervision/arbitrages.json` — une entrée `{cible, decision, date, source,
   categories}`, `cible` = celle du finding, `decision` = « ACCEPTÉ + APPLIQUÉ : <ce qui a
   été fait> ». Le scan (`py .claude/supervision/scan_transcripts.py`) clôt alors le
   finding et cesse de l'afficher en alerte. Un finding **refusé** s'y note aussi
   (« REFUSÉ : <raison> ») pour ne pas le re-proposer ; `refuser_arbitrage.py` fait
   l'écriture, mais sa régénération de page pointe un `scripts/scan_projets.py` **absent de
   ce dépôt** — relancer le scan à la main derrière lui.

Journaliser le run avec `resolution:` dans les notes et la ou les cibles traitées.

### 2 ter. Lancer réellement du multi-agents (mécanique de l'outil Agent)

Les modes de la table ci-dessus se CONCRÉTISENT par l'outil `Agent` (Task) — pas par une
description d'intention. Les gestes exacts :

- **Fan-out parallèle** : plusieurs appels `Agent` **dans le même message** = lancement
  concurrent. Un appel par message = cascade involontaire (le 2e ne part qu'à la fin du
  1er). Chaque sous-agent part avec un contexte VIERGE : son prompt doit être un **brief
  autoportant** — chemins absolus, exigence vérifiable, format de réponse attendu
  (« données brutes », pas de prose), et le rappel qu'il rend un RÉSULTAT (son texte
  final), pas un message à l'utilisateur.
- **Arrière-plan** : `run_in_background: true` (défaut) rend la main immédiatement, la
  notification arrive à la fin — ne jamais écrire le résultat à sa place ; s'il faut le
  résultat pour continuer, `run_in_background: false` (synchrone).
- **Continuer un sous-agent** : `SendMessage` avec son agentId (rendu à la fin de son run)
  relance LE MÊME agent avec son contexte intact — toujours préférable à re-briefer un
  agent neuf quand on itère sur le même sujet (revue → contre-revue).
- **Modèle par agent** : paramètre `model` de l'appel (haiku/sonnet/opus) selon la
  politique § modèle ci-dessous — le fan-out mécanique en haiku, la revue en sonnet, le
  structurant en opus ; omis = modèle de la session.
- **Écritures concurrentes** : deux sous-agents ne modifient JAMAIS les mêmes fichiers en
  parallèle — sur ce projet, le piège classique est deux agents sur `app/routers/` ou sur
  `tests/`. Si le plan l'exige, `isolation: "worktree"` (worktree git jetable par agent) ou
  sérialiser les étapes d'écriture ; les lectures/analyses, elles, se parallélisent sans
  autre limite que ≤ 4.
- **Type d'agent** : `Explore` pour chercher/inventorier (lecture seule, économe),
  `general-purpose` pour agir (outils complets), `Plan` pour concevoir une stratégie
  d'implémentation. Le type se choisit par la nature de l'étape, pas par habitude.
  **Types maison** — les 8 de `.claude/agents/`, tous porteurs de l'outil `Skill`, donc
  leurs invocations sont *comptées* par l'étage 1 :

  | Sous-agent | Pour | Modèle |
  | --- | --- | --- |
  | `bmad-revue` | Revue de code/diff, critique adversariale, cas limites, revue rédactionnelle, checkpoint, rétrospective (§ 2 quinquies) | opus |
  | `bmad-doc` | Documentation brownfield, index, découpage, rédaction technique | sonnet |
  | `bmad-recherche` | Recherche technique / domaine / marché, idéation | sonnet |
  | `bmad-cadrage` | Brief, PRD, PRFAQ, architecture, UX, règles projet — régime **proposé** | opus |
  | `bmad-livraison` | Epics/stories, sprint, implémentation d'une story — régime **proposé** | sonnet |
  | `veille-agentic` | Veille agentic sur cadence (§ 2 sexies) — écrit `veille.json`, n'adopte rien | sonnet |
  | `agent-supervisor` | Diagnostic étage 2 délégué **sur ce projet** — écrit `diagnostic.json`, n'applique rien | opus |
  | `agent-orchestrator` | Cet orchestrateur lui-même : déléguer une orchestration ENTIÈRE hors du contexte principal | hérité |

- **Revue en contexte frais : jamais un `fork`.** Quand le plan porte une revue avant
  commit — `dev-verifie`, `export-ppt-verifie` et `evolution-flotte` la portent tous les
  trois — le relecteur doit être un sous-agent **standard et isolé** (`Explore`,
  `general-purpose`, ou un porteur maison) : **jamais un `fork`, jamais `/subtask`**. Le
  fork **hérite du contexte de l'appelant** ; un relecteur qui a déjà vu le raisonnement de
  l'implémenteur ne révise plus en contexte frais, et l'étape devient décorative sans que
  rien ne le signale. Le relecteur ne reçoit que le diff et les exigences de la demande.
- **Consolidation obligatoire** : un fan-out sans étape de synthèse qui recroise les
  résultats (doublons, contradictions, trous) n'est pas un plan — c'est du bruit distribué.
  La consolidation est une étape à part entière du plan journalisé.

**Sous-agents ou agent team ?** (doc officielle Anthropic.) Les sous-agents restent le
DÉFAUT : ils rendent un résultat au demandeur et ne se parlent jamais entre eux — coût bas,
contexte principal préservé. Une *agent team* (équipiers qui se messagent via une liste de
tâches partagée) ne se justifie que si les travailleurs doivent **se coordonner ou se
contredire entre eux** : revue multi-angles avec débat, hypothèses concurrentes qu'on veut
voir se réfuter, chantier transverse où chacun possède sa couche. Elle est
**expérimentale, désactivée par défaut** (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) et son
coût croît linéairement avec le nombre d'équipiers — chacun est une session Claude
complète. Garde-fous officiels si elle est retenue : 3-5 équipiers, 5-6 tâches par
équipier, **partition stricte des fichiers** (deux équipiers sur le même fichier =
écrasement), démarrer par des tâches de recherche/revue. Le plan journalisé doit
**justifier le véhicule choisi** — un fan-out de sous-agents non justifié comme team est le
défaut attendu, pas un manque. Ici, `revue-design-parallele` est déjà un fan-out éprouvé :
il n'a pas besoin d'une team.

**Fan-out manuel ou dynamic workflow ?** Le fan-out de l'outil `Agent` reste le défaut : il
tient dans un message, se lit dans le plan journalisé, et couvre les ≤ 4 sous-agents qu'un
chantier d'ici dispatche d'ordinaire (revue multi-angles d'un deck, exploration de `app/`
couche par couche). Passé cette taille, il montre ses limites — les appels sont réécrits à
la main à chaque relance, et rien ne recroise mécaniquement les résultats. Un **dynamic
workflow** (outil `Workflow`, skill `workflow-authoring`) est un script réexécutable qui
orchestre des dizaines de sous-agents et **rejoue les étapes inchangées depuis leur
cache** : il se justifie quand (1) le plan dépasse une poignée de sous-agents, (2) les
résultats doivent être **vérifiés les uns contre les autres** (chaque défaut d'une passe
re-vérifié par un agent dédié), ou (3) la même campagne sera relancée après correction —
typiquement une passe qualité sur toutes les slides d'un deck, rejouée après le fix. Deux
garde-fous : il ne se lance que sur **opt-in explicite de l'utilisateur**, jamais sur la
seule initiative de l'orchestrateur, parce qu'il coûte cher ; et le plan journalisé doit
**dire pourquoi** ce véhicule a été choisi, exactement comme pour les agent teams.

### 2 quater. La commande `adopte` — arbitrer une trouvaille de veille

`adopte <trouvaille>` (ou « adopte la pratique X », « adopte l'entrée Y ») est **le verbe
d'arbitrage de la veille**, symétrique de « applique le finding » pour le diagnostic. La
veille *propose* (entrées de `.claude/veille/veille.json`, statut `nouveau`/`etudie`),
l'utilisateur *adopte*, **l'orchestrateur applique** — puis trace. Une entrée se refuse de
la même façon (« écarte X »), avec sa raison.

**Prérequis mesuré : il n'y a rien à adopter aujourd'hui.** `.claude/veille/` n'existe pas
sur ce dépôt — la veille n'y a jamais tourné (§ 2 sexies). Cette section décrit donc ce qui
se passe **après** le premier lancement ; d'ici là, une demande d'adoption doit renvoyer au
lancement de la veille, jamais produire une adoption inventée.

**Ce que la commande déclenche, dans l'ordre :**

1. **Retrouver l'entrée** dans `.claude/veille/veille.json` par titre, url ou mot-clé.
   Ambiguë ou absente → demander laquelle, ne jamais deviner : adopter la mauvaise pratique
   coûte plus cher que la question.
2. **Cadrer sur l'état RÉEL** : la trouvaille peut être déjà satisfaite ici, ou l'être
   autrement. Vérifier dans le code avant d'écrire quoi que ce soit — une pratique déjà en
   place ne s'« adopte » pas, elle se constate. Correction minimale > refonte.
3. **Appliquer les deux débouchés** que porte l'entrée, quand ils existent :
   - `regle_proposee` → **règle d'analyse**. Ce projet n'a pas de référentiel de critères
     séparé : la règle se pose là où ses règles vivent réellement — une ligne de la table
     des vérifications obligatoires (§ 4), une règle de `CLAUDE.md`, une étape de playbook,
     ou un hook de `.claude/hooks/`. Si elle est mesurable à froid, l'**outiller**
     (marqueur dans `.claude/supervision/scan_transcripts.py`, ou test de non-régression)
     plutôt que la laisser déclarative : une règle que rien ne mesure retombe au premier
     oubli.
   - `action_corrective` → **le correctif lui-même** : sur ce dépôt, édition puis la
     vérification de § 4 qui correspond à la cible (test ciblé, rendu réel regardé,
     `pptx-verify`) ; sur un autre dépôt, via le playbook `evolution-flotte` (cadrage réel →
     modif scopée → vérifs → commit scopé).
   Une entrée de type `agent`/`skill`/`outil`/`framework` n'a pas ces champs : l'adoption y
   est une **installation ou une greffe**, à cadrer explicitement — jamais un `git clone`
   exécuté sans lecture préalable.
4. **Vérifier par les faits**, comme tout chantier : tests réels du projet, rendu regardé si
   écran ou deck, mesure re-jouée si la règle est outillée.
5. **Tracer**, deux écritures distinctes et toutes deux obligatoires :
   - `statut` de l'entrée → `adopte` (ou `ecarte` + raison), avec en fin de `pertinence` un
     crochet daté disant ce qui a réellement été fait ;
   - une entrée dans `.claude/supervision/arbitrages.json` à la cible `veille:<slug>` —
     sans elle, la trouvaille reste en attente de décision et sera re-proposée.
6. **Journaliser** le run avec `resolution: adoption <nom>` dans les notes.

**Garde-fous.** Jamais d'exécution de code téléchargé pendant l'adoption (la veille
observe, l'adoption intègre du code LU). Jamais d'activation d'une capacité expérimentale
par défaut : documenter le critère de choix vaut adoption, poser la variable
d'environnement est une décision séparée. Et l'adoption reste un **arbitrage utilisateur** :
appliquer une trouvaille de sa propre initiative viole propose→arbitre→applique aussi
sûrement qu'appliquer un finding non validé.

### 2 quinquies. Router vers les skills BMAD

BMAD-METHOD est installé ici en v6.10.0 (core + bmm) : **39 skills** couvrant cadrage
produit, conception, planification, implémentation, revue, documentation et recherche.
Elles ne sont pas réservées à une demande explicite : **elles font partie du workflow**, et
c'est l'orchestrateur qui les déclenche quand le besoin matche — sans que l'utilisateur ait
à les nommer.

**Deux régimes de déclenchement, deux critères cumulatifs : le coût ET l'écriture.**

- **D'office** — la skill est bornée *et* ne produit qu'un rapport : une passe de lecture ou
  de critique, sans cascade et sans toucher au disque. L'orchestrateur l'insère dans le plan
  comme n'importe quelle autre étape, sans demander.
- **Proposé** — la skill remplit au moins l'une de ces conditions :
  1. elle ouvre un **workflow multi-étapes** produisant des artefacts structurants (PRD,
     architecture, epics, code) ou mobilise plusieurs personas — le coût ;
  2. elle **écrit, déplace ou restructure un fichier réel** — même vite, même bien.
  L'orchestrateur **annonce l'étape et attend le feu vert**.

Le second critère n'est pas cosmétique : sans lui, `bmad-document-project`,
`bmad-index-docs`, `bmad-shard-doc` et `bmad-agent-tech-writer` partiraient sans arbitrage
alors qu'elles écrivent dans le dépôt. La règle propose→arbitre→applique ne parle pas de
coût, elle parle d'auto-application : **une écriture non arbitrée la viole, qu'elle prenne
dix secondes ou dix minutes.** Le régime ne juge donc pas la qualité d'une skill — il dit
qui autorise la dépense *et* qui autorise le diff.

**Où ces skills ont un objet.** Ce projet produit un livrable applicatif : une application
FastAPI + Jinja2 + HTMX (transcription et IA locales), un export PPTX (python-pptx, template
OCTO) et un export PDF (reportlab). **Toutes** les familles ont donc un objet ici même, sur
ce dépôt — cadrage et conception en amont d'une feature, planification et implémentation sur
`app/`, revue et documentation en aval — via les playbooks locaux `dev-verifie` et
`export-ppt-verifie` et leurs vérifications obligatoires (§ 4).

**Une skill maison prime sur une skill BMAD générique dès que le canal est celui du deck ou
de l'écran.** Ne pas plaquer un pattern générique là où le projet a déjà son outil :
`swot-matrix` et `priority-matrix` avant de dessiner une matrice, `deck-design-library`
avant de choisir la forme d'une slide, `pptx-framed-image` pour un cadre photo,
`deck-design-review` et `slide-text-polish` pour la revue d'un deck, `pdf-quality` pour
générer et surtout **mesurer** un PDF (`--retrait-citation-mm 3.53` sur les PDF d'ici),
`run-dev-server` pour regarder un écran. `bmad-review-adversarial-general` critique un plan ou une décision, pas
une slide.

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
| Recherche technique sur une techno, un framework, une archi | `bmad-technical-research` | `bmad-recherche` | d'office |
| Recherche sur un domaine métier ou un secteur | `bmad-domain-research` | `bmad-recherche` | d'office |
| Recherche marché, concurrence, clients | `bmad-market-research` | `bmad-recherche` | d'office |
| Idéation cadrée sur un problème ouvert | `bmad-brainstorming` | `bmad-recherche` | d'office |
| Documenter le projet existant (brownfield) pour le contexte IA | `bmad-document-project` | `bmad-doc` | proposé |
| Créer / rafraîchir l'index d'un dossier de docs | `bmad-index-docs` | `bmad-doc` | proposé |
| Découper un document trop gros en sections navigables | `bmad-shard-doc` | `bmad-doc` | proposé |
| Rédiger ou curer de la documentation technique (Paige) | `bmad-agent-tech-writer` | `bmad-doc` | proposé |
| Brief produit initial | `bmad-product-brief` | `bmad-cadrage` | proposé |
| PRD — créer, éditer ou valider | `bmad-prd` | `bmad-cadrage` | proposé |
| PRFAQ Working Backwards (concept client-first) | `bmad-prfaq` | `bmad-cadrage` | proposé |
| Durcir une idée par interrogation adverse | `bmad-forge-idea` | `bmad-cadrage` | proposé |
| Analyse métier et exigences (Mary) | `bmad-agent-analyst` | `bmad-cadrage` | proposé |
| Cadrage produit conduit par un PM (John) | `bmad-agent-pm` | `bmad-cadrage` | proposé |
| Architecture technique (colonne d'invariants) | `bmad-architecture` | `bmad-cadrage` | proposé |
| Conception système conduite par un architecte (Winston) | `bmad-agent-architect` | `bmad-cadrage` | proposé |
| Specs UX, patterns d'interaction (écrans HTMX, parcours) | `bmad-ux` | `bmad-cadrage` | proposé |
| Design UX/UI conduit par une designer (Sally) | `bmad-agent-ux-designer` | `bmad-cadrage` | proposé |
| Écrire les règles IA du projet (project-context.md) | `bmad-generate-project-context` | `bmad-cadrage` | proposé |
| Table ronde multi-personas / focus group | `bmad-party-mode` | `bmad-cadrage` | proposé |
| Customiser une skill BMAD (party, personas, overrides) | `bmad-customize` | `bmad-cadrage` | proposé |
| Découper des exigences en epics et stories | `bmad-create-epics-and-stories` | `bmad-livraison` | proposé |
| Écrire une story prête à implémenter | `bmad-create-story` | `bmad-livraison` | proposé |
| Construire le plan de sprint depuis les epics | `bmad-sprint-planning` | `bmad-livraison` | proposé |
| État du sprint, risques à surfacer | `bmad-sprint-status` | `bmad-livraison` | proposé |
| Changement significatif en cours de sprint | `bmad-correct-course` | `bmad-livraison` | proposé |
| Vérifier que PRD/UX/archi/epics sont prêts pour l'implémentation | `bmad-check-implementation-readiness` | `bmad-livraison` | proposé |
| Implémenter une story déjà spécifiée | `bmad-dev-story` | `bmad-livraison` | proposé |
| Boucle de développement non surveillée (une itération) | `bmad-dev-auto` | `bmad-livraison` | proposé |
| Exécution d'histoire conduite par un dev senior (Amelia) | `bmad-agent-dev` | `bmad-livraison` | proposé |

**`bmad-customize` est routable, en régime proposé.** Elle écrit un fichier réel
(`_bmad/custom/<skill>.toml`) : l'orchestrateur annonce l'étape et attend le feu vert, comme
pour toute écriture. Elle a déjà servi ici — `_bmad/custom/bmad-code-review.toml` porte
l'override arbitré le 2026-07-23 (constat superviseur sur la 2ᵉ vague des chasseurs
adversariaux). Deux règles tiennent : une customisation passe **par la skill**, jamais par
une édition manuelle des fichiers de configuration BMAD (réécrits à chaque mise à jour du
framework) ; et la migration vers une v7, quand elle sortira, redevient une décision à part
entière — les overrides écrits en v6 devront être re-vérifiés à ce moment-là.

**Non installées ici** — ne pas les router, la skill n'existe pas dans ce dépôt :
`bmad-spec`, `bmad-quick-dev`, `bmad-qa-generate-e2e-tests` (pour des tests e2e, passer par
`dev-verifie` et la suite `tests/` du projet). Les variantes dépréciées par BMAD
(`bmad-create-prd`, `bmad-edit-prd`, `bmad-validate-prd`, `bmad-create-architecture`) ne
sont pas installées non plus : si l'utilisateur les nomme, router vers `bmad-prd` /
`bmad-architecture` et le dire.

**Faut-il toujours passer par le sous-agent porteur ?** Non — le porteur sert à *isoler* un
travail BMAD long dans un contexte à lui, ou à en paralléliser plusieurs. Quand la session
principale est déjà sur le sujet et que la skill est bornée (`bmad-advanced-elicitation` sur
ce qu'on vient d'écrire, `bmad-help` pour trancher), l'invoquer **inline** est plus direct
et compte pareil au tableau de bord. La règle :
> une skill BMAD dont le travail tient dans la conversation courante s'invoque inline ;
> une skill qui va lire beaucoup de fichiers ou produire un gros artefact part en
> sous-agent, brief autoportant compris (§ 2 ter).

**Porteur indisponible : dégrader, jamais abandonner l'étape.** Le registre des types
d'agents est chargé au **démarrage de session** — un sous-agent créé ou modifié pendant la
séance peut ne pas être adressable tout de suite. Un `subagent_type` invalide ne justifie
donc pas de sauter l'étape :

1. **Invoquer la skill inline** (outil `Skill`) — le travail est fait, et l'invocation est
   comptée exactement pareil par l'étage 1.
2. Si l'isolement du contexte est vraiment nécessaire, dispatcher `general-purpose` avec le
   contenu du mandat du porteur en brief, **et les interdits recopiés explicitement** (un
   `general-purpose` a tous les outils : les garde-fous structurels du porteur — par exemple
   l'absence de `Write`/`Edit` chez un relecteur — deviennent de simples consignes, ce qui
   doit être dit dans le brief et dans le journal).
3. **Tracer** dans les notes du run : `resolution: porteur-indisponible <nom>`. C'est le
   signal qui dira au superviseur si le problème est ponctuel ou structurel.

### 2 sexies. Lancer la veille sur cadence — chercher les pistes qu'on n'a pas demandées

Les findings du superviseur et les demandes de l'utilisateur ne couvrent qu'un angle : ce
que le projet sait déjà de lui-même. La veille couvre l'autre — **les pratiques agentic,
agents, skills et playbooks publics que le dispositif ignore encore**. Un projet peut être
parfaitement cohérent avec lui-même et en retard de six mois sur l'état de l'art. C'est
pourquoi la veille n'attend pas une demande : elle a une cadence, et c'est l'orchestrateur
qui la tient.

**État mesuré : la veille n'a JAMAIS tourné ici.** `.claude/veille/` n'existe pas — aucun
`veille.json`, donc aucune trouvaille. La skill `veille-agentic`, son sous-agent porteur et
le hook de cadence `remind_veille_agentic.py` sont pourtant tous câblés : le hook imprime
« Aucune veille enregistree » à **chaque** session, c'est-à-dire un rappel qui a cessé
d'être un signal parce qu'il n'a jamais été suivi d'effet. Trois conséquences directes : la
commande `adopte` (§ 2 quater) n'a rien à arbitrer ; l'orchestrateur ne peut pas prétendre
que « la veille est fraîche » ; et le premier lancement est à **proposer** au premier
créneau où il ne coupe pas un chantier — pas au milieu d'une boucle de deck.

**Quand la lancer** (l'un de ces déclencheurs suffit) :

| Déclencheur | Vérification avant de lancer |
| --- | --- |
| Le hook SessionStart signale « Aucune veille enregistree » ou « veille a lancer ou perimee » (> 3 j) | Rien à vérifier — le hook a déjà lu (ou constaté l'absence de) `derniere_veille` |
| Fin d'un chantier, avant de considérer l'incrément livré | Lire `.claude/veille/veille.json` : si `derniere_veille` < 3 j, **ne pas relancer** — dire qu'elle est fraîche |
| Avant de créer un agent, une skill ou un playbook maison | Toujours : réécrire ce qui existe en public, mieux maintenu, est une perte sèche |
| Le superviseur a besoin de l'état de l'art pour prouver un finding | Synchrone dans ce cas (le diagnostic attend le résultat) |
| L'utilisateur demande des pistes d'amélioration, des évolutions, des bonnes pratiques | Toujours : c'est la demande même de la veille |

**Comment la lancer.** Sous-agent `veille-agentic` (outil `Agent`), qui porte l'outil `Skill`
et charge la méthode lui-même :

- **En arrière-plan par défaut** (`run_in_background: true`) : une veille lit beaucoup de
  sources et dure. Elle n'a aucune dépendance avec le chantier courant, donc elle ne doit
  jamais le bloquer — mais **attendre la notification** avant d'en parler : ne jamais écrire
  à sa place ce qu'elle « aura trouvé » (règle du mode asynchrone, § 2 ter).
- **Synchrone** (`run_in_background: false`) uniquement quand le résultat est nécessaire pour
  continuer — typiquement quand `agent-supervisor` l'appelle pour prouver un écart.
- **Un seul chantier de veille à la fois.** Deux veilles concurrentes écriraient toutes les
  deux `veille.json` : écrasement garanti.

**Ce qui suit le retour de la veille**, dans l'ordre — et c'est là que la plupart des
dispositifs de veille meurent :

1. **Ne pas compter sur la page générée pour la rendre visible.** Le scan local
   (`py .claude/supervision/scan_transcripts.py`) régénère bien
   `docs/wiki/technical/agents-supervision.md` et le bloc de `docs/wiki.html`, mais il **ne
   lit pas `veille.json`** et n'en rend aucune section. Les trouvailles n'existent donc que
   dans le fichier — ce qui rend l'étape suivante non facultative.
2. **Présenter les trouvailles à l'utilisateur**, une ligne chacune avec sa `regle_proposee`
   et son `action_corrective`. Elles arrivent en statut `nouveau` : ce sont des
   **propositions**, pas des décisions.
3. **Ne rien adopter de sa propre initiative.** L'adoption est la commande `adopte`
   (§ 2 quater) — un arbitrage utilisateur, tracé dans `arbitrages.json`. Appliquer une
   trouvaille sans arbitrage viole propose→arbitre→applique aussi sûrement qu'appliquer un
   finding non validé.
4. **Surveiller le pourrissement.** Une trouvaille qui reste `nouveau` plus de 7 jours est un
   signal à remonter au superviseur (catégorie `inefficacite`, `cible` = `veille:<slug>`) :
   une règle produite que personne n'a arbitrée a été payée pour rien.

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
| `app/services/pptx_export/**` / `pptx_deck.py` | `pptx-verify` (rendu réel — python-pptx est un parseur tolérant). Cardinalité ou libellé rendus VARIABLES → rendre un cas **non par défaut** |
| `pptx_export/slides_diagnostic.py` (porte `_slide_swot`) | Skill `swot-matrix` chargée AVANT de dessiner (matrice 2×2 réelle, pas quatre cartes) |
| `pptx_export/slides_trajectoire.py` (porte `_slide_matrice_effort_valeur`) | Skill `priority-matrix` chargée AVANT de dessiner (matrice dessinée, jamais un scatter Excel natif) |
| Une slide dessinée ou retouchée, quelle qu'elle soit | Skill `deck-design-library` consultée AVANT (partir de l'intention pour choisir la forme) |
| Cadres photo / images encadrées (têtes de chapitre) | Skill `pptx-framed-image` (prstGeom cloné sur l'image, pas un arrondi PIL) |
| **Livrable consommé par l'utilisateur** (deck exporté, écran) | Produire l'**artefact EXACT qu'il ouvre** (export réel de la route de l'app — `GET …/export/pptx` — **pas** un `build_presentation` maison), le rendre **ENTIER** (toutes les slides, pas 2-3), et le faire **VALIDER par l'utilisateur** avant tout « fait » (évol 2026-07-22, boucle deck non convergente — `feedback-verify-the-real-app-export-all-slides`) |
| Fin d'incrément / avant commit | `revue-increment` en étape terminale |
| Exploration volumineuse | Sous-agent `Explore`, jamais la session principale |
| Skills BMAD | Le régime de § 2 quinquies : **d'office** seulement si la skill est bornée ET ne rend qu'un rapport ; **annoncé et validé** dès qu'elle coûte cher (PRD, archi, epics/stories, code) **ou qu'elle écrit un fichier réel** (documentation, index, découpage). Remplace la règle antérieure — *Uniquement sur demande explicite, via `bmad-help` (statut « à trier »)* — que § 2 quinquies rend caduque |

**Règle de non-convergence (évol 2026-07-22).** Si le MÊME livrable est rejeté par
l'utilisateur **≥ 3 tours** (« toujours KO », « pas traité »), la boucle ne converge pas :
**STOP l'itération à l'aveugle** — ne pas re-deviner le défaut. Reproduire l'artefact
utilisateur exact (§ ligne ci-dessus) ET **demander à l'utilisateur de pointer le défaut
précis** (numéro de slide, capture, écran) avant de retoucher quoi que ce soit. Re-deviner
produit l'oscillation (ex. barre d'accent ajoutée puis retirée, numéro navy-block puis
pill) ; l'oracle, c'est l'utilisateur sur SON artefact.

### 5. Journaliser

**Au moment du COMMIT** (et non « à la fin du run ») — succès **ou** échec — une ligne
dans `.claude/orchestration/runs.jsonl`. Un run sans commit se journalise à sa dernière
étape, comme avant.

Pourquoi ce déplacement (constat superviseur prio 2 du 2026-07-27, arbitré par
l'utilisateur) : une séance longue **enchaîne les demandes** et ne se termine presque
jamais par une action de clôture — la séance du 2026-07-27 a produit 4 commits et
3 demandes multi-étapes sans laisser **aucune** ligne, alors que `runs.jsonl` s'arrêtait
deux jours plus tôt. Le commit, lui, est un point de passage obligé et daté : y accrocher
le journal le rend robuste à une séance interrompue, à un enchaînement de demandes, ou à
un changement de sujet en cours de route. Corollaire : plusieurs commits dans une séance
= plusieurs lignes, ce qui est le comportement voulu (une ligne par unité livrée, pas par
conversation).

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
