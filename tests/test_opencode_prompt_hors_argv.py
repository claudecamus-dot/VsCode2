"""Les donnees client ne transitent plus par l'argv du sous-processus opencode
(audit securite 2026-10-06) : elles sont jointes par `--file`."""
from __future__ import annotations

import os

import pytest

from app.services import openhub_agents


class _Res:
    returncode = 0
    stdout = "ok"
    stderr = ""


@pytest.mark.parametrize("fonction, args", [
    (openhub_agents._run_opencode_agent, ("analyste",)),
    (openhub_agents._run_opencode_skill, ("synthese",)),
])
def test_prompt_joint_par_fichier_et_pas_en_argv(monkeypatch, fonction, args) -> None:
    vu = {}

    def faux_run(cmd, **kw):
        vu["cmd"] = list(cmd)
        f = cmd[cmd.index("--file") + 1]
        vu["contenu"] = open(f, encoding="utf-8").read()
        vu["fichier"] = f
        return _Res()

    monkeypatch.setattr(openhub_agents.subprocess, "run", faux_run)
    secret = "SECRET-CLIENT-123"
    assert fonction(*args, f"donnees {secret}") == "ok"
    assert not any(secret in a for a in vu["cmd"])
    assert secret in vu["contenu"]
    assert not os.path.exists(vu["fichier"]), "fichier temporaire non supprime"
