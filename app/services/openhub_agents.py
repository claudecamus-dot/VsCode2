from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..models import AgentResult, Mission

ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = ROOT / ".opencode" / "agents"
SKILLS_DIR = ROOT / ".opencode" / "skills"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
_FIELD_RE = re.compile(r"^([A-Za-z0-9_\-]+):\s*(.*)$", re.M)


def _parse_frontmatter(text: str) -> dict[str, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}

    content = match.group(1)
    metadata: dict[str, str] = {}
    for key, value in _FIELD_RE.findall(content):
        cleaned = value.strip().strip('"\'')
        metadata[key] = cleaned
    return metadata


def _agent_id(path: Path) -> str:
    relative = path.relative_to(AGENTS_DIR)
    return relative.with_suffix("").as_posix()


def _load_agent(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    metadata = _parse_frontmatter(text)
    return {
        "id": metadata.get("id") or _agent_id(path),
        "label": metadata.get("label") or path.stem,
        "description": metadata.get("description") or "Aucun résumé disponible.",
        "mode": metadata.get("mode") or "primary",
        "source": _agent_id(path),
    }


def list_available_agents() -> list[dict[str, Any]]:
    if not AGENTS_DIR.exists():
        return []

    agents: list[dict[str, Any]] = []
    for path in sorted(AGENTS_DIR.rglob("*.md")):
        agent = _load_agent(path)
        agents.append(agent)
    return sorted(agents, key=lambda item: item["label"].lower())


def list_available_skills() -> list[dict[str, Any]]:
    if not SKILLS_DIR.exists():
        return []

    skills: list[dict[str, Any]] = []
    for path in sorted(SKILLS_DIR.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        metadata = _parse_frontmatter(text)
        label = metadata.get("name") or path.stem
        description = metadata.get("description") or "Aucun résumé disponible."
        skills.append(
            {
                "id": metadata.get("name") or path.stem,
                "label": label,
                "description": description,
                "source": path.relative_to(ROOT).as_posix(),
            }
        )
    return sorted(skills, key=lambda item: item["label"].lower())


def get_agent(agent_id: str) -> dict[str, Any]:
    for candidate in list_available_agents():
        if candidate["id"] == agent_id:
            return candidate
    raise KeyError(f"Agent inconnu : {agent_id}")


def get_skill(skill_id: str) -> dict[str, Any]:
    for candidate in list_available_skills():
        if candidate["id"] == skill_id:
            return candidate
    raise KeyError(f"Skill inconnu : {skill_id}")


def opencode_available() -> bool:
    return shutil.which("opencode") is not None


def _build_mission_summary(mission: Mission) -> str:
    lines = [f"Mission : {mission.name}"]
    if mission.client:
        lines.append(f"Client : {mission.client}")
    if mission.description:
        lines.append(f"Description : {mission.description}")

    if mission.trame:
        lines.append("\nThèmes :")
        for theme in mission.trame.themes:
            lines.append(f"- {theme.title} ({len(theme.questions)} question(s))")
    interview_count = len(mission.interviews)
    lines.append(f"\nEntretiens : {interview_count}")
    return "\n".join(lines)


def _run_opencode_agent(agent_id: str, prompt: str) -> str:
    try:
        executable = shutil.which("opencode") or "opencode"
        cmd = [executable, "run", "--agent", agent_id, prompt]
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=90,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return (
            f"Échec : délai d'exécution dépassé après {exc.timeout} secondes."
            f"\nCommande : {' '.join(cmd)}"
        )
    except OSError as exc:
        return f"Échec : impossible de lancer opencode : {exc}"

    if result.returncode != 0:
        stderr = result.stderr.strip()
        return (
            "Échec de l'invocation de l'agent OpenCode.\n"
            f"Code de retour : {result.returncode}\n"
            f"{stderr or "Aucune sortie d'erreur."}"
        )

    output = result.stdout.strip()
    if not output:
        output = "L'agent OpenCode n'a renvoyé aucune sortie."
    return output


def create_agent_result(
    db: Session, mission: Mission, result: dict[str, Any]
) -> AgentResult:
    agent_result = AgentResult(
        mission=mission,
        agent_id=result["agent_id"],
        agent_label=result["agent_label"],
        runtime_available=result["runtime_available"],
        output=result["output"],
    )
    db.add(agent_result)
    db.commit()
    db.refresh(agent_result)
    return agent_result


def invoke_agent(agent_id: str, mission: Mission) -> dict[str, Any]:
    agent = get_agent(agent_id)
    summary = _build_mission_summary(mission)

    if opencode_available():
        prompt = (
            f"Tu es l'agent OpenHub {agent['label']}. "
            "Analyse ces informations de mission et fournis une réponse adaptée en français.\n\n"
            f"{summary}"
        )
        output = _run_opencode_agent(agent_id, prompt)
    else:
        output = (
            f"Agent simulé : {agent['label']}\n\n{agent['description']}\n\n"
            f"Contexte du projet :\n{summary}\n\n"
            "Pour exécuter un agent réel, installez OpenCode et assurez-vous que la commande "
            "`opencode` est accessible depuis le PATH."
        )

    return {
        "agent_id": agent_id,
        "agent_label": agent["label"],
        "agent_description": agent["description"],
        "runtime_available": opencode_available(),
        "output": output,
    }


def _run_opencode_skill(skill_id: str, prompt: str) -> str:
    try:
        executable = shutil.which("opencode") or "opencode"
        cmd = [executable, "run", prompt]
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=90,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return (
            f"Échec : délai d'exécution dépassé après {exc.timeout} secondes."
            f"\nCommande : {' '.join(cmd)}"
        )
    except OSError as exc:
        return f"Échec : impossible de lancer opencode : {exc}"

    if result.returncode != 0:
        stderr = result.stderr.strip()
        return (
            "Échec de l'invocation du skill OpenCode.\n"
            f"Code de retour : {result.returncode}\n"
            f"{stderr or 'Aucune sortie d\'erreur.'}"
        )

    output = result.stdout.strip()
    if not output:
        output = "Le skill OpenCode n'a renvoyé aucune sortie."
    return output


def invoke_skill(skill_id: str, mission: Mission) -> dict[str, Any]:
    skill = get_skill(skill_id)
    summary = _build_mission_summary(mission)

    if opencode_available():
        prompt = (
            f"Tu es le skill OpenHub {skill['label']} ({skill_id}). "
            "Analyse ces informations de mission et fournis une réponse adaptée en français.\n\n"
            f"{summary}"
        )
        runtime_output = _run_opencode_skill(skill_id, prompt)
        output = (
            f"Skill OpenHub : {skill['label']}\n\n"
            f"Description : {skill['description']}\n\n"
            f"Contexte de mission :\n{summary}\n\n"
            f"Résultat OpenCode :\n{runtime_output}"
        )
    else:
        output = (
            f"Skill simulé : {skill['label']}\n\n{skill['description']}\n\n"
            f"Contexte du projet :\n{summary}\n\n"
            "Pour exécuter un skill réel, installez OpenCode et assurez-vous que la commande "
            "`opencode` est accessible depuis le PATH."
        )

    return {
        "skill_id": skill_id,
        "skill_label": skill["label"],
        "skill_description": skill["description"],
        "runtime_available": opencode_available(),
        "output": output,
    }
