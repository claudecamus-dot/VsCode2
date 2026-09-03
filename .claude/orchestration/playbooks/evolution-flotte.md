# Playbook `evolution-flotte` — modifier un AUTRE projet de la flotte, vérifié et scopé

Le métier récurrent de CE projet : appliquer une évolution agentic (rattachement de
skills, déploiement du dispositif, correction de playbook/catalogue, propagation d'un
fix) sur un ou plusieurs projets cibles de `projets.json`. Capitalisé depuis les 4
premiers runs réels du 2026-07-23 (correction VSCode2, correction VSCode1, déploiement
VSCode, propagation fix scan) — constat superviseur : 5 runs sur 6 se composaient à vide
faute de playbook qui matche.

**Les trois leçons fondatrices, payées le jour même** :

1. **Lire l'état RÉEL de la cible avant d'écrire** (run VSCode1 : les 5 skills «
   à rattacher » étaient déjà rattachées — la correction juste était 3 lignes, pas un
   re-câblage). Le wiki de supervision peut retarder sur la réalité : il éclaire le
   cadrage, il ne le remplace pas.
2. **Commit scopé au périmètre de l'évolution** (run VSCode : 174 fichiers de churn
   BMAD/Codex préexistant découverts au moment de committer — jamais les embarquer,
   jamais les écraser ; si le dépôt cible porte du travail non commité qui n'est pas le
   nôtre, le signaler et le laisser).
3. **Adapter au canal du projet cible, ne pas plaquer** (run VSCode : génération PPT via
   COMOP Node/PowerShell, pas python-pptx ; étape terminale = la `revue-increment`
   préexistante du projet, pas une copie).

```json
{
  "nom": "evolution-flotte",
  "description": "Évolution agentic appliquée à un ou plusieurs projets cibles de la flotte : cadrage sur l'état réel, modification scopée, vérifications, commit limité au périmètre, wiki rafraîchi, journal.",
  "statut": "eprouve",
  "source": "manuel",
  "declencheurs": [
    "corrige/rattache/répare X sur VSCodeN",
    "déploie/met à jour le dispositif (orchestrateur, superviseur, skills, playbooks) sur un projet",
    "propage un fix/une évolution d'un composant partagé vers d'autres projets",
    "traite les propositions du diagnostic pour un projet cible"
  ],
  "etapes": [
    {
      "id": "cadrage-reel",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "état RÉEL de chaque cible lu AVANT d'écrire : fichiers concernés ouverts (playbook/catalogue/settings/skills), git status du dépôt cible relevé (churn préexistant identifié et exclu du périmètre), canal/conventions propres au projet identifiés. Le wiki de supervision éclaire, la lecture directe tranche — une demande peut être déjà (partiellement) satisfaite : la correction minimale prime sur le re-câblage. Dépôt AU REPOS avant tout dispatch de sous-agent (lecture ou écriture) : deux relevés `git status --porcelain` espacés de quelques secondes qui diffèrent signalent une session tierce active — reporter le dispatch ou élargir explicitement le budget de patience attendu avant de qualifier un run non convergent (veille adoptée 2026-09-03, garde-fou maison, pas une pratique documentée par un provider)."
      },
      "checkpoint": false
    },
    {
      "id": "modification",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "modification scopée et adaptée à la cible (jamais un écrasement aveugle d'une copie divergée — édits ciblés si le fichier a des adaptations locales) ; chaque exigence explicite de la demande cochée contre le diff ; si plusieurs cibles, appliquer projet par projet"
      },
      "checkpoint": false
    },
    {
      "id": "verification",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "sur CHAQUE cible modifiée : py_compile sur les scripts Python touchés, JSON/settings validés, hooks exécutés à blanc si touchés, tests du projet cible lancés s'ils couvrent le périmètre (leçon VSCode1 : test-export-ppt vert avant commit), grep de cohérence sur les identifiants modifiés"
      },
      "checkpoint": false
    },
    {
      "id": "revue-fraiche",
      "agent": "sous-agent revue (contexte frais)",
      "mode": "cascade",
      "modele": "sonnet",
      "contrat": {
        "type": "reel",
        "critere": "SI l'évolution modifie du code ou de la config exécutable (script, hook, settings, playbook) : revue en CONTEXTE FRAIS avant commit (pratique Anthropic adoptée 2026-07-24 — le relecteur ne voit que le diff par cible et les exigences de la demande, pas le raisonnement de l'implémenteur). Le sous-agent doit etre un sous-agent STANDARD isole (type Explore, general-purpose ou un porteur maison) : jamais un fork ni /subtask. Depuis Claude Code v2.1.232 le fork est actif par defaut et HERITE du contexte de l'appelant — un relecteur qui a vu le raisonnement de l'implementeur ne revise plus en contexte frais, et l'etape devient decorative sans que rien ne le signale (veille 2026-08-31). ; ne rapporter que les écarts de correctness/périmètre (fichier hors périmètre embarqué, exigence non couverte, régression visible au diff), pas les préférences de style. Étape sautable UNIQUEMENT pour une évolution purement documentaire, en le notant dans le journal."
      },
      "checkpoint": false
    },
    {
      "id": "commit-scope",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "git add limité aux fichiers du périmètre (vérifié par git diff --cached --name-only — aucun fichier du churn préexistant), message expliquant le POURQUOI avec référence au constat/diagnostic d'origine — pour une propagation du DISPOSITIF (canon, skills de pilotage, hooks, sous-agents), message normalisé « dispositif: <quoi> depuis le hub » —, push si le dépôt a un remote. ÉTAPE TERMINALE OBLIGATOIRE de toute écriture chez une cible : une écriture sur un dépôt tiers n'est PAS terminée tant qu'elle n'est pas soit commitée dans CE dépôt, soit couverte par une ligne d'arbitrage (cible flotte:<projet>-commit-dispositif) nommant un propriétaire et une échéance — un message de séance n'est pas un canal de décision (finding flotte:canon-ecrit-jamais-commite : 80 fichiers non commités, doyen 39 j, deux commits de cibles passés par-dessus sans les voir ; arbitré le 2026-08-31). Composition du message : ÉCRIT DANS UN FICHIER puis `git commit -F <fichier>` — jamais de here-string/heredoc pour un message portant apostrophes, backticks ou $ (3 reprises payées : heredoc bash non quoté 2026-07-24, here-string PowerShell cassée par apostrophes 2026-07-27, et rappel : les variables PowerShell sont INSENSIBLES À LA CASSE quand l'étape produit du code PowerShell sur la cible, collision realOpen/RealOpen 2026-07-28)"
      },
      "checkpoint": "avant commit/push sur un dépôt cible — action difficilement réversible : mandat utilisateur explicite requis (la demande initiale peut le porter)"
    },
    {
      "id": "wiki-et-journal",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "le scan relancé — au hub scripts/scan_projets.py, depuis une cible .claude/supervision/scan_transcripts.py, le premier n'y étant pas déployé — (le wiki reflète l'état post-évolution), run journalisé via log_run.py avec le playbook 'evolution-flotte' et les cibles dans la demande ; si l'évolution répond à un finding du diagnostic, l'arbitrage correspondant est enregistré"
      },
      "checkpoint": false
    },
    {
      "id": "revue-increment",
      "agent": "skill revue-increment",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "étape terminale OBLIGATOIRE (finding playbook:evolution-flotte 2026-07-29 : un rappel que 41 runs ignorent n'est pas un dispositif — même statut que revue-fraiche) : la boucle revue-increment jouée avant de déclarer le run terminé — vérité du journal (runs soldés via --solde), arbitrages tracés à la cible exacte, diff de séance relu contre la demande, données générées jamais éditées à la main. Allégeable en fin de CAMPAGNE (une seule revue terminale pour plusieurs runs enchaînés dans la même séance), jamais sautée ; log_run avertit si un run 'succes' n'en porte pas la trace"
      },
      "checkpoint": false
    }
  ],
  "regle_reprise": "une relance ciblée par étape en échec de contrat, puis escalade utilisateur avec l'état réel du/des dépôt(s) cible(s)"
}
```
