"""SP-1 : un bloc relancé automatiquement puis réussi est un échec masqué, COMPTÉ et
VISIBLE (JS exécuté sous node, jamais lu en texte brut)."""
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
console.log(JSON.stringify({
  deux: P.messageCompletude({ statut: 'complet', blocs_rejoues: [2, 5] }),
  un: P.messageCompletude({ statut: 'complet', blocs_rejoues: [4] }),
  vide: P.messageCompletude({ statut: 'complet', blocs_rejoues: [] }),
  absent: P.messageCompletude({ statut: 'complet' }),
  nul: P.messageCompletude(null),
  objet_vide: P.messageCompletude({}),
  non_liste: P.messageCompletude({ statut: 'complet', blocs_rejoues: 'x' }),
  non_evaluable: P.messageCompletude({ statut: 'non_evaluable', blocs_rejoues: [1] }),
  incomplet: P.messageCompletude({ statut: 'incomplet', blocs_vides: [0], blocs_rejoues: [3] }),
}));
"""


@pytest.fixture(scope="module")
def msg() -> dict:
    return _node(_SCRIPT % {"rec_retry": REC_RETRY.read_text(encoding="utf-8")})


def test_complet_avec_blocs_relances_affiche_le_compte(msg: dict) -> None:
    assert msg["deux"] == "2 blocs audio relancés automatiquement, puis réussis."
    assert msg["un"] == "1 bloc audio relancé automatiquement, puis réussi."
    assert "rejoué" not in msg["deux"]


@pytest.mark.parametrize("cle", ["vide", "absent", "nul", "objet_vide", "non_liste"])
def test_rien_a_dire_reste_vide(msg: dict, cle: str) -> None:
    assert msg[cle] == ""


def test_non_evaluable_avec_relances_montre_le_compte(msg: dict) -> None:
    assert msg["non_evaluable"] == "1 bloc audio relancé automatiquement, puis réussi."


def test_incomplet_garde_son_message(msg: dict) -> None:
    assert msg["incomplet"].startswith("Transcription incomplète : bloc sans texte : 1")
