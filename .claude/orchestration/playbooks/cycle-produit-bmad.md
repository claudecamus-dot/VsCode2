# Playbook `cycle-produit-bmad` — GÉNÉRÉ, ne pas éditer à la main

Généré par `generate_bmad_playbook.py` depuis `_bmad/_config/bmad-help.csv`
(module « BMad Method » : étapes required + fermeture des preceded-by + code-review).
Pour modifier : éditer le script ou le CSV, puis regénérer :

```bash
py .claude/orchestration/generate_bmad_playbook.py
```

Statut `jamais-joue` assumé (garde-fou « playbooks morts ») : le cycle BMAD complet
n'a jamais été déroulé sur ce projet — ne le proposer que sur demande explicite,
conformément à la règle de routage BMAD du catalogue.

```json
{
  "nom": "cycle-produit-bmad",
  "description": "Cycle produit BMAD complet : brief → PRD → architecture → epics/stories → readiness → sprint → cycle story (create/validate/dev/review), clos par revue-increment.",
  "statut": "jamais-joue",
  "source": "genere:generate_bmad_playbook.py",
  "declencheurs": [
    "dérouler le cycle produit BMAD sur une idée/feature",
    "demande explicite de workflow BMAD multi-étapes (sinon : bmad-help, une étape à la fois)"
  ],
  "etapes": [
    {
      "id": "bmad-product-brief",
      "agent": "bmad-product-brief",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "artefact « product brief » produit dans planning_artifacts"
      },
      "checkpoint": false
    },
    {
      "id": "bmad-prd",
      "agent": "bmad-prd",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "artefact « prd » produit dans planning_artifacts"
      },
      "checkpoint": false
    },
    {
      "id": "bmad-architecture",
      "agent": "bmad-architecture",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "artefact « architecture » produit dans planning_artifacts"
      },
      "checkpoint": false
    },
    {
      "id": "bmad-create-epics-and-stories",
      "agent": "bmad-create-epics-and-stories",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "artefact « epics and stories » produit dans planning_artifacts"
      },
      "checkpoint": false
    },
    {
      "id": "bmad-check-implementation-readiness",
      "agent": "bmad-check-implementation-readiness",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "artefact « readiness report » produit dans planning_artifacts"
      },
      "checkpoint": "gate humain : PRD/UX/architecture/stories alignés avant d'engager l'implémentation"
    },
    {
      "id": "bmad-sprint-planning",
      "agent": "bmad-sprint-planning",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "artefact « sprint status » produit dans implementation_artifacts"
      },
      "checkpoint": false
    },
    {
      "id": "bmad-create-story-create",
      "agent": "bmad-create-story:create",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "artefact « story » produit dans implementation_artifacts"
      },
      "checkpoint": false
    },
    {
      "id": "bmad-create-story-validate",
      "agent": "bmad-create-story:validate",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "artefact « story validation report » produit dans implementation_artifacts"
      },
      "checkpoint": false
    },
    {
      "id": "bmad-dev-story",
      "agent": "bmad-dev-story",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "story implémentée, suite du projet verte",
        "commande": "pytest -q"
      },
      "checkpoint": false
    },
    {
      "id": "bmad-code-review",
      "agent": "bmad-code-review",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "llm",
        "critere": "revue adversariale rendue avec triage des findings (pas de vérification déterministe possible)"
      },
      "checkpoint": "issues → retour bmad-dev-story (une relance) ; approuvé → story suivante ou fin d'epic"
    },
    {
      "id": "revue-increment",
      "agent": "revue-increment",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "reel",
        "critere": "definition-of-done projet : revue + correctifs appliqués + re-vérification réelle (obligation hors CSV, voir catalogue)"
      },
      "checkpoint": "avant tout commit — action difficilement réversible, proposer, ne pas exécuter unilatéralement"
    }
  ],
  "regle_reprise": "une relance ciblée par étape en échec de contrat, puis escalade utilisateur avec l'état réel"
}
```
