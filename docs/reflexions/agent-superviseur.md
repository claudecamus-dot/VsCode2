# Réflexion — Agent superviseur des agents (méta-supervision)

> Statut : **incrément A réalisé le 2026-07-17** (collecteur `.claude/supervision/`,
> hooks PostToolUse + SessionStart, page wiki générée `docs/wiki/technical/agents-supervision.md`,
> tests `tests/test_agent_supervision.py`).
> **Incrément B réalisé le 2026-07-18** : skill projet **`agent-supervisor`** (étage 2 —
> méthode de diagnostic dans la session : 4 lectures ciblées des agrégats étage 1 +
> signaux git/mémoire, jamais les JSONL bruts, 5 constats max ancrés chacun sur une
> `preuve` objective), writer validé `.claude/supervision/write_diagnostic.py`
> (schéma : categorie `ko-repete|inefficacite|agent-mort|interaction|verification-manquante|autre`,
> `titre`/`preuve` requis, priorite 1-5 ; UTF-8 forcé sur stdin/stdout — piège cp1252
> Windows constaté au premier run réel), fusion par le scan dans le wiki (section
> « Diagnostic qualitatif », cadence 14 j sinon « à relancer ») et dans
> `routing-hints.json` (liste `prudence` sur les cibles `ko-repete`/`inefficacite`).
> Intégré à `revue-increment` (§6) ; le hook SessionStart signale un diagnostic absent ou
> périmé. Premier diagnostic réel produit le 2026-07-18 (3 constats : tri BMAD arbitré non
> exécuté, changement PPT sans `pptx-verify` depuis le 2026-07-03, orchestrateur n'ayant
> encore jamais délégué à un sous-agent). Tests : `test_agent_supervision.py` (validation
> du writer, boucle écriture→scan→wiki).
> Incrément C (challenge : diffs de customisation proposés, interactions inter-agents,
> couverture OpenHub) reste à lancer. Réflexion rédigée le 2026-07-17.
> Document jumeau : [agent-orchestrateur.md](agent-orchestrateur.md) (réflexion 2026-07-17) —
> l'orchestrateur consomme les données du superviseur (hints de routage) et alimente en
> retour son journal d'exécution (`runs.jsonl`), la métrique « plan vs réel » de l'étage 2.
> Demande d'origine : un agent superviseur qui analyse en tâche de fond tous les agents
> utilisés / non utilisés, leurs performances, la qualité de leurs actions, leur efficacité,
> les résultats, les reprises d'actions restées KO, les tests nécessaires pour vérifier le
> travail — mise en place automatique par défaut, avec une sous-rubrique wiki « TODO agents »
> remontant les chantiers à lancer, et la capacité de challenger les autres agents
> (façon de travailler, interactions).

## 1. Ce qu'on appelle « agents » ici (périmètre)

Le mot recouvre 4 familles distinctes dans cet environnement — le superviseur doit les couvrir toutes, mais elles ne s'observent pas par les mêmes canaux :

| Famille | Où | Observable via |
| --- | --- | --- |
| Skills projet (`run-dev-server`, `revue-increment`, `pptx-framed-image`, `slide-text-polish`) | `.claude/skills/` versionné | transcripts de session (invocations `Skill`) |
| Skills BMAD (~46, `bmad-*`, installés le 2026-07-16) | `.claude/skills/` | transcripts (idem) + artefacts `_bmad-output/` |
| Sous-agents Claude Code (Explore, Plan, general-purpose…) | harness (pas de fichier local — `.claude/agents/` n'existe pas) | transcripts (invocations `Agent`/`Task`) |
| Agents OpenHub (`.opencode/agents/`, CLI externe `opencode`) | `app/services/openhub_agents.py` | table `AgentResult` en base + logs applicatifs |

Les skills globaux utilisateur (`roadmap-keeper`, `pptx-deck`, `pptx-verify`, `dataviz`…) sont une 5ᵉ population, hors périmètre projet mais visibles dans les mêmes transcripts — autant les mesurer aussi.

## 2. État des lieux mesuré (POC du 2026-07-17, coût : 0 token LLM)

Un simple script Python parcourant les 18 transcripts JSONL du projet (~90 Mo,
`~/.claude/projects/c--Users-claude-camus-Documents-VSCode2/*.jsonl`) suffit à produire
des métriques d'usage réelles. Résultat sur tout l'historique :

**Invocations de skills** : `run-dev-server` ×9 · `update-config` ×6 · `roadmap-keeper` ×5 ·
`run` ×3 · `pptx-deck` ×2 · `skill-creator` ×2 · `pptx-verify` ×1 · `init` ×1 · `claude-api` ×1.

**Sous-agents** : `Explore` ×12 · `general-purpose` ×7 · `claude` ×4 · `Plan` ×3 · `claude-code-guide` ×1.

**Constat majeur** : **0 invocation** pour les 46 skills BMAD (installés la veille, certes)
**et 0 pour `revue-increment`** — alors qu'un hook SessionStart la rappelle à chaque session.
Autrement dit : la première itération du superviseur, avant même d'exister, révèle déjà un
chantier (l'écart entre l'outillage installé et l'outillage utilisé). C'est la preuve de valeur
de l'approche — et la preuve que la couche « mesure » est faisable en pur déterministe.

## 3. Sources de données disponibles (vérifiées)

1. **Transcripts JSONL** — la source la plus riche : chaque invocation Skill/Agent, chaque
   erreur d'outil, chaque message utilisateur (donc les corrections « toujours KO », les
   re-demandes), horodatage, usage tokens. Volumineux (jusqu'à 17 Mo/session) →
   **parsing Python obligatoire, jamais de lecture brute par le LLM**.
2. **`rtk gain --history`** — économies de tokens par commande, déjà outillé.
3. **Historique git** — reverts, séries de fix sur le même fichier, commits « post-revue » :
   signaux de reprises d'actions KO.
4. **`.roadmap/roadmap.json`** — statut des incréments, à croiser avec le réel (dérive connue).
5. **Mémoire persistante** (`memory/`) — les fichiers `feedback_*` sont déjà des leçons
   capitalisées sur la façon de travailler (10 à ce jour) : le superviseur doit les lire
   (redondance à éviter) et en écrire.
6. **`_bmad-output/`** — artefacts produits par les workflows BMAD, quand ils tourneront.
7. **Hooks** — `PostToolUse` peut journaliser chaque invocation Skill/Agent en temps réel
   dans un journal léger, sans dépendre du parsing différé des transcripts.

## 4. Contrainte d'architecture à assumer : « tâche de fond » ≠ démon

Claude Code est orienté session : il n'existe pas de processus superviseur permanent natif.
Les mécanismes réels pour du « automatique par défaut » sont :

- **Hooks** (SessionStart / PostToolUse / Stop) : déclenchement déterministe, gratuit en
  tokens, à chaque session — c'est le seul vrai « par défaut sans y penser ».
- **Skill invocable** (`/agent-supervisor`) : l'analyse profonde par LLM, à la demande.
- **`schedule`** (agents cloud planifiés) : vraie périodicité, mais cloud + facturation ;
  et les transcripts sont sur cette machine → peu adapté ici.
- **`/loop`** : boucle dans une session ouverte, pas une tâche de fond.

**Recommandation : architecture hybride en 2 étages**, cohérente avec la discipline tokens
du CLAUDE.md (le contexte est un cache facturé) et avec le choix « tout local » du projet
(les transcripts contiennent du contenu d'interviews — ils ne doivent pas partir dans un
agent cloud) :

- **Étage 1 — Collecteur déterministe (0 token, automatique)** : script Python
  (`.claude/supervision/collect.py`) branché sur hooks. `PostToolUse` (matcher `Skill|Agent`)
  append une ligne dans `.claude/supervision/usage.jsonl` ; `SessionStart` fait un scan
  incrémental des transcripts récents (erreurs d'outils, reprises) et régénère un
  tableau de bord statique. Tourne toujours, ne consomme rien.
- **Étage 2 — Analyseur LLM (sur déclencheur, pas en continu)** : skill projet
  `agent-supervisor` qui lit le tableau de bord de l'étage 1 (jamais les JSONL bruts),
  échantillonne les sessions signalées, produit le diagnostic qualitatif : agents morts,
  agents inefficaces, patterns de KO répétés, axes d'amélioration, et met à jour le wiki.
  Déclencheurs : intégré à `revue-increment` (fin d'incrément), et/ou proposé par le hook
  SessionStart quand le journal dépasse un seuil (« N sessions non analysées »).

## 5. Métriques proposées

**Usage** : invocations par agent/skill, jamais-utilisés, dernière utilisation, tendance.
**Fiabilité** : taux d'erreurs d'outils pendant l'exécution d'un skill ; reprises détectées
(même fichier réédité en rafale, revert git, message utilisateur correctif dans les
3 tours suivant une action — heuristique lexicale FR : « non », « toujours pas », « KO »,
« encore », « refais ») — c'est la métrique « les fois où je dois revenir sur des actions
toujours KO ».
**Efficacité** : tokens et durée par invocation ; ratio sous-agent vs inline ; croisement
avec `rtk gain`.
**Qualité / résultats** : verdicts `code-review` archivés, pytest vert *plus* vérification
réelle (la mémoire projet documente déjà que pytest vert ≠ correct pour le PPT) ; pour
OpenHub : contenu de `AgentResult` (réponse réelle vs fallback simulé).
**Couverture de vérification** : pour chaque action significative, un test/une vérification
a-t-il suivi ? (ex. modification `pptx_export.py` sans passage `pptx-verify` = signalement).

## 6. Capacité de challenge (le volet « proposer des axes d'amélioration »)

Le superviseur ne se contente pas de compter — il produit des recommandations actionnables :

- **Désinstallation/mise en sommeil** des skills jamais utilisés après N semaines
  (candidat évident dès aujourd'hui : trier les 46 BMAD entre « cycle produit à garder »
  et « jamais pertinents ici »).
- **Customisation** d'un skill sous-performant : via `bmad-customize` (pour les BMAD) ou
  `skill-creator` (pour les skills projet), avec un diff de prompt proposé, jamais appliqué
  sans validation.
- **Interactions entre agents** : détecter les enchaînements qui échouent (ex. sortie de
  `bmad-create-story` inutilisable par `bmad-dev-story`, sous-agent Explore relancé 3× sur
  la même question = brief d'entrée défaillant) et proposer une correction du contrat
  d'interface (format de sortie, brief type).
- **Tests de vérification** : proposer le test manquant récurrent (ex. « 4 modifications de
  templates Jinja sans screenshot `run-dev-server` → ajouter ce réflexe au skill »).
- Chaque recommandation part dans le wiki (ci-dessous) avec priorité et preuve (données à
  l'appui), et les leçons transverses sont capitalisées en mémoire `feedback_*`.

## 7. Restitution : sous-rubrique wiki « TODO agents »

- **`docs/wiki/technical/agents-supervision.md`** : page générée (bandeau « ne pas éditer à
  la main — régénérée par agent-supervisor ») avec le tableau de bord d'usage + le
  diagnostic + la liste TODO priorisée (chantiers à lancer).
- **`docs/wiki/index.md`** : sous-rubrique « TODO agents » (3-5 lignes max, lien vers la
  page) — l'index reste un index.
- La dérive wiki/réel est un problème connu du projet : la page étant *générée* à partir des
  données, elle échappe au problème par construction (à condition de ne jamais l'éditer à la main).

## 8. Phasage proposé

- **Incrément A — Mesurer (fondations, ~½ à 1 jour)** : collecteur Python + hook PostToolUse
  (journal `usage.jsonl`) + scan incrémental des transcripts + génération du tableau de bord
  statique et de la page wiki (partie « usage »). Valeur immédiate : la liste
  utilisés/jamais-utilisés est vraie et à jour en permanence, pour 0 token.
- **Incrément B — Diagnostiquer** : skill `agent-supervisor` (analyse LLM échantillonnée :
  KO répétés, efficacité, qualité), remplissage de la section TODO du wiki, intégration
  comme étape de `revue-increment`.
- **Incrément C — Challenger** : recommandations de customisation avec diffs proposés,
  analyse des interactions inter-agents, seuils de rappel automatique via SessionStart,
  éventuelle couverture OpenHub (`AgentResult`).

## 9. Risques et garde-fous

- **Coût tokens** : le risque n°1 est un superviseur qui coûte plus qu'il ne rapporte.
  Garde-fou : tout ce qui est comptable est déterministe (étage 1) ; le LLM ne voit que des
  synthèses et des extraits échantillonnés, sur déclencheur explicite.
- **Confidentialité** : les transcripts contiennent du contenu d'interviews clients →
  analyse strictement locale, pas d'agent cloud planifié sur ces données.
- **Auto-complaisance** : le superviseur (LLM) évalue des actions produites par le même
  modèle. Garde-fou : ancrer chaque verdict sur des signaux objectifs (erreurs, reprises,
  reverts, corrections utilisateur), pas sur une auto-appréciation.
- **Faux positifs « KO répété »** : une réédition rapide d'un fichier peut être une itération
  normale. Les heuristiques signalent, le diagnostic LLM (étage 2) qualifie, l'humain tranche.
- **Un rapport que personne ne lit** : le TODO wiki doit rester court et priorisé (5 items
  max), sinon il rejoindra les 46 skills BMAD non utilisés.

## 10. Décisions à trancher avant l'incrément A

1. **Cadence de l'étage 2** : à chaque `revue-increment` (recommandé, s'insère dans une
   discipline existante) vs rappel à seuil vs hebdomadaire.
2. **Périmètre OpenHub** dès le début, ou à partir de l'incrément C (recommandé : C — canal
   d'observation différent, valeur moindre tant que la page Agents reste peu utilisée).
3. **Sort des 46 skills BMAD** : le premier « chantier TODO agents » sera probablement
   « trier BMAD ». Décider si le superviseur peut proposer une liste de désinstallation dès
   l'incrément A (simple constat d'usage) ou si ça attend le diagnostic B.
4. **Portée** : superviseur propre à ce projet (`.claude/skills/agent-supervisor`) ou
   réutilisable multi-projets (`~/.claude/skills/`) — les transcripts étant rangés par
   projet, une version globale est faisable mais à paramétrer.
