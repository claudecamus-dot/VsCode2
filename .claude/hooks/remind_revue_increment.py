"""SessionStart hook — systématise la boucle de revue-et-amélioration.

Réinjecte, au début de chaque session, la discipline « definition of done »
du projet : la revue fine + l'application des correctifs + la re-vérification
réelle ne doivent pas dépendre de « penser à les lancer ». Le skill
`revue-increment` porte le protocole ; ce hook garantit qu'il est rappelé
systématiquement et récurremment (à chaque session), sans friction par-commit.

Non bloquant : émet seulement un `additionalContext` (SessionStart). Fails
open — toute erreur de parsing rend la main sans injecter, pour ne jamais
casser un démarrage de session.
"""
import json
import sys

REMINDER = (
    "Discipline qualité du projet (rappel systématique) : avant de considérer "
    "un incrément « livré » ou de committer du code produit, lancer la boucle "
    "`/revue-increment` — revue fine (produit + façon de travailler), PUIS "
    "application des actions d'amélioration (`/code-review high --fix`, "
    "`/simplify`, correctifs concrets), PUIS re-vérification RÉELLE (tests + "
    "exécution/rendu réel via le chemin de vérif du projet, pas seulement des "
    "tests verts). Ne pas déclarer « fait » avec une vérif runtime sautée ou un "
    "correctif évident non appliqué. Les actions sensibles/irréversibles "
    "(suppression de fichier versionné, écriture en base réelle) se proposent, "
    "ne s'exécutent pas unilatéralement. "
    "Écosystème de skills : si BMAD est installé (`_bmad/`, skills `bmad-*`), "
    "invoquer `bmad-help` en cas de doute sur quel skill lancer ; "
    "`revue-increment` délègue à `bmad-code-review` / `bmad-retrospective` "
    "plutôt que de les dupliquer."
)


# --- bounded stdin read (anthropics/claude-code#87289) -------------------------
try:
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    from _stdin_borne import lire_stdin_borne as _lsb
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
    """Shared writer of ``<hooks>/../supervision/refus_stdin<suffixe>.jsonl``
    (``CLAUDE_REFUS_STDIN_LOG`` redirects it, tests). Past ``cap`` the file
    ROTATES to ``<file>.1``: a hard stop silently dropped every line once the hub
    log reached 1 MB (review 2026-10-02). ``rotation=False`` keeps a hard stop
    (dispatcher crash lines). Never raises, never changes the exit."""
    try:
        _os = __import__("os")
        _dt = __import__("datetime")
        chemin = _os.environ.get("CLAUDE_REFUS_STDIN_LOG") or _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "..", "supervision", "refus_stdin.jsonl")
        if suffixe:
            chemin = _os.path.splitext(chemin)[0] + suffixe + ".jsonl"
        if _os.path.exists(chemin) and _os.path.getsize(chemin) > cap:
            if not rotation:
                return
            _os.replace(chemin, chemin + ".1")
        ligne = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                 "hook": _os.path.splitext(_os.path.basename(__file__))[0]}
        if globals().get("_GARDES_ACTIVES") is not None:
            ligne["gardes"] = list(globals()["_GARDES_ACTIVES"])
        ligne.update(champs)
        with open(chemin, "a", encoding="utf-8") as fh:
            fh.write(__import__("json").dumps(ligne) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _journal_attente(attente, delai):
    """A payload that arrived after > 2 s still passes, but leaves one line
    (motif ``attente``, ``issue: passe``) in its OWN file
    (``refus_stdin_attente.jsonl``, 500 KB then rotation): slow-but-served
    calls can never push the refusals out of the refusal journal."""
    _ecrire_journal_stdin({"motif": "attente", "issue": "passe", "delai_s": float(delai),
                           "attente_s": attente}, 500_000, "_attente")


def _stdin_borne(delai=15.0):
    """Bounded stdin read: the payload, or None on timeout/error — the hook decides
    (guard: fail-closed refusal; reminder: its existing fail-open path).
    A hook may set a module-level ``_FLUX_STDIN`` (e.g. a raw fd 0 reader).
    ``CLAUDE_STDIN_DELAI_S`` lowers the bound, never raises it (tests). The real
    wait is kept in ``_ATTENTE_S`` and journaled when a payload took > 2 s."""
    _tm = __import__("time")
    try:
        delai = min(delai, float(__import__("os").environ.get("CLAUDE_STDIN_DELAI_S", delai)))
    except ValueError:
        pass
    globals()["_DELAI_S"] = delai
    t0 = _tm.monotonic()
    v = _lsb(delai, globals().get("_FLUX_STDIN"))
    attente = round(_tm.monotonic() - t0, 3)
    globals()["_ATTENTE_S"] = attente
    if v is not None and attente > 2.0:
        globals()["_journal_attente"](attente, delai)
    return v


def main() -> None:
    try:
        json.loads(_stdin_borne())
    except Exception:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": REMINDER,
        }
    }))


if __name__ == "__main__":
    main()
