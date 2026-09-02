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
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

# Un « orphelin » plus jeune que ce seuil peut appartenir à un enregistrement
# EN COURS : pendant le wizard, les tranches uploadées ne sont référencées que
# par un champ caché du formulaire — l'entretien (donc la référence en base)
# n'existe qu'à la confirmation. 6 h = un entretien de 3 h + marge (revue
# adversariale 2026-07-29 : l'écran invitait à « nettoyer en priorité » des
# tranches dont l'enregistrement tournait encore dans un autre onglet).
ORPHELIN_RECENT_S = 6 * 3600


_MEDIA_AUDIO = {
    ".webm": "audio/webm",
    ".weba": "audio/webm",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
    # `audio/mp4` et non `video/mp4` : le produit n'a QUE des `<audio>`, qui ne
    # lisent que la piste audio, et un `.mp4` audio-seul (dictaphone iOS, sortie
    # Safari) est le cas réel ici. Le passage à `video/mp4` du 2026-09-02 était
    # un passager clandestin — sans rapport avec le filtre de l'inventaire qu'il
    # accompagnait, sans test, et contredisant le docstring juste en dessous
    # (constat `D2-m6`).
    ".mp4": "audio/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mp3": "audio/mpeg",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
}

# L'inventaire global filtre par EXCLUSION, jamais par liste blanche
# (revue adversariale du 2026-09-02, constat BLOQUANT D1-B1). La veille, il
# n'admettait que les 8 extensions ci-dessus — or l'application accepte
# `audio/*,video/*,.weba` (l'attribut `accept` des deux écrans). Un `.mkv`,
# `.mov`, `.opus`, `.aac` ou `.weba` rattaché à une mission déjà supprimée était
# donc écrit sur disque, absent de cet inventaire ET refusé par sa route de
# lecture — pendant que le bandeau affirmait à l'utilisateur que son audio était
# « en sécurité dans Audio sans mission ». C'était C1 refabriqué, en pire :
# silencieux et démenti par l'écran.
#
# L'asymétrie des coûts commande la forme du filtre. Une exclusion à tort rend
# de l'audio d'entretien définitivement inatteignable ; une inclusion à tort
# montre un fichier parasite sur un écran d'administration. La liste des
# conteneurs média est ouverte, celle des fichiers qu'on ne veut pas voir est
# fermée : on nomme donc ces derniers.
_EXTENSIONS_NON_MEDIA = frozenset({
    ".txt", ".md", ".json", ".yml", ".yaml", ".ini", ".cfg", ".toml",
    ".log", ".csv", ".tsv", ".db", ".sqlite", ".sqlite3", ".py", ".pyc",
    ".html", ".css", ".js", ".zip", ".tmp", ".bak", ".lock",
})


def est_media(nom: str) -> bool:
    """Ce fichier de `recordings/` peut-il être de l'audio d'entretien ?

    Répond OUI par défaut : le doute profite au fichier. Seuls un nom caché
    (`.gitkeep`) et une extension explicitement non média sont écartés.
    """
    if nom.startswith("."):
        return False
    return Path(nom).suffix.lower() not in _EXTENSIONS_NON_MEDIA


def suffixe_sur(nom_client: str | None, defaut: str) -> str:
    """Extension sûre à donner au fichier écrit, déduite du nom envoyé par le client.

    Les DEUX routes d'écriture (`save_record_backup` et `transcribe_file`)
    portaient la même logique recopiée, ce qui a produit exactement le défaut
    qu'on attend d'un code dupliqué : le garde-fou B4 n'a été posé que sur l'une
    des deux, et le chemin frère est resté ouvert une journée entière (constat
    `B4-SIBLING`, revue du 2026-09-02). La logique vit désormais ici, une fois.

    Trois filtres, dans cet ordre :

    1. ni « / » ni « .. » ne survivent — le garde-fou de `get_record_backup`
       passe comme avant ;
    2. un point sans rien derrière n'est pas une extension (constat B4).
       Windows retire le point final à l'écriture, donc le nom rendu au client
       ne désignerait plus aucun fichier : référence cassée d'un côté, orphelin
       de l'autre. L'exigence `isascii()` s'ajoute à `isalnum()`, vrai pour les
       chiffres et lettres UNICODE — le module s'est déjà fait avoir par
       `isdigit()` sur « ² », inutile de laisser le même piège deux fois ;
    3. l'extension doit passer `est_media` (constat `D1-B1-BIS`). Sans ce
       dernier filtre, la route d'écriture acceptait ce que la route de lecture
       refuse : un `notes.txt` rattaché à une mission supprimée était écrit sur
       disque, annoncé « en sécurité dans Audio sans mission », **absent de
       l'inventaire et refusé en 404 par sa propre route de lecture**. Mesuré :
       23 noms sur 37. C'était C1 refabriqué, silencieux et démenti par l'écran.

    Le repli est toujours `defaut`, jamais un refus : sur ces chemins-là, l'onglet
    peut détenir la seule copie de l'audio, et un nom bizarre ne justifie pas de
    la détruire.
    """
    suffixe = "".join(c for c in (nom_client or "")[-16:] if c.isalnum() or c == ".")
    suffixe = suffixe[suffixe.rfind("."):] if "." in suffixe else defaut
    if len(suffixe) < 2 or not suffixe[1:].isascii() or not suffixe[1:].isalnum():
        return defaut
    # Nom-sonde et non le suffixe nu : `est_media` écarte les noms cachés
    # (« .gitkeep »), et un suffixe commence justement par un point.
    if not est_media(f"x{suffixe}"):
        return defaut
    return suffixe


def mission_id_du_fichier(nom: str) -> int | None:
    """Identifiant de mission porté par le préfixe d'un enregistrement, ou None.

    Les noms écrits par les deux routes valent `{mission_id}_...` — c'est déjà
    ce que `lister_orphelins_globaux` lit pour dire de quelle mission un fichier
    vient. La lecture est isolée ici parce qu'un troisième appelant en a besoin
    (constat `D1-F3`) : `transcribe_file_status` doit pouvoir dire au client que
    la mission a disparu PENDANT l'import, et `AudioFileJob` ne porte pas de
    `mission_id`.

    `isdigit()` est vrai pour les chiffres Unicode (« ² »), que `int()` refuse —
    le module s'est déjà fait piéger là-dessus, d'où le `try` plutôt qu'un test.
    """
    prefixe, _, reste = (nom or "").partition("_")
    if not reste:
        return None
    # Même exigence que les deux autres lecteurs du même préfixe (constat
    # `D2-m3`) : `lister_orphelins_globaux` demande `isdigit() and isascii()`,
    # et `appartient_a_mission` compare un `startswith(f"{id}_")`. Sans cette
    # ligne, cette fonction-ci acceptait `-5_x`, `+5_x`, `« 5_x »` et `007_x`
    # que les deux autres refusent — trois lectures divergentes du même nom,
    # dans un module dont c'est justement le sujet.
    if not (prefixe.isdigit() and prefixe.isascii()):
        return None
    try:
        valeur = int(prefixe)
    except ValueError:
        return None
    # Forme CANONIQUE exigée : `appartient_a_mission` compare un
    # `startswith(f"{id}_")`, donc « 007_… » ne désignera jamais la mission 7
    # pour lui. Rendre 7 ici en ferait une troisième lecture du même nom,
    # différente des deux autres — exactement ce que cette fonction existe pour
    # éviter. Nos propres routes n'écrivent que du canonique.
    if str(valeur) != prefixe:
        return None
    return valeur


def media_type_audio(filename: str) -> str:
    """Type MIME d'un enregistrement d'apres son extension.

    Tout etait servi en `audio/webm` tant que le seul producteur etait le
    magnetophone. Depuis qu'on rattache des fichiers importes (Meet en `.m4a`,
    dictaphone en `.mp3`), un type errone rend l'audio muet dans le lecteur du
    navigateur sans le moindre message. Repli sur `audio/webm` pour une
    extension inconnue : c'est le cas historique, et un type approximatif se
    rattrape mieux qu'un refus.
    """
    return _MEDIA_AUDIO.get(Path(filename).suffix.lower(), "audio/webm")

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


def _epoch_creation(mission) -> float:
    """`mission.created_at` en secondes epoch, comparable à un `st_mtime`.

    Rendu naïf par SQLite (la colonne est un `DateTime` sans fuseau) mais écrit
    en UTC par `models._utcnow` : on rétablit le fuseau avant de convertir,
    sinon la comparaison dérive de l'offset local — jusqu'à 2 h en France, soit
    exactement la fenêtre où un fichier fraîchement écrit paraîtrait antérieur
    à la mission qui vient de le produire. Rend `0.0` si la date est absente
    (mission non encore persistée), ce qui neutralise la garde au lieu de
    rejeter à tort."""
    date = getattr(mission, "created_at", None)
    if date is None:
        return 0.0
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    try:
        return date.timestamp()
    except (OverflowError, OSError, ValueError):
        return 0.0


def _anterieur_a_la_mission(chemin: Path, mission) -> bool:
    """Le fichier existait AVANT la mission — il ne peut donc pas être à elle.

    C'est la garde contre la réutilisation d'id de SQLite (revue du
    2026-09-01, constat C3). `Mission.id` est un rowid sans `AUTOINCREMENT` :
    supprimer la mission la plus récente rend son id à la suivante, qui hérite
    alors du préfixe de fichier de la précédente. Elle listait, servait et
    pouvait supprimer l'audio d'entretien d'un autre client — et depuis que
    plus rien ne s'efface tout seul, ces fichiers l'attendent indéfiniment.

    La chronologie tranche sans ambiguïté : l'audio d'une mission est
    forcément écrit APRÈS sa création, celui de la mission précédente
    forcément avant. Une horloge système reculée entre les deux fausserait la
    comparaison ; le fichier est alors traité comme étranger, donc invisible et
    non supprimable depuis cette mission — on préfère ne pas montrer un fichier
    à sa mission que montrer celui d'une autre."""
    creation = _epoch_creation(mission)
    if not creation:
        return False
    _, mtime = _stat(chemin)
    return bool(mtime) and mtime < creation


def appartient_a_mission(
    filename: str, mission_id: int, mission=None, recordings_dir: Path | None = None
) -> bool:
    """Garde d'appartenance pour la suppression : un fichier n'est supprimable
    depuis une mission que s'il porte son préfixe OU s'il est référencé par un
    de ses entretiens (cas d'un entretien réattaché à une autre mission, dont
    le fichier garde le préfixe de la mission brouillon d'origine).

    Le préfixe est comparé AVEC son séparateur (`13_`), sinon la mission 1
    revendiquerait les fichiers de la mission 13.

    La RÉFÉRENCE prime sur la chronologie : un entretien qui pointe le fichier
    en fait la propriété de sa mission quoi qu'en dise l'horodatage. Le préfixe
    seul, lui, est soumis à la garde anti-réutilisation d'id
    (`_anterieur_a_la_mission`) dès que `recordings_dir` est fourni — sans lui
    la garde ne peut pas s'appliquer (pas de `mtime` à lire) et le comportement
    reste celui d'avant, pour ne casser aucun appelant."""
    if mission is not None and filename in fichiers_references(mission):
        return True
    if not filename.startswith(f"{mission_id}_"):
        return False
    if mission is None or recordings_dir is None:
        return True
    return not _anterieur_a_la_mission(recordings_dir / filename, mission)


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
            # Garde anti-réutilisation d'id (C3) : un fichier antérieur à la
            # mission appartient à celle qui portait ce numéro avant elle. Il
            # n'est donc pas listé ici — il l'est dans l'inventaire global
            # (`lister_orphelins_globaux`), qui est le seul écran habilité à
            # montrer un fichier dont on ne sait plus de quelle mission il vient.
            if _anterieur_a_la_mission(chemin, mission):
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


def fichiers_de_mission(mission, recordings_dir: Path) -> list[Path]:
    """Tous les fichiers audio que cette mission possède — préfixe ET
    références de ses entretiens, dédoublonnés.

    Sert à la suppression d'une mission : sans elle, `db.delete(mission)`
    laissait son audio sur le disque avec un préfixe désormais mort, donc
    atteignable par aucun écran (constat C1). Les références sont incluses
    parce qu'un entretien réattaché garde le nom hérité de sa mission
    brouillon d'origine — le préfixe seul les manquerait."""
    noms = set(fichiers_references(mission))
    if recordings_dir.is_dir():
        for chemin in recordings_dir.glob(f"{mission.id}_*"):
            if chemin.is_file() and not _anterieur_a_la_mission(chemin, mission):
                noms.add(chemin.name)
    return [recordings_dir / nom for nom in sorted(noms) if (recordings_dir / nom).is_file()]


def _raison_orphelin(prefixe: str, mission, mtime: float | None) -> str:
    """Pourquoi ce fichier n'apparaît dans l'onglet Backup d'aucune mission.

    Trois causes, à ne pas confondre :

    1. le nom ne porte aucun préfixe de mission (convention d'avant le
       2026-09-01) — aucun `glob("{id}_*")` ne peut le trouver ;
    2. la mission dont il porte le numéro n'existe plus ;
    3. la mission dont il porte le numéro existe, mais ce n'est PAS la même :
       SQLite réattribue l'identifiant d'une ligne supprimée, donc le fichier
       appartient à une mission homonyme antérieure. C'est le cas que la garde
       de `_anterieur_a_la_mission` attrape, et celui qu'il faut expliquer le
       plus soigneusement : la mission est sous les yeux de l'utilisateur.
    """
    if not (prefixe.isdigit() and prefixe.isascii()):
        return "nom sans préfixe de mission (import d'avant le 2026-09-01)"
    if mission is None:
        return f"mission n° {prefixe} supprimée"
    enregistre = _horodatage(mtime) or "avant"
    creee = _horodatage(_epoch_creation(mission)) or "plus tard"
    return (
        f"enregistré le {enregistre}, avant la création de la mission n° {prefixe} "
        f"({creee}) : il appartient à une mission n° {prefixe} plus ancienne, "
        "supprimée depuis — SQLite a réattribué le numéro"
    )


def lister_orphelins_globaux(recordings_dir: Path, db) -> dict:
    """Les fichiers audio que PLUS AUCUNE mission ne peut atteindre.

    C'est la réponse au constat C1 de la revue du 2026-09-01, mesuré sur
    l'installation réelle : 8 fichiers, 75,8 Mo, 24 % du répertoire, que
    l'onglet Backup d'aucune mission ne montrait. `lister_backups` exige un
    objet `Mission` et globbe `{mission.id}_*` — dès que la mission est
    supprimée, ou quand le nom ne porte aucun préfixe (convention d'avant le
    2026-09-01, `import_<ts>_<hex>`), plus rien ne les liste. Or depuis que
    l'audio ne se supprime que par une action de l'utilisateur, un fichier
    qu'aucun écran n'atteint est un fichier INDESTRUCTIBLE : la règle produit
    se retournait contre elle-même.

    Un fichier est repris ici s'il n'est ni référencé par un entretien
    existant, ni revendicable par une mission existante — c'est-à-dire s'il ne
    peut apparaître dans AUCUN onglet Backup. La garde chronologique de
    `_anterieur_a_la_mission` est appliquée à l'identique, sans quoi les deux
    écrans se contrediraient : un fichier caché à sa mission par la garde
    anti-réutilisation d'id serait absent des deux listes.

    Aucune écriture : comme le reste du module, on lit le disque et la base."""
    from ..models import AudioFileJob, Interview, Mission  # local : sans dépendance de schéma
    from .audio_file_jobs import is_audio_file_job_stale

    missions = list(db.scalars(select(Mission)))
    par_id = {m.id: m for m in missions}

    references: set[str] = set()
    for interview in db.scalars(select(Interview)):
        for segment in interview.audio_segments or []:
            if segment.get("filename"):
                references.add(segment["filename"])
        if interview.audio_backup_path:
            references.add(interview.audio_backup_path)
    # Les jobs de transcription EN COURS comptent comme des références (revue
    # adversariale du 2026-09-01, constat A5). Le chemin réel : on importe un
    # fichier dans un brouillon, `nettoyer_brouillons` supprime la mission — et
    # `_draft_vide` est vrai pour un entretien libre en cours, donc c'est un
    # chemin de tous les jours, pas un cas d'école. Le fichier devenait alors
    # orphelin global, avec son bouton « Supprimer », PENDANT que le job le
    # lisait bloc par bloc. Un job terminé, lui, ne retient rien : son fichier
    # redevient légitimement supprimable dès que plus aucun entretien ne le cite.
    # Et un job PÉRIMÉ non plus : mesuré le 2026-09-01 sur l'installation réelle,
    # un import de juillet resté à `running` retenait 18,4 Mo à lui seul. Sans
    # cette borne, la protection d'A5 refabriquait le fichier indestructible
    # que C1 venait de supprimer — le remède redevenait la maladie.
    for job in db.scalars(select(AudioFileJob)):
        if job.status in ("done", "failed") or is_audio_file_job_stale(job):
            continue
        if job.filename:
            references.add(job.filename)
        for nom in job.filenames or []:
            if nom:
                references.add(nom)

    orphelins = []
    if recordings_dir.is_dir():
        for chemin in sorted(recordings_dir.glob("*")):
            if not chemin.is_file() or chemin.name in references:
                continue
            # Écarte le non-média (constat B3) sans jamais écarter un média
            # inconnu (constat D1-B1) : cf. `est_media` et l'asymétrie des coûts
            # documentée à côté de `_EXTENSIONS_NON_MEDIA`.
            if not est_media(chemin.name):
                continue
            prefixe, _, _ = chemin.name.partition("_")
            # `isdigit()` est vrai pour les chiffres Unicode (« ² »), que `int()`
            # refuse : la ValueError remontait jusqu'à la liste des missions et
            # la rendait en 500 (constat B2). `str.isascii()` ferme l'écart.
            mission = (par_id.get(int(prefixe))
                       if prefixe.isdigit() and prefixe.isascii() else None)
            if mission is not None and not _anterieur_a_la_mission(chemin, mission):
                continue  # visible dans l'onglet Backup de cette mission
            taille, mtime = _stat(chemin)
            orphelins.append({
                "filename": chemin.name,
                "taille": taille,
                "mtime": mtime,
                "horodatage": _horodatage(mtime),
                "existe": True,
                # Pourquoi il est inatteignable — l'écran le dit, sinon la liste
                # ressemble à un fourre-tout sans logique. Les trois cas sont
                # DISTINCTS, et les confondre a produit un mensonge à l'écran
                # (mesuré le 2026-09-01 sur l'installation réelle : 4 fichiers
                # sur 12 annonçaient « mission n° 13 supprimée » alors que la
                # mission 13 figurait dans la liste des missions). Devant un
                # bouton de suppression, une raison fausse est pire que pas de
                # raison : elle fait douter de l'écran entier.
                "raison": _raison_orphelin(prefixe, mission, mtime),
                "recent": bool(mtime) and (time.time() - mtime) < ORPHELIN_RECENT_S,
            })

    orphelins.sort(key=_ordre_chronologique)
    return {
        "orphelins": orphelins,
        "taille_totale": sum(e["taille"] for e in orphelins),
    }
