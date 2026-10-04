# Mode neuronal ou classique — proposé selon l'action

Demande utilisateur du 2026-10-04 : « pouvoir choisir un mode neuronal ou un mode classique,
proposé selon l'action à réaliser ». À ne pas confondre avec les **modes d'exécution**
(synchrone, parallèle, asynchrone) de `SKILL.md` : ceux-là disent *comment* lancer, celui-ci
dit *combien d'agents* travaillent sur une même question et *comment* on consolide.

**Statut : essai borné.** Les paramètres du mode neuronal viennent du conseil de flotte du
2026-10-04 (4 voix ; au tour 2, trois voix convergent sur « 1 bras + escalade » ; la 4e voix,
Garde-fou, ne s'est pas prononcée sur le nombre de bras). Ils sont révisables jusqu'à
**n ≥ 10 runs journalisés par bras**. Fondement mesuré : deux tests (20 et 30 agents,
2026-10-03), consolidation manuelle, `optimiseur.py` à n = 0/10 pour `dev-verifie` — donc
**aucun gain démontré statistiquement**, seulement des indices.

## Principe

**La redondance sert à comprendre (diagnostiquer, prouver), jamais à écrire.** Un seul
rédacteur par périmètre de fichiers reste vrai dans les deux modes (§ 2 ter).

| | Classique | Neuronal (essai borné) |
| --- | --- | --- |
| Agents | 1 agent par tâche (cascade si dépendances) | **1 bras + escalade** : 1 bras Sonnet à brief amélioré ; **2e bras seulement si le 1er est sans preuve valide** (jamais 2 bras systématiques). Vérificateur Sonnet à contexte vierge seulement pour ce qu'un script ne rejoue pas |
| Preuve | mutation vue rouge pour toute prétention « corrigé » (clause 9) | la même, plus consolidation **pondérée par la preuve** (reproduction ou mutation > commit vérifié > affirmation), jamais au vote. **Une preuve rejouable par script : le script tranche**, pas un agent vérificateur |
| Arrêt | verdict de l'agent, vérifié | après 2 bras sans preuve : « Information insuffisante », l'utilisateur tranche |
| Coût | 1× | 1 bras, 2 si escalade : rejeu sur les mêmes données des tests du 2026-10-04 (`C:/tmp/stress/`, `bilan_neural.py`) — neural-30 : 9 tâches sur 10 couvertes avec 12 runs de bras au lieu de 20 ; neural-40 : 13 sur 13 avec 15 au lieu de 25 (petits échantillons) |
| Opus | selon la politique de modèle | hors des bras de diagnostic (à réévaluer à n ≥ 10) |

## Ce que les mesures justifient, et ce qu'elles ne justifient pas

Source : tableau finding × bras du test à 30 agents (consolidation manuelle, non rejouée).

- **Justifié : pas d'Opus en diagnostic.** Le bras Opus a rendu un verdict faux 4 fois sur 6,
  dont « déjà corrigé » à tort 2 fois.
- **Non justifié : « le neuronal rend un meilleur verdict ».** Un seul bras Sonnet à brief
  amélioré, ou à brief standard, a rendu 6 verdicts justes sur 6. Un 2e bras n'a rien changé au verdict.
- **Le seul gain observé du neuronal est la profondeur** : la cause racine d'un défaut d'outil (T1)
  et la reproduction d'un trou de garde (T3) n'ont été trouvées que par un seul bras (1 sur 4
  lancés pour T1, 1 sur 5 pour T3). C'est ce gain, sur deux cas d'un seul test, que l'essai borné
  doit confirmer ou infirmer — d'où l'escalade plutôt que 2 bras systématiques.
- **Justifié : le script décide quand la preuve se rejoue** (arbitrage utilisateur du
  2026-10-04). Mesure des tests du 2026-10-04 : les agents vérificateurs ont rendu 8 faux
  positifs sur 44 verdicts et 0 faux négatif ; ~6 min et 63 000 tokens par vérification contre
  11 à 39 s et 0 token pour le script (`check_all.py`). Le vérificateur agent ne reste que pour
  ce qu'aucun script ne rejoue.
- **Justifié : 1 bras + escalade plutôt que 2 bras systématiques** (arbitrage utilisateur du
  2026-10-04), rejeu sur les mêmes données — chiffres du tableau « Coût » ci-dessus.

## Quelle action, quel mode proposé

| Action | Mode proposé | Pourquoi |
| --- | --- | --- |
| Diagnostiquer un finding ouvert ; établir qu'un correctif est « déjà fait » ou « périmé » ; revue adverse autonome d'un livrable ou d'une conception | **neuronal** | Profondeur (voir ci-dessus) ; le verdict « déjà fait » sans preuve est le défaut mesuré |
| Plusieurs findings indépendants à trier en lot | **neuronal**, par finding | Taille du lot : plafonds et topologie de § 2 ter. Interactif (plafond de 10) : au plus 3 findings en fan-out `Agent` (9 agents), ou 4 si aucun 2e bras ne part (8 agents). Lot long en arrière-plan : jusqu'à 30 agents. Au-delà de 4 éléments indépendants : `Workflow` sur opt-in. Plus de 3 sous-agents = validation du § 3 |
| Implémenter, spécification nette, périmètre borné | **classique** | Redondance sur l'écriture = conflit de fichiers |
| Tâche mécanique : inventaire, extraction, régénération, propagation du canon, rendu du wiki | **classique** | Rien à départager |
| Changement à risque (garde, hook, canon/kit, registre) | **classique** pour l'écriture, + vérificateur obligatoire | § 2 ter ; le neuronal ne s'applique qu'au diagnostic préalable si la cause est incertaine |
| Revue d'un diff à committer | **classique** : le vérificateur à contexte vierge EST la revue | Pas de revue neuronale en plus du vérificateur obligatoire |
| Audit technique ou de sécurité | **skill `audit-technique` / agent `agent-securite`** | Protocole propre : ni l'un ni l'autre |
| Action irréversible (commit, push, suppression) ; livrable que l'utilisateur ouvre | **classique**, synchrone | Confirmation et validation humaines (§ 4) |
| Choix à instruire | **salle** | Protocole propre (§ 2 septies) : ni l'un ni l'autre |
| Veille | **lots exhaustifs** | Protocole propre (§ 2 sexies) : ni l'un ni l'autre |

**Lot mixte** (des findings à diagnostiquer et du code à écrire) : chaque action prend son mode,
le diagnostic en neuronal d'abord, l'écriture ensuite en classique. Une action à deux temps
(diagnostic puis correction) suit la même règle.

## Comment le mode se choisit

1. **L'utilisateur force** : la demande commence par `neuronal :` ou `classique :` (ou dit
   « en mode neuronal / classique ») — le mode est imposé, sans proposition.
2. **Sinon l'orchestrateur propose**, à l'étape 1 (Qualifier), en une ligne avec sa raison :
   « Mode proposé : neuronal — diagnostic d'un finding ouvert (3 agents au plus) ». Le
   **classique est le défaut et s'applique sans annonce** (coût 1×). Le **neuronal est
   annoncé** ; au-delà de 3 sous-agents il suit aussi la règle de validation du § 3.
3. Les deux modes partagent **les mêmes clauses de brief** (1 à 9 de § 2 ter) : seul change
   le nombre d'agents et la consolidation.

## Journal et indicateurs (fixés avant)

Valeurs que `log_run.py` accepte (lues dans le code, pas devinées) :

- `topologie` : `agent-seul` pour le classique, `fan-out` pour le neuronal (`TOPOLOGIES`, l. 448) ;
  il n'existe pas de valeur `neuronal`, en ajouter une est une demande séparée.
- `bras` : vocabulaire **fermé** `temoin | variante | topologie-reduite` (l. 449). Une valeur
  comme `neuronal` ou `A` est **refusée**. Pour comparer les deux modes sur une même tâche,
  journaliser deux runs de même `tache_id` : le classique en `temoin`, le neuronal en
  `variante` — la paire que lit `optimiseur.py`. Cette comparaison appariée coûte 2× : la
  réserver à des tâches choisies, et **omettre `bras` et `tache_id` en dehors d'elle**.
- `famille` : chaîne libre non vide, la famille de tâches (ex. `diagnostic-finding`).
- `forme_tache` : **jamais `decomposable` pour la redondance des bras.** `log_run.py` (l. 577-586)
  exige un motif de découpage en sous-questions indépendantes et écrit que « le même brief lu en
  parallèle » est de la redondance, pas du découpage. Un run neuronal déclare `inconnu` ou omet le
  champ ; la justification « pourquoi un seul agent ne suffit pas » (§ 2 ter) va dans le plan.
- Les modes proposé et choisi se notent dans `notes` (`mode_propose=…, mode_choisi=…`) tant que
  le journal ne les porte pas.

Indicateurs :

- part des verdicts neuronaux **infirmés en aval** (base : à constituer) ;
- tokens par finding ≤ 160 000 ;
- part des actions où l'utilisateur **change** le mode proposé (qualité de la proposition).

Sans effet mesuré sur 10 runs par mode, la règle est retirée ou ramenée au classique.
