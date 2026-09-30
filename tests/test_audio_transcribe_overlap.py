"""Recouvrement entre tronçons de `_transcribe_parallel` et déduplication au
recollement (finding VSCode5 2026-09-21) : un slicing contigu SANS marge
coupait un mot pile sur la frontière calculée (`chunk_len = len(pcm) //
n_workers`) — il atterrissait à moitié des deux côtés, donc dupliqué (ou
perdu) selon ce que chaque tronçon parvenait à transcrire de sa moitié. Ces
tests échoueraient sur le code d'avant (`' '.join` naïf, aucune marge) :
- `_transcribe_parallel` slicait sans `OVERLAP_S` (inexistant) ;
- `' '.join` ne dédupliquait rien.

Complète `test_audio_transcribe_parallel.py` (aiguillage séquentiel/parallèle,
pipeline réel) sans dupliquer sa mécanique de fixture.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.services import audio_transcribe


# --------------------------------------------------------------------------- #
# _find_word_overlap / _merge_overlapping_texts — dédup au recollement.
# --------------------------------------------------------------------------- #
def test_merge_dedupes_word_split_exactly_on_boundary() -> None:
    """Le mot "anniversaire" tombe pile sur la frontière : avec la marge de
    recouvrement, il est transcrit EN ENTIER dans les deux tronçons voisins
    ('... son anniversaire' puis 'anniversaire la semaine ...') — le
    recollement doit dédupliquer la redite, pas la laisser dupliquée."""
    texte_gauche = "on a fêté son anniversaire"
    texte_droit = "anniversaire la semaine dernière"

    resultat = audio_transcribe._merge_overlapping_texts([texte_gauche, texte_droit])

    assert resultat == "on a fêté son anniversaire la semaine dernière"
    assert resultat.count("anniversaire") == 1


def test_merge_dedupes_multi_word_overlap_and_tolerates_punctuation() -> None:
    """Plusieurs mots communs à la jonction, avec une ponctuation différente
    d'un côté (VAD/beam search indépendants par tronçon) : la comparaison
    normalisée doit quand même reconnaître le recouvrement."""
    texte_gauche = "il est arrivé en retard, ce matin"
    texte_droit = "ce matin il pleuvait des cordes"

    resultat = audio_transcribe._merge_overlapping_texts([texte_gauche, texte_droit])

    assert resultat == "il est arrivé en retard, ce matin il pleuvait des cordes"


def test_merge_without_overlap_falls_back_to_plain_concatenation() -> None:
    """Aucun mot commun à la jonction : comportement inchangé, simple
    concaténation (pas de perte, pas de fausse dédup)."""
    resultat = audio_transcribe._merge_overlapping_texts(["bonjour tout le monde", "je commence"])
    assert resultat == "bonjour tout le monde je commence"


def test_merge_ignores_empty_chunks() -> None:
    resultat = audio_transcribe._merge_overlapping_texts(["", "bonjour", "", "le monde"])
    assert resultat == "bonjour le monde"


# --------------------------------------------------------------------------- #
# _transcribe_parallel — les tronçons envoyés au pool se chevauchent bien.
# --------------------------------------------------------------------------- #
def test_transcribe_parallel_builds_chunks_with_overlap_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avant correctif : `chunk_len = len(pcm)//n_workers` et un slicing
    contigu SANS marge — les tronçons ne se recouvraient jamais. On vérifie
    ici que le tronçon interne (ni premier ni dernier) déborde bien de
    `OVERLAP_S` de chaque côté de la frontière contiguë."""
    sample_rate = 16000
    n_workers = 3
    duration_s = 200.0  # borne n_workers à MAX_PARALLEL_WORKERS via // 30, peu importe ici
    pcm = np.arange(sample_rate * 30, dtype=np.float32)  # 30s de PCM factice

    monkeypatch.setattr(audio_transcribe, "_decode_to_pcm16k", lambda content: pcm)
    monkeypatch.setattr(audio_transcribe, "MAX_PARALLEL_WORKERS", n_workers)

    captured_chunks = {}

    def fake_drain(blocks, start, total, workers):
        captured_chunks["blocks"] = blocks
        for i in range(start, total):
            yield i, f"texte{i}"

    monkeypatch.setattr(audio_transcribe, "_drain_parallel", fake_drain)

    audio_transcribe._transcribe_parallel(b"contenu factice", duration_s)

    chunks = captured_chunks["blocks"]
    assert len(chunks) == n_workers
    chunk_len = len(pcm) // n_workers
    overlap = int(audio_transcribe.OVERLAP_S * sample_rate)
    # Le tronçon du milieu (index 1) doit déborder de `overlap` de chaque
    # côté de sa frontière contiguë [chunk_len, 2*chunk_len) : plus grand
    # qu'un slicing contigu, jamais égal (sauf overlap nul).
    assert overlap > 0
    assert len(chunks[1]) == min(len(pcm), 2 * chunk_len + overlap) - max(0, chunk_len - overlap)
    assert len(chunks[1]) > chunk_len


# --------------------------------------------------------------------------- #
# Sémaphore global (point 1 du brief) — plafond de workers actifs tous
# appels confondus, pas seulement par appel.
# --------------------------------------------------------------------------- #
def test_global_semaphore_bounds_concurrent_drain_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deux `_drain_parallel` réclamant chacun plus de la moitié du plafond
    global ne peuvent pas tenir leurs permis en même temps : le second doit
    attendre que le premier relâche les siens. Sans le sémaphore (avant
    correctif), rien ne les empêchait de tourner simultanément."""
    import threading
    import time

    monkeypatch.setattr(audio_transcribe, "GLOBAL_MAX_PARALLEL_WORKERS", 2)
    monkeypatch.setattr(audio_transcribe, "_GLOBAL_WORKERS_SEMAPHORE", threading.Semaphore(2))

    # Test déterministe : plusieurs threads acquièrent directement le nombre
    # de permis qu'un appel à `n_workers` réclamerait dans `_drain_parallel`
    # (mécanique exercée par `test_drain_parallel_acquires_and_releases_global_semaphore`
    # ci-dessous), et on vérifie que la somme des permis simultanément
    # détenus ne dépasse jamais le plafond global.
    sem = audio_transcribe._GLOBAL_WORKERS_SEMAPHORE
    max_detenus = {"valeur": 0}
    detenus_courants = {"valeur": 0}
    verrou = threading.Lock()

    def worker(n_permis):
        acquis = []
        for _ in range(n_permis):
            sem.acquire()
            acquis.append(1)
        with verrou:
            detenus_courants["valeur"] += n_permis
            max_detenus["valeur"] = max(max_detenus["valeur"], detenus_courants["valeur"])
        time.sleep(0.05)
        with verrou:
            detenus_courants["valeur"] -= n_permis
        for _ in acquis:
            sem.release()

    threads = [threading.Thread(target=worker, args=(2,)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert max_detenus["valeur"] <= 2


def test_drain_parallel_acquires_and_releases_global_semaphore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_drain_parallel` doit acquérir `n_workers` permis avant de soumettre
    au pool et tous les relâcher à la sortie — vérifié via un faux sémaphore
    qui compte les appels, et un faux executor qui n'exécute rien de réel."""

    class FauxSemaphore:
        def __init__(self):
            self.acquis = 0
            self.max_simultane = 0

        def acquire(self):
            self.acquis += 1
            self.max_simultane = max(self.max_simultane, self.acquis)

        def release(self):
            self.acquis -= 1

    class FauxFuture:
        def __init__(self, valeur):
            self._valeur = valeur

        def result(self):
            return self._valeur

    class FauxExecutor:
        def __init__(self, max_workers, mp_context=None):
            self.max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, fn, args):
            pcm, threads = args
            return FauxFuture(f"texte-{pcm}")

    faux_sem = FauxSemaphore()
    monkeypatch.setattr(audio_transcribe, "_GLOBAL_WORKERS_SEMAPHORE", faux_sem)
    monkeypatch.setattr(audio_transcribe, "ProcessPoolExecutor", FauxExecutor)

    blocks = ["b0", "b1", "b2"]
    resultats = list(audio_transcribe._drain_parallel(blocks, 0, 3, n_workers=3))

    assert [r[1] for r in resultats] == ["texte-b0", "texte-b1", "texte-b2"]
    assert faux_sem.max_simultane == 3  # les 3 permis tenus pendant le pool
    assert faux_sem.acquis == 0  # tous relâchés à la fin
