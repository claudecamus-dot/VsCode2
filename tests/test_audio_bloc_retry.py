"""Retry par bloc de `iter_transcribe_blocks` (lot « transcription fiable »).

Critère : « une transcription de la totalité de l'audio sans segment failed du
1er coup ». Un retry qui réussit est un échec MASQUÉ : il est compté dans
`stats["blocs_rejoues"]`. Après `BLOC_MAX_TENTATIVES` tentatives au total, le
job échoue (TranscriptionError) et la reprise par `start_index` fait le reste.
Tout est doublé (pas de Whisper, pas de vrai pool) ; aucune pause réelle.
"""
from __future__ import annotations

from concurrent.futures.process import BrokenProcessPool

import numpy as np
import pytest

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.models import AudioFileJob
from app.services import audio_file_jobs, audio_transcribe as at


@pytest.fixture(autouse=True)
def _sans_pause(monkeypatch):
    monkeypatch.setattr(at, "BLOC_RETRY_PAUSE_S", 0)
    monkeypatch.setattr(at, "_faster_whisper", lambda: object())


def _pcm(monkeypatch, secondes: int):
    pcm = np.zeros(secondes * 16000, dtype=np.float32)
    monkeypatch.setattr(at, "_decode_to_pcm16k", lambda content: pcm)
    return pcm


def _marquer_blocs(monkeypatch, n: int):
    monkeypatch.setattr(
        at, "split_pcm_blocks",
        lambda pcm, block_s: [np.array([float(i)]) for i in range(n)],
    )


def _sequentiel(monkeypatch, bloc_fautif: int | None, nb_fautes: int, exc=None):
    """Séquentiel doublé : le bloc `bloc_fautif` lève `exc` `nb_fautes` fois
    avant de réussir. Renvoie la liste des index appelés."""
    appels = []
    restant = {"n": nb_fautes}
    exc = exc or RuntimeError("transitoire")

    def _fake(bloc):
        i = int(bloc[0])
        appels.append(i)
        if i == bloc_fautif and restant["n"] > 0:
            restant["n"] -= 1
            raise exc
        return f"t{i}"

    monkeypatch.setattr(at, "_transcribe_pcm_sequential", _fake)
    return appels


def _drain_seq_only(monkeypatch, casse_au_bloc: int):
    """Force le repli séquentiel : le pool rend les blocs d'avant puis casse
    (à chaque palier) au bloc `casse_au_bloc`."""
    def _drain(blocks, start, total, n_workers):
        for i in range(start, casse_au_bloc):
            yield i, f"pool{i}"
        raise BrokenProcessPool("mort")

    monkeypatch.setattr(at, "_drain_parallel", _drain)


def _drain_fake(monkeypatch, fautes: dict, paliers_vus: list):
    """Pool doublé. `fautes[i]` = exceptions levées successivement quand le
    pool atteint le bloc `i` ; texte rendu `pool{i}`."""
    restant = {i: list(v) for i, v in fautes.items()}

    def _drain(blocks, start, total, n_workers):
        paliers_vus.append((start, n_workers))
        for i in range(start, total):
            if restant.get(i):
                raise restant[i].pop(0)
            yield i, f"pool{i}"

    monkeypatch.setattr(at, "_drain_parallel", _drain)


CHEMINS = ["bloc_unique", "repli_sequentiel", "pool"]


def _mise_en_place(monkeypatch, chemin, faute_bloc, nb_fautes, exc=None):
    """Prépare le chemin demandé ; renvoie (n_blocs, index_faute, appels, paliers)."""
    exc = exc or RuntimeError("transitoire")
    paliers: list = []
    if chemin == "bloc_unique":
        _pcm(monkeypatch, 1)
        _marquer_blocs(monkeypatch, 1)
        return 1, 0, _sequentiel(monkeypatch, 0, nb_fautes, exc), paliers
    _pcm(monkeypatch, 4)
    _marquer_blocs(monkeypatch, 4)
    if chemin == "repli_sequentiel":
        _drain_seq_only(monkeypatch, faute_bloc)
        monkeypatch.setattr(at, "MAX_PARALLEL_WORKERS", 2)   # un seul palier
        return 4, faute_bloc, _sequentiel(monkeypatch, faute_bloc, nb_fautes, exc), paliers
    appels = _sequentiel(monkeypatch, faute_bloc, nb_fautes, exc)
    _drain_fake(monkeypatch, {faute_bloc: [exc] * nb_fautes}, paliers)
    return 4, faute_bloc, appels, paliers


@pytest.mark.parametrize("chemin", CHEMINS)
def test_un_bloc_en_erreur_est_rejoue_puis_le_bloc_est_livre(monkeypatch, chemin):
    n, f, _appels, _p = _mise_en_place(monkeypatch, chemin, 2, 1)
    stats: dict = {}
    rendus = list(at.iter_transcribe_blocks(b"x", block_s=1, stats=stats))
    assert [i for i, _t, _x in rendus] == list(range(n))   # ordre, sans doublon
    assert all(t == n for _i, t, _x in rendus)
    assert stats["blocs_rejoues"] == [f]


@pytest.mark.parametrize("chemin", CHEMINS)
def test_retries_epuises_levent_transcription_error_avec_blocs_precedents_livres(
    monkeypatch, chemin
):
    n, f, _appels, _p = _mise_en_place(monkeypatch, chemin, 2, 99)
    livres = []
    stats: dict = {}
    with pytest.raises(at.TranscriptionError) as err:
        for i, _t, _texte in at.iter_transcribe_blocks(b"x", block_s=1, stats=stats):
            livres.append(i)
    assert str(err.value) == at.ECHEC_TRANSCRIPTION
    assert livres == list(range(f))   # ceux d'avant livrés, aucun saut
    assert stats["blocs_rejoues"] == [f]

    # Reprise par index : plus de faute, on repart du bloc fautif, sans doublon.
    monkeypatch.setattr(
        at, "_drain_parallel",
        lambda b, s, t, w: iter((i, f"r{i}") for i in range(s, t)),
    )
    monkeypatch.setattr(at, "_transcribe_pcm_sequential", lambda bloc: f"r{int(bloc[0])}")
    suite = [i for i, _t, _x in at.iter_transcribe_blocks(b"x", block_s=1, start_index=f)]
    assert livres + suite == list(range(n))


def test_exactement_trois_tentatives_au_total_chemin_sequentiel(monkeypatch):
    _pcm(monkeypatch, 1)
    _marquer_blocs(monkeypatch, 1)
    appels = _sequentiel(monkeypatch, 0, 99)
    with pytest.raises(at.TranscriptionError):
        list(at.iter_transcribe_blocks(b"x", block_s=1))
    assert len(appels) == at.BLOC_MAX_TENTATIVES == 3


def test_tentatives_pool_comptent_dans_le_total(monkeypatch):
    """Pool en faute 2 fois (deux paliers) : il ne reste qu'UNE tentative
    séquentielle, pas trois."""
    _pcm(monkeypatch, 4)
    _marquer_blocs(monkeypatch, 4)
    monkeypatch.setattr(at, "MAX_PARALLEL_WORKERS", 4)
    _drain_fake(monkeypatch, {1: [RuntimeError("a"), RuntimeError("b")]}, [])
    appels = _sequentiel(monkeypatch, 1, 99)
    with pytest.raises(at.TranscriptionError):
        list(at.iter_transcribe_blocks(b"x", block_s=1))
    assert appels == [1]


def test_memoryerror_worker_descend_de_palier(monkeypatch):
    _pcm(monkeypatch, 4)
    _marquer_blocs(monkeypatch, 4)
    monkeypatch.setattr(at, "MAX_PARALLEL_WORKERS", 8)
    paliers: list = []
    _drain_fake(monkeypatch, {2: [MemoryError()]}, paliers)
    stats: dict = {}
    rendus = list(at.iter_transcribe_blocks(b"x", block_s=1, stats=stats))
    assert [i for i, _t, _x in rendus] == [0, 1, 2, 3]
    (s0, w0), (s1, w1) = paliers
    assert s0 == 0 and s1 == 2        # reprise AU bloc fautif
    assert w1 < w0                    # palier inférieur, pas le même
    assert stats["blocs_rejoues"] == [2]


def test_chemin_nominal_zero_retry(monkeypatch):
    _pcm(monkeypatch, 4)
    _marquer_blocs(monkeypatch, 4)
    _drain_fake(monkeypatch, {}, [])
    stats: dict = {}
    rendus = list(at.iter_transcribe_blocks(b"x", block_s=1, stats=stats))
    assert len(rendus) == 4
    assert stats["blocs_rejoues"] == []
    assert stats["duree_decodee_s"] == 4.0


def test_sans_stats_comportement_inchange(monkeypatch):
    _pcm(monkeypatch, 4)
    _marquer_blocs(monkeypatch, 4)
    _drain_fake(monkeypatch, {}, [])
    textes = [t for _i, _n, t in at.iter_transcribe_blocks(b"x", block_s=1)]
    assert textes == ["pool0", "pool1", "pool2", "pool3"]


@pytest.mark.parametrize("chemin", ["bloc_unique", "repli_sequentiel"])
@pytest.mark.parametrize("exc", [at.NoSpeechError("silence"), OSError("disque")])
def test_no_speech_et_oserror_ne_sont_pas_rejoues(monkeypatch, chemin, exc):
    _n, f, appels, _p = _mise_en_place(monkeypatch, chemin, 1, 99, exc=exc)
    with pytest.raises(at.TranscriptionError):
        list(at.iter_transcribe_blocks(b"x", block_s=1))
    assert appels.count(f) == 1


def test_oserror_dans_le_pool_n_est_pas_rejoue(monkeypatch):
    _pcm(monkeypatch, 4)
    _marquer_blocs(monkeypatch, 4)
    paliers: list = []
    _drain_fake(monkeypatch, {1: [OSError("disque")]}, paliers)
    with pytest.raises(at.TranscriptionError):
        list(at.iter_transcribe_blocks(b"x", block_s=1))
    assert len(paliers) == 1


def test_decodage_illisible_n_est_pas_rejoue(monkeypatch):
    appels = []

    def _decode(content):
        appels.append(1)
        raise ValueError("illisible")

    monkeypatch.setattr(at, "_decode_to_pcm16k", _decode)
    with pytest.raises(at.TranscriptionError):
        list(at.iter_transcribe_blocks(b"x", block_s=1))
    assert len(appels) == 1


def test_retry_journalise_sans_chemin_ni_texte(monkeypatch, caplog):
    _mise_en_place(monkeypatch, "repli_sequentiel", 1, 1)
    with caplog.at_level("WARNING", logger=at.logger.name):
        list(at.iter_transcribe_blocks(b"x", block_s=1))
    msgs = [r.getMessage() for r in caplog.records if "tentative" in r.getMessage()]
    assert msgs and "Bloc 2/4" in msgs[0] and "2/3" in msgs[0]
    # Ni le texte de l'exception, ni un chemin : seul le TYPE est journalisé.
    import re

    gabarit = (r"Bloc \d+/\d+ en échec \(\w+(, \d+ workers)?\) — "
               r"tentative \d+/\d+(, palier suivant)?\.")
    assert all(re.fullmatch(gabarit, m) for m in msgs), msgs


# --- Invariant CR-20 (refactor sans changement de comportement, pas une preuve P1) ---

def _reinit_db() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def test_invariant_plafond_essais_bloc_via_job_de_fond(monkeypatch):
    """Un bloc toujours en échec est tenté exactement `plafond_essais_bloc(BLOC_RETRIES)`
    fois par le job de fond (le plafond est recalculé à l'appel : il suit un patch de
    BLOC_MAX_TENTATIVES ou de BLOC_RETRIES)."""
    _reinit_db()
    monkeypatch.setattr(at, "BLOC_RETRY_PAUSE_S", 0)
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0)
    monkeypatch.setattr(at, "_faster_whisper", lambda: object())
    monkeypatch.setattr(at, "_probe_duration_s", lambda content: 5.0)
    monkeypatch.setattr(
        at, "_decode_to_pcm16k", lambda content: np.zeros(16000 * 5, dtype=np.float32)
    )
    appels = []

    def _toujours_ko(bloc):
        appels.append(1)
        raise RuntimeError("echec transitoire")

    monkeypatch.setattr(at, "_transcribe_pcm_sequential", _toujours_ko)
    nom = "cr20_plafond_test.wav"
    (RECORDINGS_DIR / nom).write_bytes(b"x")
    try:
        with SessionLocal() as db:
            job = AudioFileJob(session_token="tok-cr20", status="pending", filename=nom)
            db.add(job)
            db.commit()
            job_id = job.id
        audio_file_jobs.run_audio_file_job(job_id)
    finally:
        try:
            (RECORDINGS_DIR / nom).unlink()
        except OSError:
            pass
        try:
            engine.dispose()
        except Exception:
            pass
    plafond = at.plafond_essais_bloc(audio_file_jobs.BLOC_RETRIES)
    assert len(appels) == plafond, (len(appels), plafond)


def test_contrat_ad3_echec_transitoire_epuise_au_niveau_bas_puis_succes_au_2e_passage(monkeypatch):
    """CONTRAT figé (AD-3) : le job de fond REJOUE un `ECHEC_TRANSCRIPTION` déjà épuisé
    au niveau bas (le message générique couvre aussi des échecs transitoires, rien ne
    les distingue d'un déterministe). Un bloc qui échoue `BLOC_MAX_TENTATIVES` fois
    (= un passage complet du niveau bas) puis réussit fait donc aboutir le job au
    2e passage. Le cas « toujours en échec » est couvert par l'invariant CR-20."""
    _reinit_db()
    monkeypatch.setattr(at, "BLOC_RETRY_PAUSE_S", 0)
    monkeypatch.setattr(audio_file_jobs, "BLOC_RETRY_DELAY_S", 0)
    monkeypatch.setattr(at, "_faster_whisper", lambda: object())
    monkeypatch.setattr(at, "_probe_duration_s", lambda content: 5.0)
    monkeypatch.setattr(
        at, "_decode_to_pcm16k", lambda content: np.zeros(16000 * 5, dtype=np.float32)
    )
    appels = []
    fautes = {"n": at.BLOC_MAX_TENTATIVES}

    def _ko_puis_ok(bloc):
        appels.append(1)
        if fautes["n"] > 0:
            fautes["n"] -= 1
            raise RuntimeError("echec transitoire")
        return "texte"

    monkeypatch.setattr(at, "_transcribe_pcm_sequential", _ko_puis_ok)
    nom = "ad3_contrat_test.wav"
    (RECORDINGS_DIR / nom).write_bytes(b"x")
    try:
        with SessionLocal() as db:
            job = AudioFileJob(session_token="tok-ad3", status="pending", filename=nom)
            db.add(job)
            db.commit()
            job_id = job.id
        audio_file_jobs.run_audio_file_job(job_id)
        with SessionLocal() as db:
            fin = db.get(AudioFileJob, job_id)
            statut, blocs, erreur = fin.status, list(fin.blocks or []), fin.error
    finally:
        try:
            (RECORDINGS_DIR / nom).unlink()
        except OSError:
            pass
        try:
            engine.dispose()
        except Exception:
            pass
    assert statut == "done", erreur
    assert blocs == ["texte"]
    assert len(appels) == at.BLOC_MAX_TENTATIVES + 1
