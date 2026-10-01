# Playbook `revue-design-parallele` — N angles de revue en fan-out

Pattern générique de revue par fan-out : plusieurs agents de revue lancés en parallèle sur
des angles distincts (ex. parcours utilisateur, cohérence visuelle, contenu, accessibilité),
consolidés ensuite en une liste de correctifs concrets.

Importé depuis le projet VSCode2, où ce pattern était éprouvé sur des revues UX/design.
Ici, **statut `jamais-joue`** (FORMAT.md n'a que deux valeurs : `eprouve` | `jamais-joue` —
`importe` n'en fait pas partie, corrigé le 2026-09-11 après avoir trouvé la correction
locale déjà faite chez VSCode3 sans avoir été remontée) — à confirmer sur les premiers
runs de ce projet.

Règles du mode parallèle (cf. `agent-orchestrator`) : angles réellement indépendants,
lecture seule pendant le fan-out, ≤ 4 sous-agents, consolidation obligatoire — chaque
sous-agent repart d'un contexte froid, exiger des rapports courts et structurés.

**Garde exhaustivité** : un fan-out d'`Explore` lit des *extraits*, pas des fichiers
entiers — il ne garantit jamais l'exhaustivité. Quand le fan-out sert à recenser toutes
les références à des identifiants **avant une suppression/renommage**, la consolidation
DOIT se terminer par une garde déterministe : un `grep -r` (ou l'outil Grep) de chaque
identifiant retiré sur tout le dépôt, dont le résultat **prime** sur les rapports des
sous-agents.

```json
{
  "nom": "revue-design-parallele",
  "description": "Revue UX/design (ou revue multi-angles d'un livrable) par fan-out de sous-agents en lecture seule, puis consolidation en backlog d'actions priorisées.",
  "statut": "eprouve",
  "source": "manuel",
  "declencheurs": [
    "revue UX/UI indépendante d'un ensemble d'écrans ou de slides",
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
        "critere": "constats dédoublonnés et priorisés en un backlog d'actions concrètes, contradictions entre angles arbitrées explicitement. SI le but du fan-out était une énumération exhaustive avant suppression/renommage : garde déterministe finale OBLIGATOIRE — grep -r (ou l'outil Grep) de chaque identifiant retiré sur tout le dépôt, dont le résultat PRIME sur les rapports des sous-agents (qui ne lisent que des extraits)."
      },
      "checkpoint": "restituer le backlog à l'utilisateur avant d'appliquer le moindre correctif — la revue est le livrable, les fixes sont un mandat séparé"
    }
  ],
  "regle_reprise": "une relance ciblée par étape en échec de contrat (sous-agent muet ou hors format : une seule relance du sous-agent concerné), puis escalade utilisateur avec l'état réel"
}
```

<!-- SOCLE-PROVENANCE: socle : 19a8f19 du 2026-10-01 -->
> **Socle généré** — tout ce qui PRÉCÈDE ce bandeau vient du hub de supervision (`19a8f19`, 2026-10-01) et sera **réécrit** à la prochaine propagation.
> Le chapitre « Portée sur ce projet » placé après ce bandeau, lui, n'est jamais réécrit : c'est le travail local.

## Portée sur ce projet

**Ce playbook est né ici.** Le socle du hub porte `"statut": "jamais-joue"` et la
mention « importé depuis le projet VSCode2 » : vu depuis VSCode2, c'est l'inverse. Le
pattern y a été réellement joué, précédent US9.12 (2026-07-16) — 4 agents de revue
design lancés en parallèle sur des angles distincts (parcours utilisateur, cohérence
visuelle, feedback des actions, contenu des écrans), consolidés en une liste de
correctifs concrets qui a nourri les fixes des 16-17/07.

Donc, dans le bloc JSON du socle, le champ `"statut"` vaut **`eprouve`** sur ce dépôt,
pas `jamais-joue`. Le mécanisme socle/local ne protège QUE la prose en fin de fichier,
jamais un champ à l'intérieur du socle : **statut local plus avancé que le socle, à
re-patcher à la main à chaque propagation tant qu'aucun mécanisme dédié n'existe.**

**Garde exhaustivité (ajout 2026-07-20)** — précédent réel vécu ici, que le socle
résume sans le dater : lors du tri BMAD du 2026-07-18, le fan-out de 3 `Explore` a raté
une référence `bmad-spec` (`bmad-architecture:29`) juste avant un `git rm` ; seul un
`grep -r` final non prévu l'a rattrapée. C'est ce précédent qui fonde la garde
déterministe de l'étape de consolidation, et il prime sur les rapports des sous-agents.
