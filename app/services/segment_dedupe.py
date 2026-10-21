"""Dédoublonnage des segments audio du direct (2026-10-19).

Mesuré : 118 POST /audio/transcribe-segment en 503 contre 3 en 200 — chaque
retry du même blob créait un thread qui prenait sa place dans la file du
verrou Whisper, et transcrivait un segment mort si le client était parti.

Ici : un registre thread-safe clé -> travail en vol. Les requêtes concurrentes
ou rejouées du même segment PARTAGENT un seul travail et la même réponse ; un
résultat récent est rendu sans retranscrire (TTL court, taille bornée). Un
travail dont plus personne n'attend le résultat est marqué abandonné : le
thread le teste après obtention du verrou et saute la transcription.

Clé : en-tête `X-Segment-Id` du client, sinon empreinte sha1 du contenu.
"""
from __future__ import annotations

import collections
import hashlib
import threading
import time
from concurrent.futures import Future

TTL_RESULTAT_S = 600.0
MAX_RESULTATS = 256
TTL_VOL_S = 3600.0  # sécurité : un travail « en vol » plus vieux est oublié


def cle_segment(identifiant: str | None, contenu: bytes) -> str:
    ident = (identifiant or "").strip()
    if ident:
        # id + empreinte : un même id avec un contenu différent n'est pas le même
        # segment (les retries gardent le même blob, donc la même empreinte).
        return "id:" + ident[:200] + ":" + hashlib.sha1(contenu).hexdigest()
    return "sha1:" + hashlib.sha1(contenu).hexdigest()


class Travail:
    def __init__(self) -> None:
        self.future: Future = Future()
        self.attendants = 1
        self.abandonne = False
        self.mort = False  # le thread a constaté l'abandon : plus réutilisable
        self.debut = time.monotonic()


class RegistreSegments:
    def __init__(self, clock=time.monotonic) -> None:
        self._lock = threading.Lock()
        self._vol: dict[str, Travail] = {}
        self._faits: "collections.OrderedDict[str, tuple[float, str]]" = collections.OrderedDict()
        self._clock = clock

    def resultat_recent(self, cle: str):
        with self._lock:
            return self._resultat_locked(cle)

    def _resultat_locked(self, cle):
        v = self._faits.get(cle)
        if v is None:
            return None
        if self._clock() - v[0] > TTL_RESULTAT_S:
            del self._faits[cle]
            return None
        return v[1]

    def rejoindre(self, cle: str):
        """-> (texte_ou_None, travail_ou_None, proprietaire).
        Texte déjà calculé : (texte, None, False). Sinon un Travail ; le
        propriétaire (True) doit lancer le calcul."""
        with self._lock:
            texte = self._resultat_locked(cle)
            if texte is not None:
                return texte, None, False
            t = self._vol.get(cle)
            if (t is not None and not t.mort and not t.future.done()
                    and self._clock() - t.debut < TTL_VOL_S):
                # Un travail abandonné mais pas encore constaté par son thread est
                # RÉACTIVÉ (sinon un 2e thread retranscrit le même audio).
                t.attendants += 1
                t.abandonne = False
                return None, t, False
            t = Travail()
            t.debut = self._clock()
            self._vol[cle] = t
            return None, t, True

    def quitter(self, t: Travail) -> None:
        """Un demandeur s'en va sans réponse ; le dernier marque l'abandon."""
        with self._lock:
            t.attendants -= 1
            if t.attendants <= 0 and not t.future.done():
                t.abandonne = True

    def est_abandonne(self, t: Travail) -> bool:
        with self._lock:
            if t.abandonne:
                t.mort = True
            return t.abandonne

    def terminer(self, cle: str, t: Travail, texte: str | None) -> None:
        with self._lock:
            if self._vol.get(cle) is t:
                del self._vol[cle]
            if texte is not None:
                self._faits[cle] = (self._clock(), texte)
                self._faits.move_to_end(cle)
                while len(self._faits) > MAX_RESULTATS:
                    self._faits.popitem(last=False)

    def vider(self) -> None:
        with self._lock:
            self._vol.clear()
            self._faits.clear()


REGISTRE = RegistreSegments()
