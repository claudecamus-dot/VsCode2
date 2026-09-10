r"""PostToolUse hook (Bash/PowerShell) — garde-fou NON bloquant qui, juste après
un `git push`, LIT le dernier run CI et en fait remonter le statut AU MOMENT du
push. C'est l'OPTION A du constat superviseur « Angle mort CI : 6 runs rouges
d'affilée pendant que la suite locale était verte » (VSCode2, catégorie
`verification-manquante`), arbitrée ACCEPTÉE.

Le constat : les runs GitHub Actions #10 à #15 (2026-07-24 → 2026-07-29) sont
restés `failure` sans jamais être remontés dans une séance — 2 tests dépendaient
d'une CLI absente du runner Linux. Le vert local ne prouve rien de la CI : le
poste de dev a un environnement plus riche que le runner. Sans lecture du statut
après push, la CI cesse d'être un gate et devient du rouge permanent que plus
personne ne regarde. La règle de checklist (`revue-increment` §2) posée le même
jour aide, mais rien ne l'exécute ; ce hook déplace le contrôle AU BON INSTANT
(le push) et le rend EXÉCUTABLE.

Conception (mêmes principes que `warn_verif_before_commit.py`, sibling) :
- **Non bloquant** : émet un `systemMessage` (visible utilisateur) + un
  `additionalContext` (visible modèle), SANS `permissionDecision`. On pousse
  JUSTEMENT pour déclencher la CI — bloquer serait absurde. On avertit / on
  informe, on ne bloque pas.
- **Fail-open sur les pannes PASSAGÈRES** : remote non-GitHub, réseau coupé,
  API en erreur, dépôt indéterminable → on rend la main sans rien émettre. Un
  garde-fou qui ajoute de la friction à chaque push serait débranché la semaine
  suivante.
- **MAIS plus de silence sur l'absence de jeton** (correctif du 2026-09-10).
  Mesuré ce jour-là : le remote de ce dépôt est en SSH et le dépôt est privé —
  il n'existe donc AUCUN identifiant HTTPS, `git credential fill` ne peut rien
  rendre, et ce hook ne s'était jamais exprimé depuis son installation. Se
  taire là-dessus laissait croire que la CI était surveillée alors que
  personne ne la regardait : c'est l'angle mort même qu'il devait fermer, et
  c'est ainsi que le lint a pu ne rien mesurer depuis le 2026-07-23 (`ruff`
  n'était installé dans aucun requirements, et `|| true` avalait le « No
  module named ruff »). Le hook le DIT désormais, au plus une fois par 24 h.
- **Sans `gh`** (absent de cette machine) : jeton depuis `GH_TOKEN` /
  `GITHUB_TOKEN`, à défaut `git credential fill` (sans invite possible), puis
  l'API REST `actions/runs`. Le jeton N'EST JAMAIS imprimé ni journalisé.
- **Cheap sur le cas courant** : l'écrasante majorité des commandes shell ne sont
  pas des push — détection par le tokenizer éprouvé de `guard_destructive_git.py`
  (heredocs, segments quote-safe) puis retour immédiat.
- **Le run du commit poussé n'est en général pas encore enregistré** à l'instant
  du push : le hook rapporte alors le DERNIER run connu de la branche (un rouge
  persistant y saute aux yeux — c'est exactement l'angle mort visé) et donne la
  commande de re-vérification à ~30 s.

La décision d'affichage est isolée dans `build_message()` (pure, testable sans
réseau) ; l'accès réseau est best-effort et cloisonné.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

try:  # réutilise le tokenizer éprouvé du guard voisin ; sinon, dégrade en silence
    from guard_destructive_git import _strip_heredocs, _segments
except Exception:  # pragma: no cover - fail-open
    _strip_heredocs = None
    _segments = None

_GIT_OPTS_WITH_VALUE = ("-C", "-c", "--git-dir", "--work-tree", "--namespace")
# Conclusions GitHub Actions qui valent alerte rouge (un run terminé mais non vert).
_RED = ("failure", "cancelled", "timed_out", "startup_failure", "action_required")
# Commande de re-vérification proposée dans le message. Passe par GH_TOKEN :
# sur ce dépôt (remote SSH, dépôt privé) `git credential fill` ne rend rien, et
# proposer une commande qui ne peut pas aboutir est pire que ne rien proposer.
_RECHECK = (
    "GH_TOKEN=<jeton portée repo> py .claude/hooks/check_ci_after_push.py  "
    "# (ou relancer un push)"
)


def _git_push_tokens(segment):
    """-> tokens d'un `git push` réel (après le mot 'push'), ou None sinon."""
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return None  # quotes déséquilibrées / substitution — on ne devine pas
    if not tokens:
        return None
    start = 0
    while start < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[start]):
        start += 1  # saute les affectations VAR=value en tête
    if start >= len(tokens) or tokens[start].lower() != "git":
        return None
    rest = tokens[start + 1:]
    i = 0
    sub = None
    while i < len(rest):
        t = rest[i]
        if t.startswith("-"):
            i += 2 if t in _GIT_OPTS_WITH_VALUE else 1
            continue
        sub = t
        break
    if sub != "push":
        return None
    if "--dry-run" in rest or "-n" in rest:
        return None  # ne pousse rien
    return rest[i + 1:]  # tokens après 'push' (remote / refspecs / options)


def _push_target(push_tokens):
    """(remote, branche_distante) déduits des tokens d'un `git push`. Best-effort :
    `git push`, `git push origin`, `git push origin main`, `git push origin HEAD:main`
    → la branche distante est le côté droit d'un refspec `a:b`, sinon le refspec nu,
    sinon None (l'appelant retombe sur la branche courante)."""
    remote = None
    branch = None
    for t in push_tokens:
        if t.startswith("-"):
            continue  # options : --force, -u, --tags…
        if remote is None:
            remote = t
            continue
        # premier refspec rencontré
        branch = t.split(":", 1)[1] if ":" in t else t
        break
    return remote or "origin", branch


def _run_git(args, cwd, timeout=8):
    try:
        r = subprocess.run(["git"] + args, cwd=cwd or None,
                           capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def _owner_repo(remote_url):
    """Extrait (owner, repo) d'une URL de remote GitHub (https ou ssh), sinon None."""
    if not remote_url:
        return None
    m = re.search(r"github\.com[/:]([^/]+)/(.+?)(?:\.git)?$", remote_url.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def _github_token(cwd):
    """Token GitHub (jamais imprimé). None si indisponible.

    D'ABORD l'environnement (`GH_TOKEN`, `GITHUB_TOKEN`) : c'est le seul chemin
    NON INTERACTIF, et sur ce dépôt c'est le seul qui puisse aboutir. Mesuré le
    2026-09-10 : le remote est en SSH (`git@github.com:…`), il n'existe donc
    AUCUN identifiant HTTPS en cache, et le dépôt est privé — l'API répond 404
    sans jeton. `git credential fill` part alors en demander un au Credential
    Manager, c'est-à-dire à personne dans un hook, et expire au bout de 8 s.

    Conséquence pendant des mois : ce garde-fou ne s'est JAMAIS exprimé. C'est
    ainsi que le lint CI a pu ne rien mesurer depuis le 2026-07-23 sans que
    quiconque le voie — `ruff` n'était installé dans aucun requirements, et le
    `|| true` de l'étape avalait aussi le « No module named ruff ».
    """
    for nom in ("GH_TOKEN", "GITHUB_TOKEN"):
        depuis_env = os.environ.get(nom)
        if depuis_env and depuis_env.strip():
            return depuis_env.strip()
    try:
        r = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            cwd=cwd or None, capture_output=True, text=True, timeout=8,
            # Aucune invite, jamais : dans un hook elle n'a personne en face et
            # ne fait que retarder de 8 s un échec certain.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip() or None
    return None


# Une seule alerte « pas de jeton » par 24 h, tracée hors du dépôt — rien à
# committer, rien à ajouter au .gitignore.
_MARQUEUR_AVIS = Path(tempfile.gettempdir()) / "vscode2_ci_sans_jeton.txt"


def _doit_signaler_absence_de_jeton() -> bool:
    """Vrai au plus une fois par tranche de 24 h.

    Fail-OUVERT délibérément : si le marqueur est illisible, on préfère le dire
    une fois de trop que pas du tout — c'est le silence qui a coûté cher, pas
    le bruit."""
    try:
        if _MARQUEUR_AVIS.exists():
            if time.time() - _MARQUEUR_AVIS.stat().st_mtime < 24 * 3600:
                return False
        _MARQUEUR_AVIS.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        return True
    return True


def _latest_runs(owner, repo, branch, token, limit=5):
    """Derniers runs GitHub Actions de la branche (best-effort). [] sur toute erreur."""
    url = (f"https://api.github.com/repos/{owner}/{repo}/actions/runs"
           f"?branch={urllib.request.quote(branch)}&per_page={limit}")
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "vscode2-ci-check",
    })
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.load(resp)
    except Exception:
        return []
    out = []
    for run in data.get("workflow_runs", []):
        out.append({
            "number": run.get("run_number"),
            "status": run.get("status"),          # queued | in_progress | completed
            "conclusion": run.get("conclusion"),  # success | failure | ...
            "sha": (run.get("head_sha") or "")[:7],
            "name": run.get("name") or "CI",
            "url": run.get("html_url") or "",
        })
    return out


def build_message(runs, pushed_sha, recheck=_RECHECK):
    """Décision d'affichage, PURE (testable sans réseau).

    - run du commit poussé trouvé ROUGE, ou en cours, ou vert : message adapté ;
    - run du commit pas encore enregistré : rapporte le dernier run connu (un rouge
      persistant y ressort) + comment re-vérifier ;
    - aucun run : None (silence).
    Retourne le message (str) ou None (rien à dire)."""
    if not runs:
        return None
    short = (pushed_sha or "")[:7]
    match = next((r for r in runs if short and r["sha"] == short), None)
    if match is not None:
        r = match
        if r["status"] != "completed":
            return (f"⏳ CI du commit poussé ({short}) : run #{r['number']} `{r['status']}` "
                    f"— pas encore de verdict. Re-vérifier avant de considérer l'incrément "
                    f"livré. {r['url']}".strip())
        if r["conclusion"] in _RED:
            return (f"⚠️ CI ROUGE sur le commit poussé ({short}) : run #{r['number']} "
                    f"`{r['conclusion']}`. Le vert local ne prouve rien de la CI — "
                    f"OUVRIR le run et corriger avant de continuer. {r['url']}".strip())
        if r["conclusion"] == "success":
            return f"✅ CI verte sur le commit poussé ({short}) : run #{r['number']}."
        return (f"CI du commit poussé ({short}) : run #{r['number']} "
                f"conclusion `{r['conclusion']}`. À vérifier. {r['url']}".strip())
    # Le run du commit qui vient d'être poussé n'est pas encore enregistré.
    last = runs[0]
    head = (f"ℹ️ Le run CI du commit poussé ({short}) n'est pas encore enregistré "
            f"(déclenchement asynchrone). ")
    if last["status"] != "completed":
        state = f"Dernier run connu : #{last['number']} `{last['status']}` ({last['sha']})."
    elif last["conclusion"] in _RED:
        state = (f"⚠️ ATTENTION — dernier run connu de la branche ROUGE : #{last['number']} "
                 f"`{last['conclusion']}` ({last['sha']}). {last['url']}")
    elif last["conclusion"] == "success":
        state = f"Dernier run connu : #{last['number']} vert ({last['sha']})."
    else:
        state = (f"Dernier run connu : #{last['number']} `{last['conclusion']}` "
                 f"({last['sha']}).")
    return f"{head}{state} Re-vérifier dans ~30 s :\n    {recheck}".strip()


def _detect_push(data):
    """-> True si la commande de l'outil est un `git push` réel."""
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not cmd:
        return None
    strip = _strip_heredocs or (lambda s: s)
    try:
        cmd = strip(cmd)
        segs = _segments(cmd) if _segments else [cmd]
    except Exception:
        return None  # fail-open
    for seg in segs:
        toks = _git_push_tokens(seg)
        if toks is not None:
            return toks
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    push_tokens = _detect_push(data)
    if push_tokens is None:
        return  # pas un git push — silence, cas courant

    cwd = data.get("cwd")
    remote, branch = _push_target(push_tokens)
    if branch is None:
        branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd) or "HEAD"

    remote_url = _run_git(["remote", "get-url", remote], cwd)
    owner_repo = _owner_repo(remote_url)
    if owner_repo is None:
        return  # remote non-GitHub / indéterminable — fail-open
    owner, repo = owner_repo

    token = _github_token(cwd)
    if not token:
        # PLUS de silence total (correctif du 2026-09-10). Le fail-open reste la
        # bonne réponse à une panne PASSAGÈRE, mais pas à une impossibilité
        # STRUCTURELLE : ici aucun jeton n'existe et aucun n'apparaîtra tout
        # seul. Se taire revient alors à laisser croire que la CI est
        # surveillée alors que personne ne la regarde — exactement l'angle mort
        # que ce hook a été écrit pour fermer. On le dit, au plus une fois par
        # jour, pour ne pas devenir la friction qu'on débranche la semaine
        # suivante.
        if _doit_signaler_absence_de_jeton():
            avis = (
                f"CI non vérifiée après le push ({owner}/{repo}, branche {branch}) : "
                "aucun jeton GitHub disponible. Le remote est en SSH et le dépôt "
                "est privé, donc `git credential fill` ne peut rien rendre. Pour "
                "activer ce contrôle, poser GH_TOKEN (portée `repo`) dans "
                "l'environnement. Sans lui ce garde-fou reste muet — et c'est ce "
                "silence qui a laissé passer 6 CI rouges en 2026-07, puis un lint "
                "qui ne mesurait rien depuis le 2026-07-23."
            )
            print(json.dumps({
                "systemMessage": avis,
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": avis,
                },
            }))
        return

    pushed_sha = _run_git(["rev-parse", "HEAD"], cwd) or ""
    runs = _latest_runs(owner, repo, branch, token)
    message = build_message(runs, pushed_sha)
    if not message:
        return

    print(json.dumps({
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        },
    }))


if __name__ == "__main__":
    main()
