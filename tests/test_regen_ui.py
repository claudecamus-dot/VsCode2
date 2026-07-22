"""Bug « Régénérer IA » sans effet (2026-07-22) — verrous de régression.

Deux causes racines corrigées, chacune verrouillée ici :
1. htmx chargé depuis le CDN unpkg : réseau coupé/proxy → TOUS les hx-* inertes
   en silence — autosave ET le bouton « Régénérer (IA) » de la synthèse globale
   (hx-post sur type=button : sans htmx un clic ne fait strictement rien).
   → htmx est désormais VENDORÉ dans app/static et référencé en local.
2. Générations synchrones longues (30 s à minutes avec Ollama) sans aucun retour
   visuel sur les forms classiques → busy.js gèle le bouton dès la soumission
   (data-busy-label sur chaque form de génération).
Calqué sur test_difficultes.py : DB jetable, TestClient.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import GlobalSynthesis, Interview, Mission

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


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


def _mission_id() -> int:
    db = SessionLocal()
    try:
        m = Mission(name="Mission Regen UI")
        db.add(m)
        db.flush()
        db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
        db.add(GlobalSynthesis(
            mission_id=m.id, status="generated", contexte="- C", culture_adn="- C",
            forces_succes="- F", points_amelioration="- P", aspirations="- A"))
        db.commit()
        return m.id
    finally:
        db.close()


def test_htmx_est_vendore_en_local_pas_de_cdn(client: TestClient) -> None:
    """base.html référence /static/htmx.min.js — plus JAMAIS unpkg (une coupure
    CDN rendait tous les hx-* inertes sans aucune erreur visible)."""
    page = client.get("/missions")
    assert page.status_code == 200
    assert "/static/htmx.min.js" in page.text, "htmx local non référencé par base.html"
    assert "unpkg.com" not in page.text, "htmx encore chargé depuis le CDN unpkg"
    # Le fichier vendoré existe, est servi, et est bien htmx (pas un stub vide).
    fichier = STATIC / "htmx.min.js"
    assert fichier.exists(), "app/static/htmx.min.js absent — vendorer htmx (npm pack htmx.org)"
    assert fichier.stat().st_size > 10_000, "htmx.min.js suspicieusement petit"
    served = client.get("/static/htmx.min.js")
    assert served.status_code == 200 and "htmx" in served.text[:2000].lower()
    # Verrou de VERSION (revue adversariale) : un remplacement/downgrade silencieux
    # du fichier vendoré passait tous les tests — la version attendue est épinglée.
    assert "2.0.3" in served.text, "htmx vendoré n'est plus la 2.0.3 épinglée"


def test_busy_js_reference_et_servi(client: TestClient) -> None:
    """busy.js (feedback « génération en cours ») est référencé et servi."""
    page = client.get("/missions")
    assert "/static/busy.js" in page.text
    served = client.get("/static/busy.js")
    assert served.status_code == 200 and "data-busy-label" in served.text
    # Le contrat « générique » couvre aussi le <button> sans attribut type
    # (submit implicite en HTML — revue adversariale 2026-07-22).
    assert "button:not([type])" in served.text
    # Et le retour bfcache réarme la page (sinon bouton gelé à vie sur Back).
    assert "pageshow" in served.text


def test_forms_generation_portent_busy_label(client: TestClient) -> None:
    """Chaque form de génération IA synchrone porte data-busy-label : sans lui,
    un POST de 30 s à plusieurs minutes ne donne AUCUN signe de vie (bug
    « rien ne se déclenche »). Rendu réel pour recommandations ; au niveau
    source pour les templates qui exigent une mission plus riche. L'inventaire
    est EXHAUSTIF par grep des action="…/generate" (revue adversariale : la 1re
    version listait les templates à la main et ratait export_import.html)."""
    mid = _mission_id()
    page = client.get(f"/missions/{mid}/recommandations")
    assert page.status_code == 200
    assert 'data-busy-label="⏳' in page.text, "form recommandations sans busy-label"
    # Inventaire exhaustif : TOUT form POST classique vers une route *,/generate
    # doit porter data-busy-label sur sa balise <form …> (les chemins htmx
    # hx-post ont leur propre indicateur, hors périmètre).
    import re
    for tpl in TEMPLATES.rglob("*.html"):
        src = tpl.read_text(encoding="utf-8")
        for m in re.finditer(r"<form\b[^>]*action=\"[^\"]*/generate\"[^>]*>", src, re.S):
            assert "data-busy-label=" in m.group(0), (
                f"{tpl.name} : form de génération sans data-busy-label — {m.group(0)[:90]}…"
            )
    # export_import.html régénère aussi les axes : même confirm destructif que
    # recommandations.html (trou trouvé par la revue adversariale).
    exp = (TEMPLATES / "synthese" / "export_import.html").read_text(encoding="utf-8")
    assert "onsubmit" in exp and "confirm(" in exp, "export_import.html : régénération sans confirm"
    # libre_analyse.html : le form « Régénérer l'analyse (IA) » (route non-/generate).
    analyse = (TEMPLATES / "interviews" / "libre_analyse.html").read_text(encoding="utf-8")
    assert "data-busy-label=" in analyse, "form régénérer de libre_analyse.html sans busy-label"


def test_apercu_fiche_parite_chips_et_bandeau_resultats(client: TestClient) -> None:
    """Parité aperçu/PPT de la fiche reco (revue adversariale) : l'aperçu web
    doit refléter la fiche RÉELLE — chips Valeur/Complexité (plus de jauges
    donut) et résultats attendus en bandeau bas pleine largeur (plus dans la
    colonne gauche)."""
    apercu = (TEMPLATES / "synthese" / "apercu.html").read_text(encoding="utf-8")
    assert "ppt-chip" in apercu, "aperçu fiche sans chips Valeur/Complexité"
    assert "ppt-gauge" not in apercu, "l'aperçu montre encore les jauges donut supprimées du PPT"
    assert "ppt-strip" in apercu, "aperçu fiche sans bandeau résultats pleine largeur"
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".ppt-chip" in css and ".ppt-strip" in css, "styles chips/bandeau absents d'app.css"
    assert ".ppt-gauge" not in css, "CSS mort des jauges donut encore présent"
