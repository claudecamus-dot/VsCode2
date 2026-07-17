"""Tests de l'orchestrateur — étage O-A (journal log_run.py + hook orchestrator_gate.py)
et étage O-B (playbooks + générateur cycle-produit-bmad).

Scripts exercés en subprocess ; le journal est redirigé vers un chemin temporaire par env.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOG_RUN = ROOT / ".claude" / "orchestration" / "log_run.py"
GATE = ROOT / ".claude" / "hooks" / "orchestrator_gate.py"
PLAYBOOKS_DIR = ROOT / ".claude" / "orchestration" / "playbooks"
GEN_BMAD = ROOT / ".claude" / "orchestration" / "generate_bmad_playbook.py"


def _extract_playbook_json(path):
    """Récupère l'unique bloc ```json d'un fichier playbook."""
    m = re.search(r"```json\n(.*?)\n```", path.read_text(encoding="utf-8"), re.DOTALL)
    assert m, f"pas de bloc json dans {path.name}"
    return json.loads(m.group(1))


def _playbook_files():
    return sorted(p for p in PLAYBOOKS_DIR.glob("*.md") if p.name != "FORMAT.md")


def _log_run(tmp_path, payload, via_stdin=False):
    env = dict(os.environ, AGENT_ORCHESTRATION_RUNS=str(tmp_path / "runs.jsonl"))
    raw = json.dumps(payload, ensure_ascii=False)
    args = [sys.executable, str(LOG_RUN)] + ([] if via_stdin else [raw])
    return subprocess.run(
        args, input=raw if via_stdin else None,
        env=env, capture_output=True, text=True, timeout=30, encoding="utf-8",
    )


def _gate(prompt):
    return subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps({"prompt": prompt}),
        capture_output=True, text=True, timeout=30, encoding="utf-8",
    )


def test_log_run_appends_valid_run_with_ts(tmp_path):
    payload = {
        "demande": "revue design parallèle",
        "qualification": "orchestre",
        "plan": [{"etape": "revue", "agent": "Explore", "mode": "parallele", "modele": "haiku"}],
        "resultat": "succes",
        "reprises": 0,
    }
    result = _log_run(tmp_path, payload)
    assert result.returncode == 0, result.stderr
    assert "1 etape(s)" in result.stdout
    lines = (tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    run = json.loads(lines[0])
    assert run["demande"] == "revue design parallèle"
    assert run["ts"]  # ajouté automatiquement

    # Deuxième run via stdin : append, pas d'écrasement.
    result = _log_run(tmp_path, payload, via_stdin=True)
    assert result.returncode == 0
    assert len((tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip().splitlines()) == 2


def test_log_run_rejects_missing_fields_and_bad_qualification(tmp_path):
    assert _log_run(tmp_path, {"qualification": "orchestre"}).returncode == 1
    assert _log_run(tmp_path, {"demande": "x", "qualification": "nimporte"}).returncode == 1
    assert not (tmp_path / "runs.jsonl").exists()


def test_gate_injects_grid_except_for_slash_commands():
    result = _gate("corrige le bug d'export PPT et vérifie le rendu")
    assert result.returncode == 0
    assert "agent-orchestrator" in result.stdout

    result = _gate("/code-review high")
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# --- Étage O-B : playbooks + générateur ---------------------------------------

VALID_MODES = {"cascade", "parallele", "asynchrone"}
VALID_STATUTS = {"eprouve", "jamais-joue"}
VALID_CONTRAT_TYPES = {"deterministe", "reel", "llm"}


@pytest.mark.parametrize("path", _playbook_files(), ids=lambda p: p.stem)
def test_playbook_structure_valide(path):
    pb = _extract_playbook_json(path)
    assert pb["nom"] == path.stem, "le nom du playbook doit être le nom du fichier"
    assert pb["statut"] in VALID_STATUTS
    assert pb["etapes"], "un playbook a au moins une étape"
    ids = [e["id"] for e in pb["etapes"]]
    assert len(ids) == len(set(ids)), "ids d'étape uniques"
    for e in pb["etapes"]:
        assert e["mode"] in VALID_MODES
        assert e["contrat"]["type"] in VALID_CONTRAT_TYPES
        assert e["contrat"].get("critere")
        # Une étape parallèle borne son fan-out (garde-fou §5).
        if e["mode"] == "parallele":
            assert 1 <= e.get("fan_out_max", 0) <= 4


def test_playbook_source_genere_marque_le_script():
    """Un playbook généré déclare sa source (garde-fou 'ne pas éditer à la main')."""
    pb = _extract_playbook_json(PLAYBOOKS_DIR / "cycle-produit-bmad.md")
    assert pb["source"].startswith("genere:")


def test_playbooks_de_dev_se_terminent_par_revue_increment():
    """Leçon superviseur rendue structurelle : la DoD clôt tout playbook de dev."""
    for name in ("dev-verifie", "cycle-produit-bmad"):
        pb = _extract_playbook_json(PLAYBOOKS_DIR / f"{name}.md")
        derniere = pb["etapes"][-1]
        assert derniere["agent"] == "revue-increment"
        # Étape terminale = garde-fou commit, donc checkpoint non-false.
        assert derniere["checkpoint"]


def test_generateur_bmad_est_deterministe_et_synchro(tmp_path):
    """Regénérer produit exactement le .md versionné (sinon il a dérivé)."""
    out = tmp_path / "cycle-produit-bmad.md"
    env = dict(os.environ, PLAYBOOK_OUT=str(out))
    result = subprocess.run(
        [sys.executable, str(GEN_BMAD)],
        env=env, capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    versionne = (PLAYBOOKS_DIR / "cycle-produit-bmad.md").read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == versionne, (
        "le playbook BMAD versionné a dérivé du générateur — regénérer et committer"
    )


def test_generateur_bmad_resout_le_dag(tmp_path):
    """Sélection = required + fermeture des preceded-by ; ordre par phase."""
    out = tmp_path / "cycle-produit-bmad.md"
    subprocess.run(
        [sys.executable, str(GEN_BMAD)],
        env=dict(os.environ, PLAYBOOK_OUT=str(out)),
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    pb = _extract_playbook_json(out)
    agents = [e["agent"] for e in pb["etapes"]]
    # Le brief (preceded-by de la PRD) est tiré même s'il n'est pas required.
    assert "bmad-product-brief" in agents
    # Le cycle story est complet et code-review (EXTRA) présent.
    for expected in ("bmad-prd", "bmad-architecture", "bmad-dev-story", "bmad-code-review"):
        assert expected in agents
    # bmad-ux (ni required ni preceded d'un required) est exclu.
    assert "bmad-ux" not in agents
