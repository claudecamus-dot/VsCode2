"""`lister_orphelins_globaux` : ne pas recharger les colonnes lourdes.

Constat d'audit du hub (performance, 2026-09-09), 3e volet de `list_missions`
(les deux premiers — pagination et N+1 de `nb_brouillons_vides` — sont déjà
traités, cf. `_compter_brouillons_vides` et l'arbitrage utilisateur du
2026-09-10 sur la pagination) : la fonction "recharge toutes les Missions et
toutes les Interviews" pour calculer le badge audio orphelin de la page la
plus visitée du produit, alors qu'elle n'a besoin que de quelques colonnes
étroites par ligne :

- `Interview` : `audio_segments` et `audio_backup_path` seulement. Le modèle
  porte aussi `raw_transcript`, `resume`, `reference_text`, `free_notes`,
  `repartition` — le texte intégral (ou la répartition IA) d'un entretien
  CLIENT réel, `Text`/`JSON` sans borne de taille.
- `AudioFileJob` : `status`, `created_at`, `filename`, `filenames` seulement
  (`is_audio_file_job_stale` ne lit que les deux premiers). Le modèle porte
  aussi `blocks`, les blocs déjà transcrits d'un import en cours — même nature
  que `raw_transcript`.
- `Mission` : `id` et `created_at` seulement (`_epoch_creation`).

`select(Interview)`/`select(AudioFileJob)`/`select(Mission)` nus chargent
TOUTES les colonnes ORM, ces colonnes lourdes comprises, sur CHAQUE ligne de
CHAQUE table, à CHAQUE visite de `/missions` — alors que le résultat de la
fonction n'en expose jamais le contenu (seuls des noms de fichiers, tailles et
horodatages en sortent). Un comptage de requêtes SQL (cf.
`test_missions_liste_cout_sql.py`) est aveugle à ce coût : le nombre de
requêtes reste constant, seul le volume par ligne explose.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import event

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import AudioFileJob, Interview, Mission
from app.services import mission_backups

# Colonnes lourdes qu'aucune requête de `lister_orphelins_globaux` ne doit
# jamais mentionner dans sa liste de colonnes SELECT.
_COLONNES_LOURDES_INTERVIEW = (
    "raw_transcript", "resume", "reference_text", "free_notes", "repartition",
)
_COLONNES_LOURDES_AUDIO_FILE_JOB = ("blocks",)


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


def _semer_un_entretien_avec_transcript_volumineux() -> None:
    """Une mission, un entretien libre portant un `raw_transcript` volumineux
    (texte d'entretien réel) et un import audio portant des `blocks`
    volumineux — exactement ce que `lister_orphelins_globaux` ne doit PAS
    remonter en Python pour calculer un badge de comptage."""
    gros_texte = "Verbatim client. " * 2000  # ~34 Ko, loin d'être un cas d'école
    with SessionLocal() as db:
        mission = Mission(name="Mission avec gros entretien", is_draft=False)
        db.add(mission)
        db.flush()
        db.add(
            Interview(
                mission_id=mission.id,
                interviewee_name="Client",
                mode="libre",
                raw_transcript=gros_texte,
                resume="Un résumé.",
                audio_segments=[],
            )
        )
        db.add(
            AudioFileJob(
                filename="import.wav",
                status="done",
                blocks=[gros_texte, gros_texte],
            )
        )
        db.commit()


def _requetes_de_lister_orphelins_globaux(recordings_dir: Path) -> list[str]:
    captees: list[str] = []
    ecouteur = lambda c, cur, s, p, ctx, m: captees.append(s)  # noqa: E731
    event.listen(engine, "before_cursor_execute", ecouteur)
    try:
        with SessionLocal() as db:
            resultat = mission_backups.lister_orphelins_globaux(recordings_dir, db)
    finally:
        event.remove(engine, "before_cursor_execute", ecouteur)
    assert resultat is not None
    return captees


@pytest.mark.parametrize(
    "table, colonnes_lourdes",
    [
        ("interviews", _COLONNES_LOURDES_INTERVIEW),
        ("audio_file_jobs", _COLONNES_LOURDES_AUDIO_FILE_JOB),
    ],
)
def test_ne_charge_pas_les_colonnes_lourdes(tmp_path, table, colonnes_lourdes):
    _semer_un_entretien_avec_transcript_volumineux()
    requetes = _requetes_de_lister_orphelins_globaux(tmp_path)
    requetes_table = [s for s in requetes if f"FROM {table}" in s]
    assert requetes_table, (
        f"aucune requete SELECT ... FROM {table} executee : le test ne "
        "verifie plus rien"
    )
    for sql in requetes_table:
        # Noms de colonnes EXACTS (`table.colonne`), jamais une sous-chaine :
        # "blocks" est aussi une sous-chaine de "blocks_before_file" et
        # "total_blocks", qui ne sont pas la colonne visee.
        colonnes_select = {
            nom for nom in re.findall(rf"{table}\.(\w+)", sql.split(" FROM ")[0])
        }
        chargees = colonnes_select & set(colonnes_lourdes)
        assert not chargees, (
            f"colonne(s) lourde(s) {sorted(chargees)} chargee(s) inutilement "
            f"par lister_orphelins_globaux sur {table} : {sql}"
        )
