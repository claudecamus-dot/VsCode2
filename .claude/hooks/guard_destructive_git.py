r"""PreToolUse hook (Bash/PowerShell) — deterministic backstop blocking
`git push --force` (without `--force-with-lease`) and `git reset --hard`.

Complements the git safety protocol already stated in prompt instructions
with something that can't be talked past by a persuasive-sounding reason in
context. Fails open (any parsing/edge-case error -> allow) so a bug here
never blocks unrelated shell usage.

Parsing (2026-07-16, merged from a sibling project's independent
implementation — its `shlex`-based tokenizer correctly handled leading
`VAR=value` env-var assignments and quote-safe tokenization, catching
`FOO=1 git push --force` where this hook's earlier regex-anchored version
(`^git\s+push\b`) silently let it through since the segment didn't start
with the literal string "git push"):
1. strip heredoc bodies first (always data, never a command to execute —
   e.g. a commit message *describing* this hook via
   `git commit -F - <<'EOF' ... EOF`, this project's own documented
   convention);
2. split on shell operators (&&, ||, ;, |, newline) without breaking segments
   apart inside quotes;
3. `shlex.split()` each segment and skip any leading `VAR=value` tokens
   before checking whether the first real token is `git`.
"""
import json
import os
import re
import shlex
import sys

_HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")


def _strip_heredocs(cmd: str) -> str:
    out = []
    i = 0
    for m in _HEREDOC_START.finditer(cmd):
        if m.start() < i:
            continue  # inside a heredoc body we already stripped
        out.append(cmd[i:m.end()])
        delim = m.group(2)
        nl = cmd.find("\n", m.end())
        if nl == -1:
            i = len(cmd)
            break
        body_start = nl + 1
        end_pat = re.compile(r"^[ \t]*" + re.escape(delim) + r"[ \t]*$", re.MULTILINE)
        end_m = end_pat.search(cmd, body_start)
        i = end_m.end() if end_m else len(cmd)
    out.append(cmd[i:])
    return "".join(out)


def _segments(cmd: str):
    """Les parentheses sont neutralisees ICI, en amont du decoupage : sans cela
    `(git push --force)` et `echo $(git push --force)` collaient le `(` au token de
    tete, qui n'etait donc plus `git` (verifie en rejouant le hook, 2026-08-31).
    Entre quotes elles restent intactes : `git commit -m "fix (bug)"` n'est pas coupe.

    Split on &&, ||, ;, |, (, ), newline — but not when inside '...' or "...". """
    segs = []
    buf = []
    quote = None
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if cmd[i : i + 2] in ("&&", "||"):
            segs.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in (";", "|", "(", ")", "\n"):
            segs.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segs.append("".join(buf))
    return [s.strip() for s in segs]


# Wrappers qui EXECUTENT leur argument : sans les reconnaitre,
# `eval "git push --force"` et `bash -c "git push --force"` passaient, le token de
# tete n'etant pas le mot `git`.
_WRAPPERS = frozenset({
    "eval", "exec", "command", "builtin", "env", "sudo", "doas", "nohup", "nice",
    "time", "xargs", "sh", "bash", "zsh", "dash", "ksh", "busybox",
})


def _nom_binaire(tok: str) -> str:
    """Nom du binaire invoque : `git`, `git.exe`, `/usr/bin/git` ou un chemin Windows
    absolu -> `git`. Le test litteral `lower[start] != "git"` exigeait le mot nu et
    laissait donc passer toute autre forme d'invocation (verifie en rejouant le hook
    avec un payload PreToolUse reel, 2026-08-31). `os.path.basename` decoupe sur `/`
    comme sur le separateur Windows."""
    nom = os.path.basename(tok).lower()
    if nom.endswith(".exe"):
        nom = nom[:-4]
    return nom


def _analyser(cmd: str, profondeur: int = 0):
    for seg in _segments(cmd):
        raison = _blocked_reason(seg, profondeur)
        if raison:
            return raison
    return None


def _blocked_reason(segment: str, profondeur: int = 0):
    # shlex respects quoting, so a quoted string like -m "... git push
    # --force ..." collapses into a single token instead of being split
    # into separate "git"/"push"/"--force" words.
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return None  # unbalanced quotes etc. — fail open, don't guess
    if not tokens:
        return None

    lower = [t.lower() for t in tokens]

    # Skip leading VAR=value env-var assignments so `FOO=1 git push --force`
    # is still recognized as a `git` invocation, not dismissed because the
    # segment doesn't start with the literal string "git".
    start = 0
    while start < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[start]):
        start += 1

    if start >= len(tokens):
        return None
    tete = _nom_binaire(tokens[start])

    # `eval "git push --force"` : la vraie commande est dans les arguments du wrapper.
    # Profondeur bornee (fail-open assume : on ne devine pas au-dela).
    if tete in _WRAPPERS:
        if profondeur >= 3:
            return None
        restants = tokens[start + 1 :]
        for candidat in [*restants, " ".join(restants)]:
            raison = _analyser(candidat, profondeur + 1)
            if raison:
                return raison
        return None

    if tete != "git":
        return None
    rest = lower[start + 1 :]

    if "push" in rest:
        has_force = any(t in ("--force", "-f") or t.startswith("--force=") for t in rest)
        has_lease = any(
            t == "--force-with-lease" or t.startswith("--force-with-lease=") for t in rest
        )
        if has_force and not has_lease:
            return (
                "git push --force (sans --force-with-lease) est bloqué par un hook projet. "
                "Utilisez --force-with-lease si nécessaire, ou confirmez explicitement avec "
                "l'utilisateur avant de contourner ce garde-fou."
            )

    if "reset" in rest and "--hard" in rest:
        return (
            "git reset --hard est bloqué par un hook projet (perte de modifications non "
            "commitées). Utilisez git stash, ou confirmez explicitement avec l'utilisateur."
        )

    raison = _blocked_worktree(tokens[start + 1 :], rest)
    if raison:
        return raison

    return None


# --------------------------------------------------------------------------- #
# Commandes qui DÉTRUISENT le travail non commité d'un fichier
# --------------------------------------------------------------------------- #
# Ajouté le 2026-09-02, sur un incident réel : un sous-agent de revue, dont le
# mandat dit pourtant qu'il « ne corrige rien », a joué `git checkout --` sur
# deux templates pour mesurer le code d'avant. Les correctifs non commités de la
# session appelante ont disparu du disque. Ils ont pu être reconstruits depuis
# des copies hors dépôt, mais rien dans le dispositif ne s'y opposait : le
# garde-fou ne connaissait que `push --force` et `reset --hard`, deux commandes
# qui touchent l'HISTORIQUE, alors que le travail perdu ce jour-là était dans
# l'ARBRE. C'est la classe entière qu'il fallait couvrir, pas le cas vu.
#
# Le remède n'est pas d'interdire de mesurer le code d'avant : c'est un besoin
# légitime d'une revue. Le message dit donc comment le faire sans rien détruire
# (`git show HEAD:<fichier>`, qui écrit sur la sortie standard).

_CREATION_DE_BRANCHE = frozenset({"-b", "-B", "--orphan", "--track", "--no-track", "--detach"})

_ALTERNATIVE = (
    "Pour lire le code d'avant sans toucher au disque : `git show HEAD:<fichier>` "
    "(ou `git diff` pour l'écart). Si l'écrasement est réellement voulu, copiez "
    "d'abord le fichier hors du dépôt et confirmez avec l'utilisateur."
)


def _est_un_chemin_du_depot(tok: str) -> bool:
    """Vrai si `tok` désigne un fichier ou un dossier réellement présent.

    C'est ce qui sépare `git checkout main` (une branche : rien à écraser) de
    `git checkout app/templates/x.html` (un fichier : ses modifications non
    commitées disparaissent). Deviner sur la forme du nom ne marcherait pas —
    une branche s'appelle souvent `feature/x`, avec une barre oblique comme un
    chemin. On regarde donc le disque, et on échoue en laissant passer."""
    if tok in (".", "./", ":/"):
        return True
    try:
        racine = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        return os.path.exists(os.path.join(racine, tok)) or os.path.exists(tok)
    except Exception:
        return False


def _flags_courts_groupes(rest: list) -> str:
    """Les lettres de tous les groupes de drapeaux courts (`-fdx` -> 'fdx').
    Sans ce dépliage, chercher `-f` laissait passer `git clean -fd`, qui est
    exactement la forme qu'on écrit en pratique."""
    lettres = []
    for t in rest:
        if t.startswith("-") and not t.startswith("--") and len(t) > 1:
            lettres.append(t[1:])
    return "".join(lettres)


def _blocked_worktree(tokens_apres_git: list, rest: list):
    sous_commande = next((t for t in rest if not t.startswith("-")), None)

    if sous_commande == "checkout":
        args = tokens_apres_git[1:]
        # `git checkout … -- <chemin>` : tout ce qui suit `--` est un chemin, la
        # forme la plus explicite et la plus destructive.
        if "--" in [t.lower() for t in args]:
            i = [t.lower() for t in args].index("--")
            if args[i + 1 :]:
                return (
                    "git checkout -- <chemin> est bloqué par un hook projet : il ÉCRASE "
                    "les modifications non commitées du fichier, sans copie de secours. "
                    + _ALTERNATIVE
                )
        if any(t.lower() in _CREATION_DE_BRANCHE for t in args):
            return None  # création/bascule de branche : rien de l'arbre n'est perdu
        for t in args:
            if not t.startswith("-") and _est_un_chemin_du_depot(t):
                return (
                    "git checkout <chemin> est bloqué par un hook projet : `%s` existe "
                    "sur le disque, ses modifications non commitées seraient écrasées. "
                    "Pour changer de branche, le nom ne doit pas être celui d'un fichier "
                    "existant. " % t + _ALTERNATIVE
                )
        return None

    if sous_commande == "restore":
        bas = [t.lower() for t in rest]
        # `git restore --staged <chemin>` ne touche QUE l'index : il désindexe,
        # il ne détruit rien. Il reste donc autorisé — sauf s'il est cumulé avec
        # `--worktree`, qui lui écrase bien le fichier.
        que_l_index = ("--staged" in bas or "-S" in rest) and not (
            "--worktree" in bas or "-W" in rest
        )
        if que_l_index:
            return None
        return (
            "git restore <chemin> est bloqué par un hook projet : il ÉCRASE les "
            "modifications non commitées du fichier. `git restore --staged` (qui ne "
            "touche que l'index) reste autorisé. " + _ALTERNATIVE
        )

    if sous_commande == "clean":
        bas = [t.lower() for t in rest]
        if "--force" in bas or "f" in _flags_courts_groupes(rest):
            return (
                "git clean -f est bloqué par un hook projet : il SUPPRIME les fichiers "
                "non suivis, donc tout fichier neuf pas encore ajouté (un test qu'on "
                "vient d'écrire, par exemple). Listez-les d'abord avec `git clean -n`, "
                "puis confirmez avec l'utilisateur."
            )
        return None

    if sous_commande == "stash":
        bas = [t.lower() for t in rest]
        if "drop" in bas or "clear" in bas:
            return (
                "git stash drop/clear est bloqué par un hook projet : la remise ainsi "
                "supprimée n'est plus récupérable par aucune commande ordinaire. "
                "Confirmez avec l'utilisateur."
            )
        return None

    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    cmd = _strip_heredocs(cmd)

    blocked = _analyser(cmd)

    if blocked:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": blocked,
            }
        }))


if __name__ == "__main__":
    main()
