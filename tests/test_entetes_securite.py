"""En-têtes HTTP de sécurité (finding audit-technique securite:critique du
2026-09-04, `.claude/audits/VSCode2.json` côté hub) : « FastAPI() instancié sans
le moindre add_middleware (0 occurrence dans app/), donc ni CSP, ni
X-Frame-Options, ni X-Content-Type-Options ».

Échec sur le code d'avant : aucune de ces clés n'était présente dans la réponse.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, engine, init_db
from app.entetes_securite import CSP
from app.main import app


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


def test_les_entetes_sont_poses_sur_une_page(client: TestClient) -> None:
    response = client.get("/missions")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Content-Security-Policy" in response.headers


def test_les_entetes_couvrent_aussi_le_403_de_la_garde_csrf(client: TestClient) -> None:
    """L'ordre d'installation est le piège : la garde CSRF court-circuite la
    requête, donc un middleware posé du mauvais côté ne verrait jamais sa
    réponse. C'est justement l'erreur qui laisserait la page d'erreur nue."""
    response = client.post(
        "/missions", data={"name": "x"},
        headers={"origin": "", "referer": ""}, follow_redirects=False,
    )
    assert response.status_code == 403
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_la_csp_coupe_le_canal_d_exfiltration() -> None:
    """Le point qui compte pour ce produit : aucune ressource ni connexion
    vers un domaine tiers. L'app est offline-first, rien de légitime n'est
    externe — un contenu injecté ne peut donc pas rapatrier de données
    d'entretien vers l'extérieur."""
    assert "default-src 'self'" in CSP
    assert "connect-src 'self'" in CSP
    assert "frame-ancestors 'none'" in CSP
    assert "object-src 'none'" in CSP
    assert "form-action 'self'" in CSP


def test_la_csp_laisse_vivre_ce_dont_les_ecrans_ont_besoin() -> None:
    """Contre-épreuve : une CSP trop stricte casse en SILENCE (aucune erreur
    serveur, juste un écran inerte). Les deux besoins réels du produit —
    scripts/styles inline des écrans d'enregistrement, et blob: pour la
    relecture audio — doivent rester autorisés."""
    assert "script-src 'self' 'unsafe-inline'" in CSP
    assert "style-src 'self' 'unsafe-inline'" in CSP
    assert "media-src 'self' blob:" in CSP
    assert "img-src 'self' data: blob:" in CSP


def test_le_css_et_le_js_locaux_restent_servis(client: TestClient) -> None:
    """`default-src 'self'` ne vaut que si le statique est bien servi depuis la
    même origine — vérifié réellement, pas supposé."""
    for chemin in ("/static/app.css", "/static/htmx.min.js"):
        reponse = client.get(chemin)
        assert reponse.status_code == 200, chemin
        assert reponse.headers["X-Content-Type-Options"] == "nosniff"
