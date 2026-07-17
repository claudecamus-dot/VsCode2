"""Tests de l'étage 1 du superviseur d'agents (.claude/supervision/scan_transcripts.py).

Le script est exercé en subprocess avec des chemins surchargés par env — aucun accès aux
vrais transcripts ni au vrai wiki.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / ".claude" / "supervision" / "scan_transcripts.py"


def _line(skill=None, subagent=None, ts="2026-07-17T10:00:00Z"):
    if skill:
        blk = {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": skill}}
    else:
        blk = {"type": "tool_use", "id": "t1", "name": "Task", "input": {"subagent_type": subagent}}
    return json.dumps({"timestamp": ts, "message": {"content": [blk]}}) + "\n"


def _run(tmp_path, args=()):
    env = dict(
        os.environ,
        AGENT_SUPERVISION_TRANSCRIPTS=str(tmp_path / "transcripts"),
        AGENT_SUPERVISION_STATE=str(tmp_path / "state.json"),
        AGENT_SUPERVISION_WIKI_PAGE=str(tmp_path / "page.md"),
        AGENT_SUPERVISION_WIKI_INDEX=str(tmp_path / "index.md"),
        AGENT_SUPERVISION_WIKI_HTML=str(tmp_path / "wiki.html"),
    )
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env, capture_output=True, text=True, timeout=60,
    )


def test_scan_counts_and_generates_page_and_index(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(
        _line(skill="run-dev-server") + _line(subagent="Explore"), encoding="utf-8"
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "+2 evenement" in result.stdout

    page = (tmp_path / "page.md").read_text(encoding="utf-8")
    assert "`run-dev-server` | projet | 1" in page
    assert "`Explore` | 1" in page
    assert "Ne pas éditer à la main" in page

    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "## TODO agents" in index
    assert "technical/agents-supervision.md" in index


def test_incremental_scan_only_reads_new_lines_and_is_idempotent(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    transcript = tdir / "s1.jsonl"
    transcript.write_text(_line(skill="run-dev-server"), encoding="utf-8")
    _run(tmp_path)

    # Rescan sans nouveauté : 0 événement, pas de double comptage.
    result = _run(tmp_path)
    assert "+0 evenement" in result.stdout

    # Ajout d'une invocation : le compteur cumule sans relire l'ancien.
    with transcript.open("a", encoding="utf-8") as fh:
        fh.write(_line(skill="run-dev-server", ts="2026-07-18T09:00:00Z"))
    result = _run(tmp_path)
    assert "+1 evenement" in result.stdout
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["skills"]["run-dev-server"]["n"] == 2
    assert state["skills"]["run-dev-server"]["last"].startswith("2026-07-18")

    # La section TODO de l'index est remplacée entre marqueurs, jamais dupliquée.
    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert index.count("## TODO agents") == 1


def test_wiki_html_block_replaced_between_markers_only(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(_line(skill="run-dev-server"), encoding="utf-8")

    # Sans marqueurs : la page n'est pas touchée.
    html = tmp_path / "wiki.html"
    html.write_text("<html><body><p>page</p></body></html>", encoding="utf-8")
    result = _run(tmp_path)
    assert "sans marqueurs" in result.stdout
    assert html.read_text(encoding="utf-8") == "<html><body><p>page</p></body></html>"

    # Avec marqueurs : le bloc est injecté, le reste de la page intact, et c'est idempotent.
    html.write_text(
        "<html><body><p>avant</p>\n"
        "<!-- TODO-AGENTS-HTML:START -->placeholder<!-- TODO-AGENTS-HTML:END -->\n"
        "<p>après</p></body></html>",
        encoding="utf-8",
    )
    _run(tmp_path)
    txt = html.read_text(encoding="utf-8")
    assert "run-dev-server" in txt and "<p>avant</p>" in txt and "<p>après</p>" in txt
    assert "placeholder" not in txt
    _run(tmp_path)
    assert html.read_text(encoding="utf-8").count('id="agents-supervision"') == 1


def test_partial_trailing_line_is_not_consumed(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    complete = _line(skill="run-dev-server")
    partial = _line(skill="pptx-deck").rstrip("\n")  # sans \n final : ligne en cours d'écriture
    (tdir / "s1.jsonl").write_text(complete + partial, encoding="utf-8")
    _run(tmp_path)
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["skills"]["run-dev-server"]["n"] == 1
    assert "pptx-deck" not in state["skills"]

    # La ligne se termine : elle est comptée au scan suivant.
    with (tdir / "s1.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("\n")
    _run(tmp_path)
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["skills"]["pptx-deck"]["n"] == 1
