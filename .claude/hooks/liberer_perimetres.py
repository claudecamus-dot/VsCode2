"""SubagentStop — libere le perimetre d'ecriture d'un agent qui s'arrete (story 14.14).

Appelle `guard_perimetres_actifs.liberer` : retire les entrees expirees et l'entree de
l'agent qui s'arrete, reconnue par la declaration PERIMETRE ECRIT relue dans le premier
message `user` de son `agent_transcript_path` (story 14.22 AC.2, voie (b) arbitree le
2026-11-04). Transcript lisible mais rien a liberer (`aucun`, pas de ligne, aucune entree
vivante appariee) -> rien n'est libere. Transcript ILLISIBLE (champ ou fichier absent,
illisible, plafond, entree user vide ou absente) -> repli sur la plus ancienne entree de la session, motif journalise
dans le registre ; le TTL reste le filet. N'est PAS une garde : fail-open integral et exit 0 TOUJOURS (un arret
legitime d'agent ne doit jamais etre bloque) - stdin absent, JSON illisible, registre
corrompu ou module absent -> exit 0 sans rien ecrire.
"""
import importlib.util
import json
import os
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
# --- bounded stdin read (anthropics/claude-code#87289) -------------------------
# Single source: _stdin_borne.lier_stdin (imported via a path relative to __file__).
try:
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    from _stdin_borne import lier_stdin as _lier
    from _stdin_borne import lire_stdin_borne as _lsb
    _stdin_borne, _ecrire_journal_stdin, _journal_attente = _lier(globals(), __file__)
except Exception:  # noqa: BLE001 - exported without the helper: still bounded
    def _lsb(delai=15.0, flux=None):
        import threading
        f = flux if flux is not None else sys.stdin
        boite = {}

        def _c():
            try:
                boite["v"] = f.read()
            except BaseException:  # noqa: BLE001
                boite["v"] = None
        t = threading.Thread(target=_c, daemon=True)
        t.start()
        t.join(delai)
        return None if t.is_alive() else boite.get("v")

    def _ecrire_journal_stdin(champs, cap=1_000_000, suffixe="", rotation=True):
        pass  # journal unavailable without the helper; the refusal itself is unchanged

    def _journal_attente(attente, delai):
        pass

    def _stdin_borne(delai=15.0):
        globals()["_DELAI_S"] = delai
        globals()["_ATTENTE_S"] = None
        return _lsb(delai, globals().get("_FLUX_STDIN"))


class _Fd0:
    """Raw fd 0 reader: a daemon thread blocked in BufferedReader.read crashes the
    interpreter at shutdown (0xC0000005, measured); os.read holds no Python lock."""

    def read(self):
        morceaux = []
        while True:
            b = __import__("os").read(0, 65536)
            if not b:
                return b"".join(morceaux)
            morceaux.append(b)


_FLUX_STDIN = _Fd0()


def _stdin_ou_refus(delai=15.0):
    """Guard hook: no stdin within the bound is a prudent refusal (fail-closed).

    Exit 2 blocks the tool call and shows stderr to Claude. ``os._exit`` skips
    interpreter shutdown, which can crash (0xC0000005) while the reader thread
    is still blocked — a crash code other than 2 would be a silent fail-open.
    """
    v = _stdin_borne(delai)
    delai = globals().get("_DELAI_S", delai)
    if v is None:
        _refus_prudent(f"stdin non recu en {delai:g} s", "delai", delai)
    return v

def _journal_refus(motif, delai):
    """One JSON line per refusal (review 4, Dana: measure before tuning the
    bound), with the measured wait ``attente_s``; 1 MB then rotation."""
    _ecrire_journal_stdin({"motif": motif, "delai_s": float(delai),
                           "attente_s": globals().get("_ATTENTE_S")})

def _refus_prudent(cause, motif, delai=15.0):
    _os = __import__("os")
    nom = _os.path.splitext(_os.path.basename(__file__))[0]
    try:  # UTF-8 bytes on fd 2: the harness reads UTF-8, a cp1252 dash is mojibake
        _journal_refus(motif, delai)
        _os.write(2, f"{nom}: {cause} — refus prudent, relancer la commande\n".encode())
    finally:
        _os._exit(2)

def _json_ou_refus(delai=15.0):
    """Guard hook: an empty, non-JSON or non-object payload is refused too
    (review 4, Vex) — only a VALID payload reaches the guard's own fail-open."""
    brut = _stdin_ou_refus(delai)
    delai = globals().get("_DELAI_S", delai)
    try:
        if isinstance(brut, bytes):
            brut = brut.decode("utf-8", "replace")
        data = __import__("json").loads(brut)
    except Exception:  # noqa: BLE001
        data = None
    if not isinstance(data, dict):
        _refus_prudent("entree illisible", "illisible", delai)
    return data


def main() -> None:
    try:
        brut = _stdin_borne()
        if brut is None:
            sys.exit(0)
        data = json.loads(brut.decode("utf-8", "replace") if isinstance(brut, bytes) else brut)
        if not isinstance(data, dict):
            sys.exit(0)
        spec = importlib.util.spec_from_file_location(
            "guard_perimetres_actifs", os.path.join(ICI, "guard_perimetres_actifs.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.liberer(data, mod._chemin_registre())
    except SystemExit:
        raise
    except BaseException:   # noqa: BLE001 - fail-open absolu
        pass
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:   # noqa: BLE001
        sys.exit(0)
