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
`PARALLEL_THRESHOLD_S`, `transcribe_audio()` découpe l'audio en tronçons
d'environ 30s et les transcrit en parallèle sur plusieurs cœurs CPU
(`ProcessPoolExecutor` — chaque processus charge son propre modèle, pas de
partage possible entre processus), avant de concaténer les textes dans
l'ordre. Mesuré ~1,8x plus rapide que le séquentiel sur un extrait réel de
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
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from .ai_common import AIError

logger = logging.getLogger(__name__)

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "medium")
BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "2"))

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
FILE_BLOCK_S = int(os.environ.get("WHISPER_FILE_BLOCK_S", "60"))

# Au-delà de cette durée (secondes), transcrire en parallèle plutôt qu'en un
# seul appel séquentiel — voir docstring du module.
PARALLEL_THRESHOLD_S = int(os.environ.get("WHISPER_PARALLEL_THRESHOLD_S", "90"))
# Nombre max de workers parallèles — au-delà de ~8 sur ce type de CPU
# (10 cœurs physiques), le gain mesuré devient marginal (rendements
# décroissants, cf. cadrage perf).
MAX_PARALLEL_WORKERS = int(os.environ.get("WHISPER_MAX_WORKERS", str(min(8, os.cpu_count() or 4))))
# Threads CPU par worker parallèle — mesuré : 1 thread/worker avec
# MAX_PARALLEL_WORKERS workers simultanés bat un seul worker à plusieurs
# threads, sur ce CPU hybride P/E-cores (contention mémoire au-delà d'un
# certain nombre de threads par processus).
CPU_THREADS_PER_WORKER = int(os.environ.get("WHISPER_CPU_THREADS", "1"))

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
    premier enregistrement réel de l'utilisateur n'en paie pas le coût."""
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
        return None


def _decode_to_pcm16k(content: bytes):
    """Décode le contenu audio en PCM mono 16kHz (format attendu par
    Whisper) — pré-décodage explicite plutôt que de laisser faster-whisper
    redécoder en interne un flux d'octets bruts à chaque tronçon : mesuré
    ~40 % plus rapide en pratique (2026-07-16), et de toute façon
    nécessaire ici pour découper l'audio en tronçons indépendants."""
    import av
    import numpy as np

    container = av.open(io.BytesIO(content))
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
    """Découpe l'audio en tronçons d'environ 30s (map), les transcrit en
    parallèle sur plusieurs cœurs CPU, puis concatène les textes dans
    l'ordre (reduce) — voir docstring du module pour la mesure de gain.

    Repli séquentiel si le pool de processus casse (cf. `_pool_casse`)."""
    pcm = _decode_to_pcm16k(content)
    if pcm.size == 0:
        return ""

    n_workers = max(1, min(MAX_PARALLEL_WORKERS, int(duration_s // 30) or 1))
    chunk_len = len(pcm) // n_workers
    chunks = [
        pcm[i * chunk_len:] if i == n_workers - 1 else pcm[i * chunk_len:(i + 1) * chunk_len]
        for i in range(n_workers)
    ]
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
    return " ".join(p for p in (parts[i] for i in range(total)) if p).strip()


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
    donc une reprise à `start` ne re-transcrit rien."""
    window = n_workers * 2  # assez pour ne jamais affamer les workers
    futures: dict[int, object] = {}
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
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
    except Exception as exc:
        raise TranscriptionError(f"Fichier audio illisible : {exc}") from exc
    if pcm.size == 0:
        raise TranscriptionError("Aucune parole détectée dans l'enregistrement.")

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
            raise TranscriptionError(f"Échec de la transcription : {exc}") from exc
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
            raise TranscriptionError(f"Échec de la transcription : {exc}") from exc

    # Repli séquentiel : aucun tour si un palier parallèle est allé au bout.
    while index < total:
        try:
            text = _transcribe_pcm_sequential(blocks[index])
        except Exception as exc:
            raise TranscriptionError(f"Échec de la transcription : {exc}") from exc
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

    duration_s = _probe_duration_s(content)

    try:
        if duration_s is not None and duration_s > PARALLEL_THRESHOLD_S:
            text = _transcribe_parallel(content, duration_s)
        else:
            # Verrou : le modèle singleton est partagé entre threads (cf.
            # `_MODEL_LOCK`). L'itération sur `segments` étant paresseuse, elle
            # doit rester DANS le verrou — c'est elle qui fait tourner le modèle.
            with _MODEL_LOCK:
                model = _get_model()
                # beam_size piloté par BEAM_SIZE (défaut 2, relevé depuis 1 le
                # 2026-07-15 : gain de précision net sur les noms propres/vocabulaire
                # métier d'un entretien réel, cf. l'en-tête du module). vad_filter
                # saute les silences plutôt que de les faire décoder.
                segments, _info = model.transcribe(
                    io.BytesIO(content), language="fr", beam_size=BEAM_SIZE, vad_filter=True
                )
                text = " ".join(seg.text.strip() for seg in segments).strip()
    except TranscriptionError:
        raise
    except Exception as exc:  # garde-fou : ne jamais propager une 500 brute
        raise TranscriptionError(f"Échec de la transcription : {exc}") from exc

    if not text:
        raise TranscriptionError("Aucune parole détectée dans l'enregistrement.")
    return text
