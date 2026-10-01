---
name: agent-securite
description: "Audit de sécurité ciblé d'un projet de la flotte — secrets exposés, dépendances vulnérables/supply chain, drift de `.claude/**` face à la checklist officielle Claude Code, historique git suspect, workflows CI/CD à risque agentic, classifié selon OWASP Top 10 Agentic Applications (ASI01-10). Écrit dans la dimension `securite` de `.claude/audits/<projet>.json` (même schéma qu'`audit-technique`, qu'il complète sans le remplacer). AUDITEUR PONCTUEL À LA DEMANDE — aucune capacité de détection réseau/host en continu (pas un EDR/SIEM). Ne corrige jamais rien : il propose, l'humain arbitre (R4)."
---

# agent-securite — audit de sécurité ciblé, à la demande

Née de la veille du 2026-09-12 (`.claude/veille/veille.json`, 4 trouvailles : doc
officielle Claude Code Security, OWASP Top 10 Agentic Applications 2026,
`anthropics/claude-code-security-review`, `trailofbits/skills`). `audit-technique` a déjà
une dimension `securite` (secrets en clair, injection, désérialisation, permissions,
dépendance vulnérable — jugement qualitatif à la lecture du code). Cette skill va plus
loin sur des vérifications **spécifiquement sécurité**, outillées et reproductibles, que
`audit-technique` ne fait pas : elle **complète** sa dimension `securite`, ne la
duplique pas — toujours lire l'audit existant du projet avant d'écrire (§ Méthode).

## Ce que ça couvre réellement (et ce que ça ne couvre PAS)

**Périmètre réaliste** — un sous-agent Claude Code invoqué ponctuellement lit du
code/config/historique, il ne surveille rien en continu :

| Couvre | Ne couvre PAS |
| --- | --- |
| Secrets en clair dans le code, `.env` non gitigné, historique git | Détection d'intrusion réseau/host en cours |
| Dépendances vulnérables connues (`npm audit`/équivalent du canal) | Monitoring de process/CPU/mémoire en continu |
| Drift `.claude/**` vs checklist officielle (permissions, sandbox, deny rules) | Alerting temps réel |
| Historique git suspect (réécritures, commits non signés, patterns anormaux) | Analyse de logs serveur en production |
| Workflows CI/CD (`.github/workflows/*.yml`) à risque agentic | Tout ce qui relève d'un EDR/SIEM dédié |

Si l'utilisateur attend une détection active de menaces en cours sur un serveur/une
machine, le dire explicitement en fin de restitution : ce n'est pas ce que cet
instrument peut fournir (aucune fonctionnalité de ce type n'est documentée dans
`code.claude.com/docs/en/security`, qui ne couvre que le comportement de l'outil
Claude Code lui-même).

## Méthode — 5 étapes

1. **Cadrer et lire l'existant** : `.claude/audits/<projet>.json` (dimension `securite`
   déjà présente ?), `.claude/veille/veille.json` (trouvailles `adopte` sur le sujet),
   `.claude/supervision/arbitrages.json` (drift déjà arbitré, ne pas re-proposer).
   **Cible au repos** avant de dispatcher — même garde-fou qu'`audit-technique` § Méthode
   étape 1 (deux `git status --porcelain` espacés, processus/ports actifs sur le dépôt) :
   un audit qui lit un historique git ou lance `npm audit` ne doit jamais toucher un
   service en cours ni un dépôt qu'une autre session modifie.
2. **Secrets et historique** : grep ciblé (clés API, tokens, mots de passe en clair),
   vérifier que `.env`/`secrets/**`/`credentials.json` sont gitignorés, `git log`/
   `git log -p` ciblé sur les fichiers sensibles pour des ajouts puis retraits de secrets
   (un secret committé puis supprimé reste dans l'historique). Jamais d'exécution de code
   trouvé, jamais de `git clone` d'un dépôt tiers pour l'inspecter en dehors de GitHub
   (API/web) — même règle que `veille-agentic`.
3. **Dépendances / supply chain** : `npm audit`/`pip-audit`/équivalent selon le canal du
   projet (R3 — ne pas plaquer un outil Node sur un projet Python), dépendances non
   épinglées, upstream abandonné (dernier commit > 1 an sur un paquet critique).
4. **Config et CI/CD** :
   - Comparer `<projet>/.claude/settings.json` à la checklist de
     `code.claude.com/docs/en/security` (mode de permission par défaut, deny rules,
     restrictions bash, `additionalDirectories`) — un `defaultMode` permissif ou une
     absence de deny rules sur les fichiers sensibles est un finding.
   - Auditer `.github/workflows/*.yml` s'il existe : permissions du `GITHUB_TOKEN` trop
     larges, secrets exposés en clair dans les logs, action tierce non épinglée par SHA,
     déclencheur `pull_request_target` sur du code non fiable.
5. **Classifier et écrire** : chaque finding rattaché à une catégorie OWASP ASI01-10
   pertinente (ex. ASIxx exécution non maîtrisée d'outils, ASIxx fuite de données via
   permissions trop larges — vérifier la grille exacte sur
   `genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/` au moment
   de l'audit, elle peut évoluer). Écrire dans `.claude/audits/<projet>.json`, **dimension
   `securite` uniquement**, même schéma qu'`audit-technique` (§ 4 de sa méthode) — fusionner
   avec les findings déjà présents dans cette dimension (ne jamais écraser un finding
   d'audit-technique qui n'a rien à voir), niveau `ok`/`moyen`/`critique`/`non_evalue`,
   5 points max, chaque finding avec `fichier:ligne` et sa catégorie ASI entre
   parenthèses dans le titre. Puis relancer le scan (`py scripts/scan_projets.py` au hub)
   pour propager en section 2 du wiki.

## Interdits, contrat de sortie, condition d'arrêt, écriture

Source unique : le mandat `.claude/agents/agent-securite.md` (ADR 0009 — le mandat porte
le contrat, les interdits et la condition d'arrêt ; cette skill porte la méthode). Côté
méthode, une seule règle reste ici : lire l'existant AVANT d'écrire (étape 1) et ne
dupliquer un finding déjà présent dans la dimension `securite` d'`audit-technique` que
pour apporter un élément nouveau (URL source, catégorie ASI, vérification outillée).
