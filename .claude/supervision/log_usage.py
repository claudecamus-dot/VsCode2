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
import re
import sys

# Forme deja utilisee par l'orchestrateur dans le brief d'une salle : « BUDGET : 45 min ».
_RE_BUDGET = re.compile(r"BUDGET\s*:\s*(\d+)\s*min", re.IGNORECASE)


def _budget_declare(prompt):
    """Budget en minutes lu dans le brief, ou None si absent/illisible."""
    if not isinstance(prompt, str):
        return None
    trouve = _RE_BUDGET.search(prompt)
    if not trouve:
        return None
    try:
        valeur = int(trouve.group(1))
    except ValueError:
        return None
    return valeur if valeur > 0 else None

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


# Statuts que le `tool_response` d'un Agent donne a une salle DEJA finie. En premier
# plan, le PostToolUse de l'outil Agent arrive APRES le SubagentStop du meme agent :
# sa ligne n'est donc pas un lancement en attente de fin, et la compter comme telle
# decalerait le FIFO d'un cran a chaque salle.
STATUTS_TERMINAUX = ("completed", "error", "failed", "cancelled", "aborted")


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
        agent_id = data.get("agent_id")
        entry = {"ts": horodate, "session_id": session_id,
                 "event": "subagent-stop",
                 "agent_id": agent_id if isinstance(agent_id, str) else None,
                 "agent_type": data.get("agent_type")}
        duree, appariement = _apparier(session_id, horodate, entry["agent_id"])
        entry["appariement"] = appariement
        if duree is not None:
            entry["duree_s"] = duree
        entry.update(_jetons_du_transcript(data.get("agent_transcript_path")))
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
    # Budget declare dans le brief (« BUDGET : <n> min », forme deja utilisee par
    # l'orchestrateur) : capture au LANCEMENT, seul instant ou le prompt complet de la
    # salle est disponible dans le payload PreToolUse. convergence.py le relit depuis
    # le journal, ne re-parse jamais tool_input lui-meme.
    budget_min = _budget_declare(tool_input.get("prompt"))
    if budget_min is not None:
        entry["budget_min"] = budget_min
    # Le `tool_response` d'un Agent porte la comptabilite reelle, mesuree sur payload
    # reel le 2026-09-20 (sonde jetable). DEUX formes, selon le mode :
    #  - premier plan  : status 'completed'      + totalTokens / totalToolUseCount /
    #                    totalDurationMs — le PostToolUse est alors la FIN, et il
    #                    arrive APRES le SubagentStop du meme agent ;
    #  - arriere-plan  : status 'async_launched' + agentId/resolvedModel seulement —
    #                    le PostToolUse est alors le LANCEMENT, jetons inconnus.
    # Une invocation `Skill` n'a ni modele ni sous-agent : lui coller ces champs a None
    # changerait la FORME des lignes deja ecrites et casserait leurs relecteurs.
    if tool in ("Agent", "Task"):
        entry.update(_annotation_agent(tool_input, data.get("tool_response")))
    # L'échec n'est marqué que s'il est POSITIVEMENT détecté. Les formes de réponse
    # varient d'un outil à l'autre : deviner « pas de succès donc échec » fabriquerait
    # des KO qui n'ont pas eu lieu, et le superviseur compte les `ko-repete`. Absence
    # de marque = on ne sait pas, pas « ça a marché ».
    if _echec_avere(data.get("tool_response")):
        entry["echec"] = True
    _ecrire(entry)
    return 0


def _annotation_agent(tool_input: dict, reponse) -> dict:
    """Ce que le payload DIT du modele et de la consommation d'un sous-agent.

    `modele` est le modele DEMANDE par l'appelant (haiku/sonnet/opus, ou None quand il
    herite) : c'est ce champ, et lui seul, qui rend la politique de routage de
    agent-orchestrator MESURABLE au lieu d'etre une croyance. Les autres viennent de la
    reponse de l'outil.

    Tolerant par construction : une reponse non-dict, un champ manquant ou d'un type
    inattendu laisse la cle a None. Aucun calcul, aucune inference — on n'ecrit que ce
    que le payload porte (meme principe que `_echec_avere`).
    """
    champs = {"modele": tool_input.get("model") if isinstance(tool_input, dict) else None,
              "agent_id": None, "modele_resolu": None, "statut": None,
              "jetons": None, "appels_outils": None, "duree_ms": None}
    if isinstance(reponse, dict):
        for cle, source, types in (
            ("agent_id", "agentId", str),
            ("modele_resolu", "resolvedModel", str),
            ("statut", "status", str),
            ("jetons", "totalTokens", int),
            ("appels_outils", "totalToolUseCount", int),
            ("duree_ms", "totalDurationMs", int),
        ):
            valeur = reponse.get(source)
            if isinstance(valeur, types) and not isinstance(valeur, bool):
                champs[cle] = valeur
    # La PROVENANCE du chiffre fait partie du chiffre : `tool_response` (premier plan,
    # compteur de l'outil) et `transcript` (arriere-plan, somme des messages assistant)
    # ne se comparent pas naivement. Annoncer une source qu'on n'a pas est pire que
    # n'annoncer rien : la cle reste absente quand aucun jeton n'a ete releve.
    if champs["jetons"] is not None:
        champs["source_jetons"] = "tool_response"
    return champs


CLES_USAGE = (("input", "input_tokens"),
              ("output", "output_tokens"),
              ("cache_creation", "cache_creation_input_tokens"),
              ("cache_read", "cache_read_input_tokens"))


def _jetons_du_transcript(chemin) -> dict:
    """Jetons d'une salle d'ARRIERE-PLAN, sommes sur son propre transcript.

    Pourquoi : le `tool_response` d'un Agent lance en arriere-plan (`async_launched`)
    ne porte AUCUN compteur — `jetons` valait None sur 100 % des salles d'arriere-plan,
    c'est-a-dire sur le mode par DEFAUT de l'orchestrateur. Le payload SubagentStop
    porte en revanche `agent_transcript_path` : le transcript DU SOUS-AGENT (a ne pas
    confondre avec `transcript_path`, celui de la session PARENTE).

    Forme etablie sur transcript reel (2026-09-20) : les lignes `type: assistant`
    portent `message.usage` avec input_tokens / output_tokens /
    cache_creation_input_tokens / cache_read_input_tokens. Ces compteurs sont PAR
    MESSAGE d'API, ils ne se cumulent pas — mais UNE MEME reponse d'API est ecrite sur
    PLUSIEURS lignes (une par bloc : thinking, text, tool_use), chacune repetant le
    meme `usage`. Sommer les lignes triple-compte : mesure sur une salle du jour,
    2 617 771 jetons ligne a ligne contre 1 471 730 apres dedoublonnage par
    `message.id`. On garde donc, par identifiant de message, le bloc au plus grand
    `output_tokens` (les blocs intermediaires portent un compte partiel).

    Lecture SEQUENTIELLE : un transcript fait 400-600 Ko et il y en a un par salle ;
    `read()` entier les chargerait tous en memoire. Fail-open absolu — chemin absent,
    illisible, ligne cassee, JSON invalide : les champs restent a None, jamais une
    exception, jamais un hook qui bloque la session.
    """
    vide = {"jetons": None, "jetons_detail": None, "source_jetons": None}
    if not isinstance(chemin, str) or not chemin:
        return vide
    par_message = {}
    try:
        with open(chemin, encoding="utf-8", errors="strict") as fh:
            for ligne in fh:
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    evt = json.loads(ligne)
                except ValueError:
                    continue  # une ligne cassee ne doit pas perdre tout le reste
                if not isinstance(evt, dict) or evt.get("type") != "assistant":
                    continue
                msg = evt.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                bloc = {}
                for court, brut in CLES_USAGE:
                    valeur = usage.get(brut)
                    bloc[court] = (valeur if isinstance(valeur, int)
                                   and not isinstance(valeur, bool) else 0)
                cle = msg.get("id") or evt.get("uuid")
                if not isinstance(cle, str):
                    cle = f"__anonyme_{len(par_message)}"
                ancien = par_message.get(cle)
                if ancien is None or bloc["output"] > ancien["output"]:
                    par_message[cle] = bloc
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return vide
    if not par_message:
        # Aucun message assistant : on ne SAIT pas, et 0 dirait qu'on a mesure zero.
        return vide
    detail = {court: sum(b[court] for b in par_message.values())
              for court, _ in CLES_USAGE}
    return {"jetons": sum(detail.values()), "jetons_detail": detail,
            "source_jetons": "transcript"}


def _apparier(session_id, fin_iso: str, agent_id=None):
    """(duree_s, appariement) d'un sous-agent — lancement `Agent` -> ce SubagentStop.

    `appariement` vaut "agent_id" quand la fin a pu etre reliee a SON propre lancement
    par identifiant, "fifo" quand il a fallu retomber sur l'heuristique, et None quand
    aucune duree n'est calculable.

    Historique : jusqu'au 2026-09-20 AUCUN identifiant n'etait journalise cote
    lancement, et le seul appariement possible etait FIFO — prouve faux le meme jour
    (une salle de 2 h 46 declaree terminee par le `subagent-stop` d'une AUTRE salle).
    La sonde sur payload reel a montre que `tool_response.agentId` (PostToolUse Agent)
    et `agent_id` (SubagentStop) sont le MEME identifiant : l'appariement exact existe
    des lors que le lancement a ete journalise avec son agentId.

    Repli FIFO conserve, et EXPLICITE : les lignes ecrites avant ce changement n'ont
    pas d'agent_id, et un Agent de premier plan voit son PostToolUse arriver APRES le
    SubagentStop — son identifiant n'est donc pas encore au journal. Le repli ne rend
    une duree que si UN SEUL lancement reste ouvert : une duree fausse est pire
    qu'aucune duree. Fail-open total : toute anomalie rend (None, None).
    """
    try:
        fin = datetime.datetime.fromisoformat(fin_iso)
        par_id = {}   # agent_id -> ts (datetime) du lancement
        fermes = set()  # agent_id deja apparies par un subagent-stop anterieur
        ouverts = []  # ts des lancements Agent pas encore apparies (repli FIFO)
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
                    deja = e.get("agent_id")
                    if isinstance(deja, str):
                        fermes.add(deja)
                    if ouverts:
                        ouverts.pop(0)  # FIFO : le plus ancien lancement ouvert se ferme en premier
                elif e.get("tool") == "Agent":
                    if e.get("statut") in STATUTS_TERMINAUX:
                        continue  # la ligne DIT que cette salle est finie : pas un "ouvert"
                    ts = e.get("ts")
                    if not isinstance(ts, str):
                        continue
                    try:
                        debut = datetime.datetime.fromisoformat(ts)
                    except ValueError:
                        continue
                    ouverts.append(debut)
                    aid = e.get("agent_id")
                    if isinstance(aid, str):
                        par_id[aid] = debut
        if isinstance(agent_id, str) and agent_id in par_id and agent_id not in fermes:
            return round((fin - par_id[agent_id]).total_seconds(), 1), "agent_id"
        if len(ouverts) != 1:
            return None, None  # aucun lancement ouvert, ou plusieurs (fan-out) : ambigu
        return round((fin - ouverts[0]).total_seconds(), 1), "fifo"
    except (OSError, ValueError, TypeError):
        return None, None


def _duree_appariee(session_id, fin_iso: str, agent_id=None):
    """Duree seule — contrat historique, conserve parce que des tests l'encodent."""
    return _apparier(session_id, fin_iso, agent_id)[0]


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
