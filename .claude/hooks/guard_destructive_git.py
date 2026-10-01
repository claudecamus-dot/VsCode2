r"""PreToolUse hook (Bash/PowerShell) — garde-fou deterministe, version unifiee
de la flotte (fusion des deux lignees le 2026-09-08).

Bloque trois familles de commandes :
1. l'HISTORIQUE : `git push --force` (sans `--force-with-lease`), la refspec
   forcee par `+` (`git push origin +main`) et `git reset --hard`, y compris
   ses abreviations non ambigues (`--har`, `--h`) ;
2. l'ARBRE : les commandes qui ecrasent le travail non commite d'un fichier
   (`git checkout -- <chemin>`, `git restore <chemin>`, `git clean -f`,
   `git stash drop/clear`, `git rm -f`, `git worktree remove --force`) ;
3. la LECTURE des chemins proteges (`.env`, `secrets/**`,
   `config/credentials.json`) — miroir des deny rules `Read(...)` de
   `.claude/settings.json`, qui ne couvrent QUE l'outil `Read` : cote shell,
   `cat .env` sortait sans aucune resistance.

Plus, transversalement, les WRAPPERS et INDIRECTIONS qui executent l'une de ces
commandes sans que le token de tete soit `git` : `eval`, `env`, `xargs`, `sudo`,
l'operateur d'appel PowerShell `&` et `.`, mais aussi `iex` /
`Invoke-Expression`, `powershell -Command`, `-EncodedCommand` (base64),
`bash -c`, `cmd /c`, et les executables `git.exe` / `git.cmd` / `git.bat` /
`git.ps1`.

CE QUE CE HOOK N'EST PAS. Un garde-fou contre l'accident et le contournement
de confort, pas une frontiere de securite. Il `fail open` sur tout ce qu'il ne
sait pas analyser, par choix : un bug ici ne doit jamais bloquer un usage shell
sans rapport. Ne pas s'en servir pour justifier de baisser la garde ailleurs.

LIMITE CONNUE : L'INDIRECTION D'EXECUTION — une CLASSE, pas un cas isole. Ce
docstring n'en declarait qu'UN (`$g = 'git'; & $g push --force`), ce qui laissait
croire a un trou a colmater. La sonde adversariale du 2026-09-19, rejouee par le
chemin de PRODUCTION (payload `PreToolUse` reel sur stdin, 16 commandes), a mesure
12 bloquees / 4 passees, et les 4 passees sont quatre visages du MEME defaut :
variable PowerShell, alias PowerShell (`Set-Alias`), `Invoke-Expression` sur une
chaine CONCATENEE, `xargs` qui reinjecte l'option depuis stdin, et `py -c` +
`subprocess`. Le point commun : le nom de la commande dangereuse n'existe PAS dans
le texte analyse — il est calcule a l'execution. AUCUNE analyse lexicale de la
ligne de commande ne rattrape cette classe, quel que soit le nombre de motifs
ajoutes ; la colmater cas par cas produirait une fausse assurance, qui est pire
que la limite declaree. `tests/test_guard_destructive_git_indirection.py` en
garde la trace en `xfail` : un futur durcissement s'y signalera en XPASS.

CE QUI TIENT, mesure le meme jour et a conserver : `cmd /c`, `sh -c`, prefixe de
variable d'environnement, chemin absolu, enchainement `;` / `&&` / retour a la
ligne, espaces multiples, casse, et `git -C ../VSCode2 reset --hard` (une cible
hors du depot courant).

LA GARDE ROBUSTE EST AILLEURS. Contre un `push --force`, ce qui protege vraiment
est cote depot DISTANT — protection de branche GitHub — et non cote prompt : le
serveur refuse l'operation quelle que soit la facon dont le client l'a formulee.
Ce hook reste utile comme friction sur l'accident ; il ne remplace pas ce reglage.

Analyse (tokenizer `shlex` du 2026-07-16, repris d'un projet frere : il gerait
deja les `VAR=value` de tete, la ou la version regex precedente
(`^git\s+push\b`) laissait passer `FOO=1 git push --force`) :
1. retirer les corps de heredoc (toujours de la donnee, jamais une commande —
   p.ex. un message de commit qui *decrit* ce hook via
   `git commit -F - <<'EOF' ... EOF`, convention documentee de ces depots) ;
2. decouper sur les operateurs shell (&&, ||, ;, |, (, ), saut de ligne) sans
   casser les segments a l'interieur des quotes ;
3. `shlex.split()` chaque segment, sauter les `VAR=value` de tete, puis
   normaliser l'executable (basename, sans `.exe`/`.cmd`/`.bat`/`.ps1`) ;
4. si la commande en EXECUTE une autre (wrapper ou indirection), re-analyser la
   charge utile comme une commande a part entiere, en bornant la recursion a
   `_MAX_DEPTH`.
"""
import base64
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


_MAX_DEPTH = 3

# Wrappers qui EXECUTENT leur argument : sans les reconnaitre,
# `eval "git push --force"` et `bash -c "git push --force"` passaient, le token de
# tete n'etant pas le mot `git`.
_WRAPPERS = frozenset({
    "eval", "exec", "command", "builtin", "env", "sudo", "doas", "nohup", "nice",
    "time", "xargs", "sh", "bash", "zsh", "dash", "ksh", "busybox",
    # `&` est l OPERATEUR D APPEL de PowerShell — le shell PRIMAIRE de cet
    # environnement, et ce hook est monte sur le matcher `Bash|PowerShell`. Il execute
    # ce qui le suit exactement comme `eval` : `& git push --force` passait, alors que
    # `git push --force` etait bloque. Verifie que l operateur lance bien git avant de
    # le traiter comme un wrapper (revue de securite du 2026-09-01).
    "&", ".",
})


def _nom_binaire(tok: str) -> str:
    """Nom du binaire invoque : `git`, `git.exe`, `/usr/bin/git` ou un chemin Windows
    absolu -> `git`. Le test litteral `lower[start] != "git"` exigeait le mot nu et
    laissait donc passer toute autre forme d'invocation (verifie en rejouant le hook
    avec un payload PreToolUse reel, 2026-08-31).

    Les extensions executables de Windows autres que `.exe` valent le meme
    contournement : `git.cmd push --force` PASSAIT ici alors qu'il etait bloque sur
    la lignee VSCode3 (mesure du 2026-09-07, rejeu par le chemin de production).
    `.cmd`/`.bat`/`.ps1` sont donc retirees comme `.exe`. Le `\\` est normalise en
    `/` avant le basename pour ne pas dependre du `os.path` de la plateforme."""
    nom = tok.replace("\\", "/").rstrip("/")
    nom = nom.rsplit("/", 1)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if nom.endswith(ext):
            return nom[: -len(ext)]
    return nom


# --------------------------------------------------------------------------- #
# Indirections : la vraie commande est la CHARGE UTILE d'un argument
# --------------------------------------------------------------------------- #
# Repris de la lignee VSCode3 le 2026-09-08. Aucune de ces commandes ne commence
# par « git », donc aucune n'etait vue ici : mesure du 2026-09-07 par le chemin
# de production, `iex 'git push --force'`, `powershell -Command 'git checkout --
# f.txt'` et `git.cmd push --force` PASSAIENT au hub et sur VSCode1/2/4, et
# etaient bloques sur VSCode3. On re-analyse la charge utile comme une commande a
# part entiere, en bornant la recursion.
#
# Ces indirections ne remplacent PAS `_WRAPPERS` : `-EncodedCommand` demande un
# decodage base64 qu'aucune re-analyse token-par-token ne peut faire, et
# `_WRAPPERS` couvre a l'inverse `env`/`xargs`/`sudo`/`&`, absents ici.
_INDIRECTION = frozenset({"iex", "invoke-expression", "eval", "exec"})
_SHELL_RUNNERS = frozenset({"powershell", "pwsh", "cmd", "bash", "sh", "zsh"})
_RUNNER_FLAGS = frozenset({"-c", "-command", "/c", "/k"})
_ENCODED_FLAGS = frozenset({"-encodedcommand", "-enc", "-ec"})


def _commande_interne(tete: str, args: list):
    """La commande reellement executee par une indirection, ou None."""
    if tete in _INDIRECTION:
        return " ".join(args) if args else None
    if tete not in _SHELL_RUNNERS:
        return None
    for i, a in enumerate(args):
        al = a.lower()
        if tete in ("powershell", "pwsh") and al in _ENCODED_FLAGS and i + 1 < len(args):
            try:
                return base64.b64decode(args[i + 1]).decode("utf-16-le", "replace")
            except Exception:
                return None   # pas du base64 valide : on ne devine pas
        if al in _RUNNER_FLAGS and i + 1 < len(args):
            return " ".join(args[i + 1 :])
    return None


# --------------------------------------------------------------------------- #
# Lecture d'un chemin protege
# --------------------------------------------------------------------------- #
# Repris de la lignee VSCode3 le 2026-09-08. Miroir des deny rules `Read(...)` de
# .claude/settings.json, qui ne couvrent QUE l'outil Read : mesure du 2026-09-01,
# `cat .env`, `Get-Content .env` et `type config/credentials.json` sortaient sans
# aucune resistance cote shell.
_READERS = frozenset({"cat", "type", "more", "less", "head", "tail", "nl", "od", "xxd",
                      "strings", "get-content", "gc", "select-string", "sls", "findstr",
                      "grep", "rg", "copy", "cp", "move", "mv", "curl", "wget"})
_INTERPRETERS = frozenset({"python", "python3", "py", "node", "perl", "ruby", "deno"})


def _est_un_chemin_protege(token: str) -> bool:
    # `curl -d @.env` / `curl -d@.env` : la syntaxe « @fichier » des clients HTTP
    # est un vecteur d'exfiltration direct, et le chemin n'y est pas un argument
    # nu. On teste donc aussi ce qui suit le « @ ».
    if token.startswith("@"):
        token = token[1:]
    elif token.startswith("-") and "@" in token:
        token = token.split("@", 1)[1]
    p = token.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lower()
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    if not parts:
        return False
    # basename exact : bloque .env, ./.env, foo/.env — jamais .env.example
    if parts[-1] == ".env":
        return True
    if "secrets" in parts:
        return True
    return p.endswith("config/credentials.json")


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
    apres_tete = tokens[start + 1 :]

    # 1. Indirection : la vraie commande est la CHARGE UTILE d'un argument
    #    (`iex '...'`, `powershell -Command '...'`, `-EncodedCommand <base64>`,
    #    `cmd /c ...`). On la re-analyse comme une commande a part entiere.
    if profondeur < _MAX_DEPTH:
        interne = _commande_interne(tete, apres_tete)
        if interne:
            raison = _analyser(_strip_heredocs(interne), profondeur + 1)
            if raison:
                return raison

    # 2. `eval "git push --force"` : la vraie commande est dans les arguments du wrapper.
    # Profondeur bornee (fail-open assume : on ne devine pas au-dela).
    if tete in _WRAPPERS:
        if profondeur >= _MAX_DEPTH:
            return None
        for candidat in [*apres_tete, " ".join(apres_tete)]:
            raison = _analyser(candidat, profondeur + 1)
            if raison:
                return raison
        return None

    # 3. Lecture d'un chemin protege par un lecteur ou un interpreteur.
    if tete in _READERS or tete in _INTERPRETERS:
        vises = [t for t in apres_tete if _est_un_chemin_protege(t)]
        if not vises and tete in _INTERPRETERS:
            # `python -c "print(open('.env').read())"` : le chemin est DANS la
            # charge utile, pas dans un argument a lui seul.
            for cand in re.findall(r"""['\"]([^'\"]+)['\"]""", " ".join(apres_tete)):
                if _est_un_chemin_protege(cand):
                    vises = [cand]
                    break
        if vises:
            return (
                f"Lecture d'un fichier protege ({vises[0]}) bloquee par un hook projet — "
                "meme perimetre que les deny rules Read(...) de .claude/settings.json, "
                "qui ne couvrent pas le shell. Confirmez explicitement avec l'utilisateur "
                "si cette lecture est legitime."
            )

    # 4. git destructif.
    if tete != "git":
        return None
    rest = lower[start + 1 :]

    if "push" in rest:
        has_force = any(t in ("--force", "-f") or t.startswith("--force=") for t in rest)
        has_lease = any(
            t == "--force-with-lease" or t.startswith("--force-with-lease=") for t in rest
        )
        # La forme LA PLUS COURANTE du push force ne contient pas le mot `--force` :
        # `git push origin +main` force la mise a jour. Reproduit sur un remote
        # jetable : `git push origin master` refuse (non fast-forward), `+master`
        # accepte avec « (forced update) ».
        has_plus = any(t.startswith("+") and len(t) > 1 for t in rest)
        if has_plus and not has_lease:
            return (
                "git push avec une refspec forcee (« + » devant la ref) est bloque par "
                "un hook projet : c'est un push force qui ne dit pas son nom. Utilisez "
                "--force-with-lease si necessaire, ou confirmez explicitement avec "
                "l'utilisateur."
            )
        if has_force and not has_lease:
            return (
                "git push --force (sans --force-with-lease) est bloqué par un hook projet. "
                "Utilisez --force-with-lease si nécessaire, ou confirmez explicitement avec "
                "l'utilisateur avant de contourner ce garde-fou."
            )

    # git accepte tout PREFIXE NON AMBIGU d une option longue : `--har`, `--ha` et
    # meme `--h` font un reset dur complet — verifie par execution, le travail non
    # commite est bien detruit. Le test litteral `"--hard" in rest` les laissait tous
    # passer. On borne a 3 caracteres (`--h`), la plus courte forme que git accepte
    # ici, et on exige que ce soit un prefixe de `--hard` : `--hi` n est pas bloque,
    # un garde-fou qui crie a tort finit desarme.
    def _vaut_hard(t: str) -> bool:
        return t.startswith("--h") and "--hard".startswith(t)

    if "reset" in rest and any(_vaut_hard(t) for t in rest):
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
# Ajouté le 2026-09-02 (VSCode2), propagé à toute la flotte le 2026-09-07 sur un incident réel :
# un sous-agent de revue, dont le mandat dit pourtant qu'il « ne corrige rien », a joué
# `git checkout --` sur deux templates pour mesurer le code d'avant. Les correctifs non
# commités de la session appelante ont disparu du disque. Ils ont pu être reconstruits
# depuis des copies hors dépôt, mais rien dans le dispositif ne s'y opposait : le
# garde-fou ne connaissait que `push --force` et `reset --hard`, deux commandes qui
# touchent l'HISTORIQUE, alors que le travail perdu ce jour-là était dans l'ARBRE. C'est
# la classe entière qu'il fallait couvrir, pas le cas vu.
#
# Le remède n'est pas d'interdire de mesurer le code d'avant : c'est un besoin légitime
# d'une revue. Le message dit donc comment le faire sans rien détruire
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


# Options GLOBALES de `git` (avant la sous-commande) qui prennent leur valeur
# dans un TOKEN SEPARE : `git -C . checkout ...`, `git -c core.pager=cat push
# ...`. Sans les reconnaitre, chercher « le premier token qui ne commence pas
# par - » prenait la VALEUR pour la sous-commande, ce qui desarmait tout le
# volet arbre -- reproduit par revue adversariale le 2026-09-07 :
# `git -C . checkout -- f.txt` passait, alors que `-C` est precisement la
# forme employee pour agir sur un depot tiers, le metier de ce hub (R2/R3).
# Deja en minuscules ici : `rest` est lowercased avant d'atteindre cette
# fonction, donc `-C` (chemin) et `-c` (config) y sont indiscernables --
# les deux prennent un token separe, le traitement est donc identique.
_GLOBALES_AVEC_VALEUR = frozenset({
    "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--super-prefix", "--config-env",
})


def _sous_commande_index(rest: list):
    """Index de la vraie sous-commande dans `rest` (tokens apres `git`, deja
    en minuscules), en sautant les options globales et leur valeur quand
    elle est un token separe. None si aucune sous-commande trouvee."""
    i = 0
    n = len(rest)
    while i < n:
        t = rest[i]
        if not t.startswith("-"):
            return i
        if "=" not in t and t in _GLOBALES_AVEC_VALEUR and i + 1 < n:
            i += 2  # saute le drapeau ET sa valeur (token separe)
            continue
        i += 1
    return None


def _blocked_worktree(tokens_apres_git: list, rest: list):
    idx = _sous_commande_index(rest)
    if idx is None:
        return None
    sous_commande = rest[idx]
    args = tokens_apres_git[idx + 1 :]  # tokens ORIGINAUX (casse preservee), apres la sous-commande
    bas = rest[idx + 1 :]               # memes tokens, en minuscules

    if sous_commande == "checkout":
        # `-f`/`--force` ecrase TOUT l'arbre suivi, la forme la plus
        # destructive de la commande -- ne depend d'aucun chemin cite.
        # Reproduit (revue 2026-09-07) : `git checkout -f master` passait.
        if any(t in ("-f", "--force") for t in bas):
            return (
                "git checkout -f/--force est bloqué par un hook projet : il ÉCRASE les "
                "modifications non commitées de TOUS les fichiers suivis, pas seulement "
                "d'un chemin. " + _ALTERNATIVE
            )
        # `git checkout … -- <chemin>` : tout ce qui suit `--` est un chemin, la
        # forme la plus explicite et la plus destructive.
        if "--" in bas:
            i = bas.index("--")
            if args[i + 1 :]:
                return (
                    "git checkout -- <chemin> est bloqué par un hook projet : il ÉCRASE "
                    "les modifications non commitées du fichier, sans copie de secours. "
                    + _ALTERNATIVE
                )
        if any(t in _CREATION_DE_BRANCHE for t in bas):
            return None  # création/bascule de branche : rien de l'arbre n'est perdu
        for t in args:
            if not t.startswith("-") and _est_un_chemin_du_depot(t):
                return (
                    f"git checkout <chemin> est bloqué par un hook projet : `{t}` existe "
                    "sur le disque, ses modifications non commitées seraient écrasées. "
                    "Pour changer de branche, le nom ne doit pas être celui d'un fichier "
                    "existant. " + _ALTERNATIVE
                )
        return None

    if sous_commande == "switch":
        # Forme moderne de `checkout <branche>` : `--discard-changes` (et
        # `-f`/`--force`, alias) ecrase l'arbre exactement comme
        # `checkout -f`. Reproduit (revue 2026-09-07) : passait sans ce bloc.
        if any(t in ("--discard-changes", "-f", "--force") for t in bas):
            return (
                "git switch --discard-changes est bloqué par un hook projet : il ÉCRASE "
                "les modifications non commitées, comme git checkout -f. " + _ALTERNATIVE
            )
        return None

    if sous_commande == "restore":
        # `-h`/`--help` n'ecrase rien : uniquement de la lecture.
        if "-h" in bas or "--help" in bas:
            return None
        # `git restore --staged <chemin>` ne touche QUE l'index : il désindexe,
        # il ne détruit rien. Il reste donc autorisé — sauf s'il est cumulé avec
        # `--worktree`, qui lui écrase bien le fichier. `-S`/`-W` sont les
        # formes courtes de git, EN MAJUSCULES (`-s` minuscule est un drapeau
        # different, --source) : les comparer a `bas` (deja en minuscules)
        # les rendait invisibles par construction -- reproduit (revue
        # 2026-09-07) : `git restore -S f.txt` bloquait a tort, `git restore
        # --staged -W f.txt` passait a tort. Compares ici aux tokens
        # ORIGINAUX (`args`), casse preservee.
        a_staged = "--staged" in bas or "-S" in args
        a_worktree = "--worktree" in bas or "-W" in args
        if a_staged and not a_worktree:
            return None
        return (
            "git restore <chemin> est bloqué par un hook projet : il ÉCRASE les "
            "modifications non commitées du fichier. `git restore --staged` (qui ne "
            "touche que l'index) reste autorisé. " + _ALTERNATIVE
        )

    if sous_commande == "clean":
        # `-n`/`--dry-run` ne supprime rien -- y compris cumule avec `-f`
        # dans un SEUL token groupe (`git clean -nfd`, precisement la forme
        # que le message de refus recommande pour lister avant de
        # confirmer) : verifie via _flags_courts_groupes, pas une egalite
        # de token entiere -- `"-n" in bas` ne matchait jamais "-nfd".
        # Reproduit (revue 2026-09-07) : `git clean -nfd` bloquait a tort.
        if "--dry-run" in bas or "n" in _flags_courts_groupes(args):
            return None
        if "--force" in bas or "f" in _flags_courts_groupes(args):
            return (
                "git clean -f est bloqué par un hook projet : il SUPPRIME les fichiers "
                "non suivis, donc tout fichier neuf pas encore ajouté (un test qu'on "
                "vient d'écrire, par exemple). Listez-les d'abord avec `git clean -n`, "
                "puis confirmez avec l'utilisateur."
            )
        return None

    if sous_commande == "stash":
        # Seul le PREMIER token qui n'est pas un drapeau, juste apres
        # `stash`, est la vraie sous-sous-commande -- chercher "drop"/
        # "clear" n'importe ou dans `bas` bloquait a tort un message qui
        # contient ce mot (`git stash push -m "clear le cache"`, un seul
        # token vu shlex). Reproduit (revue 2026-09-07, M2).
        stash_sous_commande = next((t for t in bas if not t.startswith("-")), None)
        if stash_sous_commande in ("drop", "clear"):
            return (
                "git stash drop/clear est bloqué par un hook projet : la remise ainsi "
                "supprimée n'est plus récupérable par aucune commande ordinaire. "
                "Confirmez avec l'utilisateur."
            )
        return None

    if sous_commande == "rm":
        # `git rm -f <chemin>` supprime le fichier du disque ET de l'index,
        # y compris ses modifications non commitees, sans copie de secours
        # -- meme classe que `clean -f`, jamais couverte (revue 2026-09-07,
        # M3). Sans -f, git refuse deja de lui-meme un fichier modifie.
        if "--force" in bas or "f" in _flags_courts_groupes(args):
            return (
                "git rm -f est bloqué par un hook projet : il SUPPRIME le fichier du "
                "disque ET de l'index, y compris ses modifications non commitées. "
                "Confirmez avec l'utilisateur, ou `git rm --cached` pour ne toucher que "
                "l'index."
            )
        return None

    if sous_commande == "worktree":
        # `git worktree remove --force` supprime un arbre de travail entier
        # -- pas un fichier, un arbre -- y compris tout travail non commite
        # qu'il contient. Classe differente, jamais couverte (revue
        # 2026-09-07, M3).
        sous_sous = next((t for t in bas if not t.startswith("-")), None)
        if sous_sous == "remove" and ("--force" in bas or "f" in _flags_courts_groupes(args)):
            return (
                "git worktree remove --force est bloqué par un hook projet : il SUPPRIME "
                "tout un arbre de travail, y compris son travail non commité. Confirmez "
                "avec l'utilisateur."
            )
        return None

    return None



# --- bounded stdin read (anthropics/claude-code#87289) -------------------------
try:
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    from _stdin_borne import lire_stdin_borne as _lsb
except Exception:  # noqa: BLE001 - exported without the helper: still bounded
    def _lsb(delai=5.0, flux=None):
        import threading
        f = flux if flux is not None else sys.stdin
        boite = {}

        def _c():
            try:
                boite["v"] = f.read()
            except BaseException:  # noqa: BLE001
                boite["v"] = None
        t = threading.Thread(target=_c, daemon=True)
        t.start()
        t.join(delai)
        return None if t.is_alive() else boite.get("v")


def _stdin_borne(delai=5.0):
    """Bounded stdin read: the payload, or None on timeout/error — the hook decides
    (guard: fail-closed refusal; reminder: its existing fail-open path).
    A hook may set a module-level ``_FLUX_STDIN`` (e.g. a raw fd 0 reader)."""
    return _lsb(delai, globals().get("_FLUX_STDIN"))


def _stdin_ou_refus(delai=5.0):
    """Guard hook: no stdin within the bound is a prudent refusal (fail-closed).

    Exit 2 blocks the tool call and shows stderr to Claude. ``os._exit`` skips
    interpreter shutdown, which can crash (0xC0000005) while the reader thread
    is still blocked — a crash code other than 2 would be a silent fail-open.
    """
    v = _stdin_borne(delai)
    if v is None:
        _refus_prudent(f"stdin non recu en {delai:g} s", "delai", delai)
    return v

def _journal_refus(motif, delai):
    """One JSON line per refusal in ``<hooks>/../supervision/refus_stdin.jsonl``
    (review 4, Dana: measure before tuning the bound). ``CLAUDE_REFUS_STDIN_LOG``
    redirects it (tests). Bounded (1 MB) and fail-silent: never changes the exit."""
    try:
        _os = __import__("os")
        _dt = __import__("datetime")
        chemin = _os.environ.get("CLAUDE_REFUS_STDIN_LOG") or _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "..", "supervision", "refus_stdin.jsonl")
        if _os.path.exists(chemin) and _os.path.getsize(chemin) > 1_000_000:
            return
        ligne = __import__("json").dumps({
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "hook": _os.path.splitext(_os.path.basename(__file__))[0],
            "motif": motif, "delai_s": float(delai)})
        with open(chemin, "a", encoding="utf-8") as fh:
            fh.write(ligne + "\n")
    except Exception:  # noqa: BLE001
        pass

def _refus_prudent(cause, motif, delai=5.0):
    _os = __import__("os")
    nom = _os.path.splitext(_os.path.basename(__file__))[0]
    try:  # UTF-8 bytes on fd 2: the harness reads UTF-8, a cp1252 dash is mojibake
        _journal_refus(motif, delai)
        _os.write(2, f"{nom}: {cause} — refus prudent, relancer la commande\n".encode())
    finally:
        _os._exit(2)

def _json_ou_refus(delai=5.0):
    """Guard hook: an empty, non-JSON or non-object payload is refused too
    (review 4, Vex) — only a VALID payload reaches the guard's own fail-open."""
    brut = _stdin_ou_refus(delai)
    try:
        if isinstance(brut, bytes):
            brut = brut.decode("utf-8", "replace")
        data = __import__("json").loads(brut)
    except Exception:  # noqa: BLE001
        data = None
    if not isinstance(data, dict):
        _refus_prudent("entree illisible", "illisible", delai)
    return data


def main() -> None:
    try:
        data = _json_ou_refus()
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
