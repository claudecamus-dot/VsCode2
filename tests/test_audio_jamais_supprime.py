"""L'audio ne se supprime QUE par une action de l'utilisateur sur le site
(règle projet du 2026-09-01).

Avant cette date, quatre chemins effaçaient de l'audio tout seuls : le succès
du job d'import, la purge à 7 jours, le refus de reprise, et deux chemins
d'erreur de l'import. Le plus coûteux était le premier — une fois la
transcription réussie, la SOURCE de l'entretien disparaissait, donc aucun rejeu
possible si l'on découvrait ensuite un défaut de transcription ou
d'extraction. C'est exactement le moment où l'on en a besoin, et c'est le
chemin que prennent les enregistrements Meet/Teams importés.

L'entretien enregistré au micro, lui, gardait son audio
(`Interview.audio_backup_path`, décrit dans le modèle comme un « filet de
sécurité en cas de souci de transcription/extraction ») : l'import était le
chemin frère privé du même filet.

Deux invariants sont figés ici :

1. **structurel** — aucun `unlink` d'audio ne subsiste hors de la route de
   suppression du site. C'est le test qui tient dans le temps : il échoue si
   quelqu'un réintroduit un helper de suppression, quel que soit son nom ;
2. **comportemental** — un fichier importé survit au succès du job, à la purge,
   et à un échec de création de job.

Plus la condition qui rend la règle TENABLE : le fichier importé porte le
préfixe de mission, donc il apparaît dans l'onglet Backup, donc l'utilisateur
peut effectivement le supprimer. Sans ce préfixe, « ne jamais supprimer
automatiquement » produisait un fichier fantôme qu'aucun écran n'atteignait.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent


def setup_module() -> None:
    """Crée les tables si un fichier de tests précédent a supprimé la base.

    Volontairement SANS `unlink` : ce module n'a pas besoin d'une base vierge,
    et le motif `dispose() + unlink(DB_PATH)` recopié hors de son contexte a
    déjà détruit des données réelles sur ce projet. `init_db()` est idempotent.
    """
    from app.db import init_db

    init_db()


# --------------------------------------------------------------------------- #
# 1. Invariant structurel — la seule suppression d'audio du dépôt
# --------------------------------------------------------------------------- #
def test_aucune_suppression_d_audio_hors_de_la_route_du_site() -> None:
    """Un `unlink` sur RECORDINGS_DIR ne doit exister QUE dans
    `delete_record_backup`, la route que l'utilisateur déclenche depuis
    l'onglet Backup.

    Test de forme assumé : il ne prouve pas qu'aucune suppression n'a lieu, il
    prouve qu'aucune n'a été ÉCRITE ailleurs. C'est ce qui attrape la
    réintroduction d'un helper — le mode d'échec réel ici, puisque les trois
    suppressions automatiques retirées vivaient dans des fonctions d'apparence
    anodine (`_remove_audio`, `release_audio_file`)."""
    suspects: list[tuple[str, int, str]] = []
    for chemin in sorted((RACINE / "app").rglob("*.py")):
        lignes = chemin.read_text(encoding="utf-8").splitlines()
        for i, ligne in enumerate(lignes, 1):
            code = ligne.split("//")[0]
            if "#" in code:
                code = code[: code.index("#")]
            if not re.search(r"\.unlink\s*\(|shutil\.rmtree\s*\(|os\.remove\s*\(", code):
                continue
            # Fenêtre large : le nom du répertoire peut être quelques lignes
            # plus haut (variable intermédiaire `path = RECORDINGS_DIR / ...`).
            contexte = "\n".join(lignes[max(0, i - 12) : i])
            if "RECORDINGS_DIR" not in contexte and "RECORDINGS_DIR" not in code:
                continue
            suspects.append((str(chemin.relative_to(RACINE)), i, ligne.strip()))

    autorises = [s for s in suspects if s[0].replace("\\", "/") == "app/routers/interviews.py"]
    interdits = [s for s in suspects if s not in autorises]

    assert not interdits, (
        "suppression d'audio hors de la route du site — l'audio d'un entretien "
        f"ne se supprime que par une action de l'utilisateur : {interdits}"
    )
    assert len(autorises) == 1, (
        "il doit rester EXACTEMENT une suppression d'audio dans le dépôt, celle "
        f"de `delete_record_backup` (onglet Backup). Trouvé : {autorises}"
    )


def test_le_module_des_jobs_audio_ne_supprime_plus_rien() -> None:
    """`audio_file_jobs` portait les trois suppressions automatiques. Il ne doit
    plus exposer de helper de suppression : un helper qui traîne finit par être
    rappelé, et c'est précisément ce qui s'était passé (`release_audio_file`
    était le « nom public » de `_remove_audio` pour un appelant externe)."""
    from app.services import audio_file_jobs

    for nom in ("_remove_audio", "release_audio_file"):
        assert not hasattr(audio_file_jobs, nom), (
            f"`audio_file_jobs.{nom}` est de retour : c'est un chemin de "
            "suppression automatique d'audio, contraire à la règle du projet"
        )


# --------------------------------------------------------------------------- #
# 2. Condition qui rend la règle tenable — le fichier est ATTEIGNABLE
# --------------------------------------------------------------------------- #
def test_le_fichier_importe_porte_le_prefixe_de_mission() -> None:
    """Sans ce préfixe, l'onglet Backup ne voit pas le fichier
    (`mission_backups.lister_backups` cherche par `glob("{mission.id}_*")`) :
    « ne jamais supprimer automatiquement » produirait alors un fichier
    fantôme, invisible ET indestructible. La règle et le nommage tiennent
    ensemble."""
    source = (RACINE / "app" / "routers" / "interviews.py").read_text(encoding="utf-8")
    # Fenêtre bornée par la route SUIVANTE, jamais par un nombre de caractères :
    # un `[:3000]` s'arrêtait avant la ligne de nommage dès qu'on ajoutait des
    # validations en tête de fonction, et l'échec accusait alors le code d'avoir
    # perdu le préfixe — qu'il portait toujours. La borne structurelle est aussi
    # plus STRICTE : elle ne peut plus se satisfaire d'une occurrence trouvée
    # dans la route d'à côté.
    debut = source.index('@router.post("/audio/transcribe-file")')
    fin = source.find("\n@router.", debut + 1)
    bloc = source[debut : fin if fin != -1 else len(source)]
    assert "mission_id" in bloc, (
        "la route d'import ne reçoit plus `mission_id` : le fichier ne peut "
        "plus porter le préfixe de mission, donc plus apparaître dans l'onglet "
        "Backup, donc plus être supprimé par l'utilisateur"
    )
    assert re.search(r'f"\{prefixe\}import_|f"\{mission_id\}_import_', bloc), (
        "le nom du fichier importé ne porte plus le préfixe de mission"
    )


def test_un_import_orphelin_est_visible_dans_l_onglet_backup(tmp_path) -> None:
    """Bout de la chaîne : un fichier importé que plus aucun entretien ne
    référence doit rester LISTÉ, sinon l'utilisateur ne peut pas le supprimer
    et la règle l'enferme dans un disque qui gonfle."""
    from app.services import mission_backups

    class _Mission:
        id = 42
        interviews: list = []

    (tmp_path / "42_import_1234_abcd.m4a").write_bytes(b"audio")
    (tmp_path / "99_import_9999_zzzz.m4a").write_bytes(b"autre mission")

    listing = mission_backups.lister_backups(_Mission(), tmp_path)

    noms = [e["filename"] for e in listing["orphelins"]]
    assert "42_import_1234_abcd.m4a" in noms, (
        "l'import orphelin de CETTE mission n'apparaît pas dans l'onglet "
        "Backup : l'utilisateur n'a aucun moyen de le supprimer"
    )
    assert "99_import_9999_zzzz.m4a" not in noms, (
        "l'onglet Backup d'une mission montre l'audio d'une autre mission"
    )


# --------------------------------------------------------------------------- #
# 2 bis. Le fichier importé est RATTACHÉ, donc rejouable
# --------------------------------------------------------------------------- #
def test_le_statut_d_import_expose_le_nom_du_fichier() -> None:
    """Le client en a besoin pour rattacher l'audio importé à l'entretien.
    Sans rattachement, le fichier survit mais reste orphelin : `_tranches_audio`
    ne le voit pas, donc « Relancer la transcription » ne peut pas rejouer
    depuis lui — le geste même que tout ce chantier existe pour rendre
    possible."""
    source = (RACINE / "app" / "routers" / "interviews.py").read_text(encoding="utf-8")
    bloc = source[source.index("def transcribe_file_status") :][:4000]
    assert '"filename"' in bloc, (
        "le statut d'import n'expose plus le nom du fichier : le client ne peut "
        "plus rattacher l'audio importé, qui redevient un orphelin injouable"
    )
    assert "job.filenames" in bloc, (
        "le nom doit être vide pour une RETRANSCRIPTION (`filenames`), dont les "
        "tranches sont déjà rattachées — sinon on les ré-attacherait en double"
    )


@pytest.mark.parametrize(
    "ecran,champ",
    [
        # Le mode libre porte des TRANCHES (`audio_segments`, rotation du
        # backupRecorder) ; le mode structuré n'a qu'un chemin unique
        # (`startBackupRecorder` : « l'arrêt du backup est toujours final »).
        # Les deux rattachent, mais PAS par le même champ : une copie du code
        # de l'un vers l'autre écrirait dans un champ inexistant.
        ("record_libre.html", "enregistrerAudioImporte"),
        ("record.html", "backupPathHidden.value = data.filename"),
    ],
)
def test_chaque_ecran_rattache_l_import_par_SON_mecanisme(ecran: str, champ: str) -> None:
    source = (RACINE / "app" / "templates" / "interviews" / ecran).read_text(
        encoding="utf-8"
    )
    assert champ in source, (
        f"{ecran} ne rattache plus le fichier importé à l'entretien : il "
        "survivra sur le disque en orphelin, sans rejeu possible"
    )
    assert "formData.append('mission_id'" in source, (
        f"{ecran} n'envoie plus `mission_id` à l'import : le fichier perdra le "
        "préfixe de mission, donc toute visibilité dans l'onglet Backup"
    )


def test_le_mode_structure_n_ecrit_pas_dans_un_champ_de_tranches_inexistant() -> None:
    """Garde anti-copier-coller : `record.html` n'a pas de champ
    `audio_segments` (grep : le `<input>` n'existe que dans le mode libre).
    Y écrire depuis le chemin d'import lèverait un TypeError sur `null` au
    moment précis où la transcription vient d'aboutir."""
    source = (RACINE / "app" / "templates" / "interviews" / "record.html").read_text(
        encoding="utf-8"
    )
    assert 'name="audio_segments"' not in source, (
        "record.html a gagné un champ `audio_segments` — ce test et le "
        "rattachement d'import doivent être revus ensemble"
    )
    assert "audioSegmentsHidden" not in source, (
        "record.html manipule `audioSegmentsHidden`, qui n'y existe pas : "
        "copier-coller depuis record_libre.html"
    )


# --------------------------------------------------------------------------- #
# 3. Invariant comportemental — le fichier survit
# --------------------------------------------------------------------------- #
def test_la_purge_efface_les_lignes_de_base_mais_pas_l_audio(tmp_path, monkeypatch) -> None:
    """La purge à 7 j garde sa raison d'être — les lignes portent du texte
    d'entretien — mais ne touche plus au fichier, qui appartient à
    l'utilisateur."""
    from app.db import SessionLocal
    from app.models import AudioFileJob
    from app.services import audio_file_jobs

    monkeypatch.setattr(audio_file_jobs, "RECORDINGS_DIR", tmp_path)
    fichier = tmp_path / "7_import_1_aaaa.m4a"
    fichier.write_bytes(b"audio")

    db = SessionLocal()
    try:
        vieux = AudioFileJob(
            session_token="purge-audio",
            filename=fichier.name,
            status="done",
            blocks=["du texte d'entretien"],
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(days=30),
        )
        db.add(vieux)
        db.commit()
        job_id = vieux.id

        audio_file_jobs.purge_stale_audio_file_jobs(db)

        assert db.get(AudioFileJob, job_id) is None, (
            "la purge ne fait plus son travail sur les lignes de base"
        )
        assert fichier.is_file(), (
            "la purge a supprimé l'audio de l'utilisateur — elle ne doit "
            "effacer que les lignes de base"
        )
    finally:
        db.close()
