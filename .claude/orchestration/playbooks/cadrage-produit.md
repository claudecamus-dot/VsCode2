# Playbook `cadrage-produit` — chaîner idée → PRD → architecture → UX, avant tout code

Ce playbook couvre le **sous-ensemble réellement linéaire** de la famille BMAD
cadrage/spec/produit : `bmad-forge-idea`/`bmad-prfaq` (durcir l'idée) →
`bmad-product-brief` (brief) → `bmad-prd` (exigences) → `bmad-architecture` (invariants
techniques) → `bmad-ux` (patterns d'interaction) — puis **relais explicite** vers
`dev-verifie` pour `cadrage-epics`/`gate-cadrage`/`implementation`, qui restent la
propriété de ce dernier (ne pas dupliquer les étapes bloquantes déjà posées là-bas,
arbitrage du 2026-09-16).

**Pourquoi un playbook séparé plutôt qu'une extension de `dev-verifie`** (arbitrage du
2026-09-21, finding `VScode5:raccordement-bmad-epics-spec-qa`) : `dev-verifie` sert des
chantiers qui n'ont souvent ni brief ni PRD — sa gate `cadrage-epics` FAIL explicitement
vers `bmad-prd` quand `PRD.md` manque, plutôt que de l'exiger d'office. Router TOUT
chantier de code par ce playbook-ci imposerait un cadrage produit lourd à des correctifs
qui n'en ont pas besoin. `cadrage-produit` s'instancie donc en AMONT, sur décision
explicite (une intention produit neuve, pas un bug), et remet la main à `dev-verifie` une
fois le PRD + l'architecture écrits.

**Ce qui reste délibérément HORS de ce playbook**, et pourquoi — trois skills de la même
table de routage qui semblaient candidates ne le sont pas :
- `bmad-spec` : chemin ALTERNATIF condensé, pas une étape de cette chaîne — sa propre
  description dit « condense any input... into a short spec », elle REMPLACE
  brief+prd+architecture pour un cas léger. L'utiliser DANS ce playbook doublonnerait son
  usage seul.
- `bmad-project-context` : documente un dépôt EXISTANT (brownfield), aucun rapport avec
  le cadrage d'une intention neuve.
- `bmad-correct-course` : s'invoque EN COURS de sprint, jamais en amont.

## Statut

`jamais-joue` — composé le 2026-09-21 depuis le finding ci-dessus, aucune exécution
réelle encore. À faire évoluer vers `eprouve` après un premier run réussi
(routing-hints le fera).

```json
{
  "nom": "cadrage-produit",
  "description": "Chaîner forge-idea/prfaq -> product-brief -> prd -> architecture -> ux pour une intention produit neuve, avant de remettre la main à dev-verifie (cadrage-epics/gate-cadrage/implementation).",
  "statut": "jamais-joue",
  "source": "manuel",
  "declencheurs": [
    "intention produit neuve sans brief ni PRD existant",
    "durcir une idée avant de l'engager en développement",
    "cadrer un besoin utilisateur avant d'écrire des epics/stories"
  ],
  "etapes": [
    {
      "id": "durcir-idee",
      "agent": "skill bmad-forge-idea ou bmad-prfaq",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "llm",
        "critere": "CONDITIONNEL : seulement si l'idée est encore floue (pas déjà un besoin net) -- si le besoin est déjà clair, sauter cette étape. bmad-forge-idea pour une critique adverse en questions ; bmad-prfaq pour tester un concept par la méthode Working Backwards. Régime proposé : annoncer et attendre le feu vert avant de lancer (écrit potentiellement un fichier)."
      },
      "checkpoint": "annonce + feu vert avant lancement"
    },
    {
      "id": "cadrage-brief",
      "agent": "skill bmad-product-brief",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "regime": "propose (ecrit un fichier reel) : annoncer et attendre le feu vert",
        "critere": "brief produit (probleme, utilisateurs, contraintes) relu par l'utilisateur avant de passer a cadrage-prd ; session principale, jamais delegue a un sous-agent sans TTY (menus interactifs des skills BMAD step-file)"
      },
      "checkpoint": "annonce + feu vert avant lancement (écrit un fichier réel)"
    },
    {
      "id": "cadrage-prd",
      "agent": "skill bmad-prd",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "regime": "propose (ecrit PRD.md) : annoncer et attendre le feu vert",
        "critere": "PRD.md produit à partir du brief -- exigences nommées avec critère d'acceptation vérifiable, pas un paragraphe générique. C'est CE fichier que dev-verifie/cadrage-epics exige en prérequis bloquant : ne pas le sauter si le chantier ira ensuite chez dev-verifie."
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
        "critere": "Architecture.md produit à partir du PRD ; invariants d'architecture nommés, pas un paragraphe générique. C'est CE fichier que dev-verifie/cadrage-epics exige aussi en prérequis."
      },
      "checkpoint": "annonce + feu vert avant lancement (écrit un fichier réel)"
    },
    {
      "id": "cadrage-ux",
      "agent": "skill bmad-ux",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "llm",
        "critere": "CONDITIONNEL : seulement si le produit a une surface d'interaction utilisateur (écran, CLI interactive, API consommée par un humain). DESIGN.md/EXPERIENCE.md produits, patterns d'interaction nommés. Régime proposé : annoncer et attendre le feu vert."
      },
      "checkpoint": "annonce + feu vert avant lancement (écrit un fichier réel)"
    },
    {
      "id": "relais-dev-verifie",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "PRD.md et Architecture.md existent et sont relus -- instancier le playbook dev-verifie à partir de son étape cadrage-epics (bmad-create-epics-and-stories), ne PAS répéter cadrage-brief/cadrage-architecture qui sont déjà faits ici."
      },
      "checkpoint": false
    }
  ],
  "regle_reprise": "une relance ciblée par étape en échec de contrat, puis escalade utilisateur avec l'état réel"
}
```

<!-- SOCLE-PROVENANCE: socle : 57e4740 du 2026-10-01 -->
> **Socle généré** — tout ce qui PRÉCÈDE ce bandeau vient du hub de supervision (`57e4740`, 2026-10-01) et sera **réécrit** à la prochaine propagation.
> Le chapitre « Portée sur ce projet » placé après ce bandeau, lui, n'est jamais réécrit : c'est le travail local.

