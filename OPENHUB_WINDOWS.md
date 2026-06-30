# Utilisation d'OpenHub sous Windows

Prérequis recommandés :

- Git (Git Bash) ou WSL (Windows Subsystem for Linux)
- `jq` (nécessaire pour `oc deploy`)
- Node.js + npm (pour `opencode`)
- `opencode-ai` (installer via `npm install -g opencode-ai`)
- `sqlite3` (optionnel, pour `oc metrics` / dashboard)

Instructions rapides :

1. Utiliser Git Bash :

   - Ouvrez Git Bash depuis votre workspace.
   - Lancez la CLI `oc` via le wrapper :

```
external/openhub/oc.bat <commande> [args]
```

   Exemple :

```
external/openhub/oc.bat list
```

2. Utiliser WSL :

   - Ouvrez un terminal WSL. Convertissez le chemin Windows si nécessaire puis lancez :

```
bash /mnt/c/Users/<votre-nom>/Documents/VSCode2/external/openhub/oc.sh <commande>
```

Notes :

- Le script `install.sh` du dépôt est conçu pour Linux/macOS et effectue des vérifications interactives (installations de `jq`, `node`, `opencode`...). Sous Windows, il est recommandé d'installer manuellement les dépendances via Git Bash ou WSL.
- Pour une intégration plus poussée (alias `oc` global, installation automatique), exécuter `install.sh` depuis WSL ou une VM Linux.
- J'ai ajouté le fichier `external/openhub/oc.bat` comme wrapper pour exécuter `oc.sh` via `bash` (Git Bash ou WSL). Assurez-vous que `bash` est dans le PATH.

Si vous souhaitez, je peux :

- Installer automatiquement les dépendances dans WSL (si WSL installé et autorisé),
- Créer des scripts PowerShell supplémentaires pour appels plus ergonomiques,
- Intégrer un agent précis de `external/openhub/agents` dans votre application `app/`.
