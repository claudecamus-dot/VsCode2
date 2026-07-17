"""Tests de l'entretien libre (incr.9) : extraction IA (tours de parole +
répartition dans les 5 catégories de synthèse globale) et flux HTTP complet
— écran d'entrée à 3 choix, mission brouillon, capture/revue/enregistrement,
finalisation (nommer ou rattacher à une mission existante sans trame),
détail d'un entretien libre, intégration à la synthèse globale de mission.

Complète `test_interview_extract_ai.py` (même style de test unitaire pour
l'extraction IA) et `test_mission_trame_flow.py` (même style de test HTTP
pour le flux mission/entretien).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import Interview, InterviewTurn, Mission
from app.services import interview_libre_extract_ai


def setup_module() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def teardown_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# --------------------------------------------------------------------------- #
# interview_libre_extract_ai — deux étapes distinctes (US9.16), unitaire,
# call_ai_json monkeypatché (pas d'appel réseau).
# --------------------------------------------------------------------------- #
def test_extract_turns_returns_turns_and_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_libre_extract_ai, "call_ai_json",
        lambda *a, **k: {
            "turns": [
                {"interlocuteur": "Consultant·e", "question": "Comment ça se passe ?", "remarque": "", "section_title": "Ouverture"},
                {"interlocuteur": "Marc Dupont", "question": "", "remarque": "On travaille en silo.", "section_title": ""},
            ],
            "identite": {
                "interviewee_name": "Marc Dupont", "interviewee_role": "", "interviewee_entity": "",
            },
        },
    )
    result = interview_libre_extract_ai.extract_turns_from_text("transcription brute")
    assert result["turns"] == [
        {"interlocuteur": "Consultant·e", "question": "Comment ça se passe ?", "remarque": None, "section_title": "Ouverture"},
        {"interlocuteur": "Marc Dupont", "question": None, "remarque": "On travaille en silo.", "section_title": None},
    ]
    assert result["identity"]["interviewee_name"] == "Marc Dupont"


def test_extract_turns_empty_transcript_raises() -> None:
    with pytest.raises(interview_libre_extract_ai.InterviewLibreExtractAIError):
        interview_libre_extract_ai.extract_turns_from_text("   ")


def test_extract_turns_drops_incomplete_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un tour sans interlocuteur, ou ni question ni remarque, n'est pas un
    vrai tour de parole exploitable — écarté plutôt que gardé à moitié vide."""
    monkeypatch.setattr(
        interview_libre_extract_ai, "call_ai_json",
        lambda *a, **k: {
            "turns": [
                {"interlocuteur": "", "question": "Question orpheline", "remarque": ""},
                {"interlocuteur": "Alice", "question": "", "remarque": ""},
                {"interlocuteur": "Bruno", "question": "Une vraie question", "remarque": ""},
            ],
            "identite": {"interviewee_name": "", "interviewee_role": "", "interviewee_entity": ""},
        },
    )
    result = interview_libre_extract_ai.extract_turns_from_text("texte")
    assert len(result["turns"]) == 1
    assert result["turns"][0]["interlocuteur"] == "Bruno"


def test_extract_turns_no_turns_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_libre_extract_ai, "call_ai_json",
        lambda *a, **k: {
            "turns": [],
            "identite": {"interviewee_name": "", "interviewee_role": "", "interviewee_entity": ""},
        },
    )
    with pytest.raises(interview_libre_extract_ai.InterviewLibreExtractAIError):
        interview_libre_extract_ai.extract_turns_from_text("texte sans tour détectable")


def test_extract_turns_recovers_from_bare_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    """Régression (crash réel `'list' object has no attribute 'get'` sur
    `split_03.weba`, 2026-07-17) : Ollama peut renvoyer un tableau JSON nu au
    lieu de `{"turns": [...]}`. On le traite comme la liste des tours plutôt
    que de planter."""
    monkeypatch.setattr(
        interview_libre_extract_ai, "call_ai_json",
        lambda *a, **k: [
            {"interlocuteur": "Consultant·e", "question": "Ça va ?", "remarque": "", "section_title": "Ouverture"},
            {"interlocuteur": "Léa Martin", "question": "", "remarque": "Tout roule.", "section_title": ""},
        ],
    )
    result = interview_libre_extract_ai.extract_turns_from_text("transcription brute")
    assert [t["interlocuteur"] for t in result["turns"]] == ["Consultant·e", "Léa Martin"]
    assert result["identity"]["interviewee_name"] == ""


def test_extract_turns_survives_malformed_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama peut glisser des éléments non-dict (chaîne, liste) dans `turns`
    ou renvoyer `repartition`/`identite` sous une forme inattendue — coercés
    plutôt que de faire planter `row.get(...)`."""
    monkeypatch.setattr(
        interview_libre_extract_ai, "call_ai_json",
        lambda *a, **k: {
            "turns": [
                "ceci n'est pas un objet de tour",
                ["non plus"],
                {"interlocuteur": "Bruno", "question": "Une vraie question", "remarque": ""},
            ],
            "identite": ["forme inattendue"],
        },
    )
    result = interview_libre_extract_ai.extract_turns_from_text("texte")
    assert len(result["turns"]) == 1
    assert result["turns"][0]["interlocuteur"] == "Bruno"
    assert result["identity"]["interviewee_name"] == ""


def test_generate_repartition_from_turns_returns_repartition_and_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        interview_libre_extract_ai, "call_ai_json",
        lambda *a, **k: {
            "repartition": {
                "contexte": "- Contexte évoqué",
                "culture_adn": "- Culture évoquée",
                "forces_succes": "",
                "points_amelioration": "- Silos entre équipes",
                "aspirations": "",
            },
            "resume": "- Résumé de l'entretien.",
        },
    )
    turns = [
        {"interlocuteur": "Consultant·e", "question": "Comment ça se passe ?", "remarque": None, "section_title": "Ouverture"},
        {"interlocuteur": "Marc Dupont", "question": None, "remarque": "On travaille en silo.", "section_title": None},
    ]
    result = interview_libre_extract_ai.generate_repartition_from_turns(turns)
    assert result["repartition"]["points_amelioration"] == "- Silos entre équipes"
    assert result["repartition"]["forces_succes"] == ""
    assert result["resume"] == "- Résumé de l'entretien."


def test_generate_repartition_from_turns_empty_raises() -> None:
    with pytest.raises(interview_libre_extract_ai.InterviewLibreExtractAIError):
        interview_libre_extract_ai.generate_repartition_from_turns([])


def test_generate_repartition_survives_bare_list_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Même classe de bug côté étape 2 : si Ollama renvoie une liste (ou une
    `repartition` non-dict) au lieu de `{"repartition": {...}, "resume": ...}`,
    on dégrade proprement vers une répartition vide (éditable à la main) au
    lieu de planter."""
    monkeypatch.setattr(
        interview_libre_extract_ai, "call_ai_json",
        lambda *a, **k: ["forme totalement inattendue"],
    )
    turns = [{"interlocuteur": "Marc", "question": None, "remarque": "Un constat.", "section_title": None}]
    result = interview_libre_extract_ai.generate_repartition_from_turns(turns)
    assert result["repartition"] == {key: "" for key in interview_libre_extract_ai.REPARTITION_KEYS}
    assert result["resume"] == ""


# --------------------------------------------------------------------------- #
# Map-reduce (US9.17) : un texte/une liste de tours trop longs pour un seul
# appel IA (fenêtre de contexte Ollama) doivent être découpés, traités par
# tronçon, puis fusionnés — vérifié ici en comptant les appels IA réels
# plutôt qu'en supposant que le découpage a eu lieu.
# --------------------------------------------------------------------------- #
def test_extract_turns_chunks_long_transcript_and_merges_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_CHUNK_MAX_WORDS", "50")
    # Deux paragraphes de ~60 mots chacun -> dépasse la limite de 50 mots
    # par tronçon -> doit produire 2 tronçons distincts.
    paragraphe_1 = "Consultant : " + ("mot " * 60)
    paragraphe_2 = "Interviewé : " + ("mot " * 60)
    transcript = paragraphe_1 + "\n\n" + paragraphe_2

    calls = []

    def fake_call_ai_json(system, prompt, schema, json_hint, **kwargs):
        calls.append(prompt)
        index = len(calls)
        return {
            "turns": [
                {"interlocuteur": f"Personne{index}", "question": "", "remarque": f"Propos {index}", "section_title": ""},
            ],
            "identite": (
                {"interviewee_name": "Trouvé au 2e tronçon", "interviewee_role": "", "interviewee_entity": ""}
                if index == 2 else
                {"interviewee_name": "", "interviewee_role": "", "interviewee_entity": ""}
            ),
        }

    monkeypatch.setattr(interview_libre_extract_ai, "call_ai_json", fake_call_ai_json)

    result = interview_libre_extract_ai.extract_turns_from_text(transcript)

    assert len(calls) == 2, "le texte doit être découpé en 2 tronçons (2 appels IA)"
    assert [t["interlocuteur"] for t in result["turns"]] == ["Personne1", "Personne2"]
    # L'identité du 2e tronçon doit remonter puisque le 1er tronçon n'en a pas trouvé.
    assert result["identity"]["interviewee_name"] == "Trouvé au 2e tronçon"


def test_generate_repartition_chunks_many_turns_and_reduces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(interview_libre_extract_ai, "_CHUNK_MAX_TURNS", 2)
    turns = [
        {"interlocuteur": f"Personne{i}", "question": None, "remarque": f"Propos {i}", "section_title": None}
        for i in range(5)  # 5 tours, tronçons de 2 -> 3 groupes -> 3 appels map + 1 appel reduce
    ]

    calls = []

    def fake_call_ai_json(system, prompt, schema, json_hint, **kwargs):
        calls.append(prompt)
        if prompt.startswith("SYNTHÈSES PARTIELLES"):
            return {
                "repartition": {
                    "contexte": "- Contexte fusionné",
                    "culture_adn": "", "forces_succes": "",
                    "points_amelioration": "", "aspirations": "",
                },
                "resume": "- Résumé final fusionné.",
            }
        return {
            "repartition": {
                "contexte": "- Contexte partiel",
                "culture_adn": "", "forces_succes": "",
                "points_amelioration": "", "aspirations": "",
            },
            "resume": "- Résumé partiel.",
        }

    monkeypatch.setattr(interview_libre_extract_ai, "call_ai_json", fake_call_ai_json)

    result = interview_libre_extract_ai.generate_repartition_from_turns(turns)

    assert len(calls) == 4, "3 tronçons (map) + 1 fusion (reduce) attendus"
    assert result["repartition"]["contexte"] == "- Contexte fusionné"
    assert result["resume"] == "- Résumé final fusionné."


def test_generate_repartition_single_group_skips_reduce_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un nombre de tours qui tient dans un seul tronçon ne doit déclencher
    qu'un seul appel IA — pas de coût de fusion inutile."""
    turns = [
        {"interlocuteur": "Personne", "question": None, "remarque": "Propos", "section_title": None},
    ]
    calls = []

    def fake_call_ai_json(system, prompt, schema, json_hint, **kwargs):
        calls.append(prompt)
        return {
            "repartition": {
                "contexte": "- Contexte", "culture_adn": "", "forces_succes": "",
                "points_amelioration": "", "aspirations": "",
            },
            "resume": "- Résumé.",
        }

    monkeypatch.setattr(interview_libre_extract_ai, "call_ai_json", fake_call_ai_json)
    interview_libre_extract_ai.generate_repartition_from_turns(turns)
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# Écran d'entrée
# --------------------------------------------------------------------------- #
def test_entree_screen_lists_three_choices(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Entretien libre" in response.text
    assert "Entretien structuré" in response.text
    assert "Nouvelle mission" in response.text


# --------------------------------------------------------------------------- #
# Flux entretien libre bout en bout : mission brouillon -> enregistrement ->
# revue des questions/réponses -> synthèse -> confirmation -> finalisation
# (nouvelle mission). Deux appels IA distincts (US9.16) : extract_turns_from_text
# (étape 1) puis generate_repartition_from_turns (étape 2).
# --------------------------------------------------------------------------- #
_DEFAULT_TURNS = [
    {"interlocuteur": "Consultant·e", "question": "Comment ça se passe ?", "remarque": None, "section_title": "Ouverture"},
    {"interlocuteur": "Claire Rousseau", "question": None, "remarque": "Beaucoup de silos entre équipes.", "section_title": None},
]

_DEFAULT_REPARTITION = {
    "contexte": "- Contexte de test",
    "culture_adn": "- Culture de test",
    "forces_succes": "- Force de test",
    "points_amelioration": "- Silos entre équipes",
    "aspirations": "- Aspiration de test",
}


def _fake_extract_turns(turns=None, identity=None):
    turns = turns or _DEFAULT_TURNS
    identity = identity if identity is not None else {
        "interviewee_name": "", "interviewee_role": "", "interviewee_entity": "",
    }
    return lambda text: {"turns": turns, "identity": identity}


def _fake_generate_repartition(repartition=None, resume=None):
    repartition = repartition or _DEFAULT_REPARTITION
    resume = "- Résumé de test" if resume is None else resume
    return lambda turns: {"repartition": repartition, "resume": resume}


def _patch_libre_extract_ai(
    monkeypatch: pytest.MonkeyPatch, turns=None, identity=None, repartition=None, resume=None,
) -> None:
    """Patche les deux étapes IA du flux libre d'un coup — le cas courant où
    les tests ne veulent pas distinguer les deux appels."""
    monkeypatch.setattr(
        "app.routers.interviews.extract_turns_from_text",
        _fake_extract_turns(turns=turns, identity=identity),
    )
    monkeypatch.setattr(
        "app.routers.interviews.generate_repartition_from_turns",
        _fake_generate_repartition(repartition=repartition, resume=resume),
    )


def _post_libre_synthese(client: TestClient, mission_id: int, *, interviewee_name: str = "", turns=None):
    """Étape 2 du flux : POST vers /synthese avec les tours (déjà validés à
    l'étape 1) pour obtenir l'écran de synthèse avant confirmation."""
    turns = turns or _DEFAULT_TURNS
    return client.post(
        f"/missions/{mission_id}/interviews/record-libre/synthese",
        data={
            "interviewee_name": interviewee_name,
            "turn_interlocuteur": [t["interlocuteur"] for t in turns],
            "turn_question": [t["question"] or "" for t in turns],
            "turn_remarque": [t["remarque"] or "" for t in turns],
            "turn_section_title": [t["section_title"] or "" for t in turns],
        },
        follow_redirects=False,
    )


def test_libre_flow_creates_draft_then_finalise_as_new_mission(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = client.post("/entretiens/libre/nouveau", follow_redirects=False)
    assert response.status_code == 303
    record_url = response.headers["location"]
    assert record_url.endswith("/interviews/record-libre")
    mission_id = int(record_url.split("/")[2])

    session = SessionLocal()
    try:
        mission = session.get(Mission, mission_id)
        assert mission.is_draft is True
        assert mission.trame is None
    finally:
        session.close()

    response = client.get(record_url)
    assert response.status_code == 200
    assert "Entretien libre" in response.text

    _patch_libre_extract_ai(monkeypatch)
    response = client.post(
        record_url,
        data={
            "transcript": "Transcription libre simulée.",
            "interviewee_name": "Claire Rousseau",
            "interviewee_role": "RSSI",
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Revue des questions/réponses" in response.text
    assert "Beaucoup de silos entre équipes." in response.text

    response = _post_libre_synthese(client, mission_id, interviewee_name="Claire Rousseau")
    assert response.status_code == 200
    assert "Synthèse avant enregistrement" in response.text
    assert "Silos entre équipes" in response.text

    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/confirm",
        data={
            "interviewee_name": "Claire Rousseau",
            "interviewee_role": "RSSI",
            "turn_interlocuteur": ["Consultant·e", "Claire Rousseau"],
            "turn_question": ["Comment ça se passe ?", ""],
            "turn_remarque": ["", "Beaucoup de silos entre équipes."],
            "repartition_contexte": "- Contexte de test",
            "repartition_culture_adn": "- Culture de test",
            "repartition_forces_succes": "- Force de test",
            "repartition_points_amelioration": "- Silos entre équipes",
            "repartition_aspirations": "- Aspiration de test",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/missions/{mission_id}/finaliser"

    session = SessionLocal()
    try:
        interview = session.scalars(
            select(Interview).where(Interview.mission_id == mission_id)
        ).one()
        assert interview.mode == "libre"
        assert interview.status == "done"
        assert interview.repartition["points_amelioration"] == "- Silos entre équipes"
        turns = session.scalars(
            select(InterviewTurn).where(InterviewTurn.interview_id == interview.id)
            .order_by(InterviewTurn.position)
        ).all()
        assert len(turns) == 2
        assert turns[0].interlocuteur == "Consultant·e"
        assert turns[1].remarque == "Beaucoup de silos entre équipes."
        interview_id = interview.id
    finally:
        session.close()

    # La page mission (brouillon, sans trame) doit s'afficher sans planter,
    # et le détail de l'entretien libre aussi (pas de trame à parcourir).
    response = client.get(f"/missions/{mission_id}")
    assert response.status_code == 200
    assert "Mission brouillon" in response.text

    response = client.get(f"/interviews/{interview_id}")
    assert response.status_code == 200
    assert "Claire Rousseau" in response.text
    assert "Beaucoup de silos entre équipes." in response.text

    # Finalisation : nomme la mission.
    response = client.get(f"/missions/{mission_id}/finaliser")
    assert response.status_code == 200
    assert "Nouvelle mission" in response.text

    response = client.post(
        f"/missions/{mission_id}/finaliser",
        data={"action": "nommer", "name": "Audit SI — Client Test", "description": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/missions/{mission_id}"

    session = SessionLocal()
    try:
        mission = session.get(Mission, mission_id)
        assert mission.is_draft is False
        assert mission.name == "Audit SI — Client Test"
    finally:
        session.close()


def test_libre_flow_fills_identity_from_transcript_when_left_blank(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le cœur de la demande : si le nom/prénom, le rôle et l'équipe/
    département sont identifiés dans la transcription, ils doivent remplir
    les champs — sans que le consultant les ait tapés à la main avant
    l'enregistrement (US9.5)."""
    response = client.post("/entretiens/libre/nouveau", follow_redirects=False)
    mission_id = int(response.headers["location"].split("/")[2])

    _patch_libre_extract_ai(monkeypatch, identity={
        "interviewee_name": "Farida Benali",
        "interviewee_role": "Responsable RH",
        "interviewee_entity": "Direction des Ressources Humaines",
    })
    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "Bonjour, je suis Farida Benali, responsable RH..."},
        # Champs identité volontairement absents : rien de saisi à la main.
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert 'value="Farida Benali"' in response.text
    assert 'value="Responsable RH"' in response.text
    assert 'value="Direction des Ressources Humaines"' in response.text

    response = _post_libre_synthese(client, mission_id, interviewee_name="Farida Benali")
    assert response.status_code == 200

    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/confirm",
        data={
            "interviewee_name": "Farida Benali",
            "interviewee_role": "Responsable RH",
            "interviewee_entity": "Direction des Ressources Humaines",
            "turn_interlocuteur": ["Consultant·e", "Farida Benali"],
            "turn_question": ["Comment ça se passe ?", ""],
            "turn_remarque": ["", "Beaucoup de silos entre équipes."],
            "repartition_contexte": "- Contexte de test",
            "repartition_culture_adn": "- Culture de test",
            "repartition_forces_succes": "- Force de test",
            "repartition_points_amelioration": "- Silos entre équipes",
            "repartition_aspirations": "- Aspiration de test",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    session = SessionLocal()
    try:
        interview = session.scalars(
            select(Interview).where(Interview.mission_id == mission_id)
        ).one()
        assert interview.interviewee_name == "Farida Benali"
        assert interview.interviewee_role == "Responsable RH"
        assert interview.interviewee_entity == "Direction des Ressources Humaines"
    finally:
        session.close()


def test_libre_flow_manual_identity_wins_over_transcript_detection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une saisie manuelle explicite n'est jamais écrasée par ce que l'IA a
    cru comprendre de la transcription."""
    response = client.post("/entretiens/libre/nouveau", follow_redirects=False)
    mission_id = int(response.headers["location"].split("/")[2])

    _patch_libre_extract_ai(monkeypatch, identity={
        "interviewee_name": "Nom Mal Compris",
        "interviewee_role": "",
        "interviewee_entity": "",
    })
    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={
            "transcript": "Transcription ambiguë.",
            "interviewee_name": "Farida Benali",
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert 'value="Farida Benali"' in response.text
    assert "Nom Mal Compris" not in response.text


def test_libre_detail_edit_updates_turns_and_repartition(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = client.post("/entretiens/libre/nouveau", follow_redirects=False)
    mission_id = int(response.headers["location"].split("/")[2])
    _patch_libre_extract_ai(monkeypatch)
    client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "Transcription.", "interviewee_name": "Denis Roche"},
    )
    _post_libre_synthese(client, mission_id, interviewee_name="Denis Roche")
    client.post(
        f"/missions/{mission_id}/interviews/record-libre/confirm",
        data={
            "interviewee_name": "Denis Roche",
            "turn_interlocuteur": ["Consultant·e", "Denis Roche"],
            "turn_question": ["Comment ça se passe ?", ""],
            "turn_remarque": ["", "Beaucoup de silos entre équipes."],
            "repartition_contexte": "- Contexte de test",
            "repartition_culture_adn": "- Culture de test",
            "repartition_forces_succes": "- Force de test",
            "repartition_points_amelioration": "- Silos entre équipes",
            "repartition_aspirations": "- Aspiration de test",
        },
        follow_redirects=False,
    )

    session = SessionLocal()
    try:
        interview = session.scalars(
            select(Interview).where(Interview.mission_id == mission_id)
        ).one()
        interview_id = interview.id
        turn_ids = [t.id for t in sorted(interview.turns, key=lambda t: t.position)]
    finally:
        session.close()

    response = client.post(
        f"/interviews/{interview_id}/libre",
        data={
            "turn_id": [str(tid) for tid in turn_ids],
            "turn_interlocuteur": ["Consultant·e", "Denis Roche"],
            "turn_question": ["Comment ça se passe (corrigé) ?", ""],
            "turn_remarque": ["", "Silos corrigés à la relecture."],
            "repartition_contexte": "- Contexte corrigé",
            "repartition_culture_adn": "- Culture de test",
            "repartition_forces_succes": "- Force de test",
            "repartition_points_amelioration": "- Silos entre équipes",
            "repartition_aspirations": "- Aspiration de test",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    session = SessionLocal()
    try:
        interview = session.get(Interview, interview_id)
        assert interview.repartition["contexte"] == "- Contexte corrigé"
        turns = sorted(interview.turns, key=lambda t: t.position)
        assert turns[0].question == "Comment ça se passe (corrigé) ?"
        assert turns[1].remarque == "Silos corrigés à la relecture."
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Finalisation : rattachement à une mission existante.
# --------------------------------------------------------------------------- #
def _create_and_finish_libre_mission(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, interviewee: str
) -> int:
    """Crée une mission brouillon (entretien libre), enregistre un entretien,
    et retourne son id — sans la finaliser."""
    response = client.post("/entretiens/libre/nouveau", follow_redirects=False)
    mission_id = int(response.headers["location"].split("/")[2])
    _patch_libre_extract_ai(monkeypatch)
    client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "Transcription.", "interviewee_name": interviewee},
    )
    _post_libre_synthese(client, mission_id, interviewee_name=interviewee)
    client.post(
        f"/missions/{mission_id}/interviews/record-libre/confirm",
        data={
            "interviewee_name": interviewee,
            "turn_interlocuteur": ["Consultant·e"],
            "turn_question": ["Une question ?"],
            "turn_remarque": [""],
            "repartition_contexte": "- Contexte",
            "repartition_culture_adn": "- Culture",
            "repartition_forces_succes": "- Force",
            "repartition_points_amelioration": "- Point",
            "repartition_aspirations": "- Aspiration",
        },
    )
    return mission_id


def test_finaliser_rattache_entretien_libre_a_mission_existante(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_id = _create_and_finish_libre_mission(client, monkeypatch, "Premier Interviewé")
    client.post(
        f"/missions/{target_id}/finaliser",
        data={"action": "nommer", "name": "Mission Cible", "description": ""},
    )

    draft_id = _create_and_finish_libre_mission(client, monkeypatch, "Second Interviewé")

    # Une mission avec trame (créée classiquement) ne doit pas apparaître :
    # ce brouillon n'a pas de trame, mais une mission cible avec trame serait
    # de toute façon hors sujet ici (le filtre "sans trame" ne s'applique
    # qu'aux brouillons structurés) — on vérifie surtout que Mission Cible
    # est bien proposée.
    response = client.get(f"/missions/{draft_id}/finaliser")
    assert response.status_code == 200
    assert "Mission Cible" in response.text

    response = client.post(
        f"/missions/{draft_id}/finaliser",
        data={"action": "rattacher", "target_mission_id": str(target_id)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/missions/{target_id}"

    session = SessionLocal()
    try:
        assert session.get(Mission, draft_id) is None
        target = session.get(Mission, target_id)
        assert len(target.interviews) == 2
        names = {iv.interviewee_name for iv in target.interviews}
        assert names == {"Premier Interviewé", "Second Interviewé"}
    finally:
        session.close()

    # La page de la mission brouillon a disparu ; celle de la cible marche.
    assert client.get(f"/missions/{draft_id}").status_code == 404
    assert client.get(f"/missions/{target_id}").status_code == 200


def test_finaliser_structure_exclut_missions_avec_trame(client: TestClient) -> None:
    """Un brouillon structuré (choix 2, trame déjà rédigée) ne doit proposer
    en rattachement que des missions sans trame — une mission classique
    (créée via /missions, trame incluse) est donc exclue."""
    response = client.post(
        "/missions", data={"name": "Mission Classique", "description": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = client.post("/entretiens/structure/nouveau", follow_redirects=False)
    assert response.status_code == 303
    draft_mission_id = int(response.headers["location"].split("/")[2])

    session = SessionLocal()
    try:
        mission = session.get(Mission, draft_mission_id)
        assert mission.trame is not None
    finally:
        session.close()

    response = client.get(f"/missions/{draft_mission_id}/finaliser")
    assert response.status_code == 200
    # "Mission Classique" a une trame -> jamais éligible au rattachement d'un
    # brouillon structuré (qui porte déjà la sienne, cf. Mission.trame 1:1).
    assert "Mission Classique" not in response.text


# --------------------------------------------------------------------------- #
# Intégration à la synthèse globale de mission (US9.6).
# --------------------------------------------------------------------------- #
def test_synthese_globale_generate_uses_libre_material(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _create_and_finish_libre_mission(client, monkeypatch, "Interviewé Synthèse")

    monkeypatch.setattr("app.routers.synthese.is_configured", lambda: True)
    captured = {}

    def fake_generate_global(mission, material_by_theme, material_libre=None):
        captured["material_libre"] = material_libre
        captured["material_by_theme"] = material_by_theme
        return {
            "contexte": "- Contexte global",
            "culture_adn": "- Culture globale",
            "forces_succes": "- Force globale",
            "points_amelioration": "- Point global",
            "aspirations": "- Aspiration globale",
        }

    monkeypatch.setattr(
        "app.routers.synthese.generate_global_synthesis", fake_generate_global
    )

    # Ne doit pas planter malgré l'absence de trame (garde-fou export.py/
    # synthese.py sur mission.trame is None).
    response = client.get(f"/missions/{mission_id}/synthese/export-import")
    assert response.status_code == 200

    response = client.post(f"/missions/{mission_id}/synthese/globale/generate")
    assert response.status_code == 200
    assert "Contexte global" in response.text

    assert captured["material_by_theme"] == []
    assert len(captured["material_libre"]) == 1
    interview, repartition = captured["material_libre"][0]
    assert interview.interviewee_name == "Interviewé Synthèse"
    assert repartition["contexte"] == "- Contexte"


# --------------------------------------------------------------------------- #
# Écrans Analyse / Synthèse (sections thématiques + résumé, US "à suivre" du
# 2026-07-15) et verrou de mode sur l'édition post-enregistrement.
# --------------------------------------------------------------------------- #
def test_libre_analyse_groups_turns_into_sections(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _create_and_finish_libre_mission(client, monkeypatch, "Analyse Test")
    session = SessionLocal()
    try:
        interview = session.scalars(
            select(Interview).where(Interview.mission_id == mission_id)
        ).one()
        interview_id = interview.id
        # _create_and_finish_libre_mission n'envoie qu'un seul tour ; on en
        # ajoute d'autres directement pour tester le regroupement par section.
        session.add_all([
            InterviewTurn(
                interview_id=interview_id, position=1,
                interlocuteur="Analyse Test", question=None,
                remarque="Réponse sans nouvelle section.", section_title=None,
            ),
            InterviewTurn(
                interview_id=interview_id, position=2,
                interlocuteur="Consultant·e", question="Autre sujet ?", remarque=None,
                section_title="Deuxième thème",
            ),
        ])
        session.commit()
    finally:
        session.close()

    response = client.get(f"/interviews/{interview_id}/analyse")
    assert response.status_code == 200
    assert "Réponse sans nouvelle section." in response.text
    assert "Deuxième thème" in response.text
    # Le premier tour (sans section_title) crée une section par défaut,
    # celui qui porte "Deuxième thème" en ouvre une nouvelle.
    assert response.text.index("Réponse sans nouvelle section.") < response.text.index("Deuxième thème")


def test_libre_analyse_shows_resume_and_repartition(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Depuis la fusion du 2026-07-17, résumé + répartition s'affichent sur
    /analyse (même écran que les tours de parole, plus de page séparée)."""
    mission_id = _create_and_finish_libre_mission(client, monkeypatch, "Synthese Test")
    session = SessionLocal()
    try:
        interview = session.scalars(
            select(Interview).where(Interview.mission_id == mission_id)
        ).one()
        interview_id = interview.id
    finally:
        session.close()

    response = client.get(f"/interviews/{interview_id}/analyse")
    assert response.status_code == 200
    assert "- Contexte" in response.text
    assert "- Point" in response.text


def test_libre_analyse_synthese_redirects_to_analyse(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ancienne URL /analyse/synthese — conservée en redirection permanente
    vers /analyse (contenu désormais fusionné) pour ne pas casser un lien
    existant."""
    mission_id = _create_and_finish_libre_mission(client, monkeypatch, "Redirect Test")
    session = SessionLocal()
    try:
        interview = session.scalars(
            select(Interview).where(Interview.mission_id == mission_id)
        ).one()
        interview_id = interview.id
    finally:
        session.close()

    response = client.get(f"/interviews/{interview_id}/analyse/synthese", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == f"/interviews/{interview_id}/analyse"


def test_export_interview_markdown_libre(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _create_and_finish_libre_mission(client, monkeypatch, "Export Libre")
    session = SessionLocal()
    try:
        interview = session.scalars(
            select(Interview).where(Interview.mission_id == mission_id)
        ).one()
        interview_id = interview.id
    finally:
        session.close()

    response = client.get(f"/interviews/{interview_id}/export/markdown")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert 'attachment; filename="entretien_export_libre.md"' in response.headers["content-disposition"]
    md = response.text

    assert "# Entretien — Export Libre" in md
    assert "## Transcription structurée" in md
    assert "**Consultant·e** : Une question ?" in md
    assert "## Répartition par catégorie" in md
    assert "### Contexte" in md
    assert "- Contexte" in md
    assert "- Point" in md


def test_libre_analyse_and_synthese_reject_structured_interview(client: TestClient) -> None:
    response = client.post(
        "/missions", data={"name": "Mission Structuree Analyse", "description": ""},
        follow_redirects=False,
    )
    mission_id = response.headers["location"].rsplit("/", 1)[-1]
    response = client.post(
        f"/missions/{mission_id}/interviews",
        data={"interviewee_name": "Interviewé Structuré"},
        follow_redirects=False,
    )
    interview_id = response.headers["location"].rsplit("/", 1)[-1]

    assert client.get(f"/interviews/{interview_id}/analyse").status_code == 400
    assert client.get(f"/interviews/{interview_id}/analyse/synthese").status_code == 400


def test_save_libre_detail_rejects_structured_interview(client: TestClient) -> None:
    """Verrou serveur (US9.1) : l'édition post-enregistrement propre au mode
    libre ne doit jamais s'appliquer à un entretien structuré."""
    response = client.post(
        "/missions", data={"name": "Mission Structuree Verrou", "description": ""},
        follow_redirects=False,
    )
    mission_id = response.headers["location"].rsplit("/", 1)[-1]
    response = client.post(
        f"/missions/{mission_id}/interviews",
        data={"interviewee_name": "Interviewé Verrou"},
        follow_redirects=False,
    )
    interview_id = response.headers["location"].rsplit("/", 1)[-1]

    response = client.post(
        f"/interviews/{interview_id}/libre",
        data={"repartition_contexte": "Tentative d'injection"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_finaliser_rejects_action_on_already_named_mission(client: TestClient) -> None:
    response = client.post(
        "/missions", data={"name": "Mission Deja Nommee", "description": ""},
        follow_redirects=False,
    )
    mission_id = response.headers["location"].rsplit("/", 1)[-1]

    assert client.get(f"/missions/{mission_id}/finaliser", follow_redirects=False).status_code == 303
    response = client.post(
        f"/missions/{mission_id}/finaliser",
        data={"action": "nommer", "name": "Nouveau nom"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_finaliser_rejects_unknown_action(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _create_and_finish_libre_mission(client, monkeypatch, "Action Inconnue")
    response = client.post(
        f"/missions/{mission_id}/finaliser",
        data={"action": "autre-chose"},
        follow_redirects=False,
    )
    assert response.status_code == 400
