"""Inventaire des enregistrements audio d'une mission (onglet « Backup »).

Les fichiers vivent sur disque (`data/recordings/`, hors base) et sont écrits
par `save_record_backup` sous le nom `{mission_id}_{horodatage}_{hex}.webm` ;
la base n'en garde que des RÉFÉRENCES, portées par l'entretien :
`Interview.audio_backup_path` (dernière tranche, rétrocompatible) et
`Interview.audio_segments` (liste ordonnée des tranches).

Deux populations coexistent donc, et l'écran doit montrer les deux — c'est
justement la seconde qui s'accumule et qu'on veut pouvoir nettoyer :

- les fichiers RATTACHÉS à un entretien enregistré ;
- les ORPHELINS : même mission (préfixe du nom), mais plus aucun entretien ne
  les référence. Un enregistrement abandonné avant l'écran de confirmation en
  laisse un à chaque fois, et la suppression d'un entretien laisse les siens
  sur le disque (`delete_interview` ne touche pas au disque).

Aucune écriture ici : ce module lit le disque et la base, la suppression est
la route qui l'utilise.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

# Un « orphelin » plus jeune que ce seuil peut appartenir à un enregistrement
# EN COURS : pendant le wizard, les tranches uploadées ne sont référencées que
# par un champ caché du formulaire — l'entretien (donc la référence en base)
# n'existe qu'à la confirmation. 6 h = un entretien de 3 h + marge (revue
# adversariale 2026-07-29 : l'écran invitait à « nettoyer en priorité » des
# tranches dont l'enregistrement tournait encore dans un autre onglet).
ORPHELIN_RECENT_S = 6 * 3600


def _stat(path: Path) -> tuple[int, float]:
    try:
        st = path.stat()
        return st.st_size, st.st_mtime
    except OSError:
        return 0, 0.0


def _horodatage(mtime: float) -> str:
    """« 29/07/2026 à 14:32 » — vide si la date est inconnue (fichier disparu,
    `_stat` ayant alors rendu 0.0 : une date de 1970 mentirait à l'écran)."""
    if not mtime:
        return ""
    try:
        return datetime.fromtimestamp(mtime).strftime("%d/%m/%Y à %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def _ordre_chronologique(entree: dict) -> tuple:
    """Du plus ancien au plus récent — l'ordre dans lequel les enregistrements
    ont été faits, et celui dans lequel on les nettoie (les plus vieux d'abord).

    Un fichier disparu n'a pas de date : il passe en fin de liste plutôt qu'en
    tête, où son `mtime` à 0 le placerait. Le nom départage deux fichiers de
    même horodatage, pour que l'ordre soit stable d'un affichage à l'autre."""
    return (0 if entree["mtime"] else 1, entree["mtime"], entree["filename"])


def fichiers_references(mission) -> dict[str, list]:
    """Nom de fichier -> entretiens qui le référencent (plusieurs possibles :
    `audio_backup_path` reprend la dernière tranche d'`audio_segments`)."""
    refs: dict[str, list] = {}
    for interview in mission.interviews:
        noms = [seg.get("filename") for seg in (interview.audio_segments or []) if seg.get("filename")]
        if interview.audio_backup_path:
            noms.append(interview.audio_backup_path)
        for nom in noms:
            refs.setdefault(nom, [])
            if interview not in refs[nom]:
                refs[nom].append(interview)
    return refs


def appartient_a_mission(filename: str, mission_id: int, mission=None) -> bool:
    """Garde d'appartenance pour la suppression : un fichier n'est supprimable
    depuis une mission que s'il porte son préfixe OU s'il est référencé par un
    de ses entretiens (cas d'un entretien réattaché à une autre mission, dont
    le fichier garde le préfixe de la mission brouillon d'origine).

    Le préfixe est comparé AVEC son séparateur (`13_`), sinon la mission 1
    revendiquerait les fichiers de la mission 13."""
    if filename.startswith(f"{mission_id}_"):
        return True
    return mission is not None and filename in fichiers_references(mission)


def lister_backups(mission, recordings_dir: Path) -> dict:
    """Enregistrements de la mission, rattachés puis orphelins, chacune des deux
    listes du plus ancien au plus récent (`_ordre_chronologique`).

    Chaque entrée : `filename`, `taille` (octets), `mtime`, `horodatage` (date
    et heure lisibles, vide si le fichier a disparu), `existe`, `interview`
    (`None` pour un orphelin), `position` (rang de tranche, `None` si le fichier
    n'est référencé que par `audio_backup_path`)."""
    refs = fichiers_references(mission)

    rattaches = []
    for interview in mission.interviews:
        segments = sorted(
            (s for s in (interview.audio_segments or []) if s.get("filename")),
            key=lambda s: s.get("position") or 0,
        )
        noms = [(s["filename"], s.get("position")) for s in segments]
        # Entretien d'avant la segmentation (2026-07-20) : une seule sauvegarde,
        # référencée par `audio_backup_path` seul.
        if interview.audio_backup_path and not any(n == interview.audio_backup_path for n, _ in noms):
            noms.append((interview.audio_backup_path, None))
        for nom, position in noms:
            chemin = recordings_dir / nom
            taille, mtime = _stat(chemin)
            rattaches.append({
                "filename": nom,
                "taille": taille,
                "mtime": mtime,
                "horodatage": _horodatage(mtime),
                "existe": chemin.is_file(),
                "interview": interview,
                "position": position,
                "nb_tranches": len(segments),
            })

    orphelins = []
    prefixe = f"{mission.id}_"
    if recordings_dir.is_dir():
        for chemin in sorted(recordings_dir.glob(f"{prefixe}*")):
            if not chemin.is_file() or chemin.name in refs:
                continue
            taille, mtime = _stat(chemin)
            orphelins.append({
                "filename": chemin.name,
                "taille": taille,
                "mtime": mtime,
                "horodatage": _horodatage(mtime),
                "existe": True,
                "interview": None,
                "position": None,
                "nb_tranches": 0,
                "recent": bool(mtime) and (time.time() - mtime) < ORPHELIN_RECENT_S,
            })

    rattaches.sort(key=_ordre_chronologique)
    orphelins.sort(key=_ordre_chronologique)

    total = sum(e["taille"] for e in rattaches + orphelins)
    return {"rattaches": rattaches, "orphelins": orphelins, "taille_totale": total}
