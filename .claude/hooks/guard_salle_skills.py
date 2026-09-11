r"""PreToolUse (Skill) — REFUSE une convocation de salle qui ne nomme pas ses skills BMAD.

POURQUOI CE HOOK EXISTE, et pourquoi il BLOQUE là où un rappel aurait suffi en théorie.

Mesure du 2026-09-02 : sur 46 skills BMAD installées, 2 seulement avaient jamais été
invoquées en 135 sessions. La table de routage qui les désigne existe depuis le
2026-07-30 et n'a rien déclenché pendant 33 jours. Le premier correctif de la journée a
consisté à ajouter un champ `skills_bmad` aux 12 salles et un paragraphe à la skill
d'orchestration — puis `bmad-advanced-elicitation` (pré-mortem + red team, invoquée sur
ce correctif même) a montré que c'était la MÊME NATURE que ce qui avait échoué : une
donnée dans un TOML plus une phrase dans un document de 700 lignes, avec trois « penser
à » entre la donnée et l'acte.

Le seul mécanisme opposable de ce dépôt est un hook bloquant — c'est ainsi que
`guard_destructive_git.py` refuse un `git push --force`. D'où celui-ci.

LE POINT DE CONTRÔLE. La convocation `bmad-party-mode --party <salle>` : c'est le moment
où le nom de la salle est connu et où le brief s'écrit. Si le texte de la convocation ne
nomme AUCUNE des skills que la salle déclare, on refuse, en listant celles qu'on
attendait — un refus qui ne dit pas quoi écrire fait deviner, et on redevine mal.

CE QU'IL NE PRÉTEND PAS FAIRE. Il vérifie que la convocation PORTE les noms, pas qu'une
voix a réellement chargé la skill. Cette seconde vérification ne peut être qu'a
posteriori, contre le compteur de l'étage 1. Annoncer plus que ce qu'on mesure est le
défaut que ce dépôt paie en boucle ; ce hook s'en tient donc à ce qu'il voit.

FAIL-OPEN INTÉGRAL. Il tourne en `PreToolUse` : une exception y bloquerait un appel
d'outil légitime. Entrée malformée, `tomllib` absent, TOML illisible, salle inconnue,
salle sans `skills_bmad` → on laisse passer, sans un mot. Il ne refuse que sur un cas
positivement établi. Un garde-fou qui refuse ce qu'il ne comprend pas devient un obstacle,
et on le contourne : ça coûte plus cher que de ne pas l'avoir.

RACINE DÉRIVÉE DE `__file__`, jamais du `cwd` : la leçon est celle de
`warn_verif_before_commit.py`, généralisé le matin même — un hook du kit peut être lancé
depuis n'importe quel sous-répertoire de la cible. Ce hook est dans le kit publié depuis
le 2026-09-02 (arbitrage utilisateur tracé à `hooks:guard_salle_skills` dans
`arbitrages.json` ; la revue de commit du même jour avait relevé qu'il n'y était pas alors
que son docstring l'affirmait). « Dans le kit » n'est pas « chez les cibles » : la
propagation vers VSCode..VSCode4 est en standby, arbitrée le même jour. `AGENT_SUPERVISION_PARTY_TOML`
permet de le rediriger — même convention que les autres journaux du dispositif, et c'est
ce qui le rend testable sans toucher au réel.

LA PORTE DE SORTIE EXISTE DANS LE CODE, pas seulement dans le message (revue du
2026-09-02 : le refus prescrivait d'écrire « aucune skill BMAD sur ce tour, parce que… »,
et cette phrase était refusée à son tour — la seule issue était de recopier un nom, le
comportement que le message condamnait). Une renonciation explicite laisse passer.

CE QUE LA COMPARAISON VAUT. Insensible à la casse et aux guillemets autour du nom de
salle ; mais elle reste une présence de chaîne — « on n'invoquera PAS bmad-help » passe.
Vérifier l'engagement réel d'une voix n'est possible qu'a posteriori, sur le compteur.
"""
import json
import os
import re
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOML_SALLES = os.environ.get("AGENT_SUPERVISION_PARTY_TOML") or os.path.join(
    RACINE, "_bmad", "custom", "bmad-party-mode.toml")

# `--party <id>` ou `--group <id>` (l'alias documenté par la skill).
_RE_PARTY = re.compile(r"--(?:party|group)[=\s]+[\x22\x27]?([A-Za-z0-9_-]+)", re.I)
# Renonciation explicite : la convocation dit qu'aucune skill ne partira, et pourquoi.
_RE_RENONCE = re.compile(r"aucune\s+skill\s+bmad\s+sur\s+ce\s+tour", re.I)


def _laisser_passer():
    """Sortie neutre : aucune décision, le harnais poursuit."""
    sys.exit(0)


def _salle_declaree(salle):
    """Les skills que cette salle déclare, ou [] dans TOUS les cas douteux."""
    try:
        import tomllib
        with open(TOML_SALLES, "rb") as fh:
            data = tomllib.load(fh)
        for groupe in (data.get("workflow") or {}).get("party_groups") or []:
            if str(groupe.get("id") or "").casefold() == salle:
                declarees = groupe.get("skills_bmad") or []
                return [s for s in declarees if isinstance(s, str)]
    except Exception:   # noqa: BLE001 - fail-open, cf. docstring
        return []
    return []


def main() -> None:
    try:
        # Octets lus et décodés EXPLICITEMENT en UTF-8 : sous `py <script>` sur Windows,
        # stdin/stdout sont en cp1252 et le harnais parle UTF-8. La sortie, elle, est en
        # ASCII pur (`ensure_ascii` par défaut) : un `deny` qui sort en cp1252 est un
        # `deny` illisible (revue du 2026-09-02, reproduit : 0xab dans « »).
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except Exception:   # noqa: BLE001
        _laisser_passer()

    if not isinstance(data, dict) or data.get("tool_name") != "Skill":
        _laisser_passer()

    entree = data.get("tool_input")
    if not isinstance(entree, dict):
        _laisser_passer()

    # Le nom de la skill peut varier de forme selon l'appelant ; on ne refuse que si on
    # reconnaît positivement party-mode.
    nom = entree.get("skill")
    if not isinstance(nom, str) or "party-mode" not in nom.casefold():
        _laisser_passer()

    args = entree.get("args")
    if not isinstance(args, str) or not args.strip():
        _laisser_passer()

    trouve = _RE_PARTY.search(args)
    if not trouve:
        _laisser_passer()

    attendues = _salle_declaree(trouve.group(1).casefold())
    if not attendues:
        _laisser_passer()

    if _RE_RENONCE.search(args):
        _laisser_passer()

    bas = args.casefold()
    if any(s.casefold() in bas for s in attendues):
        _laisser_passer()

    raison = (
        f"Convocation de la salle « {trouve.group(1)} » sans aucune de ses skills BMAD.\n"
        f"Cette salle déclare : {', '.join(attendues)}.\n"
        "Nomme dans le brief celle(s) que ses voix doivent charger via l'outil Skill, et "
        "dis QUELLE voix la charge — une voix part avec un contexte vierge, elle n'a ni la "
        "table de routage ni le TOML.\n"
        "Si aucune skill n'a d'objet sur ce sujet, écris-le dans la convocation : « aucune "
        "skill BMAD sur ce tour, parce que… ». Un refus assumé et dit vaut mieux qu'un nom "
        "recopié pour faire passer un garde-fou — c'est le compteur d'usage qu'on veut "
        "juste, pas gonflé.\n"
        "(Mesuré le 2026-09-02 : 2 skills BMAD invoquées sur 46 en 135 sessions ; la table "
        "de routage seule n'a rien déclenché en 33 jours.)"
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
    except Exception:   # noqa: BLE001 - dernier filet, cf. docstring
        sys.exit(0)
