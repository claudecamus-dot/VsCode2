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


def _blocked_reason(segment: str):
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

    if start >= len(lower) or lower[start] != "git":
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

    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    cmd = _strip_heredocs(cmd)

    blocked = None
    for seg in _segments(cmd):
        blocked = _blocked_reason(seg)
        if blocked:
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
