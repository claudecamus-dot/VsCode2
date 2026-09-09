"""Protection anti-CSRF (`app.csrf.verifier_origine`) — finding audit-technique
securite:critique du 2026-09-04 : POST /missions/{id}/delete (et les 77 autres
routes mutatives) purgeait irréversiblement mission + audio sans aucun
contrôle Origin/Referer, atteignable cross-origin sans preflight (requête
POST simple au sens CORS). Pendant du correctif VSCode1 (`app/src/csrf.js`,
commit ef3ae7b), même patron `verifier_origine`/`meme_origine`.

Reproduit l'attaque exacte de l'audit (200/303 avant, 403 après) + une
deuxième route mutante (POST /missions, sans rapport avec la suppression)
pour prouver que la garde est globale, pas câblée route par route.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import Mission


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


def _mission_id(client: TestClient, name: str) -> int:
    response = client.post("/missions", data={"name": name}, follow_redirects=False)
    return int(response.headers["location"].rsplit("/", 1)[-1])


def test_get_ne_verifie_pas_l_origine(client: TestClient) -> None:
    """Hors périmètre CSRF : la lecture ne mute rien, la garde ne doit ni
    exiger Origin ni la valider."""
    response = client.get("/missions", headers={"origin": ""})
    assert response.status_code == 200


def test_post_sans_origin_ni_referer_est_bloque(client: TestClient) -> None:
    """Reproduit l'attaque de l'audit : une requête POST sans Origin ni
    Referer (navigateur qui les strippe, ou attaquant qui les omet) sur une
    route mutante quelconque — ici la création de mission, pas seulement la
    suppression, pour prouver la garde GLOBALE plutôt qu'un correctif câblé
    route par route.

    Échoue sur le code d'avant : la mission était créée (303), aucune garde."""
    avant = SessionLocal().query(Mission).count()
    response = client.post(
        "/missions", data={"name": "Mission via CSRF"},
        headers={"origin": "", "referer": ""}, follow_redirects=False,
    )
    assert response.status_code == 403
    assert "Origine non autorisée" in response.json()["error"]
    apres = SessionLocal().query(Mission).count()
    assert apres == avant, "aucune mission ne doit avoir été créée malgré le 403"


def test_post_origine_etrangere_est_bloque(client: TestClient) -> None:
    """Origin présent mais pointant vers un autre hôte (page attaquante réelle,
    pas seulement un en-tête absent) — même verdict : 403."""
    response = client.post(
        "/missions", data={"name": "Mission via attaque cross-origin"},
        headers={"origin": "http://attaquant.exemple"}, follow_redirects=False,
    )
    assert response.status_code == 403


def test_delete_mission_sans_origin_est_bloque_et_ne_supprime_rien(
    client: TestClient,
) -> None:
    """La route de l'audit elle-même : suppression irréversible (mission +
    audio) — bloquée, la mission survit."""
    mission_id = _mission_id(client, "Mission à ne pas perdre")

    response = client.post(
        f"/missions/{mission_id}/delete",
        headers={"origin": "", "referer": ""},
        follow_redirects=False,
    )
    assert response.status_code == 403

    db = SessionLocal()
    try:
        assert db.get(Mission, mission_id) is not None
    finally:
        db.close()


def test_post_avec_la_bonne_origine_passe(client: TestClient) -> None:
    """Chemin nominal préservé : Origin correspondant au Host (ce que le
    patch de `conftest.py` injecte par défaut pour tout le reste de la
    suite) continue de passer — la garde ne doit pas bloquer l'usage normal."""
    response = client.post(
        "/missions", data={"name": "Mission légitime"}, follow_redirects=False,
    )
    assert response.status_code == 303


def test_referer_seul_suffit_quand_origin_est_absent(client: TestClient) -> None:
    """Un navigateur peut n'envoyer que Referer sur certaines requêtes
    (formulaire classique sans Origin) — la garde accepte Referer en repli,
    comme `app.csrf.meme_origine` le documente."""
    response = client.post(
        "/missions", data={"name": "Mission via Referer"},
        headers={"origin": "", "referer": "http://testserver/missions/new"},
        follow_redirects=False,
    )
    assert response.status_code == 303


# --- Interaction avec les en-têtes de sécurité (signalement du 2026-09-08) ----
#
# Sous `Referrer-Policy: no-referrer`, la spec Fetch met l'en-tête Origin à
# `null` (et retire le Referer) sur toute requête NON-CORS qui n'est pas un
# GET/HEAD — une soumission de formulaire est une navigation, donc non-CORS.
# Résultat : 403 sur Supprimer, Démarrer, changement de mode… depuis un vrai
# navigateur, alors que les `fetch` (mode cors) gardaient leur Origin et que la
# suite restait verte (le conftest injecte un Origin explicite dans tous les
# TestClient). Reproduit en Edge headless via CDP : `origin: null -> 403`.


def test_origin_null_reste_refuse(client: TestClient) -> None:
    """`null` est ce qu'envoie un formulaire cross-site depuis une origine
    opaque (sandbox, data:) : l'accepter ouvrirait exactement la porte que la
    garde ferme. Le correctif est du côté de la politique de Referer, pas ici."""
    resp = client.post(
        "/missions/0/interviews/record/backup",
        headers={"Origin": "null"},
    )
    assert resp.status_code == 403
    assert "CSRF" in resp.json()["error"]


def test_la_politique_de_referer_laisse_partir_origin_sur_les_formulaires(
    client: TestClient,
) -> None:
    """Toute politique qui rend Origin `null` sur un POST de même origine rend
    l'app inutilisable au clavier-souris. `same-origin` garde l'intention (jamais
    de Referer vers un tiers) sans casser la garde CSRF."""
    resp = client.get("/missions")
    politique = resp.headers.get("Referrer-Policy", "")
    assert politique, "les en-têtes de sécurité doivent être posés"
    assert politique != "no-referrer", (
        "no-referrer force Origin: null sur les POST de formulaire -> 403 CSRF"
    )
    # `same-origin` et RIEN d'autre : c'est la seule politique qui satisfait
    # les deux contraintes à la fois — Origin part sur un POST de même origine
    # (sinon 403), et rien ne part vers un tiers. Les autres qui réparent le
    # 403 fuient toutes quelque chose : `no-referrer-when-downgrade` envoie
    # l'URL COMPLÈTE (identifiants de mission et d'entretien) à tout site
    # https, et à tout site si l'app est servie en http hors boucle locale ;
    # `strict-origin*` / `origin-when-cross-origin` envoient l'origine. Une
    # liste blanche plus large laissait ce test vert sur une politique qui
    # rouvre exactement la fuite que `no-referrer` fermait (revue 2026-09-08).
    assert politique == "same-origin"


# --- Le journal des refus (revue adversariale du 2026-09-08) -------------------


@pytest.fixture
def journal_csrf(caplog: pytest.LogCaptureFixture):
    from app import csrf

    csrf.regulateur.reinitialiser()
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        yield caplog
    csrf.regulateur.reinitialiser()


def _lignes_csrf(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("CSRF :")]


def test_le_refus_dit_ce_qu_il_a_compare(client: TestClient, journal_csrf) -> None:
    """Le signalement d'origine : un 403 sans aucun moyen de savoir quelle
    origine avait été vue. La ligne porte Host, Origin et Referer — sur le
    logger d'uvicorn, pas sur un logger sans handler."""
    resp = client.post(
        "/missions/0/interviews/record/backup",
        headers={"Origin": "http://evil.example", "Referer": "http://evil.example/p"},
    )
    assert resp.status_code == 403
    lignes = _lignes_csrf(journal_csrf)
    assert len(lignes) == 1, lignes
    assert "POST /missions/0/interviews/record/backup" in lignes[0]
    assert "Origin='http://evil.example'" in lignes[0]
    assert "Referer='http://evil.example/p'" in lignes[0]
    assert journal_csrf.records[-1].name == "uvicorn.error"


def test_le_journal_des_refus_est_regule_et_tronque(
    client: TestClient, journal_csrf, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une page tierce qui boucle des POST refusés ne doit pas pouvoir écrire
    16 Kio par requête dans le journal : une ligne par intervalle, valeurs
    tronquées — le CHEMIN compris, pas seulement les en-têtes —, et les refus
    tus sont comptés puis annoncés. L'horloge est injectée : le test ne dépend
    pas de la vitesse du poste (revue du 2026-09-09, A14)."""
    from app import csrf

    horloge = [1000.0]
    monkeypatch.setattr(csrf.time, "monotonic", lambda: horloge[0])

    enorme = "http://" + "a" * 5000
    for _ in range(5):
        assert client.post(
            "/missions/0/interviews/record/backup", headers={"Origin": enorme},
        ).status_code == 403
    lignes = _lignes_csrf(journal_csrf)
    assert len(lignes) == 1, "un seul refus journalisé dans l'intervalle"
    assert len(lignes[0]) < 600, len(lignes[0])
    assert "(+4807 car.)" in lignes[0]
    assert csrf.regulateur.tus == 4

    # L'intervalle écoulé : le refus suivant part, et dit combien ont été tus.
    horloge[0] += csrf._INTERVALLE_JOURNAL_S + 1
    client.post("/missions/0/interviews/record/backup", headers={"Origin": "null"})
    lignes = _lignes_csrf(journal_csrf)
    assert len(lignes) == 2
    assert "4 refus non journalisé(s)" in lignes[1]
    assert csrf.regulateur.tus == 0

    # Un chemin de 6 000 caractères voyage dans la ligne comme les en-têtes :
    # tronqué lui aussi (A1 : 6 082 caractères mesurés avant le correctif).
    horloge[0] += csrf._INTERVALLE_JOURNAL_S + 1
    client.post("/" + "z" * 6000, headers={"Origin": "null"})
    lignes = _lignes_csrf(journal_csrf)
    assert len(lignes) == 3
    assert len(lignes[2]) < 600, len(lignes[2])
    assert "(+5801 car.)" in lignes[2]
