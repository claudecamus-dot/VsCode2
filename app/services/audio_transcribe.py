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
"""
from __future__ import annotations

import io
import os
from concurrent.futures import ProcessPoolExecutor

from .ai_common import AIError

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "medium")
BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "2"))

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
    l'ordre (reduce) — voir docstring du module pour la mesure de gain."""
    pcm = _decode_to_pcm16k(content)
    if pcm.size == 0:
        return ""

    n_workers = max(1, min(MAX_PARALLEL_WORKERS, int(duration_s // 30) or 1))
    chunk_len = len(pcm) // n_workers
    chunks = [
        pcm[i * chunk_len:] if i == n_workers - 1 else pcm[i * chunk_len:(i + 1) * chunk_len]
        for i in range(n_workers)
    ]
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        parts = list(
            executor.map(_transcribe_pcm_chunk, [(c, CPU_THREADS_PER_WORKER) for c in chunks])
        )
    return " ".join(p for p in parts if p).strip()


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
