"""Onglet « Backup » d'une mission (2026-07-29) : lister, télécharger, supprimer
les enregistrements audio conservés sur le poste.

Deux populations doivent apparaître — les fichiers RATTACHÉS à un entretien et
les ORPHELINS (même mission, plus aucun entretien qui les référence), ces
derniers étant justement ce qui s'accumule et qu'on veut nettoyer.

Sûreté de la suppression, deux gardes distinctes et toutes deux testées :
le nom de fichier ne doit pas permettre de sortir de `data/recordings/`, et le
fichier doit appartenir à la mission de l'URL (sans quoi l'id de mission ne
serait qu'un décor).

`RECORDINGS_DIR` pointe sur le répertoire temporaire en test (`conftest.py`
force `APP_DB_PATH`, et `app.db` en dérive `DATA_DIR`) : aucun enregistrement
réel n'est touché ici.
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview, Mission
from app.services import mission_backups


def setup_module() -> None:
    # `engine.dispose()` AVANT l'unlink : le pool du fichier de tests précédent
    # garde sinon un handle ouvert et Windows refuse la suppression
    # (cf. mémoire feedback-pytest-db-unlink-needs-engine-dispose).
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


def _ecrire(nom: str, taille: int = 32) -> str:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    (RECORDINGS_DIR / nom).write_bytes(b"x" * taille)
    return nom


def _mission_avec_entretien(nom_mission: str, segments: list[str]):
    """Mission + un entretien libre référençant `segments` (fichiers écrits sur
    disque au préalable). Retourne (mission_id, interview_id)."""
    with SessionLocal() as db:
        mission = Mission(name=nom_mission)
        db.add(mission)
        db.flush()
        interview = Interview(
            mission_id=mission.id,
            interviewee_name="Testeur",
            mode="libre",
            audio_segments=[{"filename": f, "position": i} for i, f in enumerate(segments)],
            audio_backup_path=segments[-1] if segments else None,
        )
        db.add(interview)
        db.commit()
        return mission.id, interview.id


# --------------------------------------------------------------------------- #
# Inventaire
# --------------------------------------------------------------------------- #
def test_appartenance_compare_le_prefixe_avec_son_separateur():
    """Sans le « _ », la mission 1 revendiquerait les fichiers de la mission 13
    — et pourrait donc les supprimer."""
    assert mission_backups.appartient_a_mission("13_1785328415_abc.webm", 13)
    assert not mission_backups.appartient_a_mission("13_1785328415_abc.webm", 1)
    assert not mission_backups.appartient_a_mission("7_1785328415_abc.webm", 13)


def test_lister_distingue_rattaches_et_orphelins():
    mid, _ivid = _mission_avec_entretien(
        "Backup inventaire",
        [_ecrire("_bkp_a.webm"), _ecrire("_bkp_b.webm")],
    )
    # Fichier de CETTE mission que plus aucun entretien ne référence.
    orphelin = _ecrire(f"{mid}_9999_orphelin.webm", taille=64)
    # Fichier d'une AUTRE mission : ne doit pas apparaître ici.
    _ecrire("999999_9999_ailleurs.webm")

    with SessionLocal() as db:
        mission = db.get(Mission, mid)
        inventaire = mission_backups.lister_backups(mission, RECORDINGS_DIR)

    rattaches = [b["filename"] for b in inventaire["rattaches"]]
    orphelins = [b["filename"] for b in inventaire["orphelins"]]
    assert rattaches == ["_bkp_a.webm", "_bkp_b.webm"]
    assert orphelins == [orphelin]
    assert "999999_9999_ailleurs.webm" not in orphelins
    assert inventaire["taille_totale"] == 32 + 32 + 64


def test_lister_signale_un_fichier_reference_mais_disparu():
    """La référence en base survit à la disparition du fichier (suppression
    manuelle sur le disque) : l'écran doit le dire plutôt que d'offrir un
    téléchargement mort."""
    mid, _ivid = _mission_avec_entretien("Backup fantome", ["_bkp_absent.webm"])
    with SessionLocal() as db:
        mission = db.get(Mission, mid)
        inventaire = mission_backups.lister_backups(mission, RECORDINGS_DIR)
    assert inventaire["rattaches"][0]["existe"] is False


def test_backup_path_seul_est_liste_pour_un_entretien_d_avant_la_segmentation():
    """Entretien antérieur à `audio_segments` (2026-07-20) : sa sauvegarde n'est
    portée que par `audio_backup_path` — sans ce cas, elle serait invisible ET
    comptée comme orpheline."""
    fichier = _ecrire("_bkp_ancien.webm")
    with SessionLocal() as db:
        mission = Mission(name="Backup ancien")
        db.add(mission)
        db.flush()
        db.add(
            Interview(
                mission_id=mission.id,
                interviewee_name="Ancien",
                mode="libre",
                audio_segments=[],
                audio_backup_path=fichier,
            )
        )
        db.commit()
        mid = mission.id

    with SessionLocal() as db:
        inventaire = mission_backups.lister_backups(db.get(Mission, mid), RECORDINGS_DIR)
    assert [b["filename"] for b in inventaire["rattaches"]] == [fichier]
    assert inventaire["orphelins"] == []


def test_lister_ordonne_du_plus_ancien_au_plus_recent():
    """Ordre chronologique demandé (2026-07-29) : l'ordre naturel de lecture des
    enregistrements, et celui du nettoyage (les plus vieux d'abord). Sans tri,
    les rattachés sortaient dans l'ordre des entretiens et les orphelins dans
    l'ordre alphabétique du nom de fichier — ni l'un ni l'autre chronologique."""
    vieux, recent = _ecrire("_chrono_vieux.webm"), _ecrire("_chrono_recent.webm")
    # Le nom alphabétiquement premier est le PLUS RÉCENT : un tri par nom (l'ordre
    # d'avant) rendrait donc l'inverse de ce qui est attendu ici.
    os.utime(RECORDINGS_DIR / vieux, (1_700_000_000, 1_700_000_000))
    os.utime(RECORDINGS_DIR / recent, (1_800_000_000, 1_800_000_000))
    mid, _ = _mission_avec_entretien("Backup chrono", [])
    orph_recent = _ecrire(f"{mid}_a_recent.webm")
    orph_vieux = _ecrire(f"{mid}_z_vieux.webm")
    os.utime(RECORDINGS_DIR / orph_recent, (1_800_000_000, 1_800_000_000))
    os.utime(RECORDINGS_DIR / orph_vieux, (1_700_000_000, 1_700_000_000))

    with SessionLocal() as db:
        mission = db.get(Mission, mid)
        # Entretien dont les deux tranches sont écrites dans l'ordre inverse du
        # temps : c'est bien le mtime qui doit trancher, pas la position.
        interview = Interview(
            mission_id=mid,
            interviewee_name="Chrono",
            mode="libre",
            audio_segments=[
                {"filename": recent, "position": 0},
                {"filename": vieux, "position": 1},
            ],
            audio_backup_path=vieux,
        )
        db.add(interview)
        db.commit()
        db.refresh(mission)
        inventaire = mission_backups.lister_backups(mission, RECORDINGS_DIR)

    assert [b["filename"] for b in inventaire["rattaches"]] == [vieux, recent]
    assert [b["filename"] for b in inventaire["orphelins"]] == [orph_vieux, orph_recent]


def test_lister_expose_une_date_lisible_et_laisse_un_fichier_disparu_en_fin():
    """La date/heure est affichée (demande 2026-07-29) ; un fichier disparu n'en
    a pas — il ne doit ni afficher 1970 ni remonter en tête de liste, où son
    `mtime` à 0 le placerait."""
    present = _ecrire("_date_present.webm")
    os.utime(RECORDINGS_DIR / present, (1_700_000_000, 1_700_000_000))
    mid, _ = _mission_avec_entretien("Backup date", [])
    with SessionLocal() as db:
        db.add(
            Interview(
                mission_id=mid,
                interviewee_name="Date",
                mode="libre",
                audio_segments=[
                    {"filename": "_date_disparu.webm", "position": 0},
                    {"filename": present, "position": 1},
                ],
                audio_backup_path=present,
            )
        )
        db.commit()
    with SessionLocal() as db:
        inventaire = mission_backups.lister_backups(db.get(Mission, mid), RECORDINGS_DIR)

    rattaches = inventaire["rattaches"]
    assert [b["filename"] for b in rattaches] == [present, "_date_disparu.webm"]
    attendu = datetime.fromtimestamp(1_700_000_000).strftime("%d/%m/%Y à %H:%M")
    assert rattaches[0]["horodatage"] == attendu
    assert rattaches[1]["horodatage"] == ""


# --------------------------------------------------------------------------- #
# Suppression
# --------------------------------------------------------------------------- #
def test_suppression_efface_le_fichier_et_sa_reference(client: TestClient):
    a, b = _ecrire("_del_a.webm"), _ecrire("_del_b.webm")
    mid, ivid = _mission_avec_entretien("Backup suppression", [a, b])

    resp = client.post(
        f"/missions/{mid}/interviews/record/backup/{a}/delete", follow_redirects=False
    )
    assert resp.status_code == 303
    assert not (RECORDINGS_DIR / a).exists()
    assert (RECORDINGS_DIR / b).exists()

    with SessionLocal() as db:
        interview = db.get(Interview, ivid)
        assert [s["filename"] for s in interview.audio_segments] == [b]


def test_suppression_de_la_derniere_tranche_recale_audio_backup_path(client: TestClient):
    """`audio_backup_path` désigne la DERNIÈRE tranche : supprimer celle-ci doit
    le faire retomber sur la tranche restante, pas laisser un chemin mort (le
    lecteur de `libre_detail.html` s'en sert encore)."""
    a, b = _ecrire("_recale_a.webm"), _ecrire("_recale_b.webm")
    mid, ivid = _mission_avec_entretien("Backup recalage", [a, b])

    client.post(f"/missions/{mid}/interviews/record/backup/{b}/delete", follow_redirects=False)

    with SessionLocal() as db:
        assert db.get(Interview, ivid).audio_backup_path == a


def test_suppression_du_dernier_fichier_vide_audio_backup_path(client: TestClient):
    seul = _ecrire("_dernier.webm")
    mid, ivid = _mission_avec_entretien("Backup dernier", [seul])

    client.post(f"/missions/{mid}/interviews/record/backup/{seul}/delete", follow_redirects=False)

    with SessionLocal() as db:
        interview = db.get(Interview, ivid)
        assert interview.audio_backup_path is None
        assert interview.audio_segments == []


def test_suppression_refuse_un_fichier_d_une_autre_mission(client: TestClient):
    """Garde d'appartenance : sans elle, l'id de mission de l'URL ne serait
    qu'un décor et n'importe quelle mission effacerait l'audio des autres."""
    mid_a, _ = _mission_avec_entretien("Backup mission A", [_ecrire("_croise_a.webm")])
    mid_b, _ = _mission_avec_entretien("Backup mission B", [])
    cible = _ecrire(f"{mid_a}_1111_prive.webm")

    resp = client.post(
        f"/missions/{mid_b}/interviews/record/backup/{cible}/delete", follow_redirects=False
    )
    assert resp.status_code == 404
    assert (RECORDINGS_DIR / cible).exists()


@pytest.mark.parametrize(
    "cible, attendu",
    [
        # Séparateur POSIX : le routage lui-même n'apparie plus la route (le
        # nom de fichier ne peut pas contenir de « / »).
        ("..%2F..%2Fapp.db", 404),
        # Séparateur Windows : le segment arrive INTACT jusqu'au handler — c'est
        # la garde du code qui doit refuser, pas le routage.
        ("..%5C..%5Capp.db", 400),
        # « .. » seul est normalisé par le client HTTP avant même l'envoi : la
        # route n'est jamais atteinte. Refusé aussi, par un autre étage.
        ("..", 404),
    ],
)
def test_suppression_refuse_une_traversee_de_chemin(client: TestClient, cible, attendu):
    mid, _ = _mission_avec_entretien(f"Backup traversee {cible}", [])
    resp = client.post(
        f"/missions/{mid}/interviews/record/backup/{cible}/delete",
        follow_redirects=False,
    )
    assert resp.status_code == attendu
    assert DB_PATH.exists()  # la base n'a évidemment pas bougé


def test_suppression_d_un_orphelin(client: TestClient):
    mid, _ = _mission_avec_entretien("Backup orphelin", [])
    orphelin = _ecrire(f"{mid}_2222_a_nettoyer.webm")

    resp = client.post(
        f"/missions/{mid}/interviews/record/backup/{orphelin}/delete", follow_redirects=False
    )
    assert resp.status_code == 303
    assert not (RECORDINGS_DIR / orphelin).exists()


def test_suppression_d_une_tranche_du_milieu_renumerote_les_rangs(client: TestClient):
    """Régression (revue adversariale 2026-07-29) : `position` est un RANG
    affiché (« Tranche 2/3 ») — sans renumérotation, supprimer la tranche du
    milieu laissait les rangs 0 et 2 sur un entretien à 2 tranches, soit
    « Tranche 3/2 » à l'écran."""
    a, b, c = _ecrire("_renum_a.webm"), _ecrire("_renum_b.webm"), _ecrire("_renum_c.webm")
    mid, ivid = _mission_avec_entretien("Backup renumerotation", [a, b, c])

    client.post(f"/missions/{mid}/interviews/record/backup/{b}/delete", follow_redirects=False)

    with SessionLocal() as db:
        segments = db.get(Interview, ivid).audio_segments
    assert [(s["filename"], s["position"]) for s in segments] == [(a, 0), (c, 1)]


def test_suppression_refuse_un_fichier_reference_par_une_autre_mission(client: TestClient):
    """Régression (revue adversariale 2026-07-29) : un fichier peut porter le
    préfixe de la mission A tout en étant référencé par un entretien de la
    mission B (entretien réattaché depuis une mission brouillon dont l'id a été
    réutilisé). Il apparaît alors « orphelin » chez A — le supprimer depuis A
    laisserait B avec des références pendantes, sans aucun moyen de l'avoir
    empêché."""
    mid_a, _ = _mission_avec_entretien("Backup id reutilise A", [])
    fichier = _ecrire(f"{mid_a}_4444_reattache.webm")
    mid_b, _ivid_b = _mission_avec_entretien("Backup id reutilise B", [fichier])

    resp = client.post(
        f"/missions/{mid_a}/interviews/record/backup/{fichier}/delete", follow_redirects=False
    )
    assert resp.status_code == 409
    assert (RECORDINGS_DIR / fichier).exists()


# --------------------------------------------------------------------------- #
# Téléchargement / écoute
# --------------------------------------------------------------------------- #
def test_telechargement_refuse_un_fichier_d_une_autre_mission(client: TestClient):
    """Même garde d'appartenance que la suppression (revue adversariale
    2026-07-29) : sans elle, l'id de mission de l'URL n'était qu'un décor et
    n'importe quel id servait n'importe quel enregistrement."""
    mid_a, _ = _mission_avec_entretien("Backup lecture A", [])
    mid_b, _ = _mission_avec_entretien("Backup lecture B", [])
    prive = _ecrire(f"{mid_a}_5555_prive.webm")

    assert client.get(
        f"/missions/{mid_b}/interviews/record/backup/{prive}"
    ).status_code == 404
    assert client.get(
        f"/missions/{mid_a}/interviews/record/backup/{prive}"
    ).status_code == 200


# --------------------------------------------------------------------------- #
# Rendu de l'onglet
# --------------------------------------------------------------------------- #
def test_onglet_backup_rendu_sur_la_fiche_mission(client: TestClient):
    a = _ecrire("_vue_a.webm")
    mid, _ = _mission_avec_entretien("Backup vue", [a])
    orphelin = _ecrire(f"{mid}_3333_orphelin.webm")

    html = client.get(f"/missions/{mid}").text

    assert 'data-tab="backup"' in html
    assert 'data-panel="backup"' in html
    # Les DEUX populations sont proposées au téléchargement et à la suppression.
    for fichier in (a, orphelin):
        assert f"/missions/{mid}/interviews/record/backup/{fichier}" in html
        assert f"/missions/{mid}/interviews/record/backup/{fichier}/delete" in html
    assert "Non rattachés" in html
    # Garde-fou d'ergonomie : la suppression est définitive, elle se confirme.
    assert "confirm(" in html
    # Date et heure affichées (demande 2026-07-29) : la colonne existe dans les
    # DEUX tableaux, et l'horodatage réel du fichier y est écrit. Le comptage se
    # fait DANS le panneau Backup — la fiche mission porte d'autres tableaux
    # datés, qui fausseraient un comptage sur la page entière.
    panneau = html.split('data-panel="backup"', 1)[1]
    assert panneau.count("<th>Date</th>") == 2
    with SessionLocal() as db:
        inventaire = mission_backups.lister_backups(db.get(Mission, mid), RECORDINGS_DIR)
    for entree in inventaire["rattaches"] + inventaire["orphelins"]:
        assert entree["horodatage"] in html


def test_orphelin_recent_est_signale_comme_enregistrement_possible_en_cours(client: TestClient):
    """Régression (revue adversariale 2026-07-29) : pendant le wizard, les
    tranches uploadées ne sont référencées qu'à la CONFIRMATION — un « orphelin »
    récent peut donc être un enregistrement en cours dans un autre onglet, que
    l'écran invitait pourtant à « nettoyer en priorité »."""
    mid, _ = _mission_avec_entretien("Backup orphelin recent", [])
    frais = _ecrire(f"{mid}_6666_frais.webm")           # mtime = maintenant
    vieux = _ecrire(f"{mid}_7777_vieux.webm")
    os.utime(RECORDINGS_DIR / vieux, (1_700_000_000, 1_700_000_000))

    with SessionLocal() as db:
        inventaire = mission_backups.lister_backups(db.get(Mission, mid), RECORDINGS_DIR)
    par_nom = {e["filename"]: e for e in inventaire["orphelins"]}
    assert par_nom[frais]["recent"] is True
    assert par_nom[vieux]["recent"] is False

    html = client.get(f"/missions/{mid}").text
    assert "peut appartenir à un enregistrement en cours" in html


def test_urls_de_l_onglet_encodent_le_nom_de_fichier(client: TestClient):
    """Régression (revue adversariale 2026-07-29) : les orphelins viennent d'un
    glob disque non filtré — un nom contenant « # » (copie manuelle) tronquait
    l'URL d'écoute/téléchargement, et le formulaire de suppression postait vers
    un autre chemin : précisément le fichier que l'onglet doit pouvoir nettoyer
    devenait insupprimable depuis l'UI."""
    mid, _ = _mission_avec_entretien("Backup encodage", [])
    farfelu = _ecrire(f"{mid}_note#2.webm")

    html = client.get(f"/missions/{mid}").text
    encode = farfelu.replace("#", "%23")
    assert f"/missions/{mid}/interviews/record/backup/{encode}" in html
    assert f"/missions/{mid}/interviews/record/backup/{encode}/delete" in html
    assert f"backup/{farfelu}" not in html  # jamais le « # » brut dans une URL

    # Et la chaîne complète tient : le POST de suppression sur l'URL encodée
    # atteint bien le fichier au nom farfelu.
    resp = client.post(
        f"/missions/{mid}/interviews/record/backup/{encode}/delete", follow_redirects=False
    )
    assert resp.status_code == 303
    assert not (RECORDINGS_DIR / farfelu).exists()


def test_tabs_js_reagit_aux_navigations_hash_seules():
    """Régression (revue adversariale 2026-07-29) : un Retour navigateur depuis
    `/missions/{id}#backup` (même document, pas de rechargement) laissait l'URL
    et l'onglet surligné diverger — `tabs.js` doit écouter `hashchange`."""
    source = (
        __import__("pathlib").Path(__file__).parent.parent
        / "app" / "static" / "tabs.js"
    ).read_text(encoding="utf-8")
    assert 'addEventListener("hashchange"' in source


def test_onglets_entretiens_et_synthese_restent_accessibles(client: TestClient):
    """La fiche mission passe en onglets : ne pas perdre au passage les deux
    sections qui existaient avant."""
    mid, _ = _mission_avec_entretien("Backup navigation", [])
    html = client.get(f"/missions/{mid}").text
    assert 'data-panel="entretiens"' in html
    assert 'data-panel="synthese"' in html
    assert "Synthèse transverse" in html
