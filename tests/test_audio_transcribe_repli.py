"""Repli de la transcription quand le pool de processus casse (2026-07-29).

Signalement utilisateur : l'import d'un fichier audio échouait sur
« ⚠ [fichier : Échec de la transcription : A child process terminated abruptly,
the process pool is not usable anymore] » — un worker tué (pression mémoire :
chaque worker charge SON modèle Whisper) faisait perdre TOUT l'import, y
compris les blocs déjà transcrits.

Avant correctif, `iter_transcribe_blocks` laissait `BrokenProcessPool` être
enveloppée en `TranscriptionError` : la génération s'arrêtait là. Ces tests
échoueraient donc sur le code d'avant (le premier lève au lieu de rendre les
blocs restants).

Le vrai `ProcessPoolExecutor` n'est pas exercé ici — démarrer de vrais
sous-processus Whisper dans la suite coûterait plusieurs minutes et dépendrait
de la mémoire de la machine, c'est-à-dire exactement la variable qu'on ne
contrôle pas. Les tests d'`iter_transcribe_blocks` remplacent `_drain_parallel`
par un double ; le VRAI `_drain_parallel` (fenêtre glissante, ordre, reprise à
`start`) est lui exercé par le dernier test de ce fichier et par la section
« générateur réel » de `test_audio_file_import.py`, où seuls l'executor et la
fonction de transcription sont doublés (revue adversariale 2026-07-29 : la
logique neuve ne doit pas être intégralement monkeypatchée hors de tout test).
"""
from __future__ import annotations

from concurrent.futures.process import BrokenProcessPool

import pytest

from app.services import audio_transcribe


@pytest.fixture
def blocs_factices(monkeypatch):
    """Court-circuite le décodage audio : `iter_transcribe_blocks` travaille sur
    une liste de blocs déjà découpés, seule chose qui compte ici."""
    monkeypatch.setattr(audio_transcribe, "_faster_whisper", lambda: object())
    monkeypatch.setattr(
        audio_transcribe,
        "split_pcm_blocks",
        lambda pcm, block_s: [f"bloc{i}" for i in range(5)],
    )
    # `pcm.size` est consulté avant le découpage.
    class _Pcm(list):
        size = 1

    monkeypatch.setattr(audio_transcribe, "_decode_to_pcm16k", lambda content: _Pcm(["pcm"]))
    return None


def test_pool_casse_en_cours_rend_quand_meme_tous_les_blocs(blocs_factices, monkeypatch):
    """Le pool meurt au 3e bloc : les 2 premiers restent rendus, les 3 suivants
    sont repris — aucun bloc perdu, aucun re-transcrit, et l'ordre tient."""
    paliers_vus = []

    def _drain(blocks, start, total, n_workers):
        paliers_vus.append((start, n_workers))
        if n_workers > 1:  # les paliers parallèles cassent tous les deux
            for i in range(start, 2):
                yield i, f"parallele{i}"
            raise BrokenProcessPool("A child process terminated abruptly")
        for i in range(start, total):
            yield i, f"parallele{i}"

    monkeypatch.setattr(audio_transcribe, "_drain_parallel", _drain)
    monkeypatch.setattr(
        audio_transcribe, "_transcribe_pcm_sequential", lambda bloc: f"sequentiel:{bloc}"
    )

    rendus = list(audio_transcribe.iter_transcribe_blocks(b"audio"))

    assert [i for i, _total, _t in rendus] == [0, 1, 2, 3, 4]
    assert all(total == 5 for _i, total, _t in rendus)
    textes = [t for _i, _total, t in rendus]
    assert textes[:2] == ["parallele0", "parallele1"]
    # Reprise AU bloc où le pool est mort, pas au début.
    assert textes[2:] == ["sequentiel:bloc2", "sequentiel:bloc3", "sequentiel:bloc4"]
    # Palier suivant tenté avant de retomber en séquentiel, et toujours à partir
    # du bloc non rendu.
    assert [start for start, _w in paliers_vus] == [0, 2]


def test_pool_sain_ne_declenche_aucun_repli(blocs_factices, monkeypatch):
    """Chemin nominal : un seul palier, jamais de transcription séquentielle."""
    def _drain(blocks, start, total, n_workers):
        for i in range(start, total):
            yield i, f"parallele{i}"

    def _jamais(bloc):
        raise AssertionError("repli séquentiel déclenché alors que le pool est sain")

    monkeypatch.setattr(audio_transcribe, "_drain_parallel", _drain)
    monkeypatch.setattr(audio_transcribe, "_transcribe_pcm_sequential", _jamais)

    rendus = list(audio_transcribe.iter_transcribe_blocks(b"audio"))
    assert [t for _i, _total, t in rendus] == [f"parallele{i}" for i in range(5)]


def test_erreur_non_liee_au_pool_reste_une_erreur_fonctionnelle(blocs_factices, monkeypatch):
    """Le repli ne doit pas avaler un vrai problème (fichier illisible, modèle
    absent) : seule la mort du pool déclenche une dégradation."""
    def _drain(blocks, start, total, n_workers):
        raise ValueError("flux corrompu")
        yield  # pragma: no cover - fait de _drain un générateur

    monkeypatch.setattr(audio_transcribe, "_drain_parallel", _drain)

    with pytest.raises(audio_transcribe.TranscriptionError, match="flux corrompu"):
        list(audio_transcribe.iter_transcribe_blocks(b"audio"))


def test_paliers_workers_degrade_puis_sequentiel():
    """8 workers -> 4 workers -> (séquentiel, hors de cette liste). Un pool déjà
    petit ne se dédouble pas en paliers inutiles."""
    assert audio_transcribe._paliers_workers(8) == [8, 4]
    assert audio_transcribe._paliers_workers(3) == [3, 2]
    assert audio_transcribe._paliers_workers(2) == [2]
    assert audio_transcribe._paliers_workers(1) == [1]


class _LazyExecutor:
    """Substitut de `ProcessPoolExecutor` qui n'exécute la tâche qu'au
    `.result()` — indispensable ici : un double qui exécute au `submit` ferait
    « transcrire » toute la fenêtre glissante avant la première panne, et le
    comptage des re-transcriptions ne prouverait plus rien."""

    def __init__(self, max_workers=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, args):
        class _F:
            def result(self):
                return fn(args)

        return _F()


def test_transcribe_parallel_reprend_au_troncon_fautif_sans_re_transcrire(monkeypatch):
    """Régression (revue adversariale 2026-07-29) : sur le code d'avant,
    `_transcribe_parallel` répondait à `BrokenProcessPool` par
    `_transcribe_pcm_sequential(pcm)` sur le PCM ENTIER — les tronçons déjà
    transcrits par le pool étaient jetés et tout l'audio repassait en
    séquentiel, précisément sur les fichiers longs que ce chemin cible.
    Ce test exerce le VRAI `_drain_parallel` (fenêtre glissante, ordre,
    reprise à `start`) : seuls l'executor et la fonction de transcription
    sont doublés."""
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(
        audio_transcribe, "_decode_to_pcm16k",
        lambda content: np.zeros(16000 * 120, dtype=np.float32),
    )
    monkeypatch.setattr(audio_transcribe, "MAX_PARALLEL_WORKERS", 4)
    monkeypatch.setattr(audio_transcribe, "ProcessPoolExecutor", _LazyExecutor)

    appels: list[int] = []
    etat = {"casse": True}

    def _chunk(args):
        seq = len(appels)
        appels.append(seq)
        if etat["casse"] and seq == 1:
            etat["casse"] = False
            raise BrokenProcessPool("A child process terminated abruptly")
        return f"a{seq}"

    def _jamais_le_pcm_entier(pcm):
        raise AssertionError(
            "repli séquentiel sur le PCM entier : les tronçons déjà transcrits sont jetés"
        )

    monkeypatch.setattr(audio_transcribe, "_transcribe_pcm_chunk", _chunk)
    monkeypatch.setattr(
        audio_transcribe, "_transcribe_pcm_sequential", _jamais_le_pcm_entier
    )

    texte = audio_transcribe._transcribe_parallel(b"audio", duration_s=120.0)

    # 4 tronçons : t0 réussi au 1er palier (appel 0), panne à l'appel 1, puis le
    # 2e palier reprend les tronçons 1-3 (appels 2-4). 5 appels au total — le
    # tronçon 0 n'est JAMAIS re-transcrit.
    assert appels == [0, 1, 2, 3, 4]
    assert texte == "a0 a2 a3 a4"
