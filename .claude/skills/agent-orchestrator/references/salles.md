# Convoquer une salle — détail du § 2 septies

<!-- Référence de la skill agent-orchestrator (divulgation progressive, lot 3,
     2026-10-02). Texte déplacé tel quel depuis SKILL.md : la règle courte reste
     dans SKILL.md, le détail, les mesures et l'historique vivent ici. -->

### 2 septies. Convoquer une salle — faire délibérer AVANT de planifier

Le hub porte **12 salles** de table ronde (`_bmad/custom/bmad-party-mode.toml`), rendues
dans l'onglet Dispositif du wiki avec leur casting et leur commande.

**Qui tient la salle : la session PRINCIPALE, jamais un sous-agent.** Ses voix partent
dans UN SEUL message (fan-out), et la salle ne conclut qu'après avoir écrit dans son
compte rendu « N voix lancées, N rendues ». Motif observé : un sous-agent qui tient la
salle joue lui-même les voix (3 transcripts sur 4) ou les lance en arrière-plan et clôt
son tour (1 sur 4) — « In non-interactive mode and the Agent SDK, the launching subagent
doesn't wait » (https://code.claude.com/docs/en/sub-agents) ; en interactif, il attend.

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
(« régénère le wiki », « solde les runs ») : une salle y ajouterait un tour de parole et
zéro information.

**EXCEPTION À CE QUI PRÉCÈDE — tout travail de DÉVELOPPEMENT encadre son exécution par
deux salles, systématiquement** (demande utilisateur du 2026-09-27, qui REMPLACE pour ce
cas l'exemption « corrige ce bug » ci-dessus — l'exemple a d'ailleurs été retiré de la
liste, il contredisait la règle). Un chantier de dev — correctif, feature, dette, garde
neuve — s'ouvre par une **salle de dev** (`atelier-dev` : structure, partition de fichiers
exclusive, contrat de preuve) et se ferme par une **salle de revue de dev**
(`code-review-crew`) sur le diff réellement produit, dans cet ordre, l'exécution entre les
deux. Ces deux salles sont les étapes `salle-dev` et `salle-revue-code` du playbook
`dev-verifie` : les sauter n'est pas une décision de l'orchestrateur.

**L'exception se DEMANDE, elle ne se décide pas.** Si le chantier paraît trop petit pour
deux salles (une ligne, un renommage, un chiffre), poser la question à l'utilisateur en
une ligne — quelle salle serait sautée et pourquoi — et attendre sa réponse. Un
orchestrateur qui s'exempte lui-même au motif que « c'est évident » rouvre exactement le
motif que R4 ferme. Le coût (3 à 5 sessions par salle) est un argument à présenter dans la
question, jamais un motif de sauter en silence.

**Comment.** `/bmad-party-mode --party <salle> --mode subagent`, en énonçant le sujet
juste après. Le mode compte : `session` fait jouer toutes les voix par une seule, donc
**aucun débat réel** — `subagent` donne à chaque persona son propre contexte, et c'est la
seule façon qu'elles se contredisent. Deux tours au minimum : positions indépendantes,
puis confrontation. Depuis le wiki, le bouton « Déclencher » (« En débattre » jusqu'au
2026-09-01) lance exactement la même chose sans terminal.

**Coût.** Une salle en `subagent` = une session par voix, soit 3 à 5 sessions. C'est le
prix du désaccord réel ; il ne se paie que sur un vrai choix. Une seule salle à la fois.

**Quatre mesures contre la salle qui traîne ou qui reste muette** (arbitrage utilisateur
2026-09-27, motivé par l'atelier-dev n°4 : un précédent atelier a duré ~2 h, et un
exécutant du même jour 3 h 07 dont ~2 h 35 bloquées sur un hook orphelin figé sur stdin —
cf. § du chien de garde ci-dessous). Régime tenu ce jour-là : les quatre voix mesurées ont
rendu en 8-11 min chacune (notifications à 483 s, 582 s, 649 s, 699 s).

- **(a) Chien de garde périodique.** `.claude/supervision/convergence.py` existe déjà et
  REFUSE toute salle NEUVE tant qu'une salle muette au-delà du budget n'est pas tranchée —
  mais rien ne le rappelle pendant qu'une salle DÉJÀ lancée tourne : c'est une garde au
  lancement, pas un réveil périodique. Le réveil est désormais un MÉCANISME, plus une
  consigne : l'orchestrateur arme `Monitor` sur
  `py .claude/supervision/chien_de_garde.py --boucle --intervalle 300` (timeout 30 min,
  réarmé à expiration) dès qu'une salle ou un exécutant part en arrière-plan ; il ne
  signale que les agents EN VOL (dédoublonnage 30 min). Fait mesuré le 2026-09-28 : la
  version faite à la main (appel de `convergence.py` toutes les ~15 min) a produit 2 faux
  positifs sur des agents déjà finis (H4/H2/R2) — corrigé dans b7b25e6. Sur
  une voix silencieuse, vérifier le DISQUE d'abord (le « dernier signe de vie » que
  `convergence.py` calcule sur `subagents/agent-<id>.jsonl` — jamais `tasks/*.output`, qui
  reste à 0 octet même pour un agent terminé, § « FAIT DÉCOUVERT AU LANCEMENT »), puis
  `--clore` ou `--deroger` nommément. Diagnostiquer le processus de hook orphelin (root
  cause du 2026-09-27) revient à `convergence.py` en complément (lecture bornée de stdin
  côté hooks : anthropics/claude-code#87289) — diagnostic seulement, il ne tue rien.
- **(b) Voix en sonnet.** Toute voix de salle tourne en `sonnet` ; `opus` réservé au SEUL
  rôle structurant explicitement identifié dans le manifeste de la salle — le Charpentier
  d'`atelier-dev`. `code-review-crew` n'a pas d'équivalent : ses cinq lentilles
  (sec-hawk/adversary/edge-hunter/craftsman/shipper) attaquent chacune son propre angle en
  parallèle, aucune n'arbitre une structure avant les autres — sonnet pour les cinq. Écrit
  par membre (`model`) dans `_bmad/custom/bmad-party-mode.toml`, lu par `resolve_party.py`.
- **(c) Tour 2 seulement sur désaccord réel.** Le deuxième tour de confrontation ne se
  lance que s'il reste un désaccord réel après le premier tour (positions indépendantes) —
  un arrêt adaptatif sur consensus, pas un nombre de tours fixé d'avance. Source : préprint
  arXiv:2605.19193 — **préprint, auteur unique, non relu par les pairs** : à traiter comme
  une heuristique à confirmer sur la flotte, pas un résultat établi.
- **(d) Budget explicite par voix.** Chaque brief de voix porte `BUDGET : <n> min` (le
  format lu par `convergence.py` et `log_usage`, § 2 ter du gabarit de brief) et une
  longueur de rendu visée (≤ 1200 tokens).

**Les skills BMAD de la salle vont dans le brief des voix — `skills_bmad`.** Règle posée
le 2026-09-02, sur demande utilisateur (« 44 sur 46 skills ne sont jamais utilisées,
raccorde aux salles »). Chaque salle déclare désormais, dans
`_bmad/custom/bmad-party-mode.toml`, le champ **`skills_bmad`** : les skills que ses voix
doivent réellement charger via l'outil `Skill`. **Lis-le en même temps que le manifeste**,
et recopie le nom exact dans le brief de la voix concernée — une voix part avec un contexte
vierge, elle n'a ni la table de routage ni le TOML.

Deux points que la mesure impose :

- **Seules les skills du régime « d'office » y figurent** (8 ; les 3 passes TEA d'office
  partent par `bmad-test`, elles écrivent leur rapport), et un test l'exige. Elles étaient
  13 avant la migration v6.12.0 : la consolidation des cinq lentilles de revue dans
  `bmad-review` et des trois recherches dans `bmad-deep-recon` en a retiré cinq **sans rien
  retirer de couvert** — le besoin est le même, il passe par une skill au lieu de trois. Une salle
  ne modifie aucun fichier : y router une skill qui écrit casserait son invariant, c'est-à-dire
  la garde de R4 contre une auto-application collective. Les 21 « proposé » restent
  atteignables par le porteur ou en inline, sur arbitrage.
- **`resolve_party.py` ne remonte PAS ce champ** — il ne rend qu'un jeu de clés fixe. C'est
  toi qui lis le TOML, ce que ce paragraphe t'impose déjà pour le manifeste. Patcher le
  résolveur aurait été plus direct et se serait perdu à la première mise à jour de BMAD.

Et la limite, à ne pas maquiller : ce raccord ne fait pas tomber « 44 » à zéro, et ne le
doit pas. Il garantit qu'aucune des 13 utilisables n'est ORPHELINE — sans salle qui la
nomme, donc sans chemin par lequel elle puisse partir. Forcer une skill à s'exécuter pour
faire baisser un compteur produirait un compteur qui mesure sa propre complaisance.

**Une salle neuve n'entre pas dans le kit publié sur son test de câblage.** Règle posée
le 2026-09-01 (finding `salles:accueil-projet,conseil-flotte,atelier-deck,mise-en-service`,
arbitré « rien retirer, poser la règle anti-récidive »). Le dispositif est passé de 9 à
12 salles pendant que quatre de la première génération n'avaient jamais siégé ailleurs
que dans leur propre run de création — et la réponse apportée avait été d'en créer trois
de plus. Convocations mesurées le 2026-09-01 sur les 97 runs : `atelier-idees` 7,
`atelier-dev` 4, `revue-consommation` 3, `observatoire-agentic` 2 ; `conseil-flotte`,
`atelier-deck`, `mise-en-service` et `socle-technique` 1 chacune — leur run de création ;
`accueil-projet`, `code-review-crew`, `inspection-critique` et `anti-consensus-club`
**zéro**. Une salle se publie donc après une **convocation réelle sur une demande
utilisateur**, jamais après le test qui prouve qu'elle est atteignable.

Et se garder de la lecture inverse : ces salles ont toutes un déclencheur nommé dans
`SALLES-ROUTAGE` — `tests/test_salles_routage.py` l'exige déjà de chacune. Le déclencheur
n'est donc pas ce qui leur manquait, et lui en ajouter un n'aurait rien changé. Ce qui
manque à une salle jamais convoquée, c'est une demande qui lui ressemble ; si aucune n'est
venue en un mois, la question est sa raison d'être, pas son câblage. Aucune n'a été mise
en sommeil le 2026-09-01 : trois des quatre à zéro dataient de la veille, et les juger à
un jour aurait été ne pas leur laisser leur chance.

<!-- SALLES-ROUTAGE:START — table verrouillée par tests/test_salles_routage.py : toute
     salle citée ici doit exister dans _bmad/custom/bmad-party-mode.toml, et toute salle
     du TOML doit être routée ici (sinon elle est inatteignable depuis une demande). -->

| La demande ressemble à… | Salle | Ce qu'elle apporte |
| --- | --- | --- |
| « ce bug touche trois couches, par où commencer ? », partition d'un chantier de code, structure d'un code existant à faire évoluer, **choix du langage ou de la pile** la mieux adaptée à la situation | `atelier-dev` | Le Charpentier pose la structure et les frontières AVANT qu'on réparte les fichiers, les trois dev nomment leur périmètre exclusif, le Relecteur dit ce qui bloquera en revue |
| « on adopte cette pratique ou pas ? », arbitrer un finding, revue périodique du dispositif | `conseil-flotte` | Vigie l'état de l'art, Argus les mesures, Quincaillier l'existant, Garde-fou le coût de maintenance |
| « ce deck est correct mais ne ressemble à rien », concevoir/contrôler une restitution | `atelier-deck` | Maquettiste la fabrication, Contrôleur le gabarit, Sally le regard de celui qui reçoit |
| « est-ce prêt à passer en production ? », environnements, secrets, exploitation | `mise-en-service` | Aiguilleur les environnements, Passerelle ce qui sort du poste, Archiviste la doc, Garde-fou les tests |
| « pourquoi ma consommation a doublé ? », cette dépense a-t-elle acheté quelque chose | `revue-consommation` | Jauge les chiffres, Argus les runs joués, Quincaillier les outils qui tournent pour rien |
| « un nouveau projet arrive, personne ne le connaît » | `accueil-projet` | Salle open-cast : elle génère les voix du cadrage, sans relais écrit d'avance |
| « ce code me paraît risqué sans que je sache dire pourquoi » | `code-review-crew` | Cinq angles distincts (sécurité, contradiction, cas limites, artisanat, livrer) qui se disputent |
| « j'ai une intuition, pas encore une question », refonte, organisation de l'information, navigation, simplification | `atelier-idees` | Le Cadreur pose le problème avant les solutions, Portevoix parle pour l'usager absent, Wildcard ouvre les options, Splinter casse l'accord facile |
| « il faudrait relire tout ça à froid », inspection périodique, chasse aux fonctionnalités que plus personne n'utilise, **revue approfondie d'un texte long publié (cohérence, redondance, formulation)** | `inspection-critique` | Quatre axes tenus séparés — bugs latents, design (un texte long y entre au même titre qu'un écran), expérience de celui qui s'en sert, et ce qui n'est jamais utilisé ; part d'un périmètre et de mesures d'usage, pas d'un diff. « Sonner IA » n'est pas un critère qu'elle instruit — arbitré non mesurable le 2026-09-18, aucune détection fiable et non contournable n'existe |
| « où tournent nos environnements et combien ça coûte ? », **choix de l'environnement de production**, infrastructure, secrets, reprise après incident | `socle-technique` | Le parc décrit avant d'être corrigé, les risques triés par risque et non par facilité ; tient l'infrastructure dans la durée là où la mise en service est un guichet par release |
| « qu'est-ce qui se fait ailleurs ? », état de l'art agentic, pratiques des fournisseurs IA, littérature scientifique et publications | `observatoire-agentic` | Elle CLASSE ce qu'elle lit — prouvé, sorti, annoncé, hype — et exige la source primaire ; elle ne décide pas d'adopter, elle dit ce que la chose vaut et ce qu'elle coûte à vérifier |
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
7. **Porter une case dédiée « désaccord(s) documenté(s) : qui, quoi — ou aucun et
   pourquoi »**, distincte de la synthèse du point 3 (veille adoptée 2026-09-08,
   Deliberative Illusion, arXiv:2606.03032) : une délibération multi-agents peut faire
   disparaître les faits nuancés au fil des tours (attrition factuelle) et faire
   converger les postures artificiellement (homogénéisation) sans que le désaccord de
   fond soit résolu. Une absence TOTALE de désaccord documenté sur un sujet qui a
   justifié la convocation d'une salle est en soi un signal à interroger, pas une preuve
   de consensus solide.
8. **Porter une case « faits du premier tour absents de la synthèse »** (veille adoptée
   2026-09-23, même papier, arXiv 2606.03032 — Wan, Wu, Luo, Li, Wang, Chen, Kan,
   préprint) : recroiser les positions indépendantes du tour 1 avec la synthèse finale
   et nommer chaque fait chiffré ou nuance qui y figurait et a disparu. C'est la mesure
   directe de l'attrition factuelle ; la case 7 ne voit que les désaccords, pas les
   faits qu'un accord a laissé tomber en route. « Aucun » exige d'avoir fait le recoupement.

Le reste — transcription, ordre des tours, qui a bougé — vient après, pour qui veut
vérifier. Personne ne décide en lisant un dialogue.
