# Playbook `revue-design-parallele` — N angles de revue en fan-out

Capitalisation du précédent réel US9.12 (2026-07-16) : 4 agents de revue design lancés en
parallèle sur des angles distincts (parcours utilisateur, cohérence visuelle, feedback des
actions, contenu des écrans), consolidés ensuite en une liste de correctifs concrets qui a
nourri les fixes des 16-17/07. Statut `eprouve`.

Règles du mode parallèle (conception §5) : angles réellement indépendants, lecture seule
pendant le fan-out, ≤ 4 sous-agents, consolidation obligatoire — chaque sous-agent repart
d'un contexte froid facturé, exiger des rapports courts et structurés.

```json
{
  "nom": "revue-design-parallele",
  "description": "Revue UX/design (ou revue multi-angles d'un livrable) par fan-out de sous-agents en lecture seule, puis consolidation en backlog d'actions priorisées.",
  "statut": "eprouve",
  "source": "manuel",
  "declencheurs": [
    "revue UX/UI indépendante d'un ensemble d'écrans",
    "passer en revue X sous plusieurs angles",
    "audit d'un livrable selon des dimensions distinctes (design, contenu, cohérence, parcours)"
  ],
  "etapes": [
    {
      "id": "definition-angles",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "2 à 4 angles réellement indépendants définis, avec pour chacun le périmètre à lire et le format de rapport attendu (constats courts + gravité)"
      },
      "checkpoint": false
    },
    {
      "id": "fan-out-revue",
      "agent": "Explore",
      "mode": "parallele",
      "modele": "sonnet",
      "fan_out_max": 4,
      "contrat": {
        "type": "deterministe",
        "critere": "un rapport court par angle reçu (jamais anticipé/fabriqué), lecture seule respectée — aucune écriture par les sous-agents"
      },
      "checkpoint": false
    },
    {
      "id": "consolidation",
      "agent": "session principale",
      "mode": "cascade",
      "modele": "(session)",
      "contrat": {
        "type": "deterministe",
        "critere": "constats dédoublonnés et priorisés en un backlog d'actions concrètes, contradictions entre angles arbitrées explicitement"
      },
      "checkpoint": "restituer le backlog à l'utilisateur avant d'appliquer le moindre correctif — la revue est le livrable, les fixes sont un mandat séparé"
    }
  ],
  "regle_reprise": "une relance ciblée par étape en échec de contrat (sous-agent muet ou hors format : une seule relance du sous-agent concerné), puis escalade utilisateur avec l'état réel"
}
```
