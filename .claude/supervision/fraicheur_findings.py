#!/usr/bin/env python3
"""Fraicheur des findings : pre-controle deterministe (0 token LLM) avant dispatch.

Mesure du 2026-10-03 : un test a 20 agents a depense 25 agents pour decouvrir que 5
findings ouverts sur 5 etaient DEJA corriges (commits a5a0087, 8d09ef3, 6cf87ef, c0c1a9b,
5dbc54f). Ce script classe chaque finding OUVERT (aucun arbitrage posterieur a `vu_le`,
meme oracle que write_diagnostic._ferme) selon des signaux d'obsolescence :

  (a) un fichier cite `chemin:ligne` dans titre/preuve/proposition a change depuis `vu_le`;
  (b) des commits depuis `vu_le` mentionnent le slug de la cible ou >= 2 mots-cles du
      titre : indice seul (`a-verifier`), perime seulement avec un fichier/test sujet change;
  (c) la proposition nomme un test (`test_xxx`) dont la definition est apparue depuis
      `vu_le` (git log -S), ou un fichier tests/test_*.py modifie depuis.

Verdicts : `probablement-perime` (signal a, c ou arbitrage applique), `a-verifier` (b seul),
`toujours-ouvert` (aucun signal), `non mesure` (git indisponible ou timeout : fail-open).
Il CLASSE, il ne ferme JAMAIS rien (R4) : la fermeture reste un arbitrage humain.

Usage : py .claude/supervision/fraicheur_findings.py [--cible X] [--json] [--tous]
(--tous : inclut aussi les findings deja arbitres, pour verifier un dispatch.)
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

GIT_TIMEOUT_S = 15
REPO = os.environ.get("FRAICHEUR_REPO") or os.path.dirname(os.path.dirname(HERE))
DIAGNOSTIC_PATH = os.environ.get("AGENT_SUPERVISION_DIAGNOSTIC") or os.path.join(
    HERE, "diagnostic.json")

PERIME, A_VERIFIER, OUVERT, NON_MESURE = (
    "probablement-perime", "a-verifier", "toujours-ouvert", "non mesure")

_RE_FICHIER_LIGNE = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+[/\\])*[\w.-]+\.(?:py|js|json|md|html|toml|ps1|sh|yml|yaml)):(\d+)")
_RE_TEST = re.compile(r"\b(test_[A-Za-z0-9_]+)\b")
_RE_FICHIER_TEST = re.compile(r"(?<![\w/.-])((?:tests/)?test_[A-Za-z0-9_]+\.py)\b")
_STOP = {
    "depuis", "apres", "avant", "toujours", "jamais", "plusieurs", "tests", "fichier",
    "finding", "constat", "aucun", "aucune", "chaque", "entre", "sans", "avec", "dans",
}


class GitIndisponible(Exception):
    """git absent, timeout ou erreur : le signal est « non mesure », jamais « neuf »."""


def _run_git(args: list) -> str:
    """Execute git dans REPO. Remplacable (tests). Leve GitIndisponible."""
    try:
        r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitIndisponible(str(exc)) from exc
    if r.returncode != 0:
        raise GitIndisponible((r.stderr or "").strip()[:200] or f"exit {r.returncode}")
    return r.stdout


RUNNER = _run_git


def _log(since: str, *extra: str) -> list:
    """Commits (sha court + sujet) depuis `since`."""
    out = RUNNER(["log", f"--since={since}", "--format=%h %s", *extra])
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _texte(f: dict) -> str:
    return " ".join(str(f.get(k) or "") for k in ("titre", "preuve", "proposition"))


def _slug(cible: str) -> str:
    return cible.split(":", 1)[1] if ":" in cible else cible


def _mots_cles(f: dict) -> list:
    mots = re.findall(r"[A-Za-z][A-Za-z0-9]{5,}", _slug(str(f.get("cible") or "")).replace("_", "-"))
    mots += re.findall(r"[A-Za-z][A-Za-z0-9]{6,}", str(f.get("titre") or ""))
    vus, res = set(), []
    for m in sorted(mots, key=len, reverse=True):
        if m.lower() not in vus and m.lower() not in _STOP:
            vus.add(m.lower())
            res.append(m)
    return res[:4]


def signaux(f: dict) -> dict:
    """Calcule les preuves d'obsolescence d'un finding. Leve GitIndisponible."""
    since = str(f.get("vu_le") or "")[:10]
    cible = str(f.get("cible") or "")
    ev = {"fichiers": [], "slug": [], "mots": [], "tests": [], "arbitrage": []}
    if not since:
        raise GitIndisponible("vu_le absent")
    texte = _texte(f)
    # (a) fichiers cites changes depuis vu_le
    for fichier in dict.fromkeys(m.group(1).replace("\\", "/") for m in _RE_FICHIER_LIGNE.finditer(texte)):
        commits = _log(since, "--", fichier)
        if commits:
            ev["fichiers"].append({"fichier": fichier, "commits": [c.split()[0] for c in commits[:5]]})
    # (b) commits mentionnant la cible (fort) ou des mots-cles du titre (faible)
    for terme in dict.fromkeys(t for t in (_slug(cible), cible) if t):
        if len(terme) < 16 and ":" not in terme:
            continue  # cible generique (« salles ») : ne vaut pas preuve, voir mots-cles
        ev["slug"] += [c for c in _log(since, "-F", "-i", f"--grep={terme}") if c not in ev["slug"]]
    par_commit: dict = {}
    for mot in _mots_cles(f):
        for c in _log(since, "-F", "-i", f"--grep={mot}"):
            par_commit.setdefault(c, set()).add(mot.lower())
    ev["mots"] = [c for c, ms in par_commit.items() if len(ms) >= 2 and c not in ev["slug"]]
    # (c) test nomme dans la proposition, apparu ou modifie depuis vu_le
    prop = str(f.get("proposition") or "")
    for nom in dict.fromkeys(_RE_TEST.findall(prop)):
        commits = _log(since, "-F", f"-Sdef {nom}", "--", "tests")
        if commits:
            ev["tests"].append({"test": nom, "commits": [c.split()[0] for c in commits[:5]]})
    for ft in dict.fromkeys(_RE_FICHIER_TEST.findall(prop)):
        chemin = ft if ft.startswith("tests/") else "tests/" + ft
        commits = _log(since, "--", chemin)
        if commits:
            ev["tests"].append({"test": chemin, "commits": [c.split()[0] for c in commits[:5]]})
    # (d) arbitrage ACCEPTE + APPLIQUE citant un sha qui existe dans le depot (mode --tous)
    for texte_decision in f.get("_decisions") or []:
        if "APPLIQUE" not in texte_decision.upper():
            continue
        for sha in dict.fromkeys(re.findall(r"(?<![0-9a-zA-Z])[0-9a-f]{7,40}(?![0-9a-zA-Z])", texte_decision)):
            if not re.search(r"[a-f]", sha) or not re.search(r"\d", sha):
                continue  # un mot ou un nombre, pas un sha
            try:
                RUNNER(["cat-file", "-e", sha + "^{commit}"])
            except GitIndisponible:
                continue  # sha inconnu : ignore (ne pas confondre avec git absent, deja vu plus haut)
            ev["arbitrage"].append(sha)
    return ev


def verdict(ev: dict) -> str:
    # Option 2 (arbitrage proprietaire 2026-10-04) : un commit qui ne fait que CITER la cible
    # (portage, propagation) ne prouve rien ; PERIME exige qu'un fichier ou test sujet ait
    # aussi change (signaux fichiers/tests) ou un arbitrage applique.
    if ev["fichiers"] or ev["tests"] or ev["arbitrage"]:
        return PERIME
    if ev["slug"] or ev["mots"]:
        return A_VERIFIER
    return OUVERT


def _ouverts(findings: list, tous: bool = False) -> list:
    """Findings sans arbitrage de cloture : meme oracle que write_diagnostic (fail-open :
    import impossible -> tous consideres ouverts, direction sure). Avec `tous`, les
    findings deja arbitres sont gardes (un « ACCEPTE » n'est pas un « APPLIQUE » : c'est
    le cas du dispatch qui relit diagnostic.json sans filtrer) et marques `_arbitre`."""
    try:
        import write_diagnostic as wd
        arbs = wd._charger_arbitrages()
        if tous:
            return [dict(f, _arbitre=wd._ferme(f, arbs), _decisions=[
                str(a.get("decision") or "") + " " + str(a.get("preuve_application") or "")
                for a in arbs if a.get("cible") == f.get("cible")
                and str(a.get("date") or "")[:10] >= str(f.get("vu_le") or "")[:10]])
                for f in findings]
        return [f for f in findings if not wd._ferme(f, arbs)]
    except Exception:  # noqa: BLE001 — dispositif absent ou casse : on ne ferme rien
        return list(findings)


def analyser(findings: list, cible: str = "", tous: bool = False) -> list:
    res = []
    for f in (_ouverts(findings, True) if tous else _ouverts(findings)):
        if cible and cible.lower() not in str(f.get("cible") or "").lower():
            continue
        item = {"cible": f.get("cible"), "titre": f.get("titre"), "vu_le": f.get("vu_le")}
        if f.get("_arbitre"):
            item["arbitre"] = True
        try:
            ev = signaux(f)
            item.update(verdict=verdict(ev), evidence=ev)
        except GitIndisponible as exc:
            item.update(verdict=NON_MESURE, evidence={"erreur": str(exc)})
        res.append(item)
    ordre = {PERIME: 0, A_VERIFIER: 1, NON_MESURE: 2, OUVERT: 3}
    res.sort(key=lambda r: ordre[r["verdict"]])
    return res


BANDEAU = (
    "# ATTENTION : classement ≠ preuve. La classe « probablement perime » est une indication",
    "# fondee sur des messages de commit qui citent la cible, PAS la preuve que le finding est resolu.",
    "# Faux positif connu : un commit de portage/propagation dont le message cite la cible",
    "# sans modifier les fichiers sujets du finding. Lire le commit avant de conclure.",
)


def _rendre(res: list) -> str:
    lignes = list(BANDEAU)
    for r in res:
        marque = " [deja arbitre]" if r.get("arbitre") else ""
        lignes.append(f"[{r['verdict']}] {r['cible']} (vu le {r['vu_le']}){marque}")
        ev = r["evidence"]
        if "erreur" in ev:
            lignes.append(f"    non mesure : {ev['erreur']}")
            continue
        for x in ev["fichiers"]:
            lignes.append(f"    fichier cite modifie : {x['fichier']} <- {', '.join(x['commits'])}")
        for c in ev["slug"]:
            lignes.append(f"    commit citant la cible : {c}")
        for x in ev["tests"]:
            lignes.append(f"    test apparu/modifie : {x['test']} <- {', '.join(x['commits'])}")
        for sha in ev["arbitrage"]:
            lignes.append(f"    arbitrage APPLIQUE citant le commit existant : {sha}")
        for c in ev["mots"]:
            lignes.append(f"    commit (>=2 mots-cles du titre) : {c}")
    lignes.append("Classement indicatif : rien n'est ferme (R4), l'arbitrage reste humain.")
    return "\n".join(lignes)


def main(argv: list) -> int:
    for stream in (sys.stdout,):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    tous = "--tous" in argv
    cible = ""
    if "--cible" in argv:
        i = argv.index("--cible")
        cible = argv[i + 1] if i + 1 < len(argv) else ""
    try:
        with open(DIAGNOSTIC_PATH, encoding="utf-8") as fh:
            findings = [f for f in json.load(fh).get("findings", []) if isinstance(f, dict)]
    except (OSError, ValueError, AttributeError) as exc:
        print(f"fraicheur_findings : diagnostic illisible ({exc})", file=sys.stderr)
        return 0
    res = analyser(findings, cible, tous)
    print(json.dumps(res, ensure_ascii=False, indent=1) if "--json" in argv else _rendre(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
