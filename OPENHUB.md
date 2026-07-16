# Intégration OpenHub / OpenCode

Ce document explique comment utiliser l'intégration OpenHub/OpenCode dans
l'application `Interview-to-Deck` — installation, usage dans l'app, et
spécificités Windows (Git Bash / WSL).

> **À ne pas confondre** avec le système de skills/agents de Claude Code
> (`.claude/`). OpenHub/OpenCode est un framework d'agents **externe** : le
> code de l'app shell-out vers la CLI `opencode` pour alimenter la page
> « Agents » de la mission, avec un repli simulé quand `opencode` n'est pas
> sur le PATH.

## Objectif

Invoquer des agents OpenHub depuis l'interface de mission. Le code lit les
agents définis dans `.opencode/agents`, exécute
`opencode run --agent <agent_id> <prompt>`, puis stocke le résultat en base.

## Prérequis

- Windows 10/11 (Git Bash ou WSL — voir la section Windows plus bas)
- Python 3.12+ avec un environnement virtuel `.venv`
- `pip install -r requirements.txt`
- `opencode` installé et accessible depuis le PATH
  (`opencode-ai` via `npm install -g opencode-ai`, nécessite Node.js + npm)
- `jq` (nécessaire pour `oc deploy`), `sqlite3` (optionnel, pour
  `oc metrics` / dashboard)

## Installation

1. Activer l'environnement virtuel :

   ```powershell
   Set-Location 'C:\Users\claude.camus\Documents\VSCode2'
   .\.venv\Scripts\Activate.ps1
   ```

2. Installer les dépendances :

   ```powershell
   pip install -r requirements.txt
   ```

3. Vérifier que `opencode` est présent et accessible depuis Python :

   ```powershell
   opencode --version
   python -c "import shutil; print(shutil.which('opencode'))"
   ```

## Démarrage de l'application

```powershell
uvicorn app.main:app --reload
```

Puis ouvrir http://127.0.0.1:8000

## Utilisation dans l'application

- Ouvrir une mission → bouton `Agents OpenHub`.
- La page liste tous les agents trouvés dans `.opencode/agents`.
- `Exécuter` invoque l'agent ; le résultat s'affiche dans la section
  `Résultat` et un historique récent est conservé en base.
- Si `opencode` n'est pas disponible, l'interface affiche un message d'erreur
  clair et passe en mode de démonstration temporaire.

## Utilisation de la CLI OpenHub sous Windows

Le script `install.sh` du dépôt est conçu pour Linux/macOS (vérifications
interactives, installation de `jq`/`node`/`opencode`…). Sous Windows,
installer les dépendances manuellement puis lancer la CLI `oc` via un wrapper.

**Git Bash** — lancer `oc` via le wrapper `oc.bat` (assurez-vous que `bash`
est dans le PATH) :

```bash
external/openhub/oc.bat <commande> [args]
external/openhub/oc.bat list          # exemple
```

**WSL** — convertir le chemin Windows si nécessaire puis :

```bash
bash /mnt/c/Users/<votre-nom>/Documents/VSCode2/external/openhub/oc.sh <commande>
```

Pour une intégration plus poussée (alias `oc` global, installation
automatique), exécuter `install.sh` depuis WSL ou une VM Linux.

## Architecture de l'intégration

- `app/services/openhub_agents.py` — découverte des agents dans
  `.opencode/agents`, détection de `opencode`, construction du prompt de
  mission, exécution réelle via `subprocess.run`.
- `app/routers/agents.py` — page de listing, route
  `POST /missions/{mission_id}/agents/{agent_id}/invoke`.
- `app/templates/missions/agents.html` — interface (lancer / lire les
  résultats).
- `app/models.py` — modèle `AgentResult` (persistance des invocations).

## Ajouter ou modifier des agents

Placer un fichier Markdown dans `.opencode/agents`. Le nom du fichier (ou son
frontmatter `id`) définit l'identifiant. Métadonnées utiles : `id`, `label`,
`description`, `mode`.

```md
---
id: documentarian
label: Documentarian
description: Agent de documentation du projet
mode: primary
---
```

## Dépannage

- **`opencode` détecté mais commande introuvable** — sous Windows, Python peut
  trouver `opencode.CMD` dans le PATH, mais l'appel `subprocess` doit utiliser
  le chemin complet renvoyé par `shutil.which('opencode')`.
- **Échec d'exécution d'agent** — vérifier que `.opencode/agents` existe et ne
  contient pas d'erreur de syntaxe ; relancer
  `opencode run --agent <agent_id> "test"` depuis la racine du projet.
- **Vérifier la disponibilité** :

  ```powershell
  python -c "import shutil; print(shutil.which('opencode'))"
  opencode --help
  ```

## Bonnes pratiques

- Ne pas ajouter d'agents non testés dans `.opencode/agents`.
- Vérifier les sorties de `opencode` dans le terminal avant d'appeler depuis
  l'application.
- Garder une `description` et un `mode` clairs pour chaque agent.
