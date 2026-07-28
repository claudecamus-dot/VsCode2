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
    assert calls[0]["system"] == synthese_ai.global_system(synthese_ai._axes_par_defaut())
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
        assert call["system"] == synthese_ai.global_system(synthese_ai._axes_par_defaut())
        assert f"(extrait {i}/3)" in call["prompt"]
    reduce_call = calls[3]
    assert reduce_call["system"] == synthese_ai.global_reduce_system(synthese_ai._axes_par_defaut())
    assert "Synthèse partielle 1/3" in reduce_call["prompt"]
    assert "- partiel 2" in reduce_call["prompt"]
    # Le résultat vient de l'appel de réduction (le 4e).
    assert result["contexte"] == "- partiel 4"


def test_la_matiere_libre_d_un_axe_sur_mesure_atteint_le_prompt():
    """Trouvé en revue adversariale (2026-07-28) : `_global_material_blocks` construisait
    la table des libellés d'axes puis ne la lisait JAMAIS — sa boucle restait figée sur
    les 5 clés historiques. Or `Interview.repartition` est produit sur un schéma construit
    depuis les axes de la mission : la matière recueillie en entretien libre pour un axe
    SUR MESURE n'atteignait donc jamais le prompt, et la rubrique ressortait vide sans
    que rien ne le signale — un second producteur, indépendant, du symptôme
    « rubrique vide » corrigé par ailleurs dans `_clean_global`."""
    class _Axe:
        def __init__(self, key, label):
            self.key, self.label, self.hint = key, label, ""

    axes = [_Axe("contexte", "Contexte & historique"),
            _Axe("outillage_donnees", "Outillage & données")]
    interview = SimpleNamespace(interviewee_name="Marc Dubois")
    repartition = {
        "contexte": "- Croissance rapide.",
        "outillage_donnees": "- Quatre tableurs concurrents, aucun ne fait foi.",
    }

    blocs = synthese_ai._global_material_blocks([], [(interview, repartition)], axes)
    texte = "\n".join(blocs)
    assert "Quatre tableurs concurrents" in texte, "matière de l'axe sur mesure perdue"
    # Le libellé courant de l'axe, pas un intitulé figé : le prompt système apparie
    # clé et libellé, les deux doivent concorder.
    assert "Outillage & données :" in texte
    assert "Contexte & historique :" in texte

    # Répartition ne portant QUE des clés hors axes (axe supprimé depuis) : pas de bloc
    # réduit à son en-tête — trompeur pour le modèle et payé au tronçonnage.
    orphelin = synthese_ai._global_material_blocks(
        [], [(interview, {"axe_supprime": "matière orpheline"})], axes
    )
    assert orphelin == []


def test_une_rubrique_rendue_en_liste_est_aplatie_et_non_jetee(monkeypatch: pytest.MonkeyPatch):
    """Cause racine de la « synthèse amputée » (mesurée contre un Ollama réel le
    2026-07-28) : le modèle par défaut rend une LISTE d'objets sous-thème/facteur
    là où le schéma attend une chaîne, et la garde str-sinon-"" de `_clean_global`
    la jetait EN SILENCE — la rubrique arrivait vide à l'écran alors que le modèle
    l'avait bien produite. Même défaut que celui corrigé côté SWOT le 2026-07-21 :
    on aplatit, on ne jette pas."""
    _capture_calls(
        monkeypatch,
        result={
            "contexte": "- du contexte",
            "culture_adn": ["Rituels", "Entraide"],
            "forces_succes": [
                {"sous_theme": "Outils de suivi", "facteur": "- éclatés entre quatre tableurs"},
            ],
            "points_amelioration": {"a": "réunions trop longues"},
            "aspirations": "",
        },
    )
    result = synthese_ai.generate_global_synthesis(_mission(), _material(1))
    assert result["contexte"] == "- du contexte"  # une chaîne reste INTACTE
    assert result["culture_adn"] == "- Rituels\n- Entraide"
    assert result["forces_succes"] == "- Outils de suivi — éclatés entre quatre tableurs"
    assert result["points_amelioration"] == "- réunions trop longues"
    assert result["aspirations"] == ""


def test_une_chaine_a_sous_themes_n_est_pas_transformee_en_puces(monkeypatch: pytest.MonkeyPatch):
    """Garde-fou du correctif lui-même : le prompt demande des sous-thèmes NOMMÉS
    suivis de puces. Aplatir aussi les chaînes transformerait chaque titre de
    sous-thème en puce et écraserait cette structure."""
    texte = "Outillage\n- quatre tableurs\n\nRituels\n- réunions trop longues"
    _capture_calls(monkeypatch, result={k: texte for k in synthese_ai.global_keys(
        synthese_ai._axes_par_defaut())})
    result = synthese_ai.generate_global_synthesis(_mission(), _material(1))
    assert result["contexte"] == texte


def test_clean_global_coerce_les_types_inattendus(monkeypatch: pytest.MonkeyPatch):
    """Ollama garantit du JSON valide, pas la forme du schéma (leçon
    `_safe_str` du 2026-07-17) : une valeur inexploitable (nombre, None) devient
    un champ vide, jamais un crash `.strip()`. Depuis le 2026-07-28, en revanche,
    un dict/une liste est APLATI plutôt que jeté (cf.
    `test_une_rubrique_rendue_en_liste_est_aplatie_et_non_jetee`)."""
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
        "contexte": "- objet inattendu",
        "culture_adn": "ok",
        "forces_succes": "",
        "points_amelioration": "",
        "aspirations": "- aspiration",
    }
