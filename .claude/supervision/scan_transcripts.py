"""Superviseur d'agents — étage 1 (incrément A) : collecte déterministe, 0 token LLM.

Scanne incrémentalement les transcripts JSONL du projet (~/.claude/projects/<slug>/*.jsonl),
agrège l'usage réel des skills et sous-agents (état cumulé dans state.json, offsets par
fichier pour ne relire que le nouveau), puis régénère :
  - docs/wiki/technical/agents-supervision.md  (tableau de bord + TODO agents)
  - la section entre marqueurs TODO-AGENTS de docs/wiki/index.md
  - la section entre marqueurs TODO-AGENTS-HTML de docs/wiki.html (page rendue standalone)

Lancé automatiquement par le hook SessionStart (sortie : 1 ligne, jamais bloquant).
Usage manuel : py .claude/supervision/scan_transcripts.py [--full]
  --full : ignore l'état incrémental et rescanne tout l'historique.

Env (surcharges, utilisées par les tests) : AGENT_SUPERVISION_TRANSCRIPTS,
AGENT_SUPERVISION_STATE, AGENT_SUPERVISION_WIKI_PAGE, AGENT_SUPERVISION_WIKI_INDEX.
Conception : docs/reflexions/agent-superviseur.md.
"""
import datetime as dt
import glob
import json
import os
import re
import sys

SUP_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(SUP_DIR))
STATE_PATH = os.environ.get("AGENT_SUPERVISION_STATE") or os.path.join(SUP_DIR, "state.json")
WIKI_PAGE = os.environ.get("AGENT_SUPERVISION_WIKI_PAGE") or os.path.join(
    REPO, "docs", "wiki", "technical", "agents-supervision.md"
)
WIKI_INDEX = os.environ.get("AGENT_SUPERVISION_WIKI_INDEX") or os.path.join(
    REPO, "docs", "wiki", "index.md"
)
WIKI_HTML = os.environ.get("AGENT_SUPERVISION_WIKI_HTML") or os.path.join(
    REPO, "docs", "wiki.html"
)
DORMANT_DAYS = 30
MARK_START = "<!-- TODO-AGENTS:START"
MARK_END = "<!-- TODO-AGENTS:END -->"
HTML_MARK_START = "<!-- TODO-AGENTS-HTML:START"
HTML_MARK_END = "<!-- TODO-AGENTS-HTML:END -->"


def transcript_dir() -> str:
    override = os.environ.get("AGENT_SUPERVISION_TRANSCRIPTS")
    if override:
        return override
    path = os.path.abspath(REPO)
    if len(path) >= 2 and path[1] == ":":
        path = path[0].lower() + path[1:]
    slug = re.sub(r"[\\/:.]", "-", path)
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    candidate = os.path.join(base, slug)
    if os.path.isdir(candidate):
        return candidate
    if os.path.isdir(base):  # tolérance à la casse (C: vs c:)
        for name in os.listdir(base):
            if name.lower() == slug.lower():
                return os.path.join(base, name)
    return candidate


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


def read_new_lines(path: str, offset: int):
    """Lit les lignes complètes ajoutées depuis offset ; ne consomme jamais une ligne partielle."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if size < offset:  # fichier tronqué/remplacé : repartir de zéro
        offset = 0
    if size == offset:
        return [], offset
    with open(path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset
    consumed = chunk[: end + 1]
    return [line for line in consumed.split(b"\n") if line.strip()], offset + len(consumed)


def record(agg: dict, key: str, ts: str) -> None:
    entry = agg.setdefault(key, {"n": 0, "first": ts, "last": ts})
    entry["n"] += 1
    if ts:
        if not entry["first"] or ts < entry["first"]:
            entry["first"] = ts
        if not entry["last"] or ts > entry["last"]:
            entry["last"] = ts


def scan(state: dict) -> int:
    tdir = transcript_dir()
    files_state = state.setdefault("files", {})
    skills = state.setdefault("skills", {})
    subagents = state.setdefault("subagents", {})
    new_events = 0
    if not os.path.isdir(tdir):
        state["transcript_dir_missing"] = tdir
        return 0
    state.pop("transcript_dir_missing", None)
    for path in sorted(glob.glob(os.path.join(tdir, "*.jsonl"))):
        name = os.path.basename(path)
        offset = files_state.get(name, {}).get("offset", 0)
        lines, new_offset = read_new_lines(path, offset)
        for raw in lines:
            # Préfiltre octets : ne parser en JSON que les lignes candidates.
            if b'"Skill"' not in raw and b'"subagent_type"' not in raw:
                continue
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            ts = obj.get("timestamp") or ""
            content = (obj.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for blk in content:
                if not (isinstance(blk, dict) and blk.get("type") == "tool_use"):
                    continue
                tool_input = blk.get("input") or {}
                if blk.get("name") == "Skill" and tool_input.get("skill"):
                    record(skills, str(tool_input["skill"]), ts)
                    new_events += 1
                elif blk.get("name") in ("Agent", "Task"):
                    record(subagents, str(tool_input.get("subagent_type") or "(defaut)"), ts)
                    new_events += 1
        files_state[name] = {"offset": new_offset}
    state["last_scan"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return new_events


def installed_skills() -> dict:
    """{nom_skill: famille} — projet (.claude/skills), BMAD (bmad-*), global (~/.claude/skills)."""
    fam = {}
    for d in sorted(glob.glob(os.path.join(REPO, ".claude", "skills", "*"))):
        if os.path.isdir(d):
            name = os.path.basename(d)
            fam[name] = "BMAD" if name.startswith("bmad-") else "projet"
    for d in sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "skills", "*"))):
        if os.path.isdir(d):
            fam.setdefault(os.path.basename(d), "global")
    return fam


def days_since(ts: str):
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = dt.datetime.now(t.tzinfo) if t.tzinfo else dt.datetime.now()
    return (now - t).days


def build_todos(skills: dict, fam: dict) -> list:
    todos = []
    bmad = [k for k, v in fam.items() if v == "BMAD"]
    bmad_unused = [k for k in bmad if k not in skills]
    if bmad and bmad_unused:
        if len(bmad_unused) == len(bmad):
            todos.append(
                f"**Trier les skills BMAD** : {len(bmad)} installés, 0 invocation à ce jour — "
                "décider lesquels garder, customiser ou désinstaller."
            )
        else:
            todos.append(
                f"**Élaguer les skills BMAD** : {len(bmad_unused)}/{len(bmad)} jamais invoqués — "
                "confirmer l'utilité des non-utilisés."
            )
    proj_unused = sorted(k for k, v in fam.items() if v == "projet" and k not in skills)
    if "revue-increment" in proj_unused:
        proj_unused.remove("revue-increment")
        todos.append(
            "**`revue-increment` jamais invoquée** malgré le rappel SessionStart à chaque session — "
            "revoir son déclencheur (l'ancrer au flux de commit ?) ou la simplifier."
        )
    if proj_unused:
        todos.append(
            "**Skills projet sans usage** : "
            + ", ".join(f"`{s}`" for s in proj_unused)
            + " — vérifier pertinence et déclencheurs."
        )
    dormant = sorted(
        k
        for k, e in skills.items()
        if (lambda d: d is not None and d > DORMANT_DAYS)(days_since(e.get("last", "")))
    )
    if dormant:
        todos.append(
            f"**Skills en sommeil (>{DORMANT_DAYS} j sans usage)** : "
            + ", ".join(f"`{s}`" for s in dormant)
            + "."
        )
    return todos[:5]


def _fmt_date(ts: str) -> str:
    return ts[:10] if ts else "?"


def _usage_table(agg: dict, fam: dict = None) -> list:
    lines = []
    if fam is not None:
        lines.append("| Skill | Famille | Invocations | Première | Dernière |")
        lines.append("| --- | --- | --- | --- | --- |")
    else:
        lines.append("| Sous-agent | Lancements | Premier | Dernier |")
        lines.append("| --- | --- | --- | --- |")
    for name, e in sorted(agg.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        if fam is not None:
            family = fam.get(name, "(builtin/session)")
            lines.append(
                f"| `{name}` | {family} | {e['n']} | {_fmt_date(e.get('first', ''))} | {_fmt_date(e.get('last', ''))} |"
            )
        else:
            lines.append(
                f"| `{name}` | {e['n']} | {_fmt_date(e.get('first', ''))} | {_fmt_date(e.get('last', ''))} |"
            )
    if len(lines) == 2:
        lines.append("| _(aucun)_ |" + " |" * (3 if fam is not None else 2))
    return lines


def build_page(state: dict, fam: dict, todos: list) -> str:
    skills = state.get("skills", {})
    subagents = state.get("subagents", {})
    nb_files = len(state.get("files", {}))
    total_skill = sum(e["n"] for e in skills.values())
    total_sub = sum(e["n"] for e in subagents.values())
    L = [
        "---",
        f"updated: {dt.date.today().isoformat()}",
        "generated-by: .claude/supervision/scan_transcripts.py (superviseur d'agents, étage 1)",
        "---",
        "",
        "# Supervision des agents — tableau de bord d'usage",
        "",
        "> ⚠️ **Page générée automatiquement** (hook SessionStart → `.claude/supervision/scan_transcripts.py`).",
        "> **Ne pas éditer à la main** — toute modification serait écrasée au prochain scan.",
        "> Conception et phasage : [../../reflexions/agent-superviseur.md](../../reflexions/agent-superviseur.md).",
        "",
        f"Dernier scan : {state.get('last_scan', '?')} · **{nb_files} sessions** (transcripts) · "
        f"**{total_skill}** invocations de skills · **{total_sub}** lancements de sous-agents.",
        "",
        "## Skills — usage réel",
        "",
    ]
    L += _usage_table(skills, fam)
    L += ["", "## Sous-agents", ""]
    L += _usage_table(subagents)
    L += ["", "## Jamais utilisés", ""]
    unused_by_family = {}
    for name, family in fam.items():
        if name not in skills:
            unused_by_family.setdefault(family, []).append(name)
    if not unused_by_family:
        L.append("_(tous les skills installés ont déjà été invoqués)_")
    for family in ("projet", "BMAD", "global"):
        names = sorted(unused_by_family.get(family, []))
        if not names:
            continue
        total_family = sum(1 for v in fam.values() if v == family)
        L.append(f"**{family}** — {len(names)}/{total_family} jamais invoqués :")
        L.append("")
        if len(names) > 8:
            L.append("<details><summary>Voir la liste</summary>")
            L.append("")
            L.append(", ".join(f"`{n}`" for n in names))
            L.append("")
            L.append("</details>")
        else:
            L.append(", ".join(f"`{n}`" for n in names))
        L.append("")
    L += ["## TODO agents (constats automatiques)", ""]
    if todos:
        L += [f"{i}. {t}" for i, t in enumerate(todos, 1)]
    else:
        L.append("_(aucun constat — rien à signaler sur les données actuelles)_")
    L += [
        "",
        "---",
        "",
        "_Étage 2 (diagnostic qualitatif LLM : KO répétés, efficacité, challenge des agents) : "
        "incrément B, pas encore construit — voir la réflexion._",
        "",
    ]
    return "\n".join(L)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_inline(s: str) -> str:
    """Convertit le gras/code markdown des libellés TODO en HTML (le reste est échappé)."""
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def _html_usage_rows(agg: dict, fam: dict = None) -> str:
    rows = []
    for name, e in sorted(agg.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        cells = [f"<td><code>{_esc(name)}</code></td>"]
        if fam is not None:
            cells.append(f"<td>{_esc(fam.get(name, '(builtin/session)'))}</td>")
        cells += [
            f"<td>{e['n']}</td>",
            f"<td>{_esc(_fmt_date(e.get('first', '')))}</td>",
            f"<td>{_esc(_fmt_date(e.get('last', '')))}</td>",
        ]
        rows.append("            <tr>" + "".join(cells) + "</tr>")
    if not rows:
        span = 5 if fam is not None else 4
        rows.append(f'            <tr><td colspan="{span}"><em>(aucun)</em></td></tr>')
    return "\n".join(rows)


def build_html_section(state: dict, fam: dict, todos: list) -> str:
    skills = state.get("skills", {})
    subagents = state.get("subagents", {})
    nb_files = len(state.get("files", {}))
    total_skill = sum(e["n"] for e in skills.values())
    total_sub = sum(e["n"] for e in subagents.values())
    today = dt.date.today().isoformat()
    unused_by_family = {}
    for name, family in fam.items():
        if name not in skills:
            unused_by_family.setdefault(family, []).append(name)
    unused_html = []
    for family in ("projet", "BMAD", "global"):
        names = sorted(unused_by_family.get(family, []))
        if not names:
            continue
        total_family = sum(1 for v in fam.values() if v == family)
        listing = ", ".join(f"<code>{_esc(n)}</code>" for n in names)
        if len(names) > 8:
            listing = f"<details><summary>Voir la liste ({len(names)})</summary><p>{listing}</p></details>"
        unused_html.append(
            f"      <p><strong>{family}</strong> — {len(names)}/{total_family} jamais invoqués : {listing}</p>"
        )
    todo_html = []
    for t in todos:
        todo_html.append(
            '      <div class="critical">\n'
            f"        <p>{_md_inline(t)}</p>\n"
            '        <span class="tag tag-confirme">CONFIRMÉ</span>\n'
            f'        <div class="tag-source">scan_transcripts.py · {today} · transcripts de session</div>\n'
            "      </div>"
        )
    if not todo_html:
        todo_html.append("      <p><em>(aucun constat — rien à signaler sur les données actuelles)</em></p>")
    return f"""
    <section class="doc" id="agents-supervision">
      <p class="eyebrow">Projet</p>
      <h2>Supervision des agents — tableau de bord d'usage</h2>
      <p class="file-meta"><span>docs/wiki/technical/agents-supervision.md</span><span>généré : {_esc(state.get('last_scan', '?'))}</span></p>

      <div class="fact">
        <p><strong>Bloc généré automatiquement</strong> à chaque session (hook SessionStart → <code>.claude/supervision/scan_transcripts.py</code>, scan incrémental des transcripts, 0 token LLM) — ne pas éditer à la main. <strong>{nb_files} sessions</strong> couvertes · <strong>{total_skill}</strong> invocations de skills · <strong>{total_sub}</strong> lancements de sous-agents. Conception : <code>docs/reflexions/agent-superviseur.md</code> (étage 2 — diagnostic qualitatif LLM — pas encore construit).</p>
        <span class="tag tag-confirme">CONFIRMÉ</span>
        <div class="tag-source">scan_transcripts.py · {today} · ~/.claude/projects/&lt;slug&gt;/*.jsonl</div>
      </div>

      <h3>Skills — usage réel</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Skill</th><th>Famille</th><th>Invocations</th><th>Première</th><th>Dernière</th></tr></thead>
          <tbody>
{_html_usage_rows(skills, fam)}
          </tbody>
        </table>
      </div>

      <h3>Sous-agents</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Sous-agent</th><th>Lancements</th><th>Premier</th><th>Dernier</th></tr></thead>
          <tbody>
{_html_usage_rows(subagents)}
          </tbody>
        </table>
      </div>

      <h3>Jamais utilisés</h3>
{chr(10).join(unused_html) if unused_html else "      <p><em>(tous les skills installés ont déjà été invoqués)</em></p>"}

      <h3>TODO agents — chantiers à lancer (constats automatiques)</h3>
{chr(10).join(todo_html)}
    </section>
"""


def update_wiki_html(state: dict, fam: dict, todos: list) -> bool:
    """Remplace le bloc entre marqueurs TODO-AGENTS-HTML de docs/wiki.html.

    Ne fait rien si la page ou les marqueurs n'existent pas (les marqueurs sont posés
    une fois à la main dans la page ; ce script n'insère jamais à l'aveugle dans du HTML).
    """
    try:
        with open(WIKI_HTML, encoding="utf-8") as fh:
            txt = fh.read()
    except OSError:
        return False
    if HTML_MARK_START not in txt or HTML_MARK_END not in txt:
        return False
    block = (
        f"{HTML_MARK_START} — bloc généré par .claude/supervision/scan_transcripts.py, ne pas éditer à la main -->"
        + build_html_section(state, fam, todos)
        + HTML_MARK_END
    )
    pattern = re.escape(HTML_MARK_START) + r".*?" + re.escape(HTML_MARK_END)
    new_txt = re.sub(pattern, lambda m: block, txt, flags=re.DOTALL)
    if new_txt != txt:
        with open(WIKI_HTML, "w", encoding="utf-8") as fh:
            fh.write(new_txt)
    return True


def update_index(todos: list) -> None:
    bullets = "\n".join(f"- {t}" for t in todos[:3]) or "- _(aucun constat automatique)_"
    block = (
        f"{MARK_START} — section générée par .claude/supervision/scan_transcripts.py, ne pas éditer à la main -->\n"
        "## TODO agents 🤖\n"
        "\n"
        "Constats automatiques du superviseur d'agents (usage mesuré dans les transcripts de session) :\n"
        "\n"
        f"{bullets}\n"
        "\n"
        "Tableau de bord complet : [technical/agents-supervision.md](technical/agents-supervision.md) — régénéré à chaque session.\n"
        f"{MARK_END}"
    )
    try:
        with open(WIKI_INDEX, encoding="utf-8") as fh:
            txt = fh.read()
    except OSError:
        txt = ""
    if MARK_START in txt and MARK_END in txt:
        pattern = re.escape(MARK_START) + r".*?" + re.escape(MARK_END)
        txt = re.sub(pattern, lambda m: block, txt, flags=re.DOTALL)
    else:
        txt = (txt.rstrip("\n") + "\n\n" if txt else "") + block + "\n"
    with open(WIKI_INDEX, "w", encoding="utf-8") as fh:
        fh.write(txt)


def main(argv) -> int:
    state = {} if "--full" in argv else load_state()
    new_events = scan(state)
    save_state(state)
    fam = installed_skills()
    todos = build_todos(state.get("skills", {}), fam)
    page_dir = os.path.dirname(WIKI_PAGE)
    if page_dir:
        os.makedirs(page_dir, exist_ok=True)
    with open(WIKI_PAGE, "w", encoding="utf-8") as fh:
        fh.write(build_page(state, fam, todos))
    update_index(todos)
    html_ok = update_wiki_html(state, fam, todos)
    missing = state.get("transcript_dir_missing")
    detail = f" (transcripts introuvables : {missing})" if missing else ""
    if not html_ok:
        detail += " (wiki.html sans marqueurs TODO-AGENTS-HTML : bloc HTML non mis a jour)"
    print(
        f"Supervision agents : +{new_events} evenement(s), {len(state.get('files', {}))} sessions couvertes, "
        f"{len(todos)} TODO -> agents-supervision.md, index.md{' et wiki.html' if html_ok else ''} a jour.{detail}"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # jamais bloquer le démarrage de session
        print(f"Supervision agents : scan ignore ({exc.__class__.__name__}: {exc})")
        sys.exit(0)
