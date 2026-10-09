"""guard_service — un SOUS-AGENT ne lance, ne redémarre ni ne purge un service.

Finding `flotte:mandat-sous-agent-ne-tient-pas-sans-hook-serveur` (2026-09-08, arbitré
le jour même). Le matin, le hub avait inscrit au canon du brief autoportant la clause
« n'utiliser qu'un serveur déjà en écoute qu'on n'a pas démarré, sinon écrire "non
vérifié au rendu" » (agent-orchestrator § 2 ter, commit 3b9feba). L'après-midi, la
session VSCode2 rapportait qu'un de SES sous-agents de lecture, mandat explicite « ne
lance pas de serveur », avait démarré un uvicorn sur le port 8040 pointé sur la vraie
`data/app.db`. Une consigne s'adresse au modèle ; ce hook s'adresse à l'outil — même
mécanique que `guard_destructive_git`.

CE QU'IL REFUSE, en contexte sous-agent (PreToolUse Bash + PowerShell), quand la
commande EST le lanceur (position de commande dans un segment, pas une simple mention :
`grep -rn uvicorn app/` passe, `uvicorn app:app` non) :
- lancer : uvicorn, gunicorn, flask run, `py|python -m uvicorn|gunicorn|http.server`,
  npm|yarn|pnpm start|dev|serve, serve_wiki.py, serveur-dev.ps1 sans `-StopOnly` ;
- purger : Stop-Process, taskkill, Get-ProcessusServeur, pkill, kill <pid>.
`serveur-dev.ps1 -StopOnly` reste permis : c'est l'arrêt scopé au port, le geste qui a
justement servi à réparer l'incident.

CONTEXTE SOUS-AGENT — deux signaux, le premier documenté, le second mesuré :
1. `agent_id` / `agent_type` dans le JSON d'entrée, « présents uniquement lors de
   l'exécution dans un sous-agent » (doc Claude Code, hooks reference, lue le 2026-09-08) ;
2. `transcript_path` sous `<session>/subagents/agent-<id>.jsonl` (mesuré sur ce poste :
   `~/.claude/projects/<projet>/<session>/subagents/agent-a90054a75a8bd6021.jsonl`, qui
   porte les 34 appels Bash du sous-agent et pas le transcript principal).
Session principale : INACTIF par défaut (proposition (C) du finding, décision par projet) ;
`GUARD_SERVICE_PRINCIPALE=1` dans l'environnement (section `env` de settings.json) l'y
étend.

Payload illisible (vide, non-JSON, non-objet) : REFUS prudent, exit 2 (revue n4,
Vex : un payload tronque passait en silence). FAIL-OPEN : commande vide, outil inattendu, contexte non identifié ou
toute exception -> le hook ne dit rien. Un garde-fou qui bloque tout au premier cas non
prévu est retiré dans l'heure (leçon hook-chemin-relatif-casse-toute-commande).
Texte de refus en ASCII : lisible quel que soit l'encodage de la console (leçon
tester-un-hook-comme-la-production).
"""

from __future__ import annotations

import json
import os
import re
import sys

HEREDOC = re.compile(r"<<-?\s*'?\"?(\w+)'?\"?[^\n]*\n.*?\n\1(?=\r?\n|$)", re.S)
SEPARATEURS = re.compile(r"\s*(?:&&|\|\||;|\||\r?\n)\s*")
AFFECTATION = re.compile(r"^[A-Za-z_]\w*=")
PYTHONS = {"py", "python", "python3", "python.exe", "py.exe"}
LANCEURS_PS = {"pwsh", "pwsh.exe", "powershell", "powershell.exe", "&", ".", "-file"}
PURGES = {"stop-process", "taskkill", "pkill", "get-processusserveur"}


def _strip_heredocs(cmd: str) -> str:
    """Le corps d'un heredoc est une DONNÉE (script, message de commit) : y lire
    « uvicorn » n'est pas lancer uvicorn."""
    return HEREDOC.sub("<<HEREDOC", cmd)


def _mot(token: str) -> str:
    """Nom de commande normalisé : sans guillemets ni chemin, en minuscules."""
    t = token.strip("\"'`")
    t = t.replace("\\", "/").rsplit("/", 1)[-1]
    return t.lower()


def _segments(cmd: str):
    for seg in SEPARATEURS.split(cmd):
        seg = seg.strip().lstrip("(").strip()
        if not seg:
            continue
        mots = seg.split()
        while mots and AFFECTATION.match(mots[0]):
            mots.pop(0)
        if mots:
            yield seg, mots


def _motif_segment(seg: str, mots: list) -> str | None:
    m = [_mot(t) for t in mots]
    w0 = m[0]

    # serveur-dev.ps1 en position de commande (& x.ps1 | pwsh x.ps1 | powershell -File x.ps1 | ./x.ps1)
    for t in m[:4]:
        if t.endswith("serveur-dev.ps1"):
            if not re.search(r"-stoponly\b", seg, re.I):
                return "serveur-dev.ps1 sans -StopOnly"
            return None
        if t not in LANCEURS_PS and not t.endswith(".ps1"):
            break

    if w0 in PYTHONS:
        if len(m) >= 3 and m[1] == "-m":
            if m[2] in ("uvicorn", "gunicorn", "http.server"):
                return "python -m " + m[2]
            if m[2] == "flask" and len(m) >= 4 and m[3] == "run":
                return "flask run"
        if len(m) >= 2 and m[1].endswith("serve_wiki.py"):
            return "serve_wiki.py"
        return None
    if w0 in ("uvicorn", "gunicorn"):
        return w0
    if w0 == "flask" and len(m) >= 2 and m[1] == "run":
        return "flask run"
    if w0 in ("npm", "yarn", "pnpm"):
        args = m[1:3]
        if args and args[0] == "run":
            args = args[1:]
        if args and args[0] in ("start", "dev", "serve"):
            return w0 + " " + args[0]
        return None
    if w0.endswith("serve_wiki.py"):
        return "serve_wiki.py"

    if w0 in PURGES:
        return w0
    if w0 == "kill" and any(re.fullmatch(r"\d+", t) for t in m[1:]):
        return "kill <pid>"
    return None


def analyser(cmd: str) -> str | None:
    """Le motif refusé, ou None."""
    for seg, mots in _segments(_strip_heredocs(cmd)):
        motif = _motif_segment(seg, mots)
        if motif:
            return motif
    return None


def contexte_sous_agent(data: dict) -> bool:
    if data.get("agent_id") or data.get("agent_type"):
        return True
    tp = str(data.get("transcript_path") or "").replace("\\", "/")
    return "/subagents/" in tp


def actif(data: dict) -> bool:
    if contexte_sous_agent(data):
        return True
    return os.environ.get("GUARD_SERVICE_PRINCIPALE") == "1"


def raison(motif: str) -> str:
    return (
        "guard_service : un sous-agent ne lance, ne redemarre ni ne purge un service "
        "(" + motif + "). Regle du brief (agent-orchestrator 2 ter) : n'utiliser qu'un "
        "serveur deja en ecoute que tu n'as pas demarre ; sinon ecrire << non verifie au "
        "rendu >> dans ton resultat. Un service tiers tue coute plus cher qu'une "
        "verification manquante annoncee (incident VSCode2 du 2026-09-08 : serveur "
        "utilisateur tue en pleine session). Si ce service doit vraiment tourner, c'est a "
        "la session principale de le lancer."
    )



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
        data = _json_ou_refus()
    except Exception:
        return
    try:
        if data.get("tool_name") not in ("Bash", "PowerShell"):
            return
        cmd = (data.get("tool_input") or {}).get("command") or ""
        if not cmd or not actif(data):
            return
        motif = analyser(cmd)
        if not motif:
            return
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": raison(motif),
            }
        }))
    except Exception:
        return


if __name__ == "__main__":
    main()
