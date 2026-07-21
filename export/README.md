# Export — Configuration Interview-to-Deck (réutilisable)

Bundle **auto-suffisant** de la configuration de développement du projet *Interview-to-Deck*
(FastAPI + Jinja2 + HTMX, transcription/IA locales), destiné à être **repris dans un autre
projet**. Contient : les agents de pilotage (skills `.md`) + ce README, qui documente la
**reproduction complète de l'environnement** (VSCode / Claude Code, install BMAD, autres
installs, extensions, variables d'environnement).

Ré-exporté le **2026-07-21** depuis `c:\Users\claude.camus\Documents\VSCode2`.

## Contenu de `export/`

```text
export/
  README.md                        ce guide (reproduction complète de la config)
  agent-orchestrator/              orchestrateur des agents/skills (point d'entrée multi-étapes)
    SKILL.md                       définition du skill
    conception.md                  doc de conception (étages O-A/O-B/O-C) — le « pourquoi »
    catalogue.md                   recommandations de routage (versionné)
    playbooks/
      FORMAT.md                    format déclaratif des playbooks
      dev-verifie.md               implémentation → tests → vérif réelle → revue-increment
      revue-design-parallele.md    fan-out Explore ≤4 + garde grep déterministe avant delete/rename
      export-ppt-verifie.md        génération deck → pptx-verify obligatoire
      cycle-produit-bmad.md        cycle produit→dev BMAD (généré, statut jamais-joué)
  agent-supervisor/                superviseur (étage 2 — diagnostic LLM à la demande)
    SKILL.md                       définition du skill
    conception.md                  doc de conception (incréments A/B/C)
    arbitrages.example.json        exemple réel de décisions humaines qui closent un constat
  revue-increment/                 definition-of-done avant commit (boucle revue + amélioration)
    SKILL.md                       définition du skill (règles anti-faux-vert, conformité, seuils)
```

> Les `.md` sont la couche « connaissance/comportement ». Pour un transplant *exécutable*
> (boucle de mesure d'usage → hints de routage → diagnostic), il faut aussi porter les
> scripts/hooks — voir « Dépendances non incluses » en fin de doc.

---

## 1. Prérequis & installs

| Composant | Détail | Pourquoi |
| --- | --- | --- |
| **Python 3.11+** | `python -m venv .venv` puis `pip install -r requirements-dev.txt` | Serveur FastAPI + tests |
| **Node.js** (≥ 18, testé v26) | Sur le PATH | Rendu de la roadmap (`roadmap-keeper`) + `node --check` du JS des écrans d'enregistrement |
| **Ollama** | <https://ollama.com> — puis `ollama pull llama3.1` (défaut). Modèles tirés sur le poste de dev : `llama3.1`, `qwen2.5:3b-instruct`, `qwen2.5:1.5b-instruct` | Fournisseur IA **local par défaut** (`AI_PROVIDER=ollama`) : synthèse, recommandations, extraction trame/entretien — aucune donnée envoyée à l'extérieur |
| **faster-whisper** | Dans `requirements.txt` ; modèle **`medium`** téléchargé au 1er usage (relevé de `small` le 2026-07-15 pour les noms propres) | Transcription audio locale (entretien libre + structuré) |
| **BMAD-METHOD v6.10.0** | Installé dans `_bmad/` (config `_bmad/_config/`) — via l'installeur du projet BMAD-METHOD | ~39 skills `bmad-*` (routeur `bmad-help`, cycle produit, revues adversariales) |
| **rtk** (optionnel) | Proxy CLI token-optimisé (hook de réécriture) | Économie de tokens sur les commandes de dev — non requis |

### Dépendances Python

`requirements.txt` (runtime) : `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart`,
`sqlalchemy>=2.0`, `python-docx` (import de trame `.docx`), `python-dotenv`, `faster-whisper`
(transcription, lazy), `python-pptx` (export PPT), `reportlab` (export PDF) ; `openai` et
`mistralai` sont des **fournisseurs IA alternatifs** (lazy, facultatifs au runtime).

`requirements-dev.txt` : `pytest`, `httpx2` (TestClient Starlette), `pymupdf` (extraction du
texte réel des PDF générés dans les tests).

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows ; .venv/bin/activate sur POSIX
pip install -r requirements-dev.txt
```

## 2. Extensions VSCode

Pas de `.vscode/extensions.json` versionné dans ce projet — la liste minimale utile :

- **Claude Code** (Anthropic) — l'extension qui héberge cet agent.
- **Python** + **Pylance** (Microsoft) — langage serveur, complétion, venv.
- *(optionnel)* un client REST / un visualiseur de `.pptx` pour inspecter les exports.

L'app est **server-rendered** (Jinja2 + HTMX 2.0), **aucun build front** : pas d'extension
Node/bundler requise côté frontend.

## 3. Configuration Claude Code (`.claude/`)

### 3.1 `settings.json` — hooks (versionnés, partagés par l'équipe)

| Hook | Déclencheur | Rôle |
| --- | --- | --- |
| `guard_destructive_git.py` | `PreToolUse` (Bash/PowerShell) | Bloque `git push --force` (sans `--force-with-lease`) et `git reset --hard` |
| `remind_revue_increment.py` | `SessionStart` | Rappelle la discipline `revue-increment` + route vers `bmad-help` |
| `supervision/scan_transcripts.py` | `SessionStart` | Étage 1 superviseur : scan incrémental → régénère le wiki de supervision + `routing-hints.json` |
| `orchestrator_gate.py` | `UserPromptSubmit` | Injecte la grille de qualification de l'orchestrateur (hors commandes slash) |
| `supervision/log_usage.py` | `PostToolUse` (`Skill\|Agent\|Task`) | Journalise l'usage réel des skills/sous-agents (`usage.jsonl`) |

`permissions` : `deny` sur `.env` / `secrets/**` / `config/credentials.json` ; une `allow`-list
spécifique au projet (à **purger** lors d'une reprise — elle contient des chemins de scratchpad
et de fichiers locaux propres à ce poste).

### 3.2 Arborescence `.claude/`

```text
.claude/
  settings.json                 hooks + permissions (versionné) ; settings.local.json gitignoré
  skills/                        skills projet (run-dev-server, revue-increment, agent-*, …) + bmad-*
  hooks/                         guard_destructive_git, remind_revue_increment, orchestrator_gate
  supervision/                   étage 1+2 : scan_transcripts, log_usage, write_diagnostic, arbitrages.json
  orchestration/                 catalogue.md, playbooks/, log_run.py, git_agents_inventory.py, generate_bmad_playbook.py
```

> `settings.local.json`, `CLAUDE.local.md`, et les données machine du superviseur
> (`usage.jsonl`, `state.json`, `diagnostic.json`, `routing-hints.json`, `runs.jsonl`) sont
> **gitignorés** — ne jamais y mettre de secret ni de chemin machine.

### 3.3 Variables d'environnement (`.env`, gitignoré)

Copier `.env.example` en `.env`. Principales clés :

- `AI_PROVIDER=ollama` (défaut) `| openai | mistral` — un seul actif ; clé requise sauf pour ollama.
- `SYNTHESE_DEMO=0` — à `1` : synthèse par règles hors-ligne (sans IA ni coût), pour tester le parcours.
- `SYNTHESE_MODEL` (défaut `llama3.1` pour ollama), `OLLAMA_HOST`, `OLLAMA_TIMEOUT` (300s),
  `OLLAMA_CHUNK_MAX_WORDS` (**400** — levier principal contre les timeouts CPU), `OLLAMA_KEEP_ALIVE` (30m).
- `WHISPER_MODEL=medium`, `WHISPER_BEAM_SIZE=2` (réglages **qualité** — un retour à `small`/greedy
  abîme les noms propres), `SEGMENT_JOB_STALE_AFTER_S` (2700s).

## 4. Les agents de pilotage (le cœur de cet export)

Trois skills, conçus pour se transplanter ensemble :

- **`agent-orchestrator`** — point d'entrée des demandes multi-étapes/multi-agents : qualifie,
  compose un plan (cascade/parallèle/asynchrone, modèle par sous-agent), cherche un **playbook**
  matchant avant de composer à vide, journalise le run. Routé par le hook `UserPromptSubmit`.
- **`agent-supervisor`** — diagnostic qualitatif (étage 2) sur les données déterministes de
  l'étage 1 (usage, runs, signaux git/mémoire) : KO répétés, inefficacité, agents morts,
  vérifications manquantes. **Propose** des changements concrets (champ `proposition`), l'humain
  arbitre (`arbitrages.json`), l'orchestrateur applique.
- **`revue-increment`** — definition-of-done avant commit : revue du produit **et** de la façon de
  travailler, puis application des correctifs et **re-vérification réelle** (pas seulement pytest
  vert). Porte les règles durement acquises : lire le verdict pytest réel (pas un résumé filtré),
  conformité exigence par exigence à la demande, seuil `bmad-code-review` (>5 fichiers produit /
  logique à risque / **toute modif de concurrence du JS d'enregistrement**), relecture du correctif
  issu d'une revue externe avant commit.

**Gouvernance** : le superviseur *propose* → l'humain *arbitre* → l'orchestrateur *applique*.
Voir `conception.md` de chaque agent pour le rationale et les options écartées.

## 5. Install BMAD (contexte)

**BMAD-METHOD v6.10.0** est installé dans `_bmad/` : ~39 skills `bmad-*` (personas Amelia/John/
Winston/Sally/Mary/Paige, workflows produit→dev, revues adversariales `bmad-code-review` /
`bmad-review-*`). Repères :

- En cas de doute sur quel skill BMAD lancer → **`bmad-help`** (routeur).
- Sorties des workflows → `_bmad-output/` (candidat `.gitignore`).
- Les skills BMAD ne sont routés que **sur demande explicite** (tri exécuté le 2026-07-18 :
  7 skills retirés, 39 conservés — cf. `arbitrages.example.json`, cible `famille:BMAD`).

## 6. Dépendances NON incluses (scripts / hooks à porter pour l'exécutable)

Ces `.md` restent lisibles/applicables à la main, mais la **boucle automatique** (mesure d'usage →
hints de routage → diagnostic) nécessite de porter depuis le projet source :

- **Superviseur (étage 1)** : `.claude/supervision/{scan_transcripts,log_usage,write_diagnostic}.py`,
  les hooks `PostToolUse`/`SessionStart` (`settings.json`), et les données machine gitignorées.
- **Orchestrateur** : `.claude/orchestration/{log_run,git_agents_inventory,generate_bmad_playbook}.py`,
  le hook `UserPromptSubmit` (`orchestrator_gate.py`), et `routing-hints.json` (généré par le scan).

## 7. Comment reprendre dans un autre projet

1. **Skills** — copier `agent-orchestrator/SKILL.md`, `agent-supervisor/SKILL.md`,
   `revue-increment/SKILL.md` dans les `.claude/skills/<nom>/` correspondants du projet cible.
2. **Catalogue + playbooks** — copier `catalogue.md` dans `.claude/orchestration/` et `playbooks/`
   dans `.claude/orchestration/playbooks/`. **Adapter le catalogue** : ses recommandations citent
   des skills spécifiques à Interview-to-Deck (`run-dev-server`, `pptx-*`…).
3. **Scripts/hooks** — porter les `.py` de §6 et brancher les 4 hooks dans `settings.json` du cible.
4. **Conception** — déposer les `conception.md` dans `docs/reflexions/` du cible (référence).
5. **Purger** l'`allow`-list de `settings.json` (chemins propres à ce poste).
