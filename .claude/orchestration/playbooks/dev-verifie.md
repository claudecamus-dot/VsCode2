# Playbook `dev-verifie` — implémentation vérifiée de bout en bout

Workflow de dev générique : implémenter, tester, **vérifier en réel** (pas seulement un
statut de test vert — une suite qui passe ne garantit pas qu'un rendu/écran/export soit
correct), puis revue finale avant tout commit.

Importé depuis le projet VSCode2 (Interview-to-Deck) où ce déroulé était éprouvé ; ici,
**statut `éprouvé`** (arbitrage du 2026-10-22 : 43 runs, 36 succès, 0,95 reprise par run,
mesurés sur `runs.jsonl` ; critère de `FORMAT.md` : au moins une exécution réelle réussie).

Les étapes de vérification réelle sont **conditionnelles au type de fichiers touchés** :
ne garder à l'instanciation que celles dont la condition s'applique, ne jamais retirer les
tests ni la revue finale avant commit.

Frontière avec `export-ppt-verifie` : un changement de code qui *touche* la génération PPT
au passage reste ici (l'étape `verification-pptx` couvre) ; quand le **livrable est le deck
lui-même** (layout, contenu, visuel), préférer `export-ppt-verifie`.

**Cadrage lourd obligatoire (arbitrage utilisateur 2026-09-16, second arbitrage de la
même journée — REMPLACE le paragraphe ci-dessous, conservé pour la trace)** : une salle
`conseil-flotte` convoquée le même jour a recommandé l'inverse (réparer `gate-cadrage`
puis router par niveau CORRECTIF/SOLUTION, cadrage lourd non propagé par défaut — motif :
`gate-cadrage` ne pouvait alors jamais échouer, les 5 copies du playbook divergeaient déjà
du canon, et BMAD v6 documente lui-même un Scale-Adaptive Planning par niveau). Présentée
à l'utilisateur avec ces contre-preuves, la demande initiale a été **maintenue explicitement
malgré elles** : `bmad-product-brief`, `bmad-architecture`, `bmad-create-epics-and-stories`
et `bmad-sprint-planning` sont désormais des étapes **bloquantes** de la phase de cadrage,
avant `implementation` — voir leurs contrats ci-dessous (étapes `cadrage-brief`,
`cadrage-architecture`, `cadrage-epics`, `gate-cadrage`). Deux risques réels, non résolus
par cet arbitrage mais qu'il ne faut pas laisser produire un blocage silencieux :
- `bmad-create-epics-and-stories` exige `PRD.md` + `Architecture.md`. Ce playbook ne
  produit pas de PRD complet (`bmad-product-brief` rend un BRIEF, pas un PRD) — si
  `PRD.md` manque à l'étape `cadrage-epics`, le contrat de cette étape impose un FAIL
  immédiat vers l'utilisateur (proposer `bmad-prd`), jamais une invention silencieuse du
  document ni un blocage qui attend une réponse que personne ne verra.
- L'architecture « step-file » de ces skills s'arrête sur des menus interactifs à chaque
  étape (constaté le 2026-09-16 : `bmad-code-review` a bloqué un sous-agent de la salle
  `atelier-dev` pour la même raison, et `bmad-method install` lui-même s'est révélé être
  un TUI qui rend `exit 0` sans rien écrire hors d'un vrai terminal — même famille de
  panne). Ces quatre étapes s'exécutent donc en **session principale**, jamais déléguées à
  un sous-agent sans TTY : un sous-agent qui heurte un menu interactif porte `etat: echec`,
  jamais un silence pris pour un succès.

<details>
<summary>Paragraphe remplacé (arbitrage du matin même, 2026-09-16, écarté l'après-midi)</summary>

la demande initiale d'intégrer la skill BMAD `bmad-create-epics-and-stories` en amont de
ce playbook a été écartée après lecture de son prérequis — elle exige `PRD.md` +
`Architecture.md` en bloquant, et son architecture « step-file » s'arrête à des menus
interactifs à chaque étape. `dev-verifie` sert des chantiers qui n'ont ni PRD ni
Architecture la plupart du temps — imposer la skill aurait forcé soit un blocage, soit la
fabrication de documents disproportionnés pour un correctif d'une ligne. Même logique pour
`bmad-sprint-planning` : sa « readiness gate » raisonne depuis un `epics.md` que ce
playbook ne produisait pas.

</details>

```json
{
  "nom": "dev-verifie",
  "description": "Implémentation d'une feature/correction avec tests, vérification réelle adaptée aux fichiers touchés, et revue finale avant commit.",
  "statut": "eprouve",
  "source": "manuel",
  "declencheurs": [
    "implémente/corrige/ajoute une fonctionnalité",
    "changement de template/UI (HTML, CSS, JS)",
    "changement touchant la génération d'un export PPT",
    "fin d'incrément, préparation d'un commit de code produit"
  ],
  "etapes": [
    {
      "id": "salle-dev",
      "agent": "salle atelier-dev (bmad-party-mode --party atelier-dev --mode subagent)",
      "mode": "parallele",
      "modele": "sonnet pour les voix, opus réservé au seul rôle structurant (Charpentier) — arbitrage utilisateur 2026-09-27",
      "contrat": {
        "type": "reel",
        "critere": "ÉTAPE D'OUVERTURE OBLIGATOIRE (demande utilisateur du 2026-09-27) : la salle siège AVANT qu'une ligne soit écrite et rend un PLAN, une PARTITION DE FICHIERS EXCLUSIVE (deux exécutants ne touchent jamais le même fichier) et un CONTRAT DE PREUVE (quels tests, quelle mutation vue rouge, quelle commande de vérification). Tour 2 OBLIGATOIRE si le tour 1 montre un désaccord (règle des salles du 2026-10-03, qui remplace l'arbitrage du 2026-09-27), sauté si le tour 1 est unanime ; la session n'arbitre JAMAIS : elle relance les voix en désaccord, puis « DÉSACCORD DOCUMENTÉ » et l'utilisateur tranche. AVANT tout fan-out de plusieurs exécutants : `py .claude/orchestration/verifier_partition.py <plan.json>` ({voix: [fichiers]}), exit 0 exigé — un fichier, un propriétaire. Chaque brief de voix porte un budget explicite (`BUDGET : <n> min`, lu par `.claude/supervision/convergence.py`) et un rendu ≤ 1200 tokens. Pendant que la salle tourne, l'orchestrateur relance `py .claude/supervision/convergence.py` toutes les ~15 min (chien de garde périodique, § 2 septies) — sur une voix muette, vérifier le dernier signe de vie sur disque avant `--clore`/`--deroger`, jamais `tasks/*.output` qui reste à 0 octet même finie. La salle ne modifie AUCUN fichier : un diff produit par une salle est une auto-application collective, elle rend un compte rendu. Ce qu'elle achète, mesuré le 2026-09-27 : 3 prémisses fausses corrigées avant écriture, dont un champ tenu pour un marqueur de solde alors qu'il s'écrit aussi à l'append — la garde bâtie dessus aurait refusé un premier solde légitime. EXCEPTION : si le chantier paraît trop petit pour une salle, la DEMANDER à l'utilisateur en une ligne (quelle salle, pourquoi, quel coût) et attendre sa réponse — jamais s'en exempter soi-même"
      },
      "checkpoint": "sauter cette étape exige une réponse explicite de l'utilisateur, jamais une décision de l'orchestrateur"
    },
    {
      "id": "cadrage",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "fichiers concernés lus, appelants des fonctions/champs partagés grep-és avant modification ; PUIS la demande décomposée en exigences discrètes et NOMMÉES, une ligne chacune, avec son critère d'acceptation vérifiable — liste courte tenue dans le plan TodoWrite ou les notes du run ; chaque exigence de cette liste est recochée une à une à l'étape `revue-finale`, aucune n'est réinterprétée en silence entre le cadrage et le commit"
      },
      "checkpoint": false
    },
    {
      "id": "cadrage-brief",
      "agent": "skill bmad-product-brief",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "regime": "propose (la skill ECRIT un fichier reel) : annoncer l'etape et attendre le feu vert avant de la lancer, § 2 quinquies de agent-orchestrator",
        "critere": "brief produit (probleme, utilisateurs, contraintes) et relu par l'utilisateur avant de passer a `cadrage-architecture` ; exécuté en SESSION PRINCIPALE, jamais délégué à un sous-agent sans TTY (menus interactifs — voir note ci-dessus) — un blocage sur un menu est `etat: echec`, jamais un silence pris pour un succès"
      },
      "checkpoint": "annonce + feu vert avant lancement (écrit un fichier réel)"
    },
    {
      "id": "cadrage-architecture",
      "agent": "skill bmad-architecture",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "regime": "propose (ecrit Architecture.md) : annoncer et attendre le feu vert",
        "critere": "Architecture.md produit à partir du brief de `cadrage-brief` ; invariants d'architecture nommés, pas un paragraphe générique ; session principale, mêmes garde-fous menu interactif que `cadrage-brief`"
      },
      "checkpoint": "annonce + feu vert avant lancement (écrit un fichier réel)"
    },
    {
      "id": "cadrage-epics",
      "agent": "skill bmad-create-epics-and-stories",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "regime": "propose (ecrit epics.md/stories) : annoncer et attendre le feu vert",
        "critere": "epics.md produit, decoupe en stories verifiables ; PRÉREQUIS CONNU : cette skill exige `PRD.md` + `Architecture.md` en bloquant — `Architecture.md` vient de `cadrage-architecture`, mais ce playbook ne produit PAS de `PRD.md` complet (`cadrage-brief` rend un brief, pas un PRD). SI `PRD.md` manque au moment de lancer cette étape : FAIL immédiat vers l'utilisateur avec la proposition explicite de lancer `bmad-prd` d'abord — jamais de PRD inventé en silence, jamais d'attente sur un prompt que personne ne surveille"
      },
      "checkpoint": "annonce + feu vert avant lancement (écrit un fichier réel) ; FAIL nommé si PRD.md absent"
    },
    {
      "id": "gate-cadrage",
      "agent": "skill bmad-sprint-planning",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "regime": "propose (peut ecrire sprint-status) : annoncer et attendre le feu vert",
        "critere": "verdict PASS/CONCERNS/FAIL de la readiness gate de bmad-sprint-planning, lu sur `epics.md` produit par `cadrage-epics` — PASS : `implementation` démarre ; CONCERNS : lacunes nommées, présentées à l'utilisateur, confirmation attendue avant `implementation` ; FAIL (epics.md absent, ou une story sans critère d'acceptation vérifiable) : retour à l'étape en amont qui a échoué, jamais d'implementation sur une base FAIL. Le verdict doit pouvoir échouer réellement — un verdict qui ne connaît que PASS est un contrat décoratif, pas une gate"
      },
      "checkpoint": false
    },
    {
      "id": "atdd",
      "agent": "bmad-test (skill bmad-testarch-atdd)",
      "mode": "cascade",
      "modele": "sonnet",
      "contrat": {
        "type": "deterministe",
        "regime": "propose (ecrit sous _bmad-output/test-artifacts/) : annoncer et attendre le feu vert",
        "critere": "CONDITIONNELLE — SI les stories portent des critères d'acceptance vérifiables et que l'utilisateur accepte l'ATDD : tests d'acceptance écrits en phase ROUGE avant `implementation` (ils échouent réellement, sortie du runner citée) ; sinon étape sautée et le dire"
      },
      "checkpoint": "annonce + feu vert avant lancement (écrit un fichier réel)"
    },
    {
      "id": "implementation",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "PRÉCONDITION OBLIGATOIRE (arbitrage 2026-10-28, story requise ; statut d'étape `bloque`, voir FORMAT.md) : le brief d'implémentation cite l'id de la story cible (ex. `Story 14.7`) présente dans `_bmad-output/planning-artifacts/epics.md` du projet cible. Deux cas distincts : (i) `epics.md` ABSENT = `bloque`, demander à l'utilisateur de le créer via `bmad-create-epics-and-stories` ; (ii) fichier présent mais story non citée ou inexistante = `bloque` jusqu'à ce que l'utilisateur la fasse créer. L'agent ne crée JAMAIS la story lui-même (R4 : il propose, l'utilisateur arbitre). Seule exception : une dérogation écrite de l'UTILISATEUR, citée telle quelle dans le brief et reprise dans le commit (`Refs: derogation-utilisateur <date> <raison>`), jamais accordée par l'agent ; la précondition n'est alors pas sautée en silence, elle est tracée. L'id de story est repris dans le trailer `Refs:` du commit. Puis : chaque exigence EXPLICITE de la demande (points numérotés, contraintes) cochée une à une contre le diff — pas seulement « ça compile/passe » ; toute exigence réinterprétée ou écartée signalée, jamais silencieuse ; style du fichier environnant respecté ; si l'implémentation est longue (plusieurs fichiers ou une série d'itérations), rendre un jalon intermédiaire journalisable avant de poursuivre — coût tokens/latence mis en regard du bénéfice de détection précoce (veille 2026-09-08, Beyond the Leaderboard, arXiv:2607.05775)"
      },
      "checkpoint": false
    },
    {
      "id": "tests",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "verdict lu sur la ligne de synthèse RÉELLE du test-runner (N passed / 0 failed / 0 error), pas sur une sortie tronquée ou filtrée",
        "commande": "(adapter à la stack du projet — ex. pytest -q)"
      },
      "checkpoint": false
    },
    {
      "id": "verification-ui",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "SI template/CSS/JS/écran touché : rendu réel regardé (screenshot ou app lancée), pas seulement le code source relu"
      },
      "checkpoint": false
    },
    {
      "id": "verification-pptx",
      "agent": "pptx-verify",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "SI la génération d'un export PPT est touchée : export réel rendu en images et inspecté (python-pptx est un parseur tolérant, un fichier qui parse peut ne pas s'ouvrir correctement dans PowerPoint)"
      },
      "checkpoint": false
    },
    {
      "id": "revue-finale",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "relecture du diff complet, exigences de la demande recochées une à une, tests/vérifications ci-dessus confirmés faits avant de proposer le commit"
      },
      "checkpoint": "avant tout commit — action difficilement réversible, proposer, ne pas exécuter unilatéralement"
    },
    {
      "id": "salle-revue-code",
      "agent": "salle code-review-crew (bmad-party-mode --party code-review-crew --mode subagent)",
      "mode": "parallele",
      "modele": "sonnet pour les cinq voix — aucun rôle structurant identifiable dans son manifeste (arbitrage utilisateur 2026-09-27)",
      "contrat": {
        "type": "reel",
        "critere": "ÉTAPE DE CLÔTURE OBLIGATOIRE (demande utilisateur du 2026-09-27), à la SUITE de `salle-dev` et de l'exécution : la salle revoit le DIFF RÉELLEMENT PRODUIT (sha énumérés dans le brief, `git show <sha>`), pas l'intention. Tour 2 OBLIGATOIRE si les lectures indépendantes du tour 1 divergent (règle des salles du 2026-10-03), sauté si unanimes ; la session n'arbitre JAMAIS (voix relancées, puis « DÉSACCORD DOCUMENTÉ » et l'utilisateur tranche). Chaque brief de voix porte `BUDGET : <n> min` (lu par `convergence.py`) et un rendu ≤ 1200 tokens ; chaque voix porte son angle : artisanat sous `bmad-code-review`, cas-limites sous `bmad-review`, sécurité/adversaire/pragmatique sans skill et le disant. Pendant que la salle tourne, l'orchestrateur relance `py .claude/supervision/convergence.py` toutes les ~15 min (chien de garde périodique) — voix muette : dernier signe de vie sur disque avant `--clore`/`--deroger`, jamais `tasks/*.output`. Exigences du brief : chaque constat porte son `fichier:ligne` et le fait VÉRIFIABLE (la commande qui l'établit) ; un contournement allégué se démontre par exécution, pas par lecture ; les mutations proposées nomment le test qui devrait les tuer. Un diff vert n'est pas un diff revu — le 2026-09-27, sur deux chantiers verts, la salle a trouvé 3 mutants survivants et un chemin qui soldait le mauvais run en sortant `exit 0`. Ses constats reviennent à l'utilisateur pour arbitrage (R4), ils ne s'appliquent pas d'office. EXCEPTION : se DEMANDE à l'utilisateur, jamais décidée par l'orchestrateur"
      },
      "checkpoint": "les constats de la salle sont présentés à l'utilisateur avant toute application — R4"
    },
    {
      "id": "relecture-adverse",
      "agent": "bmad-revue (relecteur indépendant, sous-agent à contexte vierge)",
      "mode": "cascade",
      "modele": "opus",
      "contrat": {
        "type": "llm",
        "regime": "d'office (passe de lecture qui rend un rapport)",
        "commande": "git diff --name-only main... | grep -E '^(\\.claude/hooks/|\\.claude/dispositif/canon/|\\.claude/orchestration/log_run\\.py)'",
        "critere": "OBLIGATOIRE quand la `commande` ci-dessus liste au moins un fichier (diff touchant `.claude/hooks/`, `.claude/dispositif/canon/` ou `log_run.py`, copies canon et orchestration) (diagnostic 2026-10-19 : deux correctifs de garde sur deux, 396d9875 puis e16ed731 et 60ba70b8 puis f4646a94 puis 5c67f9b1, n'ont été justes qu'après une relecture indépendante) ; sinon sautée et le dire. Relecteur à CONTEXTE VIERGE (brief autoportant : sha du diff, jamais le raisonnement ni la conclusion de l'auteur), modèle différent de celui de l'auteur : auteur sonnet → opus ; auteur opus ou fable → sonnet ; auteur haiku → sonnet ; auteur `(session)` → un modèle différent de celui de la session (le dire explicitement dans le brief). Cette étape ne dispense PAS `salle-revue-code` et n'en est pas dispensée : ce sont deux contrôles distincts (la salle revoit le diff, la relecture tente de le casser) ; verdicts combinés dans l'ordre salle puis relecture, le plus sévère l'emporte. Mission : TENTER DE CASSER le correctif — chaque contre-exemple se démontre par exécution sur une COPIE (jamais sur le dépôt), pas par lecture. Le relecteur REJOUE lui-même les suites concernées ; tout échec est comparé à la baseline sur main avant d'être attribué au diff. Verdict en une des trois valeurs : fusionnable | après correctifs | à rejeter, chaque constat avec `fichier:ligne` et la commande qui l'établit. « Après correctifs » renvoie à `implementation` puis relecture par un relecteur à contexte à nouveau vierge ; « à rejeter » remonte à l'utilisateur (R4)"
      },
      "checkpoint": "verdict présenté à l'utilisateur ; pas de commit sur « après correctifs » ou « à rejeter »"
    },
    {
      "id": "revue-avant-fusion",
      "agent": "bmad-revue (relecteur qui n'est pas l'auteur, sous-agent à contexte vierge)",
      "mode": "cascade",
      "modele": "opus",
      "contrat": {
        "type": "llm",
        "regime": "d'office (passe de lecture qui rend un rapport)",
        "commande": "git diff main...<branche-du-lot>",
        "critere": "OBLIGATOIRE avant toute fusion sur main, quel que soit le périmètre du diff (arbitrage `cible: dev-verifie` du 2026-11-02 dans `.claude/supervision/arbitrages.json`, catégorie `ko-repete` : « S4 revue adversariale avant fusion dans dev-verifie »). Nombre de voix selon le palier de risque T0/T1/T2 du diff (D30, 2026-11-02 : T0 tests et textes = aucune voix, cette étape est alors la revue finale de l'auteur ; T1 hook ordinaire/source du kit/script = une voix ; T2 garde `guard_*`, `settings.json`, dispatcher, propagation flotte, porte de fusion = trois voix en série, `BUDGET : 10 min` chacune, verdict partiel obligatoire) : `agent-orchestrator/references/regles-detaillees.md` § « Revue graduée (D30) » ; et aucune fusion de feature tant que la CI de main est rouge (§ « Gel main verte (D30) », `livrer_lot.py --pousser` refuse hors `quarantaine.json`). UNE passe adversariale sur le DIFF RÉEL, identifié par son sha cité dans le brief, par un relecteur qui n'est pas l'auteur (contexte vierge, jamais le raisonnement de l'auteur) et de modèle différent de celui de l'auteur (même règle que `relecture-adverse`). `relecture-adverse` tient lieu de cette passe UNIQUEMENT si elle a réellement tourné sur le MÊME sha, a rendu un verdict, par un relecteur autre que l'auteur et de modèle différent ; sinon cette étape n'est pas satisfaite et doit tourner. Lot sans diff (`git diff main...<branche-du-lot>` vide) : rien à fusionner, l'étape est sautée et le dire ; aucune fusion n'a lieu. Verdict en une des trois valeurs, qui conditionne la fusion : `fusionnable` (= « fusionnable » de `relecture-adverse` : fusion autorisée) | `fusionnable-apres-correction` (= « après correctifs » : correction, puis l'étape `test` est rejouée sur le nouveau sha, puis vérification avant fusion) | `non-fusionnable` (= « à rejeter » : pas de fusion, retour utilisateur — R4). Chaque constat porte `fichier:ligne` et une sévérité. Constats tous MINEUR : la correction est vérifiée par une seule voix ciblée sur ces constats, pas par une nouvelle revue complète. Tours de revue répétés sur un même lot : escalade à l'utilisateur avec l'état réel plutôt qu'une boucle ; le plafond relève de la story 19.13 (`_bmad-output/planning-artifacts/epics.md`, en stand-by, D15 de `docs/decisions-en-attente.md`), à laquelle cette étape renvoie sans en reprendre les critères."
      },
      "checkpoint": "pas de fusion sans verdict `fusionnable` (ou `fusionnable-apres-correction` dont la correction est vérifiée) ; verdict présenté à l'utilisateur"
    },
    {
      "id": "test",
      "agent": "bmad-test (skills bmad-testarch-test-review puis bmad-testarch-trace)",
      "mode": "cascade",
      "modele": "sonnet",
      "contrat": {
        "type": "reel",
        "regime": "d'office (passes de lecture qui rendent un rapport)",
        "critere": "OBLIGATOIRE pour du code applicatif, à la suite de `salle-revue-code` : revue de la qualité des tests écrits (test-review) puis matrice exigences → tests et décision de gate (trace) ; CONDITIONNEL — SI le PRD porte des exigences non fonctionnelles : audit des preuves NFR (bmad-testarch-nfr) ; chaque lacune porte son `fichier:ligne` ; un gate FAIL renvoie à `implementation`, jamais de livraison dessus"
      },
      "checkpoint": false
    },
    {
      "id": "utilisateur-produit",
      "agent": "utilisateur-produit",
      "mode": "cascade",
      "modele": "sonnet",
      "contrat": {
        "type": "reel",
        "critere": "CONDITIONNELLE — SI un chemin exécutable de bout en bout existe (règle d'entrée de `.claude/agents/utilisateur-produit.md`) : le produit est exercé sur ses cas d'usage réels, critères d'acceptance notés un par un ; sinon étape sautée et le dire. Ses constats reviennent à l'utilisateur (R4)"
      },
      "checkpoint": "constats présentés à l'utilisateur avant toute application — R4"
    },
    {
      "id": "revue-increment",
      "agent": "skill revue-increment",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "étape terminale OBLIGATOIRE (finding playbook:evolution-flotte 2026-07-29) : la boucle revue-increment jouée en fin d'incrément — vérité du journal, diff relu contre la demande, vérifications réelles confirmées faites. Allégeable en fin de campagne (une revue pour plusieurs runs de la même séance), jamais sautée"
      },
      "checkpoint": false
    }
  ],
  "regle_reprise": "une relance ciblée par étape en échec de contrat, puis escalade utilisateur avec l'état réel"
}
```

## Bras témoin (optimiseur)

Pour savoir si le pilotage multi-étapes de ce playbook vaut son coût, on le compare à un
**agent seul** sur la MÊME tâche. Protocole (verdict : `.claude/supervision/optimiseur.py`) :

- **Bras `temoin`** : un seul agent, aucun fan-out ni salle, avec le même `budget_tokens`
  que le bras `variante`. Journalisé avec `"bras": "temoin"`, `"topologie": "agent-seul"`.
- **Bras `variante`** : le playbook tel qu'écrit, `"bras": "variante"`, même `budget_tokens`.
- **Appariement** : les deux runs portent le même `tache_id`. Sans paire, l'optimiseur rend
  `donnees-insuffisantes` (« non apparié ») et ne propose rien.
- **Assignation alternée** : une tâche sur deux commence par le témoin, pour que l'ordre
  (et le cache chaud) ne favorise pas toujours le même bras.
- Chaque run déclare `tokens` et `duree_s` ; sans eux, jamais de verdict `gagnant`.
- Un verdict est une proposition (R4) : rien ne s'applique sans arbitrage.
