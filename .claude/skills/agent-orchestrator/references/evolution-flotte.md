# Recommandation du superviseur et playbook evolution-flotte — détail du § 2 bis

<!-- Référence de la skill agent-orchestrator (divulgation progressive, lot 3,
     2026-10-02). Texte déplacé tel quel depuis SKILL.md : la règle courte reste
     dans SKILL.md, le détail, les mesures et l'historique vivent ici. -->

### 2 bis. Agir sur une recommandation du superviseur

Le superviseur *propose* (findings de `diagnostic.json`, avec un champ `proposition`),
l'utilisateur *arbitre*, **l'orchestrateur applique la version validée** — c'est la
boucle propose→arbitre→applique. Quand la demande est « applique la reco X », « traite le
finding Y », « corrige le point de pratique Z » (ou plus large : « traite tout ») :

1. **Lire les propositions** dans `.claude/supervision/diagnostic.json` (les mêmes que la
   section « Pratiques, couverture & risques » et les findings du wiki). Chaque finding
   porte `categorie`, `cible`, `titre`, `preuve`, `recommandation`, `proposition`. Les
   deux volets sont traitables :
   - **Usage des agents** (`ko-repete`, `inefficacite`, `agent-mort`, `interaction`,
     `verification-manquante`, `non-convergence`) → la proposition amende un skill, un
     playbook, un contrat d'étape, ou met un agent en sommeil.
   - **Pratiques d'ingénierie** (`pratique-test`, `pratique-dev`, `pratique-revue`,
     `pratique-design`) → la proposition installe un outil (coverage, linter), câble un
     hook (revue pré-commit), greffe une skill (`deck-design-review`), ou impose un audit
     `audit-technique` sur un projet cible.
   - **Documentation** (`pratique-doc`) → remédiation via `bmad-project-context`
     (règles agent d'un dépôt, brownfield compris), ou rédaction directe d'un
     README/CLAUDE.md manquant. La v6.12.0 a retiré `bmad-index-docs`, `bmad-shard-doc`
     et le tech-writer Paige **sans remplaçant** : sur ces trois besoins, la rédaction
     directe est le seul chemin restant.
   - **Cadrage produit** (`pratique-produit`) → remédiation via `bmad-product-brief`,
     `bmad-prd`, `bmad-forge-idea`, `bmad-agent-analyst`/`bmad-agent-pm` — famille
     `bmad-cadrage`, régime **proposé** (§ 2 quinquies) : l'orchestrateur annonce le
     livrable de cadrage visé et attend le feu vert avant de lancer.
2. **N'appliquer QUE l'arbitré.** Si l'utilisateur n'a pas explicitement validé, présenter
   la proposition et demander l'arbitrage — jamais d'auto-application, même « évidente »
   (gouvernance stricte, identique côté superviseur). « Traite tout » vaut arbitrage de
   l'ensemble des findings ouverts.
3. **Choisir le véhicule d'exécution** selon la cible de la proposition :
   - proposition qui touche **un autre projet de la flotte** (installer un linter sur
     VSCode2, greffer une skill sur VSCode4…) → instancier le playbook **`evolution-flotte`**
     (cadrage sur l'état réel → modif scopée → vérifs → commit limité au périmètre → wiki
     → journal).
   - proposition qui touche **ce projet-ci** (un skill/playbook/script local) → édition
     directe suivie de la vérification adaptée (py_compile, JSON valide, test).
4. **Enregistrer l'arbitrage** une fois appliqué : `.claude/supervision/arbitrages.json`
   (champ `cible` = celle du finding, `decision` = « ACCEPTÉ + APPLIQUÉ : <ce qui a été
   fait> »). Le scan clôt alors le finding (le wiki cesse de l'afficher en alerte). Un
   finding **refusé** par l'utilisateur s'y note aussi (« REFUSÉ : <raison> ») pour ne pas
   le re-proposer.
5. **Un travail laissé OUVERT se journalise en *finding*, jamais en *arbitrage*.**
   Finding `flotte:23-items-cadres-sans-canal-arbitrable` (2026-09-04) : un cadrage de
   23 items (aucun corrigé, juste évalués effort/risque) avait été tracé comme une
   entrée `arbitrages.json` — le fichier des décisions **closes** — alors que son propre
   texte disait « les items restent ouverts ». Résultat mesuré : `point_du_jour.py`
   répondait le soir même « rien n'attend votre arbitrage », et les 4 dépôts cibles
   n'avaient aucune trace locale à consulter en cross-session (2 sans session pair pour
   recevoir un `SendMessage`). `arbitrages.json` trace une **décision prise** (accepté,
   refusé, différé sur demande explicite) — jamais une **liste de travail restant à
   faire**. Un cadrage, une transmission par message à une session pair qui n'a pas
   encore répondu, ou un item explicitement hors périmètre du tour : ça va dans le
   `diagnostic.json` de la cible concernée (via `write_diagnostic.py --fusionner`, qui
   préserve les findings déjà ouverts de cette cible au lieu de les écraser — le mode
   par défaut n'a de sens que pour le diagnostic du hub lui-même, requalifié en entier
   à chaque passage d'`agent-supervisor`), jamais dans `arbitrages.json`.

Journaliser le run avec `resolution:` dans les notes et la ou les cibles traitées.
