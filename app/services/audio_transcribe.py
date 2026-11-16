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

import collections
import contextlib
import contextvars
import io
import logging
import multiprocessing
import os
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from concurrent.futures.process import BrokenProcessPool

from app.services.priority_lock import FOND, LIVE, PriorityLock

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

# Filet « transcription fiable » : un bloc est tenté au plus BLOC_MAX_TENTATIVES
# fois AU TOTAL (pool compris) avant d'échouer ; la reprise par `start_index`
# fait le reste. Un retry réussi n'est pas un succès propre : il est compté
# dans `stats["blocs_rejoues"]` de `iter_transcribe_blocks`.
BLOC_MAX_TENTATIVES = 3
BLOC_RETRY_PAUSE_S = 2.0


def plafond_essais_bloc(bloc_retries: int) -> int:
    """Plafond réel d'essais d'un bloc : (bloc_retries + 1) x BLOC_MAX_TENTATIVES.
    `bloc_retries` (nouveaux essais du job de fond) vit dans `audio_file_jobs` ;
    cette source unique évite de réécrire « 9 » à la main."""
    return (bloc_retries + 1) * BLOC_MAX_TENTATIVES


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
# Sérialise la prise multi-permis d'un job (sinon interblocage partiel, EH-1).
_ACQUISITION_PERMIS_LOCK = threading.Lock()

# Story 2.2 (2026-11-16, entretien 23, ~105 min) : 8 workers x ~1,58 Go privés
# (mesuré : `medium`, compute_type int8) sur une machine déjà chargée = mémoire
# pleine, pagination continue, 0 bloc en 45 min. Les permis ci-dessus ne
# comptent que des PROCESSUS ; avant de démarrer le pool, `_drain_parallel`
# réduit donc aussi le nombre de workers à ce que la mémoire DISPONIBLE permet :
# (disponible - MEMOIRE_MARGE_OCTETS) // estimation par worker.
# Estimation par taille de modèle (Gio, privé par processus, modèle chargé +
# inférence d'un bloc de 60 s) : `medium` mesuré, les autres extrapolés du
# rapport des poids ; un nom inconnu prend l'estimation la plus prudente.
# `WHISPER_MEMOIRE_PAR_WORKER_MO` l'écrase (machine mesurée autrement).
_MEMOIRE_PAR_MODELE_GIO = {
    "tiny": 0.4, "base": 0.5, "small": 0.9, "medium": 1.6,
    "large-v1": 3.2, "large-v2": 3.2, "large-v3": 3.2, "large": 3.2,
}
MEMOIRE_PAR_WORKER_OCTETS = (
    _entier_env("WHISPER_MEMOIRE_PAR_WORKER_MO", 0) * 1024 * 1024
    or int(_MEMOIRE_PAR_MODELE_GIO.get(MODEL_SIZE, 3.2) * 1024 ** 3)
)
# Laissé libre pour le parent (PCM décodé : ~690 Mo pour 3 h), l'OS et le reste
# de l'appli : sans marge, le dernier worker admis fait déjà paginer.
MEMOIRE_MARGE_OCTETS = 1024 ** 3

# Attente max (s) du RÉSULTAT d'un bloc par `_drain_parallel` (avant : aucune,
# `futures.pop(index).result()` nu, et seule la péremption de 3 h finissait par
# parler). Un bloc nominal dure 60 s d'audio x RTF ~3,6 par worker bridé = ~216 s
# au pire (cf. SEGMENT_LOCK_TIMEOUT_S) ; le premier bloc d'un worker paie en plus
# le chargement du modèle (minutes sur une machine chargée). 900 s = ~4x le pire
# bloc nominal, chargement compris, et moitié de BACKGROUND_LOCK_TIMEOUT_S (1800 s,
# l'attente bornée sœur du chemin séquentiel) : au-delà, le bloc ne progresse plus
# (pagination, worker figé) — l'incident aurait échoué à 15 min au lieu de 3 h.
BLOC_RESULTAT_TIMEOUT_S = float(_entier_env("WHISPER_BLOC_TIMEOUT_S", 900))

# Story 3.2 (C-1, AC3.2.1) : le délai d'un bloc NE COURT QU'UNE FOIS le modèle
# prêt. Avant, les 900 s ci-dessus incluaient le chargement ; sous charge (12-15
# min de chargement observés, entretien 23) le 1er bloc de chaque worker expirait,
# la relance tuait les workers et repayait le chargement : le job pouvait ne
# jamais avancer. Le chargement a donc SA borne, généreuse : 1800 s = 2x le pire
# chargement observé sous charge, et égale à BACKGROUND_LOCK_TIMEOUT_S (l'autre
# attente longue légitime du fond). Au-delà, `TranscriptionBloqueeError` avec un
# message propre au chargement. `WHISPER_CHARGEMENT_TIMEOUT_S` l'écrase.
CHARGEMENT_MODELE_TIMEOUT_S = float(_entier_env("WHISPER_CHARGEMENT_TIMEOUT_S", 1800))

# Story 3.2 (D-10 a, AC3.2.2) : attente max (s), côté FOND, d'un chargement du
# modèle du serveur DÉJÀ en cours dans un autre fil (préchauffage). Au-delà :
# `TranscriptionChargementEnCoursError` (« occupé » réessayable), que le job
# rejoue avec battement sans consommer d'essai, borné au total par
# CHARGEMENT_MODELE_TIMEOUT_S. 60 s = un battement : le job signale qu'il vit au
# moins à ce rythme, jamais une attente silencieuse sans borne.
ATTENTE_CHARGEMENT_FOND_S = float(_entier_env("WHISPER_ATTENTE_CHARGEMENT_FOND_S", 60))

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
# à délai court (plan `RETRY_PLAN` de rec_retry.js, valeurs à y relire) avant
# d'abandonner
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
#
# DÉFAUT 150 s (relevé de 60 s, revue 2026-10-19) : le verrou est NON préemptif,
# et un bloc de fond dure 60 s d'audio x RTF ~3,6 (medium int8 CPU, worker
# bridé, cf. FILE_BLOCK_S) = ~216 s au pire. À 60 s, le direct recevait un 503
# alors que le verrou n'avait jamais été libre. 150 s couvre un bloc de fond en
# cours + une marge. `retry_after_s` (renvoyé en `Retry-After`) n'est PLUS ce
# délai : c'est `estimer_retry_after()` (file réelle x durée récente).
SEGMENT_LOCK_TIMEOUT_S = float(os.environ.get("WHISPER_SEGMENT_LOCK_TIMEOUT_S", "150"))

# AD-2 : l'attente de permis du FOND n'a (volontairement) pas d'échéance — une
# attente légitime sous charge ne doit pas devenir un échec. Sans signe de vie,
# un job bloqué plus longtemps que le seuil de péremption paraît mort et un clic
# « Reprendre » le double. Le job de fond dépose ici un rappel (son battement) ;
# la boucle d'attente l'appelle toutes les BATTEMENT_ATTENTE_S secondes (horloge
# monotone), jamais à chaque tour de 0,05 s.
BATTEMENT_ATTENTE = contextvars.ContextVar("battement_attente", default=None)
BATTEMENT_ATTENTE_S = 60.0


def _signaler_vivant() -> None:
    rappel = BATTEMENT_ATTENTE.get()
    if rappel is None:
        return
    try:
        rappel()
    except Exception:   # un battement raté ne doit jamais casser l'attente
        logger.warning("Battement d'attente de permis impossible.", exc_info=True)

# Estimation honnête du `retry_after_s` d'un refus « occupé » (2026-10-19) :
# avant, la constante SEGMENT_LOCK_TIMEOUT_S, sans rapport avec la file réelle.
# Désormais : requêtes en attente du verrou x durée moyenne RÉCENTE d'une
# transcription directe, bornée à [RETRY_AFTER_MIN_S, RETRY_AFTER_MAX_S].
RETRY_AFTER_MIN_S = 10.0
RETRY_AFTER_MAX_S = 240.0
DUREE_PAR_DEFAUT_S = 30.0  # tant qu'aucune transcription directe n'a été mesurée
_STATS_LOCK = threading.Lock()
_ATTENTE_LIVE = 0
_DUREES_RECENTES: collections.deque = collections.deque(maxlen=8)


@contextlib.contextmanager
def _compte_attente():
    """Compte une requête directe EN ATTENTE d'une ressource (verrou modèle ou
    sémaphore de workers) — chemins séquentiel ET parallèle — pour l'estimation."""
    global _ATTENTE_LIVE
    with _STATS_LOCK:
        _ATTENTE_LIVE += 1
    try:
        yield
    finally:
        with _STATS_LOCK:
            _ATTENTE_LIVE -= 1


def _noter_duree(duree_s: float) -> None:
    with _STATS_LOCK:
        _DUREES_RECENTES.append(float(duree_s))


def estimer_retry_after() -> float:
    """Délai honnête avant de rejouer : (en attente + 1 en cours) x durée moyenne
    récente, borné 10..240 s."""
    with _STATS_LOCK:
        attente = _ATTENTE_LIVE
        moy = (sum(_DUREES_RECENTES) / len(_DUREES_RECENTES)) if _DUREES_RECENTES else DUREE_PAR_DEFAUT_S
    return max(RETRY_AFTER_MIN_S, min(RETRY_AFTER_MAX_S, (attente + 1) * moy))

# Attente max (s) du FOND sur `_MODEL_LOCK` (30 min par défaut) : avant, sans
# borne et retour ignoré. Échec => TranscriptionError (le job se relance).
BACKGROUND_LOCK_TIMEOUT_S = float(os.environ.get("WHISPER_BACKGROUND_LOCK_TIMEOUT_S", "1800"))

# Parallélisme du FOND (import de fichier) tant qu'un direct est en cours ou en
# attente (cf. `direct_actif`). Le pool de processus n'utilise PAS
# `_MODEL_LOCK` : sans ce plafond, jusqu'à 8 processus se disputent le CPU avec
# le segment du direct de l'entretien suivant, qui se dégrade (RTF > 1). Choix
# minimal sans refonte : le fond ne garde en vol que ce nombre de blocs (défaut
# 1) et cède donc entre deux blocs ; un bloc déjà parti finit (non préemptif).
BACKGROUND_WORKERS_WHEN_LIVE = _entier_env("WHISPER_BACKGROUND_WORKERS_WHEN_LIVE", 1)

_LIVE_COMPTEUR = 0
_LIVE_COMPTEUR_LOCK = threading.Lock()


def direct_actif() -> bool:
    """Vrai si une transcription du DIRECT est en cours ou attend le verrou."""
    return _LIVE_COMPTEUR > 0 or _MODEL_LOCK.attente_live()

_model = None
# Le singleton est partagé par plusieurs threads : `/audio/transcribe-segment`
# (un `to_thread` par requête, plusieurs onglets possibles) et, depuis
# l'import de fichier bloc par bloc, la tâche de fond `run_audio_file_job`.
# Le chemin parallèle isole les modèles dans des PROCESSUS séparés justement
# parce qu'un modèle ne se partage pas ; ce verrou tient la même garantie pour
# le chemin séquentiel (revue adversariale 2026-07-27) — il sérialise deux
# transcriptions concurrentes au lieu de les laisser se marcher dessus.
_MODEL_LOCK = PriorityLock()


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


class TranscriptionFondBusyError(TranscriptionError):
    """Verrou de FOND non obtenu après BACKGROUND_LOCK_TIMEOUT_S (30 min) : NON
    réessayée par `audio_file_jobs._est_reessayable` (`plafond_essais_bloc` x 1800 s
    par bloc sinon)."""


class TranscriptionBloqueeError(TranscriptionError):
    """Aucun résultat d'un bloc en BLOC_RESULTAT_TIMEOUT_S (story 2.2) : les workers
    sont arrêtés et le job échoue avec un message proposant la relance. NON
    réessayée par `audio_file_jobs._est_reessayable` (message ≠ ECHEC_TRANSCRIPTION) :
    relancer aussitôt sur une machine qui pagine reproduirait l'attente."""


def message_transcription_bloquee(delai_s: float) -> str:
    return (
        f"La transcription n'avance plus : un bloc n'a rendu aucun résultat en "
        f"{max(1, round(delai_s / 60))} min "
        "(mémoire de la machine probablement saturée). Relancez la transcription — "
        "les blocs déjà transcrits sont conservés, la reprise repart de là."
    )


MESSAGE_MEMOIRE_INSUFFISANTE = (
    "Mémoire insuffisante pour transcrire maintenant : fermez des applications "
    "puis relancez — rien n'est perdu."
)


class TranscriptionMemoireInsuffisanteError(TranscriptionError):
    """Pas même un worker ne tient en mémoire ET le modèle du serveur n'est pas
    chargé (story 2.2) : le charger maintenant ajouterait ~1,6 Gio pile quand la
    mémoire manque. Échec immédiat, aucun chargement tenté ; NON réessayée."""


class TranscriptionAbandonnee(Exception):
    """Le client est parti et plus personne n'attend ce segment : transcription
    sautée après obtention du verrou (rendu aussitôt)."""


class TranscriptionBusyError(TranscriptionError):
    """Le modèle Whisper est occupé par une autre transcription (verrou
    `_MODEL_LOCK`) depuis plus de `SEGMENT_LOCK_TIMEOUT_S` — distinct d'un
    échec : le segment n'a pas été tenté, il doit être REJOUÉ. `retry_after_s`
    porte le délai suggéré, retranscrit par la route en en-tête `Retry-After`
    (`code: "busy"`, HTTP 503) pour que le client sache attendre plutôt que
    deviner via un timeout aveugle."""

    retry_after_s: float = 0.0


class TranscriptionChargementEnCoursError(TranscriptionBusyError):
    """Le modèle du serveur est en cours de chargement dans un autre fil (story
    3.2, D-10 a) : rien n'a été tenté, à rejouer. Sous-classe de
    `TranscriptionBusyError` : le direct garde son refus « occupé » ; le job de
    fond la rejoue sans consommer d'essai (borne : CHARGEMENT_MODELE_TIMEOUT_S)."""


def message_chargement_trop_long(delai_s: float) -> str:
    return (
        f"Le modèle de transcription n'a pas pu être prêt en "
        f"{max(1, round(delai_s / 60))} min (machine très chargée). Relancez la "
        "transcription plus tard — les blocs déjà transcrits sont conservés."
    )


def corps_erreur_transcription(exc: TranscriptionError) -> dict:
    """Corps JSON d'un refus 422 de transcription (routes segment et dictée).

    `code: "transient"` ne qualifie QUE l'échec de transcription lui-même
    (`ECHEC_TRANSCRIPTION` : worker tué par la mémoire, erreur Whisper sur ce
    chunk) : l'audio est intact et le même segment peut être REJOUÉ. Tout autre
    refus (fichier illisible, aucun enregistrement, modèle absent…) est
    définitif pour ces octets et reste sans code. Les sous-classes
    (`NoSpeechError`, `TranscriptionBusyError`) ont leurs propres codes, posés
    par les routes. Le client ne doit jamais deviner la rejouabilité d'après le
    texte français."""
    corps = {"error": str(exc)}
    if type(exc) is TranscriptionError and str(exc) == ECHEC_TRANSCRIPTION:
        corps["code"] = "transient"
    return corps


def _faster_whisper():
    try:
        import faster_whisper

        return faster_whisper
    except ModuleNotFoundError:
        return None


def is_available() -> bool:
    """Vrai si la transcription locale est possible (paquet installé)."""
    return _faster_whisper() is not None


_MODEL_LOAD_LOCK = threading.Lock()  # chargement seul ; `_MODEL_LOCK` sérialise l'inférence


# Borne (secondes) de l'attente d'un chargement DÉJÀ en cours chez un autre fil,
# posée par le chemin direct autour de son appel à `_get_model()` : ce fil tient
# `_MODEL_LOCK` et ne doit pas rendre le refus « occupé » inatteignable pendant un
# chargement à froid de plusieurs minutes. `None` = attente illimitée (warm-up,
# jobs de fond). Un ContextVar, pas un argument : `_get_model()` reste appelable
# sans paramètre (les doublures de tests le remplacent par un `lambda:`).
_ATTENTE_CHARGEMENT_MAX: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "attente_chargement_max", default=None
)

# Story 3.2 (C-2) : rappel posé par `_sequentiel_borne`, appelé par
# `_transcribe_pcm_sequential` une fois `_MODEL_LOCK` obtenu ET le modèle prêt —
# le délai du bloc ne court qu'à partir de là. Il lève `TranscriptionAbandonnee`
# si le demandeur a déjà renoncé (le bloc n'est alors pas transcrit pour rien).
_SIGNAL_MODELE_PRET: contextvars.ContextVar = contextvars.ContextVar(
    "signal_modele_pret", default=None
)


def _get_model():
    """Charge le modèle une seule fois, même si le warm-up (démarrage ou
    `POST /audio/warm-up`) et une transcription le demandent en même temps :
    `medium` pèse ~1,5 Go, deux chargements concurrents doublent la mémoire.
    Si `_ATTENTE_CHARGEMENT_MAX` est posé, l'attente d'un chargement en cours est
    bornée : au-delà, refus « occupé » rejouable (comme l'attente de `_MODEL_LOCK`)."""
    global _model
    if _model is None:
        borne = _ATTENTE_CHARGEMENT_MAX.get()
        if not _MODEL_LOAD_LOCK.acquire(timeout=-1 if borne is None else borne):
            exc = TranscriptionChargementEnCoursError("Chargement du modèle en cours, réessayez.")
            exc.retry_after_s = estimer_retry_after()
            raise exc
        try:
            if _model is None:
                faster_whisper = _faster_whisper()
                _model = faster_whisper.WhisperModel(
                    MODEL_SIZE, device="cpu", compute_type="int8"
                )
        finally:
            _MODEL_LOAD_LOCK.release()
    return _model


_GET_MODEL_REEL = _get_model   # cf. `_garde_memoire_sequentielle`


def warm_up() -> None:
    """Charge le modèle en mémoire dès le démarrage du serveur, pour que le
    premier enregistrement réel de l'utilisateur n'en paie pas le coût.
    Lancé depuis le 2026-11-05 dans un fil d'arrière-plan (`prechauffage-whisper`,
    app/main.py) : il ne bloque PAS l'ouverture du port.

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


# Modèle du PROCESSUS WORKER (story 2.2) : chargé une fois par processus du pool,
# puis réutilisé pour tous ses blocs. Avant, `_transcribe_pcm_chunk` construisait
# un `WhisperModel` À CHAQUE bloc de 60 s (~105 chargements pour 105 min). Reste
# `None` dans le processus serveur : le chemin séquentiel a son singleton `_model`.
_worker_model = None
# Story 3.2 (C-1) : compteur PARTAGÉ (multiprocessing.Value) des workers dont le
# modèle est prêt, transmis par l'`initializer` du pool RÉEL. Le parent ne fait
# courir le délai d'un bloc qu'une fois tous les workers attendus prêts.
_workers_prets = None
# Revue 3.2 : compteur PARTAGÉ des workers dont le chargement a COMMENCÉ (servis).
_workers_servis = None


def _init_worker(compteur, servis=None) -> None:
    """`initializer` du pool réel : mémorise les compteurs partagés (modèles prêts,
    chargements commencés)."""
    global _workers_prets, _workers_servis
    _workers_prets = compteur
    _workers_servis = servis


def _ajouter(compteur, delta: int) -> None:
    if compteur is not None:
        with compteur.get_lock():
            compteur.value += delta


def _modele_worker(threads: int):
    """Modèle du processus courant, chargé au premier bloc et gardé ensuite —
    cache de niveau module, donc propre à chaque processus `spawn`. Pas
    d'`initializer` de pool : les doublures de `ProcessPoolExecutor` des tests
    n'acceptent pas ce paramètre, et charger au premier bloc revient au même
    (une fois par processus) sans casser le pool si le chargement échoue — l'échec
    remonte alors comme celui d'un bloc, avec ses tentatives."""
    global _worker_model
    if _worker_model is None:
        faster_whisper = _faster_whisper()
        _ajouter(_workers_servis, 1)        # chargement commencé
        try:
            _worker_model = faster_whisper.WhisperModel(
                MODEL_SIZE, device="cpu", compute_type="int8", cpu_threads=threads
            )
        except BaseException:
            _ajouter(_workers_servis, -1)   # échec : la tâche suivante recommencera
            raise
        _ajouter(_workers_prets, 1)
    return _worker_model


# Story 3.2 (C-4, AC3.2.4) : hallucinations de Whisper sur le silence. Constaté :
# « Merci d'avoir regardé cette vidéo ! » x7 conservé, et un bloc d'hallucinations
# neutralise l'alerte « aucune parole ». Trois parades, toutes appliquées par
# `_texte_segments` aux TROIS sites d'appel (worker, séquentiel, direct) :
# 1. `condition_on_previous_text=False` (`OPTIONS_ANTI_HALLUCINATION`) : un texte
#    halluciné n'amorce plus le suivant (cause des boucles répétitives).
# 2. Segment écarté si no_speech_prob > SEUIL_NO_SPEECH ET avg_logprob <
#    SEUIL_LOGPROB : MÊMES seuils (0,6 / -1,0) et même conjonction que
#    faster-whisper (`no_speech_threshold`, `log_prob_threshold`,
#    transcribe.py:274-277 et 1217-1223 du paquet installé), qui ne l'applique
#    qu'à la FENÊTRE de 30 s entière ; ici, segment par segment.
# 3. Phrases types de silence (FR/EN, `_PHRASES_SILENCE`, phrase entière)
#    écartées seulement si RÉPÉTÉES dans le bloc ou PEU SÛRES (no_speech_prob >=
#    SEUIL_PHRASE_NO_SPEECH ou avg_logprob < SEUIL_PHRASE_LOGPROB) : une vraie
#    phrase prononcée avec assurance reste, même seule. Puis les suites d'au
#    moins RUN_REPETITION_MIN segments identiques sont repliées.
# Un bloc qui ne contient que cela rend "" : le direct lève alors `NoSpeechError`
# (422 `no_speech`, sémantique inchangée) et le fond compte un bloc vide.
OPTIONS_ANTI_HALLUCINATION = {"condition_on_previous_text": False}
SEUIL_NO_SPEECH = 0.6
SEUIL_LOGPROB = -1.0
SEUIL_PHRASE_NO_SPEECH = 0.3
SEUIL_PHRASE_LOGPROB = -0.7
# Revue 3.2 : PHRASES ENTIÈRES (après normalisation), jamais un préfixe — « merci
# de votre attention, on passe à la suite » est de la parole. Une phrase type
# n'est écartée que PEU SÛRE ou RÉPÉTÉE dans le bloc (signature d'une boucle
# d'hallucination) ; une phrase type isolée et sûre reste (vraie parole).
_PHRASES_SILENCE = frozenset({
    "merci d'avoir regardé cette vidéo",
    "merci d'avoir regardé",
    "merci de votre attention",
    "merci à tous",
    "sous-titres réalisés par la communauté d'amara.org",
    "sous-titrage st' 501",
    "sous-titrage société radio-canada",
    "abonnez-vous",
    "thank you for watching",
    "thanks for watching",
})
# Une suite d'au moins RUN_REPETITION_MIN segments identiques consécutifs est
# repliée en un seul (boucle de décodage) ; deux « Oui. » d'affilée (deux
# interlocuteurs) restent tous les deux.
RUN_REPETITION_MIN = 3


def _norm_phrase(texte: str) -> str:
    return " ".join(texte.replace("’", "'").lower().split()).strip(" .,;:!?…\"'«»")


def _est_phrase_silence(texte: str) -> bool:
    return _norm_phrase(texte) in _PHRASES_SILENCE


def _texte_segments(segments) -> str:
    """Texte d'un bloc, hallucinations de silence écartées (cf. ci-dessus).
    Tolère des segments sans `no_speech_prob`/`avg_logprob` (doublures)."""
    candidats = []
    for seg in segments:
        texte = (seg.text or "").strip()
        if not texte:
            continue
        nsp = getattr(seg, "no_speech_prob", 0.0) or 0.0
        logp = getattr(seg, "avg_logprob", 0.0) or 0.0
        if nsp > SEUIL_NO_SPEECH and logp < SEUIL_LOGPROB:
            continue
        candidats.append((texte, nsp, logp))
    occurrences = collections.Counter(_norm_phrase(t) for t, _, _ in candidats)
    retenus = [
        t for t, nsp, logp in candidats
        if not (_est_phrase_silence(t) and (
            occurrences[_norm_phrase(t)] >= 2
            or nsp >= SEUIL_PHRASE_NO_SPEECH or logp < SEUIL_PHRASE_LOGPROB))
    ]
    gardes: list[str] = []
    i = 0
    while i < len(retenus):
        j = i
        while j + 1 < len(retenus) and _norm_phrase(retenus[j + 1]) == _norm_phrase(retenus[i]):
            j += 1
        longueur = j - i + 1
        gardes.extend(retenus[i:i + 1] if longueur >= RUN_REPETITION_MIN else retenus[i:j + 1])
        i = j + 1
    return " ".join(gardes).strip()


def _transcribe_pcm_chunk(args: tuple) -> str:
    """Transcrit un tronçon PCM déjà décodé — fonction de niveau module
    (requis par `ProcessPoolExecutor` sous Windows, qui doit pouvoir la
    pickler). Chaque processus a son propre modèle (aucun partage possible
    entre processus séparés), chargé UNE fois par processus (`_modele_worker`)."""
    pcm, threads = args
    model = _modele_worker(threads)
    segments, _info = model.transcribe(
        pcm, language="fr", beam_size=BEAM_SIZE, vad_filter=True, **OPTIONS_ANTI_HALLUCINATION
    )
    return _texte_segments(segments)


def _transcribe_parallel(content: bytes, duration_s: float, priority: int = FOND,
                         abandonne=None) -> str:
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
    # `abandonne` (client du direct parti, plus aucun demandeur) : testé avant
    # de lancer le travail puis entre chaque tronçon — on ne transcrit pas en
    # entier un long segment dont plus personne n'attend le texte.
    def _verifier_abandon() -> None:
        if abandonne is not None and abandonne():
            raise TranscriptionAbandonnee()

    _verifier_abandon()

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
            # `priority` en kwarg et seulement hors défaut (compat des doubles de test).
            drain_kw = {} if priority == FOND else {"priority": priority}
            for i, text in _drain_parallel(chunks, index, total, workers, **drain_kw):
                parts[i] = text
                index = i + 1
                _verifier_abandon()
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
        _verifier_abandon()
        parts[index] = _transcribe_pcm_sequential(chunks[index], priority)
        index += 1
    return _merge_overlapping_texts([parts[i] for i in range(total)])


def _transcribe_pcm_sequential(pcm, priority: int = FOND) -> str:
    """Transcrit un PCM déjà décodé avec le modèle singleton du processus
    courant (pas de sous-processus) — chemin d'un fichier tenant en UN bloc,
    où démarrer un `ProcessPoolExecutor` coûterait plus cher que le gain."""
    # FOND : le verrou n'est tenu que le temps d'UN bloc (FILE_BLOCK_S d'audio) ;
    # un segment du direct en attente passe devant au bloc suivant.
    # `priority=LIVE` : tronçon d'un segment du direct long (`transcribe_audio`
    # -> `_transcribe_parallel`) -> même attente bornée et même refus "occupé"
    # que le chemin court ; FOND (défaut) : attente sans borne.
    # LIMITE CONNUE : le délai court PAR tronçon (segment live long en repli
    # séquentiel : n tronçons => attente cumulée jusqu'à n x SEGMENT_LOCK_TIMEOUT_S).
    if priority == LIVE:
        with _compte_attente():
            obtenu = _MODEL_LOCK.acquire(timeout=SEGMENT_LOCK_TIMEOUT_S, priority=LIVE)
        if not obtenu:
            exc = TranscriptionBusyError("Transcription momentanément occupée, réessayez.")
            exc.retry_after_s = estimer_retry_after()
            raise exc
    else:
        # Pas de battement ici (A3) : l'attente est bornée par BACKGROUND_LOCK_TIMEOUT_S
        # (1800 s) et le seuil de péremption par défaut du job est de 3 h (10800 s,
        # AUDIO_FILE_JOB_STALE_AFTER_S) : cette attente ne peut pas le faire déclarer périmé.
        if not _MODEL_LOCK.acquire(timeout=BACKGROUND_LOCK_TIMEOUT_S, priority=FOND):
            raise TranscriptionFondBusyError(ECHEC_TRANSCRIPTION)
    try:
        # D-10 a : un chargement DÉJÀ en cours ailleurs (préchauffage) n'est plus
        # attendu sans borne par le fond : « occupé » réessayable au-delà de
        # ATTENTE_CHARGEMENT_FOND_S. Le direct garde sa propre borne (posée par lui).
        jeton = None
        if _ATTENTE_CHARGEMENT_MAX.get() is None:
            jeton = _ATTENTE_CHARGEMENT_MAX.set(
                SEGMENT_LOCK_TIMEOUT_S if priority == LIVE else ATTENTE_CHARGEMENT_FOND_S
            )
        try:
            model = _get_model()
        finally:
            if jeton is not None:
                _ATTENTE_CHARGEMENT_MAX.reset(jeton)
        # C-2 : verrou obtenu ET modèle prêt — le délai du bloc commence ici ;
        # un demandeur qui a déjà abandonné fait sauter le bloc (verrou rendu).
        signal = _SIGNAL_MODELE_PRET.get()
        if signal is not None:
            signal()
        segments, _info = model.transcribe(
            pcm, language="fr", beam_size=BEAM_SIZE, vad_filter=True, **OPTIONS_ANTI_HALLUCINATION
        )
        return _texte_segments(segments)
    finally:
        _MODEL_LOCK.release()


# Vraie fonction, capturée à la définition (cf. `_POOL_REEL`) : elle seule émet
# `_SIGNAL_MODELE_PRET`.
_SEQUENTIEL_REEL = _transcribe_pcm_sequential


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


def memoire_disponible() -> int | None:
    """Mémoire physique DISPONIBLE (octets), ou `None` si indéterminable (le garde
    mémoire s'efface alors : comportement d'avant). Sans psutil (pas une dépendance
    du projet) : `GlobalMemoryStatusEx` sous Windows, `MemAvailable` de
    /proc/meminfo sous Linux, `os.sysconf` ailleurs. Remplaçable dans les tests."""
    try:
        if os.name == "nt":
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            etat = _MemoryStatusEx()
            etat.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(etat)):
                return int(etat.ullAvailPhys)
            return None
        with open("/proc/meminfo", "r", encoding="ascii") as f:
            for ligne in f:
                if ligne.startswith("MemAvailable:"):
                    return int(ligne.split()[1]) * 1024
    except Exception:
        logger.debug("Mesure de la mémoire disponible impossible", exc_info=True)
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None


def _workers_selon_memoire(demandes: int) -> int:
    """Workers que la mémoire disponible permet de démarrer (0 = aucun : repli
    séquentiel borné si le modèle du serveur est chargé, sinon échec immédiat).

    RISQUE RÉSIDUEL assumé : deux pools démarrés au même instant lisent la même
    mémoire libre avant que l'un ou l'autre ait chargé ses modèles, et peuvent
    ensemble dépasser. Un compteur de réservations le fermerait mais compterait
    deux fois un pool déjà chargé (sa mémoire est alors déjà retirée du
    « disponible ») ; `GLOBAL_MAX_PARALLEL_WORKERS` borne toujours la somme."""
    dispo = memoire_disponible()
    if dispo is None:
        return demandes
    permis = max(0, int((dispo - MEMOIRE_MARGE_OCTETS) // max(1, MEMOIRE_PAR_WORKER_OCTETS)))
    retenus = min(demandes, permis)
    logger.info(
        "Transcription parallèle : %d worker(s) demandé(s), %d retenu(s) "
        "(mémoire disponible %.1f Go, ~%.1f Go par worker, marge %.1f Go).",
        demandes, retenus, dispo / 1024 ** 3, MEMOIRE_PAR_WORKER_OCTETS / 1024 ** 3,
        MEMOIRE_MARGE_OCTETS / 1024 ** 3,
    )
    return retenus


# Classe réelle, capturée à l'import : le garde mémoire ne vaut que pour de VRAIS
# processus qui chargent un modèle. Les doublures de `ProcessPoolExecutor` des tests
# (qui ne chargent rien) n'en dépendent donc pas de la mémoire libre de la machine.
_POOL_REEL = ProcessPoolExecutor


def _pool_reel() -> bool:
    return isinstance(ProcessPoolExecutor, type) and issubclass(ProcessPoolExecutor, _POOL_REEL)


def _attendre_resultat(future, index: int, total: int, chargement=None) -> str:
    """Résultat d'un bloc, attendu au plus BLOC_RESULTAT_TIMEOUT_S, avec un battement
    du job toutes les BATTEMENT_ATTENTE_S tant que l'attente dure (sans lui, un bloc
    lent mais vivant laissait le job sans signe de vie jusqu'au bloc suivant).
    Au-delà : `TranscriptionBloqueeError`. Une doublure de test qui n'est pas un
    `Future` (déjà résolue, sans `timeout`) est lue directement.

    `chargement` (story 3.2, C-1/C-2) : appelé à chaque réveil, vrai tant qu'un
    modèle attendu se charge encore (ou que le verrou est attendu) — l'échéance du
    bloc est alors repoussée : son délai ne court qu'une fois le modèle prêt. Il
    lève lui-même `TranscriptionBloqueeError` au-delà de la borne du chargement."""
    if not isinstance(future, Future):
        return future.result()
    echeance = time.monotonic() + BLOC_RESULTAT_TIMEOUT_S
    while True:
        if chargement is not None and not future.done() and chargement():
            echeance = max(echeance, time.monotonic() + BLOC_RESULTAT_TIMEOUT_S)
        reste = echeance - time.monotonic()
        if reste <= 0:
            if future.done():
                return future.result()
            logger.error(
                "Bloc %d/%d sans résultat après %.0f s : workers arrêtés, transcription en échec.",
                index + 1, total, BLOC_RESULTAT_TIMEOUT_S,
            )
            raise TranscriptionBloqueeError(message_transcription_bloquee(BLOC_RESULTAT_TIMEOUT_S))
        try:
            return future.result(timeout=min(reste, BATTEMENT_ATTENTE_S))
        except FuturesTimeoutError:
            if future.done():
                raise      # TimeoutError levée PAR la tâche elle-même, pas par l'attente
            _signaler_vivant()


def _arreter_workers(executor, futures: dict) -> None:
    """Arrête net les workers d'un pool dont un bloc ne rend plus rien : annule les
    blocs en file et termine les processus (sinon `shutdown(wait=True)` à la
    sortie du `with` attendrait le worker figé indéfiniment, et ses processus
    resteraient orphelins). `terminate_workers` existe depuis Python 3.14."""
    for f in futures.values():
        f.cancel()
    # Instantané AVANT l'arrêt : `shutdown` oublie ses processus, et
    # `terminate_workers` ne les attend pas — vu au test sur pool réel, le worker
    # était encore vivant à la sortie du `with`.
    processus = list((getattr(executor, "_processes", None) or {}).values())
    terminer = getattr(executor, "terminate_workers", None)
    if terminer is not None:
        terminer()
    else:   # Python < 3.14 (la CI tourne en 3.12)
        arreter = getattr(executor, "shutdown", None)
        if arreter is not None:
            arreter(wait=False, cancel_futures=True)
        for p in processus:
            p.terminate()
    for p in processus:
        p.join(5)
        if p.is_alive():
            p.kill()
            p.join(5)


def _sequentiel_borne(pcm, priority: int, index: int, total: int) -> str:
    """Un bloc sur le modèle du serveur, dans un fil dédié, attendu comme un bloc du
    pool (`_attendre_resultat` : borne BLOC_RESULTAT_TIMEOUT_S + battement). Au
    délai dépassé le fil n'est pas tuable : il finit son bloc, dont le texte est
    jeté, puis rend `_MODEL_LOCK`.

    Story 3.2 (C-2, C-10) : le délai du bloc ne court qu'une fois le verrou obtenu
    ET le modèle prêt (`_SIGNAL_MODELE_PRET`) ; l'attente du verrou garde sa borne
    (BACKGROUND_LOCK_TIMEOUT_S / SEGMENT_LOCK_TIMEOUT_S, levée par le fil), le
    chargement la sienne (CHARGEMENT_MODELE_TIMEOUT_S en plus). Le fil s'exécute
    dans une COPIE du contexte (contextvars : bornes du direct, battement). Après
    abandon du demandeur, un fil qui obtient le verrou saute le bloc."""
    future: Future = Future()
    pret = threading.Event()
    abandon = threading.Event()
    debut = time.monotonic()
    borne_verrou = SEGMENT_LOCK_TIMEOUT_S if priority == LIVE else BACKGROUND_LOCK_TIMEOUT_S
    borne_totale = borne_verrou + CHARGEMENT_MODELE_TIMEOUT_S

    def _signal() -> None:
        pret.set()
        if abandon.is_set():
            raise TranscriptionAbandonnee()

    def _executer() -> None:
        _SIGNAL_MODELE_PRET.set(_signal)
        try:
            if priority == FOND:
                future.set_result(_transcribe_pcm_sequential(pcm))
            else:
                future.set_result(_transcribe_pcm_sequential(pcm, priority))
        except BaseException as exc:   # remonté au demandeur via le Future
            future.set_exception(exc)

    def _chargement() -> bool:
        if pret.is_set():
            return False
        if time.monotonic() - debut > borne_totale:
            logger.error("Bloc %d/%d : verrou/modèle non prêts en %.0f s.", index + 1, total, borne_totale)
            raise TranscriptionBloqueeError(message_chargement_trop_long(borne_totale))
        return True

    contexte = contextvars.copy_context()
    threading.Thread(target=contexte.run, args=(_executer,),
                     name="whisper-repli-borne", daemon=True).start()
    try:
        # Seule la vraie `_transcribe_pcm_sequential` émet le signal « prêt » : une
        # doublure de test (qui ne charge rien) garde la borne simple d'avant.
        return _attendre_resultat(
            future, index, total,
            chargement=_chargement if _transcribe_pcm_sequential is _SEQUENTIEL_REEL else None,
        )
    except BaseException:
        # Toute sortie du demandeur (délai, KeyboardInterrupt, GeneratorExit…) : le
        # fil qui obtiendrait le verrou ensuite saute le bloc (revue 3.2).
        abandon.set()
        raise


def _drain_parallel(blocks: list, start: int, total: int, n_workers: int,
                    priority: int = FOND):
    """Génère `(index, texte)` pour les blocs `[start, total)` via un pool de
    `n_workers` processus, dans l'ordre et par fenêtre glissante bornée.

    Laisse remonter `BrokenProcessPool` : c'est l'appelant qui décide du repli
    (palier suivant, puis séquentiel). Ne touche jamais aux blocs déjà rendus,
    donc une reprise à `start` ne re-transcrit rien.

    Acquiert `n_workers` permis sur `_GLOBAL_WORKERS_SEMAPHORE` avant de
    démarrer le pool, et les relâche à la sortie (y compris sur
    `BrokenProcessPool` ou toute autre exception) — plafond mémoire GLOBAL
    partagé entre tous les appels concurrents, cf. `GLOBAL_MAX_PARALLEL_WORKERS`."""
    if priority != LIVE and direct_actif():
        n_workers = max(1, min(n_workers, BACKGROUND_WORKERS_WHEN_LIVE))
    window = n_workers * 2  # assez pour ne jamais affamer les workers

    def _limite() -> int:
        # Fond : cède dès qu'un direct est actif (relu à chaque soumission).
        if priority != LIVE and direct_actif():
            return max(1, BACKGROUND_WORKERS_WHEN_LIVE)
        return window

    futures: dict[int, object] = {}
    acquis = 0
    # Jamais plus de permis que le plafond global : sinon attente éternelle.
    n_workers = max(1, min(n_workers, max(1, GLOBAL_MAX_PARALLEL_WORKERS)))
    if _pool_reel():
        # Story 2.2 : garde mémoire AVANT les permis et le pool (cf. MEMOIRE_PAR_WORKER_OCTETS).
        n_workers = _workers_selon_memoire(n_workers)
        if n_workers == 0:
            # Pas même un worker. Modèle du serveur DÉJÀ chargé : repli séquentiel
            # sur lui (aucune mémoire de plus), borné comme un bloc du pool. Pas
            # chargé : échec immédiat plutôt que charger ~1,6 Gio maintenant.
            if _model is None:
                logger.warning("Mémoire insuffisante pour un worker et modèle non chargé : échec immédiat.")
                raise TranscriptionMemoireInsuffisanteError(MESSAGE_MEMOIRE_INSUFFISANTE)
            for index in range(start, total):
                yield index, _sequentiel_borne(blocks[index], priority, index, total)
            return
    window = min(window, n_workers * 2)
    try:
        # Tout-ou-rien (EH-1) : un fond ne garde aucun permis en attendant les
        # autres. Un direct (LIVE) démarre avec les permis LIBRES (au moins 1) :
        # il dégrade son parallélisme au lieu de bloquer sur un jeu complet.
        deadline = time.monotonic() + SEGMENT_LOCK_TIMEOUT_S
        expire = False
        prochain_battement = time.monotonic() + BATTEMENT_ATTENTE_S
        with (_compte_attente() if priority == LIVE else contextlib.nullcontext()):
            while True:
                with _ACQUISITION_PERMIS_LOCK:     # section courte, jamais tenue en attente
                    for _ in range(n_workers):
                        if not _GLOBAL_WORKERS_SEMAPHORE.acquire(blocking=False):
                            break
                        acquis += 1                # compté dès qu'il est pris
                    if acquis == n_workers or (priority == LIVE and acquis >= 1):
                        break
                    while acquis:
                        _GLOBAL_WORKERS_SEMAPHORE.release()
                        acquis -= 1
                if priority == LIVE:
                    reste = deadline - time.monotonic()
                    if reste <= 0:
                        expire = True      # levé HORS du compteur d'attente (estimation)
                        break
                    time.sleep(min(0.05, reste))
                else:
                    if time.monotonic() >= prochain_battement:
                        _signaler_vivant()
                        prochain_battement = time.monotonic() + BATTEMENT_ATTENTE_S
                    time.sleep(0.05)
        if expire:
            exc = TranscriptionBusyError("Transcription momentanément occupée, réessayez.")
            exc.retry_after_s = estimer_retry_after()
            raise exc
        # Pool et fenêtre suivent les permis réellement tenus.
        n_workers = acquis
        window = n_workers * 2
        # `spawn` PARTOUT, jamais `fork` (défaut Linux jusqu'à Python 3.13) :
        # le parent a déjà des threads vivants (moniteur tqdm, feeder de
        # queue, uvicorn) et un enfant forké dans cet état se bloque sur un
        # verrou hérité. Vécu en CI le 2026-09-30 : pytest figé 6 h dans
        # `_drain_parallel` (runs #48-#50), jamais vu sous Windows qui spawne.
        contexte_mp = multiprocessing.get_context("spawn")
        pool_kw: dict = {}
        chargement = None
        if _pool_reel():
            # C-1 : chaque worker incrémente ce compteur une fois SON modèle chargé ;
            # tant qu'un worker attendu charge encore, le délai des blocs ne court pas
            # (borne propre du chargement : CHARGEMENT_MODELE_TIMEOUT_S).
            # Revue 3.2 : on compare aux workers qui ont RÉELLEMENT commencé à charger
            # (`servis`, incrémenté au début du chargement), pas à un nombre figé : un
            # worker ne charge qu'à sa 1re tâche, et `_limite()` (direct actif) peut ne
            # jamais en servir certains — l'échéance resterait repoussée à tort.
            prets = contexte_mp.Value("i", 0)
            servis = contexte_mp.Value("i", 0)
            debut_pool = time.monotonic()
            pool_kw = {"initializer": _init_worker, "initargs": (prets, servis)}

            def chargement() -> bool:
                # Repoussé tant qu'AUCUN modèle n'est prêt (démarrage du pool : le
                # processus n'a peut-être pas encore commencé à charger), puis
                # seulement tant qu'un chargement commencé n'a pas abouti. Un worker
                # jamais servi ne compte pas.
                if prets.value > 0 and prets.value >= servis.value:
                    return False
                if time.monotonic() - debut_pool > CHARGEMENT_MODELE_TIMEOUT_S:
                    logger.error("Modèle des workers non chargé en %.0f s.", CHARGEMENT_MODELE_TIMEOUT_S)
                    raise TranscriptionBloqueeError(
                        message_chargement_trop_long(CHARGEMENT_MODELE_TIMEOUT_S))
                return True
        with ProcessPoolExecutor(max_workers=n_workers, mp_context=contexte_mp,
                                 **pool_kw) as executor:
            def _submit(i: int) -> None:
                futures[i] = executor.submit(
                    _transcribe_pcm_chunk, (blocks[i], CPU_THREADS_PER_WORKER)
                )

            submitted = start
            while submitted < total and len(futures) < _limite():
                _submit(submitted)
                submitted += 1
            for index in range(start, total):
                try:
                    text = _attendre_resultat(futures.pop(index), index, total, chargement)
                except TranscriptionBloqueeError:
                    _arreter_workers(executor, futures)
                    raise
                while submitted < total and len(futures) < _limite():
                    _submit(submitted)
                    submitted += 1
                try:
                    yield index, text
                except GeneratorExit:
                    # Consommateur parti (exécution supplantée, abandon) : personne ne
                    # lira les blocs en vol — arrêter les workers au lieu d'attendre
                    # qu'ils finissent dans `shutdown(wait=True)`.
                    _arreter_workers(executor, futures)
                    raise
    finally:
        for _ in range(acquis):
            _GLOBAL_WORKERS_SEMAPHORE.release()


def _garde_memoire_sequentielle() -> None:
    """Story 3.2 (C-3) : avant un bloc sur le modèle du SERVEUR, si ce modèle n'est
    pas encore chargé et que la mémoire disponible ne couvre pas un modèle + la
    marge, échec immédiat (`TranscriptionMemoireInsuffisanteError`) plutôt que
    charger ~1,6 Gio maintenant. Ne vaut que pour la vraie chaîne de chargement
    (doublures de tests : rien n'est chargé, la mémoire de la machine n'y entre pas)."""
    if _model is not None or _get_model is not _GET_MODEL_REEL \
            or _transcribe_pcm_sequential is not _SEQUENTIEL_REEL:
        return
    if _MODEL_LOAD_LOCK.locked():
        # Revue 3.2 : un chargement est DÉJÀ en cours (préchauffage) — sa mémoire
        # est déjà engagée, ce n'est pas un manque : `_get_model` rendra un
        # « occupé » réessayable (TranscriptionChargementEnCoursError), pas un échec.
        return
    dispo = memoire_disponible()
    if dispo is not None and dispo - MEMOIRE_MARGE_OCTETS < MEMOIRE_PAR_WORKER_OCTETS:
        logger.warning("Mémoire insuffisante pour charger le modèle du serveur : échec immédiat.")
        raise TranscriptionMemoireInsuffisanteError(MESSAGE_MEMOIRE_INSUFFISANTE)


def _noter_rejeu(stats: dict | None, index: int) -> None:
    """Consigne qu'un bloc a nécessité plus d'une tentative (échec masqué)."""
    if stats is not None and index not in stats["blocs_rejoues"]:
        stats["blocs_rejoues"].append(index)


def _transcrire_bloc_sequentiel(blocs: list, index: int, total: int,
                                tentatives: dict, stats: dict | None) -> str:
    """Transcrit `blocs[index]` dans le processus courant, avec les tentatives
    restantes. Définitifs, jamais rejoués : `TranscriptionError` (dont
    `NoSpeechError`) et `OSError`. Épuisement : `TranscriptionError`.

    Story 3.2 (C-3) : job d'un seul bloc et repli final après casse du pool passent
    par `_sequentiel_borne` (borné, battement, délai démarré modèle prêt) et par le
    garde mémoire (`_garde_memoire_sequentielle`) — avant, attente sans borne ni
    battement, et chargement de ~1,6 Gio sans contrôle juste après un
    `BrokenProcessPool` (typiquement un manque de mémoire)."""
    _garde_memoire_sequentielle()
    while True:
        tentatives[index] = tentatives.get(index, 0) + 1
        try:
            return _sequentiel_borne(blocs[index], FOND, index, total)
        except (TranscriptionFondBusyError, TranscriptionBusyError,
                TranscriptionBloqueeError, TranscriptionMemoireInsuffisanteError):
            raise   # classes conservées : `_est_reessayable` en décide (AD-6, story 2.2/3.2)
        except Exception as exc:
            if (isinstance(exc, (TranscriptionError, OSError))
                    or tentatives[index] >= BLOC_MAX_TENTATIVES):
                logger.exception("Échec de la transcription d'un bloc audio")
                raise TranscriptionError(ECHEC_TRANSCRIPTION) from exc
            _noter_rejeu(stats, index)
            logger.warning(
                "Bloc %d/%d en échec (%s) — tentative %d/%d.",
                index + 1, total, type(exc).__name__,
                tentatives[index] + 1, BLOC_MAX_TENTATIVES,
            )
            time.sleep(BLOC_RETRY_PAUSE_S)


TOLERANCE_ECART_MIN_S = 2.0
TOLERANCE_ECART_RATIO = 0.02


def bilan_completude(
    duree_conteneur_s, duree_decodee_s, blocs, block_s, tranches_ignorees,
    blocs_rejoues, termine=True,
) -> dict:
    """Bilan de complétude d'une transcription de fichier — fonction PURE.

    Un job pouvait finir `done` avec des blocs vides ou des tranches ignorées
    sans que rien ne l'indique. Trois signes d'incomplétude : un bloc au texte
    vide, une tranche ignorée (`tranches_ignorees`, rangs, jamais un chemin), un
    conteneur plus LONG que l'audio décodé de plus de `max(2 s, 2 %)` (de l'audio
    a été perdu au décodage). L'inverse — décodé plus long que ce qu'annonce le
    conteneur — n'est PAS un signe d'incomplétude : la durée d'un `.webm` de
    MediaRecorder est souvent approximative, et alarmer à tort détruirait la
    confiance dans l'alerte ; l'écart reste exposé (`ecart_s` négatif).
    Un conteneur sans durée lisible rend l'écart non évaluable : `non_evaluable`,
    SAUF si un autre signe prouve déjà `incomplet` (incomplet l'emporte). Tant
    que le job n'est pas `termine` (échec, reprise en cours), les durées
    cumulées sont partielles : jamais « complet » sur cette base. Un bloc
    rejoué puis réussi (`blocs_rejoues`) est un échec masqué : exposé, mais il
    ne rend PAS incomplet. `block_s` n'entre dans aucun calcul : la couverture
    par simple découpage des blocs est tautologique, on ne la compte pas.

    Espaces d'index, à ne pas confondre : `blocs_vides` et `blocs_rejoues` sont
    des index 0-based dans `job.blocks` (ensemble du job) ; `tranches_ignorees`
    sont des RANGS 1-based de tranche.

    Ne contient ni texte transcrit ni chemin : seulement des index et des durées."""
    vides = [i for i, b in enumerate(blocs or []) if not (b or "").strip()]
    ignorees = list(dict.fromkeys(tranches_ignorees or []))
    rejoues = sorted(set(blocs_rejoues or []))
    ecart = None
    if duree_conteneur_s is not None and duree_decodee_s is not None:
        ecart = round(duree_conteneur_s - duree_decodee_s, 2)
    if vides or ignorees:
        statut = "incomplet"
    elif ecart is None or not termine:
        statut = "non_evaluable"
    elif ecart > max(
        TOLERANCE_ECART_MIN_S, TOLERANCE_ECART_RATIO * duree_conteneur_s
    ):
        statut = "incomplet"
    else:
        statut = "complet"
    return {
        "duree_conteneur_s": (
            None if duree_conteneur_s is None else round(duree_conteneur_s, 2)
        ),
        "duree_decodee_s": (
            None if duree_decodee_s is None else round(duree_decodee_s, 2)
        ),
        "ecart_s": ecart,
        "blocs_vides": vides,
        "tranches_ignorees": ignorees,
        "blocs_rejoues": rejoues,
        "statut": statut,
    }


def iter_transcribe_blocks(
    content: bytes, block_s: int | None = None, start_index: int = 0,
    stats: dict | None = None,
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

    Chaque bloc est tenté au plus `BLOC_MAX_TENTATIVES` fois PAR APPEL (pool
    compris), AVANT d'être rendu. `tentatives` et `blocs_rejoues` sont remis à
    zéro à chaque appel : le plafond réel par bloc est donc
    `plafond_essais_bloc(BLOC_RETRIES)` quand l'appelant (job de fond)
    rappelle la fonction après un échec. Un échec dans le pool fait descendre d'un
    palier à partir du bloc fautif, puis le séquentiel prend les tentatives
    restantes. `stats` (optionnel, rempli en place) reçoit `blocs_rejoues`
    (index des blocs ayant eu besoin de plus d'une tentative),
    `duree_conteneur_s` (durée annoncée par le conteneur, `None` si illisible —
    cas probable d'un `.webm` de MediaRecorder) et `duree_decodee_s` (durée du
    PCM décodé, absente si le décodage échoue).
    """
    if stats is not None:
        stats["blocs_rejoues"] = []
        stats["duree_conteneur_s"] = _probe_duration_s(content) if content else None
    tentatives: dict[int, int] = {}
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
    if stats is not None:
        stats["duree_decodee_s"] = pcm.size / 16000
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
        yield 0, 1, _transcrire_bloc_sequentiel(blocks, 0, 1, tentatives, stats)
        return

    index = start_index
    for workers in _paliers_workers(min(MAX_PARALLEL_WORKERS, total)):
        try:
            gen = _drain_parallel(blocks, index, total, workers)
            try:
                for i, text in gen:
                    yield i, total, text
                    index = i + 1
            finally:
                # Fermeture explicite : les permis (finally de `_drain_parallel`)
                # sont rendus même si l'appelant abandonne ce générateur.
                gen.close()
            break
        except TranscriptionError:
            raise
        except OSError as exc:  # définitif : jamais rejoué
            logger.exception("Échec de la transcription d'un bloc audio")
            raise TranscriptionError(ECHEC_TRANSCRIPTION) from exc
        except Exception as exc:
            # Pool cassé (worker tué, cf. « Repli quand le pool casse » dans la
            # docstring du module) ou bloc en échec (MemoryError du worker,
            # exception transitoire). On ne perd NI les blocs déjà rendus, NI
            # ceux qui restent : on reprend à `index` — le bloc fautif — au
            # palier SUIVANT (jamais le même), puis en séquentiel ci-dessous.
            # Attribution APPROXIMATIVE : le fautif réel peut être un bloc en vol
            # plus loin dans la fenêtre ; on compte sur `index`, le premier non
            # rendu. La borne (3 tentatives par bloc, 2 paliers) tient quand même.
            tentatives[index] = tentatives.get(index, 0) + 1
            if tentatives[index] >= BLOC_MAX_TENTATIVES:
                logger.exception("Échec de la transcription d'un bloc audio")
                raise TranscriptionError(ECHEC_TRANSCRIPTION) from exc
            _noter_rejeu(stats, index)
            logger.warning(
                "Bloc %d/%d en échec (%s, %d workers) — tentative %d/%d, palier suivant.",
                index + 1, total, type(exc).__name__, workers,
                tentatives[index] + 1, BLOC_MAX_TENTATIVES,
            )
            time.sleep(BLOC_RETRY_PAUSE_S)

    # Repli séquentiel : aucun tour si un palier parallèle est allé au bout.
    while index < total:
        text = _transcrire_bloc_sequentiel(blocks, index, total, tentatives, stats)
        yield index, total, text
        index += 1


def transcribe_audio(content: bytes, abandonne=None) -> str:
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

    global _LIVE_COMPTEUR
    with _LIVE_COMPTEUR_LOCK:
        _LIVE_COMPTEUR += 1
    try:
        return _transcribe_audio_live(content, duration_s, abandonne)
    finally:
        with _LIVE_COMPTEUR_LOCK:
            _LIVE_COMPTEUR -= 1


def _transcribe_audio_live(content: bytes, duration_s, abandonne=None) -> str:
    try:
        if duration_s is not None and duration_s > PARALLEL_THRESHOLD_S:
            text = _transcribe_parallel(content, duration_s, LIVE, abandonne=abandonne)
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
            with _compte_attente():
                obtenu = _MODEL_LOCK.acquire(timeout=SEGMENT_LOCK_TIMEOUT_S, priority=LIVE)
            if not obtenu:
                exc = TranscriptionBusyError(
                    "Transcription momentanément occupée, réessayez."
                )
                exc.retry_after_s = estimer_retry_after()
                raise exc
            try:
                # Client parti pendant l'attente et plus aucun demandeur du même
                # segment : ne pas brûler 60 s de CPU pour un segment mort.
                if abandonne is not None and abandonne():
                    raise TranscriptionAbandonnee()
                debut = time.monotonic()
                jeton = _ATTENTE_CHARGEMENT_MAX.set(SEGMENT_LOCK_TIMEOUT_S)
                try:
                    model = _get_model()
                finally:
                    _ATTENTE_CHARGEMENT_MAX.reset(jeton)
                segments, _info = model.transcribe(
                    io.BytesIO(content), language="fr", beam_size=BEAM_SIZE, vad_filter=True,
                    **OPTIONS_ANTI_HALLUCINATION,
                )
                text = _texte_segments(segments)
                _noter_duree(time.monotonic() - debut)
            finally:
                _MODEL_LOCK.release()
    except (TranscriptionError, TranscriptionAbandonnee):
        raise
    except Exception as exc:  # garde-fou : ne jamais propager une 500 brute
        logger.exception("Échec de la transcription d'un bloc audio")
        raise TranscriptionError(ECHEC_TRANSCRIPTION) from exc

    if not text:
        raise NoSpeechError("Aucune parole détectée dans l'enregistrement.")
    return text
