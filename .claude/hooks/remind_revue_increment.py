"""SessionStart hook — systématise la boucle de revue-et-amélioration.

Réinjecte, au début de chaque session, la discipline « definition of done »
du projet : la revue fine + l'application des correctifs + la re-vérification
réelle ne doivent pas dépendre de « penser à les lancer ». Le skill
`revue-increment` porte le protocole ; ce hook garantit qu'il est rappelé
systématiquement et récurremment (à chaque session), sans friction par-commit.

Non bloquant : émet seulement un `additionalContext` (SessionStart). Fails
open — toute erreur de parsing rend la main sans injecter, pour ne jamais
casser un démarrage de session.
"""
import json
import sys

REMINDER = (
    "Discipline qualité du projet (rappel systématique) : avant de considérer "
    "un incrément « livré » ou de committer du code produit, lancer la boucle "
    "`/revue-increment` — revue fine (produit + façon de travailler), PUIS "
    "application des actions d'amélioration (`/code-review high --fix`, "
    "`/simplify`, correctifs concrets), PUIS re-vérification RÉELLE (tests + "
    "exécution/rendu réel via le chemin de vérif du projet, pas seulement des "
    "tests verts). Ne pas déclarer « fait » avec une vérif runtime sautée ou un "
    "correctif évident non appliqué. Les actions sensibles/irréversibles "
    "(suppression de fichier versionné, écriture en base réelle) se proposent, "
    "ne s'exécutent pas unilatéralement. "
    "Écosystème de skills : si BMAD est installé (`_bmad/`, skills `bmad-*`), "
    "invoquer `bmad-help` en cas de doute sur quel skill lancer ; "
    "`revue-increment` délègue à `bmad-code-review` / `bmad-retrospective` "
    "plutôt que de les dupliquer."
)


def main() -> None:
    try:
        json.load(sys.stdin)
    except Exception:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": REMINDER,
        }
    }))


if __name__ == "__main__":
    main()
