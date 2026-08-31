---
name: audit-technique
description: Audit qualitatif du code d'un projet de la flotte sur 4 dimensions qui exigent de LIRE le code — robustesse, performance, risque technique, failles de sécurité — et écrit le verdict dans .claude/audits/<projet>.json (rendu dans la section « Pratiques, couverture & risques » du wiki de supervision, étage qualitatif). À lancer sur demande (« audit technique de VSCodeN », « niveau de sécurité/robustesse/perf/risque de X ») — pas à chaque scan : c'est un audit facturé qui lit du code réel, contrairement à l'étage déterministe du scanner (0 token) qui ne mesure que la présence de dispositifs.
---

# audit-technique — l'étage qualitatif de la supervision

Le scanner (`scripts/scan_projets.py`) mesure ce qui est DÉTECTABLE sans lire le code
(tests présents, CI, linter, garde-fous sécurité). Cet audit qualifie ce qui exige de
LIRE le code : robustesse, performance, risque technique, failles de sécurité. Un score
de sécurité déduit de la seule présence de fichiers serait un faux signal — d'où cet
étage séparé, facturé, à la demande.

## Règles

- **Un audit lit le code réel** du projet cible (Read/Grep/Glob), il ne devine pas depuis
  le wiki. Pour un gros projet, déléguer l'exploration à des sous-agents `Explore` par
  zone (≤ 4), jamais tout charger en session principale.
- **Pas de constat sans localisation** : chaque point porte `fichier:ligne` ou une
  fonction nommée. Un ressenti n'est pas un finding.
- **Réutiliser l'existant** avant de re-analyser à la main : `/security-review` (sécurité
  des changements), `bmad-review-adversarial-general`, `bmad-review-edge-case-hunter`
  (robustesse / cas limites). Cet audit les **oriente et consolide**, il ne les remplace
  pas.
- **Niveaux** : `ok` (rien de significatif), `moyen` (points à traiter, non bloquants),
  `critique` (à traiter en priorité — faille exploitable, perte de données possible,
  goulot avéré). Le niveau le plus haut d'une dimension = son niveau.
- **5 points max par dimension**, priorisés — un audit illisible ne sert personne.

## Les 4 dimensions

| Dimension | Ce qu'on lit | Exemples de findings |
| --- | --- | --- |
| **robustesse** | Gestion d'erreur, cas limites, entrées non validées, échecs silencieux, idempotence | `except: pass` qui masque, chemin sans validation, division/index non gardés, absence de rollback |
| **performance** | Boucles imbriquées sur gros volumes, I/O dans une boucle, requêtes N+1, absence de cache/pagination, rendu synchrone bloquant | lecture fichier par ligne dans une double boucle, appel réseau/DB par item |
| **risque_technique** | Dette structurelle : duplication logique, couplage fort, dépendance non épinglée, code mort, TODO critiques, magic numbers, absence de test sur un chemin critique | même logique copiée 4×, version de dépendance flottante, fonction de 300 lignes |
| **securite** | Secrets en clair/commités, injection (SQL/commande/template), désérialisation non sûre, chemins utilisateur non assainis, permissions trop larges, dépendance vulnérable connue | clé API dans le code, `subprocess(shell=True)` sur entrée, `eval`/`pickle` sur données externes, `.env` non gitigné |

## Méthode — 4 étapes

1. **Cadrer** : identifier le projet cible et ses zones de code réel (hors `_bmad/`,
   `.venv/`, `node_modules/`, `.claude/`). Lire son CLAUDE.md pour le contexte.
2. **Explorer** (fan-out `Explore` si volumineux) : une passe par dimension, ou par zone
   de code selon ce qui est le plus efficace. Lancer `/security-review` sur le diff
   courant si pertinent.
3. **Qualifier** : pour chaque dimension, niveau + 1 à 5 findings localisés + une synthèse
   d'une phrase. Consolider les sorties des sous-agents/skills réutilisés.
4. **Écrire** dans `.claude/audits/<projet>.json` :

```json
{
  "projet": "VSCode2",
  "date": "2026-07-23",
  "dimensions": {
    "robustesse":      {"niveau": "moyen",    "synthese": "…", "findings": [{"titre": "…", "localisation": "app/x.py:42"}]},
    "performance":     {"niveau": "ok",        "synthese": "…", "findings": []},
    "risque_technique":{"niveau": "moyen",    "synthese": "…", "findings": [...]},
    "securite":        {"niveau": "critique", "synthese": "…", "findings": [...]}
  }
}
```

Puis relancer `py scripts/scan_projets.py` — la section « Pratiques, couverture & risques »
du wiki fusionne l'étage déterministe et cet étage qualitatif. Enfin, restituer à
l'utilisateur les niveaux par dimension en une ligne chacun avec le finding le plus grave.

## Cadence

À la demande. Un audit est daté : le wiki signale `🟠 périmé` au-delà de 30 jours ou si le
projet a beaucoup bougé depuis (dernier commit postérieur à la date d'audit). Les findings
`critique` remontent dans le bandeau exécutif du wiki comme décisions en attente.
