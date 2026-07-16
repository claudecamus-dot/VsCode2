"""SessionStart hook — systématise la boucle de revue-et-amélioration.

Réinjecte, au début de chaque session, la discipline « definition of done »
du projet : la revue fine + l'application des correctifs + la re-vérification
réelle ne doivent pas dépendre de « penser à les lancer ». Le skill
`revue-increment` porte le protocole ; ce hook garantit qu'il est rappelé
systématiquement et récurremment (à chaque session), sans ajouter de friction
par-commit.

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
    "`/simplify`, correctifs concrets), PUIS re-vérification RÉELLE (pytest + "
    "run-dev-server / pptx-verify, pas seulement pytest vert). Ne pas déclarer "
    "« fait » avec une vérif runtime sautée ou un correctif évident non "
    "appliqué. Les actions sensibles/irréversibles (suppression de fichier "
    "versionné, écriture en base réelle) se proposent, ne s'exécutent pas "
    "unilatéralement. "
    "Écosystème de skills : BMAD est installé (`_bmad/`, ~46 skills `bmad-*`). "
    "En cas de doute sur quel skill lancer, invoquer `bmad-help` (routeur). "
    "`revue-increment` reste la boucle courte definition-of-done et délègue à "
    "`bmad-code-review` (revue adversariale) / `bmad-retrospective` (rétro "
    "d'epic) plutôt que de les dupliquer."
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
