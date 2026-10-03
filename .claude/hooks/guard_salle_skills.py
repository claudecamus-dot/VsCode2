r"""PreToolUse (Skill) — REFUSE une convocation de salle qui ne nomme pas ses skills BMAD.

POURQUOI CE HOOK EXISTE, et pourquoi il BLOQUE là où un rappel aurait suffi en théorie.

Mesure du 2026-09-02 : sur 46 skills BMAD installées, 2 seulement avaient jamais été
invoquées en 135 sessions. La table de routage qui les désigne existe depuis le
2026-07-30 et n'a rien déclenché pendant 33 jours. Le premier correctif de la journée a
consisté à ajouter un champ `skills_bmad` aux 12 salles et un paragraphe à la skill
d'orchestration — puis `bmad-advanced-elicitation` (pré-mortem + red team, invoquée sur
ce correctif même) a montré que c'était la MÊME NATURE que ce qui avait échoué : une
donnée dans un TOML plus une phrase dans un document de 700 lignes, avec trois « penser
à » entre la donnée et l'acte.

Le seul mécanisme opposable de ce dépôt est un hook bloquant — c'est ainsi que
`guard_destructive_git.py` refuse un `git push --force`. D'où celui-ci.

LE POINT DE CONTRÔLE. La convocation `bmad-party-mode --party <salle>` : c'est le moment
où le nom de la salle est connu et où le brief s'écrit. Si le texte de la convocation ne
nomme AUCUNE des skills que la salle déclare, on refuse, en listant celles qu'on
attendait — un refus qui ne dit pas quoi écrire fait deviner, et on redevine mal.

CE QU'IL NE PRÉTEND PAS FAIRE. Il vérifie que la convocation PORTE les noms, pas qu'une
voix a réellement chargé la skill. Cette seconde vérification ne peut être qu'a
posteriori, contre le compteur de l'étage 1. Annoncer plus que ce qu'on mesure est le
défaut que ce dépôt paie en boucle ; ce hook s'en tient donc à ce qu'il voit.

FAIL-OPEN INTÉGRAL. Il tourne en `PreToolUse` : une exception y bloquerait un appel
d'outil légitime. Entrée malformée, `tomllib` absent, TOML illisible, salle inconnue,
salle sans `skills_bmad` → on laisse passer, sans un mot. Il ne refuse que sur un cas
positivement établi. Un garde-fou qui refuse ce qu'il ne comprend pas devient un obstacle,
et on le contourne : ça coûte plus cher que de ne pas l'avoir.

RACINE DÉRIVÉE DE `__file__`, jamais du `cwd` : la leçon est celle de
`warn_verif_before_commit.py`, généralisé le matin même — un hook du kit peut être lancé
depuis n'importe quel sous-répertoire de la cible. Ce hook est dans le kit publié depuis
le 2026-09-02 (arbitrage utilisateur tracé à `hooks:guard_salle_skills` dans
`arbitrages.json` ; la revue de commit du même jour avait relevé qu'il n'y était pas alors
que son docstring l'affirmait). « Dans le kit » n'est pas « chez les cibles » : la
propagation vers VSCode..VSCode4 est en standby, arbitrée le même jour. `AGENT_SUPERVISION_PARTY_TOML`
permet de le rediriger — même convention que les autres journaux du dispositif, et c'est
ce qui le rend testable sans toucher au réel.

LA PORTE DE SORTIE EXISTE DANS LE CODE, pas seulement dans le message (revue du
2026-09-02 : le refus prescrivait d'écrire « aucune skill BMAD sur ce tour, parce que… »,
et cette phrase était refusée à son tour — la seule issue était de recopier un nom, le
comportement que le message condamnait). Une renonciation explicite laisse passer.

CE QUE LA COMPARAISON VAUT. Insensible à la casse et aux guillemets autour du nom de
salle ; mais elle reste une présence de chaîne — « on n'invoquera PAS bmad-help » passe.
Vérifier l'engagement réel d'une voix n'est possible qu'a posteriori, sur le compteur.
"""
import json
import os
import re
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOML_SALLES = os.environ.get("AGENT_SUPERVISION_PARTY_TOML") or os.path.join(
    RACINE, "_bmad", "custom", "bmad-party-mode.toml")

# `--party <id>` ou `--group <id>` (l'alias documenté par la skill).
_RE_PARTY = re.compile(r"--(?:party|group)[=\s]+[\x22\x27]?([A-Za-z0-9_-]+)", re.I)
# Renonciation explicite : la convocation dit qu'aucune skill ne partira, et pourquoi.
_RE_RENONCE = re.compile(r"aucune\s+skill\s+bmad\s+sur\s+ce\s+tour", re.I)


def _laisser_passer():
    """Sortie neutre : aucune décision, le harnais poursuit."""
    sys.exit(0)


def _salle_declaree(salle):
    """Les skills que cette salle déclare, ou [] dans TOUS les cas douteux."""
    try:
        import tomllib
        with open(TOML_SALLES, "rb") as fh:
            data = tomllib.load(fh)
        for groupe in (data.get("workflow") or {}).get("party_groups") or []:
            if str(groupe.get("id") or "").casefold() == salle:
                declarees = groupe.get("skills_bmad") or []
                return [s for s in declarees if isinstance(s, str)]
    except Exception:   # noqa: BLE001 - fail-open, cf. docstring
        return []
    return []



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
        # Octets lus et décodés EXPLICITEMENT en UTF-8 : sous `py <script>` sur Windows,
        # stdin/stdout sont en cp1252 et le harnais parle UTF-8. La sortie, elle, est en
        # ASCII pur (`ensure_ascii` par défaut) : un `deny` qui sort en cp1252 est un
        # `deny` illisible (revue du 2026-09-02, reproduit : 0xab dans « »).
        data = json.loads(_stdin_ou_refus().decode("utf-8", "replace"))
    except Exception:   # noqa: BLE001
        _laisser_passer()

    if not isinstance(data, dict) or data.get("tool_name") != "Skill":
        _laisser_passer()

    entree = data.get("tool_input")
    if not isinstance(entree, dict):
        _laisser_passer()

    # Le nom de la skill peut varier de forme selon l'appelant ; on ne refuse que si on
    # reconnaît positivement party-mode.
    nom = entree.get("skill")
    if not isinstance(nom, str) or "party-mode" not in nom.casefold():
        _laisser_passer()

    args = entree.get("args")
    if not isinstance(args, str) or not args.strip():
        _laisser_passer()

    trouve = _RE_PARTY.search(args)
    if not trouve:
        _laisser_passer()

    attendues = _salle_declaree(trouve.group(1).casefold())
    if not attendues:
        _laisser_passer()

    if _RE_RENONCE.search(args):
        _laisser_passer()

    bas = args.casefold()
    if any(s.casefold() in bas for s in attendues):
        _laisser_passer()

    raison = (
        f"Convocation de la salle « {trouve.group(1)} » sans aucune de ses skills BMAD.\n"
        f"Cette salle déclare : {', '.join(attendues)}.\n"
        "Nomme dans le brief celle(s) que ses voix doivent charger via l'outil Skill, et "
        "dis QUELLE voix la charge — une voix part avec un contexte vierge, elle n'a ni la "
        "table de routage ni le TOML.\n"
        "Si aucune skill n'a d'objet sur ce sujet, écris-le dans la convocation : « aucune "
        "skill BMAD sur ce tour, parce que… ». Un refus assumé et dit vaut mieux qu'un nom "
        "recopié pour faire passer un garde-fou — c'est le compteur d'usage qu'on veut "
        "juste, pas gonflé.\n"
        "(Mesuré le 2026-09-02 : 2 skills BMAD invoquées sur 46 en 135 sessions ; la table "
        "de routage seule n'a rien déclenché en 33 jours.)"
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
    except Exception:   # noqa: BLE001 - dernier filet, cf. docstring
        sys.exit(0)
