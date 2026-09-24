r"""PreToolUse (Skill) — REFUSE une skill d'implémentation quand aucune user story n'existe.

POURQUOI. Arbitrage utilisateur du 2026-09-24 : les critères d'acceptance des user
stories, écrits en amont via `/bmad-create-epics-and-stories`, sont le référentiel de
conformité du sous-agent `utilisateur-produit`. Leur absence est un **prérequis absent,
bloquant pour tout nouveau développement** ; une correction de bug n'est possible
qu'après rattrapage (écrire d'abord les critères de la story touchée).

Constat qui a motivé le hook : sur Vscode7-CAT, un générateur de Handbook a été codé le
2026-09-17 avec un PRD mais sans architecture, sans epics.md, sans aucun critère
d'acceptance — alors que le playbook `dev-verifie` porte depuis le 2026-09-16 une étape
`cadrage-epics` dite « bloquante ». Un contrat de playbook n'est tenu que par la
discipline de celui qui l'instancie ; ce hook est le seul mécanisme opposable.

LE POINT DE CONTRÔLE. L'invocation d'une skill BMAD d'IMPLÉMENTATION (`bmad-build`,
`bmad-build-auto`, `bmad-agent-dev`) : c'est le moment où du code va être écrit. Si
`{planning_artifacts}/epics.md` n'existe pas dans le projet, on refuse en disant quoi
faire (générer les stories), jamais en laissant deviner.

CE QU'IL NE PRÉTEND PAS FAIRE. Il vérifie la PRÉSENCE du fichier, pas que la story
touchée y a des critères vérifiables — cela reste la lecture du sous-agent
`utilisateur-produit` (« CONFORMITE AUX ATTENDUS »). Il ne voit pas non plus un code
écrit par `Edit`/`Write` hors de toute skill : un garde-fou qui bloquerait toute écriture
de fichier tant qu'epics.md manque bloquerait aussi le rattrapage lui-même.

FAIL-OPEN INTÉGRAL. PreToolUse : entrée malformée, skill non reconnue, racine
introuvable → on laisse passer sans un mot. Il ne refuse que sur un cas positivement
établi : skill d'implémentation reconnue ET fichier absent.

RACINE DÉRIVÉE DE `__file__` (`.claude/hooks/<ce fichier>` → 3 niveaux), jamais du
`cwd` : un hook du kit peut être lancé depuis n'importe quel sous-répertoire de la
cible. `AGENT_SUPERVISION_EPICS` redirige le chemin contrôlé — même convention que les
autres garde-fous du dispositif, c'est ce qui le rend testable sans toucher au réel.
"""
import json
import os
import re
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EPICS = os.environ.get("AGENT_SUPERVISION_EPICS") or os.path.join(
    RACINE, "_bmad-output", "planning-artifacts", "epics.md")

# Les skills qui ÉCRIVENT du code applicatif. `bmad-create-epics-and-stories`,
# `bmad-architecture`, `bmad-prd`… n'y sont pas : ce sont elles qui produisent le
# prérequis, les bloquer rendrait le rattrapage impossible.
SKILLS_IMPLEMENTATION = ("bmad-build", "bmad-build-auto", "bmad-agent-dev")


_RE_CRITERES = re.compile(r"acceptance\s+criteria|crit[eè]res?\s+d[’']acceptance", re.I)


def _porte_des_criteres(chemin: str) -> bool:
    """Vrai si le fichier existe ET contient au moins un bloc de criteres d'acceptance."""
    try:
        with open(chemin, encoding="utf-8", errors="replace") as fh:
            return bool(_RE_CRITERES.search(fh.read()))
    except OSError:
        return False


def _laisser_passer():
    sys.exit(0)


def main() -> None:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except Exception:   # noqa: BLE001 - fail-open
        _laisser_passer()

    if not isinstance(data, dict) or data.get("tool_name") != "Skill":
        _laisser_passer()
    entree = data.get("tool_input")
    if not isinstance(entree, dict):
        _laisser_passer()
    nom = entree.get("skill")
    if not isinstance(nom, str):
        _laisser_passer()
    nom_bas = nom.strip().casefold().split(":")[-1]
    if nom_bas not in SKILLS_IMPLEMENTATION:
        _laisser_passer()

    # Presence ET contenu : un `epics.md` vide, ou sans un seul bloc de criteres, est
    # exactement le contournement qu'un garde-fou de presence laisse passer (revue
    # adversariale du 2026-09-24). Le gabarit BMAD marque chaque story par
    # « **Acceptance Criteria:** » ; on accepte aussi la forme francaise.
    if _porte_des_criteres(EPICS):
        _laisser_passer()

    etat = "absent" if not os.path.isfile(EPICS) else "present mais sans aucun critere d'acceptance"
    raison = (
        f"Skill d'implementation « {nom} » refusee : aucune user story exploitable "
        f"({os.path.relpath(EPICS, RACINE) if EPICS.startswith(RACINE) else EPICS} {etat}).\n"
        "Prerequis absent = BLOQUANT pour tout nouveau developpement (arbitrage du "
        "2026-09-24) : generer d'abord les epics / user stories et leurs criteres "
        "d'acceptance via /bmad-create-epics-and-stories (playbook cadrage-produit, ou "
        "l'etape cadrage-epics de dev-verifie ; prerequis de la skill : PRD + architecture).\n"
        "Correction de bug ? Rattrapage d'abord : ecrire les criteres d'acceptance de la "
        "story que le bug touche, puis corriger contre eux. Un correctif sans critere "
        "ecrit est un developpement a l'aveugle."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": raison,
    }}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:   # noqa: BLE001 - dernier filet
        sys.exit(0)
