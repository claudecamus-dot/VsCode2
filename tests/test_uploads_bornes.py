"""Bornes de taille et anti-zip-bomb sur les uploads (finding audit-technique
securite:critique du 2026-09-04, `.claude/audits/VSCode2.json` côté hub) :
« Uploads sans plafond de taille et décompression zip non bornée (zip-bomb) sur
deux chemins non authentifiés : le contenu est lu entièrement en RAM par
`await file.read()` puis dépaqueté par python-docx et python-pptx avant toute
validation de taille ».

Chaque test passe par le flux HTTP réel (vrai `UploadFile`, vraies routes),
avec une VRAIE archive zip forgée — pas un mock, qui esquiverait justement le
dépaquetage en cause.

Échec sur le code d'avant : les routes appelaient `await file.read()` sans
argument et passaient les octets directement à `Document()`/`Presentation()`,
donc 200 et dépaquetage, jamais de refus.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview
from app.uploads import (
    MAX_UPLOAD_BYTES,
    MAX_ZIP_ENTREES,
    UploadTropVolumineux,
    verifier_zip_borne,
)


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


def _creer_mission(client: TestClient, name: str) -> str:
    response = client.post(
        "/missions", data={"name": name}, follow_redirects=False
    )
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def _creer_mission_avec_entretien(client: TestClient, name: str) -> str:
    """`synthese/apercu.html` masque tout — y compris le message d'erreur —
    derrière l'écran « aucun entretien » tant que la mission n'en a aucun : il
    en faut un, minimal, pour observer le comportement réel de la route (même
    contrainte que `test_robustesse_entrees_utilisateur.py`)."""
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


def _zip_bomb(taille_declaree: int = 400 * 1024 * 1024) -> bytes:
    """Archive minuscule dont le contenu déclaré est énorme — le cas exact que
    python-docx/python-pptx dépaquetaient sans rien vérifier. Zéros purs : ils
    se compriment d'un facteur ~1000, comme un vrai zip-bomb."""
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"\0" * taille_declaree)
    return tampon.getvalue()


# --------------------------------------------------------------------------- #
# Garde 2 — dépaquetage borné, au niveau du helper
# --------------------------------------------------------------------------- #
def test_zip_bomb_refuse_avant_tout_depaquetage() -> None:
    """400 Mo déclarés dans une archive de quelques Ko : refusée sur le
    répertoire central, sans qu'un seul octet ne soit décompressé."""
    with pytest.raises(UploadTropVolumineux):
        verifier_zip_borne(_zip_bomb())


def test_archive_normale_passe() -> None:
    """Le cas légitime ne doit PAS être refusé : un .docx réel est du XML peu
    volumineux, très en dessous des deux plafonds."""
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"<w:document>Bonjour</w:document>")
    verifier_zip_borne(tampon.getvalue())  # ne lève pas


def test_ratio_anormal_refuse_meme_sous_le_plafond_absolu() -> None:
    """Amplification seule : 30 Mo décompressés depuis quelques Ko restent sous
    le plafond absolu (300 Mo) mais dépassent le ratio — refusés quand même,
    sinon le plafond absolu suffirait à passer en boucle."""
    with pytest.raises(UploadTropVolumineux):
        verifier_zip_borne(_zip_bomb(taille_declaree=30 * 1024 * 1024))


def _zip_innombrable(entrees: int = 20_000) -> bytes:
    """Archive de quelques Mo qui ne déclare AUCUN octet décompressé mais des
    dizaines de milliers d'entrées vides : elle passe les trois plafonds
    existants (taille brute, total décompressé, ratio) puisqu'ils raisonnent
    tous sur des octets. Le coût est ailleurs — dans le nombre d'objets que la
    lecture du répertoire central fabrique."""
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_STORED) as archive:
        for i in range(entrees):
            archive.writestr(str(i), b"")
    return tampon.getvalue()


def test_archive_a_entrees_innombrables_refusee() -> None:
    """Troisième forme de zip-bomb, celle que les plafonds en octets ne voient
    pas : l'amplification porte sur le NOMBRE d'entrées, pas sur leur taille.

    Mesuré le 2026-09-09 sur le venv du projet, avant ce plafond : 200 000
    entrées vides tiennent dans 16,6 Mo envoyés (sous les 40 Mo), déclarent 0
    octet décompressé (sous les 300 Mo, ratio 0) — donc ACCEPTÉES par les trois
    gardes en octets — et coûtent 6,94 s de CPU et 111 Mo de pic mémoire dans
    le thread de la requête, sur une route non authentifiée."""
    with pytest.raises(UploadTropVolumineux):
        verifier_zip_borne(_zip_innombrable())


def test_le_nombre_d_entrees_est_lu_sans_indexer_l_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La borne ne vaut que si elle tombe AVANT la construction de l'index :
    c'est cette construction qui coûte les 6,94 s mesurées. Un refus qui
    appellerait `infolist()` pour compter aurait déjà payé la facture qu'il
    prétend éviter — on vérifie donc que `zipfile.ZipFile` n'est jamais
    instancié sur ce contenu."""
    contenu = _zip_innombrable()

    class _ZipFileInterdit:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError(
                "l'archive a été indexée avant d'être refusée sur son nombre d'entrées"
            )

    monkeypatch.setattr(zipfile, "ZipFile", _ZipFileInterdit)
    with pytest.raises(UploadTropVolumineux):
        verifier_zip_borne(contenu)


def test_le_plafond_d_entrees_laisse_passer_les_vrais_documents() -> None:
    """Garde-fou sur la garde, exercé sur du RÉEL et non sur la seule
    constante : les deux .pptx versionnés du dépôt passent la garde. Comptes
    mesurés le 2026-09-09 — `app/assets/template-octo.pptx` 149 entrées,
    `docs/exemples/deck-restitution-exemple.pptx` 169 ; le plus gros document
    vu sur le poste, un template client sous `data/pptx_templates/` (non
    versionné), en compte 416. Un plafond descendu à cet ordre de grandeur
    refuserait un fichier parfaitement légitime."""
    racine = Path(__file__).resolve().parents[1]
    reels = [
        racine / "app" / "assets" / "template-octo.pptx",
        racine / "docs" / "exemples" / "deck-restitution-exemple.pptx",
    ]
    for chemin in reels:
        assert chemin.exists(), f"document de référence absent : {chemin}"
        verifier_zip_borne(chemin.read_bytes())  # ne lève pas
    assert MAX_ZIP_ENTREES >= 4000


def test_archive_corrompue_n_est_pas_un_probleme_de_taille() -> None:
    """Distinction que les routes rendent par deux messages différents : un
    fichier corrompu lève `BadZipFile`, jamais `UploadTropVolumineux` — sinon
    l'utilisateur serait envoyé réduire un fichier sain."""
    with pytest.raises(zipfile.BadZipFile):
        verifier_zip_borne(b"ceci n'est pas une archive")


# --------------------------------------------------------------------------- #
# Garde 1 + 2 sur les routes réelles
# --------------------------------------------------------------------------- #
def test_import_trame_refuse_le_zip_bomb(client: TestClient) -> None:
    """`POST /missions/{id}/trame/import` — chemin non authentifié cité par
    l'audit (`trames.py:201-202` via `docx_trame.py:229`)."""
    mission_id = _creer_mission(client, "Mission bombe trame")
    response = client.post(
        f"/missions/{mission_id}/trame/import",
        files={"file": ("piege.docx", _zip_bomb(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"ai_mode": "false"},
    )
    assert response.status_code == 200
    assert "compression anormal" in response.text or "décompressé dépasse" in response.text


def test_import_entretien_refuse_le_zip_bomb(client: TestClient) -> None:
    """Second chemin .docx, celui de l'entretien (`interviews.py`) — la garde
    doit tenir sur les DEUX, pas seulement sur la trame."""
    mission_id = _creer_mission(client, "Mission bombe entretien")
    response = client.post(
        f"/missions/{mission_id}/interviews/import",
        files={"file": ("piege.docx", _zip_bomb(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"interviewee_name": "Test"},
    )
    assert response.status_code == 200
    assert "compression anormal" in response.text or "décompressé dépasse" in response.text


def test_upload_template_pptx_refuse_le_zip_bomb(client: TestClient) -> None:
    """`POST /missions/{id}/pptx-template` — l'autre chemin cité
    (`export.py:345-349`), qui validait bien le format mais jamais la taille."""
    mission_id = _creer_mission_avec_entretien(client, "Mission bombe template")
    response = client.post(
        f"/missions/{mission_id}/pptx-template",
        files={"file": ("piege.pptx", _zip_bomb(), "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
    )
    assert response.status_code == 200
    assert "compression anormal" in response.text or "décompressé dépasse" in response.text


def test_import_trame_refuse_l_archive_a_entrees_innombrables(
    client: TestClient,
) -> None:
    """Même route, troisième forme de bombe : l'utilisateur doit voir le même
    écran d'import avec un message, pas un 500 ni une requête qui s'éternise.
    C'est ce que ne prouve pas un test au niveau du helper seul."""
    mission_id = _creer_mission(client, "Mission bombe entrees")
    response = client.post(
        f"/missions/{mission_id}/trame/import",
        files={"file": ("piege.docx", _zip_innombrable(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"ai_mode": "false"},
    )
    assert response.status_code == 200
    assert "entrées" in response.text


def test_upload_trop_gros_refuse_avant_de_tout_charger_en_ram(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plafond de taille brute : abaissé à 4 Ko le temps du test pour ne pas
    faire transiter 40 Mo dans la suite. C'est bien la garde de `lire_upload_borne`
    qui répond — le fichier envoyé est un zip PARFAITEMENT valide, donc le
    message « corrompu » serait un faux diagnostic."""
    monkeypatch.setattr("app.uploads.MAX_UPLOAD_BYTES", 4096)
    mission_id = _creer_mission(client, "Mission fichier lourd")

    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("word/document.xml", b"x" * 20000)
    gros_docx_valide = tampon.getvalue()
    assert len(gros_docx_valide) > 4096

    response = client.post(
        f"/missions/{mission_id}/trame/import",
        files={"file": ("gros.docx", gros_docx_valide, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"ai_mode": "false"},
    )
    assert response.status_code == 200
    assert "trop volumineux" in response.text.lower()


def test_le_plafond_par_defaut_reste_realiste() -> None:
    """Garde-fou sur la garde : un plafond descendu par erreur sous la taille
    d'un vrai template PowerPoint casserait le produit sans que rien ne le
    dise."""
    assert MAX_UPLOAD_BYTES >= 20 * 1024 * 1024
