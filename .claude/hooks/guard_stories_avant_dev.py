r"""PreToolUse (Skill) — REFUSE une skill d'implémentation quand aucune user story n'existe.

POURQUOI. Arbitrage utilisateur du 2026-09-24 : les critères d'acceptance des user
stories, écrits en amont via `/bmad-create-epics-and-stories`, sont le référentiel de
conformité du sous-agent `utilisateur-produit`. Leur absence est un **prérequis absent,
bloquant pour tout nouveau développement** ; une correction de bug n'est possible
qu'après rattrapage (écrire d'abord les critères de la story touchée).

Constat qui a motivé le hook : sur Vscode7-CAT, un générateur de Handbook a été codé le
2026-09-17 avec un PRD mais sans architecture, sans epics.md, sans aucun critère
d'acceptance — alors que le playbook `dev-verifie` porte depuis le 2026-09-16 une étape
`cadrage-epics` dite « bloquante ». Un contrat de playbook n'est tenu que par la
discipline de celui qui l'instancie ; ce hook est le seul mécanisme opposable.

LE POINT DE CONTRÔLE. L'invocation d'une skill BMAD d'IMPLÉMENTATION (`bmad-build`,
`bmad-build-auto`, `bmad-agent-dev`) : c'est le moment où du code va être écrit. Si
`{planning_artifacts}/epics.md` n'existe pas dans le projet, on refuse en disant quoi
faire (générer les stories), jamais en laissant deviner.

CE QU'IL NE PRÉTEND PAS FAIRE. Il vérifie la PRÉSENCE du fichier, pas que la story
touchée y a des critères vérifiables — cela reste la lecture du sous-agent
`utilisateur-produit` (« CONFORMITE AUX ATTENDUS »). Il ne voit pas non plus un code
écrit par `Edit`/`Write` hors de toute skill : un garde-fou qui bloquerait toute écriture
de fichier tant qu'epics.md manque bloquerait aussi le rattrapage lui-même.

FAIL-OPEN INTÉGRAL. PreToolUse : entrée malformée, skill non reconnue, racine
introuvable → on laisse passer sans un mot. Il ne refuse que sur un cas positivement
établi : skill d'implémentation reconnue ET fichier absent.

RACINE DÉRIVÉE DE `__file__` (`.claude/hooks/<ce fichier>` → 3 niveaux), jamais du
`cwd` : un hook du kit peut être lancé depuis n'importe quel sous-répertoire de la
cible. `AGENT_SUPERVISION_EPICS` redirige le chemin contrôlé — même convention que les
autres garde-fous du dispositif, c'est ce qui le rend testable sans toucher au réel.
"""
import json
import os
import re
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EPICS = os.environ.get("AGENT_SUPERVISION_EPICS") or os.path.join(
    RACINE, "_bmad-output", "planning-artifacts", "epics.md")

# Les skills qui ÉCRIVENT du code applicatif. `bmad-create-epics-and-stories`,
# `bmad-architecture`, `bmad-prd`… n'y sont pas : ce sont elles qui produisent le
# prérequis, les bloquer rendrait le rattrapage impossible.
SKILLS_IMPLEMENTATION = ("bmad-build", "bmad-build-auto", "bmad-agent-dev")


_RE_CRITERES = re.compile(r"acceptance\s+criteria|crit[eè]res?\s+d[’']acceptance", re.I)


def _porte_des_criteres(chemin: str) -> bool:
    """Vrai si le fichier existe ET contient au moins un bloc de criteres d'acceptance."""
    try:
        with open(chemin, encoding="utf-8", errors="replace") as fh:
            return bool(_RE_CRITERES.search(fh.read()))
    except OSError:
        return False


def _laisser_passer():
    sys.exit(0)



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
        data = json.loads(_stdin_ou_refus().decode("utf-8", "replace"))
    except Exception:   # noqa: BLE001 - fail-open
        _laisser_passer()

    if not isinstance(data, dict) or data.get("tool_name") != "Skill":
        _laisser_passer()
    entree = data.get("tool_input")
    if not isinstance(entree, dict):
        _laisser_passer()
    nom = entree.get("skill")
    if not isinstance(nom, str):
        _laisser_passer()
    nom_bas = nom.strip().casefold().split(":")[-1]
    if nom_bas not in SKILLS_IMPLEMENTATION:
        _laisser_passer()

    # Presence ET contenu : un `epics.md` vide, ou sans un seul bloc de criteres, est
    # exactement le contournement qu'un garde-fou de presence laisse passer (revue
    # adversariale du 2026-09-24). Le gabarit BMAD marque chaque story par
    # « **Acceptance Criteria:** » ; on accepte aussi la forme francaise.
    if _porte_des_criteres(EPICS):
        _laisser_passer()

    etat = "absent" if not os.path.isfile(EPICS) else "present mais sans aucun critere d'acceptance"
    raison = (
        f"Skill d'implementation « {nom} » refusee : aucune user story exploitable "
        f"({os.path.relpath(EPICS, RACINE) if EPICS.startswith(RACINE) else EPICS} {etat}).\n"
        "Prerequis absent = BLOQUANT pour tout nouveau developpement (arbitrage du "
        "2026-09-24) : generer d'abord les epics / user stories et leurs criteres "
        "d'acceptance via /bmad-create-epics-and-stories (playbook cadrage-produit, ou "
        "l'etape cadrage-epics de dev-verifie ; prerequis de la skill : PRD + architecture).\n"
        "Correction de bug ? Rattrapage d'abord : ecrire les criteres d'acceptance de la "
        "story que le bug touche, puis corriger contre eux. Un correctif sans critere "
        "ecrit est un developpement a l'aveugle."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": raison,
    }}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:   # noqa: BLE001 - dernier filet
        sys.exit(0)
