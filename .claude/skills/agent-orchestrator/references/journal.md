# Journaliser — détail du § 5

<!-- Référence de la skill agent-orchestrator (divulgation progressive, lot 3,
     2026-10-02). Texte déplacé tel quel depuis SKILL.md : la règle courte reste
     dans SKILL.md, le détail, les mesures et l'historique vivent ici. -->

### 5. Journaliser

À la fin du run (succès **ou** échec), une ligne dans `.claude/orchestration/runs.jsonl` :

```bash
py .claude/orchestration/log_run.py '{"demande": "résumé court", "qualification": "orchestre", "playbook": "dev-verifie", "gabarit": "executor-lot", "topologie": "cascade", "plan": [{"etape": "revue design", "agent": "Explore", "mode": "parallele", "modele": "sonnet", "etat": "ok"}], "resultat": "succes", "reprises": 0, "notes": "", "livrable_utilisateur": false, "livrable_utilisateur_motif": "chantier interne, aucun artefact ouvert par un humain"}'
```

**`livrable_utilisateur` est OBLIGATOIRE** (booléen) depuis le 2026-09-19 — absent, le run
est **refusé** (rien n'est écrit). Il déclare, dès la composition du plan, si ce run produit
un artefact qu'un humain va ouvrir. Typer cela automatiquement est impossible à 0 token ;
une **déclaration**, elle, se vérifie à coût nul. Deux formes valides :

- `"livrable_utilisateur": false` **+ `"livrable_utilisateur_motif": "<pourquoi>"`** — le
  motif est exigé, sinon `false` devient la case à cocher qui désarme la garde ;
- `"livrable_utilisateur": true` **+ un bloc `validation`** — la *quittance nommée* :

```json
"validation": {"par": "Claude Camus", "artefact_ouvert": "C:/tmp/deck-restitution.pptx", "quand": "2026-09-19T14:00:00+02:00", "rapport": "deck ouvert dans PowerPoint, 14 slides lisibles"}
```

`par` = un sous-agent **réellement présent** comme `agent` dans une étape du plan, ou un nom
d'humain ; `artefact_ouvert` = chemin, URL servie ou capture, **non nullable dès que
`resultat: succes`** (un `par` rempli ne suffit pas) ; `quand` = horodatage. Quatre refus
mécaniques de `succes` : champ absent ; `true` sans `validation` ; `artefact_ouvert` vide ;
et — le plus important — `par: utilisateur-produit` dont le rapport porte un signal d'échec
produit (« PRODUIT NON OPERATIONNEL »…), auquel cas `en-attente-validation` est le mieux
atteignable : `utilisateur-produit` est un utilisateur **simulé**, il répond à « qui a été
simulé en train d'ouvrir la page », pas à « qui a ouvert la page » — sans ce refus, la garde
se signerait elle-même. Ce que la garde **ne ferme pas** : rien ne prouve que l'artefact cité
a été réellement ouvert (une déclaration suffit à passer), et les exécutions directes, hors
`log_run.py`, y échappent structurellement. **Non rétroactif** : au `--solde`, un run qui ne
porte pas le champ (les 194 d'avant le déploiement) n'est pas contrôlé.

(JSON aussi accepté sur stdin. Chaque étape du `plan` accepte un champ optionnel `etat`
(`ok` | `echec` | `non-rendu`) : `log_run.py` refuse un `resultat: succes` si une étape
porte `etat: echec` ou `etat: non-rendu` — un fan-out dont un sous-agent a échoué ou n'a
rien rendu ne peut pas être journalisé comme un succès global (motif OrchestraBench,
arXiv:2608.05263, veille 2026-09-08). Le même contrôle joue au `--solde` (revue de code du
2026-09-09 : il passait par la porte de derrière) ; un `etat` hors vocabulaire n'est refusé
que sur `succes`, un `echec` mal étiqueté reste journalisable (R5). `qualification` : `orchestre` | `direct-signale` ;
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

**Champs optionnels de l'optimiseur** (chantier `optimiser`, 2026-10-03). Tous absents
acceptés (runs antérieurs, `--solde` compris) ; présents mais invalides : refus qui liste
les valeurs permises (`verifier_gabarit_topologie`). Ils alimentent
`.claude/supervision/optimiseur.py`, qui ne compare que des cellules
(playbook, gabarit, topologie) complètes.

| Champ | Valeurs | Rôle |
| --- | --- | --- |
| `gabarit` | nom de fichier de `prompts/*.md` sans extension, ou `null` | Gabarit de brief utilisé |
| `topologie` | `agent-seul` \| `fan-out` \| `salle` \| `workflow` \| `cascade` | Forme du pilotage |
| `bras` | `temoin` \| `variante` \| `topologie-reduite` | Rôle dans une comparaison appariée ; `topologie-reduite` se compare à `variante` (le bras multi-agent) sur le même `tache_id` |
| `tache_id` | chaîne non vide | Identifiant commun aux deux bras d'une paire |
| `tokens`, `duree_s`, `budget_tokens` | nombre ≥ 0 ou `null` | Coût, durée, budget égal des deux bras |
| `famille` | chaîne non vide | Famille de tâches : un candidat n'est `gagnant` que s'il gagne sur ≥ 2 familles, sinon `donnees-insuffisantes` (« une seule famille ») |
| `branches_lancees`, `duree_branche_max_s` | entier ≥ 0 | Détection du fan-out dégénéré (topologie `fan-out`/`salle`/`workflow`) : `branches_lancees` ≤ 1 OU `duree_s` ≥ 0,9 × branches × branche la plus longue |
| `tour2` | `true` \| `false` | Salle à désaccord au tour 1 uniquement : `true` = tour 2 joué, `false` = désaccord sans tour 2. Rempli UNIQUEMENT si le tour 1 a produit un désaccord ; absent = pas de désaccord OU non renseigné (indistinguables), et `--salles` ne compte que les runs où le champ est présent |
| `voix` | liste de `{nom, modele, duree_s ≥ 0 fini, trouvailles_retenues ≥ 0}` | Rendement par voix d'une salle (une entrée par lentille) |

Les seuils du lot 2 (0,9 ; 2 familles ; 3 tâches par famille ; 4 runs résolus pour la
tendance ; fenêtre de 10 salles ; 5 séances) sont des estimations non mesurées. La tendance « tokens par run résolu »
(1re moitié contre 2de moitié des runs d'une cellule) s'affiche dans
`optimiseur.py --rapport` et n'entre jamais dans la porte.

## Annexe — §§ 1 et 1 bis, texte intégral avant le lot 3

### 1. Qualifier (silencieux, jamais mentionné à l'utilisateur si exécution directe)

- **Exécution directe** (pas d'orchestration, pas de journal) : une seule étape, un seul
  agent/skill évident, micro-tâche, question, correction en cours de tâche.
- **Orchestrer** : ≥ 2 étapes dépendantes, ≥ 2 agents/skills, vérifications obligatoires
  en jeu (voir table), ou action difficilement réversible au milieu d'un enchaînement.
  Orchestrer décide de la MÉTHODE (plan, modes, sous-agents) — pas du journal.
- **Journaliser (`log_run.py`) — uniquement une correction ou un bug traité.** Arbitrage
  utilisateur du 2026-09-07 : « ne prendre en compte à titre de run que les corrections et
  bugs ». Un run journalisé est un livrable de correction : correctif de code, bug traité,
  dette remboursée — avec sa preuve (commit, test) et, s'il ferme un finding, l'arbitrage de
  clôture qui va avec. Tout le reste, même orchestré — état des lieux, propagation de canon,
  réception d'un diagnostic ou d'une veille, reprise de travaux, cadrage, rapport — ne
  s'inscrit PAS dans `runs.jsonl`. Mesuré au wiki du 2026-09-07 : 16 runs à solder, dont 6
  aller-retours de slides et 6 « réception / reprise / lance les travaux » — du bookkeeping
  qui gonfle un compteur que personne ne solde, et qui noie les seuls runs qui comptent.

**Un aller-retour sur un livrable pas encore validé n'est jamais une nouvelle orchestration**
(mesuré au wiki du 2026-09-07 : 38 runs `en-attente-validation` flotte-wide, l'essentiel du
motif « refais la slide 3 », « toujours pas assez lisible », « ajoute X » sur un deck en
cours — pas de nouvelle demande, la continuation de la même). C'est le cas « correction en
cours de tâche » ci-dessus, donc exécution directe, jamais rejournalisé : le run déjà ouvert
reste `en-attente-validation` jusqu'à validation (`--solde`) ou jusqu'à une demande qui change
réellement de sujet (celle-là, orchestrable si elle qualifie). Journaliser un nouveau run à
chaque aller-retour ne mesure rien : ça dilue le seul signal qui compte (le livrable est-il
enfin validé ?) dans du bruit qu'aucun humain ne va soldé un par un.

### 1 bis. Les signaux de SessionStart se traitent au premier message, pas sur demande

Mesuré sur `runs.jsonl` le 2026-09-12 (157 runs) : 10 demandes portent sur « reprendre /
relancer les travaux » et 20 sur « traiter les findings/écarts » — un motif récurrent que
l'utilisateur a explicitement demandé de réduire. Le hook SessionStart annonce pourtant déjà
tout ce qui justifie ces demandes (reliquat non commité, N commits jamais poussés,
`point_du_jour.py` qui donne la commande exacte à taper). La reformulation répétée ne
comble pas un manque d'information — elle comble l'absence d'un premier geste avant que
l'utilisateur n'ait à la demander.

**Règle** : quand le hook SessionStart signale un reliquat non commité ou des findings/
trouvailles sans arbitrage, et que le premier message de l'utilisateur ne les mentionne pas
déjà, les traiter (ou au minimum les proposer explicitement) AU PREMIER TOUR de la session —
avant, ou en même temps que, la nouvelle demande. Ne pas attendre une formulation du type
« relance les travaux », « traite les findings » : le signal du hook EST la demande. Ça ne
dispense d'aucune des étapes qui suivent (qualifier, composer, valider si le geste est
coûteux ou irréversible) — ça évite seulement d'attendre une redite de ce que le hook a
déjà dit.
