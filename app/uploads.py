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
   plafond. (Les chemins AUDIO ne passent pas ici : ils streament déjà vers le
   disque par `shutil.copyfileobj`, cf. `interviews.py` — un entretien de 3 h
   est légitimement volumineux et ne doit pas être plafonné à la même aune.)

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

3. `_entrees_declarees` — plafonne le NOMBRE d'entrées. Une archive peut
   n'afficher aucun ratio suspect, ne déclarer aucun octet décompressé, et
   coûter quand même : 200 000 entrées vides passaient les deux gardes
   ci-dessus (mesuré le 2026-09-09). Le compte se lit dans la fin
   d'archive, avant que `zipfile.ZipFile` ne construise son index — sinon
   la garde paie ce qu'elle évite.
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
# un facteur 10 au cas légitime.
MAX_ZIP_ENTREES = 4000


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


# Signatures de fin d'archive (« end of central directory »). Les lire nous-mêmes
# est le seul moyen de connaître le nombre d'entrées SANS le payer :
# `zipfile.ZipFile(...)` construit tout l'index dans son constructeur, donc un
# refus prononcé après lui aurait déjà brûlé le CPU et la mémoire qu'il
# prétend épargner.
_SIG_FIN = b"PK\x05\x06"
_SIG_FIN64 = b"PK\x06\x06"
# 22 octets d'enregistrement + le commentaire d'archive, borné à 65 535 par le
# format : au-delà, la fin d'archive n'est pas là.
_ZONE_FIN = 22 + 65535


def _entrees_declarees(content: bytes) -> int | None:
    """Nombre d'entrées annoncé par la fin d'archive, ou `None` si elle est
    introuvable (fichier corrompu — laissé à `zipfile`, qui lèvera son
    `BadZipFile` habituel, cas déjà traité par les routes).

    Déclaratif comme le reste du répertoire central, même limite assumée que
    ci-dessus : une archive peut mentir. Elle ne peut en revanche pas mentir
    à la BAISSE sans se rendre illisible, et c'est le sens du contrôle.
    """
    queue = content[-_ZONE_FIN:] if len(content) > _ZONE_FIN else content
    position = queue.rfind(_SIG_FIN)
    if position < 0 or position + 12 > len(queue):
        return None
    (entrees,) = struct.unpack_from("<H", queue, position + 10)
    if entrees != 0xFFFF:
        return entrees
    # Sentinelle zip64 : le vrai compte est dans l'enregistrement étendu qui
    # précède. Absent ou tronqué, on garde 0xFFFF — c'est un plancher (au moins
    # 65 535 entrées), donc déjà au-dessus du plafond : pas d'échappatoire.
    position64 = queue.rfind(_SIG_FIN64, 0, position)
    if position64 >= 0 and position64 + 40 <= len(queue):
        (entrees,) = struct.unpack_from("<Q", queue, position64 + 32)
    return entrees


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
    entrees = _entrees_declarees(content)
    if entrees is not None and entrees > max_entrees:
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
