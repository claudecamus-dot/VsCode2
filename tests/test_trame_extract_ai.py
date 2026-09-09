"""Extraction IA d'une trame depuis un document libre — finding
audit-technique risque_technique du 2026-09-09 (`.claude/audits/VSCode2.json`
côté hub) : « `trame_extract_ai.py` (96 lignes) n'est exercé par AUCUN test ;
c'est le seul module IA du projet dans ce cas ». Le seul test qui passait par
son mode IA envoyait un `.docx` corrompu, dont l'exception est levée EN AMONT
dans `extract_text_bytes` — `extract_trame_from_text` n'était donc jamais
appelée, ni en succès ni en échec.

Couverture minimale du chemin nominal et des cas d'erreur, sans réseau ni
Ollama : `call_ai_json` est remplacée, comme le font les autres tests de
modules IA du dépôt. Ce qui est vérifié ici n'est pas la qualité du prompt mais
le contrat de la fonction : ce qu'elle rend au routeur, et ce qu'elle lève.
"""
from __future__ import annotations

import pytest

from app.importers.docx_trame import ParsedTrame
from app.services import trame_extract_ai
from app.services.trame_extract_ai import (
    TrameExtractAIError,
    extract_trame_from_text,
)


def _reponse_ia(monkeypatch: pytest.MonkeyPatch, data: dict) -> list[dict]:
    """Remplace l'appel IA et rend la liste des appels observés, pour vérifier
    ce qui lui est réellement transmis (et qu'elle n'est PAS appelée quand la
    garde d'entrée doit couper avant)."""
    appels: list[dict] = []

    def _faux_call(system, prompt, schema, json_hint, *, max_tokens, error_cls):
        appels.append(
            {"system": system, "prompt": prompt, "max_tokens": max_tokens,
             "error_cls": error_cls}
        )
        return data

    monkeypatch.setattr(trame_extract_ai, "call_ai_json", _faux_call)
    return appels


def test_chemin_nominal_rend_une_trame_utilisable_par_le_routeur(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appels = _reponse_ia(
        monkeypatch,
        {
            "themes": [
                {
                    "title": "  Contexte  ",
                    "questions": [
                        {"label": "  Quel est le contexte ?  ", "help": " a preciser "},
                        {"label": "Depuis quand ?"},
                    ],
                }
            ]
        },
    )

    trame = extract_trame_from_text("Un document de cadrage", name="Trame client")

    assert isinstance(trame, ParsedTrame)
    assert trame.name == "Trame client"
    assert [t.title for t in trame.themes] == ["Contexte"]
    questions = trame.themes[0].questions
    assert [q.label for q in questions] == ["Quel est le contexte ?", "Depuis quand ?"]
    # `help` absent du JSON ne doit pas donner None : le gabarit et le modèle
    # attendent une chaîne.
    assert [q.help for q in questions] == ["a preciser", ""]
    # Le type de question par défaut est celui du parser heuristique : les deux
    # sources doivent fusionner à l'identique dans la trame.
    assert {q.qtype for q in questions} == {"open"}
    # L'erreur passée à l'appel IA est bien celle du module : c'est elle qui
    # permet au routeur de distinguer une extraction de trame d'une synthèse.
    assert appels[0]["error_cls"] is TrameExtractAIError
    assert appels[0]["max_tokens"] == trame_extract_ai.MAX_TOKENS
    assert appels[0]["prompt"] == "Un document de cadrage"


def test_un_theme_sans_question_exploitable_est_ecarte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un thème vide (ou dont toutes les questions ont un libellé blanc) créerait
    dans la trame une section sur laquelle l'écran d'entretien n'a rien à
    poser."""
    _reponse_ia(
        monkeypatch,
        {
            "themes": [
                {"title": "Vide", "questions": []},
                {"title": "Blanches", "questions": [{"label": "   "}]},
                {"title": "", "questions": [{"label": "Une vraie question ?"}]},
            ]
        },
    )

    trame = extract_trame_from_text("Un document")

    # Seul le troisième survit — et son titre manquant reçoit le repli, jamais
    # une chaîne vide qui rendrait un onglet sans nom.
    assert [t.title for t in trame.themes] == ["Thème"]


def test_document_vide_refuse_avant_tout_appel_ia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le refus doit précéder l'appel : payer un aller-retour IA pour un
    document blanc coûte des minutes et ne peut rien rendre."""
    appels = _reponse_ia(monkeypatch, {"themes": []})

    with pytest.raises(TrameExtractAIError) as excinfo:
        extract_trame_from_text("   \n\t  ")

    assert "vide" in str(excinfo.value).lower()
    assert appels == []


def test_un_echec_de_l_appel_ia_remonte_tel_quel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`call_ai_json` lève l'`error_cls` qu'on lui passe : le routeur ne doit
    voir qu'un `TrameExtractAIError`, jamais une exception de transport dont le
    message n'est pas destiné à l'écran."""

    def _boom(system, prompt, schema, json_hint, *, max_tokens, error_cls):
        raise error_cls("Service IA injoignable.")

    monkeypatch.setattr(trame_extract_ai, "call_ai_json", _boom)

    with pytest.raises(TrameExtractAIError):
        extract_trame_from_text("Un document de cadrage")


def test_une_reponse_ia_sans_cle_themes_rend_une_trame_vide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le modèle peut rendre un JSON valide au regard du schéma mais sans
    matière. Rendre une trame vide (et non lever) est le comportement voulu :
    le routeur affiche « aucun thème détecté », l'utilisateur garde son
    document."""
    _reponse_ia(monkeypatch, {})

    trame = extract_trame_from_text("Un document")

    assert trame.themes == []
    assert trame.name == "Trame importée (IA)"
