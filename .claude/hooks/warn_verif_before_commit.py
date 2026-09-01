r"""PreToolUse hook (Bash/PowerShell) — soft, NON-blocking reminder that warns
when application code (`app/**`) is about to be committed without a real
verification having run in the current session.

Provenance : proposition du constat #1 du superviseur d'agents (étage 2),
arbitrée puis appliquée le 2026-07-21. Le diagnostic (voir
`docs/wiki/technical/agents-supervision.md`) montrait que la vérif réelle de
fin d'incrément (`npm test` + rendu réel via `/revue-increment` / `pptx-verify`)
était systématiquement sautée : `revue-increment` n=0 sur 14 sessions,
`pptx-verify` figé à 1 usage, alors que du code continuait d'être commité. Le
rappel SessionStart passif (`remind_revue_increment.py`) ne suffit pas — rien
n'oblige à le suivre. Ce hook déplace le rappel AU BON INSTANT : le commit.

Conception (delta assumé vs. la proposition brute) :
- **Non bloquant** : émet un `systemMessage` (visible utilisateur) + un
  `additionalContext` (visible modèle si supporté), SANS `permissionDecision`.
  Le commit passe — on avertit, on ne bloque pas (cf. guard_destructive_git.py,
  lui, bloque : ce sont deux niveaux de sévérité volontairement distincts).
- **Ciblé `app/**` uniquement**, PAS `docs/wiki/**` : le wiki est régénéré
  automatiquement par le scan (dashboard, index) — l'y inclure noierait le
  signal sous des commits de doc auto-générée. La vérif « réelle » (tests +
  rendu) concerne le code applicatif.
- **Détection de trace de vérif = vraie exécution d'outil**, pas une simple
  mention : on parse le transcript de la session (tool_use Bash `npm test`… /
  Skill `pptx-verify`/`revue-increment`), même structure que
  scan_transcripts.py — sinon toute session qui *parle* de vérif se
  faux-négativerait.
- **Fail-open partout** : toute erreur (parsing, git indisponible, transcript
  illisible, import) rend la main SANS avertir. Un bug ici ne doit jamais
  ajouter de friction ni bloquer un commit.

Le tokenizer shell robuste (heredocs, segments quote-safe) est réutilisé de
`guard_destructive_git.py` (même répertoire) pour ne pas diverger d'un second
parseur du même problème ; si l'import échoue, dégradation en silence.
"""
import json
import os
import re
import shlex
import subprocess
import sys

try:  # réutilise le tokenizer éprouvé du guard voisin ; sinon, dégrade en silence
    from guard_destructive_git import _strip_heredocs, _segments
except Exception:  # pragma: no cover - fail-open
    _strip_heredocs = None
    _segments = None

# Zone sous vérification : le code applicatif (tests + rendu réel). Volontairement
# PAS docs/wiki/ (généré par le scan) pour garder le signal haut.
_WATCHED_PREFIXES = ("app/",)

# Zone dispositif (constat superviseur `sync-canon` du 2026-07-29, arbitré) : le
# commit 5eb121b — propagation du canon, +23 lignes dans log_run.py, 0 test — a
# cassé un test-contrat existant, découvert seulement à la revue suivante. Les
# commits touchant ces chemins passent hors playbook dev-verifie (« c'est juste
# une sync ») : ce hook rappelle le gate minimal, les deux fichiers-contrat.
_DISPOSITIF_PREFIXES = (".claude/orchestration/", ".claude/supervision/", ".claude/hooks/")
_DISPOSITIF_TESTS = ("tests/test_agent_orchestration.py", "tests/test_agent_supervision.py",
                     "tests/test_hooks_discipline.py")

# Signaux d'une vraie exécution de vérif dans la session (commandes Bash / skills).
# Adapté à VSCode2 (pytest) — porté de VSCode1 le 2026-07-23 (finding pratique-revue).
_VERIF_BASH = ("pytest", "-m pytest", "python -m pytest", "ruff check")
_VERIF_SKILL = ("pptx-verify", "revue-increment")

_GIT_OPTS_WITH_VALUE = ("-C", "-c", "--git-dir", "--work-tree", "--namespace")


def _git_commit_flags(segment):
    """-> liste des tokens d'un `git commit` réel, ou None si le segment n'en est pas un."""
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return None  # quotes déséquilibrées, substitution… — on ne devine pas
    if not tokens:
        return None
    start = 0
    while start < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[start]):
        start += 1  # saute les affectations VAR=value en tête
    if start >= len(tokens) or tokens[start].lower() != "git":
        return None
    rest = tokens[start + 1:]
    # Sous-commande = premier token non-option (en sautant -C/-c <val> globaux).
    i = 0
    sub = None
    while i < len(rest):
        t = rest[i]
        if t.startswith("-"):
            i += 2 if t in _GIT_OPTS_WITH_VALUE else 1
            continue
        sub = t
        break
    if sub != "commit":
        return None
    if "--dry-run" in rest:
        return None  # ne crée pas de commit
    return rest


def _staged_watched(cwd, commit_flags):
    """Tous les fichiers qui seront réellement commités (le filtrage par zone se
    fait chez l'appelant), ou None si indéterminable."""
    def _run(args):
        try:
            r = subprocess.run(
                ["git"] + args, cwd=cwd or None,
                capture_output=True, text=True, timeout=8,
            )
        except Exception:
            return None
        if r.returncode != 0:
            return None
        return [ln.strip().replace("\\", "/") for ln in r.stdout.splitlines() if ln.strip()]

    files = _run(["diff", "--cached", "--name-only"])
    if files is None:
        return None
    # `git commit -a/--all` valide aussi les modifs de fichiers suivis non stagés :
    # les ajouter, sinon on manquerait le périmètre réel du commit.
    if any(f in ("-a", "--all") for f in commit_flags):
        unstaged = _run(["diff", "--name-only"])
        if unstaged:
            files = list(dict.fromkeys(files + unstaged))
    return files


def _iter_tool_uses(obj):
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if not isinstance(content, list):
        return
    for blk in content:
        if isinstance(blk, dict) and blk.get("type") == "tool_use":
            yield blk


def _session_tool_uses(transcript_path):
    """Générateur (name, input) des tool_use du transcript de session — mutualisé
    entre les deux détections de vérif ; itérable vide sur toute erreur (fail-open)."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return
    try:
        with open(transcript_path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue  # préfiltre octet bon marché (cf. scan_transcripts.py)
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                for blk in _iter_tool_uses(obj):
                    yield blk.get("name"), blk.get("input") or {}
    except Exception:
        return


def _verif_ran(transcript_path):
    """True si une vraie exécution de vérif est présente dans le transcript de session."""
    for name, inp in _session_tool_uses(transcript_path):
        # PowerShell est le shell PRIMAIRE de cet environnement : ne reconnaître
        # que Bash faisait crier « vérif non détectée » à un commit qui venait de
        # lancer pytest en PowerShell (2026-09-01, signalé par le hub, reproduit
        # par appel direct de `_verif_ran`). Un garde-fou qui crie au loup quand
        # la vérif A eu lieu finit ignoré — donc ignoré aussi le jour où il a
        # raison. Les deux outils exposent la commande sous la même clé.
        if name in ("Bash", "PowerShell"):
            cmd = (inp.get("command") or "").lower()
            if any(k in cmd for k in _VERIF_BASH):
                return True
        elif name == "Skill":
            if (inp.get("skill") or "").lower() in _VERIF_SKILL:
                return True
    return False


def _dispositif_verif_ran(transcript_path):
    """True si les fichiers-contrat du dispositif ont tourné cette session : un
    pytest ciblant `tests/test_agent_*`, ou une suite complète (pytest sans chemin
    `tests/...`, qui les inclut de fait)."""
    for name, inp in _session_tool_uses(transcript_path):
        # Même aveuglement PowerShell que `_verif_ran` ci-dessus, sur le chemin
        # frère — non signalé par le hub, trouvé en relisant les DEUX
        # détections : corriger l'occurrence nommée et laisser sa jumelle est
        # une leçon déjà payée sur ce dépôt.
        if name not in ("Bash", "PowerShell"):
            continue
        cmd = (inp.get("command") or "").lower().replace("\\", "/")
        if "pytest" not in cmd:
            continue
        if any(t in cmd for t in ("test_agent_orchestration", "test_agent_supervision",
                                  "test_hooks_discipline")):
            return True
        if "tests/" not in cmd:  # suite complète (`pytest -q`) : les inclut
            return True
    return False


_WARNING = (
    "⚠️ Vérif de fin d'incrément non détectée dans cette session : des fichiers "
    "app/ sont sur le point d'être commités sans trace de `pytest` ni de rendu "
    "réel (`/revue-increment` ou `pptx-verify`). Lancer la vérif RÉELLE avant de "
    "committer le code applicatif, ou confirmer que c'est volontaire. "
    "(Garde-fou projet non bloquant — constat superviseur #1.)"
)

_WARNING_DISPOSITIF = (
    "⚠️ Commit touchant le dispositif (.claude/orchestration|supervision|hooks) sans "
    "trace des fichiers-contrat cette session : lancer `pytest "
    + " ".join(_DISPOSITIF_TESTS) + " -q` (~1 min) avant de committer — un commit de "
    "sync canon sans test a déjà cassé la suite (5eb121b, constat superviseur "
    "sync-canon du 2026-07-29). Garde-fou non bloquant."
)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    strip = _strip_heredocs or (lambda s: s)
    segs = _segments(cmd) if _segments else [cmd]
    try:
        cmd = strip(cmd)
        segs = _segments(cmd) if _segments else [cmd]
    except Exception:
        return  # fail-open

    commit_flags = None
    for seg in segs:
        commit_flags = _git_commit_flags(seg)
        if commit_flags is not None:
            break
    if commit_flags is None:
        return  # pas un git commit

    files = _staged_watched(data.get("cwd"), commit_flags)
    if not files:
        return  # rien à committer (ou git indéterminable) — silence
    watched_app = [f for f in files if f.startswith(_WATCHED_PREFIXES)]
    watched_disp = [f for f in files if f.startswith(_DISPOSITIF_PREFIXES)]
    if not watched_app and not watched_disp:
        return

    transcript = data.get("transcript_path")
    warnings = []
    if watched_app and not _verif_ran(transcript):
        warnings.append(_WARNING)
    if watched_disp and not _dispositif_verif_ran(transcript):
        warnings.append(_WARNING_DISPOSITIF)
    if not warnings:
        return  # les vérifs attendues ont tourné cette session — pas de rappel

    message = "\n\n".join(warnings)
    print(json.dumps({
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        },
    }))


if __name__ == "__main__":
    main()
