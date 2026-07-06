# Export de configuration Claude Code — pour réintégration dans un nouveau projet

Ce document recense ce qui, dans la configuration Claude Code d'Interview-to-Deck,
est **réutilisable tel quel** dans un autre projet, par opposition à ce qui est
spécifique à ce dépôt. Objectif : bootstrap rapide d'un nouveau projet sans
redécouvrir ces réglages depuis zéro.

## 1. Fichiers projet à copier tels quels

### `.claude/settings.json` — copier la structure, PAS le contenu en l'état

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|PowerShell",
        "hooks": [{ "type": "command", "command": "py .claude/hooks/guard_destructive_git.py", "timeout": 10 }]
      }
    ]
  },
  "permissions": {
    "disableBypassPermissionsMode": "disable",
    "deny": ["Read(./.env)", "Read(./secrets/**)", "Read(./config/credentials.json)"]
  }
}
```

Ce qui est réutilisable : le mécanisme de hook `PreToolUse` sur `Bash|PowerShell`,
et le pattern `permissions.deny` pour bloquer la lecture de secrets — adapter les
chemins à la structure du nouveau projet.

⚠️ **Ne pas copier `permissions.allow` tel quel.** Cette liste s'accumule au fil
des sessions avec des commandes one-off (chemins absolus machine-spécifiques,
UUID de scratchpad temporaires, fichiers de test ponctuels supprimés depuis) —
c'est du bruit accumulé, pas un réglage intentionnel. Repartir d'une liste vide
et laisser l'auto-approbation se reconstruire naturellement sur le nouveau projet.

### `.claude/hooks/guard_destructive_git.py` — copier tel quel

Garde-fou déterministe (pas une instruction de prompt) bloquant `git push --force`
(sans `--force-with-lease`) et `git reset --hard`. Agnostique du projet, aucune
adaptation nécessaire. Points d'implémentation à connaître si on l'étend :
- Fail-open : toute erreur de parsing laisse passer plutôt que de bloquer à tort.
- Découpe la commande en segments (`&&`, `||`, `;`, `|`, saut de ligne) sans se
  faire piéger par ces caractères à l'intérieur de guillemets.
- Neutralise d'abord le contenu des heredocs (`<<EOF ... EOF`) avant de chercher
  les motifs interdits — sans ça, un message de commit qui *décrit* la commande
  bloquée (ex. « ce hook bloque git push --force ») déclenche un faux positif.

### `.gitignore` — entrées Claude Code

```gitignore
# Claude Code — réglages/notes propres à une machine ou une personne (jamais
# partagés), à distinguer de .claude/settings.json et .claude/skills|hooks|
# agents/ qui eux restent versionnés pour toute l'équipe.
.claude/settings.local.json
CLAUDE.local.md
```

## 2. Structure de `CLAUDE.md` à répliquer

Sections qui ont fait leurs preuves sur ce projet, dans cet ordre :

1. **Un paragraphe de contexte projet** : ce que fait l'app, le vocabulaire
   métier à préserver tel quel (ne pas laisser traduire/angliciser des termes
   établis), pointeur vers un roadmap et des docs plus profonds — avec un
   avertissement explicite s'ils peuvent être obsolètes.
2. **Commandes** : setup/run/test, copier-collables telles quelles, avec un
   exemple de lancement d'un test unique et d'un sous-ensemble par mot-clé.
3. **Architecture** : le flux de requête (couches), le modèle de données, puis
   une sous-section par module non trivial avec **des décisions non
   redérivables du code** (le "pourquoi", pas le "quoi") — ex. « pourquoi ce
   composant fait X et pas Y », avec référence `fichier:ligne`.
4. **Section "Claude Code project setup"** explicite : quels fichiers sont
   versionnés vs locaux, ce que bloque le hook, quels skills projet existent et
   ce qu'ils remplacent (« utiliser X plutôt que redécouvrir Y »).

## 3. Pattern de skill projet-local

Un skill projet (`.claude/skills/<nom>/SKILL.md`) documente une séquence
opérationnelle déjà découverte une fois, pour ne pas la redécouvrir à chaque
session. Gabarit qui fonctionne bien (voir `run-dev-server` dans ce projet) :

- Frontmatter avec `description` qui **nomme explicitement les déclencheurs**
  (« Use whenever asked to run/start/preview the app, or to verify a
  template/CSS/JS change actually renders correctly — not just that tests
  pass ») plutôt qu'une description vague.
- Sections numérotées, une par sous-capacité (lancer, peupler avec des
  données réelles, vérifier visuellement, exercer un endpoint HTMX isolé).
- Commandes **copier-collables**, pas de prose décrivant ce qu'il faudrait
  faire — le skill est un script commenté, pas un tutoriel.
- Pièges d'environnement documentés explicitement (ex. un flag obligatoire
  sous peine d'erreur cryptique, un port à éviter pour ne pas entrer en
  collision avec un serveur déjà lancé par l'utilisateur).

## 4. Skills globaux (utilisateur) mobilisés par ce projet

Ces skills vivent dans `~/.claude/skills/` (partagés entre tous les projets,
pas à copier dans le dépôt) — utile de savoir lesquels sont pertinents pour un
projet similaire (génération de livrables PowerPoint, suivi de roadmap) :

- `pptx-deck` / `pptx-verify` / `restitution-deck-design` — trio génération
  + vérification rendu + design system pour des decks python-pptx.
- `roadmap-keeper` — suivi visuel Réflexion/Conception/Réalisation avec
  tracking de consommation de tokens ; démarre par un gabarit prêt à l'emploi.
- `run` / `run-dev-server` — lancer et piloter l'app pour vérifier un
  changement dans un vrai navigateur plutôt que sur la seule foi des tests.
- `code-review` / `simplify` — revue et nettoyage du diff courant.
- `verify` — exercer end-to-end un changement avant de le considérer fini.
- `artifact-design` — calibrer l'investissement de design avant de produire
  une page HTML/Markdown en Artifact.

## 5. Suivi de roadmap (`.roadmap/`)

- `.roadmap/roadmap.json` **versionné** (source de vérité editable) ;
  `.roadmap/*.svg` **gitignoré** (régénéré à la demande par `roadmap-keeper`,
  pas une source de vérité à committer).
- `CLAUDE.md` doit préciser explicitement que ce fichier peut être en avance
  ou en retard sur le code réel — vérifier contre `git log`/`git status`
  avant de faire confiance à son statut affiché.

## 6. Enseignements opérationnels (à ne pas redécouvrir)

- **Les outils `Bash` et `PowerShell` peuvent tourner dans des namespaces de
  process différents** sur cet environnement Windows : un process lancé en
  arrière-plan via `Bash` (ex. `uvicorn ... &`) peut être invisible à
  `Get-CimInstance`/`Stop-Process` appelés via l'outil `PowerShell` — celui-ci
  ne voit/tue rien, silencieusement, sans erreur. Pour arrêter un process
  lancé via `Bash`, invoquer `powershell.exe` **depuis `Bash`** plutôt que via
  l'outil `PowerShell` séparé.
- **`verifier_geometrie()` (ou tout garde-fou géométrique équivalent) ne
  détecte que des formes hors du cadre de la slide** — pas un texte qui
  déborde de sa propre zone, ni un fichier qu'un vrai lecteur refuse d'ouvrir.
  Un export qui « passe les tests » n'est pas encore un export vérifié
  visuellement (voir §7 — c'est justement le point qui a motivé le test
  automatisé de rendu réel ajouté à ce projet).
- Un process headless (navigateur, LibreOffice) lancé pour une vérification
  ponctuelle peut rester orphelin si le test/script échoue avant de le tuer
  explicitement — toujours vérifier qu'aucun processus superflu ne traîne en
  fin de session, pas seulement les fichiers temporaires.

## 7. Renforcer la suite de tests avec un vrai moteur de rendu

Constat sur ce projet : plusieurs bugs de rendu réels (titres qui chevauchent
le contenu, champs qui débordent, template client illisible par PowerPoint —
incrément 5) sont passés inaperçus de la suite `pytest` alors qu'elle passait
au vert, parce que `python-pptx` est un **parseur tolérant** — un fichier peut
être syntaxiquement valide pour lui tout en étant rejeté ou mal rendu par un
vrai lecteur. Le garde-fou géométrique automatique (`verifier_geometrie`)
réduit ce risque mais ne l'élimine pas (voir §6).

Renforcement ajouté : un test `pytest` qui convertit un export dense/adversarial
via LibreOffice headless (`soffice --headless --convert-to pdf`) et vérifie que
la conversion réussit avec le bon nombre de pages — `@pytest.mark.skipif` si
LibreOffice n'est pas sur le PATH, sur le même principe que les tests qui
sautent si `faster-whisper` n'est pas installé. Reproductible dans un nouveau
projet générant des fichiers Office/PDF : la conversion réelle réussie est un
signal bien plus fort que le seul parsing réussi de la bibliothèque de
génération.
