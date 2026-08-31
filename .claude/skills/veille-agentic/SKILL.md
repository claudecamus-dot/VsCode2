---
name: veille-agentic
description: Agent de veille agentic à deux volets — (1) explore la partie publique de GitHub (et sources associées) pour repérer agents, sous-agents, skills, rules, playbooks ou frameworks pertinents pour les projets supervisés ; (2) surveille les référentiels documentaires des providers IA (Anthropic/Claude Code, OpenAI, Mistral, GitHub…) pour repérer les PRATIQUES agentic recommandées, en dériver des règles d'analyse (référentiel criteres-pratiques.md) et des actions correctives arbitrables sur la flotte. Trouvailles dans .claude/veille/veille.json (rendues dans la section 3 du wiki). Cadence : tous les 3 jours (hook SessionStart) ou manuel. À charger quand l'utilisateur demande une veille, quand le hook la signale périmée, ou avant de créer un agent/skill maison.
---

# veille-agentic — veille écosystème + pratiques providers

Objectif : ne pas réinventer ce qui existe déjà publiquement, repérer tôt les
évolutions utiles aux projets supervisés (listés dans `projets.json`), et maintenir
les règles d'analyse de la flotte alignées sur les pratiques recommandées par les
providers IA.

## Méthode — 4 étapes

### 1. Contexte : qu'est-ce qui est pertinent ?

Lire `projets.json` et `docs/wiki/projets-supervision.md` (généré) pour connaître les
projets, leurs besoins réels et leurs manques du moment. Les thèmes durables des projets :

- **Claude Code** : skills, subagents (`.claude/agents`), hooks, orchestration
  multi-agents, supervision d'usage.
- **BMAD-METHOD** : nouvelles versions, nouveaux modules (tea, bmb, cis…), pratiques
  de tri/customisation.
- **Génération PPT programmatique** : python-pptx, rendu/vérification visuelle,
  design de decks.
- **Pratiques d'équipe agentic** : rules/playbooks versionnés, mémoire projet, garde-fous
  (hooks destructifs, revues adversariales).

### 2. Explorer (WebSearch / WebFetch — public uniquement)

3 à 6 recherches ciblées par session de veille, PAS une rafale exhaustive. Exemples de
requêtes efficaces :

- `github claude code skills collection <thème>` / `awesome claude code`
- `github claude code subagents <besoin du moment>`
- `BMAD-METHOD release notes` / repo `bmad-code-org/BMAD-METHOD` (releases récentes)
- `github python-pptx <problème rencontré récemment>`
- Suivre aussi les trouvailles précédentes en statut `nouveau`/`etudie` (leur repo a-t-il bougé ?)

Règles : sources **publiques** uniquement, jamais d'exécution de code téléchargé pendant
la veille, jamais d'installation — la veille observe et qualifie, l'adoption est un
chantier séparé arbitré par l'utilisateur.

### 3. Qualifier chaque trouvaille

Ne retenir que ce qui a un lien concret avec au moins un projet supervisé. Pour chaque
entrée retenue : titre, url, type (`agent` | `sous-agent` | `skill` | `rules` |
`playbook` | `framework` | `outil`), projets concernés, pertinence en une phrase
(le POURQUOI, pas un résumé du repo), statut initial `nouveau`.

5 entrées max par session de veille — une veille qui noie ne sert personne.

### 4. Enregistrer et propager

Mettre à jour `.claude/veille/veille.json` (créer le dossier au besoin) :

```json
{
  "derniere_veille": "2026-07-23T18:00:00",
  "entrees": [
    {
      "titre": "nom court",
      "url": "https://github.com/...",
      "type": "skill",
      "projets_concernes": ["VSCode2", "VScode5"],
      "pertinence": "pourquoi c'est pertinent en une phrase",
      "date": "2026-07-23",
      "statut": "nouveau"
    }
  ]
}
```

Règles d'entretien du fichier :
- **Cumulatif** : ne jamais écraser les entrées existantes — ajouter les nouvelles,
  mettre à jour `derniere_veille`.
- **Cycle de vie des statuts** : `nouveau` → `etudie` (regardé de près) → `adopte`
  (intégré à un projet — noter où) ou `ecarte` (avec la raison dans `pertinence`).
  Les transitions de statut sont des décisions utilisateur, pas automatiques.
- **Doublons** : si une trouvaille existe déjà (même url), mettre à jour sa pertinence
  plutôt que dupliquer.

Puis régénérer le wiki : `py scripts/scan_projets.py` — la section 3 « Veille agentic »
reflète le fichier. Terminer en restituant à l'utilisateur les nouvelles entrées en une
ligne chacune.

## Volet 2 — pratiques agentic des providers (docs officielles)

Ce volet ne cherche pas des *outils à adopter* mais des **pratiques normatives** : ce
que les providers recommandent dans leur documentation, à comparer avec ce que fait la
flotte. Ses trouvailles **alimentent les règles d'analyse** (référentiel
`docs/wiki/technical/criteres-pratiques.md` § 7 → critères du scan / répertoire craft)
et **des actions correctives** arbitrables.

### Sources à surveiller (publiques)

- **Anthropic / Claude Code** : docs Claude Code (skills, subagents, hooks, memory,
  settings/permissions), guides « building effective agents », release notes.
- **OpenAI / ChatGPT** : platform docs (agents guide, function calling, Assistants),
  cookbook patterns agentic.
- **Mistral** : docs agents/function calling, guides.
- **GitHub** : blog engineering (Copilot agents, workflows), docs Actions pour l'aspect
  automatisation.
- Autres providers si pertinent (Google/Gemini, AWS/Bedrock agents…).
- **Index communautaires curatés** — point d'entrée à re-parcourir à CHAQUE cycle plutôt
  que redécouvrir les mêmes dépôts. Référence adoptée le 2026-07-31 :
  [hesreallyhim/awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)
  — vérifié vivant ce jour-là (51 394 ★, poussé le jour même), et réellement curaté :
  `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` et un index **généré programmatiquement**
  depuis des entrées structurées, pas une liste de liens à la main. 17 catégories, dont
  « Agent Orchestration », « Skills » et « Observability & Monitoring » qui recoupent
  directement le dispositif du hub. **Ce qu'un index ne dispense pas de faire** : il
  signale ce qui existe, il ne dit ni si c'est vivant ni si ça vaut pour cette flotte —
  chaque entrée retenue se vérifie à la source (dernier commit, licence) avant d'être
  proposée à l'arbitrage.
- **Gestion optimisée des tokens** (thème transverse, à surveiller à chaque cycle) :
  outils et actions qui réduisent la consommation — prompt caching, gestion du contexte
  (/compact, /clear, statusline de suivi), sous-agents d'exploration, proxys CLI
  token-optimisés (rtk a été essayé puis retiré de la flotte le 2026-07-29),
  batch/headless. Sources : docs Anthropic
  (« reduce token usage », prompt caching), pages coûts des providers, outils GitHub.
  La flotte a déjà une discipline écrite (sections « optimisation tokens » des
  CLAUDE.md VSCode1/VSCode3, playbook OCTO) — la veille cherche ce qui MANQUE.

### Qualifier une pratique (type `pratique`)

Une entrée `pratique` complète les champs communs (titre, url, projets_concernes,
pertinence, statut) avec :

- `source_referentiel` : le provider et le document (ex. « Anthropic — Claude Code
  docs / memory »).
- `regle_proposee` : la règle d'analyse à intégrer au référentiel si adoptée — quelque
  chose de **mesurable** par le scan ou l'audit (ex. « présence d'un CLAUDE.md par
  projet », « hooks de garde-fou destructifs câblés », « permissions deny explicites »).
- `action_corrective` : le correctif concret à appliquer aux projets en écart (formulé
  comme une proposition arbitrable — jamais auto-appliquée).

3 à 5 pratiques max par session, comme le volet 1. Comparer chaque pratique à l'état
réel mesuré (wiki « Pratiques, couverture & risques ») avant de la retenir : une
pratique déjà généralisée sur la flotte ne mérite pas d'entrée.

### Débouchés (boucle propose → arbitre → applique)

1. **Règles d'analyse** : quand l'utilisateur passe une pratique en `adopte`, sa
   `regle_proposee` est intégrée au référentiel (`criteres-pratiques.md` § 7) et,
   si mesurable à froid, au scan (`scripts/scan_projets.py`) ou au répertoire craft.
2. **Actions correctives** : l'`action_corrective` des pratiques adoptées se traite
   comme un finding arbitré — via le playbook `evolution-flotte` pour les projets
   cibles, arbitrage tracé dans `arbitrages.json`.
3. Les transitions de statut restent des **décisions utilisateur** — la veille
   propose, n'applique jamais.

## Cadence

- Le hook SessionStart (`.claude/hooks/remind_veille_agentic.py`) signale « veille
  agentic à lancer » si la dernière veille date de plus de **3 jours**.
- Déclenchement manuel : `/veille-agentic` à tout moment.
- La veille ne bloque jamais une autre tâche en cours : si le rappel tombe au milieu
  d'un chantier, proposer de la faire en fin de session.
