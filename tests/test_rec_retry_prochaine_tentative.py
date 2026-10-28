"""CR-27: `prochaineTentative` and `afficherCompletude` live in rec_retry.js
(one copy for record.html and record_libre.html). Executed under node."""
from __future__ import annotations

import pytest

try:
    from test_repartition_live import _node
    from test_transcription_occupee_execution import REC_RETRY
except ImportError:
    from tests.test_repartition_live import _node
    from tests.test_transcription_occupee_execution import REC_RETRY

_SCRIPT = r"""
const window = {};
%(rec_retry)s
const P = window.recRetryPlan;
const banner = { hidden: true }, texte = { textContent: '' };
const out = {
  retry: [0, 1, 2].map(a => P.prochaineTentative('retry', a)),
  transient: [0, 1, 2, 3].map(a => P.prochaineTentative('transient', a)),
  autres: ['busy', 'timeout', 'no_speech', 'definitif'].map(n => P.prochaineTentative(n, 0)),
};
P.afficherCompletude({ statut: 'incomplet', blocs_vides: [2] }, banner, texte);
out.visible = { hidden: banner.hidden, texte: texte.textContent };
out.attendu = P.messageCompletude({ statut: 'incomplet', blocs_vides: [2] });
P.afficherCompletude(null, banner, texte);
out.masque = { hidden: banner.hidden, texte: texte.textContent };
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def r() -> dict:
    return _node(_SCRIPT % {"rec_retry": REC_RETRY.read_text(encoding="utf-8")})


def test_prochaine_tentative_delai_numero_et_total(r: dict) -> None:
    assert r["retry"] == [
        {"delai": 2000, "numero": 2, "total": 3},
        {"delai": 6000, "numero": 3, "total": 3},
        None,
    ]
    assert r["transient"] == [
        {"delai": 2000, "numero": 2, "total": 4},
        {"delai": 6000, "numero": 3, "total": 4},
        {"delai": 15000, "numero": 4, "total": 4},
        None,
    ]
    assert r["autres"] == [None] * 4


def test_afficher_completude_pose_et_masque_le_bandeau(r: dict) -> None:
    assert r["attendu"] and r["visible"] == {"hidden": False, "texte": r["attendu"]}
    assert r["masque"] == {"hidden": True, "texte": ""}
