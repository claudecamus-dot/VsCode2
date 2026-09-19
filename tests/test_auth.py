"""Authentification de l'application (`app.auth`) — arbitrage utilisateur du
2026-09-19 : la cible d'usage devient **des clients externes**, alors que
l'app ne portait AUCUNE authentification (117 routes ouvertes, dont audio
d'entretien, transcriptions, exports et missions de clients réels).

Ce que ces tests prouvent, et pourquoi ils existent sous cette forme :

- le défaut est FERMÉ : la garde est un middleware global, pas un décorateur
  posé route par route. Un test sur une route quelconque (`/missions`) et un
  test sur une route AJOUTÉE à la volée (`/__route_inexistante_du_jour`)
  montrent qu'aucune énumération n'a à être tenue à jour ;
- un 401 est exigé SANS credentials, et le scénario métier doit passer AVEC.
  Un 200 sans authentification n'est pas un déploiement réussi ;
- mot de passe NON configuré => refus (503), jamais ouverture. Une garde qui
  s'efface quand sa configuration manque ne garde rien.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

MOT_DE_PASSE = "motdepasse-de-test"


def setup_module() -> None:
    from app.db import DB_PATH, engine, init_db

    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def teardown_module() -> None:
    from app.db import DB_PATH, engine

    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture(autouse=True)
def _mot_de_passe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_AUTH_PASSWORD", MOT_DE_PASSE)


def _client_anonyme() -> TestClient:
    """TestClient VRAIMENT anonyme : `tests/conftest.py` injecte des credentials
    par défaut dans tout TestClient pour que les 80 autres fichiers de test
    continuent d'exercer le produit — il faut donc les retirer ici, sinon ce
    test serait vert par construction."""
    from app.main import app

    client = TestClient(app)
    client.headers.pop("Authorization", None)
    client.cookies.clear()
    return client


def test_route_metier_sans_credentials_est_refusee() -> None:
    reponse = _client_anonyme().get("/missions", follow_redirects=False)
    assert reponse.status_code in (401, 403), reponse.status_code


def test_route_mutante_sans_credentials_est_refusee() -> None:
    reponse = _client_anonyme().post(
        "/missions", data={"name": "x"}, follow_redirects=False
    )
    assert reponse.status_code in (401, 403), reponse.status_code


def test_defaut_ferme_meme_sur_une_route_inconnue() -> None:
    """Le 404 lui-même est derrière la garde : un défaut fermé ne renseigne pas
    un anonyme sur la table de routage."""
    reponse = _client_anonyme().get("/__route_inexistante_du_jour")
    assert reponse.status_code in (401, 403), reponse.status_code


def test_avec_credentials_le_scenario_metier_passe() -> None:
    from app.main import app

    client = _client_anonyme()
    connexion = client.post(
        "/connexion",
        data={"mot_de_passe": MOT_DE_PASSE},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    assert connexion.status_code in (302, 303), connexion.status_code
    liste = client.get("/missions")
    assert liste.status_code == 200, liste.status_code
    creation = client.post(
        "/missions",
        data={"name": "Mission authentifiee"},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    assert creation.status_code in (302, 303), creation.status_code
    assert "/missions/" in creation.headers["location"]


def test_mauvais_mot_de_passe_ne_connecte_pas() -> None:
    client = _client_anonyme()
    reponse = client.post(
        "/connexion",
        data={"mot_de_passe": "pas-le-bon"},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    assert reponse.status_code in (401, 403), reponse.status_code
    assert _client_anonyme().get("/missions").status_code in (401, 403)


def test_page_de_connexion_et_statique_restent_publiques() -> None:
    client = _client_anonyme()
    assert client.get("/connexion").status_code == 200
    assert client.get("/static/app.css").status_code in (200, 404)


def test_mot_de_passe_non_configure_refuse_au_lieu_d_ouvrir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APP_AUTH_PASSWORD", raising=False)
    reponse = _client_anonyme().get("/missions", follow_redirects=False)
    assert reponse.status_code == 503, reponse.status_code


# --------------------------------------------------------------------------- #
# Revue adversariale du lot : deux facons de passer a cote d'une garde verte.
# --------------------------------------------------------------------------- #


def test_un_chemin_a_double_point_nest_jamais_public() -> None:
    """`/static/..%2fmissions` se decode en `/static/../missions` dans
    `request.url.path` : le seul prefixe public de l'app deviendrait alors un
    laissez-passer. La garde doit refuser AVANT de se reposer sur le routeur."""
    from app.auth import _public

    assert _public("/static/app.css") is True
    assert _public("/static/../missions") is False
    assert _public("/connexion") is True
    assert _public("/connexion/../missions") is False


def test_un_secret_non_ascii_refuse_au_lieu_de_lever() -> None:
    """`hmac.compare_digest` sur deux `str` leve TypeError hors ASCII : un mot
    de passe accentue rendait un 500 au lieu d'un refus."""
    from app.auth import egal

    assert egal("mot-de-passe-é", "mot-de-passe-é") is True
    assert egal("mauvais", "mot-de-passe-é") is False


def test_un_jeton_de_session_expire_ne_vaut_plus() -> None:
    from app.auth import jeton_valide, signer

    assert jeton_valide(MOT_DE_PASSE, signer(MOT_DE_PASSE, int(time.time()) + 60))
    assert not jeton_valide(MOT_DE_PASSE, signer(MOT_DE_PASSE, int(time.time()) - 1))
    # Jeton signe avec un AUTRE secret : rotation du mot de passe = revocation.
    assert not jeton_valide(MOT_DE_PASSE, signer("autre-secret", int(time.time()) + 60))
