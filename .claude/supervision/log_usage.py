"""Superviseur d'agents — étage 1 : journal temps réel des invocations Skill/Agent.

Branché sur le hook PostToolUse (matcher Skill|Agent|Task). Append une ligne JSON dans
.claude/supervision/usage.jsonl à chaque invocation — couvre la session en cours, que le
scan différé des transcripts (scan_transcripts.py) ne verra qu'à la prochaine session.
Ne bloque jamais l'outil (exit 0 en toutes circonstances), mais ne perd plus rien
en silence : une invocation non journalisée est signalée sur stderr.
"""
import datetime
import json
import os
import sys

# Windows : la console par defaut est cp1252 — un payload de hook accentué lu tel quel
# part en mojibake dans le journal. Mesuré sur le fichier réel : 57 lignes sur 233
# contiennent « Ã » ou « â€ ». Pire, un UnicodeDecodeError est une sous-classe de
# ValueError : il était avalé par le `except` ci-dessous, l'invocation disparaissait en
# silence et l'étage 1 sous-comptait. Même reconfiguration que le canon log_run.py.
# stdin en utf-8-sig : un pipe PowerShell 5.1 préfixe un BOM qui casserait json.loads
# (vécu 2026-07-23) ; sans BOM, utf-8-sig == utf-8.
for _flux, _enc in ((sys.stdin, "utf-8-sig"), (sys.stdout, "utf-8"), (sys.stderr, "utf-8")):
    if hasattr(_flux, "reconfigure"):
        _flux.reconfigure(encoding=_enc)

# Surchargeable pour les tests : le journal d'usage REEL ne doit jamais etre pollue
# par la suite (meme motif que AGENT_SUPERVISION_JOBS_JOURNAL, cf. tests/conftest.py).
USAGE_PATH = os.environ.get("AGENT_SUPERVISION_USAGE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "usage.jsonl"
)


def main() -> int:
    # Ne bloque jamais (exit 0), mais ne perd plus rien en SILENCE : une invocation
    # non journalisée est un sous-comptage de l'étage 1, elle doit se voir.
    try:
        brut = sys.stdin.read()
    except UnicodeDecodeError as exc:
        print(f"log_usage : payload non décodable en UTF-8 ({exc}) — invocation NON "
              "journalisée, l'étage 1 sous-compte d'autant.", file=sys.stderr)
        return 0
    except OSError as exc:
        print(f"log_usage : stdin illisible ({exc}) — invocation non journalisée.",
              file=sys.stderr)
        return 0
    try:
        data = json.loads(brut)
    except ValueError as exc:
        print(f"log_usage : payload JSON invalide ({exc}) — invocation non "
              "journalisée.", file=sys.stderr)
        return 0
    if not isinstance(data, dict):
        print("log_usage : payload inattendu (objet JSON attendu) — invocation non "
              "journalisée.", file=sys.stderr)
        return 0
    horodate = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    # SubagentStop — la FIN d'un sous-agent, et non plus seulement son lancement.
    # Adoption de la trouvaille `veille:disler-observabilite` (2026-09-01). L'écart
    # mesuré chez eux : 12 types d'événements captés contre UN SEUL ici. Celui-ci est
    # le plus utile au hub, parce qu'il ferme une question que l'étage 1 ne savait pas
    # poser : un sous-agent DISPATCHÉ et un sous-agent REVENU s'écrivaient pareil.
    # Sans lui, un fan-out dont une branche meurt est indiscernable d'un fan-out
    # complet — exactement le genre de non-convergence que le superviseur cherche.
    if data.get("hook_event_name") == "SubagentStop":
        session_id = data.get("session_id")
        entry = {"ts": horodate, "session_id": session_id,
                 "event": "subagent-stop"}
        duree = _duree_appariee(session_id, horodate)
        if duree is not None:
            entry["duree_s"] = duree
        _ecrire(entry)
        return 0

    tool = data.get("tool_name", "")
    if tool not in ("Skill", "Agent", "Task"):
        return 0
    tool_input = data.get("tool_input") or {}
    entry = {
        "ts": horodate,
        "session_id": data.get("session_id"),
        "tool": tool,
        "skill": tool_input.get("skill"),
        "subagent_type": tool_input.get("subagent_type")
        or (None if tool == "Skill" else "(defaut)"),
        "description": tool_input.get("description"),
    }
    # L'échec n'est marqué que s'il est POSITIVEMENT détecté. Les formes de réponse
    # varient d'un outil à l'autre : deviner « pas de succès donc échec » fabriquerait
    # des KO qui n'ont pas eu lieu, et le superviseur compte les `ko-repete`. Absence
    # de marque = on ne sait pas, pas « ça a marché ».
    if _echec_avere(data.get("tool_response")):
        entry["echec"] = True
    _ecrire(entry)
    return 0


def _duree_appariee(session_id, fin_iso: str):
    """`duree_s` d'un sous-agent (lancement Agent -> ce SubagentStop), UNIQUEMENT quand
    non ambigu — veille adoptée 2026-09-03 (finding : aucune durée n'était calculable,
    donc aucun seuil de non-convergence mesurable ; incident source : un sous-agent
    resté `running` 4h+ contre 8-17 min pour des tâches comparables).

    Aucun identifiant ne relie un lancement `Agent` à SA propre fin dans les payloads
    de hook captés ici : deux lancements concurrents (fan-out, le cas courant de ce
    dispatcher) sont donc indiscernables entre eux. Plutôt que deviner lequel vient de
    finir (une durée fausse est pire qu'aucune durée — c'est le même principe que
    `_echec_avere`, qui ne marque un échec que positivement détecté), cette fonction
    ne rend une durée QUE si un seul lancement `Agent` de cette session reste "ouvert"
    (sans SubagentStop déjà apparié) au moment de cet arrêt : le cas d'un sous-agent à
    la fois, ou du dernier restant d'un fan-out. Fail-open total : tout journal
    illisible, ligne corrompue ou horodatage non parsable rend None, jamais une
    exception — ce hook ne doit jamais bloquer l'outil qu'il journalise.
    """
    try:
        fin = datetime.datetime.fromisoformat(fin_iso)
        ouverts = []  # ts (datetime) des lancements Agent de cette session pas encore apparies
        with open(USAGE_PATH, encoding="utf-8") as fh:
            for ligne in fh:
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    e = json.loads(ligne)
                except ValueError:
                    continue
                if not isinstance(e, dict) or e.get("session_id") != session_id:
                    continue
                if e.get("event") == "subagent-stop":
                    if ouverts:
                        ouverts.pop(0)  # FIFO : le plus ancien lancement ouvert se ferme en premier
                elif e.get("tool") == "Agent":
                    ts = e.get("ts")
                    if isinstance(ts, str):
                        try:
                            ouverts.append(datetime.datetime.fromisoformat(ts))
                        except ValueError:
                            pass
        if len(ouverts) != 1:
            return None  # aucun lancement ouvert, ou plusieurs (fan-out) : ambigu, on ne devine pas
        return round((fin - ouverts[0]).total_seconds(), 1)
    except (OSError, ValueError, TypeError):
        return None


def _echec_avere(reponse) -> bool:
    """True seulement si la réponse DIT qu'elle a échoué."""
    if isinstance(reponse, dict):
        if reponse.get("is_error") is True or reponse.get("success") is False:
            return True
        statut = str(reponse.get("status", "")).lower()
        return statut in ("error", "failed", "failure")
    return False


def _ecrire(entry: dict) -> None:
    with open(USAGE_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    # Deux exigences que le docstring pose ensemble, et qui doivent le rester :
    # NE JAMAIS BLOQUER l'outil de l'utilisateur (un hook PostToolUse qui casse casse
    # l'outil), et NE RIEN PERDRE EN SILENCE. La version precedente ne tenait que la
    # premiere : `except Exception: sys.exit(0)` avalait toute panne d'ecriture —
    # repertoire absent, disque plein, permission refusee — avec stderr VIDE. L'etage 1
    # sous-comptait sans trace, et le superviseur batissait ses findings « agent mort »
    # sur un journal troue sans le savoir (audit technique du 2026-09-01).
    # « exit 0 » voulait dire aussi bien « journalise » que « perdu ».
    try:
        sys.exit(main())
    except Exception as err:                                     # noqa: BLE001
        print(f"log_usage : invocation NON journalisee ({type(err).__name__}: {err}) "
              f"— l'etage 1 sous-comptera cette session", file=sys.stderr)
        sys.exit(0)
