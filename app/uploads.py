"""Bornes sur les fichiers reçus des points d'entrée utilisateur (finding
audit-technique securite:critique du 2026-09-04, `.claude/audits/VSCode2.json`
côté hub) : « Uploads sans plafond de taille et décompression zip non bornée
(zip-bomb) sur deux chemins non authentifiés — le contenu est lu entièrement en
RAM par `await file.read()` puis dépaqueté par python-docx et python-pptx AVANT
toute validation de taille ».

Trois gardes distinctes, parce que les dégâts sont distincts :

1. `lire_upload_borne` — plafonne ce qui entre en MÉMOIRE. `await file.read()`
   sans argument matérialise tout le corps de la requête d'un coup ; un envoi
   de plusieurs Go suffit à faire tomber le processus. On lit par morceaux et
   on s'arrête net au dépassement, sans jamais avoir alloué plus que le
   plafond. (Les chemins audio qui STREAMENT vers le disque par
   `shutil.copyfileobj` — import de fichier, sauvegarde d'enregistrement — ne
   passent pas ici : un entretien de 3 h est légitimement volumineux et ne
   doit pas être plafonné à la même aune. Les deux qui matérialisent en RAM,
   eux, y passent : cf. `lire_upload_audio_borne` et son plafond dédié.)

2. `verifier_zip_borne` — plafonne ce qui sortirait du DÉPAQUETAGE. `.docx` et
   `.pptx` sont des archives ZIP : quelques centaines de Ko compressés peuvent
   déclarer des gigaoctets décompressés (zip-bomb classique). python-docx et
   python-pptx dépaquettent sans rien vérifier. On lit le catalogue de
   l'archive (répertoire central, aucune décompression) et on refuse AVANT de
   passer les octets à la bibliothèque.

   Limite assumée et connue : le répertoire central est déclaratif, une archive
   peut mentir sur `file_size`. C'est la garde de première ligne standard —
   elle arrête le zip-bomb par amplification, pas une archive forgée pour
   mentir. Le plafond de l'étape 1 borne alors le mensonge à ce qui a pu
   physiquement entrer.

3. `_bornes_repertoire_central` — plafonne le NOMBRE d'entrées. Une archive
   peut n'afficher aucun ratio suspect, ne déclarer aucun octet décompressé,
   et coûter quand même : 200 000 entrées vides passaient les deux gardes
   ci-dessus (mesuré le 2026-09-09). Le compte se lit dans la fin
   d'archive, avant que `zipfile.ZipFile` ne construise son index — sinon
   la garde paie ce qu'elle évite.

   Contournement trouvé et fermé le 2026-09-09 (revue adversariale, F1) : le
   champ « nombre d'entrées » de la fin d'archive est déclaratif — CPython
   `zipfile._RealGetContents` ne le lit JAMAIS pour borner sa boucle, il lit
   les enregistrements du répertoire central un par un jusqu'à avoir
   consommé `size_cd` (la TAILLE du répertoire central, un champ voisin,
   4 octets). Falsifier les 4 octets du compte déclaré (sans toucher
   `size_cd`) faisait donc passer une archive à 20 000 entrées réelles pour
   une archive « à 1 entrée » — acceptée en 0,78 s, puis indexée en entier
   quand même. La borne porte donc sur `size_cd // 46` (un enregistrement de
   répertoire central fait au moins 46 octets), qui ne peut pas mentir à la
   baisse sans rendre l'archive illisible — le compte déclaré reste vérifié
   en plus, pour un message d'erreur plus clair sur une archive honnête.
"""
from __future__ import annotations

import io
import os
import struct
import zipfile

from fastapi import UploadFile


def _mo_env(nom: str, defaut: int) -> int:
    """Plafond en Mo lu dans l'environnement, repli sur `defaut`. Une valeur
    illisible ou absurde (<= 0) ne doit pas DÉSACTIVER la garde : on retombe
    sur le défaut plutôt que d'ouvrir la porte en silence."""
    try:
        valeur = int(os.environ.get(nom, ""))
    except ValueError:
        return defaut * 1024 * 1024
    if valeur <= 0:
        return defaut * 1024 * 1024
    return valeur * 1024 * 1024


def _entier_env(nom: str, defaut: int) -> int:
    """Même contrat que `_mo_env`, pour un plafond qui n'est pas une taille en
    octets (un compte d'entrées) — pas de facteur Mo."""
    try:
        valeur = int(os.environ.get(nom, ""))
    except ValueError:
        return defaut
    return valeur if valeur > 0 else defaut


# Document bureautique importé (.docx d'entretien ou de trame, .pptx de
# template client, .md d'analyse externe). 40 Mo laisse passer un template
# PowerPoint chargé en images tout en fermant l'envoi qui fait tomber le
# processus.
MAX_UPLOAD_BYTES = _mo_env("MAX_UPLOAD_MB", 40)

# Total décompressé déclaré par l'archive. Un .docx/.pptx réel reste très en
# dessous : les images qu'il transporte sont déjà compressées (ratio ~1), seul
# le XML se comprime fort.
MAX_ZIP_DECOMPRESSE_BYTES = _mo_env("MAX_ZIP_DECOMPRESSE_MB", 300)

# Rapport décompressé/compressé au-delà duquel l'archive n'est plus un document
# mais une amplification. Un .docx purement textuel monte vers 15-20 ; un
# zip-bomb dépasse 1000. 120 laisse une marge large au cas légitime.
RATIO_MAX = 120

# Nombre d'entrées de l'archive. Troisième forme de zip-bomb, invisible pour
# les deux plafonds ci-dessus parce qu'elle n'amène aucun octet à décompresser :
# 200 000 entrées VIDES tiennent dans 16,6 Mo envoyés et déclarent 0 octet
# décompressé (donc ratio 0), mais coûtent 6,94 s de CPU et 111 Mo de pic
# mémoire à la seule lecture du répertoire central (mesuré le 2026-09-09 sur le
# venv du projet). Calibré sur le réel : le plus gros document du dépôt,
# `data/pptx_templates/1.pptx` (template client), en compte 416 — 4000 laisse
# un facteur 10 au cas légitime. Surchargeable par l'environnement (comme les
# plafonds en octets ci-dessus) depuis le 2026-09-09 : un poste qui rencontre
# légitimement plus de 416 entrées n'avait sinon aucune issue sans modifier le
# code (revue adversariale, F9).
MAX_ZIP_ENTREES = _entier_env("MAX_ZIP_ENTREES", 4000)

# Audio matérialisé en RAM (finding audit-technique securite du 2026-09-09 :
# `/audio/transcribe-segment` et `/interviews/{id}/notes/transcribe` faisaient
# `await file.read()` nu). Plafond DISTINCT de MAX_UPLOAD_BYTES : un document
# bureautique de 40 Mo est déjà énorme, une minute de parole ne pèse rien, mais
# une dictée de notes libres n'a aucune rotation qui la borne côté navigateur.
#
# Calibré sur le réel plutôt que deviné (mesuré le 2026-09-09 sur les
# enregistrements du poste, `av.open` + `os.path.getsize`) : les sauvegardes
# produites par MediaRecorder pèsent 15,7 Ko/s (18,43 Mo pour 1200,0 s,
# 27,64 Mo pour 1799,9 s — deux fichiers, même débit). Donc :
#   - segment de rotation (`SEGMENT_MS = 60000` dans record.html) : ~0,92 Mo ;
#   - plus gros audio du poste (tranche de sauvegarde de 20 min) : 18,43 Mo ;
#   - 100 Mo ≈ 1 h 48 min de parole d'un seul tenant.
# Soit un facteur ~100 sur le cas nominal et ~5 sur le plus gros artefact réel,
# tout en fermant l'envoi de plusieurs Go qui fait tomber le processus. À
# retenir si le chiffre est retouché : le décodage alloue ENSUITE le PCM
# 16 kHz mono (~2 fois la taille du webm), le plafond n'est donc pas le pic.
MAX_AUDIO_UPLOAD_BYTES = _mo_env("MAX_AUDIO_UPLOAD_MB", 100)


_TAILLE_MORCEAU = 1024 * 1024


class UploadTropVolumineux(Exception):
    """Fichier refusé sur sa taille (ou sur son amplification au dépaquetage).

    Type dédié pour que les routes le distinguent d'un fichier CORROMPU : les
    deux se rendent par le même écran mais pas avec le même message, et
    confondre les deux enverrait l'utilisateur réparer un fichier sain.
    """


async def lire_upload_borne(
    file: UploadFile, max_bytes: int | None = None
) -> bytes:
    """Contenu complet du fichier, ou `UploadTropVolumineux` au dépassement.

    Remplace `await file.read()` : même valeur de retour sur le cas nominal,
    mais l'allocation ne dépasse jamais `max_bytes` + un morceau.

    Le plafond est lu À L'APPEL (sentinelle `None`), pas figé en valeur par
    défaut : une valeur par défaut est évaluée une seule fois à la définition
    du module, ce qui rendrait le réglage inopérant pour tout ce qui le change
    après l'import — à commencer par les tests qui l'abaissent.
    """
    if max_bytes is None:
        max_bytes = MAX_UPLOAD_BYTES
    morceaux: list[bytes] = []
    total = 0
    while True:
        morceau = await file.read(_TAILLE_MORCEAU)
        if not morceau:
            break
        total += len(morceau)
        if total > max_bytes:
            raise UploadTropVolumineux(
                f"Fichier trop volumineux : {max_bytes // (1024 * 1024)} Mo maximum."
            )
        morceaux.append(morceau)
    return b"".join(morceaux)


async def lire_upload_audio_borne(file: UploadFile) -> bytes:
    """Même garde que `lire_upload_borne`, au plafond AUDIO.

    Fonction dédiée plutôt qu'un `lire_upload_borne(file, MAX_AUDIO_UPLOAD_BYTES)`
    écrit dans le routeur : le plafond doit être lu À L'APPEL et DEPUIS CE
    MODULE (même raison que la sentinelle `None` ci-dessus). Un
    `from ..uploads import MAX_AUDIO_UPLOAD_BYTES` côté routeur figerait la
    valeur à l'import et rendrait le réglage — et les tests qui l'abaissent —
    inopérants.
    """
    return await lire_upload_borne(file, MAX_AUDIO_UPLOAD_BYTES)


# Signature de fin d'archive 32 bits (« end of central directory »). La lire
# nous-mêmes est le seul moyen de connaître le nombre d'entrées SANS le
# payer : `zipfile.ZipFile(...)` construit tout l'index dans son
# constructeur, donc un refus prononcé après lui aurait déjà brûlé le CPU et
# la mémoire qu'il prétend épargner. Les constantes zip64
# (`zipfile.stringEndArchive64*`, `zipfile.structEndArchive64*`) sont prises
# directement au module `zipfile` plutôt que redéfinies ici : si CPython
# change un jour ce format, cette garde suit sans qu'on ait à y repenser.
_SIG_FIN = b"PK\x05\x06"
# 22 octets d'enregistrement + le commentaire d'archive, borné à 65 535 par le
# format : au-delà, la fin d'archive n'est pas là. Fenêtre de RECHERCHE
# seulement (une optimisation pour éviter un `rfind` sur tout le fichier) —
# une fois la position trouvée, tout le reste travaille en offsets ABSOLUS
# dans `content`, y compris pour remonter à un enregistrement zip64 qui peut
# se trouver bien plus loin en arrière dans un gros fichier.
_ZONE_FIN = 22 + 65535
# Taille minimale d'un enregistrement de répertoire central (partie fixe,
# 46 octets — nom/extra/commentaire peuvent être vides) : c'est ce plancher,
# pas le compte déclaré, qui borne le nombre d'entrées que `zipfile` peut
# RÉELLEMENT indexer pour une taille de répertoire central donnée.
_TAILLE_MIN_ENREGISTREMENT_CD = 46


def _bornes_repertoire_central(content: bytes) -> tuple[int | None, int | None]:
    """`(entrees_declarees, taille_repertoire_central)` — le PLUS GRAND des
    deux jeux de valeurs que l'archive porte (fin d'archive 32 bits et,
    quand présente, fin d'archive zip64), ou `(None, None)` si la fin
    d'archive 32 bits est introuvable (fichier corrompu — laissé à
    `zipfile`, qui lèvera son `BadZipFile` habituel, cas déjà traité par les
    routes).

    Les champs sont déclaratifs — une archive peut mentir. Mais PAS de la
    même façon : `entrees_declarees` (le compte) n'est JAMAIS lu par
    `zipfile._RealGetContents`, qui boucle uniquement sur
    `taille_repertoire_central` (« tant que je n'ai pas consommé size_cd
    octets ») — le compte peut donc mentir à la baisse sans que l'archive
    devienne illisible (trouvé le 2026-09-09 : 4 octets falsifiés, une
    archive à 20 000 entrées réelles se présentant comme n'en ayant qu'une,
    indexée quand même). `taille_repertoire_central`, elle, ne peut PAS
    mentir à la baisse sans rendre l'archive illisible — c'est elle qui borne
    le coût réel.

    Le zip64 est consulté INCONDITIONNELLEMENT dès qu'un locator valide
    précède la fin d'archive 32 bits — PAS seulement sur sentinelle
    (`0xFFFF`/`0xFFFFFFFF`) dans les champs 32 bits. Une première version de
    cette garde ne le faisait que sur sentinelle et rouvrait entièrement le
    contournement qu'elle existe pour fermer (revue adversariale du
    2026-09-09, second passage) : `zipfile._EndRecData64` — la fonction que
    `ZipFile()` appelle réellement — suit le locator dès qu'il est présent,
    sans regarder si l'enregistrement 32 bits affiche des sentinelles ; une
    archive peut donc porter un locator+enregistrement zip64 VALIDES pointant
    vers le vrai (gros) répertoire central, tout en gardant un enregistrement
    32 bits qui ment SANS sentinelle (compte=1, taille=46) — reproduit :
    `zipfile` lit alors 20 000 entrées réelles, cette garde n'en voyait qu'1
    si elle ne regardait que les champs 32 bits. Le plus grand des deux jeux
    de valeurs ferme les deux formes de mensonge à la fois.
    """
    position_abs = content.rfind(_SIG_FIN, max(0, len(content) - _ZONE_FIN))
    if position_abs < 0 or position_abs + 16 > len(content):
        return None, None
    (entrees,) = struct.unpack_from("<H", content, position_abs + 10)
    (taille_cd,) = struct.unpack_from("<I", content, position_abs + 12)

    pos_locator = position_abs - zipfile.sizeEndCentDir64Locator
    if 0 <= pos_locator and pos_locator + zipfile.sizeEndCentDir64Locator <= len(content):
        sig_loc, diskno, reloff, disks = struct.unpack_from(
            zipfile.structEndArchive64Locator, content, pos_locator
        )
        if (
            sig_loc == zipfile.stringEndArchive64Locator
            and diskno == 0 and disks <= 1
            and 0 <= reloff <= pos_locator - zipfile.sizeEndCentDir64
        ):
            (sig64,) = struct.unpack_from("<4s", content, reloff)
            if sig64 == zipfile.stringEndArchive64:
                rec = struct.unpack_from(zipfile.structEndArchive64, content, reloff)
                # structEndArchive64 = '<4sQ2H2L4Q' : sig, sz, create_version,
                # read_version, disk_num, disk_dir, dircount, dircount2
                # (total, tous disques), dirsize, diroffset.
                _, _, _, _, _, _, _dircount, dircount2, dirsize, _diroffset = rec
                entrees = max(entrees, dircount2)
                taille_cd = max(taille_cd, dirsize)
    return entrees, taille_cd


def verifier_zip_borne(
    content: bytes,
    max_decompresse: int | None = None,
    ratio_max: int | None = None,
    max_entrees: int | None = None,
) -> None:
    """Refuse une archive (.docx/.pptx) dont le dépaquetage exploserait.

    Ne décompresse RIEN : lecture du répertoire central seul. Laisse remonter
    `zipfile.BadZipFile` sur un fichier corrompu — c'est le cas « invalide ou
    corrompu » que les routes traitent déjà, pas un cas de taille.

    Plafonds lus à l'appel, même raison que `lire_upload_borne`.
    """
    if max_decompresse is None:
        max_decompresse = MAX_ZIP_DECOMPRESSE_BYTES
    if ratio_max is None:
        ratio_max = RATIO_MAX
    if max_entrees is None:
        max_entrees = MAX_ZIP_ENTREES
    entrees, taille_cd = _bornes_repertoire_central(content)
    if taille_cd is not None:
        # Le plafond qui protège réellement : un MAJORANT de ce que `zipfile`
        # pourrait indexer, dérivé de la taille du répertoire central — pas
        # le compte déclaré, qui peut mentir à la baisse (cf. docstring de
        # `_bornes_repertoire_central`, F1 de la revue du 2026-09-09). C'est
        # un plafond, pas un plancher : `size_cd // 46` majore le nombre
        # d'entrées possibles (chaque enregistrement fait AU MOINS 46 octets),
        # il ne garantit pas qu'il y en ait exactement autant.
        entrees_indexables = taille_cd // _TAILLE_MIN_ENREGISTREMENT_CD
        if entrees_indexables > max_entrees:
            raise UploadTropVolumineux(
                f"Fichier refusé : l'archive peut indexer jusqu'à "
                f"{entrees_indexables} entrées ({max_entrees} au maximum)."
            )
    if entrees is not None and entrees > max_entrees:
        # Message plus précis sur une archive HONNÊTE (compte déclaré fiable
        # et supérieur au plafond) — le contrôle qui protège reste celui
        # ci-dessus, appliqué en premier.
        raise UploadTropVolumineux(
            f"Fichier refusé : l'archive déclare {entrees} entrées "
            f"({max_entrees} au maximum)."
        )
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        total = sum(info.file_size for info in archive.infolist())
    if total > max_decompresse:
        raise UploadTropVolumineux(
            "Fichier refusé : son contenu décompressé dépasse "
            f"{max_decompresse // (1024 * 1024)} Mo."
        )
    if content and total // max(1, len(content)) > ratio_max:
        raise UploadTropVolumineux(
            "Fichier refusé : taux de compression anormal (archive suspecte)."
        )


class EcritureAudioTropVolumineuse(UploadTropVolumineux):
    """Le flux audio a dépassé le plafond PENDANT l'écriture disque."""


def ecrire_audio_borne(fichier, destination, taille_bloc: int = 1024 * 1024) -> int:
    """Copie `fichier` vers `destination` en s'arrêtant AU PLAFOND audio, et
    rend le nombre d'octets écrits.

    Troisième et dernière jambe du constat sécurité du 2026-09-04. Les deux
    routes qui écrivent l'audio en streaming sur DISQUE
    (`transcribe_file` et `save_record_backup`) n'avaient aucun plafond, alors
    que les chemins qui lisent en MÉMOIRE en ont un depuis le 2026-09-09 : un
    envoi de plusieurs Go y remplissait le disque. Ces deux routes ne sont pas
    authentifiées — l'atténuation tient au binding 127.0.0.1 et à la garde
    CSRF, pas à une borne (constat d'audit `audio-streaming-disque-sans-plafond`,
    arbitré « traiter » le 2026-09-10).

    Le fichier PARTIEL est supprimé au dépassement : le laisser serait un défaut
    à lui seul — il occuperait le disque qu'on protège, et
    `lister_orphelins_globaux` le proposerait à la suppression comme s'il
    s'agissait d'un enregistrement légitime.

    Le plafond est lu À L'APPEL et DEPUIS CE MODULE, même raison que
    `lire_upload_audio_borne` : l'importer côté routeur figerait la valeur et
    rendrait les tests qui l'abaissent inopérants.
    """
    ecrits = 0
    plafond = MAX_AUDIO_UPLOAD_BYTES
    try:
        with open(destination, "wb") as sortie:
            while True:
                bloc = fichier.read(taille_bloc)
                if not bloc:
                    break
                ecrits += len(bloc)
                if ecrits > plafond:
                    raise EcritureAudioTropVolumineuse(
                        f"Fichier audio trop volumineux (plafond "
                        f"{plafond // (1024 * 1024)} Mo)."
                    )
                sortie.write(bloc)
    except BaseException:
        # Y COMPRIS sur une interruption : un fichier partiel ne doit jamais
        # survivre à l'écriture qui l'a abandonné.
        try:
            os.unlink(destination)
        except OSError:
            pass
        raise
    return ecrits
