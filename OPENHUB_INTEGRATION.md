# Intégration OpenHub/OpenCode

Ce document explique comment utiliser l’intégration OpenHub/OpenCode dans l’application `Interview-to-Deck`.

## Objectif

L’intégration permet d’invoquer des agents OpenHub depuis l’interface de mission.
Le code lit les agents définis dans `.opencode/agents`, exécute `opencode run --agent <agent_id> <prompt>`, puis stocke le résultat en base.

## Prérequis

- Windows 10/11
- Python 3.12+ avec un environnement virtuel `.venv`
- `pip install -r requirements.txt`
- `opencode` installé et accessible depuis le PATH
- Projet cloné dans `C:\Users\claude.camus\Documents\VSCode2`

## Installation

1. Activer l’environnement virtuel :

```powershell
Set-Location 'C:\Users\claude.camus\Documents\VSCode2'
.\.venv\Scripts\Activate.ps1
```

2. Installer les dépendances :

```powershell
pip install -r requirements.txt
```

3. Vérifier que `opencode` est présent :

```powershell
opencode --version
```

4. Vérifier que la commande est accessible depuis Python :

```powershell
python -c "import shutil; print(shutil.which('opencode'))"
```

## Démarrage de l’application

```powershell
uvicorn app.main:app --reload
```

Puis ouvrir :

- http://127.0.0.1:8000

## Utilisation OpenHub dans l’application

### Page OpenHub agents

- Ouvrir une mission
- Cliquer sur le bouton `Agents OpenHub`
- La page affiche tous les agents trouvés dans `.opencode/agents`
- Cliquer sur `Exécuter` pour invoquer un agent

### Résultats

- Le résultat de l’invocation s’affiche dans la section `Résultat`
- Une historique récent des exécutions est conservée en base
- Si `opencode` n’est pas disponible, l’interface affiche un message d’erreur clair

## Architecture de l’intégration

### Fichiers clés

- `app/services/openhub_agents.py`
  - découverte des agents dans `.opencode/agents`
  - détection de `opencode`
  - construction du prompt de mission
  - exécution réelle via `subprocess.run`

- `app/routers/agents.py`
  - page de listing des agents
  - route `POST /missions/{mission_id}/agents/{agent_id}/invoke`

- `app/templates/missions/agents.html`
  - interface utilisateur pour lancer et lire les résultats

- `app/models.py`
  - modèle `AgentResult` pour stocker les invocations

## Ajouter ou modifier des agents

- Placer un fichier Markdown dans `.opencode/agents`
- Le nom du fichier ou son frontmatter `id` définit l’identifiant de l’agent
- Les métadonnées utiles sont :
  - `id`
  - `label`
  - `description`
  - `mode`

### Exemple de frontmatter

```md
---
id: documentarian
label: Documentarian
description: Agent de documentation du projet
mode: primary
---
```

## Dépannage

### `opencode` détecté mais commande introuvable

Sous Windows, Python peut trouver `opencode.CMD` dans le PATH, mais l’appel `subprocess` doit utiliser le chemin complet renvoyé par `shutil.which('opencode')`.

### Échec d’exécution d’agent

- Vérifier que le dossier `.opencode/agents` existe
- Vérifier qu’il n’y a pas d’erreur de syntaxe dans le fichier agent
- Relancer `opencode run --agent <agent_id> "test"` depuis le répertoire racine du projet

### Vérifier la disponibilité OpenCode

```powershell
python -c "import shutil; print(shutil.which('opencode'))"
opencode --help
```

## Points importants

- L’application utilise maintenant un vrai runtime OpenCode pour invoquer les agents, pas une simulation.
- Les résultats sont persistés dans la base SQLite et affichés dans l’historique.
- Si `opencode` n’est pas installé, l’interface conserve un message d’erreur utilisateur et passe en mode de démonstration temporaire.

## Bonnes pratiques

- Ne pas ajouter d’agents non testés dans `.opencode/agents`
- Vérifier les sorties de `opencode` dans le terminal avant d’appeler depuis l’application
- Garder la description et le mode clairs pour chaque agent
