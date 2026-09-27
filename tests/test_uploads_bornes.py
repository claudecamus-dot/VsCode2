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
import struct
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import uploads
from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview
from app.services import audio_transcribe
from app.uploads import (
    MAX_AUDIO_UPLOAD_BYTES,
    MAX_UPLOAD_BYTES,
    UploadTropVolumineux,
    verifier_zip_borne,
)


def setup_module() -> None:
    # Le pool de `engine` est PARTAGE par toute la suite : sans ce dispose,
    # le fichier de test precedent tient encore la base et l'unlink leve
    # WinError 32 sur Windows (vert en isolation, rouge en ordre de
    # collecte). Motif canonique de la suite, cf. test_deck_qualite.py.
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
    # 400 depuis l'harmonisation des codes des 2 uploads .pptx (2026-09-27).
    assert response.status_code == 400
    assert "compression anormal" in response.text or "décompressé dépasse" in response.text


@pytest.mark.parametrize("route", ["pptx-template", "pptx-exemple"])
@pytest.mark.parametrize(
    ("fichier", "octets", "attendu_dans_la_page"),
    [("notes.txt", b"hello", ".pptx est attendu"),
     ("faux.pptx", b"not a real pptx", "invalide ou corrompu")],
    ids=["mauvaise-extension", "octets-corrompus"],
)
def test_les_deux_uploads_pptx_refusent_en_400(
    client: TestClient, route: str, fichier: str, octets: bytes,
    attendu_dans_la_page: str,
) -> None:
    """Codes HTTP harmonisés (arbitrage utilisateur 2026-09-26) : les DEUX
    uploads .pptx répondent 400 sur un fichier refusé, comme les imports
    d'entretien et de trame — l'upload de template rendait 200 avec un message,
    donc « succès » pour tout appelant qui lit le code. Le message reste visible
    dans la page rendue : le corps de la 400 EST l'écran d'aperçu."""
    mission_id = _creer_mission_avec_entretien(client, f"Mission 400 {route} {fichier}")
    r = client.post(f"/missions/{mission_id}/{route}",
                    files={"file": (fichier, octets, "application/octet-stream")})
    assert r.status_code == 400
    assert attendu_dans_la_page in r.text
    # Le corps est bien l'écran d'aperçu, pas une page d'erreur générique ni du JSON.
    assert "<h1>Export PPT</h1>" in r.text and "Erreur interne" not in r.text


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


# --------------------------------------------------------------------------- #
# Garde 1 bis — les deux points d'entrée AUDIO qui matérialisent en RAM
# (finding audit-technique securite du 2026-09-09 : « deux routes audio non
# authentifiées font `await file.read()` sans plafond »). Ils ne passent PAS
# par le plafond des documents : un plafond dédié, `MAX_AUDIO_UPLOAD_BYTES`.
#
# Échec sur le code d'avant : les deux routes faisaient `await file.read()`
# nu, donc tout le corps était alloué puis remis à Whisper — jamais de 413.
# --------------------------------------------------------------------------- #
def _interdire_la_transcription(monkeypatch: pytest.MonkeyPatch) -> None:
    """La borne doit répondre AVANT tout décodage : si Whisper est atteint,
    c'est que les octets ont déjà été matérialisés puis passés au décodeur —
    exactement le défaut à fermer. Même façon de faire que
    `test_le_nombre_d_entrees_est_lu_sans_indexer_l_archive`."""

    def _interdit(*args, **kwargs):
        raise AssertionError(
            "la transcription a été atteinte : la borne mémoire n'a pas coupé avant"
        )

    monkeypatch.setattr(audio_transcribe, "transcribe_audio", _interdit)


def test_segment_audio_trop_gros_refuse_avant_de_tout_charger_en_ram(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/audio/transcribe-segment` : route sans état et non authentifiée, la
    plus exposée des deux. Plafond abaissé à 4 Ko le temps du test (même
    procédé que pour les documents) — faire transiter 100 Mo dans la suite
    coûterait sans rien prouver de plus."""
    monkeypatch.setattr("app.uploads.MAX_AUDIO_UPLOAD_BYTES", 4096)
    _interdire_la_transcription(monkeypatch)

    response = client.post(
        "/audio/transcribe-segment",
        files={"file": ("segment.webm", b"\0" * 20000, "audio/webm")},
    )

    assert response.status_code == 413
    # 413 et pas 5xx : le JS de record.html ne relance automatiquement que les
    # `status >= 500`. Un refus de taille est définitif pour ces octets.
    assert "trop volumineux" in response.json()["error"].lower()
    # Contrat d'erreur de la route : `{"error": ...}`, jamais `{"detail": ...}`.
    assert "detail" not in response.json()


def test_notes_audio_trop_grosses_refusees_avant_de_tout_charger_en_ram(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/interviews/{id}/notes/transcribe` : la dictée de notes libres n'a
    AUCUNE rotation côté navigateur (capture.html enregistre d'un seul tenant),
    c'est donc le chemin où la durée — donc la taille — n'est bornée par
    rien."""
    monkeypatch.setattr("app.uploads.MAX_AUDIO_UPLOAD_BYTES", 4096)
    _interdire_la_transcription(monkeypatch)
    _creer_mission_avec_entretien(client, "Mission notes audio")
    session = SessionLocal()
    try:
        interview_id = session.scalars(
            select(Interview).order_by(Interview.id.desc())
        ).first().id
    finally:
        session.close()

    response = client.post(
        f"/interviews/{interview_id}/notes/transcribe",
        files={"file": ("note.webm", b"\0" * 20000, "audio/webm")},
    )

    assert response.status_code == 413
    assert "trop volumineux" in response.json()["error"].lower()


def test_import_fichier_trop_gros_rend_413_et_dit_pourquoi(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chemin DISQUE de `/audio/transcribe-file` — finding audit-technique
    sécurité du 2026-09-13.

    Les deux chemins MÉMOIRE ci-dessus rendaient déjà 413 avec le message qui
    dit pourquoi. Les deux chemins qui écrivent en streaming sur DISQUE, eux,
    ont reçu leur plafond le 2026-09-10 (`ecrire_audio_borne`) mais pas leur
    code de retour : `EcritureAudioTropVolumineuse` — sous-classe de
    `UploadTropVolumineux` — était attrapée par le `except Exception` qui
    enveloppe l'écriture et aplatie en 500 « Échec de l'import du fichier
    audio. ». Le plafond était donc annoncé sous un code qui signifie « panne
    serveur », et la raison perdue.

    Rouge sur le code d'avant : 500 au lieu de 413."""
    monkeypatch.setattr("app.uploads.MAX_AUDIO_UPLOAD_BYTES", 4096)
    mission_id = _creer_mission(client, "Mission import trop gros")

    response = client.post(
        "/audio/transcribe-file",
        files={"file": ("entretien.weba", b"\0" * 20000, "audio/webm")},
        data={"session_token": "sess-plafond", "mission_id": str(mission_id)},
    )

    assert response.status_code == 413, response.text
    assert "trop volumineux" in response.json()["error"].lower()
    assert "plafond" in response.json()["error"].lower()
    assert "detail" not in response.json()


def test_sauvegarde_de_secours_trop_grosse_rend_413_et_dit_pourquoi(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Jumeau du précédent sur `save_record_backup`. L'enjeu y est plus direct
    encore : l'onglet détient la SEULE copie de cet audio, et le JS relance
    automatiquement les `status >= 500` — un refus de taille rendu en 500 fait
    donc rejouer le même volume hors norme au lieu de dire ce qui ne va pas."""
    monkeypatch.setattr("app.uploads.MAX_AUDIO_UPLOAD_BYTES", 4096)
    mission_id = _creer_mission(client, "Mission backup trop gros")

    response = client.post(
        f"/missions/{mission_id}/interviews/record/backup",
        files={"file": ("secours.webm", b"\0" * 20000, "audio/webm")},
    )

    assert response.status_code == 413, response.text
    assert "trop volumineux" in response.json()["error"].lower()


def test_le_plafond_audio_par_defaut_laisse_passer_un_enregistrement_reel() -> None:
    """Garde-fou sur la garde. Mesuré le 2026-09-09 sur les enregistrements du
    poste : MediaRecorder produit 15,72 Ko/s (18,43 Mio pour 1200,0 s,
    27,64 Mio pour 1799,9 s — deux fichiers, même débit, reconfirmé par une
    revue indépendante). Le plafond doit donc rester très au-dessus de la
    plus grosse tranche de sauvegarde réelle mesurée (20 min ≈ 18,43 Mio),
    sans quoi une dictée un peu longue serait refusée.

    Contre l'artefact mesuré, pas contre `MAX_UPLOAD_BYTES` (revue
    adversariale du 2026-09-09, F10) : les deux plafonds viennent chacun
    de l'environnement, monter `MAX_UPLOAD_MB` — geste opérationnel que le
    module invite lui-même à faire — rougirait ce test sans rien changer à
    la validité du plafond audio."""
    plus_gros_artefact_mesure = 27.64 * 1024 * 1024  # 1799,9 s d'enregistrement
    assert MAX_AUDIO_UPLOAD_BYTES >= plus_gros_artefact_mesure * 3


def test_les_deux_plafonds_par_defaut_restent_distincts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'invariant que le module énonce — l'audio est plafonné PLUS HAUT que
    les documents — porté sur les DÉFAUTS et non sur les valeurs courantes
    (second passage de revue, m9) : l'assertion d'origine
    (`MAX_AUDIO_UPLOAD_BYTES > MAX_UPLOAD_BYTES`) couplait deux réglages
    indépendants, celle-ci ne couple rien mais garde l'inversion sous
    surveillance."""
    monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
    monkeypatch.delenv("MAX_AUDIO_UPLOAD_MB", raising=False)
    assert uploads._mo_env("MAX_AUDIO_UPLOAD_MB", 100) > uploads._mo_env("MAX_UPLOAD_MB", 40)


# --------------------------------------------------------------------------- #
# Le compte d'entrées ne protège que s'il ne peut pas MENTIR — les deux formes
# de mensonge trouvées par les revues adversariales du 2026-09-09.
# --------------------------------------------------------------------------- #
def _mentir_sur_le_compte(archive: bytes, compte: int = 1) -> bytes:
    """Falsifie le seul compte déclaré de la fin d'archive 32 bits, en
    laissant `size_cd` intact — l'archive reste parfaitement lisible."""
    data = bytearray(archive)
    position = data.rfind(b"PK\x05\x06")
    struct.pack_into("<H", data, position + 10, compte)
    return bytes(data)


def _fantome_zip64(archive: bytes) -> bytes:
    """Archive à fin d'archive DOUBLE : un locator + enregistrement zip64
    VALIDES portant les vraies (grosses) valeurs, suivis d'une fin d'archive
    32 bits qui MENT sans afficher de sentinelle. `zipfile._EndRecData64` suit
    le locator dès qu'il est présent — sans regarder les sentinelles — donc
    `zipfile` lit le vrai répertoire central pendant qu'une garde naïve, elle,
    ne lirait que le mensonge 32 bits."""
    position = archive.rfind(b"PK\x05\x06")
    taille_cd, offset_cd, compte = struct.unpack_from("<IIH", archive, position + 12)
    prefixe = archive[:position]
    eocd64 = struct.pack(
        "<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, compte, compte, taille_cd, offset_cd,
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, len(prefixe), 1)
    eocd32_menteur = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, 46, offset_cd, 0)
    return prefixe + eocd64 + locator + eocd32_menteur


def test_le_compte_declare_falsifie_ne_fait_pas_passer_l_archive() -> None:
    """Première forme (revue du 2026-09-09, F1) : 4 octets réécrits dans la
    fin d'archive font passer 20 000 entrées pour 1. `size_cd` reste intact —
    c'est LUI que `zipfile._RealGetContents` consomme, et c'est donc lui que
    la garde doit lire.

    Échec sur le code d'avant : refus prononcé sur le seul compte déclaré,
    donc archive ACCEPTÉE puis indexée en entier (mesuré : 0,78 s)."""
    menteuse = _mentir_sur_le_compte(_zip_innombrable())
    # L'archive falsifiée reste lisible : c'est ce qui rend l'attaque utile.
    with zipfile.ZipFile(io.BytesIO(menteuse)) as archive:
        assert len(archive.infolist()) == 20_000
    with pytest.raises(UploadTropVolumineux):
        verifier_zip_borne(menteuse)


def test_le_fantome_zip64_ne_fait_pas_passer_l_archive() -> None:
    """Seconde forme (second passage de revue, B4) : la garde ne consultait le
    zip64 que sur SENTINELLE (`0xFFFF`), alors que `zipfile` le suit dès qu'un
    locator valide précède la fin d'archive. Une archive portant un vrai zip64
    + une fin 32 bits menteuse SANS sentinelle rouvrait donc entièrement le
    contournement que la garde venait de fermer.

    Échec sur le code d'avant ce correctif : `_bornes_repertoire_central`
    rendait `(1, 46)` sur cette archive, donc ACCEPTÉE, pendant que `zipfile`
    y indexait 20 000 entrées."""
    fantome = _fantome_zip64(_zip_innombrable())
    with zipfile.ZipFile(io.BytesIO(fantome)) as archive:
        assert len(archive.infolist()) == 20_000, "la forgerie doit rester lisible"
    with pytest.raises(UploadTropVolumineux):
        verifier_zip_borne(fantome)


def test_une_vraie_archive_zip64_est_bornee_comme_les_autres() -> None:
    """Contre-épreuve sur du zip64 LÉGITIME (au-delà des 65 535 entrées que
    le format 32 bits peut compter) : la garde doit le borner par le même
    plafond, sans dépendre d'une falsification."""
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        for i in range(70_000):
            archive.writestr(str(i), b"")
    with pytest.raises(UploadTropVolumineux):
        verifier_zip_borne(tampon.getvalue())


# --------------------------------------------------------------------------- #
# Surcharge d'environnement du plafond d'entrées (F9) — jamais exercée jusqu'ici
# (second passage de revue, m10) : remplacer le corps de `_entier_env` par
# `return defaut` ne rougissait rien.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [("1200", 1200), ("pas-un-entier", 4000), ("0", 4000), ("-5", 4000), (None, 4000)],
    ids=["valeur-valide", "illisible", "zero", "negatif", "absente"],
)
def test_le_plafond_d_entrees_se_surcharge_sans_pouvoir_etre_desactive(
    monkeypatch: pytest.MonkeyPatch, valeur: str | None, attendu: int,
) -> None:
    """Une valeur illisible ou absurde ne doit pas DÉSACTIVER la garde : elle
    retombe sur le défaut, jamais sur 0 (qui refuserait tout) ni sur
    « pas de plafond »."""
    if valeur is None:
        monkeypatch.delenv("MAX_ZIP_ENTREES", raising=False)
    else:
        monkeypatch.setenv("MAX_ZIP_ENTREES", valeur)
    assert uploads._entier_env("MAX_ZIP_ENTREES", 4000) == attendu
