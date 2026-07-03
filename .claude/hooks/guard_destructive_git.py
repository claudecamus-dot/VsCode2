"""PreToolUse hook (Bash/PowerShell) — deterministic backstop blocking
`git push --force` (without `--force-with-lease`) and `git reset --hard`.

Complements the git safety protocol already stated in prompt instructions
with something that can't be talked past by a persuasive-sounding reason in
context — see the "restructuration Claude Code" checklist item on hooks for
critical guardrails. Fails open (any parsing/edge-case error -> allow) so a
bug here never blocks unrelated shell usage.

A naive substring search over the whole command string also matches those
words when they appear as plain DATA rather than a command to run — e.g. a
commit message *describing* this hook, passed via `git commit -F - <<'EOF'
... EOF` (the project's own documented convention for commit messages).
Two passes avoid that: strip heredoc bodies first (they're always data,
never a command to execute), then only match `git push`/`git reset` at the
START of a shell segment (splitting on &&, ||, ;, |, newline — but not
inside quotes, so descriptive text containing those characters in a quoted
string isn't mistaken for a command boundary either).
"""
import json
import re
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
    """Split on &&, ||, ;, |, newline — but not when inside '...' or "...". """
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
        if c in (";", "|", "\n"):
            segs.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segs.append("".join(buf))
    return [s.strip() for s in segs]


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    cmd = _strip_heredocs(cmd)

    blocked = None
    for seg in _segments(cmd):
        if re.match(r"^git\s+push\b", seg) and (
            re.search(r"--force(?!-with-lease)\b", seg) or re.search(r"(?<!\S)-f\b", seg)
        ):
            blocked = (
                "git push --force (sans --force-with-lease) est bloqué par un hook projet. "
                "Utilisez --force-with-lease si nécessaire, ou confirmez explicitement avec "
                "l'utilisateur avant de contourner ce garde-fou."
            )
            break
        if re.match(r"^git\s+reset\b", seg) and re.search(r"--hard\b", seg):
            blocked = (
                "git reset --hard est bloqué par un hook projet (perte de modifications non "
                "commitées). Utilisez git stash, ou confirmez explicitement avec l'utilisateur."
            )
            break

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
