"""Tests unitaires du map-reduce de la synthèse globale (2026-07-18) :
`synthese_ai.generate_global_synthesis` découpe la matière en tronçons aux
frontières de thème/entretien libre, synthétise chaque tronçon puis fusionne
via un appel de réduction dédié — cause du timeout réel observé le 2026-07-17
sur poste CPU (un seul prompt géant dépassait `OLLAMA_TIMEOUT` et la fenêtre
de contexte). `_call_claude` est monkeypatché : aucun appel réseau.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import synthese_ai


def _mission(name="Mission Test"):
    return SimpleNamespace(name=name)


def _theme(title, rows_by_label):
    """Un thème à une question par (label → réponses) — assez pour le prompt."""
    questions = [
        SimpleNamespace(id=i, label=label)
        for i, label in enumerate(rows_by_label, start=1)
    ]
    by_question = {
        q.id: [{"interviewee": "Alice", "role": None, "value": None, "text": text}]
        for q, text in zip(questions, rows_by_label.values())
    }
    return SimpleNamespace(title=title, questions=questions), by_question


def _material(n_themes: int, words_per_answer: int = 5):
    material = []
    for i in range(1, n_themes + 1):
        answer = " ".join(f"mot{j}" for j in range(words_per_answer))
        theme, by_question = _theme(f"Thème {i}", {f"Question {i} ?": answer})
        material.append((theme, by_question, []))
    return material


def _capture_calls(monkeypatch, result=None):
    calls = []

    def fake_call(system, prompt, schema, json_hint, **kwargs):
        calls.append({"system": system, "prompt": prompt})
        return result or {
            "contexte": f"- partiel {len(calls)}",
            "culture_adn": "- culture",
            "forces_succes": "- force",
            "points_amelioration": "- point",
            "aspirations": "- aspiration",
        }

    monkeypatch.setattr(synthese_ai, "_call_claude", fake_call)
    return calls


def test_chunk_blocks_ne_coupe_jamais_un_bloc():
    blocks = ["a " * 10, "b " * 10, "c " * 10]
    chunks = synthese_ai._chunk_blocks([b.strip() for b in blocks], max_words=25)
    assert chunks == [
        [blocks[0].strip(), blocks[1].strip()],
        [blocks[2].strip()],
    ]
    # Un bloc seul plus long que le budget forme son propre tronçon (jamais coupé).
    assert synthese_ai._chunk_blocks(["x " * 50], max_words=10) == [["x " * 50]]
    assert synthese_ai._chunk_blocks([], max_words=10) == [[]]


def test_global_synthesis_mission_courte_un_seul_appel(monkeypatch: pytest.MonkeyPatch):
    calls = _capture_calls(monkeypatch)
    result = synthese_ai.generate_global_synthesis(_mission(), _material(2))
    assert len(calls) == 1  # chemin court inchangé : pas de reduce
    assert calls[0]["system"] == synthese_ai.GLOBAL_SYSTEM
    assert "MISSION : Mission Test" in calls[0]["prompt"]
    assert "Thème 2" in calls[0]["prompt"]
    assert result["contexte"] == "- partiel 1"


def test_global_synthesis_mission_longue_map_puis_reduce(monkeypatch: pytest.MonkeyPatch):
    calls = _capture_calls(monkeypatch)
    # Budget minuscule : chaque thème (~10 mots) devient son propre tronçon.
    monkeypatch.setattr(synthese_ai, "ollama_chunk_max_words", lambda: 8)
    result = synthese_ai.generate_global_synthesis(_mission(), _material(3))
    assert len(calls) == 4  # 3 map + 1 reduce
    for i, call in enumerate(calls[:3], start=1):
        assert call["system"] == synthese_ai.GLOBAL_SYSTEM
        assert f"(extrait {i}/3)" in call["prompt"]
    reduce_call = calls[3]
    assert reduce_call["system"] == synthese_ai.GLOBAL_REDUCE_SYSTEM
    assert "Synthèse partielle 1/3" in reduce_call["prompt"]
    assert "- partiel 2" in reduce_call["prompt"]
    # Le résultat vient de l'appel de réduction (le 4e).
    assert result["contexte"] == "- partiel 4"


def test_clean_global_coerce_les_types_inattendus(monkeypatch: pytest.MonkeyPatch):
    """Ollama garantit du JSON valide, pas la forme du schéma (leçon
    `_safe_str` du 2026-07-17) : un dict imbriqué là où une chaîne était
    attendue devient un champ vide, jamais un crash `.strip()` sur dict."""
    _capture_calls(
        monkeypatch,
        result={
            "contexte": {"nested": "objet inattendu"},
            "culture_adn": "  ok  ",
            "forces_succes": None,
            "points_amelioration": 42,
            "aspirations": "- aspiration",
        },
    )
    result = synthese_ai.generate_global_synthesis(_mission(), _material(1))
    assert result == {
        "contexte": "",
        "culture_adn": "ok",
        "forces_succes": "",
        "points_amelioration": "",
        "aspirations": "- aspiration",
    }
