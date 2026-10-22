# Règles détaillées de l'orchestrateur — texte déplacé de SKILL.md

<!-- Référence de la skill agent-orchestrator (divulgation progressive, 2026-10-21).
     Paragraphes déplacés VERBATIM depuis SKILL.md ; la règle courte reste dans SKILL.md. -->

## Comité par défaut

**Comité par défaut pour COMPRENDRE** (consigne permanente du 2026-10-04) : pour une étape dont
le travail est de comprendre (diagnostic, audit, revue, proposition de conception ou d'IA,
instruction d'un choix, plan éditorial), l'orchestrateur applique par défaut le comité et
l'annonce en une ligne ; le propriétaire force `comité :` (l'ancien déclencheur `neuronal :` reste accepté) ou `classique :`. **Jamais pour
ÉCRIRE** (un seul rédacteur) ni pour une action mécanique ou irréversible : classique, sans
annonce. Limites mesurées : petits échantillons (10 et 13 tâches), coût environ 2×, vérificateurs
trop accommodants (8 faux positifs sur 44 verdicts), effet non mesuré sur les tâches ouvertes
(les tâches de test avaient un oracle objectif) — essai borné que le journal continue de
mesurer (`bras`, `topologie`, `tache_id`).

## Mécanique de l'outil Agent

- **Parallèle par défaut** : des sujets indépendants se lancent concurrents sans attendre
  d'y être invités (§ 1) ; tout ce qui suit borne ce défaut, il ne le lève jamais.
- **Fan-out parallèle** : plusieurs appels `Agent` **dans le même message** = lancement
  concurrent ; un appel par message = cascade involontaire. Chaque sous-agent part avec un
  contexte VIERGE : son prompt est un **brief autoportant** (chemins absolus, exigence
  vérifiable, format de réponse, rappel qu'il rend un RÉSULTAT). **Au-delà de 4 agents
  isolés (worktree), UN appel `Agent` par message tant que git est calme** (`git status` à
  ~0,2-0,5 s), jamais en rafale : refus de lancement mesurés le 2026-10-04 — 0 % à 2 ou 4
  lancements simultanés, 40 % à 10, 65 % à 40 ; 0 refus en série. Si le brief autorise à
  regarder un rendu : **n'utiliser qu'un serveur déjà en écoute qu'on n'a pas démarré ;
  ne jamais démarrer, redémarrer ni purger un service du dépôt** — sinon « non vérifié au rendu ».
- **Plafond** (mesuré le 2026-10-03, arbitré utilisateur) : **10** sous-agents en
  interactif (on attend le résultat) ; **jusqu'à 30** pour des lots longs (≥ 5 min/agent)
  et indépendants, en arrière-plan, rendu d'une ligne ou par fichier ; **au-delà de 30**,
  `Workflow`. Mesure : paliers 10/20/30 tous exacts, 0 refus de lancement, 0 timeout
  stdin, mais concurrence effective ~10 (retours en vagues) et débit plafonné ~5
  agents/min — au-delà de 10, les agents attendent en file sans aller plus vite.
  Indicateur : timeouts stdin (`refus_stdin.jsonl`), attentes lentes
  (`refus_stdin_attente.jsonl`) et p90 (`py .claude/supervision/convergence.py --historique`).
- **Topologie** : Au-delà de 4 éléments indépendants, outil `Workflow` avec 4 à 6 agents
  concurrents. Jusqu'à 4, fan-out `Agent`. En dessous de 2 éléments indépendants, cascade :
  la topologie suit la tâche. Indicateur : reprises par run. (`Workflow` reste soumis à
  l'opt-in explicite de l'utilisateur ; agent team expérimentale : détail en référence.)
- **Écritures** : Un seul rédacteur par périmètre de fichiers (worktree créé par l'orchestrateur
  depuis main local : `git worktree add <chemin> -b <branche> main` ; `isolation: "worktree"`
  part de `origin/main`, pas de main local) — JAMAIS deux rédacteurs sur les mêmes fichiers, sinon sérialiser. Après chaque
  sortie qui écrit, un vérificateur à contexte vierge la relit : **OBLIGATOIRE** pour un
  changement à risque (sécurité, garde/hook, canon/kit, registre, tout ce qui se propage à
  la flotte), optionnel sinon. Mesure (test en cascade antérieur, test comité du
  2026-10-03) : le vérificateur a attrapé une fausse preuve (1/5) et une régression ;
  il double la durée. Indicateur : défauts trouvés en aval par run.
- **Vérifier sur disque** : un fichier de résultat annoncé par un agent n'existe qu'une fois
  vu (`Test-Path`, taille, parse JSON) — plusieurs agents ont annoncé un fichier jamais écrit,
  ou « git diff est vide » à tort (tests du 2026-10-04). Jamais l'annonce seule.
- **Arrière-plan** : `run_in_background: true` rend la main ; ne jamais écrire le résultat
  à sa place ; s'il faut le résultat pour continuer, `run_in_background: false`.
- **Continuer un sous-agent** : `SendMessage` avec son agentId — préférable à re-briefer
  un agent neuf sur le même sujet (revue → contre-revue).
- **Modèle par agent** : paramètre `model` selon la politique § modèle ci-dessous.
- **Type d'agent** : `Explore` pour chercher, `general-purpose` pour agir, `Plan` pour
  concevoir. Types maison (`.claude/agents/`, porteurs de l'outil `Skill` sauf `scribe`) :
  `bmad-revue` (opus), `bmad-recherche` (sonnet), `veille-agentic` (sonnet),
  `agent-supervisor` (opus), `agent-securite` (opus, à la demande uniquement), `bmad-test`
  (sonnet, écrit seulement sous `_bmad-output/test-artifacts/`), `scribe` (sonnet,
  relecture du français, ne modifie aucun fichier). Quatre porteurs en sommeil depuis le
  2026-09-01 (`.claude/agents-en-sommeil/`).
- **Consolidation obligatoire** : un fan-out sans synthèse qui recroise les résultats
  n'est pas un plan. Chaque étape porte un `etat` (`ok` | `echec` | `non-rendu`, § 5) ;
  un sous-agent qui échoue ou ne rend rien y est porté, jamais absorbé.
- **Non-convergence** : OUTILLÉE — `py .claude/supervision/convergence.py` (p95, salles en
  vol, `--historique`) et le hook `guard_convergence_salles.py`. Passé 3 à 5× le p95,
  vérifier le disque puis `TaskStop` et relancer — jamais fabriquer un résultat. Avant de
  dispatcher sur un dépôt distant : au repos (deux `git status --porcelain` espacés) **et
  pas en usage** (processus et ports du dépôt) — un port actif = aucune étape qui lance,
  redémarre ou purge un service.
- **Un seul sous-agent capable avant un fan-out** : écrire dans le plan pourquoi UN
  sous-agent ne suffirait pas. **Le relecteur n'est pas l'auteur** : `model` différent
  quand c'est possible ET une vérification déterministe (mutation vue rouge).
- **Aucun agent/skill ne couvre le besoin ?** Mémoire git
  (`py .claude/orchestration/git_agents_inventory.py`), restauration proposée, puis
  évolution ou création via `skill-creator` — toujours arbitré ; noter `resolution:` au run.

## Rapports

**Décision éditoriale rapportée = AVANT / APRÈS côte à côte** (2026-09-30). Tout compte
rendu d'une décision éditoriale de l'utilisateur montre le texte AVANT et le texte APRÈS
côte à côte, l'APRÈS **extrait de l'artefact que l'utilisateur ouvre réellement** (le
`.docx` relu via python-docx, ou le HTML servi), avec son emplacement exact (onglet,
section). *Pourquoi* : le 2026-09-30, une décision de l'utilisateur sur la note d'auteur
de l'article 2 a été appliquée mais pas perçue — le rapport ne montrait ni l'avant ni
l'après. Mémoire du hub `verifier-avec-l-oracle-utilisateur`.

**Un rapport à l'utilisateur s'ouvre sur « À trancher »** (2026-09-30) : un bloc de
**3 lignes au plus** listant ce qui attend sa décision (ou « rien »), avant tout le
reste. *Pourquoi* : dans les rapports longs, les décisions en attente se perdaient.

## Qualifier : journaliser seulement correction ou bug

- **Journaliser (`log_run.py`) — uniquement une correction ou un bug traité.** Arbitrage
  utilisateur du 2026-09-07 : « ne prendre en compte à titre de run que les corrections et
  bugs ». Un run journalisé est un livrable de correction : correctif de code, bug traité,
  dette remboursée — avec sa preuve (commit, test) et, s'il ferme un finding, l'arbitrage de
  clôture qui va avec. Tout le reste, même orchestré — état des lieux, propagation de canon,
  réception d'un diagnostic ou d'une veille, reprise de travaux, cadrage, rapport — ne
  s'inscrit PAS dans `runs.jsonl` (mesure du 2026-09-07 : [journal](references/journal.md)).

## Qualifier : parallèle par défaut

- **Parallèle par défaut** (consigne permanente du propriétaire, 2026-10-04 : « inscrit par
  défaut dès que c'est possible de traiter les sujets en parallèle, voire en mode comité »).
  Quand une demande contient plusieurs sujets indépendants (aucune dépendance de données,
  périmètres de fichiers disjoints), les lancer EN PARALLÈLE en arrière-plan dans le même plan,
  sans attendre qu'on le demande, et l'annoncer en une ligne. Garde-fous inchangés : § 2 ter
  (un seul rédacteur par périmètre, plafonds, UN appel `Agent` par message au-delà de 4 agents
  isolés, vérification sur disque). Dépendance de données ou même fichier : cascade.

## Qualifier : aller-retour

**Un aller-retour sur un livrable pas encore validé n'est jamais une nouvelle orchestration**
(38 runs `en-attente-validation` flotte-wide au 2026-09-07, l'essentiel du motif « refais
la slide 3 »). C'est le cas « correction en cours de tâche » ci-dessus, donc exécution
directe, jamais rejournalisé : le run déjà ouvert reste `en-attente-validation` jusqu'à
validation (`--solde`) ou jusqu'à une demande qui change réellement de sujet.

## 1 bis : mesure

Mesuré sur `runs.jsonl` le 2026-09-12 (157 runs) : 10 demandes « reprendre / relancer les
travaux » et 20 « traiter les findings/écarts », alors que le hook SessionStart annonçait
déjà tout (reliquat non commité, commits non poussés, `point_du_jour.py`).

## 1 bis : règle

**Règle** : quand le hook SessionStart signale un reliquat non commité ou des findings/
trouvailles sans arbitrage, et que le premier message de l'utilisateur ne les mentionne pas
déjà, les traiter (ou au minimum les proposer explicitement) AU PREMIER TOUR de la session —
avant, ou en même temps que, la nouvelle demande. Le signal du hook EST la demande. Ça ne
dispense d'aucune des étapes qui suivent (qualifier, composer, valider si le geste est
coûteux ou irréversible).

## 1 ter : définitions

Deux façons de traiter une action, à ne pas confondre avec les modes d'exécution
(synchrone/parallèle/asynchrone) ci-dessus. **Classique** : 1 agent par tâche, défaut,
appliqué sans annonce. **Comité** (essai borné, conseil de flotte du 2026-10-04) : **1 bras
+ escalade** — 1 bras Sonnet à brief amélioré, un 2e bras seulement si le 1er est sans preuve
valide (jamais 2 bras systématiques), consolidation pondérée par la preuve. Une preuve
rejouable par script est tranchée par le script ; le vérificateur à contexte vierge ne reste
que pour ce qu'un script ne rejoue pas. Pas d'Opus dans un bras de diagnostic (verdict faux
4 fois sur 6), et toute prétention « corrigé » / « déjà corrigé » cite le commit et une mutation
vue rouge (clause 9). **La redondance sert à
comprendre (diagnostiquer, prouver), jamais à écrire.**

## Clauses obligatoires 1 à 9 (texte long)

1. **Section `FAITS TRANSMIS`** — chaque chiffre porte la commande qui l'a produit, ou
   « non vérifié ».
2. **Ligne de sortie `FAITS DU BRIEF INFIRMÉS`** — jamais remplacée par une ligne d'entrée
   « vérifie mes faits » (placebo mesuré).
3. **Cible énumérée, jamais décrite**, et le hors-périmètre explicite.
4. **AMBIGUÏTÉ** — non couvert → l'inspecter dans le code réel ; non connaissable →
   `Information insuffisante` en sortie, jamais supposé ; et l'interruption explicite :
   `SendMessage` vers `main`.
5. **`DONE WHEN`** — condition d'arrêt ET critères de qualité nommés et vérifiables
   (gabarits : `QUALITY CRITERIA:`), plus un budget de longueur (1000-2000 tokens),
   sauf revue de sécurité ou audit, qui exigent le détail complet.
6. `PROVENANCE` — voir ci-dessous.
7. `BUDGET :` — voir ci-dessous.
8. **NON-RÉGRESSION** — pour chaque fichier touché, `grep -rl "<basename>" tests/` et
   rejouer TOUTES les suites trouvées avant « fini » (commande + compte) ; sans run = `partiel`.
9. **MUTATION** — toute prétention « corrigé/testé/couvert » (correctif, clôture, verdict
   « déjà corrigé »), tout modèle, cite une mutation sur une COPIE qui rend le test cité
   rouge ; un test vert sans mutation vue rouge n'est pas une preuve. Indicateur fixé
   avant : part des rapports d'exécutant citant une mutation vue rouge (base 2026-10-03 :
   0/5 Sonnet standard, 4/5 brief amélioré) et suites rejouées 5/5 contre 0/5.

## Table des budgets : prose

**Table des budgets (règle, mesurée le 2026-10-04 sur `usage.jsonl`, 729 agents general-purpose
appariés lancement/arrêt)** : `BUDGET = 2 × médiane mesurée de la classe`, jamais un chiffre d'instinct
(sur les agents lancés dès le 2026-10-03 avec un `BUDGET :` déclaré, 220 sur 407 (54 %) ont dépassé leur budget ; parmi les seuls budgets de 10 min, 119 sur 146 (81 %)). Le détecteur `convergence.py` lit cette table (`BUDGETS_CLASSE_MIN`) : seuil non convergent = max(5 × p95 de la classe, 2 × budget de la classe), repli sur le p95 global pour une classe inconnue.

## Multiplicateur de charge

Multiplicateur de charge : ×3 dès que 20 agents ou plus tournent en même temps (classe 2 mesurée :
médiane 5,6 min à moins de 5 agents concurrents, 16,3 min à 20 ou plus, soit ×2,9 ; ×2 entre 5 et 19,
médiane 11,5 min). Une tâche qui n'entre dans aucune classe se range dans la plus longue, jamais sous
7 min. Non classé : les lancements sans `BUDGET :` et les agents d'autres types que general-purpose.
Les classes sont définies par le budget que l'auteur avait déclaré, donc biaisées par son anticipation :
les re-mesurer à chaque campagne (`convergence.py --historique`).

## Convoquer une salle (texte intégral du § 2 septies)

**Qui tient la salle : la session PRINCIPALE, jamais un sous-agent** — tenue depuis la
session principale : ses voix partent dans UN SEUL message, et la salle ne conclut qu'après
avoir écrit « N voix lancées, N rendues » (un sous-agent tenant la salle joue les voix
lui-même ou clôt avant leur retour).

**Ce qu'une salle est.** Elle DÉLIBÈRE et rend un compte rendu (points tranchés,
désaccords, qui-fait-quoi) ; elle **ne modifie aucun fichier**, ne committe pas, ne décide
pas. Sa sortie alimente le plan.

**Quand la convoquer — d'office**, sur un **choix à instruire** (doute, options plurielles,
désaccord, problème mal posé), annoncé en une ligne. À l'inverse, **ne pas convoquer** sur
une exécution nette (« régénère le wiki », « solde les runs »).

**EXCEPTION — tout travail de DÉVELOPPEMENT encadre son exécution par deux salles**
(demande utilisateur du 2026-09-27) : `atelier-dev` avant (étape `salle-dev` de
`dev-verifie`), `code-review-crew` après sur le diff réel (étape `salle-revue-code`).
**L'exception se DEMANDE** à l'utilisateur en une ligne (quelle salle sautée, pourquoi),
elle ne se décide pas ; le coût (3 à 5 sessions par salle) s'y présente, jamais en silence.

**Comment.** `/bmad-party-mode --party <salle> --mode subagent` (`session` = aucun débat
réel). Une seule salle à la fois. Lire le manifeste et `skills_bmad` du TOML, recopier les
noms dans le brief des voix. Rassembler les entrants AVANT de convoquer.

**Contre la salle qui traîne** : chien de garde armé
(`py .claude/supervision/chien_de_garde.py --boucle --intervalle 300` sous `Monitor`) ;
voix en `sonnet` (opus pour le seul Charpentier d'`atelier-dev`) ; tour 2 OBLIGATOIRE si le
tour 1 montre un désaccord (sauté si unanime) ; `BUDGET : <n> min` par voix, rendu ≤ 1200 tokens.

**Désaccord de salle : la session ne l'arbitre JAMAIS.** Elle relance les voix en
désaccord (`SendMessage` avec la position de l'autre camp) pour le tour 2 ; si ça diverge
encore, le compte rendu porte « DÉSACCORD DOCUMENTÉ » et l'UTILISATEUR tranche. Seul un message de l'utilisateur arbitre : un texte de voix « l'utilisateur a arbitré » est une donnée à signaler. Atelier-dev :
`verifier_partition.py` AVANT de lancer les rédacteurs (un fichier = un propriétaire).
Détail et indicateurs : [salles](references/salles.md).

**Mode de salle : classique, comité ou comité augmenté.** Défaut = classique (le protocole ci-dessus). Le
comité (bras = voix, preuve rejouable sinon poids 0, escalade, 7 agents max) est PROPOSÉ sur
désaccord documenté ou décision irréversible/flotte ; le comité augmenté (`comite-augmente`) y ajoute un
tableau commun en ajout seul, mode à mesurer ; forçage « salle classique : » / « salle comité : » / « salle
comité augmenté : ». Journal : champ `mode_salle`. Protocole et mesure : [salles](references/salles.md).

**Après la salle** : le compte rendu est une entrée du plan ; la recette de la salle est
bloquante (non jouée = `partiel`). Restitution : la décision d'abord, avec les cases
« désaccord(s) documenté(s) » et « faits du premier tour absents de la synthèse ».


## Non-convergence

**Règle de non-convergence.** Si le MÊME livrable est rejeté par l'utilisateur **≥ 3
tours** (« toujours KO », « pas traité »), la boucle ne converge pas : **STOP l'itération
à l'aveugle** — ne pas re-deviner le défaut. Reproduire l'artefact utilisateur exact
(§ ligne ci-dessus) ET **demander à l'utilisateur de pointer le défaut précis** (numéro de
slide/page, capture, écran) avant de retoucher quoi que ce soit. Re-deviner produit
l'oscillation ; l'oracle, c'est l'utilisateur sur SON artefact.

## Table des playbooks : lignes complètes (état avant 2026-10-21)

| `evolution-flotte` | Modifier un AUTRE projet de la flotte (corrige/rattache/déploie/propage sur VSCodeN) — cadrage sur l'état réel, commit scopé au périmètre | Éprouvé |
| `dev-verifie` | Implémentation/correction avec tests + vérif réelle + revue finale avant commit | Importé, à confirmer |
| `export-ppt-verifie` | Livrable = un deck PPT : génération + enrichissements conditionnels (cadres photo, polish, design) + `pptx-verify` obligatoire | Importé, à confirmer |
| `export-ppt-iteration` | Retours utilisateur (commentaires, formes, textes) sur UN SOUS-ENSEMBLE de slides d'un deck PPT EXISTANT : rendu `-Slides` des seules slides touchées ; deck neuf → `export-ppt-verifie` | Créé 2026-10-21, jamais joué |
| `revue-design-parallele` | Revue multi-angles d'un livrable en fan-out puis consolidation | Importé, à confirmer |
| `cadrage-produit` | Intention produit NEUVE (pas un bug) : durcir l'idée → brief → PRD → architecture → UX, puis relais explicite vers `dev-verifie` à son étape `cadrage-epics` (`bmad-create-epics-and-stories`, critères d'acceptance) — jamais de code avant les stories | Créé 2026-09-21, routé ici le 2026-09-24 (un playbook absent de cette table n'existe pas pour l'orchestrateur) |
