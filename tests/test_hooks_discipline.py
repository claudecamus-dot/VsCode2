"""Tests-contrat des hooks de discipline (.claude/hooks/) — créés le 2026-07-29
en appliquant les constats superviseur arbitrés du jour. Jusqu'ici ces hooks
n'avaient AUCUN test (le constat `sync-canon` — un commit dispositif sans test
cassant la suite — vaut aussi pour eux) : chaque hook est exercé ici comme un
sous-processus réel, JSON sur stdin, exactement comme Claude Code l'invoque.

Les filets « reliquat de séance » et « validations en attente » vivent dans le
CANON du hub (scan_transcripts.py, synchronisé le même jour — arbre_sale /
runs_a_solder), pas ici : une première implémentation locale dans le hook remind
a été retirée le jour même pour ne pas dupliquer le canon en divergeant
(seuil 24 h, exclusions du churn généré). Cf. tests/test_agent_supervision.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
REMIND = REPO / ".claude" / "hooks" / "remind_revue_increment.py"
WARN = REPO / ".claude" / "hooks" / "warn_verif_before_commit.py"


def _run_hook(script: Path, payload) -> subprocess.CompletedProcess:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(script)],
        input=raw, capture_output=True, text=True, timeout=30,
    )


def _context(result: subprocess.CompletedProcess) -> str:
    """additionalContext émis par le hook (chaîne vide si silencieux)."""
    if not result.stdout.strip():
        return ""
    data = json.loads(result.stdout)
    return data.get("hookSpecificOutput", {}).get("additionalContext", "")


def _git(args, cwd) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@test", "-c", "user.name=t"] + args,
        cwd=cwd, check=True, capture_output=True, timeout=30,
    )


def _transcript(tmp_path: Path, commands: list) -> Path:
    """Transcript de session minimal : une ligne JSONL par tool_use Bash."""
    path = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({"message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}
        ]}})
        for cmd in commands
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# remind_revue_increment — rappel de discipline (les filets git/validations
# vivent dans le canon scan_transcripts.py, cf. docstring du module)
# --------------------------------------------------------------------------- #

def test_remind_rappelle_la_discipline(tmp_path):
    ctx = _context(_run_hook(REMIND, {"cwd": str(tmp_path)}))
    assert "Discipline qualité" in ctx
    assert "revue-increment" in ctx


def test_remind_fail_open_sur_stdin_invalide(tmp_path):
    r = _run_hook(REMIND, "pas du json")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


# --------------------------------------------------------------------------- #
# warn_verif_before_commit — zone app/ (message pytest) + zone dispositif
# --------------------------------------------------------------------------- #

def _repo_avec_stage(tmp_path: Path, relpath: str) -> Path:
    _git(["init", "-q"], tmp_path)
    f = tmp_path / Path(relpath)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# contenu\n", encoding="utf-8")
    _git(["add", relpath], tmp_path)
    return tmp_path


def _payload(tmp_path: Path, transcript: Path = None) -> dict:
    return {
        "tool_input": {"command": 'git commit -m "x"'},
        "cwd": str(tmp_path),
        "transcript_path": str(transcript) if transcript else "",
    }


def test_warn_commit_dispositif_sans_contrat_avertit_et_nomme_les_deux_fichiers(tmp_path):
    """Constat superviseur `sync-canon` 2026-07-29 (arbitré) : 5eb121b (+23 lignes
    log_run.py, 0 test) a cassé un test-contrat, vu seulement à la revue suivante.
    Le gate exécutable : commit touchant le dispositif sans trace des
    fichiers-contrat cette session → avertissement les nommant."""
    _repo_avec_stage(tmp_path, ".claude/orchestration/nouveau_script.py")
    r = _run_hook(WARN, _payload(tmp_path))
    ctx = _context(r)
    assert "tests/test_agent_orchestration.py" in ctx
    assert "tests/test_agent_supervision.py" in ctx


def test_warn_commit_dispositif_silencieux_si_fichiers_contrat_joues(tmp_path):
    _repo_avec_stage(tmp_path, ".claude/supervision/outil.py")
    transcript = _transcript(
        tmp_path, ["pytest tests/test_agent_supervision.py tests/test_agent_orchestration.py -q"])
    r = _run_hook(WARN, _payload(tmp_path, transcript))
    assert r.stdout.strip() == ""


def test_warn_commit_dispositif_silencieux_si_suite_complete_jouee(tmp_path):
    """`pytest -q` (suite complète, aucun chemin tests/...) inclut de fait les
    fichiers-contrat : pas d'avertissement."""
    _repo_avec_stage(tmp_path, ".claude/hooks/un_hook.py")
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    r = _run_hook(WARN, _payload(tmp_path, transcript))
    assert r.stdout.strip() == ""


def test_warn_commit_app_sans_verif_cite_pytest_pas_npm_test(tmp_path):
    """Régression du message hérité du portage VSCode1 : `_VERIF_BASH` détecte
    pytest mais le texte disait encore « npm test » — corrigé le 2026-07-29."""
    _repo_avec_stage(tmp_path, "app/services/quelque_chose.py")
    ctx = _context(_run_hook(WARN, _payload(tmp_path)))
    assert "pytest" in ctx
    assert "npm test" not in ctx


def test_warn_commit_app_et_dispositif_cumule_les_deux_avertissements(tmp_path):
    _repo_avec_stage(tmp_path, "app/services/quelque_chose.py")
    autre = tmp_path / ".claude" / "orchestration" / "outil.py"
    autre.parent.mkdir(parents=True, exist_ok=True)
    autre.write_text("# x\n", encoding="utf-8")
    _git(["add", "."], tmp_path)
    ctx = _context(_run_hook(WARN, _payload(tmp_path)))
    assert "code applicatif" in ctx
    assert "fichiers-contrat" in ctx


def test_warn_commit_hors_zones_reste_silencieux(tmp_path):
    _repo_avec_stage(tmp_path, "docs/notes.md")
    r = _run_hook(WARN, _payload(tmp_path))
    assert r.stdout.strip() == ""


# --------------------------------------------------------------------------- #
# guard_destructive_git — le garde-fou de l'ARBRE, pas seulement de l'historique
# --------------------------------------------------------------------------- #
# Ajoutés le 2026-09-02, sur incident réel : un sous-agent de revue a joué
# `git checkout --` sur deux templates pour mesurer le code d'avant, effaçant
# les correctifs non commités de la session appelante. Le hook ne connaissait
# alors que `push --force` et `reset --hard` — deux commandes qui touchent
# l'HISTORIQUE, quand le travail perdu ce jour-là était dans l'ARBRE.
#
# Le hook n'avait par ailleurs AUCUN test, alors que deux autres hooks
# l'importent (`check_ci_after_push`, `warn_verif_before_commit`) : une
# régression dans son tokenizer les cassait tous les trois en silence.

GUARD = REPO / ".claude" / "hooks" / "guard_destructive_git.py"


def _refus(commande: str, cwd: Path | None = None) -> str:
    """Motif de refus rendu par le garde-fou (chaîne vide s'il laisse passer)."""
    payload = {"tool_name": "Bash", "tool_input": {"command": commande}}
    env = None
    if cwd is not None:
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)}
    r = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload), capture_output=True, text=True, timeout=30, env=env,
    )
    if not r.stdout.strip():
        return ""
    sortie = json.loads(r.stdout)["hookSpecificOutput"]
    assert sortie["permissionDecision"] == "deny"
    return sortie["permissionDecisionReason"]


@pytest.fixture
def depot(tmp_path):
    """Un dépôt minimal portant un fichier suivi — de quoi distinguer un chemin
    réel d'un nom de branche."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "ecran.html").write_text("<p>x</p>\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "commande",
    [
        "git checkout -- app/ecran.html",
        "git checkout HEAD -- app/ecran.html",
        "git checkout -- .",
        "git checkout app/ecran.html",
        "cd /tmp && git checkout -- app/ecran.html",
        'eval "git checkout -- app/ecran.html"',
    ],
)
def test_le_garde_fou_refuse_d_ecraser_un_fichier_de_l_arbre(commande, depot):
    """La commande exacte de l'incident, et ses formes voisines. Chacune écrase
    les modifications non commitées du fichier, sans copie de secours."""
    motif = _refus(commande, depot)
    assert motif, "commande destructive laissée passer : %s" % commande
    assert "git show HEAD:" in motif, (
        "le refus doit dire comment lire le code d'avant sans rien détruire — "
        "sinon il empêche un besoin légitime de revue sans offrir d'issue"
    )


@pytest.mark.parametrize(
    "commande",
    [
        "git checkout main",
        "git checkout -b nouvelle-branche",
        "git checkout -B nouvelle-branche",
        "git status",
        "git diff app/ecran.html",
        "git show HEAD:app/ecran.html",
        "git restore --staged app/ecran.html",
    ],
)
def test_le_garde_fou_laisse_passer_ce_qui_ne_detruit_rien(commande, depot):
    """La contrepartie, sans laquelle le garde-fou serait inutilisable : changer
    de branche, en créer une, lire un fichier ou DÉSINDEXER n'écrase aucun
    travail. `git restore --staged` en particulier ne touche que l'index — il
    sert précisément à retirer d'un commit un fichier hors périmètre."""
    assert _refus(commande, depot) == "", "commande légitime bloquée : %s" % commande


def test_le_garde_fou_refuse_restore_du_fichier_mais_pas_de_l_index(depot):
    """`git restore` sans `--staged` écrit dans le fichier ; avec, il ne touche
    que l'index. Les deux formes ne diffèrent que d'un drapeau, et se confondre
    coûte un fichier."""
    assert _refus("git restore app/ecran.html", depot)
    assert _refus("git restore --staged app/ecran.html", depot) == ""
    # Cumulées, elles écrasent bien le fichier : le drapeau `--staged` ne suffit
    # plus à rendre la commande inoffensive.
    assert _refus("git restore --staged --worktree app/ecran.html", depot)


@pytest.mark.parametrize("commande", ["git clean -f", "git clean -fd", "git clean -xdf"])
def test_le_garde_fou_refuse_de_supprimer_les_fichiers_non_suivis(commande, depot):
    """Le danger propre à `git clean` : il emporte les fichiers NEUFS, pas
    encore ajoutés — un test qu'on vient d'écrire, typiquement. Les formes
    groupées comptent : c'est `-fd` qu'on écrit en pratique, pas `-f -d`."""
    motif = _refus(commande, depot)
    assert motif and "git clean -n" in motif


def test_le_garde_fou_laisse_lister_avant_de_supprimer(depot):
    """`git clean -n` ne fait que lister : c'est l'issue que le refus propose,
    elle doit rester ouverte."""
    assert _refus("git clean -n", depot) == ""


@pytest.mark.parametrize("commande", ["git stash drop", "git stash clear"])
def test_le_garde_fou_refuse_de_jeter_une_remise(commande, depot):
    """Une remise supprimée n'est plus récupérable par aucune commande
    ordinaire — contrairement à `git stash push`, qui reste autorisé."""
    assert _refus(commande, depot)


def test_le_garde_fou_laisse_remiser_et_reprendre(depot):
    assert _refus("git stash push -m wip", depot) == ""
    assert _refus("git stash pop", depot) == ""


def test_les_garde_fous_d_historique_tiennent_toujours(depot):
    """Non-régression des deux règles d'origine : l'extension à l'arbre ne doit
    pas les avoir déplacées."""
    assert _refus("git push --force", depot)
    assert _refus("git reset --hard HEAD~1", depot)
    assert _refus("git push --force-with-lease", depot) == ""


def test_un_message_de_commit_qui_DECRIT_la_commande_passe(depot):
    """Le piège déjà payé sur ce hook : un message de commit qui parle de la
    commande gardée n'est pas un appel à cette commande. Le corps d'un heredoc
    est de la donnée."""
    commande = (
        "git commit -F - <<'EOF'\n"
        "Garde-fou : git checkout -- <fichier> est desormais bloque\n"
        "EOF"
    )
    assert _refus(commande, depot) == ""
