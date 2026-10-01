"""T9 — les constats IA doivent citer des porteurs, sans nom de personne.

Option B (matière avec id seul, consigne explicite) + S (signal `sans_porteur` /
`non_cites`, jamais de rejet). Faux client IA déterministe ; pas de base.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import synthese_ai


def _libre(ids_noms):
    return [(SimpleNamespace(id=i, interviewee_name=n), {"contexte": f"Propos de {n}."})
            for i, n in ids_noms]


def _mission(*ids):
    return SimpleNamespace(name="M", interviews=[SimpleNamespace(id=i) for i in ids])


def _constat(libelle, ids):
    return {"axe": "contexte", "type": "consensus", "libelle": libelle, "interviewes": ids}


def test_matiere_avec_ids_sans_nom() -> None:
    assert synthese_ai._qui("Alix", 12, True) == "[E12]"
    blocs = synthese_ai._global_material_blocks([], _libre([(12, "Alix")]), avec_ids=True)
    assert "[E12]" in blocs[0] and "Alix" not in blocs[0].split("\n")[0]
    # le rôle reste (décision utilisateur)
    theme = SimpleNamespace(title="T", questions=[SimpleNamespace(id=1, label="Q")])
    rows = {1: [{"interviewee": "Alix", "interview_id": 12, "role": "DSI", "value": "oui"}]}
    bloc = synthese_ai._global_material_blocks([(theme, rows, [])], avec_ids=True)[0]
    assert "  - [E12] (DSI) : oui" in bloc and "Alix" not in bloc


def test_matiere_sans_ids_inchangee() -> None:
    assert synthese_ai._qui("Alix", 12, False) == "Alix"
    assert synthese_ai._qui("Alix", None, True) == "Alix"
    blocs = synthese_ai._global_material_blocks([], _libre([(12, "Alix")]))
    assert blocs == ["=== ENTRETIEN LIBRE : Alix ===\nContexte : Propos de Alix."]


def test_consigne_exige_au_moins_un_porteur_et_interdit_le_nom() -> None:
    systeme = synthese_ai.constats_system(synthese_ai._axes_par_defaut())
    for texte in (systeme, synthese_ai.CONSTATS_JSON_HINT):
        assert "interviewes" in texte
        assert "au moins un" in texte
        assert "nom de personne" in texte


def test_sans_porteur_et_non_cites_comptes(monkeypatch, caplog) -> None:
    monkeypatch.setattr(synthese_ai, "_call_claude", lambda *a, **k: {"constats": [
        _constat("Cap partagé", ["E1"]),
        _constat("Rythme contesté", []),
        _constat("Outils hétérogènes", ["E999"]),
    ]})
    with caplog.at_level("WARNING"):
        out = synthese_ai.generate_constats(
            _mission(1, 2, 3), [], _libre([(1, "A"), (2, "B"), (3, "C")]))
    assert len(out["constats"]) == 3, "signalés, jamais rejetés"
    assert out["sans_porteur"] == 2
    assert out["non_cites"] == [2, 3]
    assert "sans porteur" in caplog.text


def test_non_cites_vide_si_tous_cites(monkeypatch, caplog) -> None:
    monkeypatch.setattr(synthese_ai, "_call_claude", lambda *a, **k: {"constats": [
        _constat("Cap partagé", ["E1", "E2"])]})
    with caplog.at_level("WARNING"):
        out = synthese_ai.generate_constats(_mission(1, 2), [], _libre([(1, "A"), (2, "B")]))
    assert out["sans_porteur"] == 0 and out["non_cites"] == []
    assert "sans porteur" not in caplog.text
