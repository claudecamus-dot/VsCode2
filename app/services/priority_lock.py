"""Verrou exclusif à deux priorités (LIVE > FOND), thread-safe.

Pourquoi : `threading.Lock` n'est ni FIFO ni prioritaire. Un traitement de fond
qui reprend le verrou bloc après bloc peut le re-prendre avant qu'un segment du
direct, en attente depuis des secondes, ne soit réveillé (barging). Ici le
verrou reste exclusif et NON préemptif (l'appel en cours finit toujours) mais,
à chaque libération, le prochain servi est choisi ainsi :

- priorité HAUTE (`LIVE`) avant BASSE (`FOND`) ;
- FIFO au sein d'une même priorité (ordre d'arrivée) ;
- plancher anti-famine : un waiter FOND en attente depuis plus de
  `starvation_s` est traité comme LIVE (il garde son rang FIFO parmi eux), de
  sorte qu'un flux continu de live ne bloque pas le fond indéfiniment.
  ATTENTION : cette borne n'est PAS `starvation_s` seule. Le verrou n'étant pas
  préemptif, le fond promu attend encore la fin du détenteur courant et celle de
  chaque LIVE (ou fond promu) arrivé AVANT lui (rang FIFO).

API compatible `Lock` : `acquire(blocking=True, timeout=-1, priority=LIVE)`
-> bool, `release()`, usage en `with` (priorité LIVE).

Incompatibilité mineure : `acquire(timeout=None)` attend SANS borne (comme -1)
alors que `threading.Lock.acquire(timeout=None)` lève `TypeError`.
"""
from __future__ import annotations

import threading
import time

LIVE = 0
FOND = 1

# Attente (s) au-delà de laquelle un waiter FOND passe en priorité LIVE.
STARVATION_S_DEFAUT = 120.0


class PriorityLock:
    def __init__(self, starvation_s: float = STARVATION_S_DEFAUT, clock=time.monotonic):
        self._cond = threading.Condition(threading.Lock())
        self._held = False
        self._waiters: list[tuple[int, int, float]] = []  # (priority, seq, depuis)
        self._seq = 0
        self._starvation_s = starvation_s
        self._clock = clock

    def _prochain(self):
        """Waiter à servir : (priorité effective, seq) minimal."""
        now = self._clock()
        def cle(w):
            prio, seq, depuis = w
            eff = LIVE if (prio == FOND and now - depuis >= self._starvation_s) else prio
            return (eff, seq)
        return min(self._waiters, key=cle)

    def acquire(self, blocking: bool = True, timeout: float = -1, priority: int = LIVE) -> bool:
        with self._cond:
            if not self._held and not self._waiters:
                self._held = True
                return True
            if not blocking:
                return False
            self._seq += 1
            me = (priority, self._seq, self._clock())
            self._waiters.append(me)
            fin = None if timeout is None or timeout < 0 else self._clock() + timeout
            try:
                while True:
                    if not self._held and self._prochain() is me:
                        self._held = True
                        return True
                    reste = None if fin is None else fin - self._clock()
                    if reste is not None and reste <= 0:
                        return False
                    # Réveil périodique : le plancher anti-famine dépend du temps.
                    borne = 1.0 if reste is None else min(reste, 1.0)
                    self._cond.wait(borne)
            finally:
                self._waiters.remove(me)
                self._cond.notify_all()

    def release(self) -> None:
        with self._cond:
            if not self._held:
                raise RuntimeError("release d'un PriorityLock non pris")
            self._held = False
            self._cond.notify_all()

    def locked(self) -> bool:
        with self._cond:
            return self._held

    def attente_live(self) -> bool:
        """Vrai si un waiter LIVE attend (le fond peut s'en servir pour céder)."""
        with self._cond:
            return any(p == LIVE for p, _s, _d in self._waiters)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
