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
from datetime import UTC, datetime

import pytest
from conftest import vider_recordings_de_test
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
    # Et le répertoire d'enregistrements avec (2026-09-01) : ce module est le
    # seul à affirmer un inventaire EXACT de `RECORDINGS_DIR`, or depuis que
    # l'audio ne se supprime plus tout seul, les imports des modules précédents
    # y restent — nommés `1_import_…`, donc attribués à la mission n° 1 que
    # `init_db()` sur base neuve recrée ici. Sans ce nettoyage, l'échec dépend
    # de l'ordre de collecte et disparaît quand on isole le fichier.
    vider_recordings_de_test()


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


def _antidater_mission(mission_id: int, epoch: float) -> None:
    """Recule la creation de la mission avant `epoch`.

    Necessaire depuis la garde anti-reutilisation d'id (2026-09-01, constat
    C3) : un fichier ANTERIEUR a sa mission appartient a celle qui portait ce
    numero avant elle, donc il n'est plus liste comme son orphelin. Les tests
    qui antidatent un fichier pour eprouver l'ordre ou la fraicheur creaient un
    etat impossible en production (l'audio d'une mission est forcement ecrit
    apres sa creation) ; on retablit la chronologie reelle au lieu
    d'affaiblir la garde.
    """
    from datetime import datetime

    with SessionLocal() as db:
        mission = db.get(Mission, mission_id)
        mission.created_at = datetime.fromtimestamp(epoch - 3600, tz=UTC).replace(
            tzinfo=None
        )
        db.commit()


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

    _antidater_mission(mid, 1_700_000_000)
    with SessionLocal() as db:
        inventaire = mission_backups.lister_backups(db.get(Mission, mid), RECORDINGS_DIR)

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
    _antidater_mission(mid, 1_700_000_000)

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


# --------------------------------------------------------------------------- #
# Cycle de vie de l'audio — correctifs de la revue du 2026-09-01
#
# Trois constats liés, qu'on ne peut pas tester séparément sans mentir sur ce
# qui les relie : depuis que l'audio ne se supprime QUE par une action de
# l'utilisateur (commit c79e8b5), un fichier qu'aucun écran n'atteint est un
# fichier INDESTRUCTIBLE. La règle produit se retournait contre elle-même.
#
#   C1 — la suppression d'une mission fabriquait ces fichiers (mesuré :
#        75,8 Mo, 24 % du répertoire, sur l'installation réelle) ;
#   C2 — les imports d'avant la convention de nommage n'ont aucun préfixe,
#        donc n'ont JAMAIS été atteignables ;
#   C3 — `Mission.id` est un rowid sans AUTOINCREMENT : l'id d'une mission
#        supprimée est rendu à la suivante, qui héritait de son audio.
# --------------------------------------------------------------------------- #
def _globaux(db) -> list[dict]:
    return mission_backups.lister_orphelins_globaux(RECORDINGS_DIR, db)["orphelins"]


def _vieillir(nom: str, secondes: int) -> str:
    """Recule le `mtime` d'un fichier. La garde anti-réutilisation d'id compare
    l'âge du fichier à la création de la mission : sans ce recul, tout est
    écrit dans la même seconde et le test ne prouverait rien."""
    chemin = RECORDINGS_DIR / nom
    ancien = chemin.stat().st_mtime
    os.utime(chemin, (ancien - secondes, ancien - secondes))
    return nom


def test_un_fichier_dont_la_mission_est_supprimee_reste_atteignable_globalement():
    """C1 — le cœur du correctif. Avant, ce fichier n'était listé nulle part :
    `lister_backups` exige un objet `Mission` et globbe `{mission.id}_*`."""
    mid, _ = _mission_avec_entretien("Mission a supprimer", [_ecrire("_c1_rattache.webm")])
    orphelin = _ecrire(f"{mid}_9999_c1.webm")

    with SessionLocal() as db:
        db.delete(db.get(Mission, mid))
        db.commit()
        noms = [e["filename"] for e in _globaux(db)]

    assert orphelin in noms, "l'audio d'une mission supprimee n'est atteignable par aucun ecran"
    assert "_c1_rattache.webm" in noms, "le fichier reference par l'entretien supprime aussi"


def test_l_import_sans_prefixe_de_mission_est_atteignable_globalement():
    """C2 — convention d'avant le 2026-09-01 (`import_<ts>_<hex>`). Aucun
    préfixe, donc aucune mission ne peut le revendiquer : sans l'inventaire
    global il n'existe pour personne."""
    legacy = _ecrire("import_1785351999_f938b8c0.webm")

    with SessionLocal() as db:
        entrees = _globaux(db)

    noms = [e["filename"] for e in entrees]
    raisons = {e["filename"]: e["raison"] for e in entrees}
    assert legacy in noms
    assert "sans préfixe" in raisons[legacy], "l'ecran doit dire POURQUOI le fichier est la"


def test_l_inventaire_global_ignore_ce_qu_une_mission_montre_deja():
    """La contrepartie : un fichier visible dans un onglet Backup n'a rien à
    faire ici. Sans cette borne, l'inventaire global doublonnerait tous les
    écrans et inviterait à supprimer de l'audio rattaché à un entretien vivant."""
    mid, _ = _mission_avec_entretien("Mission vivante", [_ecrire("_c1_vivant.webm")])
    orphelin_de_mission = _ecrire(f"{mid}_9999_visible.webm")

    with SessionLocal() as db:
        noms = [e["filename"] for e in _globaux(db)]

    assert "_c1_vivant.webm" not in noms, "fichier rattache a un entretien : jamais ici"
    assert orphelin_de_mission not in noms, "deja visible dans l'onglet Backup de sa mission"


def test_supprimer_une_mission_emporte_son_audio(client):
    """C1 en amont : ne plus FABRIQUER de fichiers inatteignables. Supprimer la
    mission est bien une action de l'utilisateur sur le site — la plus
    explicite qui soit."""
    mid, _ = _mission_avec_entretien("Mission jetable", [_ecrire("_c1_cascade.webm")])
    orphelin = _ecrire(f"{mid}_9999_cascade.webm")
    temoin = _ecrire("999999_9999_autre_mission.webm")

    reponse = client.post(f"/missions/{mid}/delete", follow_redirects=False)

    assert reponse.status_code == 303
    assert not (RECORDINGS_DIR / "_c1_cascade.webm").exists(), "fichier reference non supprime"
    assert not (RECORDINGS_DIR / orphelin).exists(), "orphelin de la mission non supprime"
    assert (RECORDINGS_DIR / temoin).is_file(), "la cascade a deborde sur une autre mission"


def test_le_nettoyage_groupe_des_brouillons_n_emporte_PAS_l_audio(client):
    """Chemin frère de la cascade, et l'exception est VOULUE : un brouillon
    vide est exactement l'état d'un enregistrement en cours (l'entretien
    n'existe qu'à la confirmation). Une passe groupée détruirait l'audio d'une
    séance qui tourne dans un autre onglet. Il retombe dans l'inventaire
    global, visible et supprimable — personne ne décide à sa place."""
    with SessionLocal() as db:
        brouillon = Mission(name="Brouillon", is_draft=True)
        db.add(brouillon)
        db.commit()
        mid = brouillon.id
    en_cours = _ecrire(f"{mid}_9999_enregistrement_en_cours.webm")

    reponse = client.post("/missions/brouillons/nettoyer", follow_redirects=False)

    assert reponse.status_code == 303
    assert (RECORDINGS_DIR / en_cours).is_file(), (
        "le nettoyage groupe a detruit l'audio d'un enregistrement peut-etre en cours"
    )
    with SessionLocal() as db:
        noms = [e["filename"] for e in _globaux(db)]
    assert en_cours in noms, "il doit rester atteignable par l'inventaire global"


def test_une_mission_qui_reutilise_un_id_n_herite_pas_de_l_audio_precedent():
    """C3 — `Mission.id` est un rowid sans AUTOINCREMENT. La mission suivante
    récupérait le préfixe de la précédente : elle listait, servait et pouvait
    SUPPRIMER l'audio d'entretien d'un autre client."""
    with SessionLocal() as db:
        ancienne = Mission(name="Ancienne")
        db.add(ancienne)
        db.commit()
        ancien_id = ancienne.id
    herite = _vieillir(_ecrire(f"{ancien_id}_9999_audio_du_client_precedent.webm"), 86400)

    with SessionLocal() as db:
        db.delete(db.get(Mission, ancien_id))
        db.commit()
        nouvelle = Mission(name="Nouvelle")
        db.add(nouvelle)
        db.commit()
        # L'id doit effectivement être réutilisé, sinon le test ne prouve rien.
        if nouvelle.id != ancien_id:
            pytest.skip("SQLite n'a pas reutilise l'id : le scenario vise n'est pas atteint")
        orphelins = [e["filename"] for e in mission_backups.lister_backups(nouvelle, RECORDINGS_DIR)["orphelins"]]
        globaux = [e["filename"] for e in _globaux(db)]
        autorise = mission_backups.appartient_a_mission(
            herite, nouvelle.id, nouvelle, RECORDINGS_DIR
        )

    assert herite not in orphelins, "la nouvelle mission liste l'audio d'entretien de la precedente"
    assert not autorise, "la nouvelle mission peut SUPPRIMER l'audio de la precedente"
    assert herite in globaux, (
        "cache a sa mission mais absent de l'inventaire global = fichier indestructible"
    )


def test_une_reference_explicite_prime_sur_la_chronologie():
    """Contrepartie de C3 : un entretien réattaché depuis une mission brouillon
    garde un fichier plus VIEUX que sa mission d'accueil. La garde
    chronologique ne doit pas le lui retirer — sinon le correctif de C3 casse
    le rattachement, qui est un chemin nominal."""
    vieux = _vieillir(_ecrire("_c3_reference_ancienne.webm"), 86400)
    mid, _ = _mission_avec_entretien("Mission d'accueil", [vieux])

    with SessionLocal() as db:
        mission = db.get(Mission, mid)
        rattaches = [
            e["filename"] for e in mission_backups.lister_backups(mission, RECORDINGS_DIR)["rattaches"]
        ]
        autorise = mission_backups.appartient_a_mission(vieux, mid, mission, RECORDINGS_DIR)
        globaux = [e["filename"] for e in _globaux(db)]

    assert vieux in rattaches
    assert autorise, "un fichier reference appartient a sa mission, quel que soit son age"
    assert vieux not in globaux, "il est visible dans l'onglet Backup : pas un orphelin global"


# --------------------------------------------------------------------------- #
# Routes de l'inventaire global
# --------------------------------------------------------------------------- #
def test_l_ecran_global_sert_et_supprime_un_orphelin(client):
    """Écouter AVANT de supprimer : ces fichiers ne sont rattachés à aucune
    mission, donc `get_record_backup` les refuse. Sans route d'écoute, l'écran
    demanderait de détruire des dizaines de Mo d'audio d'entretien à l'aveugle."""
    orphelin = _ecrire("import_1785000000_ecoute.webm", taille=128)

    ecoute = client.get(f"/missions/audio-orphelin/{orphelin}")
    assert ecoute.status_code == 200
    assert len(ecoute.content) == 128

    suppression = client.post(
        f"/missions/audio-orphelin/{orphelin}/delete", follow_redirects=False
    )
    assert suppression.status_code == 303
    assert not (RECORDINGS_DIR / orphelin).exists()


def test_l_ecran_global_refuse_un_fichier_qui_n_est_PAS_orphelin(client):
    """La garde qui compte. Sans elle, ces deux routes seraient un « sers ou
    supprime n'importe quel enregistrement par son nom », contournant toutes
    les gardes d'appartenance de l'onglet Backup : un écran de nettoyage ne
    doit pas être la porte dérobée des écrans qu'il complète."""
    _mission_avec_entretien("Mission protegee", [_ecrire("_c1_protege.webm")])

    assert client.get("/missions/audio-orphelin/_c1_protege.webm").status_code == 404
    assert (
        client.post(
            "/missions/audio-orphelin/_c1_protege.webm/delete", follow_redirects=False
        ).status_code
        == 404
    )
    assert (RECORDINGS_DIR / "_c1_protege.webm").is_file()


def test_l_ecran_global_refuse_une_traversee_de_chemin(client):
    assert client.get("/missions/audio-orphelin/..%2F..%2Fapp.db").status_code in (400, 404)
    assert client.post(
        "/missions/audio-orphelin/..%2F..%2Fapp.db/delete", follow_redirects=False
    ).status_code in (400, 404)


def test_l_ecran_global_est_atteignable_depuis_la_liste_des_missions(client):
    """Le lien est le SEUL point d'entrée : ces fichiers n'apparaissent dans
    l'onglet Backup d'aucune mission. Sans lui, l'écran existe mais personne ne
    le trouve — et l'audio reste indestructible, constat C1 non traité."""
    _ecrire("import_1785000001_lien.webm")

    page = client.get("/missions")

    assert page.status_code == 200
    assert "/missions/audio-orphelin" in page.text, (
        "aucun lien vers l'inventaire global depuis la liste des missions"
    )
    assert client.get("/missions/audio-orphelin").status_code == 200


# --------------------------------------------------------------------------- #
# La RAISON affichée à côté du bouton « Supprimer »
# --------------------------------------------------------------------------- #
def test_la_raison_distingue_mission_absente_et_numero_reattribue(tmp_path) -> None:
    """Devant un bouton de suppression, une raison fausse est pire que pas de
    raison : elle fait douter de l'écran entier.

    Défaut mesuré le 2026-09-01 sur l'installation réelle, en regardant la page
    servie : sur 12 orphelins, 4 annonçaient « mission n° N supprimée » alors
    que la mission N figurait dans la liste des missions, juste à côté. Le
    fichier était bien orphelin — c'est la garde anti-réutilisation d'id qui
    l'écarte, SQLite ayant réattribué le numéro d'une mission supprimée — mais
    la phrase, elle, était fausse. Les trois causes sont distinctes et doivent
    le rester.
    """
    from app.services import mission_backups

    class _Mission:
        id = 13
        created_at = None

    mission = _Mission()
    # `_epoch_creation` lit `created_at` ; on passe par le helper pour rester
    # sur la même conversion de fuseau que la production.
    mission.created_at = datetime.fromtimestamp(1_700_000_000, tz=UTC).replace(
        tzinfo=None
    )

    sans_prefixe = mission_backups._raison_orphelin("import", None, 1_700_000_000)
    assert "sans préfixe" in sans_prefixe
    assert "supprimée" not in sans_prefixe

    absente = mission_backups._raison_orphelin("8", None, 1_700_000_000)
    assert absente == "mission n° 8 supprimée"

    # Le cas qui mentait : la mission EXISTE, le fichier est plus ancien qu'elle.
    reattribue = mission_backups._raison_orphelin("13", mission, 1_600_000_000)
    assert "réattribué" in reattribue, reattribue
    assert "plus ancienne" in reattribue, reattribue
    # Et surtout : il ne dit PAS que la mission n° 13 a été supprimée.
    assert reattribue != "mission n° 13 supprimée"
    assert "mission n° 13 supprimée" not in reattribue


def test_l_ecran_global_nomme_la_bonne_cause_pour_un_id_reattribue(tmp_path) -> None:
    """Bout en bout : un fichier antérieur à SA mission est bien listé comme
    orphelin (il est inatteignable depuis l'onglet Backup de cette mission),
    mais avec la cause exacte."""
    from app.db import SessionLocal
    from app.models import Mission
    from app.services import mission_backups

    db = SessionLocal()
    try:
        mission = Mission(name="ZZ raison orphelin")
        db.add(mission)
        db.commit()
        mid = mission.id
        _antidater_mission(mid, 1_700_000_000)

        # Fichier au préfixe de la mission, mais PLUS ANCIEN qu'elle.
        fichier = tmp_path / f"{mid}_1600000000_aaaabbbb.webm"
        fichier.write_bytes(b"audio")
        os.utime(fichier, (1_600_000_000, 1_600_000_000))

        listing = mission_backups.lister_orphelins_globaux(tmp_path, db)
        entrees = {e["filename"]: e for e in listing["orphelins"]}
        assert fichier.name in entrees, (
            "un fichier écarté de sa mission par la garde de chronologie doit "
            "apparaître ici, sinon il n'est listé NULLE PART"
        )
        raison = entrees[fichier.name]["raison"]
        assert "réattribué" in raison, raison
        assert f"mission n° {mid} supprimée" not in raison, (
            f"la raison affirme que la mission n° {mid} est supprimée alors "
            "qu'elle existe : c'est le mensonge corrigé le 2026-09-01"
        )
    finally:
        db.query(Mission).filter(Mission.name == "ZZ raison orphelin").delete()
        db.commit()
        db.close()


def test_un_fichier_en_cours_de_transcription_n_est_pas_proposable_a_la_suppression(
    tmp_path,
) -> None:
    """Revue adversariale du 2026-09-01, constat A5.

    Le chemin réel, et il n'a rien d'exotique : on importe un fichier dans un
    brouillon, `nettoyer_brouillons` emporte la mission — `_draft_vide` est vrai
    pour un entretien libre encore en cours — et le fichier devient orphelin
    global AVEC son bouton « Supprimer », pendant que le job de transcription le
    lit bloc par bloc. Le jeu de références ne regardait que les entretiens.

    Un job terminé, lui, ne retient rien : son fichier redevient supprimable dès
    que plus aucun entretien ne le cite, sinon la règle produirait de nouveau
    des fichiers indestructibles — exactement ce que C1 corrigeait.
    """
    from app.db import SessionLocal
    from app.models import AudioFileJob
    from app.services import mission_backups

    en_cours = tmp_path / "77_import_1600000000_encours.webm"
    termine = tmp_path / "77_import_1600000000_termine.webm"
    for f in (en_cours, termine):
        f.write_bytes(b"audio")

    db = SessionLocal()
    try:
        db.add(AudioFileJob(
            session_token="a5-en-cours", filename=en_cours.name, status="running",
        ))
        db.add(AudioFileJob(
            session_token="a5-termine", filename=termine.name, status="done",
        ))
        db.commit()

        noms = {e["filename"] for e in
                mission_backups.lister_orphelins_globaux(tmp_path, db)["orphelins"]}

        assert en_cours.name not in noms, (
            "l'écran propose de supprimer un fichier que le transcripteur est "
            "en train de lire : la suppression casse le job en cours"
        )
        assert termine.name in noms, (
            "un job TERMINÉ ne doit plus retenir son fichier, sinon on refabrique "
            "des fichiers qu'aucun écran ne peut supprimer (constat C1)"
        )
    finally:
        db.query(AudioFileJob).filter(
            AudioFileJob.session_token.in_(["a5-en-cours", "a5-termine"])
        ).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_l_inventaire_global_ignore_ce_qui_n_est_pas_de_l_audio(tmp_path) -> None:
    """Constat B3 : un `.gitkeep` ou un `notes.txt` déposé dans `recordings/`
    était listé comme un enregistrement supprimable, et collait un badge
    permanent sur la liste des missions."""
    from app.db import SessionLocal
    from app.services import mission_backups

    (tmp_path / "99_1600000000_aaaa.webm").write_bytes(b"audio")
    (tmp_path / ".gitkeep").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("rien a voir", encoding="utf-8")
    # `isdigit()` est vrai pour « ² », que `int()` refuse (constat B2) : sans la
    # garde `isascii`, la ValueError remontait jusqu'à la liste des missions.
    (tmp_path / "\u00b2_1600000000_bbbb.webm").write_bytes(b"audio")

    db = SessionLocal()
    try:
        noms = {e["filename"] for e in
                mission_backups.lister_orphelins_globaux(tmp_path, db)["orphelins"]}
    finally:
        db.close()

    assert "99_1600000000_aaaa.webm" in noms
    assert ".gitkeep" not in noms and "notes.txt" not in noms, (
        f"un fichier non audio est proposé à la suppression : {noms}"
    )
    assert "\u00b2_1600000000_bbbb.webm" in noms, (
        "un préfixe non ASCII doit être traité comme « sans préfixe », pas lever "
        "une ValueError qui rend la liste des missions en 500"
    )
