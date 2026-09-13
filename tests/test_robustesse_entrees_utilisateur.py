"""Filet de robustesse sur les points d'entrée utilisateur sans garde
(constat audit-technique VSCode2, 2026-09-04, dimension robustesse,
`.claude/audits/VSCode2.json` côté hub) : import .docx corrompu (trame ET
entretien), champ caché JSON tronqué/altéré à la confirmation d'import de
trame, template PPTX déclenchant un débordement géométrique à l'export — plus
le filet de dernier recours applicatif (`@app.exception_handler`) qui
n'existait pas du tout.

Chaque test reproduit le cas RÉEL nommé par l'audit sur un flux HTTP complet
(vraies fonctions `python-docx`, `json.loads`, `build_presentation`), pas un
mock qui esquiverait le chemin de code justement en cause.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview


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


@pytest.fixture
def client_http() -> TestClient:
    """Client qui n'escamote PAS l'erreur serveur en exception Python : c'est
    la réponse reçue par le navigateur qu'on veut voir (même convention que
    `test_interview_pdf_export.py:client_http`)."""
    return TestClient(app, raise_server_exceptions=False)


def _creer_mission(client: TestClient, name: str) -> str:
    response = client.post(
        "/missions",
        data={"name": name, "description": "Description de test"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def _creer_mission_avec_entretien(client: TestClient, name: str) -> str:
    """`synthese/apercu.html` (rendu par `export_pptx` en cas d'erreur) masque
    tout — y compris le message d'erreur — derrière l'écran « aucun entretien »
    tant que la mission n'en a aucun (`{% if not mission.interviews %}`) : il
    en faut un, minimal, pour observer le comportement réel de la route."""
    mission_id = _creer_mission(client, name)
    session = SessionLocal()
    try:
        session.add(
            Interview(
                mission_id=int(mission_id),
                interviewee_name="Alice Martin",
                mode="libre",
                status="done",
            )
        )
        session.commit()
    finally:
        session.close()
    return mission_id


# Extension .docx correcte, contenu invalide : ni un zip, ni a fortiori un
# paquet OPC — reproduit un fichier renommé ou tronqué à l'upload, PAS une
# erreur d'extraction IA (déjà couverte par ailleurs).
_DOCX_CORROMPU = b"Ceci n'est pas un fichier .docx valide, juste du texte."


# --------------------------------------------------------------------------- #
# 1. app/routers/trames.py:import_docx — .docx corrompu (audit robustesse #2)
# --------------------------------------------------------------------------- #
def test_import_trame_docx_corrompu_ne_leve_pas_500(client: TestClient) -> None:
    """`parse_docx_bytes` rouvre le fichier via `Document()` (python-docx)
    sans garde : avant correctif, `zipfile.BadZipFile` remontait telle
    quelle en 500 brut alors que seule l'extension était vérifiée."""
    mission_id = _creer_mission(client, "Trame docx corrompu")
    response = client.post(
        f"/missions/{mission_id}/trame/import",
        files={
            "file": (
                "import.docx",
                _DOCX_CORROMPU,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "invalide ou corrompu" in response.text


def test_import_trame_docx_corrompu_en_mode_ia_ne_leve_pas_500(client: TestClient) -> None:
    """Même contenu corrompu, `ai_mode=True` cette fois : le fichier est relu
    par `extract_text_bytes` (même famille d'erreur), sur le second des deux
    call-sites touchés par ce même défaut."""
    mission_id = _creer_mission(client, "Trame docx corrompu IA")
    response = client.post(
        f"/missions/{mission_id}/trame/import",
        files={
            "file": (
                "import.docx",
                _DOCX_CORROMPU,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"ai_mode": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "invalide ou corrompu" in response.text


# --------------------------------------------------------------------------- #
# 2. app/routers/interviews.py — import d'entretien depuis un .docx corrompu
# --------------------------------------------------------------------------- #
def test_import_interview_docx_corrompu_ne_leve_pas_500(client: TestClient) -> None:
    """Même famille de défaut sur le chemin d'import d'entretien depuis un
    document : le `try/except` existant n'attrapait que
    `InterviewExtractAIError`, pas l'échec de lecture du fichier lui-même."""
    mission_id = _creer_mission(client, "Entretien docx corrompu")
    response = client.post(
        f"/missions/{mission_id}/interviews/import",
        files={
            "file": (
                "entretien.docx",
                _DOCX_CORROMPU,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"interviewee_name": "Alice Martin"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "invalide ou corrompu" in response.text


# --------------------------------------------------------------------------- #
# 3. app/routers/trames.py:import_confirm — champ caché JSON tronqué/altéré
# --------------------------------------------------------------------------- #
def test_import_confirm_trame_json_tronque_rend_400_pas_500(client: TestClient) -> None:
    """`_parsed_from_json` fait `json.loads` sans garde sur le champ caché qui
    reposte toute la trame — tronqué (navigateur, proxy, taille de champ),
    `JSONDecodeError` remontait brute avant correctif."""
    mission_id = _creer_mission(client, "Confirm JSON tronque")
    response = client.post(
        f"/missions/{mission_id}/trame/import/confirm",
        data={"parsed": '{"name": "Trame importée", "themes": [', "keep": []},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "invalides ou tronquées" in response.json()["detail"]


def test_import_confirm_trame_json_pas_un_objet_rend_400_pas_500(client: TestClient) -> None:
    """Même route : JSON valide mais pas un objet (une liste) — `.get()`
    appelé sur chaque entrée lève `AttributeError`, pas capturée avant
    correctif."""
    mission_id = _creer_mission(client, "Confirm JSON liste")
    response = client.post(
        f"/missions/{mission_id}/trame/import/confirm",
        data={"parsed": '["pas un objet"]', "keep": []},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "invalides ou tronquées" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# 3 bis. app/routers/interviews.py — les DEUX confirmations sœurs, laissées nues
#        quand celle des trames a été protégée (audit-technique du 2026-09-13)
# --------------------------------------------------------------------------- #
def test_import_confirm_entretien_json_tronque_rend_400_pas_500(client: TestClient) -> None:
    """Même défaut, même forme, autre fichier : `import_interview_confirm`
    faisait `json.loads(proposed)` sans garde sur le champ caché qui reposte
    toute la proposition d'import. L'incohérence était interne au dépôt — la
    confirmation de TRAME a été protégée le 2026-09-04, ses deux sœurs
    d'entretien ne l'ont jamais été. C'est l'écran où l'utilisateur vient de
    RELIRE sa proposition : une 500 la lui fait perdre juste après validation."""
    mission_id = _creer_mission(client, "Confirm entretien JSON tronque")
    response = client.post(
        f"/missions/{mission_id}/interviews/import/confirm",
        data={"proposed": '{"identity": {"interviewee_name": "Ada"', "keep": []},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "recommence" in response.json()["detail"]


def test_import_confirm_entretien_json_pas_un_objet_rend_400_pas_500(
    client: TestClient,
) -> None:
    """JSON valide mais pas un objet : `data.get(...)` lèverait `AttributeError`."""
    mission_id = _creer_mission(client, "Confirm entretien JSON liste")
    response = client.post(
        f"/missions/{mission_id}/interviews/import/confirm",
        data={"proposed": '["pas un objet"]', "keep": []},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "format attendu" in response.json()["detail"]


def test_import_confirm_entretien_selection_non_numerique_rend_400_pas_500(
    client: TestClient,
) -> None:
    """`{int(k) for k in keep}` coerçait une `list[str] = Form([])` sans garde :
    une case cochée dont la valeur n'est pas un entier levait `ValueError`."""
    mission_id = _creer_mission(client, "Confirm entretien keep non numerique")
    response = client.post(
        f"/missions/{mission_id}/interviews/import/confirm",
        data={"proposed": '{"identity": {}, "answers": []}', "keep": ["pas-un-entier"]},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "sélection" in response.json()["detail"]


def test_import_confirm_entretien_tranches_manquantes_non_numerique_passe(
    client: TestClient,
) -> None:
    """`int(data.get("tranches_manquantes") or 0)` levait, lui aussi. Ce
    champ-là n'alimente qu'un bandeau d'information : le repli silencieux est le
    bon, l'import DOIT aboutir. La distinction est le cœur du correctif — on ne
    refuse que ce dont la perte perdrait du contenu d'entretien."""
    mission_id = _creer_mission(client, "Confirm entretien tranches illisibles")
    response = client.post(
        f"/missions/{mission_id}/interviews/import/confirm",
        data={
            "proposed": '{"identity": {"interviewee_name": "Ada"}, '
                        '"tranches_manquantes": "beaucoup", "answers": []}',
            "keep": [],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    db = SessionLocal()
    try:
        cree = db.query(Interview).filter(Interview.interviewee_name == "Ada").first()
        assert cree is not None, "l'import doit aboutir malgre le compteur illisible"
        assert cree.tranches_manquantes == 0
    finally:
        db.close()


def test_confirm_notes_json_tronque_rend_400_pas_500(client: TestClient) -> None:
    """Second site du même défaut : `confirm_notes`, la confirmation des notes
    libres. Protégé par le même chemin que son jumeau."""
    mission_id = _creer_mission_avec_entretien(client, "Confirm notes JSON tronque")
    db = SessionLocal()
    try:
        interview_id = db.query(Interview).order_by(Interview.id.desc()).first().id
    finally:
        db.close()
    response = client.post(
        f"/interviews/{interview_id}/notes/confirm",
        data={"proposed": '{"answers": [', "keep": []},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "recommence" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# 4. app/routers/export.py:export_pptx — template déclenchant un débordement
# --------------------------------------------------------------------------- #
def test_export_pptx_template_incompatible_ne_leve_pas_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`build_presentation` lève `RuntimeError` PAR CONCEPTION sur
    débordement géométrique (garde-fou US7.1, `pptx_export/build.py`) : un
    template client aux dimensions inattendues ne doit pas faire planter la
    route qui sert le livrable principal, seulement échouer proprement."""
    mission_id = _creer_mission_avec_entretien(client, "Export PPTX template incompatible")

    def _echoue(*args, **kwargs):
        raise RuntimeError("Export PPT : formes hors cadre détectées —\n...")

    monkeypatch.setattr("app.routers.export.build_presentation", _echoue)
    response = client.get(f"/missions/{mission_id}/export/pptx", follow_redirects=False)
    assert response.status_code == 200
    assert "ne convient pas à ce contenu" in response.text


def test_export_pptx_slide_manipulation_invalide_ne_leve_pas_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second type d'échec « par conception » du même appel : `ValueError`
    levée par `pptx_deck.py` sur une manipulation de slides du template
    incompatible (assertion d'unicité, slide absente de `sldIdLst`)."""
    mission_id = _creer_mission_avec_entretien(client, "Export PPTX slides incompatibles")

    def _echoue(*args, **kwargs):
        raise ValueError("slide id=1 absente de sldIdLst")

    monkeypatch.setattr("app.routers.export.build_presentation", _echoue)
    response = client.get(f"/missions/{mission_id}/export/pptx", follow_redirects=False)
    assert response.status_code == 200
    assert "ne convient pas à ce contenu" in response.text


# --------------------------------------------------------------------------- #
# 5. Filet de dernier recours applicatif — app/main.py:erreur_inattendue
# --------------------------------------------------------------------------- #
def test_exception_non_prevue_rend_500_propre_pas_de_trace_brute(
    client_http: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avant correctif, l'app ne déclarait AUCUN `@app.exception_handler` ni
    middleware d'erreur : une exception échappant à tous les `try/except`
    ciblés (régression non prévue, pas nécessairement l'une des 4 ci-dessus)
    retombait sur le texte brut Starlette « Internal Server Error ». Ici, une
    exception qu'aucun `except` de la route ne cible (`KeyError`, pas
    `RuntimeError`/`ValueError`) doit quand même rendre un 500 JSON
    générique, sans fuiter le type ni le message de l'exception."""
    mission_id = _creer_mission(client_http, "Filet global")

    def _explose(*args, **kwargs):
        raise KeyError("régression non prévue, hors du try/except ciblé")

    monkeypatch.setattr("app.routers.export.axes_of", _explose)
    response = client_http.get(f"/missions/{mission_id}/export/pptx", follow_redirects=False)

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Erreur interne inattendue."}
    assert "KeyError" not in response.text
    assert "Traceback" not in response.text


def test_exception_dans_verifier_origine_avant_call_next_rend_500_propre(
    client_http: TestClient, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Constat audit-technique robustesse VSCode2 (2026-09-11) : le handler
    générique ne couvrirait pas une exception levée dans `verifier_origine`
    (middleware CSRF, `app/main.py`) AVANT `call_next` — hors du périmètre de
    l'`ExceptionMiddleware` qui porte `erreur_inattendue`, donc réponse brute
    de Starlette et log hors logger applicatif.

    Vérifié réellement (pas seulement lu) : avec le `BaseHTTPMiddleware` de la
    version de Starlette épinglée par ce dépôt (`create_collapsing_task_group`,
    cf. `starlette/middleware/base.py`), une exception levée AVANT `call_next`
    dans `verifier_origine` se propage jusqu'au `ServerErrorMiddleware` — le
    plus extérieur de la pile — qui invoque bien le handler applicatif
    `erreur_inattendue` (log `app.main` + JSON générique), pas une réponse
    brute. Ce test fige ce comportement : il doit rester rouge si un futur
    changement de version de Starlette (ou de l'ordre des middlewares)
    réintroduit le contournement décrit par le constat."""
    import app.csrf as csrf

    def _explose(request):
        raise RuntimeError("exception avant call_next, dans le middleware CSRF")

    monkeypatch.setattr(csrf, "meme_origine", _explose)

    with caplog.at_level("ERROR", logger="app.main"):
        response = client_http.post(
            "/missions", headers={"origin": "http://evil.example"},
            data={"name": "x", "description": "x"},
        )

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Erreur interne inattendue."}
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text
    # Logué par le logger APPLICATIF (`erreur_inattendue`), pas seulement par
    # une trace Starlette par défaut hors de ce logger.
    assert any(
        record.name == "app.main" and "Exception non gérée" in record.message
        for record in caplog.records
    )
