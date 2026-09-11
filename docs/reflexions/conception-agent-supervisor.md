# Conception — agent superviseur (le POURQUOI)

> **Origine** : VSCode2, `export/agent-supervisor/conception.md`, dernier commit `f6504bb` du 2026-07-29 — le fichier n'est plus dans l'arbre de VSCode2 (export/ supprimé), il se lit par `git -C ~/Documents/VSCode2 show f6504bb:export/agent-supervisor/conception.md`.
> **Reprise au hub** : 2026-09-02 — reprise **partielle**, assumée telle.
> **Repris** : §1 les 4 familles d'agents et leurs canaux d'observation, §4 l'architecture à 2 étages (le repère), §6 propose→arbitre→applique (rationale), §9 anti-auto-complaisance et 5 constats max (rationale), §10 décisions avec leur statut RÉEL au hub.
> **Écarté** : §2 état des lieux daté, §3 sources dont `rtk` (outil retiré de la flotte le 2026-07-29), §5 table de métriques, §7-§8 restitution et phasage, §11 anecdote datée, incréments A/B/C — journal de projet VSCode2, pas du rationale.
> Ce document explique le **POURQUOI** ; le QUOI opérationnel vit dans la skill `.claude/skills/agent-supervisor/SKILL.md`.

La numérotation est celle de la source, trous compris : `§4` ici est bien `§4` là-bas.

## 1. Ce qu'on appelle « agents » — et par quel canal chacun s'observe

Le mot recouvre des familles distinctes qui **ne s'observent pas par les mêmes canaux**.
C'est le point de départ de toute la conception : il n'existe aucun canal unique, donc pas
de « compteur d'agents » universel — chaque famille impose sa source.

| Famille | Où | Observable via |
| --- | --- | --- |
| Skills de projet | `.claude/skills/` versionné | transcripts de session (invocations `Skill`) |
| Skills BMAD (46, `bmad-*`) | `.claude/skills/` | transcripts (idem) + artefacts `_bmad-output/` |
| Sous-agents du harness (`Explore`, `Plan`, `general-purpose`…) | harness, sans fichier local | transcripts (invocations `Agent`/`Task`) |
| Agents OpenHub (`.opencode/`, CLI externe) | code applicatif de VSCode2 | table `AgentResult` en base + logs applicatifs |

Les skills globales de l'utilisateur (`~/.claude/skills/`) forment une 5ᵉ population : hors
périmètre projet, mais visibles dans les mêmes transcripts — autant les mesurer aussi.

**Écart au hub, à connaître avant de lire ce tableau comme un état des lieux.** La source
notait « `.claude/agents/` n'existe pas » : c'était vrai de VSCode2, ça ne l'est plus ici —
le hub porte 4 sous-agents maison (`agent-supervisor`, `bmad-revue`, `bmad-recherche`,
`veille-agentic`, `ls .claude/agents/` le 2026-09-02), tous porteurs de l'outil `Skill`,
donc **leurs invocations sont comptées** par l'étage 1, qui ne filtre pas les sidechains.
La famille OpenHub, elle, n'existe pas au hub (cf. §10.2). Ce qui reste vrai et structurant,
c'est le principe : identifier le canal AVANT de prétendre mesurer une famille.

## 4. Architecture : « tâche de fond » ≠ démon — le repère à 2 étages

Claude Code est orienté session : il n'existe pas de processus superviseur permanent natif.
Les seuls mécanismes réels d'un « automatique par défaut » sont les **hooks**
(SessionStart / PostToolUse / Stop — déterministes, gratuits en tokens), la **skill
invocable** (analyse LLM à la demande), les agents **planifiés** dans le cloud (vraie
périodicité, mais facturés et distants — inadaptés à des données locales sensibles) et
`/loop` (une boucle dans une session ouverte, pas une tâche de fond).

D'où l'architecture hybride, qui est *le* repère du dispositif :

- **Étage 1 — collecteur déterministe (0 token, automatique)** : scripts branchés sur les
  hooks, qui journalisent les invocations et agrègent les transcripts en tableaux de bord.
  Tourne toujours, ne consomme rien.
- **Étage 2 — analyseur LLM (sur déclencheur, jamais en continu)** : une skill qui lit les
  **agrégats** de l'étage 1 — jamais les JSONL bruts — échantillonne ce qui est signalé et
  produit le diagnostic qualitatif.

Deux raisons, toutes deux non négociables : le **coût** (tout ce qui est comptable doit
être déterministe, le LLM ne voit que des synthèses) et la **confidentialité** (les
transcripts contiennent du contenu client — analyse strictement locale, jamais d'agent
cloud planifié sur ces données).

Corollaire que le hub a payé depuis : **l'étage 1 mesure la présence, jamais le
fonctionnement** (règle R6 de `CLAUDE.md`). Une skill comptée « installée » peut ne pas
démarrer. L'étage 1 dit ce qui existe et ce qui est invoqué ; il ne dit pas que ça marche.

## 6. Le superviseur challenge, il n'applique pas — propose → arbitre → applique

Le superviseur ne se contente pas de compter : il produit des recommandations actionnables
(mise en sommeil d'une skill jamais utilisée, customisation d'un skill sous-performant,
correction d'un **contrat d'interface** entre deux agents dont l'enchaînement échoue, ajout
de la vérification systématiquement oubliée). Mais chacune est une **proposition** :
l'humain arbitre, l'orchestrateur applique la version validée. Jamais d'auto-application,
même « évidente ».

Le rationale est double. D'abord la **séparation des pouvoirs** : celui qui diagnostique ne
doit pas être celui qui corrige — sinon plus rien ne contrôle le diagnostic. Ensuite la
**traçabilité de la décision** : un correctif auto-appliqué ne laisse pas de trace de
*pourquoi* on l'a voulu, et l'on re-diagnostique indéfiniment ce que l'humain avait déjà
tranché. C'est ce second point qui a imposé un fichier d'arbitrages versionné : le cycle
propose→arbitre existait pour les *propositions* de l'étage 2, pas pour les *constats*
déterministes de l'étage 1, qui repartaient donc après arbitrage.

Garde-fous à conserver : l'usage réel reste mesuré (la liste « jamais utilisés » ne ment
pas), **un arbitrage n'est pas une preuve d'utilité** — l'étage 2 peut le re-challenger sur
données nouvelles — et une entrée d'arbitrage invalide est ignorée sans bloquer le scan.
Au hub, la règle est devenue R4 de `CLAUDE.md`.

## 9. Risques et garde-fous — dont l'anti-auto-complaisance

- **Coût tokens** : le risque n°1 est un superviseur qui coûte plus qu'il ne rapporte.
  Garde-fou : tout ce qui est comptable est déterministe (étage 1) ; le LLM ne voit que des
  synthèses, sur déclencheur explicite.
- **Confidentialité** : contenu client dans les transcripts → analyse strictement locale.
- **Auto-complaisance** : le superviseur (LLM) évalue des actions produites par le même
  modèle. C'est le biais structurel du dispositif, et il n'a qu'un seul remède :
  **ancrer chaque verdict sur un signal objectif** — erreur d'outil, reprise, revert git,
  correction explicite de l'utilisateur — jamais sur une auto-appréciation. « Pas de constat
  sans preuve » n'est pas une exigence de style : c'est ce qui empêche le superviseur de se
  décerner un satisfecit.
- **Faux positifs « KO répété »** : une réédition rapide peut être une itération normale.
  Les heuristiques **signalent**, le diagnostic LLM **qualifie**, l'humain **tranche** —
  trois rôles, jamais confondus.
- **Un rapport que personne ne lit** : c'est la justification des **5 constats maximum,
  priorisés**. Un diagnostic exhaustif finit comme les skills BMAD installées et jamais
  invoquées — présent, ignoré. La limite n'est pas une contrainte de format, c'est une
  contrainte de lecture : au-delà, plus rien n'est arbitré, donc le dispositif ne produit
  plus de décision.

## 10. Décisions — et leur statut RÉEL au hub le 2026-09-02

1. **Cadence de l'étage 2** (à chaque revue d'incrément, à seuil, ou hebdomadaire ?).
   **Dépassée en mieux** : les deux à la fois. Cadence temporelle —
   `.claude/supervision/scan_transcripts.py:96` (`DIAGNOSTIC_CADENCE_DAYS = 14`), signalée
   par le hook SessionStart (`scan_transcripts.py:1565` : « diagnostic agent-supervisor a
   lancer ou perime ») — **et** péremption à l'activité :
   `scan_transcripts.py:97` (`DIAGNOSTIC_STALE_RUNS = 3`), 3 orchestrations non couvertes
   périment le diagnostic quelle que soit la date. L'insertion dans la revue de fin
   d'incrément est portée par `.claude/skills/revue-increment/SKILL.md:83`.
2. **Périmètre OpenHub** (dès le début, ou plus tard ?). **OUVERTE au hub, et sans objet en
   l'état.** Même question que la décision n°4 du document orchestrateur : à trancher UNE
   fois, là-bas — ce paragraphe n'est que le constat côté superviseur, pas un second to-do. Le code de couverture existe — `openhub_stats()`,
   `.claude/supervision/scan_transcripts.py:749`, lisant en lecture seule un
   `REPO/data/app.db` (chemin l. 80-82) — mais il est **hérité de VSCode2 et mort ici** :
   mesuré le 2026-09-02, ni `data/app.db` ni `.opencode/` n'existent au hub, et
   `catalogue.md` ne mentionne aucune famille OpenHub. Aucune trace d'arbitrage sur ce point
   (`grep -i "openhub" .claude/supervision/arbitrages.json` : 1 occurrence, dans
   l'arbitrage de retrait de `rtk`, sans rapport). Reste à trancher : garder ce code inerte
   dans le canon, ou le retirer.
3. **Sort des 46 skills BMAD** (le superviseur peut-il proposer une liste de
   désinstallation dès le premier incrément ?). **Dépassée en mieux** : la question portait
   sur *quoi désinstaller*, la réponse arbitrée le 2026-07-30 a été *comment les faire
   servir* — routage par besoin détecté, deux régimes de déclenchement
   (`.claude/skills/agent-orchestrator/SKILL.md:249`). La mise en sommeil existe toujours
   comme geste, mais elle a frappé les **porteurs** plutôt que les skills : 4 sous-agents
   jamais invoqués mis en sommeil le 2026-09-01 (`bmad-doc`, `bmad-cadrage`,
   `bmad-livraison`, `agent-orchestrator` — `.claude/orchestration/catalogue.md:53-59`).
4. **Portée : superviseur propre à un projet, ou réutilisable multi-projets ?**
   **Tranchée dans les faits, jamais arbitrée par écrit.** La réponse effective est la
   troisième voie, que la conception n'envisageait pas : ni skill locale à chaque projet, ni
   skill globale dans `~/.claude/skills/`, mais **un dépôt hub dédié qui supervise les
   autres** — ce projet. Vérifié le 2026-09-02 : `ls ~/.claude/skills/` ne contient aucune
   skill de supervision (5 skills, toutes de livrable ou d'outillage) ; `agent-supervisor`
   vit dans le `.claude/skills/` du hub. Aucune entrée d'arbitrage ne porte cette décision
   (`grep -i "multi-projet\|portee"` sur `arbitrages.json` : aucune correspondance sur ce
   sujet). C'est la décision la plus structurante du dispositif — elle explique R1, R2 et R3
   de `CLAUDE.md` (agir sur d'AUTRES dépôts) — et c'est celle dont il n'existe aucune trace
   de délibération : à confirmer explicitement, ou à requalifier.
