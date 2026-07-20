---
updated: 2026-07-20
confidence: confirmed
agents: [onboarder, claude]
---

# Architecture — Interview-to-Deck

## Structure globale

Monolithe Python backend avec rendu serveur. Pas de séparation API/SPA — FastAPI produit directement du HTML via Jinja2.
— `CONFIRMÉ` · onboarder · 2026-06-30

## Découpage en couches

Router FastAPI (validation HTTP) → Service (logique métier) → Template Jinja2 (rendu). Les sessions SQLAlchemy sont injectées par `Depends(get_session)`.
— `CONFIRMÉ` · onboarder · 2026-06-30 · `app/routers/`, `app/services/`

## Modèle de données

Hiérarchie `Mission` → `Trame` → `Theme` → `Question`. Entités satellites : `Interview` → `Answer` + `Verbatim`, `Synthesis` (par thème), `AgentResult`.
— `CONFIRMÉ` · onboarder · 2026-06-30 · `app/models.py`

## Communication entre modules

Appels synchrones en Python — pas de queue, pas d'événements. Vérifié dans le code : chaque route qui appelle l'IA (`synthese.py::generate_global`, `generate_recommendations_view`, `interviews.py` régénération/répartition) est une fonction `def` classique (pas `async def`) qui appelle `ai_common.call_ai_json()` → `_call_ollama()`/`_call_openai()`/`_call_mistral()`, elles-mêmes des `def` bloquantes utilisant `urllib.request.urlopen()` (aucun `asyncio`, aucun `await`). FastAPI exécute ces routes `def` dans son threadpool interne — ça ne gèle donc pas la boucle d'événements pour les autres requêtes, mais la requête HTTP de l'utilisateur, elle, reste ouverte jusqu'à la fin de l'appel IA (des dizaines de secondes à plusieurs minutes selon le volume, voir `docs/reflexions/extraction-longue-duree.md`). Aucune trace de file de messages ou de bus d'événements dans le projet (`grep BackgroundTasks|celery|asyncio.create_task|threading.Thread|websocket|redis` sur `app/` : aucun résultat) — confirmant qu'il n'existe aujourd'hui aucune infrastructure de traitement en arrière-plan, malgré la recommandation de `docs/reflexions/extraction-longue-duree.md` §3.B (traitement asynchrone via `BackgroundTasks` de FastAPI, jamais implémenté à ce jour) et `docs/reflexions/enregistrement-segmente-30min.md` §4 (Palier 2, même statut).
— `CONFIRMÉ` · claude · 2026-07-20 · `app/services/ai_common.py:242-350`, `app/routers/synthese.py:206,295`, `app/routers/interviews.py:627,911`

## Décisions architecturales notables

- **Import non destructif** : fusion par titre de thème, questions ajoutées sans écrasement (préserve les réponses existantes)
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/routers/trames.py:97`
- **Synthèse IA avec fallback** : fournisseur actif (Ollama par défaut, ou OpenAI/Mistral) avec sortie JSON structurée, mode démo heuristique si clé absente
  — `CONFIRMÉ` · claude · 2026-07-17 · `app/services/synthese_ai.py`
- **Entretien libre vs synthèse de mission, décorrélés (2026-07-17)** : l'écran de lecture d'un entretien libre (`libre_analyse.html`, route `/interviews/{id}/analyse`, libellée « Aperçu » depuis le 2026-07-19) affiche l'apport d'**un seul** entretien aux 5 catégories transverses, via un bandeau explicite ; la synthèse globale de mission (`synthese/globale.html`) agrège tous les entretiens (structurés + libres) séparément. L'ancien écran dédié `libre_synthese.html` a été fusionné dans `libre_analyse.html` le 2026-07-17 (un seul écran de lecture par entretien libre, comme côté structuré) puis supprimé.
  — `CONFIRMÉ` · claude · 2026-07-19 · `app/templates/interviews/libre_analyse.html`, `app/templates/synthese/globale.html`
- **Régénération contrôlée de l'analyse IA (2026-07-18)** : toute action qui écrase une génération IA existante (réanalyse d'un entretien libre, régénération de la synthèse globale/recommandations) passe par une étape de confirmation avant d'écraser — écran de revue dédié pour l'entretien (`libre_regen_review.html`, ancienne valeur repliable en regard), `hx-confirm`/`confirm()` JS pour la synthèse globale/recommandations (correctif 2026-07-19 après constat qu'aucun garde-fou n'existait). Aucune de ces régénérations ne touche les données sources (tours de parole, réponses brutes) — seul le contenu dérivé (résumé/répartition/axes) est remplacé, et seulement après validation.
  — `CONFIRMÉ` · claude · 2026-07-19 · `app/routers/interviews.py`, `app/routers/synthese.py`
- **Migrations additives à chaud** : les nouvelles colonnes sont ajoutées via `ALTER TABLE` au démarrage (pas de migration versionnée)
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/db.py:33`
- **HTMX pour l'interactivité** : autosave par champ, navigation entre thèmes, pas de JS framework
  — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/templates/`

## Points de fragilité connus

- Absence d'authentification — `CONFIRMÉ` · onboarder · 2026-06-30 · `app/main.py`
- Pas de CI/CD — `CONFIRMÉ` · onboarder · 2026-06-30
- Vitesse d'inférence Ollama sur poste CPU sans GPU dédié — un entretien de ~37min a atteint `OLLAMA_TIMEOUT` avant que le map-reduce ne soit ajouté (voir décision ci-dessous) ; la marge reste faible sur les très gros entretiens, `qwen2.5:1.5b-instruct` (~4x plus rapide, qualité d'extraction non validée) est une option de secours non tranchée
  — `CONFIRMÉ` · claude · 2026-07-19 · `app/services/ai_common.py`, `docs/wiki.html` (table TODO)

~~`generate_global_synthesis()` n'a pas de découpage map-reduce~~ — **résolu le 2026-07-18** : applique désormais le même map-reduce que l'extraction d'entretien libre (découpe aux frontières de thème/entretien, synthèses partielles fusionnées par un appel de réduction dédié) ; une mission courte ne fait toujours qu'un seul appel.
  — `CONFIRMÉ` · claude · 2026-07-19 · `app/services/synthese_ai.py:_chunk_blocks,_reduce_partial_globals`

## Supervision et orchestration des agents (2026-07-17/18, incréments O-A à O-C)

Système à deux étages, entièrement local et déterministe à l'étage 1 (0 token LLM) :

- **Étage 1 — mesure** (`.claude/supervision/scan_transcripts.py`, hook `SessionStart`) : scan incrémental des transcripts de session, agrège l'usage réel des skills/sous-agents dans `state.json`, régénère le tableau de bord (`docs/wiki/technical/agents-supervision.md`, section auto de ce wiki) et `.claude/orchestration/routing-hints.json` (éprouvés/jamais-utilisés/en-sommeil, stats plan-vs-réel par playbook/agent).
- **Étage 2 — diagnostic** (skill `agent-supervisor`, sur demande ou signal de péremption) : qualifie les données étage 1 (KO répétés, inefficacité, agents morts, vérifications manquantes) avec preuve obligatoire, propose des changements concrets (jamais auto-appliqués) — l'humain arbitre via `.claude/supervision/arbitrages.json` (versionné, clôt un constat sans effacer la mesure réelle).
- **Orchestrateur** (skill `agent-orchestrator`, point d'entrée par défaut des demandes multi-étapes — hook `UserPromptSubmit` injecte une grille de qualification à chaque prompt) : compose un plan (cascade/parallèle/asynchrone, modèle par sous-agent) depuis un catalogue (`.claude/orchestration/catalogue.md`) et des playbooks déclaratifs (`.claude/orchestration/playbooks/`, ex. `dev-verifie`, `export-ppt-verifie`), journalise chaque run dans `.claude/orchestration/runs.jsonl`.
- Boucle de gouvernance : scan → diagnostic (propose) → arbitrage humain → orchestrateur (applique la version validée) → nouveau scan qui mesure l'effet réel — jamais d'auto-modification.
  — `CONFIRMÉ` · claude · 2026-07-19 · `.claude/skills/agent-orchestrator/`, `.claude/skills/agent-supervisor/`, `docs/reflexions/agent-orchestrateur.md`, `docs/reflexions/agent-superviseur.md`

## BMAD-METHOD (installé 2026-07-17, trié 2026-07-18)

`_bmad/` (config `_bmad/config.toml`) ajoute 39 skills `bmad-*` (agents-personas : dev/PM/architecte/UX/analyste/tech-writer ; workflows produit : brief→PRD→architecture→epics→dev ; qualité : `bmad-code-review`, `bmad-retrospective`) après un tri qui a retiré 7 skills dépréciés/redondants sur 46 installés initialement (`docs/reflexions/tri-skills-bmad.md`). Routage uniquement sur demande explicite (`bmad-help` comme routeur) — `revue-increment` délègue à `bmad-code-review` au-delà d'un seuil de diff (>5 fichiers produit ou logique à risque, ajouté le 2026-07-19 après constat que la délégation implicite ne se déclenchait jamais).
  — `CONFIRMÉ` · claude · 2026-07-19 · `.claude/skills/bmad-*`, `.claude/skills/revue-increment/SKILL.md`
