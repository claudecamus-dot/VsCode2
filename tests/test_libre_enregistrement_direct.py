"""Enregistrement direct d'un entretien libre depuis l'écran de transcription
(demande utilisateur 2026-07-29).

Le wizard libre comptait 3 écrans : transcription → revue des tours de parole
→ synthèse → enregistrement. Les deux derniers sont retirés de l'UI (la revue
des tours doublonnait l'onglet « Répartition (Q/R) » de l'écran de
transcription, la synthèse retenait l'entretien derrière une génération IA
longue) : l'entretien s'enregistre désormais depuis le premier écran, résumé
et répartition restant vides et générables plus tard depuis l'aperçu.

Les routes et écrans du wizard historique sont CONSERVÉS (non atteignables
depuis l'UI) — leurs tests restent dans `test_interview_libre.py` et
`test_interview_segment_jobs.py`, ce fichier ne couvre que le chemin direct.

Comme partout ailleurs, l'IA (`extract_turns_from_text`) est monkeypatchée :
aucun appel réseau.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import Interview, InterviewSegmentJob, InterviewTurn
from app.services.interview_libre_extract_ai import InterviewLibreExtractAIError


def setup_module() -> None:
    # `engine.dispose()` avant l'unlink : le pool du fichier de test précédent
    # garde sinon une connexion ouverte sur DB_PATH (verrou Windows).
    try:
        engine.dispose()
    except Exception:
        pass
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


def _turn(interlocuteur="Alice", question=None, remarque="Un propos.", section_title=None):
    return {
        "interlocuteur": interlocuteur,
        "question": question,
        "remarque": remarque,
        "section_title": section_title,
    }


def _payload(turns=None, name="", role="", entity=""):
    return {
        "turns": turns if turns is not None else [_turn()],
        "identity": {
            "interviewee_name": name,
            "interviewee_role": role,
            "interviewee_entity": entity,
        },
    }


def _patch_extract(monkeypatch: pytest.MonkeyPatch, payload=None, spy=None):
    def _fake(text):
        if spy is not None:
            spy.append(text)
        return payload if payload is not None else _payload()

    monkeypatch.setattr("app.routers.interviews.extract_turns_from_text", _fake)


def _mission_brouillon(client: TestClient) -> int:
    response = client.post("/entretiens/libre/nouveau", follow_redirects=False)
    assert response.status_code == 303
    return int(response.headers["location"].split("/")[2])


def _entretiens(mission_id: int) -> list[Interview]:
    db = SessionLocal()
    try:
        return list(
            db.scalars(
                select(Interview).where(Interview.mission_id == mission_id)
            ).all()
        )
    finally:
        db.close()


def _seed_job(session_token: str, position: int, status: str, turns_result=None, text=""):
    db = SessionLocal()
    try:
        db.add(
            InterviewSegmentJob(
                session_token=session_token, position=position, status=status,
                turns_result=turns_result, text=text,
            )
        )
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Écran : plus d'étape intermédiaire, un bouton d'enregistrement
# --------------------------------------------------------------------------- #
def test_ecran_transcription_enregistre_directement(client: TestClient) -> None:
    mission_id = _mission_brouillon(client)
    html = client.get(f"/missions/{mission_id}/interviews/record-libre").text

    assert f'action="/missions/{mission_id}/interviews/record-libre/enregistrer"' in html
    assert "Enregistrer l&#39;entretien" in html or "Enregistrer l'entretien" in html
    # Les deux étapes désactivées ne sont plus proposées depuis cet écran.
    assert "Continuer vers les tours" not in html
    assert "Continuer vers la synthèse" not in html
    # Le repère d'étapes du wizard 3 écrans n'a plus lieu d'être.
    assert "wizard-steps" not in html
    # L'identité se saisit ici : c'était le seul autre écran à la porter.
    assert 'name="interviewee_name"' in html
    assert 'name="interview_date"' in html


def test_ecran_transcription_gate_enregistrement_sur_segments_perdus(
    client: TestClient,
) -> None:
    """Régression bmad-code-review 2026-07-29 : pas de harnais JS dans ce
    projet pour exécuter `updateSubmitState()` en conditions réelles — ce test
    verrouille au moins la PRÉSENCE du gate dans le HTML rendu (le lire
    disparaître silencieusement d'un futur refactor serait sinon invisible en
    pytest). Le comportement runtime (bouton "Enregistrer" désactivé tant que
    `lostSegments`/`lostRetryBlocking` ne sont pas vides) est vérifié par
    lecture de code, pas exécuté ici."""
    mission_id = _mission_brouillon(client)
    html = client.get(f"/missions/{mission_id}/interviews/record-libre").text

    # `lostRetryBlocking` (compteur, pas booléen) a remplacé `lostSegmentsRetrying`
    # le 2026-07-30 : un drain concurrent (relance ciblée + « Relancer tous »)
    # pouvait sinon remettre à zéro le compte d'un autre drain encore en vol.
    assert "lostRetryBlocking" in html
    # Depuis le 2026-07-30, le gate ne compte que les segments BLOQUANTS (audio
    # jamais transmis) : un 422 « aucune parole » est désormais conservé pour
    # rejeu lui aussi, et un entretien à distance en accumule des dizaines —
    # les compter dans le gate rendrait l'enregistrement impossible.
    assert "lostSegments.some(function (s) { return s.blocking; })" in html
    assert "|| lostRetryBlocking > 0" in html
    assert "updateSubmitState();" in html


# --------------------------------------------------------------------------- #
# Chemin nominal — aucun job de tranche (entretien court)
# --------------------------------------------------------------------------- #
def test_enregistrement_direct_cree_entretien_tours_et_transcription(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _mission_brouillon(client)
    _patch_extract(
        monkeypatch,
        _payload(turns=[
            _turn("Consultant", question="Comment ça se passe ?", remarque=None,
                  section_title="Contexte"),
            _turn("Alice", remarque="Plutôt bien."),
        ]),
    )

    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "Comment ça se passe ? Plutôt bien.",
              "interviewee_name": "Alice Martin"},
        follow_redirects=False,
    )

    # Mission brouillon : on part la nommer / la rattacher.
    assert response.status_code == 303
    assert response.headers["location"] == f"/missions/{mission_id}/finaliser"

    entretiens = _entretiens(mission_id)
    assert len(entretiens) == 1
    interview = entretiens[0]
    assert interview.mode == "libre"
    assert interview.interviewee_name == "Alice Martin"
    assert interview.raw_transcript == "Comment ça se passe ? Plutôt bien."
    # Aucune synthèse sur ce chemin : elle se génère plus tard depuis l'aperçu.
    assert interview.resume is None
    assert interview.repartition is None

    db = SessionLocal()
    try:
        tours = list(db.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.interview_id == interview.id)
            .order_by(InterviewTurn.position)
        ).all())
    finally:
        db.close()
    assert [t.interlocuteur for t in tours] == ["Consultant", "Alice"]
    assert tours[0].section_title == "Contexte"
    assert tours[1].remarque == "Plutôt bien."


def test_enregistrement_direct_identite_saisie_gagne_sur_celle_detectee(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _mission_brouillon(client)
    _patch_extract(monkeypatch, _payload(name="Nom Entendu", role="Rôle entendu",
                                         entity="Équipe entendue"))

    client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "un texte", "interviewee_name": "Nom Saisi",
              "interview_date": "2026-07-29"},
        follow_redirects=False,
    )

    interview = _entretiens(mission_id)[0]
    assert interview.interviewee_name == "Nom Saisi"      # la saisie l'emporte
    assert interview.interviewee_role == "Rôle entendu"   # le vide est complété
    assert interview.interviewee_entity == "Équipe entendue"
    assert interview.interview_date.isoformat() == "2026-07-29"


def test_enregistrement_direct_refuse_une_transcription_vide(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _mission_brouillon(client)
    _patch_extract(monkeypatch)

    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "   "},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Aucun texte transcrit" in response.text
    assert _entretiens(mission_id) == []


def test_enregistrement_direct_zero_tour_naboutit_pas_a_un_entretien_vide(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Régression bmad-code-review 2026-07-29 : sur le chemin synchrone (aucun
    job de tranche), `extract_turns_from_text` peut répondre SANS lever
    d'exception mais sans détecter aucun tour (silence, transcription trop
    courte, échec silencieux malgré les relances internes). Avant ce
    correctif, `_enregistrer_libre_direct` créait quand même l'entretien
    (`status="done"`, 0 tour) et redirigeait comme un succès — l'écran de
    revue qui filtrait ce cas dans l'ancien wizard n'existe plus sur ce
    chemin direct."""
    mission_id = _mission_brouillon(client)
    _patch_extract(monkeypatch, _payload(turns=[]))

    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "un souffle, rien d'autre"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Aucun tour de parole détecté" in response.text
    assert "un souffle, rien d&#39;autre" in response.text or "un souffle, rien d'autre" in response.text
    assert _entretiens(mission_id) == []


def test_enregistrement_direct_echec_ia_garde_le_texte_et_ne_cree_rien(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _mission_brouillon(client)

    def _boom(text):
        raise InterviewLibreExtractAIError("Ollama n'a pas répondu à temps")

    monkeypatch.setattr("app.routers.interviews.extract_turns_from_text", _boom)

    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "une heure de parole"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Ollama n&#39;a pas répondu à temps" in response.text
    # La transcription est reconduite dans le formulaire (export PDF possible).
    assert "une heure de parole" in response.text
    assert _entretiens(mission_id) == []


# --------------------------------------------------------------------------- #
# Tranches traitées en tâche de fond
# --------------------------------------------------------------------------- #
def test_enregistrement_direct_attend_les_tranches_encore_en_cours(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _mission_brouillon(client)
    _patch_extract(monkeypatch)
    _seed_job("tok-attente-enr", 0, "running", text="tranche 1")

    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "texte", "session_token": "tok-attente-enr"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    # L'attente enchaîne sur l'ENREGISTREMENT, pas sur la revue des tours.
    assert (
        f'action="/missions/{mission_id}/interviews/record-libre/enregistrer/from-jobs"'
        in response.text
    )
    assert _entretiens(mission_id) == []


def test_ecran_attente_du_wizard_historique_pointe_toujours_vers_from_jobs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Régression : `finalize_action` est devenu paramétrable — le chemin
    historique (route `/record-libre`, conservée) doit continuer de finaliser
    vers la revue des tours."""
    mission_id = _mission_brouillon(client)
    _patch_extract(monkeypatch)
    _seed_job("tok-attente-wizard", 0, "running", text="tranche 1")

    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre",
        data={"transcript": "texte", "session_token": "tok-attente-wizard"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert (
        f'action="/missions/{mission_id}/interviews/record-libre/from-jobs"'
        in response.text
    )


def test_enregistrement_from_jobs_reprend_les_tours_des_tranches_et_nettoie(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _mission_brouillon(client)
    spy: list[str] = []
    _patch_extract(monkeypatch, spy=spy)
    _seed_job("tok-done", 0, "done", turns_result=_payload(
        turns=[_turn("Alice", remarque="Tranche 1.")], name="Alice Martin"))
    _seed_job("tok-done", 1, "done", turns_result=_payload(
        turns=[_turn("Bob", remarque="Tranche 2.")]))

    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer/from-jobs",
        data={"transcript": "Tranche 1. Tranche 2.", "session_token": "tok-done"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    # Aucun reliquat -> aucun appel IA de plus : les tranches sont déjà extraites.
    assert spy == []

    interview = _entretiens(mission_id)[0]
    assert interview.interviewee_name == "Alice Martin"  # identité de la 1re tranche
    db = SessionLocal()
    try:
        tours = list(db.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.interview_id == interview.id)
            .order_by(InterviewTurn.position)
        ).all())
        restants = list(db.scalars(
            select(InterviewSegmentJob)
            .where(InterviewSegmentJob.session_token == "tok-done")
        ).all())
    finally:
        db.close()
    assert [t.remarque for t in tours] == ["Tranche 1.", "Tranche 2."]
    # Jobs consommés : ils portent du contenu d'entretien, on ne les garde pas.
    assert restants == []


def test_enregistrement_from_jobs_extrait_le_reliquat_en_plus_des_tranches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission_id = _mission_brouillon(client)
    spy: list[str] = []
    _patch_extract(
        monkeypatch,
        _payload(turns=[_turn("Carol", remarque="Le reliquat.")]),
        spy=spy,
    )
    _seed_job("tok-tail", 0, "done", turns_result=_payload(
        turns=[_turn("Alice", remarque="Tranche 1.")]))

    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer/from-jobs",
        data={"transcript": "Tranche 1. Le reliquat.",
              "session_token": "tok-tail", "segment_tail": "Le reliquat."},
        follow_redirects=False,
    )

    assert response.status_code == 303
    # Seul le RELIQUAT part à l'IA, jamais la transcription entière (le mur
    # synchrone multi-heures que le Palier 2 supprime).
    assert spy == ["Le reliquat."]

    interview = _entretiens(mission_id)[0]
    db = SessionLocal()
    try:
        tours = list(db.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.interview_id == interview.id)
            .order_by(InterviewTurn.position)
        ).all())
    finally:
        db.close()
    assert [t.remarque for t in tours] == ["Tranche 1.", "Le reliquat."]
