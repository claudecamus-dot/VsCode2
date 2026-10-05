# Veille sur cadence — détail du § 2 sexies

<!-- Référence de la skill agent-orchestrator (divulgation progressive, lot 3,
     2026-10-02). Texte déplacé tel quel depuis SKILL.md : la règle courte reste
     dans SKILL.md, le détail, les mesures et l'historique vivent ici. -->

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

1. **Régénérer le wiki** — au HUB, `py scripts/scan_projets.py` (ce script n'est pas
   déployé : depuis une cible, il n'y a pas de wiki à régénérer) : la section 3 « Veille agentic »
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
5. **Séparer latence et budget.** Dans une entrée de veille, noter à part un gain de latence et un gain à budget égal (tokens) : un papier qui accélère n'a pas prouvé qu'il coûte moins (veille du 2026-10-05, préprints 2602.02276 et 2605.02801).
