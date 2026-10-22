"""P5 : la fiche entretien montre dans un vrai navigateur les passages audio relancés."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from test_e2e_relance_tranches import (  # noqa: F401  (fixture `nav` + helpers)
    _NAVIGATEUR, _python_sur_base, nav, pytestmark, serveur_uvicorn,
)

_SEED = r"""
import json
from app.db import SessionLocal, init_db
from app.models import AudioFileJob, Interview, Mission
init_db()
db = SessionLocal()
m = Mission(name="E2E bilan audio", is_draft=False)
db.add(m); db.flush()
iv = Interview(mission_id=m.id, interviewee_name="Témoin", mode="libre", status="done",
               structuration_status="fait", raw_transcript="Texte complet.")
db.add(iv); db.flush()
db.add(AudioFileJob(session_token="tok-e2e-bilan", filename="e2e.webm", status="done",
                    blocks=["a"], interview_id=iv.id,
                    completude={"statut": "complet", "blocs_rejoues": [1, 3], "blocs_vides": []}))
db.commit()
print(json.dumps({"iid": iv.id}))
"""


def test_la_fiche_montre_les_blocs_relances(tmp_path_factory, nav) -> None:
    dossier = tmp_path_factory.mktemp("e2e-bilan")
    iid = json.loads(_python_sur_base(dossier / "e2e.db", _SEED))["iid"]
    with serveur_uvicorn(dossier) as base:
        nav.naviguer(f"{base}/interviews/{iid}")
        assert not nav.erreurs_http(), nav.erreurs_http()
        texte = nav.texte()
        assert "Transcription complète." in texte, texte[:500]
        assert "2 passages audio relancés automatiquement, puis réussis." in texte
        assert nav.evaluer("document.getElementById('bilan-audio') !== null")
