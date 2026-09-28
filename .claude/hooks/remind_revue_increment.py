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
import threading

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


# --- bounded stdin read (anthropics/claude-code#87289) ---------------------
# Claude Code does not enforce a hook's timeout while it is blocked reading
# stdin: an unclosed pipe hangs the hook (and the launcher timeout does not
# kill the child python.exe). Read in a daemon thread and give up after 5 s;
# on timeout the existing fail-open path (no injection) applies.
def _stdin_borne(delai: float = 5.0):
    boite = {}

    def _cible():
        try:
            boite["v"] = sys.stdin.read()
        except BaseException:  # noqa: BLE001 - never raise from the reader
            boite["v"] = None

    fil = threading.Thread(target=_cible, daemon=True)
    fil.start()
    fil.join(delai)
    if fil.is_alive() or boite.get("v") is None:
        raise ValueError("stdin non recu")
    return boite["v"]


def main() -> None:
    try:
        json.loads(_stdin_borne())
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
