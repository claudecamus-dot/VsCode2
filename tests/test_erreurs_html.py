"""404 de navigation -> page lisible (arbitrage 2026-10-14, handler central dans
app/main.py). JSON/fetch/HTMX et autres statuts gardent leur réponse d'avant."""
from __future__ import annotations

import html

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import DB_PATH, engine, init_db
from app.main import app

HTML = {"Accept": "text/html,application/xhtml+xml"}
FICHE = "/interviews/999999/finish"
MISSION = "/missions/999999/interviews/new"
INCONNUE = "/__route_inexistante_pa"


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


@pytest.mark.parametrize("chemin,attendu", [
    (FICHE, "Cet entretien n'existe plus."),
    (MISSION, "Cette mission n'existe plus."),
    (INCONNUE, "Cette page n'existe pas."),
])
def test_404_navigation_rend_une_page_lisible(client, chemin, attendu) -> None:
    r = client.get(chemin, headers=HTML)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert attendu in html.unescape(r.text)  # l'apostrophe est échappée (&#39;)
    assert 'href="/missions"' in r.text and "Voir les missions" in r.text
    assert "<h1>Introuvable</h1>" in r.text and "<title>Introuvable" in r.text
    assert 'role="alert"' not in r.text  # contenu statique : pas une alerte dynamique
    assert '{"detail"' not in r.text


@pytest.mark.parametrize("chemin", [FICHE, MISSION, INCONNUE])
@pytest.mark.parametrize("entetes", [
    {"Accept": "application/json"},
    {"Accept": "*/*"},
    {"HX-Request": "true", "Accept": "text/html"},
    {"Accept": ""},
], ids=["json", "etoile", "htmx", "sans-accept"])
def test_404_consommateurs_json_inchanges(client, chemin, entetes) -> None:
    r = client.get(chemin, headers=entetes)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    corps = r.json()
    assert set(corps) == {"detail"}
    assert corps["detail"] in ("Entretien introuvable.", "Mission introuvable.", "Not Found")
    assert "<h1>" not in r.text


def test_405_garde_sa_reponse_json_et_son_en_tete_allow(client) -> None:
    r = client.delete("/missions", headers=HTML)
    assert r.status_code == 405
    assert r.headers["content-type"].startswith("application/json")
    assert "allow" in r.headers
    assert r.json() == {"detail": "Method Not Allowed"}


def test_403_csrf_inchange_meme_pour_un_navigateur(client) -> None:
    r = client.post("/missions", data={"name": "x"}, follow_redirects=False,
                    headers={**HTML, "origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.headers["content-type"].startswith("application/json")
    assert "Origine non autorisée" in r.json()["error"]


def test_non_authentifie_garde_le_401_d_auth_pas_la_page_404() -> None:
    """Prouve UNIQUEMENT la précédence du middleware d'auth : il répond avant
    tout handler, donc ce test passerait aussi handler 404 supprimé. Il garde
    qu'un anonyme ne voie jamais la page « Introuvable » (fuite d'existence)."""
    anonyme = TestClient(app)
    anonyme.headers.pop("Authorization", None)
    anonyme.cookies.clear()
    r = anonyme.get(FICHE, headers=HTML, follow_redirects=False)
    assert r.status_code in (401, 503)
    assert "n'existe plus" not in r.text
    assert "<h1>Introuvable</h1>" not in r.text and 'role="alert"' not in r.text


def test_aucun_contenu_de_requete_n_est_reflete(client) -> None:
    r = client.get("/<script>alert(1)</script>?x=<b>pwn</b>", headers=HTML)
    assert r.status_code == 404
    assert "alert(1)" not in r.text and "pwn" not in r.text
    assert "script>alert" not in r.text


@pytest.fixture
def routes_detail_exotique():
    """Routes de TEST montées puis retirées (aucune route permanente : la table
    figée par test_route_inventory reste intacte)."""
    details = {"dict": {"code": "x"}, "liste": ["a", "b"], "none": None}
    avant = list(app.router.routes)
    for nom, detail in details.items():
        def _leve(detail=detail):
            raise HTTPException(status_code=404, detail=detail)
        app.add_api_route(f"/__test_detail_{nom}", _leve, methods=["GET"])

    def _leve_html():
        raise HTTPException(status_code=404, detail="<script>alert(7)</script>",
                            headers={"X-Test-Garde": "oui"})
    app.add_api_route("/__test_detail_html", _leve_html, methods=["GET"])
    yield list(details)
    app.router.routes[:] = avant


@pytest.mark.parametrize("nom", ["dict", "liste", "none"])
def test_404_a_detail_non_str_reste_404_jamais_500(client, routes_detail_exotique, nom) -> None:
    # Choix le plus sûr : un detail non-str retombe sur le handler PAR DÉFAUT (JSON).
    r = client.get(f"/__test_detail_{nom}", headers=HTML)
    assert r.status_code == 404
    if nom == "none":  # Starlette remplace None par « Not Found » (str) : page générique
        return
    assert r.headers["content-type"].startswith("application/json")
    assert "<h1>" not in r.text


def test_404_sous_static_reste_json_meme_pour_un_navigateur(client) -> None:
    r = client.get("/static/inexistant.css", headers=HTML)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert "<h1>" not in r.text


def test_detail_html_jamais_reflechi_et_en_tetes_conserves(client, routes_detail_exotique) -> None:
    r = client.get("/__test_detail_html", headers=HTML)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert "<script>alert(7)" not in r.text and "alert(7)" not in r.text
    assert "Cette page n'existe pas." in html.unescape(r.text)
    assert r.headers.get("x-test-garde") == "oui"  # exc.headers conservés (finding 8b)


@pytest.mark.parametrize("accept", ["text/html;q=0", "text/html; q=0.0, application/json"])
def test_html_q_zero_est_un_refus(client, accept) -> None:
    r = client.get(INCONNUE, headers={"Accept": accept})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_html_avec_q_positif_reste_html(client) -> None:
    r = client.get(INCONNUE, headers={"Accept": "text/html;q=0.8,*/*;q=0.1"})
    assert r.headers["content-type"].startswith("text/html")


def test_rendu_casse_retombe_sur_la_404_json_jamais_500(client, monkeypatch) -> None:
    from app import main as main_mod

    def _boum(*a, **k):
        raise RuntimeError("rendu cassé")
    monkeypatch.setattr(main_mod.templates, "TemplateResponse", _boum)
    r = client.get(INCONNUE, headers=HTML)
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}
