"""Transcription locale d'un enregistrement audio (US3.2) via faster-whisper.

Modèle chargé une fois (singleton lazy), sans quoi chaque transcription
rechargerait les poids depuis le disque. Aucun appel réseau pour la
transcription elle-même : la voix des personnes interviewées reste sur la
machine de l'interviewer·euse. Même convention de dégradation gracieuse que
`ai_common.py` (`is_configured()`/`_openai()`) : `is_available()` renvoie
False si `faster-whisper` n'est pas installé, et `transcribe_audio()` lève
`TranscriptionError` avec un message destiné à l'UI.

Compromis vitesse/qualité (WHISPER_MODEL/WHISPER_BEAM_SIZE, 2026-07-15) :
la transcription se fait au fil de l'eau par segments d'~1 min pendant
l'enregistrement (voir record.html), donc chaque segment doit rester
sensiblement plus rapide à transcrire qu'à enregistrer. Le réglage d'origine
(model="small", beam_size=1/greedy) privilégiait la vitesse CPU au prix
d'erreurs de transcription notables sur un entretien réel (accents,
vocabulaire métier, recouvrements de parole). Défaut relevé à
model="medium" + beam_size=2 : nette amélioration de qualité, encore
raisonnable au fil de l'eau sur un CPU correct — à ajuster via les variables
d'environnement selon la machine (WHISPER_MODEL=large-v3 pour la meilleure
qualité possible en local, si le CPU suit ; WHISPER_MODEL=small pour
revenir à l'ancien compromis si medium est trop lent).

Transcription parallèle d'un fichier long (US9.18, 2026-07-16) : un
entretien pré-enregistré de 1h30-3h ne peut pas passer par le chemin
séquentiel ci-dessus dans un temps raisonnable (mesuré ~0,84-0,88x la durée
réelle en RTF sur du contenu réel, CPU seul, soit ~80-160 min de calcul) —
voir le cadrage `_bmad-output/cadrage-transcription-perf.md`. Au-delà de
`PARALLEL_THRESHOLD_S`, `transcribe_audio()` découpe l'audio en `n_workers`
tronçons de taille ÉGALE (`len(pcm) // n_workers`, chevauchant légèrement
leurs voisins — cf. `OVERLAP_S` — pour ne pas couper un mot en deux) et les
transcrit en parallèle sur plusieurs cœurs CPU (`ProcessPoolExecutor` —
chaque processus charge son propre modèle, pas de partage possible entre
processus), avant de recoller les textes dans l'ordre en dédupliquant les
recouvrements (`_merge_overlapping_texts`). `duration_s // 30` ne sert qu'à
BORNER `n_workers` (voir `MAX_PARALLEL_WORKERS`), pas à fixer une taille de
tronçon fixe de 30s : sur un fichier de 3h avec 8 workers, chaque tronçon
fait ~22 minutes, pas 30s. Mesuré ~1,8x plus rapide que le séquentiel sur un extrait réel de
5 min (RTF 0,84 → 0,45-0,49 selon le nombre de workers, 2026-07-16) — un
entretien de 1h30 tient alors tout juste dans un budget de 45 min avec le
reste du pipeline (traitement IA inclus) ; un entretien de 3h reste
au-delà (~94 min mesuré/extrapolé), plafond matériel de ce poste (CPU
seul, pas de GPU dédié) plutôt qu'un réglage logiciel manquant. En dessous
du seuil (cas du direct au fil de l'eau, segments ~1 min), le chemin
séquentiel reste inchangé : démarrer des sous-processus coûterait plus cher
que le gain sur un si petit segment.

Repli quand le pool casse (2026-07-29) : un worker tué brutalement (« A child
process terminated abruptly, the process pool is not usable anymore »)
faisait échouer TOUT l'import — l'utilisateur ne récupérait rien, pas même
les blocs déjà transcrits, alors que la cause est de la pression mémoire, pas
un fichier invalide (chaque worker charge son propre modèle : ~1,5 Go en
`medium` int8, soit ~12 Go à 8 workers, sur une machine où Ollama garde en
plus le sien chargé). La transcription dégrade désormais son parallélisme au
lieu d'abandonner : palier à `MAX_PARALLEL_WORKERS`, puis à la moitié
(`_paliers_workers`), puis séquentiel dans le processus courant — en
reprenant au bloc où le pool est mort, sans jamais re-transcrire ni perdre
ce qui a déjà été rendu. Baisser `WHISPER_MAX_WORKERS` évite d'en arriver là
sur une machine à mémoire serrée.
"""
from __future__ import annotations

import io
import logging
import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from .ai_common import AIError

logger = logging.getLogger(__name__)


def _entier_env(nom: str, defaut: int) -> int:
    """Entier positif lu dans l'environnement, sinon le défaut.

    Jumeau de `app.uploads._entier_env` / `app.services.pptx_export.images._entier_env`,
    volontairement recopié plutôt qu'importé (même raison : ce module doit
    rester chargeable sans dépendre d'un autre package applicatif). Existe
    parce qu'un `int(os.environ[...])` nu exécuté au niveau module plantait
    l'import ENTIER sur une valeur non entière ou absente — un déploiement
    avec une faute de frappe sur `WHISPER_BEAM_SIZE` par ex. rendait toute
    l'appli indisponible au lieu de retomber sur le réglage documenté (finding
    audit-technique robustesse, 2026-09-16)."""
    try:
        valeur = int(os.environ.get(nom, ""))
    except ValueError:
        return defaut
    return valeur if valeur > 0 else defaut


# Message rendu au CLIENT quand la transcription échoue. Constante parce qu'il
# part depuis 4 endroits et qu'il doit rester identique : c'est un texte
# d'interface, pas un diagnostic. Le diagnostic, lui, est journalisé à chacun
# de ces 4 endroits.
ECHEC_TRANSCRIPTION = "Échec de la transcription de l'audio."

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "medium")
BEAM_SIZE = _entier_env("WHISPER_BEAM_SIZE", 2)

# Durée (secondes) d'un BLOC de transcription d'un fichier audio importé
# (`iter_transcribe_blocks`). Un fichier pré-enregistré n'a pas de rotation
# micro : c'est le serveur qui rejoue la granularité « au fil de l'eau » du
# direct, pour que le texte s'affiche bloc par bloc et que l'extraction IA
# (tours de parole / répartition Q/R) démarre sans attendre la fin du fichier.
#
# 60 s = exactement la rotation du direct (SEGMENT_MS de record*.html), et non
# la cadence des jobs d'extraction (5 min) : un bloc est transcrit par UN
# worker, à un RTF par worker d'environ 3,6 (mesures du 2026-07-16 : RTF
# agrégé 0,45-0,49 sur 8 workers) — un bloc de 5 min ne s'afficherait donc
# qu'au bout de ~18 min, ce qui n'a plus rien de « au fil de l'eau ». À 60 s,
# la première vague de texte tombe en ~3-4 min et le regroupement en tranches
# d'extraction est fait côté client, comme pour le micro.
FILE_BLOCK_S = _entier_env("WHISPER_FILE_BLOCK_S", 60)

# Au-delà de cette durée (secondes), transcrire en parallèle plutôt qu'en un
# seul appel séquentiel — voir docstring du module.
PARALLEL_THRESHOLD_S = _entier_env("WHISPER_PARALLEL_THRESHOLD_S", 90)
# Nombre max de workers parallèles — au-delà de ~8 sur ce type de CPU
# (10 cœurs physiques), le gain mesuré devient marginal (rendements
# décroissants, cf. cadrage perf).
MAX_PARALLEL_WORKERS = _entier_env("WHISPER_MAX_WORKERS", min(8, os.cpu_count() or 4))
# Threads CPU par worker parallèle — mesuré : 1 thread/worker avec
# MAX_PARALLEL_WORKERS workers simultanés bat un seul worker à plusieurs
# threads, sur ce CPU hybride P/E-cores (contention mémoire au-delà d'un
# certain nombre de threads par processus).
CPU_THREADS_PER_WORKER = _entier_env("WHISPER_CPU_THREADS", 1)

# Marge de recouvrement (secondes) entre tronçons VOISINS de
# `_transcribe_parallel`, de part et d'autre de chaque frontière de découpage
# interne (pas aux deux bords du fichier). Un slicing contigu sans marge
# coupe un mot ou une phrase pile sur la frontière calculée : la moitié
# atterrit dans un tronçon, l'autre moitié dans le suivant, et chacun la
# transcrit (ou non) indépendamment — mot dupliqué ou perdu selon les VAD de
# chaque côté. 2s suffit à couvrir un mot/une courte phrase à cheval sans
# gonfler notablement le volume total transcrit (~n_workers-1 fois 2×2s sur
# tout l'audio). Voir `_merge_overlapping_texts` pour la déduplication au
# recollement.
OVERLAP_S = float(os.environ.get("WHISPER_PARALLEL_OVERLAP_S", "2"))

# Plafond GLOBAL de workers de transcription actifs simultanément, tous
# appels confondus (chemin parallèle ET séquentiel) — chaque worker parallèle
# charge son propre modèle Whisper (~1,5 Go en `medium` int8, cf. « Repli
# quand le pool casse » dans la docstring du module) : `MAX_PARALLEL_WORKERS`
# ne borne qu'UN appel, pas la somme de plusieurs transcriptions parallèles
# concurrentes (plusieurs imports de fichiers longs lancés en même temps),
# qui pouvait donc dépasser la mémoire disponible sans qu'aucun garde-fou ne
# s'en aperçoive avant coup — `_paliers_workers` ne réagit qu'APRÈS un
# `BrokenProcessPool`, pas avant, et un `MemoryError` levé DANS un worker
# (au lieu de le tuer proprement) y échappe entièrement. Le chemin séquentiel
# a lui `_MODEL_LOCK` (un seul modèle en mémoire pour toute l'appli) ; ce
# sémaphore lui donne l'équivalent côté parallèle.
GLOBAL_MAX_PARALLEL_WORKERS = int(
    os.environ.get("WHISPER_GLOBAL_MAX_WORKERS", str(MAX_PARALLEL_WORKERS))
)
_GLOBAL_WORKERS_SEMAPHORE = threading.Semaphore(max(1, GLOBAL_MAX_PARALLEL_WORKERS))

# Temps (secondes) qu'une transcription synchrone (direct : segment de
# record.html, ou dictée de notes libres) attend `_MODEL_LOCK` avant de
# recevoir un refus "occupé" plutôt que de bloquer le thread indéfiniment
# (atelier-dev 2026-09-15). Calé sur `SEGMENT_MS` (record.html, 60s) et non
# sur une valeur courte arbitraire : le module documente déjà l'invariant visé
# (« chaque segment doit rester sensiblement plus rapide à transcrire qu'à
# l'enregistrer ») — tant qu'il tient, au plus UN segment attend le
# précédent, et cette attente reste sous ~60s. Un timeout plus court (ex. 8s)
# déclencherait le refus "occupé" sur ce cas NORMAL (un seul onglet, une seule
# transcription en avance) : côté client, `uploadSegment()` n'a que 2 relances
# à délai court (`SEGMENT_RETRY_DELAYS_MS`, ~8s cumulées) avant d'abandonner
# le segment — bien en-deçà des 900s de patience qu'il accordait avant ce
# correctif (`TRANSCRIBE_TIMEOUT_MS`, le fetch attendait le déblocage du
# verrou sans jamais échouer). Un timeout serveur trop court aurait donc fait
# perdre des segments là où l'ancien blocage silencieux finissait par réussir
# (revue adversariale du lot 1, 2026-09-15) — 60s ne se déclenche que quand
# l'invariant ci-dessus est déjà rompu (CPU saturé, plusieurs onglets/
# entretiens concurrents), le cas que ce correctif vise réellement.
#
# S'applique aux DEUX appelants de `transcribe_audio()` : la route segment
# (`interviews_audio.py`) et la dictée de notes libres (`interviews.py`,
# `/interviews/{id}/notes/transcribe`) — même verrou, même contrat de refus.
# L'import de fichier (`iter_transcribe_blocks`) reste sur l'attente
# bloquante existante, qui a déjà son propre mécanisme de reprise en tâche de
# fond (reprise au bloc fautif, cf. docstring du module).
SEGMENT_LOCK_TIMEOUT_S = float(os.environ.get("WHISPER_SEGMENT_LOCK_TIMEOUT_S", "60"))

_model = None
# Le singleton est partagé par plusieurs threads : `/audio/transcribe-segment`
# (un `to_thread` par requête, plusieurs onglets possibles) et, depuis
# l'import de fichier bloc par bloc, la tâche de fond `run_audio_file_job`.
# Le chemin parallèle isole les modèles dans des PROCESSUS séparés justement
# parce qu'un modèle ne se partage pas ; ce verrou tient la même garantie pour
# le chemin séquentiel (revue adversariale 2026-07-27) — il sérialise deux
# transcriptions concurrentes au lieu de les laisser se marcher dessus.
_MODEL_LOCK = threading.Lock()


class TranscriptionError(AIError):
    """Erreur fonctionnelle de transcription — le message est destiné à l'UI."""


class NoSpeechError(TranscriptionError):
    """Aucune parole détectée dans l'audio — cas distinct d'un échec technique :
    l'audio s'est décodé correctement mais le VAD n'y trouve pas de voix.
    `/audio/transcribe-segment` le signale par `code: "no_speech"` pour que
    l'écran d'enregistrement puisse alerter sur la SOURCE audio (constaté en
    réel le 2026-07-30, mission 16 : ~100 min d'un entretien Google Meet
    capturées en quasi-silence — le micro physique n'entend pas le son du
    casque — sans aucune alerte visible, 90 segments perdus en silence)."""


class TranscriptionBusyError(TranscriptionError):
    """Le modèle Whisper est occupé par une autre transcription (verrou
    `_MODEL_LOCK`) depuis plus de `SEGMENT_LOCK_TIMEOUT_S` — distinct d'un
    échec : le segment n'a pas été tenté, il doit être REJOUÉ. `retry_after_s`
    porte le délai suggéré, retranscrit par la route en en-tête `Retry-After`
    (`code: "busy"`, HTTP 503) pour que le client sache attendre plutôt que
    deviner via un timeout aveugle."""

    retry_after_s: float = 0.0


def _faster_whisper():
    try:
        import faster_whisper

        return faster_whisper
    except ModuleNotFoundError:
        return None


def is_available() -> bool:
    """Vrai si la transcription locale est possible (paquet installé)."""
    return _faster_whisper() is not None


def _get_model():
    global _model
    if _model is None:
        faster_whisper = _faster_whisper()
        _model = faster_whisper.WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def warm_up() -> None:
    """Charge le modèle en mémoire dès le démarrage du serveur, pour que le
    premier enregistrement réel de l'utilisateur n'en paie pas le coût.

    `WHISPER_WARM_UP=0` le désactive : le serveur des tests navigateur
    (`tests/test_e2e_premiers_clics.py`) n'enregistre rien, et charger
    `medium` (≈1,5 Go, téléchargé s'il manque) à chaque démarrage de test
    aurait coûté le parcours en CI — le modèle se chargera au premier besoin
    réel, comme avant l'existence du warm-up."""
    if os.environ.get("WHISPER_WARM_UP", "1") == "0":
        return
    if is_available():
        _get_model()


def _probe_duration_s(content: bytes) -> float | None:
    """Sonde la durée du flux via les métadonnées du conteneur, sans décoder
    les échantillons audio (rapide). Retourne `None` si indéterminable (flux
    invalide/inattendu) — dans ce cas `transcribe_audio` retombe sur le
    chemin séquentiel existant, qui gère déjà ce genre d'entrée via son
    propre garde-fou, plutôt que de risquer un plantage ici sur un flux
    qu'on ne sait pas sonder."""
    try:
        import av

        container = av.open(io.BytesIO(content))
        duration = container.duration
        container.close()
        return (duration / 1_000_000) if duration else None
    except Exception:
        logger.debug("Durée audio indéterminable, repli séquentiel", exc_info=True)
        return None


def _exiger_piste_audio(content: bytes) -> None:
    """Lève un message lisible si le contenu N'A AUCUNE piste audio.

    Atteignable depuis 2026-07-31 : l'import accepte désormais les vidéos (le
    `.mp4` produit par Meet/Teams). Sans cette garde, une vidéo muette remonte à
    l'utilisateur en « tuple index out of range » — les DEUX décodeurs font
    `streams.audio[0]` : le nôtre (`_decode_to_pcm16k`) et celui de
    faster-whisper, sur le chemin court de `transcribe_audio`.

    Volontairement tolérante : elle ne se prononce QUE si le conteneur s'ouvre
    et ne contient aucune piste audio. Un fichier illisible pour d'autres raisons
    garde le message d'erreur du décodeur, qui en dit plus."""
    try:
        import av

        container = av.open(io.BytesIO(content))
    except Exception:
        logger.debug("Conteneur illisible, contrôle de piste audio ignoré", exc_info=True)
        return
    try:
        sans_audio = not container.streams.audio
    finally:
        container.close()
    if sans_audio:
        raise TranscriptionError(
            "Ce fichier ne contient aucune piste audio (vidéo muette ?) — "
            "vérifie l'enregistrement exporté depuis Meet/Teams."
        )


def _decode_to_pcm16k(content: bytes):
    """Décode le contenu audio en PCM mono 16kHz (format attendu par
    Whisper) — pré-décodage explicite plutôt que de laisser faster-whisper
    redécoder en interne un flux d'octets bruts à chaque tronçon : mesuré
    ~40 % plus rapide en pratique (2026-07-16), et de toute façon
    nécessaire ici pour découper l'audio en tronçons indépendants."""
    import av
    import numpy as np

    container = av.open(io.BytesIO(content))
    if not container.streams.audio:
        container.close()
        _exiger_piste_audio(content)   # message unique, défini au même endroit
        # `_exiger_piste_audio` est volontairement tolérante (elle ne lève QUE
        # si elle a pu ré-ouvrir le contenu et confirmer l'absence de piste —
        # cf. sa docstring) : si elle rend la main sans lever, ne PAS retomber
        # sur `streams.audio[0]` sur un conteneur déjà fermé (`ValueError`
        # cryptique) — contrat explicite plutôt qu'implicite (revue
        # adversariale 2026-07-31).
        raise TranscriptionError(
            "Ce fichier ne contient aucune piste audio (vidéo muette ?) — "
            "vérifie l'enregistrement exporté depuis Meet/Teams."
        )
    stream = container.streams.audio[0]
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    frames = []
    for frame in container.decode(stream):
        for rframe in resampler.resample(frame):
            frames.append(rframe.to_ndarray())
    container.close()
    if not frames:
        return np.array([], dtype=np.float32)
    pcm = np.concatenate(frames, axis=1).flatten().astype(np.float32) / 32768.0
    return pcm


def _transcribe_pcm_chunk(args: tuple) -> str:
    """Transcrit un tronçon PCM déjà décodé — fonction de niveau module
    (requis par `ProcessPoolExecutor` sous Windows, qui doit pouvoir la
    pickler). Chaque processus charge son propre modèle : aucun partage
    possible entre processus séparés, contrairement au singleton `_model`
    du chemin séquentiel."""
    pcm, threads = args
    faster_whisper = _faster_whisper()
    model = faster_whisper.WhisperModel(
        MODEL_SIZE, device="cpu", compute_type="int8", cpu_threads=threads
    )
    segments, _info = model.transcribe(
        pcm, language="fr", beam_size=BEAM_SIZE, vad_filter=True
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


def _transcribe_parallel(content: bytes, duration_s: float) -> str:
    """Découpe l'audio en `n_workers` tronçons de taille égale (`len(pcm) //
    n_workers`, PAS des tronçons fixes de 30s : `duration_s // 30` ne sert
    qu'à BORNER `n_workers` ci-dessous, cf. `MAX_PARALLEL_WORKERS` — sur un
    fichier de 3h avec 8 workers, chaque tronçon fait ~22 min, pas 30s),
    les transcrit en parallèle sur plusieurs cœurs CPU, puis recolle les
    textes dans l'ordre (reduce) — voir docstring du module pour la mesure
    de gain.

    Tronçons voisins se chevauchent de `OVERLAP_S` de part et d'autre de la
    frontière calculée (sauf aux deux bords du fichier) : un slicing contigu
    sans marge coupait un mot ou une phrase pile sur la frontière, le
    dupliquant à moitié dans un tronçon et le perdant dans l'autre. Avec la
    marge, le mot à cheval est entendu EN ENTIER des deux côtés ;
    `_merge_overlapping_texts` déduplique ensuite la redite au recollement
    plutôt qu'un `' '.join` naïf qui la laisserait telle quelle.

    Repli séquentiel si le pool de processus casse (cf. `_pool_casse`)."""
    pcm = _decode_to_pcm16k(content)
    if pcm.size == 0:
        return ""

    n_workers = max(1, min(MAX_PARALLEL_WORKERS, int(duration_s // 30) or 1))
    chunk_len = len(pcm) // n_workers
    overlap = int(OVERLAP_S * 16000)
    chunks = []
    for i in range(n_workers):
        start = 0 if i == 0 else i * chunk_len - overlap
        end = len(pcm) if i == n_workers - 1 else (i + 1) * chunk_len + overlap
        chunks.append(pcm[max(0, start):min(len(pcm), end)])
    # Même mécanique de reprise que `iter_transcribe_blocks` (revue
    # adversariale 2026-07-29) : l'ancien repli `except BrokenProcessPool:
    # return _transcribe_pcm_sequential(pcm)` jetait les tronçons DÉJÀ
    # transcrits par le pool et recommençait TOUT l'audio en séquentiel —
    # précisément sur les fichiers longs que ce chemin cible. On reprend au
    # tronçon fautif, par paliers de workers, puis en séquentiel tronçon par
    # tronçon en dernier recours.
    total = len(chunks)
    parts: dict[int, str] = {}
    index = 0
    for workers in _paliers_workers(n_workers):
        try:
            for i, text in _drain_parallel(chunks, index, total, workers):
                parts[i] = text
                index = i + 1
            break
        except BrokenProcessPool:
            logger.warning(
                "Pool de transcription cassé au tronçon %d/%d (%d workers, %.0fs d'audio) — palier suivant.",
                index + 1,
                total,
                workers,
                duration_s,
            )
    while index < total:
        parts[index] = _transcribe_pcm_sequential(chunks[index])
        index += 1
    return _merge_overlapping_texts([parts[i] for i in range(total)])


def _transcribe_pcm_sequential(pcm) -> str:
    """Transcrit un PCM déjà décodé avec le modèle singleton du processus
    courant (pas de sous-processus) — chemin d'un fichier tenant en UN bloc,
    où démarrer un `ProcessPoolExecutor` coûterait plus cher que le gain."""
    with _MODEL_LOCK:
        model = _get_model()
        segments, _info = model.transcribe(
            pcm, language="fr", beam_size=BEAM_SIZE, vad_filter=True
        )
        return " ".join(seg.text.strip() for seg in segments).strip()


def _norm_word(word: str) -> str:
    """Normalise un mot pour la comparaison de recouvrement : casse et
    ponctuation simple ignorées, car les DEUX côtés d'un même son peuvent
    être transcrits avec une ponctuation légèrement différente (VAD/beam
    search indépendants dans chaque tronçon)."""
    return word.strip(".,;:!?\"'…»«").lower()


def _find_word_overlap(prev_words: list, next_words: list, max_check: int = 12) -> int:
    """Longueur (en mots) du plus long suffixe de `prev_words` égal au préfixe
    de `next_words`, recherchée parmi les `max_check` derniers/premiers mots.
    `OVERLAP_S` (quelques secondes) ne peut produire qu'une poignée de mots
    communs, jamais une phrase entière : borner la recherche évite un faux
    positif sur une répétition fortuite plus loin dans le texte."""
    limit = min(max_check, len(prev_words), len(next_words))
    for size in range(limit, 0, -1):
        a = [_norm_word(w) for w in prev_words[-size:]]
        b = [_norm_word(w) for w in next_words[:size]]
        if a == b:
            return size
    return 0


def _merge_overlapping_texts(texts: list) -> str:
    """Recolle les textes de tronçons VOISINS transcrits avec un recouvrement
    audio (`OVERLAP_S`) en dédupliquant les mots communs à la jonction, au
    lieu d'un `' '.join` naïf qui laisserait la redite telle quelle (un mot
    ou une courte phrase à cheval sur la frontière calculée serait alors
    dupliqué dans le texte final)."""
    non_vides = [t for t in texts if t]
    if not non_vides:
        return ""
    mots = non_vides[0].split()
    for texte in non_vides[1:]:
        suivants = texte.split()
        chevauchement = _find_word_overlap(mots, suivants)
        mots.extend(suivants[chevauchement:])
    return " ".join(mots).strip()


def split_pcm_blocks(pcm, block_s: int) -> list:
    """Découpe un PCM 16 kHz en blocs d'environ `block_s` secondes. Le dernier
    bloc porte le reste (jamais de bloc vide en fin de fichier).

    Plancher à 1 s : `WHISPER_FILE_BLOCK_S=0` (ou négatif) produirait sinon un
    bloc PAR ÉCHANTILLON — des dizaines de millions de tâches sur un entretien
    réel (revue adversariale 2026-07-27)."""
    per_block = max(1, int(block_s)) * 16000
    return [pcm[i:i + per_block] for i in range(0, len(pcm), per_block)]


def _paliers_workers(n_workers: int) -> list[int]:
    """Paliers de parallélisme à tenter, du plus rapide au plus prudent.

    Un pool qui casse est presque toujours un worker tué faute de mémoire :
    chaque worker charge SON PROPRE modèle Whisper (~1,5 Go en `medium`
    int8), donc 8 workers réclament ~12 Go — que la machine n'a pas
    forcément quand Ollama garde par ailleurs son modèle chargé
    (`OLLAMA_KEEP_ALIVE`). Retenter à la moitié des workers coûte deux fois
    moins de mémoire et reste bien plus rapide que le séquentiel ; ce n'est
    qu'après ce palier qu'on retombe dans le processus courant."""
    paliers = [max(1, n_workers)]
    if paliers[0] > 2:
        paliers.append(max(2, paliers[0] // 2))
    return paliers


def _drain_parallel(blocks: list, start: int, total: int, n_workers: int):
    """Génère `(index, texte)` pour les blocs `[start, total)` via un pool de
    `n_workers` processus, dans l'ordre et par fenêtre glissante bornée.

    Laisse remonter `BrokenProcessPool` : c'est l'appelant qui décide du repli
    (palier suivant, puis séquentiel). Ne touche jamais aux blocs déjà rendus,
    donc une reprise à `start` ne re-transcrit rien.

    Acquiert `n_workers` permis sur `_GLOBAL_WORKERS_SEMAPHORE` avant de
    démarrer le pool, et les relâche à la sortie (y compris sur
    `BrokenProcessPool` ou toute autre exception) — plafond mémoire GLOBAL
    partagé entre tous les appels concurrents, cf. `GLOBAL_MAX_PARALLEL_WORKERS`."""
    window = n_workers * 2  # assez pour ne jamais affamer les workers
    futures: dict[int, object] = {}
    acquis = 0
    try:
        for _ in range(n_workers):
            _GLOBAL_WORKERS_SEMAPHORE.acquire()
            acquis += 1
        # `spawn` PARTOUT, jamais `fork` (défaut Linux jusqu'à Python 3.13) :
        # le parent a déjà des threads vivants (moniteur tqdm, feeder de
        # queue, uvicorn) et un enfant forké dans cet état se bloque sur un
        # verrou hérité. Vécu en CI le 2026-09-30 : pytest figé 6 h dans
        # `_drain_parallel` (runs #48-#50), jamais vu sous Windows qui spawne.
        with ProcessPoolExecutor(max_workers=n_workers,
                                 mp_context=multiprocessing.get_context("spawn")) as executor:
            def _submit(i: int) -> None:
                futures[i] = executor.submit(
                    _transcribe_pcm_chunk, (blocks[i], CPU_THREADS_PER_WORKER)
                )

            submitted = start
            while submitted < min(start + window, total):
                _submit(submitted)
                submitted += 1
            for index in range(start, total):
                text = futures.pop(index).result()
                if submitted < total:
                    _submit(submitted)
                    submitted += 1
                yield index, text
    finally:
        for _ in range(acquis):
            _GLOBAL_WORKERS_SEMAPHORE.release()


def iter_transcribe_blocks(
    content: bytes, block_s: int | None = None, start_index: int = 0
):
    """Génère `(index, total, texte)` bloc par bloc pour un fichier audio
    importé — équivalent serveur de la rotation de segments du direct.

    Contrairement à `transcribe_audio()` (qui ne rend la main qu'une fois le
    fichier ENTIER transcrit — plusieurs dizaines de minutes sur un entretien
    long, écran figé et aucune extraction IA démarrée avant la fin), chaque
    bloc d'environ `block_s` secondes est rendu dès qu'il est prêt : l'appelant
    (`audio_file_jobs.run_audio_file_job`) le persiste, l'UI l'affiche et
    soumet son extraction en tâche de fond pendant que les blocs suivants se
    transcrivent.

    `start_index` reprend au bloc demandé sans re-transcrire les précédents —
    ce qui permet de relancer un import échoué au bloc fautif plutôt que de
    tout recommencer (le découpage étant déterministe pour un même fichier et
    un même `block_s`, l'index désigne bien le même audio d'un appel à l'autre).

    Les blocs sont transcrits en parallèle (mêmes workers/mesures que
    `_transcribe_parallel`), mais rendus DANS L'ORDRE et par une fenêtre
    glissante bornée : soumettre les N blocs d'un coup enverrait tout le PCM
    d'un entretien de 3h (~690 Mo) aux processus workers en une fois. Si le
    pool casse, le parallélisme se dégrade par paliers sans rien perdre (cf.
    « Repli quand le pool casse » dans la docstring du module).

    Coût mémoire assumé (revue adversariale 2026-07-27) : le PCM décodé reste
    entier en mémoire pendant tout le job (les blocs en sont des VUES numpy,
    pas des copies — les libérer un à un ne libère rien), soit ~690 Mo pour
    3h, plus une copie par bloc en vol. C'est le coût que paie déjà
    `_transcribe_parallel` sur le même volume ; le supprimer demanderait un
    décodage en flux, hors périmètre ici. Les vues sont conservées jusqu'au
    bout (elles ne coûtent rien) pour que le repli puisse reprendre les blocs
    non encore rendus.
    """
    if not content:
        raise TranscriptionError("Aucun enregistrement reçu.")
    if _faster_whisper() is None:
        raise TranscriptionError(
            "faster-whisper n'est pas installé : pip install faster-whisper."
        )
    block_s = block_s or FILE_BLOCK_S

    try:
        pcm = _decode_to_pcm16k(content)
    except TranscriptionError:
        raise          # message déjà explicite (vidéo sans piste audio) — ne pas le noyer
    except Exception as exc:
        # Message FIXE, jamais `str(exc)` : cette TranscriptionError est rendue
        # telle quelle au client par les routes de transcription
        # (`interviews.py`, `{"error": str(exc)}`), et le texte d'une exception
        # `av`/`faster-whisper` peut porter des chemins du poste. Le détail
        # reste au journal serveur, où il sert au diagnostic sans être publié
        # (finding audit-technique securite du 2026-09-09). L'audit décrivait
        # ce module comme le « dernier résidu » du constat « message
        # d'exception brut » : c'était inexact, et une revue adversariale l'a
        # mesuré le 2026-09-10 — trois sites frères écrivaient encore
        # `str(exc)` dans un champ rendu au navigateur (`audio_file_jobs`,
        # `interview_segment_jobs`, `global_synthesis_job`). Leurs garde-fous
        # `except Exception` sont fermés dans le même lot que celui-ci.
        #
        # CE QUI RESTE OUVERT, dit ici pour ne pas répéter l'erreur de
        # l'audit — ne pas déclarer un chantier clos plus large qu'il ne l'est :
        # les `str(exc)` sur les erreurs MÉTIER (`_EXTRACT_ERRORS`,
        # `SynthesisAIError`, `TranscriptionError`) portent des messages écrits
        # par nous, sauf via `ai_common` — dont la branche par défaut
        # (`_friendly`) et `AIError(f"Erreur Ollama : {data['error']}")`
        # interpolent le texte du moteur, qui peut citer un chemin de modèle.
        # Et `openhub_agents` interpole le chemin d'`opencode` dans deux
        # retours persistés en `AgentResult.output`. Ces deux chaînes sont hors
        # du périmètre de l'audit du 2026-09-09 : non traitées, pas closes.
        logger.exception("Décodage audio impossible")
        raise TranscriptionError("Fichier audio illisible.") from exc
    if pcm.size == 0:
        raise NoSpeechError("Aucune parole détectée dans l'enregistrement.")

    blocks = split_pcm_blocks(pcm, block_s)
    total = len(blocks)

    # Reprise : les blocs déjà obtenus par un passage précédent ne sont pas
    # re-transcrits (le découpage est déterministe, `blocks[i]` désigne donc
    # bien le même audio d'un appel à l'autre).
    start_index = max(0, min(start_index, total))
    if start_index >= total:
        return

    if total == 1:
        try:
            yield 0, 1, _transcribe_pcm_sequential(blocks[0])
        except Exception as exc:
            logger.exception("Échec de la transcription d'un bloc audio")
            raise TranscriptionError(ECHEC_TRANSCRIPTION) from exc
        return

    index = start_index
    for workers in _paliers_workers(min(MAX_PARALLEL_WORKERS, total)):
        try:
            for i, text in _drain_parallel(blocks, index, total, workers):
                yield i, total, text
                index = i + 1
            break
        except BrokenProcessPool:
            # Un worker est mort brutalement (cf. « Repli quand le pool casse »
            # dans la docstring du module). On ne perd NI les blocs déjà rendus,
            # NI ceux qui restent : on reprend à `index` au palier suivant, et
            # en dernier recours en séquentiel ci-dessous.
            logger.warning(
                "Pool de transcription cassé au bloc %d/%d (%d workers) — palier suivant.",
                index + 1,
                total,
                workers,
            )
        except TranscriptionError:
            raise
        except Exception as exc:  # garde-fou : ne jamais propager une 500 brute
            logger.exception("Échec de la transcription d'un bloc audio")
            raise TranscriptionError(ECHEC_TRANSCRIPTION) from exc

    # Repli séquentiel : aucun tour si un palier parallèle est allé au bout.
    while index < total:
        try:
            text = _transcribe_pcm_sequential(blocks[index])
        except Exception as exc:
            logger.exception("Échec de la transcription d'un bloc audio")
            raise TranscriptionError(ECHEC_TRANSCRIPTION) from exc
        yield index, total, text
        index += 1


def transcribe_audio(content: bytes) -> str:
    """Retourne le texte transcrit. Lève TranscriptionError. Au-delà de
    `PARALLEL_THRESHOLD_S`, découpe et transcrit en parallèle (voir
    docstring du module) ; en dessous (cas du direct au fil de l'eau),
    chemin séquentiel inchangé."""
    if not content:
        raise TranscriptionError("Aucun enregistrement reçu.")
    faster_whisper = _faster_whisper()
    if faster_whisper is None:
        raise TranscriptionError(
            "faster-whisper n'est pas installé : pip install faster-whisper."
        )

    # Avant de choisir la branche : les deux décodeurs en aval (le nôtre et celui
    # de faster-whisper sur le chemin court) échouent sur un « tuple index out of
    # range » incompréhensible quand le fichier n'a pas de piste audio.
    _exiger_piste_audio(content)
    duration_s = _probe_duration_s(content)

    try:
        if duration_s is not None and duration_s > PARALLEL_THRESHOLD_S:
            text = _transcribe_parallel(content, duration_s)
        else:
            # Verrou : le modèle singleton est partagé entre threads (cf.
            # `_MODEL_LOCK`). L'itération sur `segments` étant paresseuse, elle
            # doit rester DANS le verrou — c'est elle qui fait tourner le modèle.
            #
            # Attente BORNÉE, pas indéfinie (atelier-dev 2026-09-15) : les deux
            # appelants de `transcribe_audio()` (route segment du direct et
            # dictée de notes libres, cf. `SEGMENT_LOCK_TIMEOUT_S` ci-dessus) —
            # plusieurs onglets/entretiens concurrents se sérialisent derrière
            # ce même verrou. Au-delà de `SEGMENT_LOCK_TIMEOUT_S`, mieux vaut
            # renvoyer un refus "occupé" exploitable par le client (retry avec
            # délai connu) que de le laisser espérer sur un thread bloqué
            # jusqu'au timeout réseau.
            if not _MODEL_LOCK.acquire(timeout=SEGMENT_LOCK_TIMEOUT_S):
                exc = TranscriptionBusyError(
                    "Transcription momentanément occupée, réessayez."
                )
                exc.retry_after_s = SEGMENT_LOCK_TIMEOUT_S
                raise exc
            try:
                model = _get_model()
                # beam_size piloté par BEAM_SIZE (défaut 2, relevé depuis 1 le
                # 2026-07-15 : gain de précision net sur les noms propres/vocabulaire
                # métier d'un entretien réel, cf. l'en-tête du module). vad_filter
                # saute les silences plutôt que de les faire décoder.
                segments, _info = model.transcribe(
                    io.BytesIO(content), language="fr", beam_size=BEAM_SIZE, vad_filter=True
                )
                text = " ".join(seg.text.strip() for seg in segments).strip()
            finally:
                _MODEL_LOCK.release()
    except TranscriptionError:
        raise
    except Exception as exc:  # garde-fou : ne jamais propager une 500 brute
        logger.exception("Échec de la transcription d'un bloc audio")
        raise TranscriptionError(ECHEC_TRANSCRIPTION) from exc

    if not text:
        raise NoSpeechError("Aucune parole détectée dans l'enregistrement.")
    return text
