"""Couche d'accès SQLite : engine, session, initialisation du schéma."""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

logger = logging.getLogger(__name__)

# Chemin de la base, surchargeable via APP_DB_PATH (utilisé par les tests pour
# pointer vers une base jetable et ne jamais toucher la base de dev/prod).
_env_db_path = os.environ.get("APP_DB_PATH")
if _env_db_path:
    DB_PATH = Path(_env_db_path)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_DIR = DB_PATH.parent
else:
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"
    DATA_DIR.mkdir(exist_ok=True)
    DB_PATH = DATA_DIR / "app.db"

# Sauvegardes audio brutes des entretiens enregistrés (filet de sécurité) —
# même dossier de données que la base, donc déjà couvert par `data/` dans
# .gitignore.
RECORDINGS_DIR = DATA_DIR / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)

# Templates PPT client uploadés, utilisés comme base pour l'export PPT
# (évol) — même convention que RECORDINGS_DIR.
PPTX_TEMPLATES_DIR = DATA_DIR / "pptx_templates"
PPTX_TEMPLATES_DIR.mkdir(exist_ok=True)

# Decks d'exemple uploadés (US5.2) : leur plan (suite d'archétypes) réordonne
# et filtre les slides de l'export — même convention que PPTX_TEMPLATES_DIR.
PPTX_EXEMPLES_DIR = DATA_DIR / "pptx_exemples"
PPTX_EXEMPLES_DIR.mkdir(exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


@event.listens_for(engine, "connect")
def _enable_sqlite_fk(dbapi_connection, _connection_record) -> None:
    """SQLite n'applique pas les clés étrangères sans ce PRAGMA."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False
)


def _add_missing_columns() -> None:
    """Migrations additives légères (SQLite) : ajoute les colonnes absentes.

    `create_all` crée les tables manquantes mais n'altère pas une table
    existante — on ajoute donc à la main les colonnes introduites après coup.
    """
    additions = {
        "interviews": {
            "reference_text": "TEXT",
            "audio_backup_path": "TEXT",
            "mode": "TEXT DEFAULT 'parametre'",
            "repartition": "JSON",
            "resume": "TEXT",
            "raw_transcript": "TEXT",
            "audio_segments": "JSON",
            # Persistance du bandeau « tranches manquantes » (2026-09-04,
            # bmad-code-review finding F2) : le compteur ne voyageait qu'en
            # query string sur la redirection d'enregistrement — un simple F5
            # sur la fiche le perdait, sans qu'aucune trace ne subsiste.
            "tranches_manquantes": "INTEGER DEFAULT 0",
        },
        # Axes de synthèse configurables (2026-07-27) : le contenu passe des 5
        # colonnes figées à un dictionnaire par clé d'axe. Les colonnes
        # historiques restent (miroir des 5 clés par défaut, cf.
        # `GlobalSynthesis.set_contenu`).
        "global_syntheses": {
            "valeurs": "JSON",
            "generation_status": "TEXT DEFAULT 'idle'",
            "generation_error": "TEXT",
        },
        "trames": {"intro_text": "TEXT"},
        "questions": {"help_text": "TEXT"},
        "missions": {"pptx_template_path": "TEXT", "pptx_exemple_path": "TEXT", "is_draft": "BOOLEAN DEFAULT 0", "restitution_verbatim_ids": "JSON", "is_demo": "BOOLEAN DEFAULT 0"},
        "interview_turns": {"section_title": "TEXT"},
        "interview_segment_jobs": {
            "text": "TEXT DEFAULT ''",
            # Extension 2026-07-25 (répartition Q/R au fil de l'eau, mode
            # structuré) : nature du job + mission porteuse de la trame.
            # Pas de clause REFERENCES sur une colonne ajoutée après coup
            # (les jobs sont éphémères, la FK n'est portée que par les bases
            # neuves via create_all) — même compromis que `interview_id`.
            "kind": "TEXT DEFAULT 'libre_turns'",
            "mission_id": "INTEGER",
        },
        # Retranscription d'un entretien enregistré (2026-07-30) : plusieurs
        # tranches persistées à enchaîner. Pas de clause REFERENCES sur
        # `interview_id` ajoutée après coup (même compromis que ci-dessus).
        # Drapeau « édité à la main » des trois listes de suivi (G, 2026-09-27) :
        # la garde de régénération refuse d'écraser une ligne éditée sans
        # confirmation. Migration ADDITIVE, donc les lignes déjà éditées AVANT
        # elle se relisent à `edite = 0` — limite assumée, motivée dans
        # `docs/adr/0001-garde-regeneration-lignes-editees.md` : la garde
        # protège les éditions postérieures à la migration.
        "mission_kpis": {"edite": "BOOLEAN DEFAULT 0"},
        "mission_risks": {"edite": "BOOLEAN DEFAULT 0"},
        "mission_maturites": {"edite": "BOOLEAN DEFAULT 0"},
        # Extension aux autres surfaces régénérables (2026-09-27, arbitrage
        # utilisateur « garde limitée à 3 surfaces sur 8 ») : mêmes colonne, même
        # limite assumée. Les trois enregistrements UNIQUES (synthèse globale,
        # SWOT, executive summary) n'apparaissent pas ici : leur marqueur est le
        # `status = "edited"` qui existe déjà, aucune migration à faire.
        "mission_difficulties": {"edite": "BOOLEAN DEFAULT 0"},
        "recommendation_axes": {"edite": "BOOLEAN DEFAULT 0"},
        "recommendations": {"edite": "BOOLEAN DEFAULT 0"},
        "audio_file_jobs": {
            "filenames": "JSON",
            "interview_id": "INTEGER",
            "files_done": "INTEGER DEFAULT 0",
            "blocks_before_file": "INTEGER DEFAULT 0",
        },
    }
    with engine.begin() as conn:
        for table, cols in additions.items():
            existing = {
                row[1]
                for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            for name, ddl in cols.items():
                if name not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                    )


# (nom, table, colonnes de la clé, ORDER BY qui désigne le survivant).
# L'ordre est celui d'un `ORDER BY … LIMIT 1` : la PREMIÈRE ligne du groupe est
# gardée, les autres sont supprimées.
_INDEX_UNIQUES = [
    # (2026-09-09, audit robustesse) Deux jobs au même (session_token,
    # position, kind) sortent leurs tours de parole en double à la fusion.
    # Survivant : celui qui a un résultat (`turns_result IS NULL` trie les
    # aboutis en premier), à défaut le plus ancien — jeter un job abouti
    # ferait repayer un appel IA de plusieurs minutes.
    (
        "uq_segment_job_tranche",
        "interview_segment_jobs",
        ("session_token", "position", "kind"),
        "(turns_result IS NULL), rowid",
    ),
]


def _add_missing_indexes() -> None:
    """Pose les contraintes d'unicité que `create_all` n'applique qu'aux bases
    NEUVES, sur les bases déjà créées.

    `_add_missing_columns` ne sait qu'ajouter des colonnes : une contrainte
    ajoutée après coup au modèle n'atteint jamais une base existante, et
    l'invariant ne tiendrait que sur les postes installés après le correctif —
    c'est-à-dire nulle part où les données ont de la valeur.

    Chaque entrée est dédoublonnée AVANT la création de l'index, sinon celle-ci
    échoue sur toute base qui porte déjà le défaut que l'index vient interdire
    (et `init_db` étant appelé au démarrage, l'application ne démarrerait plus).
    Le doublon conservé est celui qui porte un résultat, à défaut le plus ancien
    (`rowid` minimal — pas `id`, pour rester indépendant du nom de la clé
    primaire) : jeter un job abouti ferait repayer un appel IA de plusieurs
    minutes, et perdre le seul exemplaire du texte de la tranche.
    """
    with engine.begin() as conn:
        _poser_index_uniques(conn, _INDEX_UNIQUES)


def _index_unique_existe(conn, table: str, colonnes: tuple[str, ...]) -> bool:
    """Vrai si une contrainte d'unicité porte DÉJÀ exactement ces colonnes.

    Cherchée par ses COLONNES et non par son nom : sur une base neuve, la
    contrainte vient de `__table_args__` et `create_all` l'écrit en ligne dans
    le CREATE TABLE — SQLite l'indexe alors sous un nom automatique
    (`sqlite_autoindex_…`). Une détection par nom ne la verrait pas et
    poserait un SECOND index redondant sur toute installation neuve.
    """
    for row in conn.exec_driver_sql(f"PRAGMA index_list({table})"):
        nom_index, unique, partiel = row[1], row[2], row[4]
        if not unique or partiel:
            # Un index unique PARTIEL (`… WHERE <condition>`) ne contraint que
            # les lignes qui satisfont sa condition : le prendre pour
            # l'invariant ferait SAUTER la pose du vrai index, en silence, et
            # les doublons rentreraient sans erreur. Reproduit en sqlite3
            # isolé par la revue adversariale du 2026-09-10 (finding 8) —
            # latent ici (aucun index partiel sur cette table) mais
            # `_INDEX_UNIQUES` est un mécanisme destiné à grandir.
            continue
        portees = tuple(
            info[2]
            for info in conn.exec_driver_sql(f"PRAGMA index_info('{nom_index}')")
        )
        if set(portees) == set(colonnes) and len(portees) == len(colonnes):
            return True
    return False


def _poser_index_uniques(conn, index_uniques) -> None:
    """Le corps de `_add_missing_indexes`, sur une connexion donnée — pour que
    les tests puissent l'exercer sur une base à l'ANCIEN schéma (sans la
    contrainte), qu'aucun `create_all` ne sait plus produire."""
    for nom, table, colonnes, ordre in index_uniques:
        if not _index_unique_existe(conn, table, colonnes):
            cles = ", ".join(colonnes)
            # `ROW_NUMBER() OVER (PARTITION BY …)` numérote chaque ligne dans
            # son groupe de doublons ; on garde la n°1 et on supprime le reste.
            # Le partitionnement traite deux NULL comme égaux — c'est ce qu'on
            # veut ici (`kind` a un défaut, mais une base ancienne peut porter
            # des NULL), là où un `=` en sous-requête ne les apparierait jamais.
            # `rowid` plutôt que `id` : indépendant du nom de la clé primaire,
            # dont `id` est un alias sur ces tables.
            supprimees = conn.exec_driver_sql(
                f"DELETE FROM {table} WHERE rowid IN ("
                f" SELECT rowid FROM ("
                f"  SELECT rowid, ROW_NUMBER() OVER ("
                f"   PARTITION BY {cles} ORDER BY {ordre}"
                f"  ) AS rang FROM {table}"
                f" ) WHERE rang > 1"
                f")"
            ).rowcount
            if supprimees:
                # Ce DELETE est la SEULE opération destructive du démarrage, et
                # elle porte sur des données d'entretien. Les trois
                # réconciliations du `lifespan` journalisent leur compte alors
                # qu'elles se contentent de ré-étiqueter : celle-ci le doit
                # d'autant plus. Sans cette ligne, si le dédoublonnage mange
                # une tranche réelle, rien ne le dit (revue du 2026-09-10, n°7).
                logger.warning(
                    "%d doublon(s) supprimé(s) dans %s à la pose de l'index "
                    "unique %s (clé : %s)", supprimees, table, nom, cles,
                )
            conn.exec_driver_sql(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {nom} ON {table} ({cles})"
            )


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()
    _add_missing_indexes()


def get_session() -> Iterator[Session]:
    """Dépendance FastAPI : ouvre une session par requête."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
