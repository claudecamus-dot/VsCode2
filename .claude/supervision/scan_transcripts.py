"""Superviseur d'agents — étage 1 (incrément A) : collecte déterministe, 0 token LLM.

Scanne incrémentalement les transcripts JSONL du projet (~/.claude/projects/<slug>/*.jsonl),
agrège l'usage réel des skills et sous-agents (état cumulé dans state.json, offsets par
fichier pour ne relire que le nouveau), puis régénère :
  - docs/wiki/technical/agents-supervision.md  (tableau de bord + TODO agents)
  - la section entre marqueurs TODO-AGENTS de docs/wiki/index.md
  - la section entre marqueurs TODO-AGENTS-HTML de docs/wiki.html (page rendue standalone)
  - .claude/orchestration/routing-hints.json (incrément O-C, consommé par agent-orchestrator :
    agents éprouvés/jamais-utilisés/en sommeil, vérifications oubliées, stats plan-vs-réel
    croisées avec .claude/orchestration/runs.jsonl)

Si .claude/supervision/diagnostic.json existe (écrit par la skill `agent-supervisor`,
étage 2 — diagnostic LLM), ses constats qualitatifs sont fusionnés dans la section TODO
du tableau de bord (distincts des constats déterministes, avec leur éventuelle
`proposition` de changement) et dans routing-hints.json (liste "prudence").

Incrément C (challenge, déterministe) : prudence automatique sur les agents en échec
répété dans runs.jsonl, agrégat des `resolution: <type> <nom>` (trous du catalogue,
TODO si récurrent), péremption du diagnostic à l'activité (DIAGNOSTIC_STALE_RUNS runs
non couverts) en plus de la cadence temporelle, et couverture OpenHub (table
agent_results de data/app.db, lecture seule, optionnelle). Ce script ne produit jamais
lui-même de diagnostic qualitatif — 0 token LLM, toujours.

Lancé automatiquement par le hook SessionStart (sortie : 1 ligne, jamais bloquant).
Usage manuel : py .claude/supervision/scan_transcripts.py [--full]
  --full : ignore l'état incrémental et rescanne tout l'historique.

Arbitrages (boucle propose→arbitre bouclée) : .claude/supervision/arbitrages.json
(versionné, édité à la main) enregistre les décisions humaines qui closent un constat
automatique — le TODO correspondant disparaît, la décision reste affichée dans la section
« Arbitrages enregistrés » et fusionnée dans routing-hints.json. L'usage réel reste mesuré.

Env (surcharges, utilisées par les tests) : AGENT_SUPERVISION_TRANSCRIPTS,
AGENT_SUPERVISION_STATE, AGENT_SUPERVISION_WIKI_PAGE, AGENT_SUPERVISION_WIKI_INDEX,
AGENT_SUPERVISION_RUNS, AGENT_SUPERVISION_ROUTING_HINTS, AGENT_SUPERVISION_DIAGNOSTIC,
AGENT_SUPERVISION_OPENHUB_DB, AGENT_SUPERVISION_ARBITRAGES.
Conception : docs/reflexions/agent-superviseur.md, docs/reflexions/agent-orchestrateur.md §6.
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
RUNS_PATH = os.environ.get("AGENT_SUPERVISION_RUNS") or os.path.join(
    REPO, ".claude", "orchestration", "runs.jsonl"
)
ROUTING_HINTS_PATH = os.environ.get("AGENT_SUPERVISION_ROUTING_HINTS") or os.path.join(
    REPO, ".claude", "orchestration", "routing-hints.json"
)
DIAGNOSTIC_PATH = os.environ.get("AGENT_SUPERVISION_DIAGNOSTIC") or os.path.join(
    SUP_DIR, "diagnostic.json"
)
OPENHUB_DB = os.environ.get("AGENT_SUPERVISION_OPENHUB_DB") or os.path.join(
    REPO, "data", "app.db"
)
ARBITRAGES_PATH = os.environ.get("AGENT_SUPERVISION_ARBITRAGES") or os.path.join(
    SUP_DIR, "arbitrages.json"
)
DORMANT_DAYS = 30
PROVEN_MIN = 3  # invocations à partir desquelles un agent/skill est "éprouvé"
DIAGNOSTIC_CADENCE_DAYS = 14  # au-delà : le diagnostic étage 2 est signalé "à relancer"
DIAGNOSTIC_STALE_RUNS = 3  # runs d'orchestration non couverts qui périment aussi le diagnostic
ECHEC_PRUDENCE_MIN = 2  # échecs en orchestration à partir desquels un agent passe en prudence
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


def load_jsonl(path: str) -> list:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def load_arbitrages() -> list:
    """Décisions humaines closant des constats automatiques (fichier versionné, jamais écrit ici).
    Chaque entrée : {cible, decision, date, source} — cible = nom de skill ou famille:<Nom>."""
    try:
        with open(ARBITRAGES_PATH, encoding="utf-8") as fh:
            entries = json.load(fh).get("arbitrages", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("cible") and e.get("decision")]


def load_diagnostic() -> dict:
    """Constats qualitatifs de la skill agent-supervisor (étage 2) ; None si jamais lancée."""
    try:
        with open(DIAGNOSTIC_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def diagnostic_a_jour(diagnostic, runs: list = None) -> bool:
    """Périmé au-delà de la cadence temporelle, OU dès que trop d'orchestrations récentes
    (incrément C : seuil d'activité) ne sont pas couvertes par le dernier diagnostic."""
    if not diagnostic:
        return False
    generated = diagnostic.get("generated", "")
    d = days_since(generated)
    if d is None or d > DIAGNOSTIC_CADENCE_DAYS:
        return False
    non_couverts = sum(1 for r in runs or [] if (r.get("ts") or "") > generated)
    return non_couverts < DIAGNOSTIC_STALE_RUNS


def diagnostic_todos(diagnostic, arbitrages: list = None) -> list:
    """Top constats qualitatifs (étage 2), triés par priorité, pour fusion dans le TODO wiki.

    Un constat dont la `cible` a été arbitrée (`arbitrages.json`) est exclu — même
    contrat que `build_todos()` pour les constats déterministes : une décision humaine
    ferme le TODO affiché, sans effacer la mesure réelle ni le diagnostic lui-même."""
    if not diagnostic:
        return []
    arbitres = {a["cible"] for a in arbitrages or []}
    findings = [f for f in (diagnostic.get("findings", []) or []) if f.get("cible") not in arbitres]
    findings.sort(key=lambda f: -(f.get("priorite") or 0))
    out = []
    for f in findings[:5]:
        titre = (f.get("titre") or "").strip()
        if not titre:
            continue
        reco = (f.get("recommandation") or "").strip()
        prop = (f.get("proposition") or "").strip()
        item = f"**{titre}**" + (f" — {reco}" if reco else "")
        if prop:  # incrément C : changement concret proposé, à arbitrer (jamais auto-appliqué)
            item += f" · **Proposition** : {prop}"
        out.append(item)
    return out


def catalogue_gaps(runs: list) -> dict:
    """Trous du catalogue (incrément C) : agrégat des `resolution: <type> <nom>` notés par
    l'orchestrateur quand aucun agent ne couvrait la demande (restauration/évolution/création)."""
    gaps = {}
    for r in runs:
        for res, nom in re.findall(
            r"resolution:\s*(restauration|evolution|creation)\s+([\w./-]+)", r.get("notes") or ""
        ):
            gaps[(res, nom)] = gaps.get((res, nom), 0) + 1
    return gaps


def openhub_stats():
    """Couverture OpenHub (incrément C) : lit la table agent_results de l'app (SQLite,
    lecture seule) — résultats réels vs fallback simulé (opencode absent). None si base
    ou table absente : la couverture reste optionnelle, jamais bloquante."""
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{OPENHUB_DB}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT agent_label, runtime_available, created_at FROM agent_results"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    par_agent = {}
    reels = 0
    last = ""
    for label, runtime, created in rows:
        par_agent[label] = par_agent.get(label, 0) + 1
        reels += 1 if runtime else 0
        last = max(last, created or "")
    return {"n": len(rows), "reels": reels, "simules": len(rows) - reels,
            "last": last, "par_agent": par_agent}


def build_runs_stats(runs: list):
    """Plan vs réel (O-C) : taux de réussite par playbook et par agent, à partir de runs.jsonl.

    Approximation assumée : un run n'enregistre qu'un résultat global (log_run.py, format
    O-A/O-B inchangé), donc chaque agent du plan hérite du résultat et des reprises du run
    entier — pas de granularité par étape.
    """
    par_playbook, par_agent = {}, {}
    for r in runs:
        resultat = r.get("resultat")
        reprises = r.get("reprises") or 0
        playbook = r.get("playbook")
        if playbook:
            e = par_playbook.setdefault(playbook, {"n": 0, "succes": 0, "echecs": 0, "reprises": 0})
            e["n"] += 1
            e["reprises"] += reprises
            if resultat == "succes":
                e["succes"] += 1
            elif resultat == "echec":
                e["echecs"] += 1
        for etape in r.get("plan") or []:
            agent = etape.get("agent")
            if not agent:
                continue
            e = par_agent.setdefault(agent, {"n": 0, "succes": 0, "echecs": 0, "reprises": 0})
            e["n"] += 1
            e["reprises"] += reprises
            if resultat == "succes":
                e["succes"] += 1
            elif resultat == "echec":
                e["echecs"] += 1
    return par_playbook, par_agent


def build_routing_hints(state: dict, fam: dict, par_playbook: dict, par_agent: dict, diagnostic,
                        runs: list = None, arbitrages: list = None) -> dict:
    """Sens superviseur → orchestrateur (conception §6) : ce que le scan mesure, appliqué
    par la skill agent-orchestrator lors de la composition d'un plan."""
    skills = state.get("skills", {})
    subagents = state.get("subagents", {})
    combined = {**skills, **subagents}
    eprouves = sorted(k for k, e in combined.items() if e["n"] >= PROVEN_MIN)
    jamais = sorted(k for k, v in fam.items() if k not in skills)
    en_sommeil = sorted(
        k for k, e in combined.items()
        if (lambda d: d is not None and d > DORMANT_DAYS)(days_since(e.get("last", "")))
    )
    verifs_oubliees = []
    if "revue-increment" not in skills:
        verifs_oubliees.append(
            "revue-increment jamais invoquee malgre le rappel SessionStart -> l'inserer d'office en etape terminale des plans de dev"
        )
    prudence = []
    arbitres = {a["cible"] for a in arbitrages or []}
    if diagnostic:
        for f in diagnostic.get("findings", []) or []:
            if (
                f.get("categorie") in ("ko-repete", "inefficacite")
                and f.get("cible")
                and f["cible"] not in arbitres  # un constat arbitré ne pèse plus sur le routage
            ):
                prudence.append({"cible": f["cible"], "raison": (f.get("titre") or "").strip()})
    # Incrément C — prudence déterministe : échecs répétés dans le journal d'orchestration,
    # sans attendre le diagnostic LLM (dédupliqué sur les cibles déjà signalées).
    deja = {p["cible"] for p in prudence}
    for agent, e in sorted(par_agent.items()):
        if agent not in deja and e["echecs"] >= ECHEC_PRUDENCE_MIN and e["echecs"] > e["succes"]:
            prudence.append({
                "cible": agent,
                "raison": f"échecs répétés en orchestration ({e['echecs']}/{e['n']} runs)",
            })
    gaps = catalogue_gaps(runs or [])
    return {
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "eprouves": eprouves,
        "jamais_utilises": jamais,
        "en_sommeil": en_sommeil,
        "verifications_oubliees": verifs_oubliees,
        "playbooks": par_playbook,
        "agents": par_agent,
        "prudence": prudence,
        "trous_catalogue": [
            {"resolution": res, "nom": nom, "n": n}
            for (res, nom), n in sorted(gaps.items(), key=lambda kv: -kv[1])
        ],
        "diagnostic_a_jour": diagnostic_a_jour(diagnostic, runs),
        # Boucle propose→arbitre : décisions humaines à respecter lors du routage
        # (un jamais-utilisé arbitré "conserver" se propose via son playbook, sans re-nagguer).
        "arbitrages": load_arbitrages(),
    }


def build_todos(skills: dict, fam: dict, gaps: dict = None, arbitrages: list = None) -> list:
    arbitres = {a["cible"] for a in arbitrages or []}
    todos = []
    # Incrément C : un même agent demandé/recréé plusieurs fois ad hoc = trou récurrent.
    for (res, nom), n in sorted((gaps or {}).items(), key=lambda kv: -kv[1]):
        if n >= 2:
            todos.append(
                f"**Trou récurrent du catalogue** : `{nom}` a nécessité une résolution ad hoc "
                f"×{n} ({res}) — l'ancrer pour de bon (création/restauration à arbitrer)."
            )
    bmad = [k for k, v in fam.items() if v == "BMAD"]
    bmad_unused = [k for k in bmad if k not in skills]
    if "famille:BMAD" in arbitres:
        bmad_unused = []  # tri déjà arbitré par l'humain — ne pas re-nagguer
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
    proj_unused = sorted(
        k for k, v in fam.items() if v == "projet" and k not in skills and k not in arbitres
    )
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


def build_page(state: dict, fam: dict, todos: list, diag_todos: list = None, diag_a_jour: bool = False,
               openhub: dict = None, arbitrages: list = None, diagnostic_ran: bool = False) -> str:
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
    if openhub and openhub["n"]:
        L += ["## Agents OpenHub (app)", ""]
        L.append(
            f"**{openhub['n']}** résultat(s) en base (`agent_results`) — {openhub['reels']} réel(s), "
            f"{openhub['simules']} simulé(s) (fallback sans `opencode`) · dernier : {_fmt_date(openhub['last'])}."
        )
        L.append("")
        L.append(", ".join(f"`{k}` ×{v}" for k, v in sorted(openhub["par_agent"].items())))
        L.append("")
    L += ["## TODO agents (constats automatiques)", ""]
    if todos:
        L += [f"{i}. {t}" for i, t in enumerate(todos, 1)]
    else:
        L.append("_(aucun constat — rien à signaler sur les données actuelles)_")
    if arbitrages:
        L += [
            "",
            "## Arbitrages enregistrés",
            "",
            "_Constats clos par décision humaine (`.claude/supervision/arbitrages.json`) — "
            "l'usage réel reste mesuré ci-dessus._",
            "",
        ]
        L += [f"- **`{a['cible']}`** ({a.get('date', '?')}) : {a['decision']}" for a in arbitrages]
    L += ["", "## Diagnostic qualitatif (étage 2 — `agent-supervisor`)", ""]
    if diag_todos:
        statut = "à jour" if diag_a_jour else f"⚠️ à relancer (> {DIAGNOSTIC_CADENCE_DAYS} j)"
        L.append(f"_Diagnostic {statut}._")
        L.append("")
        L += [f"{i}. {t}" for i, t in enumerate(diag_todos, 1)]
    elif diagnostic_ran:
        # Diagnostic déjà lancé mais tous ses constats sont arbitrés (cf. Arbitrages
        # enregistrés ci-dessus) — distinct de « jamais lancé », sinon le rappel
        # SessionStart induirait en erreur (on ne relance pas ce qui n'a rien à signaler).
        statut = "à jour" if diag_a_jour else f"⚠️ à relancer (> {DIAGNOSTIC_CADENCE_DAYS} j)"
        L.append(f"_Diagnostic {statut} — rien à signaler, tous les constats précédents ont été arbitrés._")
    else:
        L.append(
            "_Jamais lancé — invoquer la skill `agent-supervisor` (intégrée à `revue-increment`) "
            "pour un diagnostic qualitatif (KO répétés, efficacité, interactions entre agents)._"
        )
    L += [
        "",
        "---",
        "",
        "_Étage O-C (croisement modèle × tâche × reprises, exploitation de `runs.jsonl`) : "
        "voir `.claude/orchestration/routing-hints.json`, régénéré à chaque session._",
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


def build_html_section(state: dict, fam: dict, todos: list, diag_todos: list = None, diag_a_jour: bool = False,
                       openhub: dict = None, arbitrages: list = None, diagnostic_ran: bool = False) -> str:
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
    diag_html = []
    for t in diag_todos or []:
        diag_html.append(
            '      <div class="critical">\n'
            f"        <p>{_md_inline(t)}</p>\n"
            '        <span class="tag tag-confirme">CONFIRMÉ</span>\n'
            f'        <div class="tag-source">agent-supervisor · étage 2</div>\n'
            "      </div>"
        )
    if diag_html:
        diag_statut = "à jour" if diag_a_jour else f"⚠️ à relancer (&gt; {DIAGNOSTIC_CADENCE_DAYS} j)"
        diag_body = f'      <p><em>Diagnostic {diag_statut}.</em></p>\n' + chr(10).join(diag_html)
    elif diagnostic_ran:
        diag_statut = "à jour" if diag_a_jour else f"⚠️ à relancer (&gt; {DIAGNOSTIC_CADENCE_DAYS} j)"
        diag_body = (
            f"      <p><em>Diagnostic {diag_statut} — rien à signaler, tous les constats "
            "précédents ont été arbitrés.</em></p>"
        )
    else:
        diag_body = (
            "      <p><em>Jamais lancé — invoquer la skill <code>agent-supervisor</code> "
            "(intégrée à <code>revue-increment</code>).</em></p>"
        )
    if arbitrages:
        items = "\n".join(
            f"        <li><strong><code>{_esc(a['cible'])}</code></strong> ({_esc(a.get('date', '?'))}) : "
            f"{_esc(a['decision'])}</li>"
            for a in arbitrages
        )
        arbitrages_html = (
            "      <h3>Arbitrages enregistrés</h3>\n"
            "      <p><em>Constats clos par décision humaine (<code>.claude/supervision/arbitrages.json</code>) — "
            "l'usage réel reste mesuré ci-dessus.</em></p>\n"
            f"      <ul>\n{items}\n      </ul>\n"
        )
    else:
        arbitrages_html = ""
    if openhub and openhub["n"]:
        detail = ", ".join(f"<code>{_esc(k)}</code> ×{v}" for k, v in sorted(openhub["par_agent"].items()))
        openhub_html = (
            "      <h3>Agents OpenHub (app)</h3>\n"
            f"      <p><strong>{openhub['n']}</strong> résultat(s) en base (<code>agent_results</code>) — "
            f"{openhub['reels']} réel(s), {openhub['simules']} simulé(s) (fallback sans <code>opencode</code>) · "
            f"dernier : {_esc(_fmt_date(openhub['last']))}. {detail}</p>\n"
        )
    else:
        openhub_html = ""
    return f"""
    <section class="doc" id="agents-supervision">
      <p class="eyebrow">Projet</p>
      <h2>Supervision des agents — tableau de bord d'usage</h2>
      <p class="file-meta"><span>docs/wiki/technical/agents-supervision.md</span><span>généré : {_esc(state.get('last_scan', '?'))}</span></p>

      <div class="fact">
        <p><strong>Bloc généré automatiquement</strong> à chaque session (hook SessionStart → <code>.claude/supervision/scan_transcripts.py</code>, scan incrémental des transcripts, 0 token LLM) — ne pas éditer à la main. <strong>{nb_files} sessions</strong> couvertes · <strong>{total_skill}</strong> invocations de skills · <strong>{total_sub}</strong> lancements de sous-agents. Conception : <code>docs/reflexions/agent-superviseur.md</code> (étage 2 : skill <code>agent-supervisor</code>, section diagnostic ci-dessous).</p>
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

{openhub_html}
      <h3>TODO agents — chantiers à lancer (constats automatiques)</h3>
{chr(10).join(todo_html)}

{arbitrages_html}      <h3>Diagnostic qualitatif (étage 2 — agent-supervisor)</h3>
{diag_body}
    </section>
"""


def update_wiki_html(state: dict, fam: dict, todos: list, diag_todos: list = None, diag_a_jour: bool = False,
                     openhub: dict = None, arbitrages: list = None, diagnostic_ran: bool = False) -> bool:
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
        + build_html_section(state, fam, todos, diag_todos, diag_a_jour, openhub, arbitrages, diagnostic_ran)
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
    runs = load_jsonl(RUNS_PATH)
    arbitrages = load_arbitrages()
    todos = build_todos(state.get("skills", {}), fam, catalogue_gaps(runs), arbitrages)

    par_playbook, par_agent = build_runs_stats(runs)
    diagnostic = load_diagnostic()
    diag_todos = diagnostic_todos(diagnostic, arbitrages)
    diag_a_jour = diagnostic_a_jour(diagnostic, runs)
    openhub = openhub_stats()
    hints = build_routing_hints(state, fam, par_playbook, par_agent, diagnostic, runs, arbitrages)
    hints_dir = os.path.dirname(ROUTING_HINTS_PATH)
    if hints_dir:
        os.makedirs(hints_dir, exist_ok=True)
    with open(ROUTING_HINTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(hints, fh, ensure_ascii=False, indent=1)

    page_dir = os.path.dirname(WIKI_PAGE)
    if page_dir:
        os.makedirs(page_dir, exist_ok=True)
    diagnostic_ran = diagnostic is not None
    with open(WIKI_PAGE, "w", encoding="utf-8") as fh:
        fh.write(build_page(state, fam, todos, diag_todos, diag_a_jour, openhub, arbitrages, diagnostic_ran))
    update_index(todos)
    html_ok = update_wiki_html(state, fam, todos, diag_todos, diag_a_jour, openhub, arbitrages, diagnostic_ran)
    missing = state.get("transcript_dir_missing")
    detail = f" (transcripts introuvables : {missing})" if missing else ""
    if not html_ok:
        detail += " (wiki.html sans marqueurs TODO-AGENTS-HTML : bloc HTML non mis a jour)"
    if not diag_a_jour:
        detail += " (diagnostic agent-supervisor a lancer ou perime)"
    print(
        f"Supervision agents : +{new_events} evenement(s), {len(state.get('files', {}))} sessions couvertes, "
        f"{len(todos)} TODO, {len(runs)} run(s) orchestrateur -> agents-supervision.md, index.md"
        f"{' et wiki.html' if html_ok else ''}, routing-hints.json a jour.{detail}"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # jamais bloquer le démarrage de session
        print(f"Supervision agents : scan ignore ({exc.__class__.__name__}: {exc})")
        sys.exit(0)
