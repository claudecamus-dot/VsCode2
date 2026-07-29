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
import subprocess
import sys
from pathlib import Path

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
