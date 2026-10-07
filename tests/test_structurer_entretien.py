"""Structuration DIFFÉRÉE de l'entretien libre (2026-10-06).

Demande utilisateur : « enregistrer l'entretien libre avec l'audio et la
transcription dans un premier temps et après traiter le reste en asynchrone ».
« Enregistrer l'entretien » crée l'entretien sans aucun appel IA
(`structuration_status="a_traiter"`) et programme `structurer_entretien` en
tâche de fond ; la fiche permet de la relancer.

`TestClient` exécute les BackgroundTasks AVANT de rendre la réponse : pour
prouver que l'enregistrement n'appelle pas l'IA, on remplace le callable que la
route passe à `add_task` (`interviews_libre.structurer_entretien`).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview, InterviewSegmentJob, Mission
from app.services import structuration_libre


def setup_module() -> None:
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


def _mission(client: TestClient, nommee: bool = True) -> int:
    response = client.post("/entretiens/libre/nouveau", follow_redirects=False)
    mission_id = int(response.headers["location"].split("/")[2])
    if nommee:
        with SessionLocal() as db:
            db.get(Mission, mission_id).is_draft = False
            db.commit()
    return mission_id


def _tour(remarque="Un propos."):
    return {"interlocuteur": "Alice", "question": None, "remarque": remarque,
            "section_title": None}


def _espion_ia(monkeypatch, turns=None, exc=None) -> list:
    appels: list = []

    def _fake(text):
        appels.append(text)
        if exc is not None:
            raise exc
        return {"turns": [_tour()] if turns is None else turns,
                "identity": {"interviewee_name": "Alice Detectee"}}

    monkeypatch.setattr("app.routers.interviews.extract_turns_from_text", _fake)
    return appels


def _programmes(monkeypatch) -> list:
    programmes: list = []
    monkeypatch.setattr(
        "app.routers.interviews_libre.planifier_structuration", programmes.append
    )
    return programmes


def _entretiens(mission_id: int) -> list[Interview]:
    with SessionLocal() as db:
        return list(db.scalars(select(Interview).where(Interview.mission_id == mission_id)))


def _creer_a_traiter(mission_id: int, texte="Du texte.", jeton=None, statut="a_traiter") -> int:
    with SessionLocal() as db:
        interview = Interview(
            mission_id=mission_id, mode="libre", status="done",
            interviewee_name="Sans nom", raw_transcript=texte,
            segment_token=jeton, structuration_status=statut,
        )
        db.add(interview)
        db.commit()
        return interview.id


# --------------------------------------------------------------------------- #
# Enregistrement immédiat
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "route", ["record-libre/enregistrer", "record-libre/enregistrer/from-jobs"]
)
def test_enregistrer_cree_l_entretien_sans_appel_ia(
    client: TestClient, monkeypatch, route
) -> None:
    appels = _espion_ia(monkeypatch)
    programmes = _programmes(monkeypatch)
    mission_id = _mission(client)

    response = client.post(
        f"/missions/{mission_id}/interviews/{route}",
        data={"transcript": "Texte intégral.", "interviewee_name": "Bob",
              "session_token": f"tok-{route}", "segment_tail": "reliquat"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    (interview,) = _entretiens(mission_id)
    assert response.headers["location"] == f"/interviews/{interview.id}"
    assert appels == []
    assert programmes == [interview.id]
    assert interview.structuration_status == "a_traiter"
    assert interview.raw_transcript == "Texte intégral."
    assert interview.segment_token == f"tok-{route}"
    assert interview.segment_tail == "reliquat"
    assert interview.interviewee_name == "Bob"


def test_enregistrer_ne_supprime_pas_les_jobs_de_tranche(client: TestClient, monkeypatch) -> None:
    _espion_ia(monkeypatch)
    _programmes(monkeypatch)
    mission_id = _mission(client)
    with SessionLocal() as db:
        db.add(InterviewSegmentJob(session_token="tok-garde", position=0, status="done",
                                   turns_result=[_tour()], text="t"))
        db.commit()
    client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "x", "session_token": "tok-garde"},
        follow_redirects=False,
    )
    with SessionLocal() as db:
        assert db.scalar(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == "tok-garde")) is not None


def test_double_post_du_meme_jeton_ne_cree_qu_un_entretien(
    client: TestClient, monkeypatch
) -> None:
    """M1. Échoue sur le code d'avant : le second POST levait IntegrityError
    (500) au lieu de rediriger vers l'entretien déjà créé."""
    _espion_ia(monkeypatch)
    programmes = _programmes(monkeypatch)
    mission_id = _mission(client)
    donnees = {"transcript": "x", "session_token": "tok-double"}
    url = f"/missions/{mission_id}/interviews/record-libre/enregistrer"

    premier = client.post(url, data=donnees, follow_redirects=False)
    second = client.post(url, data=donnees, follow_redirects=False)

    assert second.status_code == 303
    assert second.headers["location"] == premier.headers["location"]
    assert len(_entretiens(mission_id)) == 1
    assert len(programmes) == 1


def test_sans_jeton_deux_enregistrements_restent_possibles(client: TestClient, monkeypatch) -> None:
    _espion_ia(monkeypatch)
    _programmes(monkeypatch)
    mission_id = _mission(client)
    url = f"/missions/{mission_id}/interviews/record-libre/enregistrer"
    client.post(url, data={"transcript": "a"}, follow_redirects=False)
    client.post(url, data={"transcript": "b"}, follow_redirects=False)
    assert len(_entretiens(mission_id)) == 2


def test_enregistrement_auto_structure_en_tache_de_fond(client: TestClient, monkeypatch) -> None:
    """Sans patch du callable : TestClient joue la tâche de fond, l'entretien
    sort structuré (AUTO, choix utilisateur)."""
    _espion_ia(monkeypatch)
    mission_id = _mission(client)
    client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "Du texte."},
        follow_redirects=False,
    )
    (interview,) = _entretiens(mission_id)
    assert interview.structuration_status == "fait"
    with SessionLocal() as db:
        assert len(db.get(Interview, interview.id).turns) == 1
        # Identité relevée à l'oral : complète un « Sans nom ».
        assert db.get(Interview, interview.id).interviewee_name == "Alice Detectee"


# --------------------------------------------------------------------------- #
# structurer_entretien
# --------------------------------------------------------------------------- #
def test_structurer_succes(client: TestClient, monkeypatch) -> None:
    appels = _espion_ia(monkeypatch, turns=[_tour("a"), _tour("b")])
    interview_id = _creer_a_traiter(_mission(client))
    assert structuration_libre.structurer_entretien(interview_id) is True
    assert len(appels) == 1
    with SessionLocal() as db:
        interview = db.get(Interview, interview_id)
        assert interview.structuration_status == "fait"
        assert [t.remarque for t in interview.turns] == ["a", "b"]
        assert interview.tranches_manquantes == 0


def test_structurer_exception_inattendue_laisse_echec(client: TestClient, monkeypatch) -> None:
    _espion_ia(monkeypatch, exc=RuntimeError("Ollama absent"))
    interview_id = _creer_a_traiter(_mission(client))
    assert structuration_libre.structurer_entretien(interview_id) is False
    with SessionLocal() as db:
        interview = db.get(Interview, interview_id)
        assert interview.structuration_status == "echec"
        assert interview.turns == []


def test_structurer_sans_aucun_tour_sur_tranches_en_echec_laisse_echec_et_garde_les_jobs(
    client: TestClient, monkeypatch
) -> None:
    from app.services.interview_libre_extract_ai import InterviewLibreExtractAIError

    _espion_ia(monkeypatch, exc=InterviewLibreExtractAIError("panne"))
    monkeypatch.setattr(structuration_libre, "recover_stalled_or_failed_jobs", lambda db, jobs: None)
    mission_id = _mission(client)
    with SessionLocal() as db:
        db.add(InterviewSegmentJob(session_token="tok-ko", position=0, status="failed", text="t"))
        db.commit()
    interview_id = _creer_a_traiter(mission_id, jeton="tok-ko")
    assert structuration_libre.structurer_entretien(interview_id) is False
    with SessionLocal() as db:
        assert db.get(Interview, interview_id).structuration_status == "echec"
        assert db.scalar(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == "tok-ko")) is not None


def test_structurer_deux_fois_un_seul_appel_ia(client: TestClient, monkeypatch) -> None:
    appels = _espion_ia(monkeypatch)
    interview_id = _creer_a_traiter(_mission(client))
    assert structuration_libre.structurer_entretien(interview_id) is True
    assert structuration_libre.structurer_entretien(interview_id) is False
    assert len(appels) == 1


@pytest.mark.parametrize("statut", ["en_cours", "fait"])
def test_structurer_ne_reprend_pas_un_entretien_en_cours_ou_fait(
    client: TestClient, monkeypatch, statut
) -> None:
    appels = _espion_ia(monkeypatch)
    interview_id = _creer_a_traiter(_mission(client), statut=statut)
    assert structuration_libre.structurer_entretien(interview_id) is False
    assert appels == []


def test_structurer_relance_un_echec(client: TestClient, monkeypatch) -> None:
    _espion_ia(monkeypatch)
    interview_id = _creer_a_traiter(_mission(client), statut="echec")
    assert structuration_libre.structurer_entretien(interview_id) is True


# --------------------------------------------------------------------------- #
# Routes de relance et de statut
# --------------------------------------------------------------------------- #
def test_route_structurer_programme_puis_double_clic_un_seul_appel_ia(
    client: TestClient, monkeypatch
) -> None:
    appels = _espion_ia(monkeypatch)
    interview_id = _creer_a_traiter(_mission(client), statut="echec")
    r1 = client.post(f"/interviews/{interview_id}/structurer", follow_redirects=False)
    r2 = client.post(f"/interviews/{interview_id}/structurer", follow_redirects=False)
    assert r1.status_code == r2.status_code == 303
    assert r1.headers["location"] == f"/interviews/{interview_id}"
    assert len(appels) == 1
    with SessionLocal() as db:
        assert db.get(Interview, interview_id).structuration_status == "fait"


def test_route_structurer_refuse_un_entretien_deja_structure_et_le_dit(
    client: TestClient, monkeypatch
) -> None:
    """F4 : un refus n'est plus un 303 muet."""
    appels = _espion_ia(monkeypatch)
    interview_id = _creer_a_traiter(_mission(client))
    structuration_libre.structurer_entretien(interview_id)  # fait, 1 tour
    r = client.post(f"/interviews/{interview_id}/structurer", follow_redirects=False)
    assert r.headers["location"].endswith("?structuration=refusee")
    assert "Relance non prise en compte" in client.get(r.headers["location"]).text
    assert len(appels) == 1


def test_route_structurer_refuse_un_entretien_deja_en_file(client: TestClient, monkeypatch) -> None:
    appels = _espion_ia(monkeypatch)
    interview_id = _creer_a_traiter(_mission(client), statut="echec")
    monkeypatch.setitem(structuration_libre._en_vol, interview_id, 0.0)
    r = client.post(f"/interviews/{interview_id}/structurer", follow_redirects=False)
    assert r.headers["location"].endswith("?structuration=refusee")
    assert appels == []


@pytest.mark.parametrize("statut", ["en_cours", "fait"])
def test_route_structurer_relance_un_en_cours_orphelin_ou_un_fait_sans_tour(
    client: TestClient, monkeypatch, statut
) -> None:
    """F4. Échoue sur le code d'avant : seuls a_traiter|echec étaient
    relançables — un `en_cours` orphelin ou un `fait` à 0 tour restaient figés."""
    appels = _espion_ia(monkeypatch)
    interview_id = _creer_a_traiter(_mission(client), statut=statut)
    client.post(f"/interviews/{interview_id}/structurer", follow_redirects=False)
    assert len(appels) == 1
    with SessionLocal() as db:
        interview = db.get(Interview, interview_id)
        assert interview.structuration_status == "fait"
        assert len(interview.turns) == 1


def test_statut_d_un_entretien_supprime_arrete_le_sondage(client: TestClient) -> None:
    interview_id = _creer_a_traiter(_mission(client), statut="en_cours")
    client.post(f"/interviews/{interview_id}/delete", follow_redirects=False)
    assert client.get(f"/interviews/{interview_id}/structurer/statut?suivi=1").status_code == 286


def test_route_structurer_refuse_un_entretien_parametre(client: TestClient) -> None:
    mission_id = _mission(client)
    with SessionLocal() as db:
        interview = Interview(mission_id=mission_id, mode="parametre", interviewee_name="x")
        db.add(interview)
        db.commit()
        interview_id = interview.id
    assert client.post(f"/interviews/{interview_id}/structurer").status_code == 404


@pytest.mark.parametrize(
    "statut,sonde,bouton",
    [("a_traiter", True, "Structurer maintenant"), ("en_cours", True, "Relancer"),
     ("echec", False, "Relancer"), ("fait", False, "Structurer maintenant")],
)
def test_statut_fragment_sonde_seulement_hors_etat_terminal(
    client: TestClient, statut, sonde, bouton
) -> None:
    interview_id = _creer_a_traiter(_mission(client), statut=statut)
    html = client.get(f"/interviews/{interview_id}/structurer/statut").text
    assert 'aria-live="polite"' in html
    assert f'data-statut="{statut}"' in html
    assert ('hx-trigger="every 3s"' in html) is sonde
    assert "formaction" not in html
    if bouton:
        assert bouton in html
    else:
        assert "<button" not in html


def test_statut_suivi_recharge_la_fiche_a_l_arrivee_sur_un_etat_terminal(client: TestClient) -> None:
    fait = _creer_a_traiter(_mission(client), statut="fait")
    en_cours = _creer_a_traiter(_mission(client), statut="en_cours")
    assert client.get(f"/interviews/{fait}/structurer/statut?suivi=1").headers.get("HX-Refresh") == "true"
    assert "HX-Refresh" not in client.get(f"/interviews/{en_cours}/structurer/statut?suivi=1").headers
    assert "HX-Refresh" not in client.get(f"/interviews/{fait}/structurer/statut").headers


# --------------------------------------------------------------------------- #
# Démarrage, purge, Ollama
# --------------------------------------------------------------------------- #
def test_reconcile_au_demarrage_en_cours_devient_echec_sans_relance(
    client: TestClient, monkeypatch
) -> None:
    appels = _espion_ia(monkeypatch)
    mission_id = _mission(client)
    en_cours = _creer_a_traiter(mission_id, statut="en_cours")
    a_traiter = _creer_a_traiter(mission_id, statut="a_traiter")
    assert structuration_libre.reconcile_en_cours_on_startup() >= 1
    with SessionLocal() as db:
        assert db.get(Interview, en_cours).structuration_status == "echec"
        # F4 : au démarrage la file est vide, un `a_traiter` serait figé.
        assert db.get(Interview, a_traiter).structuration_status == "echec"
    assert appels == []


def test_purge_epargne_les_tranches_d_un_entretien_non_structure(client: TestClient) -> None:
    from datetime import datetime, timedelta

    from app.services.interview_segment_jobs import purge_stale_segment_jobs

    mission_id = _mission(client)
    vieux = datetime.now() - timedelta(days=30)
    _creer_a_traiter(mission_id, jeton="tok-protege", statut="echec")
    _creer_a_traiter(mission_id, jeton="tok-fait", statut="fait")
    with SessionLocal() as db:
        for jeton in ("tok-protege", "tok-fait", "tok-orphelin"):
            db.add(InterviewSegmentJob(session_token=jeton, position=0, status="done",
                                       text="t", created_at=vieux))
        db.commit()
        purge_stale_segment_jobs(db)
        restants = set(db.scalars(select(InterviewSegmentJob.session_token)))
    assert "tok-protege" in restants
    assert "tok-fait" not in restants
    assert "tok-orphelin" not in restants


def test_un_seul_appel_ollama_a_la_fois(monkeypatch) -> None:
    import threading
    import time as _time

    from app.services import ai_common

    en_vol = 0
    maximum = 0
    verrou = threading.Lock()

    class _Reponse:
        def __enter__(self):
            nonlocal en_vol, maximum
            with verrou:
                en_vol += 1
                maximum = max(maximum, en_vol)
            _time.sleep(0.05)
            return self

        def __exit__(self, *exc):
            nonlocal en_vol
            with verrou:
                en_vol -= 1

        def read(self):
            return b'{"message": {"content": "{}"}}'

    monkeypatch.setattr(ai_common.urllib.request, "urlopen", lambda req, timeout: _Reponse())
    fils = [threading.Thread(target=ai_common._call_ollama_once, args=(b"{}", "m"))
            for _ in range(4)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    assert maximum == 1


def test_num_thread_optionnel(monkeypatch) -> None:
    import json as _json

    from app.services import ai_common

    envoyes = []
    monkeypatch.setattr(
        ai_common, "_call_ollama_once",
        lambda payload, model: envoyes.append(_json.loads(payload)) or {"message": {"content": "{}"}},
    )
    monkeypatch.delenv("OLLAMA_NUM_THREAD", raising=False)
    ai_common._call_ollama("s", "p", {}, "", "m", 10)
    monkeypatch.setenv("OLLAMA_NUM_THREAD", "3")
    ai_common._call_ollama("s", "p", {}, "", "m", 10)
    monkeypatch.setenv("OLLAMA_NUM_THREAD", "abc")
    ai_common._call_ollama("s", "p", {}, "", "m", 10)
    assert "num_thread" not in envoyes[0]["options"]
    assert envoyes[1]["options"]["num_thread"] == 3
    assert "num_thread" not in envoyes[2]["options"]

# --------------------------------------------------------------------------- #
# Écrans : badge sur la fiche, avertissement là où les entretiens sont listés
# --------------------------------------------------------------------------- #
def test_fiche_libre_affiche_badge_et_bouton(client: TestClient) -> None:
    interview_id = _creer_a_traiter(_mission(client), statut="echec")
    html = client.get(f"/interviews/{interview_id}").text
    assert 'id="structuration-statut"' in html
    assert "badge-echec" in html
    assert f'action="/interviews/{interview_id}/structurer"' in html
    assert "Relancer" in html


def test_fiche_libre_structuree_sans_sondage(client: TestClient) -> None:
    interview_id = _creer_a_traiter(_mission(client), statut="fait")
    html = client.get(f"/interviews/{interview_id}").text
    assert "badge-fait" in html
    assert 'hx-trigger="every 3s"' not in html


@pytest.mark.parametrize(
    "url", ["/missions/{m}", "/missions/{m}/synthese/globale"],
)
def test_ecrans_de_mission_avertissent_des_entretiens_non_structures(
    client: TestClient, url
) -> None:
    mission_id = _mission(client)
    _creer_a_traiter(mission_id, statut="a_traiter")
    html = client.get(url.format(m=mission_id)).text
    assert "pas encore structuré" in html
    assert "(à traiter)" in html


def test_ecran_de_mission_sans_entretien_non_structure_n_avertit_pas(client: TestClient) -> None:
    mission_id = _mission(client)
    _creer_a_traiter(mission_id, statut="fait")
    assert "pas encore structuré" not in client.get(f"/missions/{mission_id}").text


def test_finaliser_avertit_de_la_structuration_en_attente(client: TestClient, monkeypatch) -> None:
    _espion_ia(monkeypatch)
    _programmes(monkeypatch)
    mission_id = _mission(client, nommee=False)
    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "x"}, follow_redirects=True,
    )
    assert "pas encore structuré" in response.text

def test_structurer_sans_jeton_ia_en_panne_laisse_echec_relancable(client: TestClient, monkeypatch) -> None:
    """Vu par l'e2e réel : un entretien court n'a pas de jobs de tranche (jeton
    vide). Une panne IA le laissait « fait » à 0 tour — plus relançable."""
    from app.services.interview_libre_extract_ai import InterviewLibreExtractAIError

    _espion_ia(monkeypatch, exc=InterviewLibreExtractAIError("panne"))
    interview_id = _creer_a_traiter(_mission(client), jeton=None)
    assert structuration_libre.structurer_entretien(interview_id) is False
    with SessionLocal() as db:
        assert db.get(Interview, interview_id).structuration_status == "echec"
    _espion_ia(monkeypatch)
    assert structuration_libre.structurer_entretien(interview_id) is True

# --------------------------------------------------------------------------- #
# F3 (revue 2026-10-06) : écriture finale gardée sur `en_cours`
# --------------------------------------------------------------------------- #
def _ia_qui_modifie(monkeypatch, action) -> None:
    def _fake(text):
        action()
        return {"turns": [_tour()], "identity": {}}

    monkeypatch.setattr("app.routers.interviews.extract_turns_from_text", _fake)


def test_structurer_n_ecrit_rien_si_le_statut_a_change_pendant_le_traitement(
    client: TestClient, monkeypatch
) -> None:
    """Échoue sur le code d'avant : l'écriture finale ignorait le statut
    courant et posait « fait » + des tours sur un entretien ré-étiqueté."""
    interview_id = _creer_a_traiter(_mission(client))

    def _reetiqueter():
        with SessionLocal() as db:
            db.get(Interview, interview_id).structuration_status = "echec"
            db.commit()

    _ia_qui_modifie(monkeypatch, _reetiqueter)
    assert structuration_libre.structurer_entretien(interview_id) is False
    with SessionLocal() as db:
        interview = db.get(Interview, interview_id)
        assert interview.structuration_status == "echec"
        assert interview.turns == []


def test_structurer_d_un_entretien_supprime_ne_laisse_aucun_tour_orphelin(
    client: TestClient, monkeypatch
) -> None:
    from app.models import InterviewTurn

    mission_id = _mission(client)
    interview_id = _creer_a_traiter(mission_id, jeton="tok-supprime")
    with SessionLocal() as db:
        db.add(InterviewSegmentJob(session_token="tok-supprime", position=0,
                                   status="done", turns_result=[_tour()], text="t"))
        db.commit()
    _ia_qui_modifie(
        monkeypatch,
        lambda: client.post(f"/interviews/{interview_id}/delete", follow_redirects=False),
    )
    monkeypatch.setattr(structuration_libre, "_attendre_les_tranches", lambda *a, **k: None)
    # Jeton présent, jobs « done » : pas d'appel IA — on supprime donc avant.
    client.post(f"/interviews/{interview_id}/delete", follow_redirects=False)
    assert structuration_libre.structurer_entretien(interview_id) is False
    with SessionLocal() as db:
        assert db.scalars(select(InterviewTurn).where(
            InterviewTurn.interview_id == interview_id)).all() == []
        assert db.scalar(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == "tok-supprime")) is None

# --------------------------------------------------------------------------- #
# F2 : l'attente ne s'arrête pas au premier échec tant que d'autres tournent
# --------------------------------------------------------------------------- #
def test_attente_continue_tant_qu_une_tranche_tourne_malgre_un_echec(monkeypatch) -> None:
    """Échoue sur le code d'avant : `any_failed` arrêtait l'attente alors que
    la tranche voisine était encore `running`."""
    import threading
    import time as _time

    monkeypatch.setattr(structuration_libre, "ATTENTE_PAS_S", 0.05)
    monkeypatch.setattr(structuration_libre, "ATTENTE_TRANCHES_S", 20)
    with SessionLocal() as db:
        db.add(InterviewSegmentJob(session_token="tok-f2", position=0, status="failed", text="a"))
        db.add(InterviewSegmentJob(session_token="tok-f2", position=1, status="running", text="b"))
        db.commit()

    def _terminer():
        _time.sleep(0.5)
        with SessionLocal() as db:
            job = db.scalar(select(InterviewSegmentJob).where(
                InterviewSegmentJob.session_token == "tok-f2",
                InterviewSegmentJob.position == 1))
            job.status = "done"
            db.commit()

    fil = threading.Thread(target=_terminer)
    fil.start()
    structuration_libre._attendre_les_tranches("tok-f2")
    with SessionLocal() as db:
        statuts = {j.position: j.status for j in db.scalars(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == "tok-f2"))}
    fil.join()
    assert statuts[1] == "done"


def test_attente_bornee_par_l_echeance(monkeypatch) -> None:
    monkeypatch.setattr(structuration_libre, "ATTENTE_PAS_S", 0.02)
    monkeypatch.setattr(structuration_libre, "ATTENTE_TRANCHES_S", 0.2)
    with SessionLocal() as db:
        db.add(InterviewSegmentJob(session_token="tok-f2b", position=0, status="running", text="a"))
        db.commit()
    structuration_libre._attendre_les_tranches("tok-f2b")  # rend la main

# --------------------------------------------------------------------------- #
# F1 : worker dédié, dédoublonné ; verrou Ollama à attente bornée
# --------------------------------------------------------------------------- #
def test_planifier_ne_bloque_pas_l_appelant_et_dedoublonne(monkeypatch) -> None:
    """Échoue sur le code d'avant : la structuration s'exécutait DANS
    l'appelant (thread du pool partagé), bloqué jusqu'à la fin."""
    import threading
    import time as _time

    monkeypatch.setattr(structuration_libre, "EXECUTION_SYNCHRONE", False)
    libere = threading.Event()
    appels: list[int] = []

    def _lent(interview_id):
        appels.append(interview_id)
        libere.wait(3)

    monkeypatch.setattr(structuration_libre, "structurer_entretien", _lent)
    debut = _time.monotonic()
    assert structuration_libre.planifier_structuration(424242) is True
    assert _time.monotonic() - debut < 1.0
    assert structuration_libre.planifier_structuration(424242) is False  # déjà en file
    assert structuration_libre.est_en_vol(424242)
    libere.set()
    structuration_libre._file.join()
    assert appels == [424242]
    assert not structuration_libre.est_en_vol(424242)


def test_verrou_ollama_attente_bornee(monkeypatch) -> None:
    from app.services import ai_common

    monkeypatch.setenv("OLLAMA_VERROU_TIMEOUT", "0.1")
    assert ai_common._OLLAMA_UN_A_LA_FOIS.acquire(timeout=1)
    try:
        with pytest.raises(ai_common.AIError, match="occupé"):
            ai_common._call_ollama_once(b"{}", "m")
    finally:
        ai_common._OLLAMA_UN_A_LA_FOIS.release()

# --------------------------------------------------------------------------- #
# F6
# --------------------------------------------------------------------------- #
def test_menage_des_jobs_en_echec_ne_casse_pas_un_fait(client: TestClient, monkeypatch) -> None:
    """Échoue sur le code d'avant : l'exception du ménage remontait du worker."""
    _espion_ia(monkeypatch)

    def _boom(db, jeton):
        raise RuntimeError("disque verrouillé")

    monkeypatch.setattr(structuration_libre, "delete_segment_jobs", _boom)
    interview_id = _creer_a_traiter(_mission(client), jeton="tok-f6")
    assert structuration_libre.structurer_entretien(interview_id) is True
    with SessionLocal() as db:
        assert db.get(Interview, interview_id).structuration_status == "fait"


def test_conflit_d_unicite_sans_entretien_existant_rend_409(client: TestClient, monkeypatch) -> None:
    """Échoue sur le code d'avant : `raise` nu, donc une 500."""
    from sqlalchemy.exc import IntegrityError

    def _conflit(*a, **k):
        raise IntegrityError("INSERT", {}, Exception("UNIQUE"))

    monkeypatch.setattr("app.routers.interviews_libre._creer_interview_libre", _conflit)
    mission_id = _mission(client)
    r = client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": "x", "session_token": "tok-fantome"},
        follow_redirects=False,
    )
    assert r.status_code == 409

# --------------------------------------------------------------------------- #
# G4 : suppression de l'entretien et de ses jobs dans UNE transaction
# --------------------------------------------------------------------------- #
def test_suppression_entretien_et_jobs_tout_ou_rien(client: TestClient, monkeypatch) -> None:
    """Échoue sur le code d'avant : l'entretien était commité supprimé AVANT
    le ménage des jobs ; un échec de ce ménage laissait l'un sans l'autre."""
    mission_id = _mission(client)
    interview_id = _creer_a_traiter(mission_id, jeton="tok-g4", statut="fait")
    with SessionLocal() as db:
        db.add(InterviewSegmentJob(session_token="tok-g4", position=0, status="done", text="t"))
        db.commit()

    def _boom(*a, **k):
        raise RuntimeError("ménage impossible")

    monkeypatch.setattr("app.routers.interviews_gestion._supprimer_jobs_du_jeton", _boom, raising=False)
    monkeypatch.setattr("app.routers.interviews_gestion.delete_segment_jobs", _boom, raising=False)
    TestClient(app, raise_server_exceptions=False).post(
        f"/interviews/{interview_id}/delete", follow_redirects=False
    )
    with SessionLocal() as db:
        assert db.get(Interview, interview_id) is not None
        assert db.scalar(select(InterviewSegmentJob).where(
            InterviewSegmentJob.session_token == "tok-g4")) is not None