#!/usr/bin/env python3
"""Inventaire git des agents/skills — présents ET supprimés de l'historique.

Utilisé par la skill `agent-orchestrator` quand aucun agent/skill du catalogue ne couvre
une demande : avant de proposer une création, vérifier si un agent adapté a existé
(il a pu être supprimé — ex. les agents `external/openhub_clone/agents/` retirés en
nettoyage) et proposer sa restauration plutôt qu'une réécriture à vide.

Sont considérés « agents » les fichiers que git connaît sous :
  - */skills/<nom>/SKILL.md   (skills Claude Code — le nom est le dossier)
  - */agents/**/*.md          (définitions d'agents — .claude, .opencode, dépôts mirrorés)

Usage : py .claude/orchestration/git_agents_inventory.py [--json]
  Sortie par défaut : deux tables markdown (présents, supprimés avec commande de
  restauration `git show <sha>^:<chemin>`). `--json` : structure exploitable.

Env (tests) : AGENT_INVENTORY_REPO — racine du dépôt git à inventorier.
Déterministe, 0 token LLM. Conception : `.claude/skills/agent-orchestrator/SKILL.md`
(§ 2, escalade « aucun agent/skill ne couvre le besoin »).
"""
import json
import os
import re
import subprocess
import sys

REPO = os.environ.get("AGENT_INVENTORY_REPO") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
SKILL_RE = re.compile(r"(^|/)skills/([^/]+)/SKILL\.md$")
AGENT_RE = re.compile(r"(^|/)agents/(?:.+/)?([^/]+)\.md$")


# Separateur des lignes d'en-tete de commit dans `git log --format=...`, au lieu de
# `@`. Un CHEMIN peut legitimement commencer par `@` (mesure : `@robot/agents/x.md`
# supprime d'un vrai depot) et se faisait alors confondre avec une ligne d'en-tete —
# `sha, date, sujet = ligne[1:].split("|", 2)` levait `ValueError` sur un texte sans
# `|`, et `inventaire()` plantait. US (Unit Separator, ASCII 0x1F) ne peut PAS
# apparaitre en clair en tete d'un chemin reel : Windows interdit purement les
# caracteres de controle 0-31 dans un nom de fichier, et meme la ou l'OS l'autorise,
# git echappe TOUJOURS les caracteres de controle d'un chemin qu'il imprime — ce
# comportement est independant de `core.quotepath`, qui ne gouverne que les octets
# >= 0x80. Un vrai chemin ne peut donc jamais entrer en collision avec ce prefixe.
# PIEGE ECARTE : RS (0x1E), premier choix, est silencieusement AVALE par
# `str.splitlines()` — Python le traite comme une frontiere de ligne au meme titre
# que `\n` (avec \x1c et \x1d), donc `line.startswith(sep)` ne matchait plus JAMAIS
# et tous les commits supprimes disparaissaient sans un mot. Mesure avant d'ecrire
# (`'a\x1ebbb\nccc'.splitlines() == ['a', 'bbb', 'ccc']`) ; 0x1F n'est pas dans la
# liste des frontieres reconnues par `splitlines()` et survit intact.
_SEP_ENTETE = "\x1f"


def _git(*args) -> str:
    out = subprocess.run(
        # `-c core.quotepath=false` : par defaut (aucune config globale sur ce
        # poste ne le change — verifie), git ECHAPPE tout octet non-ASCII d'un
        # chemin et l'entoure de guillemets ("agents/r\303\251sum\303\251.md" au
        # lieu de "agents/résumé.md"). Cette forme ne termine plus par l'extension
        # attendue pour les regex de classification : un agent/skill au nom
        # accentue devenait invisible, present ou supprime, et l'outil repondait
        # « non » a tort a la question qu'il sert a trancher.
        ["git", "-c", "core.quotepath=false", "-C", REPO, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args[:2])}... a echoue : {out.stderr.strip()}")
    return out.stdout


def classify(path: str):
    """(nom, type) si le chemin est une définition d'agent/skill, sinon None."""
    m = SKILL_RE.search(path)
    if m:
        return m.group(2), "skill"
    m = AGENT_RE.search(path)
    if m:
        return m.group(2), "agent"
    return None


def famille(path: str) -> str:
    if path.startswith(".claude/"):
        return "claude"
    if path.startswith(".opencode/"):
        return "opencode"
    return "externe"


def inventaire() -> dict:
    presents, present_paths = [], set()
    for path in _git("ls-files").splitlines():
        c = classify(path)
        if c:
            presents.append({"nom": c[0], "type": c[1], "famille": famille(path), "chemin": path})
            present_paths.add(path)

    supprimes, vus = [], set()
    # --format en tête de chaque commit, suivi des chemins supprimés ; le plus récent d'abord.
    log = _git("log", "--diff-filter=D", "--name-only", "--date=short",
               f"--format={_SEP_ENTETE}%H|%ad|%s")
    commit = None
    for line in log.splitlines():
        if line.startswith(_SEP_ENTETE):
            sha, date, sujet = line[1:].split("|", 2)
            commit = {"sha": sha, "date": date, "sujet": sujet}
            continue
        path = line.strip()
        c = classify(path) if path else None
        if not c or path in present_paths or path in vus:
            continue
        vus.add(path)
        supprimes.append({
            "nom": c[0], "type": c[1], "famille": famille(path), "chemin": path,
            "supprime_le": commit["date"], "commit": commit["sha"][:9], "sujet": commit["sujet"],
            "restaurer": f"git show {commit['sha'][:9]}^:{path}",
        })
    return {"repo": REPO, "presents": presents, "supprimes": supprimes}


def render(inv: dict) -> str:
    L = [f"# Inventaire git des agents — {len(inv['presents'])} présents, "
         f"{len(inv['supprimes'])} supprimés", ""]
    L += ["## Présents", "", "| Nom | Type | Famille | Chemin |", "| --- | --- | --- | --- |"]
    for e in sorted(inv["presents"], key=lambda e: (e["famille"], e["nom"])):
        L.append(f"| `{e['nom']}` | {e['type']} | {e['famille']} | {e['chemin']} |")
    L += ["", "## Supprimés (restaurables)", ""]
    if inv["supprimes"]:
        L += ["| Nom | Famille | Supprimé le | Commit | Restaurer |",
              "| --- | --- | --- | --- | --- |"]
        for e in sorted(inv["supprimes"], key=lambda e: (e["famille"], e["nom"])):
            L.append(f"| `{e['nom']}` | {e['famille']} | {e['supprime_le']} "
                     f"({e['sujet']}) | {e['commit']} | `{e['restaurer']}` |")
    else:
        L.append("Aucun.")
    return "\n".join(L) + "\n"


def main(argv) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # console Windows en cp1252 sinon
        sys.stdout.reconfigure(encoding="utf-8")
    inv = inventaire()
    if "--json" in argv:
        print(json.dumps(inv, ensure_ascii=False, indent=1))
    else:
        print(render(inv))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
