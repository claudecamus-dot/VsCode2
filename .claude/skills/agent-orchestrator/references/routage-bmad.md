# Routage des skills BMAD — détail du § 2 quinquies

<!-- Référence de la skill agent-orchestrator (divulgation progressive, lot 3,
     2026-10-02). Texte déplacé tel quel depuis SKILL.md : la règle courte reste
     dans SKILL.md, le détail, les mesures et l'historique vivent ici. -->

### 2 quinquies. Router vers les skills BMAD

BMAD-METHOD est installé ici (**v6.12.0**, core + bmm) : **39 skills canoniques sur le
disque du hub** (dont les 10 TEA de test, lot 4b) — les seules que la table ci-dessous route. Les **21 shims dépréciés** que la
migration avait retenus par défaut ont été **retirés du hub le 2026-09-08** : 0 invocation
depuis leur installation et ~2 180 tokens de listing payés à chaque tour (finding
`VScode5:33-skills-bmad-jamais-invoquees-cout-listing`, mesuré par `/skill-doctor` du
2026-09-07). Les projets de la flotte les portent encore tant que leur propre mesure ne les a
pas jugés : leur compte reste plus élevé que celui du hub, et il n'est pas uniforme —
mesuré le 2026-09-08, 46 chez VSCode1, 50 chez VSCode2/3/4, 71 chez VSCode. Un chiffre lu
ici ne vaut donc que pour le hub ; sur une cible, le compter avant de s'en réclamer.
Elles couvrent cadrage produit, conception, planification, implémentation, revue,
documentation et recherche.
La migration 6.10.0 → 6.12.0 (pilote du 2026-09-07) a consolidé cinq familles : les cinq
lentilles de revue dans `bmad-review`, les trois recherches dans `bmad-deep-recon`, les deux
skills de contexte projet dans `bmad-project-context`, `bmad-quick-dev`/`bmad-dev-story` dans
`bmad-build`, et `bmad-sprint-status` dans `bmad-sprint-planning`.
Jusqu'au 2026-07-30 elles étaient réservées à la « demande explicite, via `bmad-help` » —
résultat mesuré par l'étage 1 : **0 invocation sur 113 sessions**, et un TODO
`agent-mort` ouvert au wiki. La règle a changé (arbitrage utilisateur du 2026-07-30) :
**elles font partie du workflow**, et c'est l'orchestrateur qui les déclenche quand le
besoin matche — plus besoin que l'utilisateur les nomme.

**Deux régimes de déclenchement, deux critères cumulatifs : le coût ET l'écriture.**

- **D'office** — la skill est bornée, ne rend qu'un rapport ; n'écrit rien sur disque — sauf,
  pour les passes portées par `bmad-test`, sous `_bmad-output/test-artifacts/` (jamais le
  code ni les tests du dépôt) : lecture ou critique, sans cascade. L'orchestrateur
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
(Ces quatre noms sont ceux de 2026-07-30 : la v6.12.0 en a retiré trois sans remplaçant et
a fait de la quatrième un shim vers `bmad-project-context`. Le critère, lui, n'a pas bougé —
il porte aujourd'hui sur `bmad-project-context`.)
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
| Critiquer un livrable non-code, chasser ses cas limites, ses écarts de vérification, sa prose ou sa structure — lentilles à nommer dans le brief | `bmad-review` | `bmad-revue` | d'office |
| Faire relire un changement par un humain (checkpoint, walkthrough) | `bmad-walkthrough` | `bmad-revue` | d'office |
| Approfondir une sortie récente (socratique, prémortem, red team) | `bmad-advanced-elicitation` | `bmad-revue` | d'office |
| Rétrospective de fin d'epic ou d'incrément | `bmad-retrospective` | `bmad-revue` | d'office |
| S'orienter dans le catalogue BMAD, choisir la bonne skill | `bmad-help` | `bmad-revue` | d'office |
| Documenter un dépôt existant (brownfield) et y écrire les règles agent (bloc AGENTS.md) | `bmad-project-context` | `inline` | proposé |
| Recherche pour décider — technique, domaine/secteur, marché, concurrence, voix client, littérature ; type à nommer dans le brief | `bmad-deep-recon` | `bmad-recherche` | d'office |
| Idéation cadrée sur un problème ouvert | `bmad-brainstorming` | `bmad-recherche` | d'office |
| Brief produit initial | `bmad-product-brief` | `inline` | proposé |
| PRD — créer, éditer ou valider | `bmad-prd` | `inline` | proposé |
| PRFAQ Working Backwards (concept client-first) | `bmad-prfaq` | `inline` | proposé |
| Durcir une idée par interrogation adverse | `bmad-forge-idea` | `inline` | proposé |
| Distiller une intention en noyau SPEC machine | `bmad-spec` | `inline` | proposé |
| Analyse métier et exigences (Mary) | `bmad-agent-analyst` | `inline` | proposé |
| Cadrage produit conduit par un PM (John) | `bmad-agent-pm` | `inline` | proposé |
| Architecture technique (colonne d'invariants) | `bmad-architecture` | `inline` | proposé |
| Conception système conduite par un architecte (Winston) | `bmad-agent-architect` | `inline` | proposé |
| Specs UX, patterns d'interaction | `bmad-ux` | `inline` | proposé |
| Design UX/UI conduit par une designer (Sally) | `bmad-agent-ux-designer` | `inline` | proposé |
| Table ronde multi-personas / focus group | `bmad-party-mode` | `inline` | proposé |
| Customiser une skill BMAD (party, personas, overrides de config) | `bmad-customize` | `inline` | proposé |
| Découper des exigences en epics et stories | `bmad-create-epics-and-stories` | `inline` | proposé |
| Plan de sprint depuis les epics, état du sprint, gate « prêt à implémenter » | `bmad-sprint-planning` | `inline` | proposé |
| Changement significatif en cours de sprint | `bmad-correct-course` | `inline` | proposé |
| Implémenter une intention, une story, un correctif — code écrit, revu, vérifié | `bmad-build` | `inline` | proposé |
| Boucle de développement non surveillée (une itération) | `bmad-build-auto` | `inline` | proposé |
| Exécution d'histoire conduite par un dev senior (Amelia) | `bmad-agent-dev` | `inline` | proposé |
| Générer des tests e2e sur une feature existante | `bmad-qa-generate-e2e-tests` | `bmad-test` | proposé |
| Conseil d'architecture de test (Murat) | `bmad-tea` | `bmad-test` | proposé |
| Plan / stratégie de test système ou epic (conception) | `bmad-testarch-test-design` | `bmad-test` | proposé |
| Initialiser un framework de test (Playwright, Cypress) | `bmad-testarch-framework` | `bmad-test` | proposé |
| Pipeline CI de qualité avec exécution des tests | `bmad-testarch-ci` | `bmad-test` | proposé |
| Tests d'acceptance en phase rouge avant le dev (ATDD) | `bmad-testarch-atdd` | `bmad-test` | proposé |
| Étendre la couverture de tests automatisés | `bmad-testarch-automate` | `bmad-test` | proposé |
| Revoir la qualité des tests écrits (étape `test` de dev-verifie) | `bmad-testarch-test-review` | `bmad-test` | d'office |
| Matrice de traçabilité exigences → tests, décision de gate | `bmad-testarch-trace` | `bmad-test` | d'office |
| Auditer les preuves NFR (perf, sécurité, fiabilité) | `bmad-testarch-nfr` | `bmad-test` | d'office |
| Apprendre les pratiques de test (session pédagogique, sans porteur) | `bmad-teach-me-testing` | `inline` | proposé |

**Le gel de `bmad-customize` est LEVÉ** (arbitrage utilisateur du 2026-07-31). L'arbitrage
`skills-jamais-utilisees` du 2026-07-27 avait posé « aucune customisation jusqu'à la v7 » :
la customisation attendait une version qui n'est toujours pas sortie (v6.12.0 installée le
2026-09-07, aucun tag `v7*`). La décision est de **rester en v6 et de
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

**Quatre skills ont été RETIRÉES sans shim** par la v6.12.0 — les nommer ne redirige nulle
part, elles ne sont plus sur le disque : `bmad-index-docs` et `bmad-shard-doc` (aucun
remplaçant : rédiger directement), `bmad-check-implementation-readiness` (absorbée par
`bmad-sprint-planning`, qui porte désormais la gate), `bmad-agent-tech-writer` — le persona
Paige est retiré du catalogue, ce qui fait passer les agents BMAD installés de 6 à 5.

**Jamais routées** — **dépréciées par BMAD** (21 noms, retirés en v7). Au hub, leurs shims
de compatibilité ont été **retirés du disque le 2026-09-08** et `installShims` passé à
`false` au manifeste (c'est ce drapeau, opt-in depuis la v6.12.0, qui les réinstallerait à
la prochaine mise à jour) ; chez les cibles ils peuvent subsister. Dans les deux cas, si
l'utilisateur les nomme, router vers la skill canonique et le dire :
`bmad-create-prd`, `bmad-edit-prd`, `bmad-validate-prd` → utiliser `bmad-prd` ;
`bmad-create-architecture` → utiliser `bmad-architecture` ;
`bmad-review-adversarial-general`, `bmad-review-edge-case-hunter`, `bmad-review-verification-gap`, `bmad-editorial-review`, `bmad-editorial-review-prose`, `bmad-editorial-review-structure` → utiliser `bmad-review` et nommer la lentille ;
`bmad-technical-research`, `bmad-domain-research`, `bmad-market-research` → utiliser `bmad-deep-recon` et nommer le type ;
`bmad-document-project`, `bmad-generate-project-context` → utiliser `bmad-project-context` ;
`bmad-quick-dev`, `bmad-dev-story`, `bmad-create-story` → utiliser `bmad-build` ;
`bmad-dev-auto` → utiliser `bmad-build-auto` ;
`bmad-checkpoint-preview` → utiliser `bmad-walkthrough` ;
`bmad-sprint-status` → utiliser `bmad-sprint-planning`.

<!-- BMAD-ROUTAGE:END -->

**Faut-il toujours passer par le sous-agent porteur ?** Non — le porteur sert à
*isoler* un travail BMAD long dans un contexte à lui, ou à en paralléliser plusieurs.
Quand la session principale est déjà sur le sujet et que la skill est bornée
(`bmad-advanced-elicitation` sur ce qu'on vient d'écrire, `bmad-help` pour trancher),
l'invoquer **inline** est plus direct et compte pareil au tableau de bord. La règle :
> une skill BMAD dont le travail tient dans la conversation courante s'invoque inline ;
> une skill qui va lire beaucoup de fichiers ou produire un gros artefact part en
> sous-agent, brief autoportant compris (§ 2 ter).

**Le brief nomme la skill — sinon « d'office » n'est une consigne pour personne.** Règle
posée le 2026-09-02, sur demande utilisateur de vérifier une information affichée par le
site. Elle était exacte, et pire que ce qu'elle disait : sur les **46 skills BMAD
installées, 2 seulement** avaient jamais été invoquées — `bmad-party-mode` (7 fois, par
les salles) et `bmad-customize` (1 fois, en direct), **toutes deux sans porteur**. Le
porteur `bmad-revue`, lui, a tourné **5 fois sans en charger une seule**, alors que son
mandat dit « invoque réellement les skills bmad-* » et qu'il déclare un champ
`SKILL INVOQUÉE` dans son contrat de sortie.

La cause n'est pas l'installation : les 46 skills sont bien là, au hub comme chez les
cibles (une exception, VSCode2 à 39). La cause est que **rien dans la chaîne ne portait
le nom de la skill à charger** — la table le dit à l'orchestrateur, le mandat le dit au
porteur, et le brief, seul document que le porteur reçoit réellement, se taisait. Trois
gestes, désormais obligatoires :

1. **Le brief porte le nom exact.** Dispatcher un porteur sans écrire « invoque
   `bmad-code-review` via l'outil `Skill` » revient à espérer qu'il retrouve la table
   tout seul — il ne l'a pas, son contexte est vierge (§ 2 ter). Le nom va dans le
   brief, pas dans l'intention.
2. **L'invocation est un contrat de sortie, donc vérifiable.** Le rapport doit ouvrir
   sur `SKILL INVOQUÉE : <nom>` ou sur `aucune` avec sa raison. Un rapport qui déclare
   une skill sans que l'étage 1 ait vu passer le `tool_use` correspondant est un écart
   mesurable, pas une question de confiance — le scan compte les invocations, sidechains
   comprises.
3. **Contrat non rempli → une relance ciblée, puis escalade** (§ 4), comme pour toute
   étape. Ne pas récrire le rapport à la place du porteur : ce serait reproduire à la
   main exactement ce qu'on cherche à faire faire par la skill.

Et la contrepartie honnête : si la skill n'apporte rien sur ce besoin précis, le porteur
écrit `aucune` et explique. Un rapport franc sans skill vaut mieux qu'un nom emprunté —
c'est le compteur d'usage qu'on veut juste, pas gonflé.

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
