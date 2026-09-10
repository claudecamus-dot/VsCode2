"""Tests-contrat de `.claude/hooks/check_ci_after_push.py` — écrit en même temps
que le hook (2026-07-30) mais resté non câblé/non testé jusqu'ici (reliquat de
séance repris le 2026-07-31, cf. `feedback-adversarial-findings-need-durable-triage-file`
sur le risque de laisser du travail non bouclé sans trace).

Ce hook lit le dernier run CI GitHub Actions juste après un `git push` — c'est
l'implémentation « Option A » (garde-fou exécutable) du constat superviseur
« Angle mort CI : 6 runs rouges d'affilée pendant que la suite locale était
verte » (`.claude/supervision/diagnostic.json`, catégorie
`verification-manquante`) ; l'alternative « règle de checklist » (revue-increment
§2 + mémoire `feedback-green-local-suite-hides-red-ci`) était déjà en place.

Deux niveaux, comme `build_message` le documente lui-même (« PURE, testable
sans réseau ») :
1. unitaire, en import direct du module (`importlib.util`, même pattern que
   `tests/test_agent_supervision.py::test_runs_a_solder_...`) — toutes les
   branches de `build_message`/`_git_push_tokens`/`_push_target`/`_owner_repo`,
   plus `main()` avec `_github_token`/`_latest_runs` monkeypatchés (jamais de
   vrai réseau dans cette suite) ;
2. contrat, en sous-processus réel (même pattern que `test_hooks_discipline.py`)
   — silence sur le cas courant (pas un push), JSON invalide, et un remote non-
   GitHub : ces trois chemins n'atteignent jamais `_github_token`/le réseau,
   donc restent déterministes sans dépendre du credential store de la machine.
"""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / ".claude" / "hooks" / "check_ci_after_push.py"


def _load_module():
    hooks_dir = str(HOOK.parent)
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)  # pour que `from guard_destructive_git import …` résolve
    spec = importlib.util.spec_from_file_location("check_ci_after_push", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CI = _load_module()


@pytest.fixture
def tmp_path(tmp_path_git):
    """`git init` dans le `tmp_path` de pytest échoue quand la suite tourne
    élevée (ACL restrictive de `mode=0o700` sous Python 3.13+) — cf. conftest."""
    return tmp_path_git


def _git(args, cwd) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@test", "-c", "user.name=t"] + args,
        cwd=cwd, check=True, capture_output=True, timeout=30,
    )


# --------------------------------------------------------------------------- #
# build_message — pure, toutes branches (le cœur de la décision d'affichage)
# --------------------------------------------------------------------------- #

def test_build_message_silencieux_si_aucun_run():
    assert CI.build_message([], "abc1234") is None


def test_build_message_run_du_commit_pousse_en_cours():
    runs = [{"number": 5, "status": "in_progress", "conclusion": None,
             "sha": "abc1234", "name": "CI", "url": "http://x/5"}]
    msg = CI.build_message(runs, "abc1234")
    assert "⏳" in msg and "#5" in msg and "in_progress" in msg


def test_build_message_run_du_commit_pousse_rouge():
    runs = [{"number": 6, "status": "completed", "conclusion": "failure",
             "sha": "abc1234", "name": "CI", "url": "http://x/6"}]
    msg = CI.build_message(runs, "abc1234")
    assert "⚠️" in msg and "ROUGE" in msg and "http://x/6" in msg


def test_build_message_run_du_commit_pousse_vert():
    runs = [{"number": 7, "status": "completed", "conclusion": "success",
             "sha": "abc1234", "name": "CI", "url": "http://x/7"}]
    msg = CI.build_message(runs, "abc1234")
    assert msg == "✅ CI verte sur le commit poussé (abc1234) : run #7."


def test_build_message_run_du_commit_pousse_conclusion_atypique():
    runs = [{"number": 8, "status": "completed", "conclusion": "neutral",
             "sha": "abc1234", "name": "CI", "url": "http://x/8"}]
    msg = CI.build_message(runs, "abc1234")
    assert "`neutral`" in msg and "#8" in msg


def test_build_message_commit_pas_encore_enregistre_dernier_run_en_cours():
    runs = [{"number": 9, "status": "queued", "conclusion": None,
             "sha": "def5678", "name": "CI", "url": "http://x/9"}]
    msg = CI.build_message(runs, "abc1234", recheck="RECHECK_CMD")
    assert "n'est pas encore enregistré" in msg
    assert "#9" in msg and "queued" in msg and "RECHECK_CMD" in msg


def test_build_message_commit_pas_encore_enregistre_dernier_run_rouge():
    runs = [{"number": 10, "status": "completed", "conclusion": "cancelled",
             "sha": "def5678", "name": "CI", "url": "http://x/10"}]
    msg = CI.build_message(runs, "abc1234")
    assert "ATTENTION" in msg and "#10" in msg and "cancelled" in msg


def test_build_message_commit_pas_encore_enregistre_dernier_run_vert():
    runs = [{"number": 11, "status": "completed", "conclusion": "success",
             "sha": "def5678", "name": "CI", "url": "http://x/11"}]
    msg = CI.build_message(runs, "abc1234")
    assert "Dernier run connu : #11 vert" in msg


# --------------------------------------------------------------------------- #
# Tokenisation d'un `git push` réel — mêmes pièges que guard_destructive_git
# --------------------------------------------------------------------------- #

def test_git_push_tokens_forme_simple():
    assert CI._git_push_tokens("git push") == []


def test_git_push_tokens_avec_remote_et_branche():
    assert CI._git_push_tokens("git push origin main") == ["origin", "main"]


def test_git_push_tokens_saute_les_affectations_var_value():
    assert CI._git_push_tokens("FOO=1 git push --force origin main") == [
        "--force", "origin", "main"
    ]


def test_git_push_tokens_none_si_pas_un_push():
    assert CI._git_push_tokens("git status") is None
    assert CI._git_push_tokens("git pull") is None
    assert CI._git_push_tokens("npm test") is None


def test_git_push_tokens_none_si_dry_run():
    assert CI._git_push_tokens("git push --dry-run origin main") is None
    assert CI._git_push_tokens("git push -n") is None


def test_git_push_tokens_none_si_quotes_desequilibrees():
    assert CI._git_push_tokens('git push "origin') is None


def test_push_target_defauts_et_refspec():
    assert CI._push_target([]) == ("origin", None)
    assert CI._push_target(["origin"]) == ("origin", None)
    assert CI._push_target(["origin", "main"]) == ("origin", "main")
    assert CI._push_target(["origin", "HEAD:main"]) == ("origin", "main")
    assert CI._push_target(["--force", "origin", "main"]) == ("origin", "main")


def test_owner_repo_https_et_ssh():
    assert CI._owner_repo("https://github.com/acme/demo.git") == ("acme", "demo")
    assert CI._owner_repo("https://github.com/acme/demo") == ("acme", "demo")
    assert CI._owner_repo("git@github.com:acme/demo.git") == ("acme", "demo")


def test_owner_repo_none_si_pas_github_ou_absent():
    assert CI._owner_repo("https://gitlab.com/acme/demo.git") is None
    assert CI._owner_repo(None) is None
    assert CI._owner_repo("") is None


def test_detect_push_ignore_un_push_dans_un_corps_de_heredoc():
    """Le piège nommé dans le docstring du hook : un commit qui *décrit* la
    commande via un heredoc ne doit pas être pris pour un push réel."""
    cmd = "git commit -F - <<'EOF'\nDocumente git push --force ici.\nEOF"
    data = {"tool_input": {"command": cmd}}
    assert CI._detect_push(data) is None


def test_detect_push_trouve_un_push_apres_un_operateur():
    data = {"tool_input": {"command": "pytest -q && git push origin main"}}
    assert CI._detect_push(data) == ["origin", "main"]


def test_detect_push_none_sans_tool_input_ou_commande_vide():
    assert CI._detect_push({}) is None
    assert CI._detect_push({"tool_input": {"command": ""}}) is None


# --------------------------------------------------------------------------- #
# main() — _github_token/_latest_runs monkeypatchés : jamais de vrai réseau
# --------------------------------------------------------------------------- #

def test_main_emet_une_alerte_rouge_sur_le_commit_pousse(tmp_path, monkeypatch, capsys):
    _git(["init", "-q"], tmp_path)
    _git(["remote", "add", "origin", "https://github.com/acme/demo.git"], tmp_path)
    _git(["commit", "--allow-empty", "-m", "x"], tmp_path)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, timeout=10,
    ).stdout.strip()

    monkeypatch.setattr(CI, "_github_token", lambda cwd: "fake-token")
    monkeypatch.setattr(
        CI, "_latest_runs",
        lambda owner, repo, branch, token, limit=5: [
            {"number": 42, "status": "completed", "conclusion": "failure",
             "sha": sha[:7], "name": "CI", "url": "http://example/42"}
        ],
    )
    payload = {"tool_input": {"command": "git push origin main"}, "cwd": str(tmp_path)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    CI.main()

    out = json.loads(capsys.readouterr().out)
    assert "ROUGE" in out["systemMessage"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert out["hookSpecificOutput"]["additionalContext"] == out["systemMessage"]


def test_main_signale_l_absence_de_jeton_puis_se_tait(tmp_path, monkeypatch, capsys):
    """Sans jeton, le hook DIT qu'il ne peut pas lire la CI — une fois par 24 h.

    Ce test encodait l'inverse jusqu'au 2026-09-10 (« silencieux sans token ») :
    c'était le contrat d'origine, et il s'est retourné contre son propre but.
    Mesuré ce jour-là sur ce dépôt : remote SSH + dépôt privé = aucun
    identifiant HTTPS ne peut exister, donc `git credential fill` ne rend
    jamais rien et le hook ne s'était JAMAIS exprimé depuis son installation.
    Le silence, prévu pour une panne passagère, couvrait en fait une
    impossibilité permanente — et c'est ainsi que le lint CI a pu ne rien
    mesurer pendant sept semaines sans que personne le voie.

    Les DEUX moitiés comptent : on le dit (sinon l'angle mort revient), et on
    ne le répète pas (sinon c'est la friction qu'on débranche)."""
    _git(["init", "-q"], tmp_path)
    _git(["remote", "add", "origin", "https://github.com/acme/demo.git"], tmp_path)
    monkeypatch.setattr(CI, "_github_token", lambda cwd: None)
    # Marqueur isolé : sinon un run précédent (ou une vraie séance sur ce poste)
    # aurait déjà consommé la fenêtre de 24 h et le test passerait à tort.
    monkeypatch.setattr(CI, "_MARQUEUR_AVIS", tmp_path / "marqueur.txt")
    payload = {"tool_input": {"command": "git push"}, "cwd": str(tmp_path)}

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    CI.main()
    premier = capsys.readouterr().out
    assert premier, "le hook se tait alors qu'il ne peut PAS lire la CI"
    charge = json.loads(premier)
    assert "GH_TOKEN" in charge["systemMessage"], "le message ne dit pas quoi faire"
    assert charge["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    CI.main()
    assert capsys.readouterr().out == "", (
        "le hook repete l'avis au push suivant : c'est la friction qui le fera "
        "debrancher"
    )


def test_le_jeton_vient_d_abord_de_l_environnement(tmp_path, monkeypatch):
    """`GH_TOKEN` / `GITHUB_TOKEN` sont le seul chemin NON INTERACTIF, donc le
    seul qui puisse aboutir dans un hook. Ils doivent être consultés AVANT
    `git credential fill`, qui part sinon interroger un Credential Manager
    qui n'a personne en face."""
    _git(["init", "-q"], tmp_path)
    appels = []
    monkeypatch.setattr(
        CI.subprocess, "run",
        lambda *a, **k: appels.append(a) or pytest.fail("credential fill appele"),
    )
    monkeypatch.setenv("GH_TOKEN", "  jeton-de-test  ")
    assert CI._github_token(str(tmp_path)) == "jeton-de-test", "espaces non retires"
    assert appels == []

    monkeypatch.delenv("GH_TOKEN")
    monkeypatch.setenv("GITHUB_TOKEN", "second-nom")
    assert CI._github_token(str(tmp_path)) == "second-nom"

    # Une variable VIDE ne compte pas : elle doit laisser sa chance au repli,
    # pas court-circuiter la recherche avec une chaîne blanche.
    monkeypatch.setenv("GITHUB_TOKEN", "   ")
    replis = []

    class _Resultat:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(
        CI.subprocess, "run", lambda *a, **k: replis.append(k) or _Resultat()
    )
    assert CI._github_token(str(tmp_path)) is None
    assert len(replis) == 1, "une variable vide a court-circuite le repli"
    # Et le repli n'ouvre JAMAIS d'invite : dans un hook, personne n'y répond.
    env = replis[0]["env"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"


# --------------------------------------------------------------------------- #
# Contrat en sous-processus réel — silence garanti sans toucher au réseau
# --------------------------------------------------------------------------- #

def _run_hook(payload) -> subprocess.CompletedProcess:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK)], input=raw, capture_output=True, text=True, timeout=30,
    )


def test_silencieux_si_pas_un_push(tmp_path):
    r = _run_hook({"tool_input": {"command": "pytest -q"}, "cwd": str(tmp_path)})
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_fail_open_sur_stdin_invalide():
    r = _run_hook("pas du json")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_silencieux_si_remote_non_github(tmp_path):
    """N'atteint jamais `_github_token`/le réseau : `_owner_repo` rend None
    avant — déterministe quel que soit le credential store de la machine."""
    _git(["init", "-q"], tmp_path)
    _git(["remote", "add", "origin", "https://gitlab.com/acme/demo.git"], tmp_path)
    _git(["commit", "--allow-empty", "-m", "x"], tmp_path)
    r = _run_hook({"tool_input": {"command": "git push origin main"}, "cwd": str(tmp_path)})
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_silencieux_si_pas_de_remote_du_tout(tmp_path):
    _git(["init", "-q"], tmp_path)
    _git(["commit", "--allow-empty", "-m", "x"], tmp_path)
    r = _run_hook({"tool_input": {"command": "git push"}, "cwd": str(tmp_path)})
    assert r.returncode == 0
    assert r.stdout.strip() == ""
