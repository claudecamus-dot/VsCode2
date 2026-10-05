# +-- GÉNÉRÉ — NE PAS ÉDITER LOCALEMENT ---------------------------------------
# | Source de vérité : hub de supervision VScode5, .claude/dispositif/canon/scan_transcripts.py
# | Une correction faite ICI sera ÉCRASÉE à la prochaine propagation. Pour la
# | garder : la signaler au hub, qui corrige le canon et re-synchronise.
# | (Depuis le hub : « py .claude/dispositif/sync_dispositif.py » — ce script
# |  n'est pas déployé, il n'existe pas dans ce dépôt.)
# | Provenance canon : 2659606 du 2026-10-05 — permet, au prochain sync, de dire si
# | une différence vient d'une édition locale ou d'une avance du canon (voir
# | `determiner_cause` dans sync_dispositif.py au hub).
# +---------------------------------------------------------------------------

"""Superviseur d'agents — étage 1 (incrément A) : collecte déterministe, 0 token LLM.

Scanne incrémentalement les transcripts JSONL du projet (~/.claude/projects/<slug>/*.jsonl),
agrège l'usage réel des skills et sous-agents (état cumulé dans state.json, offsets par
fichier pour ne relire que le nouveau), puis régénère :
  - docs/wiki/technical/agents-supervision.md  (tableau de bord + TODO agents)
  - la section entre marqueurs TODO-AGENTS de docs/wiki/index.md
  - la section entre marqueurs TODO-AGENTS-HTML de docs/wiki.html (page rendue standalone)
  - .claude/orchestration/routing-hints.json (incrément O-C, consommé par agent-orchestrator :
    agents éprouvés/jamais-utilisés/en sommeil, vérifications oubliées, stats plan-vs-réel
    croisées avec .claude/orchestration/runs.jsonl)

Si .claude/supervision/diagnostic.json existe (écrit par la skill `agent-supervisor`,
étage 2 — diagnostic LLM), ses constats qualitatifs sont fusionnés dans la section TODO
du tableau de bord (distincts des constats déterministes, avec leur éventuelle
`proposition` de changement) et dans routing-hints.json (liste "prudence").

Incrément C (challenge, déterministe) : prudence automatique sur les agents en échec
répété dans runs.jsonl, agrégat des `resolution: <type> <nom>` (trous du catalogue,
TODO si récurrent), péremption du diagnostic à l'activité (DIAGNOSTIC_STALE_RUNS runs
non couverts) en plus de la cadence temporelle, et couverture OpenHub (table
agent_results de data/app.db, lecture seule, optionnelle). Ce script ne produit jamais
lui-même de diagnostic qualitatif — 0 token LLM, toujours.

Lancé automatiquement par le hook SessionStart (sortie : 1 ligne, jamais bloquant).
Usage manuel : py .claude/supervision/scan_transcripts.py [--full]
  --full : ignore l'état incrémental et rescanne tout l'historique.

Arbitrages (boucle propose→arbitre bouclée) : .claude/supervision/arbitrages.json
(versionné, édité à la main) enregistre les décisions humaines qui closent un constat
automatique — le TODO correspondant disparaît, la décision reste affichée dans la section
« Arbitrages enregistrés » et fusionnée dans routing-hints.json. L'usage réel reste mesuré.

Env (surcharges, utilisées par les tests) : AGENT_SUPERVISION_TRANSCRIPTS,
AGENT_SUPERVISION_STATE, AGENT_SUPERVISION_WIKI_PAGE, AGENT_SUPERVISION_WIKI_INDEX,
AGENT_SUPERVISION_RUNS, AGENT_SUPERVISION_ROUTING_HINTS, AGENT_SUPERVISION_DIAGNOSTIC,
AGENT_SUPERVISION_ARBITRAGES.
"""
import datetime as dt
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import traceback

SUP_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(SUP_DIR))
STATE_PATH = os.environ.get("AGENT_SUPERVISION_STATE") or os.path.join(SUP_DIR, "state.json")
WIKI_PAGE = os.environ.get("AGENT_SUPERVISION_WIKI_PAGE") or os.path.join(
    REPO, "docs", "wiki", "technical", "agents-supervision.md"
)
WIKI_INDEX = os.environ.get("AGENT_SUPERVISION_WIKI_INDEX") or os.path.join(
    REPO, "docs", "wiki", "index.md"
)
WIKI_HTML = os.environ.get("AGENT_SUPERVISION_WIKI_HTML") or os.path.join(
    REPO, "docs", "wiki.html"
)
RUNS_PATH = os.environ.get("AGENT_SUPERVISION_RUNS") or os.path.join(
    REPO, ".claude", "orchestration", "runs.jsonl"
)
PROMPTS_PATH = os.environ.get("AGENT_ORCHESTRATION_PROMPTS") or os.path.join(
    REPO, ".claude", "orchestration", "prompts.jsonl"
)
# Optionnel : présent seulement sur les projets qui embarquent une app OpenHub
# (SQLite `agent_results`, ex. VSCode2) — `openhub_stats()` rend None ailleurs.
OPENHUB_DB = os.environ.get("AGENT_SUPERVISION_OPENHUB_DB") or os.path.join(
    REPO, "data", "app.db"
)
ROUTING_HINTS_PATH = os.environ.get("AGENT_SUPERVISION_ROUTING_HINTS") or os.path.join(
    REPO, ".claude", "orchestration", "routing-hints.json"
)
DIAGNOSTIC_PATH = os.environ.get("AGENT_SUPERVISION_DIAGNOSTIC") or os.path.join(
    SUP_DIR, "diagnostic.json"
)
ARBITRAGES_PATH = os.environ.get("AGENT_SUPERVISION_ARBITRAGES") or os.path.join(
    SUP_DIR, "arbitrages.json"
)
# Optionnel, jamais regenere par ce scan (capture a la demande via
# skill_doctor_snapshot.py) : croise dans jamais_utilises (finding
# verification-manquante:jamais-utilises-vs-skill-doctor, 2026-09-15).
SKILL_DOCTOR_PATH = os.environ.get("AGENT_SUPERVISION_SKILL_DOCTOR") or os.path.join(
    SUP_DIR, "skill_doctor_last.txt"
)
DORMANT_DAYS = 30
# Au-delà de cet âge, l'instantané /skill-doctor ne contredit plus rien : le coût 7 j
# qu'il rapporte glisse avec le temps (finding skill_doctor_snapshot:instantane-perime-17j,
# arbitré le 2026-09-26 — un rapport de 17 jours faisait encore foi).
SKILL_DOCTOR_MAX_JOURS = 7
# Version de la LOGIQUE DE DÉTECTION (préfiltre + parsing des invocations dans
# scan()). À incrémenter à chaque fois qu'on apprend à reconnaître un mode
# d'invocation de plus : le scan rejoue alors l'intégralité des transcripts au
# lieu de reprendre après l'offset — sans quoi la nouvelle détection ne verrait
# jamais le passé déjà consommé par l'ancienne (cf. reset_si_detecteur_change).
# v2 : détection des slash-commands <command-name> (ajoutée le 2026-07-23, restée
#      sans effet rétroactif jusqu'au 2026-07-27).
DETECTOR_VERSION = 2
PROVEN_MIN = 3  # invocations à partir desquelles un agent/skill est "éprouvé"
DIAGNOSTIC_CADENCE_DAYS = 14  # au-delà : le diagnostic étage 2 est signalé "à relancer"
DIAGNOSTIC_STALE_RUNS = 3  # runs d'orchestration non couverts qui périment aussi le diagnostic
ECHEC_PRUDENCE_MIN = 2  # échecs en orchestration à partir desquels un agent passe en prudence
# `echec` est un mot du vocabulaire de log_run.py quasiment jamais utilisé en pratique
# (0 sur 131 runs mesurés le 2026-09-04, malgré 85 reprises journalisées ailleurs) : la
# branche echecs ci-dessus ne peut donc jamais se déclencher (finding
# `VScode5:prudence-routage-inerte`). Les reprises, elles, SONT journalisées à chaque
# run — seuil structurellement atteignable, ne remplace pas la branche echecs (gardée
# pour le jour où `echec` sera vraiment utilisé), s'y ajoute.
REPRISE_PRUDENCE_SEUIL = 2  # reprises sur un même run à partir desquelles il compte comme "fort"
REPRISES_PRUDENCE_MIN_RUNS = 2  # runs à forte reprise avant qu'un agent/skill passe en prudence
MARK_START = "<!-- TODO-AGENTS:START"
MARK_END = "<!-- TODO-AGENTS:END -->"
HTML_MARK_START = "<!-- TODO-AGENTS-HTML:START"
HTML_MARK_END = "<!-- TODO-AGENTS-HTML:END -->"


def transcript_dir() -> str:
    override = os.environ.get("AGENT_SUPERVISION_TRANSCRIPTS")
    if override:
        return override
    path = os.path.abspath(REPO)
    if len(path) >= 2 and path[1] == ":":
        path = path[0].lower() + path[1:]
    # Claude Code remplace TOUT caractère non alphanumérique par un tiret
    # (espaces compris — fix propagé depuis VScode5, 2026-07-23)
    slug = re.sub(r"[^A-Za-z0-9]", "-", path)
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    candidate = os.path.join(base, slug)
    if os.path.isdir(candidate):
        return candidate
    if os.path.isdir(base):  # tolérance à la casse (C: vs c:)
        for name in os.listdir(base):
            if name.lower() == slug.lower():
                return os.path.join(base, name)
    return candidate


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    """Écriture ATOMIQUE. Un `open(STATE_PATH, "w")` interrompu (Ctrl-C, coupure,
    valeur non sérialisable en fin de dict) laisse un state.json tronqué que
    `load_state` ne sait plus relire : le scan repart alors de zéro, en silence, et
    réagrège tout l'historique. On écrit à côté puis `os.replace` — atomique sous
    Windows comme sous POSIX : l'état publié est complet, ou reste le précédent."""
    tmp = f"{STATE_PATH}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_PATH)
    finally:
        if os.path.exists(tmp):   # échec en cours d'écriture : pas de reliquat
            os.remove(tmp)


def read_new_lines(path: str, offset: int):
    """Lit les lignes complètes ajoutées depuis offset ; ne consomme jamais une ligne partielle."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if size < offset:  # fichier tronqué/remplacé : repartir de zéro
        offset = 0
    if size == offset:
        return [], offset
    with open(path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset
    consumed = chunk[: end + 1]
    return [line for line in consumed.split(b"\n") if line.strip()], offset + len(consumed)


def record(agg: dict, key: str, ts: str) -> None:
    entry = agg.setdefault(key, {"n": 0, "first": ts, "last": ts})
    entry["n"] += 1
    if ts:
        if not entry["first"] or ts < entry["first"]:
            entry["first"] = ts
        if not entry["last"] or ts > entry["last"]:
            entry["last"] = ts


def reset_si_detecteur_change(state: dict) -> bool:
    """Rejoue tout l'historique quand la logique de détection a changé.

    Les offsets rendent le scan incrémental, mais ils survivaient au remplacement
    du détecteur : la détection des slash-commands ajoutée le 2026-07-23 n'a
    jamais revu les 854 Ko déjà consommés par l'ancienne version, et `skills` est
    resté vide pendant 4 jours (constat superviseur VSCode 2026-07-27 — offset
    854518 identique avant et après le commit qui ajoutait le détecteur), au point
    de faire passer tout le catalogue pour « jamais utilisé ».

    Les agrégats sont dérivés des seuls transcripts : on les remet à zéro en même
    temps que les offsets, sinon le rejeu compterait deux fois ce qui est déjà là.
    Contrepartie assumée : un rejeu ne voit que les transcripts encore présents sur
    le disque — mieux vaut un historique tronqué qu'un compteur figé à faux.
    """
    if state.get("detector_version") == DETECTOR_VERSION:
        return False
    state["files"] = {}
    state["skills"] = {}
    state["subagents"] = {}
    state["skills_journal"] = {}
    state["subagents_journal"] = {}
    state.pop("usage_offset", None)
    state.pop("usage_empreinte", None)
    state["detector_version"] = DETECTOR_VERSION
    state["last_replay"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return True


def scan(state: dict) -> int:
    tdir = transcript_dir()
    if reset_si_detecteur_change(state):
        print(
            f"Supervision agents : detecteur v{DETECTOR_VERSION} — rejeu complet "
            "des transcripts (offsets et agregats remis a zero)."
        )
    files_state = state.setdefault("files", {})
    skills = state.setdefault("skills", {})
    subagents = state.setdefault("subagents", {})
    fam_installees = installed_skills()  # filtre des /commandes : skills réelles seulement
    new_events = 0
    if not os.path.isdir(tdir):
        state["transcript_dir_missing"] = tdir
        return 0
    state.pop("transcript_dir_missing", None)
    for path in sorted(glob.glob(os.path.join(tdir, "*.jsonl"))):
        name = os.path.basename(path)
        offset = files_state.get(name, {}).get("offset", 0)
        lines, new_offset = read_new_lines(path, offset)
        for raw in lines:
            # Préfiltre octets : ne parser en JSON que les lignes candidates.
            if (b'"Skill"' not in raw and b'"subagent_type"' not in raw
                    and b"command-name" not in raw):
                continue
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            ts = obj.get("timestamp") or ""
            content = (obj.get("message") or {}).get("content")
            # Slash-commands : une skill invoquée en /commande n'émet PAS de
            # tool_use Skill — elle apparaît en <command-name> dans le message
            # utilisateur (constat superviseur VScode5 2026-07-23, propagé).
            if b"command-name" in raw:
                if isinstance(content, str):
                    textes = [content]
                elif isinstance(content, list):
                    textes = [b.get("text", "") for b in content
                              if isinstance(b, dict) and b.get("type") == "text"]
                else:
                    textes = []
                for txt in textes:
                    for m in re.finditer(
                            r"<command-name>/?([A-Za-z0-9:_-]+)</command-name>", txt):
                        if m.group(1) in fam_installees:
                            record(skills, m.group(1), ts)
                            new_events += 1
            if not isinstance(content, list):
                continue
            for blk in content:
                if not (isinstance(blk, dict) and blk.get("type") == "tool_use"):
                    continue
                tool_input = blk.get("input") or {}
                if blk.get("name") == "Skill" and tool_input.get("skill"):
                    record(skills, str(tool_input["skill"]), ts)
                    new_events += 1
                elif blk.get("name") in ("Agent", "Task"):
                    record(subagents, str(tool_input.get("subagent_type") or "(defaut)"), ts)
                    new_events += 1
        files_state[name] = {"offset": new_offset}
    state["last_scan"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return new_events


def scan_journal_usage(state: dict) -> int:
    """Le TROISIÈME canal : `usage.jsonl`, écrit par le hook PostToolUse.

    POURQUOI IL FALLAIT L'AJOUTER (mesure du 2026-09-02). Les deux canaux existants —
    `skills` et `subagents` — dérivent tous deux des transcripts, et 126 des 137
    transcripts référencés avaient disparu du disque. Résultat publié au tableau de
    bord : « Élaguer les skills BMAD : 43/46 jamais invoqués », alors que `usage.jsonl`
    portait au même instant 8 invocations de `bmad-code-review`, 10 de
    `bmad-review-edge-case-hunter`, 6 de `bmad-review-adversarial-general` et 1 de
    `bmad-technical-research` — plus `pdf-quality`. Cinq skills réellement invoquées
    étaient comptées « jamais utilisées », et le TODO proposait d'élaguer ce qui venait
    de servir le jour même. `dormants()` annonçait déjà « tous canaux confondus » : ses
    deux canaux étaient en fait la même source, disparue.

    Ce journal-ci est local, append-only et écrit à l'instant de l'appel : il survit à
    la purge des transcripts. Il ne REMPLACE pas les transcripts (eux seuls portent les
    slash-commands et le contexte), il les complète — d'où des agrégats SÉPARÉS
    (`skills_journal`, `subagents_journal`) plutôt qu'une fusion des compteurs : les
    deux sources se recouvrent partiellement, additionner leurs `n` inventerait un
    volume. Ce qui se déduit des deux, et qui est tout ce dont les TODO ont besoin,
    c'est « ce nom a-t-il servi, et quand pour la dernière fois » — cf. `derniers_usages`.

    Lecture INCRÉMENTALE par offset d'octets, comme les transcripts : un scan qui
    relirait tout à chaque session doublerait les compteurs. Fail-open intégral
    (hook SessionStart) : journal absent, ligne illisible, offset incohérent →
    on ignore, jamais d'exception.
    """
    chemin = os.environ.get("AGENT_SUPERVISION_USAGE") or os.path.join(SUP_DIR, "usage.jsonl")
    # Un `state.json` abime ne doit pas couter le scan de demarrage : les agregats et
    # l'offset sont RETYPES avant usage plutot que supposes sains (revue 2026-09-02 —
    # `usage_offset` en chaine levait un TypeError, `skills_journal` en liste un
    # AttributeError, et le docstring ci-dessus promettait « jamais d'exception »).
    if not isinstance(state.get("skills_journal"), dict):
        state["skills_journal"] = {}
    if not isinstance(state.get("subagents_journal"), dict):
        state["subagents_journal"] = {}
    skills, subagents = state["skills_journal"], state["subagents_journal"]
    offset = state.get("usage_offset")
    offset = offset if isinstance(offset, int) and offset >= 0 else 0
    try:
        taille = os.path.getsize(chemin)
    except OSError:
        return 0
    if offset > taille:      # journal tronqué ou remplacé : on repart de zéro
        offset, state["skills_journal"], state["subagents_journal"] = 0, {}, {}
        skills, subagents = state["skills_journal"], state["subagents_journal"]
    # Un journal remplacé par un contenu DIFFÉRENT mais de MÊME taille ou plus long
    # (rotation, restauration, `git checkout`) ne déclenche pas `offset > taille` :
    # la lecture reprendrait au milieu d'une ligne qui n'existe plus dans le nouveau
    # fichier (chasse aux cas limites, 2026-09-02). On empreinte exactement le
    # PRÉFIXE DÉJÀ CONSOMMÉ (`offset` octets, pas un nombre fixe) : un pur APPEND ne
    # touche jamais ces octets-là, quelle que soit la taille du fichier — une
    # empreinte à fenêtre fixe (4096) échouait sur tout journal plus petit que la
    # fenêtre, où un simple append fait grossir CE QUE `read(4096)` renvoie.
    if offset > 0:
        try:
            with open(chemin, "rb") as fh:
                empreinte = hashlib.sha1(fh.read(offset)).hexdigest()
        except OSError:
            empreinte = None
        if empreinte is not None and state.get("usage_empreinte") not in (None, empreinte):
            offset, state["skills_journal"], state["subagents_journal"] = 0, {}, {}
            skills, subagents = state["skills_journal"], state["subagents_journal"]
    n = 0
    avant_skills = {k: dict(v) for k, v in skills.items() if isinstance(v, dict)}
    avant_subagents = {k: dict(v) for k, v in subagents.items() if isinstance(v, dict)}
    try:
        with open(chemin, "rb") as fh:
            fh.seek(offset)
            for raw in fh:
                if not raw.endswith(b"\n"):      # ligne en cours d'écriture
                    break
                offset += len(raw)
                try:
                    e = json.loads(raw.decode("utf-8-sig", "replace"))
                except ValueError:
                    continue
                if not isinstance(e, dict) or e.get("event"):
                    continue
                ts = e.get("ts") if isinstance(e.get("ts"), str) else ""
                # `log_usage.py` marque `echec: true` quand la reponse de l'outil est
                # une erreur. On compte quand meme : le canal transcripts compte des
                # `tool_use` sans regarder leur issue, et deux canaux fusionnes par un
                # `max()` doivent mesurer LA MEME CHOSE. La question de la page est « ce
                # nom a-t-il servi », pas « a-t-il reussi » — R6 dit precisement que
                # l'etage 1 mesure la presence, jamais le fonctionnement.
                for canal, cle in ((skills, "skill"), (subagents, "subagent_type")):
                    nom = e.get(cle)
                    if isinstance(nom, str) and nom:
                        record(canal, nom, ts)
                        n += 1
    except Exception:
        # Fail-open integral, comme le docstring l'annonce : l'offset n'est PAS ecrit,
        # donc les lignes deja comptees le seraient une seconde fois au scan suivant.
        # On rend le state a son etat d'avant plutot que de publier un double comptage.
        state["skills_journal"], state["subagents_journal"] = avant_skills, avant_subagents
        return 0
    state["usage_offset"] = offset
    # Empreinte du prefixe maintenant consomme (`offset` octets APRES cette lecture) :
    # comparee au debut du PROCHAIN appel, elle detecte un remplacement de contenu
    # qu'un simple `offset > taille` laisserait passer (meme taille ou plus long).
    try:
        with open(chemin, "rb") as fh:
            state["usage_empreinte"] = hashlib.sha1(fh.read(offset)).hexdigest()
    except OSError:
        pass
    return n


def ratio_qualification(runs: list) -> dict:
    """Le ratio « demandes vues / demandes orchestrées », enfin calculable.

    Finding `VScode5:seuil-qualification-non-mesurable` (2026-09-02), option A. Le
    numérateur existait depuis juillet (`runs.jsonl`), le dénominateur nulle part :
    106 runs, 106 `orchestre`, 0 `direct-signale`, parce qu'une exécution directe ne
    se journalise pas. `orchestrator_gate.py` voit CHAQUE prompt et en écrit une ligne
    dans `prompts.jsonl` — c'est ce fichier-ci qu'on lit.

    On compare sur la MÊME fenêtre : le journal de prompts commence le jour où le hook
    s'est mis à écrire, donc ne compter que les runs postérieurs à sa première ligne.
    Comparer 106 runs de six semaines à trois jours de prompts donnerait un ratio
    supérieur à 1 — un chiffre qui ne veut rien dire est pire que pas de chiffre.

    Rend `None` tant que le journal est vide : un lecteur qui afficherait « 0 % » sur
    une mesure qui n'a pas commencé serait lu comme un résultat.
    """
    lignes = [l for l in load_jsonl(PROMPTS_PATH) if isinstance(l, dict)]
    vus = [l for l in lignes if not l.get("slash")]
    # Compte EXPLICITE, jamais une soustraction (revue 2026-09-02) : `len(lignes) -
    # len(vus)` publiait toute ligne mal formee comme « commande slash », un chiffre
    # invente sur une page d'arbitrage.
    slash = sum(1 for l in lignes if l.get("slash"))
    horodates = [l["ts"] for l in lignes if isinstance(l.get("ts"), str) and l["ts"]]
    if not lignes or not horodates:
        return None
    depuis = min(horodates)
    orchestres = [r for r in runs if isinstance(r, dict) and (r.get("ts") or "") >= depuis]
    part = (len(orchestres) / len(vus)) if vus else None
    # Un ratio > 1 ne veut rien dire (chasse aux cas limites, 2026-09-02 : 4 commandes
    # slash + 1 demande hors-slash pour 5 runs -> 500 %) : les commandes slash restent
    # HORS denominateur (elles ont deja tranche « orchestrer », rien a qualifier), mais
    # leurs runs, eux, comptent au numerateur -- d'ou la possibilite d'un ecart. On NE
    # BORNE PAS le chiffre (un ecart est un signal, pas une erreur a masquer), on le
    # SIGNALE pour que l'affichage ne publie pas un pourcentage absurde sans avertir.
    part_fiable = part is None or part <= 1
    return {"depuis": depuis, "prompts": len(vus), "slash": slash,
            "runs": len(orchestres), "part": part, "part_fiable": part_fiable}


def usage_affiche(state: dict, canal: str = "skills") -> dict:
    """Vue d'AFFICHAGE d'un canal et de son journal, fusionnés par nom.

    Pourquoi elle existe (revue du 2026-09-02, trois constats bloquants). Le canal
    journal avait été branché sur « Jamais utilisés » et sur rien d'autre : une skill
    connue du seul journal sortait de la liste des jamais-utilisées SANS entrer dans le
    tableau d'usage, donc disparaissait de la page — un faux négatif visible remplacé
    par une absence, plus difficile à rattraper. Et la section HTML, le canal que
    CLAUDE.md désigne comme celui à contrôler, n'avait pas suivi du tout : la même page
    affichait « 43/46 jamais invoqués » au-dessus de son propre TODO qui disait autre
    chose.

    `n` prend le MAXIMUM des canaux, jamais leur somme : les deux sources se recouvrent
    partiellement (un même appel peut être vu par le transcript ET par le hook), et
    additionner inventerait du volume — c'est déjà la règle posée par
    `scan_journal_usage`. Le maximum est donc une borne BASSE assumée : « au moins tant
    d'appels », ce qui suffit à tout ce que la page en fait (a servi / dort depuis).
    `last` prend le maximum des dates, `first` le minimum des dates non vides.
    """
    fusion = {}
    for source in (canal, f"{canal}_journal"):
        entrees = state.get(source)
        if not isinstance(entrees, dict):
            continue
        for nom, e in entrees.items():
            if not isinstance(e, dict):
                continue
            cur = fusion.setdefault(nom, {"n": 0, "first": "", "last": ""})
            n = e.get("n")
            cur["n"] = max(cur["n"], n if isinstance(n, int) else 0)
            first = e.get("first") or ""
            if first and (not cur["first"] or first < cur["first"]):
                cur["first"] = first
            cur["last"] = max(cur["last"], e.get("last") or "")
    return fusion


def derniers_usages(state: dict) -> dict:
    """`{nom: dernier usage ISO}` sur les QUATRE canaux — la seule base honnête pour
    dire « a servi » ou « dort depuis ».

    Prend le MAXIMUM des dates, jamais un choix de canal : une entité qui a servi dans
    un canal quelconque n'est ni endormie ni « jamais invoquée ». C'est l'extension
    exacte du raisonnement déjà écrit dans `dormants()` le 2026-09-01, à la source qui
    manquait."""
    derniers = {}
    for canal in ("skills", "subagents", "skills_journal", "subagents_journal"):
        for nom, e in (state.get(canal) or {}).items():
            last = (e or {}).get("last", "") or ""
            if last > derniers.get(nom, ""):
                derniers[nom] = last
    return derniers


def mesure_incomplete(state: dict) -> dict:
    """Constat de FIABILITÉ de la mesure — jamais un correctif de la mesure elle-même.

    Le scan est incrémental : `state['files']` garde un offset PAR FICHIER, mais rien
    n'en retire l'entrée quand Claude Code purge le transcript correspondant sur le
    disque. Le scan suivant n'a alors plus rien à relire pour ce nom — mais il
    continue de publier les compteurs déjà accumulés (`skills`, `subagents`) comme une
    mesure courante, sans le dire. Finding `state-transcripts-absents` (2026-09-01) :
    0 transcript sur disque, `state.json` en référençait 2, absents tous les deux, et
    le scan a quand même publié 75 skills « jamais utilisées » sur cette base — un
    `n=0` qui ne veut alors plus dire « jamais invoquée » mais « on ne regarde plus »,
    sans qu'aucun signal ne distingue les deux.

    Retourne TOUJOURS un dict (jamais None) : `transcripts_absents=0` sur une base
    saine se distingue ainsi de « jamais mesuré », et l'appelant peut dériver
    `mesure_non_fiable` par un simple test de vérité sans cas particulier. Fail-open
    total (hook SessionStart) : un chemin illisible compte comme absent, jamais une
    exception.

    Ne supprime ni ne recalcule aucun compteur — l'arbitrage du 2026-09-02 est
    explicite là-dessus : l'information doit porter sa fiabilité, pas disparaître."""
    files = state.get("files") or {}
    tdir = transcript_dir()
    absents = 0
    for name in files:
        try:
            if not os.path.isfile(os.path.join(tdir, str(name))):
                absents += 1
        except (OSError, TypeError, ValueError):
            absents += 1
    dernier = ""
    for canal in ("skills", "subagents"):
        for entry in (state.get(canal) or {}).values():
            if not isinstance(entry, dict):
                continue
            last = entry.get("last") or ""
            if last > dernier:
                dernier = last
    # Le TROISIEME canal (chasse aux cas limites, 2026-09-02) : un `usage.jsonl`
    # present mais dont l'offset ne s'est jamais pose (permission refusee, chemin
    # incoherent) rend 0 evenement neuf pour la MEME raison qu'un canal sain qui n'a
    # simplement rien de nouveau -- c'est exactement le defaut que cet increment
    # existe pour corriger sur les transcripts, reintroduit tel quel sur le canal
    # neuf s'il n'est pas signale a son tour.
    chemin_usage = os.environ.get("AGENT_SUPERVISION_USAGE") or os.path.join(SUP_DIR, "usage.jsonl")
    journal_muet = os.path.isfile(chemin_usage) and not isinstance(state.get("usage_offset"), int)
    return {
        "transcripts_absents": absents,
        "total_fichiers": len(files),
        "dernier_evenement": dernier,
        "journal_usage_muet": journal_muet,
    }


def installed_skills() -> dict:
    """{nom_skill: famille} — projet (.claude/skills), BMAD (bmad-*), global (~/.claude/skills)."""
    fam = {}
    for d in sorted(glob.glob(os.path.join(REPO, ".claude", "skills", "*"))):
        if os.path.isdir(d):
            name = os.path.basename(d)
            fam[name] = "BMAD" if name.startswith("bmad-") else "projet"
    for d in sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "skills", "*"))):
        if os.path.isdir(d):
            fam.setdefault(os.path.basename(d), "global")
    return fam


_AGENTS_TEXT = None


def _agents_text() -> str:
    """Concaténation (mémoïsée) des .claude/agents/*.md, pour repérer les skills
    qu'un sous-agent déclare consommer comme ressource."""
    global _AGENTS_TEXT
    if _AGENTS_TEXT is None:
        parts = []
        for a in sorted(glob.glob(os.path.join(REPO, ".claude", "agents", "*.md"))):
            try:
                with open(a, encoding="utf-8") as fh:
                    parts.append(fh.read())
            except OSError:
                pass
        _AGENTS_TEXT = "\n".join(parts)
    return _AGENTS_TEXT


def skills_reference_declares() -> set:
    """Skills déclarés « bibliothèque/référence » par ARBITRAGE humain, dans le
    fichier versionné `.claude/supervision/skills_reference.json` (liste de noms,
    ou {"skills": [...]}). Complément explicite des deux critères structurels de
    non_invocation_skills, pour les usages qu'aucun critère déterministe ne peut
    voir : skill consommé par lecture depuis les projets CIBLES (deck-design-library,
    restitution-deck-design) ou exécuté inline par la session qui le suit sans
    l'invoquer formellement (veille-agentic, prouvé par son artefact daté
    .claude/veille/veille.json — finding agent-mort du 2026-07-27). Ce n'est pas une
    liste codée en dur : c'est une donnée par projet, arbitrée et tracée. Fichier
    absent ou invalide → ensemble vide (fail open)."""
    path = os.path.join(REPO, ".claude", "supervision", "skills_reference.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return set()
    if isinstance(data, dict):
        data = data.get("skills", [])
    if not isinstance(data, list):
        return set()
    return {s for s in data if isinstance(s, str)}


# Un frontmatter de skill qui commence sa `description` par DEPRECATED — la seule
# declaration d obsolescence qui vienne de l EDITEUR et non de notre compteur.
_RE_DESCRIPTION = re.compile(r"^description:\s*(.+)$", re.MULTILINE)
_RE_DEPRECATED = re.compile(r"\bDEPRECATED\b", re.IGNORECASE)
_RE_REMPLACANT = re.compile(r"in favor of `([A-Za-z0-9_.-]+)`", re.IGNORECASE)


def skills_depreciees(fam: dict) -> dict:
    """`{nom: remplacant}` pour les skills que LEUR PROPRE frontmatter declare
    depreciees (`remplacant` vaut "" quand la description n en nomme aucun).

    Pourquoi cette lecture existe (finding `bmad-catalogue-elagage`, 2026-09-04). Le
    TODO « Elaguer les skills BMAD : N/M jamais invoques » melange deux choses qui
    n ont rien a voir : ce que BMAD DECLARE obsolete, et ce que notre compteur n a pas
    vu passer. Le second a failli faire desinstaller sur VSCode, le 2026-09-01,
    `bmad-forge-idea` et `bmad-party-mode` — les deux skills qui ont porte toute la
    refonte du produit COMOP trois jours plus tard. Le premier, lui, est une donnee
    sure, lisible dans le depot, independante de la fiabilite de notre mesure.

    Deterministe et sans liste codee en dur (meme exigence que `non_invocation_skills`)
    : on LIT la `description` du frontmatter. A ce jour BMAD v6.10.0 en declare quatre
    — bmad-create-architecture, bmad-create-prd, bmad-edit-prd, bmad-validate-prd —
    mais la liste n est ecrite nulle part ici : elle suivra l editeur.

    Fail-open : SKILL.md absent, illisible ou sans frontmatter -> le nom n est
    simplement pas declare deprecie. On ne suppose jamais l obsolescence."""
    out = {}
    for nom in (fam or {}):
        chemin = os.path.join(REPO, ".claude", "skills", nom, "SKILL.md")
        try:
            with open(chemin, encoding="utf-8", errors="ignore") as fh:
                tete = fh.read(4096)
        except OSError:
            continue
        m = _RE_DESCRIPTION.search(tete)
        if not m or not _RE_DEPRECATED.search(m.group(1)):
            continue
        r = _RE_REMPLACANT.search(m.group(1))
        out[nom] = r.group(1) if r else ""
    return out


def non_invocation_skills(fam: dict) -> set:
    """Skills dont la valeur se consomme en LISANT/EXÉCUTANT leurs ressources, jamais
    via l'outil Skill — le compteur d'invocations ne peut donc structurellement pas les
    voir, et `n=0` n'y prouve aucune inutilité (constat superviseur #2). Déterministe,
    sans liste codée en dur — un skill (hors BMAD, dont le tri est traité à part) en est si :
      - il livre un dossier `scripts/` (bibliothèque de code importée/exécutée), ou
      - il est cité par son CHEMIN `skills/<nom>` dans un `.claude/agents/*.md` :
        un sous-agent le déclare comme ressource à lire/exécuter (cf. ppt-designer
        « Skills you rely on » — lui n'a PAS l'outil Skill). On exige le chemin, pas
        une simple mention du nom : sinon un skill juste *nommé* en prose (ex. un
        agent qui écrit « within agent-orchestrator ») serait happé à tort, ou
      - il est déclaré par arbitrage dans skills_reference.json (cf.
        skills_reference_declares — usages réels invisibles des deux critères
        structurels ci-dessus).
    Un skill sans `scripts/`, cité par chemin nulle part et non déclaré reste, lui,
    un vrai « jamais utilisé » — on ne suppose pas l'usage sans preuve."""
    text = _agents_text()
    declares = skills_reference_declares()
    out = set()
    for name, family in fam.items():
        if family == "BMAD":
            continue
        proj = os.path.join(REPO, ".claude", "skills", name, "scripts")
        glb = os.path.join(os.path.expanduser("~"), ".claude", "skills", name, "scripts")
        if name in declares:
            out.add(name)
        elif os.path.isdir(proj) or os.path.isdir(glb):
            out.add(name)
        elif re.search(r"skills/" + re.escape(name) + r"(?![\w-])", text):
            out.add(name)
    return out


def days_since(ts: str):
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = dt.datetime.now(t.tzinfo) if t.tzinfo else dt.datetime.now()
    return (now - t).days


# Lignes JSONL non parsables au dernier passage, par chemin — lues par main() pour
# les SIGNALER. Un journal abîmé ne doit ni casser le démarrage ni disparaître sans
# un mot : le run que porte la ligne perdue n'apparaît nulle part ailleurs.
LIGNES_ILLISIBLES = {}


def _journal_incidents() -> str:
    """Chemin du journal des incidents de scan, surchargeable pour les tests."""
    # `SUP_DIR` et non un `ROOT` inexistant : ce script vit DANS
    # .claude/supervision/, le journal se pose a cote de lui.
    return os.environ.get("AGENT_SUPERVISION_SCAN_INCIDENTS") or os.path.join(
        SUP_DIR, "scan_incidents.jsonl")


def signaler_incident(exc: BaseException) -> int:
    """Un scan qui plante le DIT, et laisse une trace qui lui survit.

    Rend le code de sortie a utiliser. Le `except Exception` du point d entree est
    delibere — un hook SessionStart qui leve bloque l ouverture de session, ce qui est
    pire que de ne pas scanner — et il n est pas remis en cause : le code reste 0 par
    defaut. Ce n est pas le rattrapage qui etait fautif, c est ce qu il laissait.

    Incident vecu le 2026-09-01 : un `NameError: name state is not defined` introduit
    dans `build_todos` a ete absorbe, seule trace « Supervision agents : scan ignore
    (NameError: ...) », code de sortie 0. Le scan n avait rien produit — ni TODO, ni
    routing-hints, ni page — et rien ne le disait. La regression est restee invisible
    pendant deux commandes. Trois manques distincts, corriges ici :

    1. « ignore » ment sur la nature de l evenement : le mot dit un saut delibere, le
       fait est un plantage. On ecrit ECHEC.
    2. aucune localisation : une classe et un message, sans fichier ni ligne. On sort
       la pile complete.
    3. aucune trace durable : la ligne defilait dans un demarrage de session et
       disparaissait. On l ecrit dans un journal, et le scan SUIVANT la remonte —
       c est le point qui compte, parce qu il repare la visibilite de la DUREE et pas
       seulement celle de l instant.

    `AGENT_SUPERVISION_SCAN_STRICT=1` rend le plantage fatal (code 1), pour les tests,
    la CI et l appel manuel — la ou rien n est bloque, un plantage doit se voir.
    """
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    # « rien n a ete produit » etait plus large que ce que ce code sait : le
    # plantage peut survenir APRES l ecriture des fichiers. On dit ce qui est
    # certain — le scan s est interrompu — et on laisse la pile situer ou.
    print(f"Supervision agents : ECHEC, le scan s est interrompu en cours "
          f"({exc.__class__.__name__}: {exc}) - ce qui suit ce point n a pas "
          f"ete fait")
    print(trace.rstrip())
    print("  incident enregistre : le prochain scan le remontera. "
          "Le rendre fatal : AGENT_SUPERVISION_SCAN_STRICT=1")
    try:
        with open(_journal_incidents(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "type": "incident",
                "date": dt.datetime.now().isoformat(timespec="seconds"),
                "exception": exc.__class__.__name__,
                "message": str(exc),
                "trace": trace,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass  # un journal illisible ne doit pas, lui non plus, bloquer la session
    return 1 if os.environ.get("AGENT_SUPERVISION_SCAN_STRICT") else 0


def incidents_a_signaler() -> list:
    """Les incidents survenus depuis le dernier acquittement, en lignes lisibles.

    Appelee par `main()` au demarrage : c est ce qui empeche un plantage de
    disparaitre avec le defilement de la session ou il s est produit.
    """
    chemin = _journal_incidents()
    if not os.path.isfile(chemin):
        return []
    entrees = []
    try:
        with open(chemin, encoding="utf-8", errors="replace") as fh:
            for ligne in fh:
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    e = json.loads(ligne)
                except ValueError:
                    continue
                if e.get("type") == "acquitte":
                    entrees = []          # tout ce qui precede a deja ete remonte
                else:
                    entrees.append(e)
    except OSError:
        return []
    def _ligne(e: dict) -> str:
        date = e.get("date", "?")
        classe = e.get("exception", "?")
        message = e.get("message", "?")
        return date + "  " + classe + ": " + message

    return [_ligne(e) for e in entrees]


def acquitter_incidents() -> None:
    """Marque les incidents comme remontes, pour ne pas les repeter a chaque scan."""
    try:
        with open(_journal_incidents(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "acquitte",
                                 "date": dt.datetime.now().isoformat(timespec="seconds")},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_jsonl(path: str) -> list:
    """Journal JSONL, lecture TOLÉRANTE aux octets invalides.

    `errors="replace"` : un seul octet non-UTF-8 — ce que produit `Add-Content` en
    PowerShell — levait `UnicodeDecodeError`, qui échappait à `except OSError`,
    remontait jusqu'au `except Exception` de `main()` et annulait TOUT le scan de
    démarrage avec pour seule trace « scan ignore ». Les lignes qui restent non
    parsables sont comptées dans `LIGNES_ILLISIBLES[path]`, plus sautées en silence."""
    out = []
    illisibles = 0
    try:
        # utf-8-sig, comme log_run.py/log_usage.py (memoire 2026-07-23) : un pipe
        # PowerShell 5.1 prefixe un BOM qui, sans ce mode, colle a la premiere ligne
        # et la fait echouer au parse -- perdue DEFINITIVEMENT ici, puisque le
        # lecteur avance en offset d'octets sur certains journaux (chasse aux cas
        # limites, 2026-09-02). Sans BOM, utf-8-sig == utf-8.
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    illisibles += 1
    except OSError:
        pass
    LIGNES_ILLISIBLES[path] = illisibles
    return out


def load_arbitrages() -> list:
    """Décisions humaines closant des constats automatiques (fichier versionné, jamais écrit ici).
    Chaque entrée : {cible, decision, date, source, categories?} — cible = nom de skill ou
    famille:<Nom> ; `categories` (optionnel) restreint les catégories de constats fermées
    par cet arbitrage (défaut : toutes, cf. finding_arbitre). Le contrôle du vocabulaire
    des catégories est fait à part, par `categories_inconnues`."""
    try:
        with open(ARBITRAGES_PATH, encoding="utf-8") as fh:
            entries = json.load(fh).get("arbitrages", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("cible") and e.get("decision")]


# Doit rester le MIROIR de `CATEGORIES` dans write_diagnostic.py : ce qui s'écrit dans
# un diagnostic doit pouvoir se fermer dans un arbitrage. Le volet 2 (pratiques
# d'ingénierie, documentation, cadrage produit) manquait ici au rapatriement du
# 2026-07-28 — le contrôle criait « hors vocabulaire » sur les 5 catégories `pratique-*`
# réellement utilisées par les arbitrages du hub, alors qu'elles ferment bien leurs
# constats. Un garde-fou qui hurle à tort finit ignoré : c'est lui qu'on corrige.
CATEGORIES_CONNUES = (
    # Volet 1 — usage des agents
    "ko-repete", "inefficacite", "agent-mort", "interaction",
    "verification-manquante", "non-convergence",
    # Volet 2 — pratiques d'ingénierie, documentation, cadrage produit
    "pratique-test", "pratique-dev", "pratique-revue", "pratique-design",
    "pratique-doc", "pratique-produit",
    # Volet 2 — securite (agent-securite installe le 2026-09-13). Absente jusqu'au
    # 2026-09-21 : un arbitrage du 2026-09-11 portant cette categorie etait
    # inoperant depuis son ecriture, hors vocabulaire. Distincte de "securite"
    # (volet 3, dimension d'audit) : couvre les PRATIQUES, pas le niveau mesure.
    "pratique-securite",
    # Volet 3 - dimensions de l'audit technique (miroir de DIM_AUDIT dans
    # scan_projets.py). Absentes jusqu'au 2026-09-21 : un constat d'audit
    # corrige, teste et commite ne pouvait PAS etre ferme, log_arbitrage le
    # refusant « hors vocabulaire ». Mesure du 2026-09-20 : 7 constats des lots
    # VSCode et VScode6 dans ce cas, et le hook de session signalait deja
    # « categorie(s) hors vocabulaire, sans effet ». Meme raison que le
    # rattrapage du volet 2 : un garde-fou qui hurle a tort finit ignore.
    "robustesse", "performance", "risque_technique", "securite",
    # Orthographe heritee, presente dans arbitrages.json : la refuser ferait
    # crier le controle sur des entrees reelles deja ecrites.
    "risque-technique",
    "autre",
)


def categories_inconnues(arbitrages: list) -> list:
    """Catégories hors vocabulaire dans `arbitrages.json` — signalées, jamais corrigées
    (fichier humain). Sans ce contrôle, une faute de frappe (`verification_manquante`)
    donnerait un arbitrage qui ne ferme rien, sans le moindre message."""
    vues = set()
    for a in arbitrages or []:
        cats = a.get("categories")
        if isinstance(cats, list):
            vues.update(c for c in cats if c not in CATEGORIES_CONNUES)
        elif cats is not None:
            vues.add(f"{a.get('cible')}: champ `categories` mal formé")
    return sorted(vues)


# Bascule de l'amnistie de l'heritage (cf. `_couvre`) : avant cette date, un
# arbitrage sans `categories` ne ferme rien ; apres, `log_arbitrage.py` garantit
# qu'il y en a toujours un.
AMNISTIE_HERITAGE = "2026-09-19"


def _couvre(arbitrage: dict, categorie: str) -> bool:
    """Cet arbitrage ferme-t-il cette CATÉGORIE de constat ?

    `categories` absent = ferme tout (rétro-compatible). Liste = ferme exactement ces
    catégories — donc `[]` ne ferme rien, la lecture naturelle. Un champ mal formé
    (chaîne, nombre) ne ferme rien non plus : un `in` sur une chaîne matcherait par
    sous-chaîne (`"interaction" in "interactions-multiples"`), silencieusement faux."""
    cats = arbitrage.get("categories")
    if cats is None:
        # AMNISTIE DE L'HERITAGE (arbitre par l'utilisateur le 2026-09-19).
        # `refuser_arbitrage.py` n'a jamais ecrit `categories` : 137 des 258
        # arbitrages reels n'en ont pas, et « ferme tout » les faisait masquer a
        # perpetuite TOUT constat futur de leur cible, toutes categories
        # confondues. Plus l'utilisateur arbitrait, plus le dispositif devenait
        # aveugle. Les entrees ANTERIEURES a la bascule ne ferment donc plus rien ;
        # au-dela, `log_arbitrage.py` exige `categories`, donc le cas ne se
        # represente pas. Une entree SANS date garde l'ancien comportement : on ne
        # peut pas la situer par rapport a la bascule, et la trancher au hasard
        # effacerait une decision humaine.
        date = str(arbitrage.get("date") or "")
        return not (date and date < AMNISTIE_HERITAGE)
    return isinstance(cats, list) and categorie in cats


def _vise_ce_constat(arbitrage: dict, finding: dict) -> bool:
    """Un arbitrage qui NOMME son constat (champ `titre`) ne ferme que celui-là ;
    un arbitrage sans titre garde sa portée large (cible + catégorie).

    Rétro-compatibilité totale par construction : mesuré le 2026-09-18, AUCUN des
    241 arbitrages du hub ne porte de `titre` — tous conservent donc exactement le
    comportement d'avant. La précision n'arrive que sur les arbitrages à venir.

    Le défaut corrigé (F5, arbitré le 2026-09-18) : avec 241 décisions accumulées,
    presque toute paire (cible, catégorie) est déjà « couverte ». Un constat neuf
    naissait masqué par une décision rendue sur un tout autre sujet — mesuré sur
    2 constats du 18/09, fermés par des arbitrages des 15 et 17."""
    titre_arb = str(arbitrage.get("titre") or "").strip().lower()
    if titre_arb:
        return titre_arb == str(finding.get("titre") or "").strip().lower()
    # Arbitrage SANS titre (portee large, cible+categorie) : ne ferme que s'il est
    # POSTERIEUR OU DU MEME JOUR que la premiere mesure du constat (`vu_le`). Sans
    # cette borne, un arbitrage ancien sur un tout autre sujet masque a perpetuite
    # tout constat NEUF de la meme cible/categorie -- mesure le 2026-09-21 sur 2
    # constats de VSCode1 (vu_le 2026-09-18), fermes par des arbitrages de juillet
    # (finding VScode5:masquage-par-arbitrage-sans-rapport). Un `vu_le` absent sur
    # le constat garde l'ancien comportement (retro-compatibilite : rien ne changerait
    # pour un appelant qui ne remplit pas ce champ).
    vu_le = str(finding.get("vu_le") or "")[:10]
    if not vu_le:
        return True
    date_arb = str(arbitrage.get("date") or "")[:10]
    if len(date_arb) != 10:
        return True  # arbitrage sans date exploitable : comportement inchange
    return date_arb >= vu_le


def finding_arbitre(finding: dict, arbitrages: list = None, respecter_re_challenge: bool = True,
                    posterieur_a: str = "") -> bool:
    """Vrai si un arbitrage ferme ce constat : même `cible` ET catégorie couverte
    (cf. `_couvre`) — ainsi un arbitrage de *routage* (ex. « agent activé ») cesse de
    masquer un constat de *vérification/qualité* sur la même cible (friction
    cible-suppression, 2026-07-21).

    `re_challenge: true` sur le CONSTAT prime sur les arbitrages ANTÉRIEURS au diagnostic
    (2026-07-28) : le superviseur déclare re-challenger une décision close avec des
    données NOUVELLES — ce que le fichier d'arbitrages autorise depuis toujours dans sa
    doctrine (« un arbitrage n'est pas une preuve d'utilité »), mais que le filtre rendait
    impossible en pratique. La granularité par catégorie n'y suffit pas : deux constats
    différents sur la même cible partagent souvent la même catégorie (constat prio 5 du
    2026-07-28 — 3 constats sur 4 masqués avant d'atteindre le tableau de bord). Un
    arbitrage pris DEPUIS le diagnostic, lui, referme le constat : c'est la réponse de
    l'humain, la boucle propose→arbitre se termine.

    `respecter_re_challenge=False` neutralise ce passe-droit : un re-challenge rouvre
    l'AFFICHAGE (l'humain doit voir le constat pour le trancher), jamais le ROUTAGE.
    Sans quoi le superviseur écraserait de lui-même une décision humaine dans
    `prudence` — exactement l'auto-modification que sa propre gouvernance interdit,
    et le cas s'est produit dès le premier usage : un constat `ko-repete` re-challengé
    sur `revue-increment` y plaçait la skill que le playbook `dev-verifie` rend
    obligatoire, deux hints contradictoires livrés ensemble."""
    cible = finding.get("cible")
    if not cible:
        return False
    cat = finding.get("categorie")
    couvrants = [a for a in arbitrages or [] if a.get("cible") == cible and _couvre(a, cat)
                 and _vise_ce_constat(a, finding)]
    if not couvrants:
        return False
    if not (respecter_re_challenge and finding.get("re_challenge") is True):
        return True
    # Un arbitrage du JOUR du diagnostic ou postérieur tranche le re-challenge : c'est
    # la réponse humaine à ce constat précis, elle referme la boucle. Sans cette règle,
    # un constat re-challengé resterait un TODO actif jusqu'à la réécriture du
    # diagnostic (cadence 14 j) alors même que l'humain l'aurait tranché — or il
    # l'arbitre presque toujours le jour même, d'où la comparaison à la JOURNÉE (les
    # deux champs n'ont pas la même précision : date seule contre horodatage complet).
    # La reference est la date ou CE constat a ete vu (`vu_le`, posee par
    # write_diagnostic), pas la date du fichier : une fusion (`--fusionner`) avance
    # `generated` a maintenant sans changer les constats reconduits, et chaque passe du
    # superviseur rouvrait ainsi tous les constats deja arbitres (19 ouverts au lieu de 6
    # le 2026-09-24, cause n1 du « ca reapparait »).
    jour = (finding.get("vu_le") or posterieur_a or "")[:10]
    if not jour:
        return False
    return any((a.get("date") or "")[:10] >= jour for a in couvrants)


def diagnostic_masques(diagnostic, arbitrages: list = None) -> list:
    """Constats du diagnostic écartés par un arbitrage — rendus VISIBLES (2026-07-28).

    Le filtrage était silencieux : rien dans le tableau de bord (md + HTML) ni sur la
    sortie du scan n'indiquait qu'un constat avait été écarté, si bien que le superviseur
    pouvait écrire cinq constats justes et n'en afficher aucun. On n'affiche que le titre
    et la cible : l'humain voit ce que sa décision passée continue de fermer, et peut
    demander un re-challenge."""
    return [
        {"titre": _libelle_finding(f), "cible": f.get("cible") or "?"}
        for f in _findings(diagnostic)
        if _titre_court(f) and finding_arbitre(f, arbitrages, posterieur_a=_genere_le(diagnostic))
    ]


def _findings(diagnostic) -> list:
    """Constats exploitables d'un diagnostic. `diagnostic.json` est une donnée machine
    éditable à la main : une entrée mal formée (chaîne au lieu d'objet, `findings` qui
    n'est pas une liste) ne doit pas faire échouer la régénération du wiki ET des hints."""
    if not isinstance(diagnostic, dict):
        return []
    findings = diagnostic.get("findings")
    return [f for f in findings if isinstance(f, dict)] if isinstance(findings, list) else []


def _genere_le(diagnostic) -> str:
    return (diagnostic or {}).get("generated", "") if isinstance(diagnostic, dict) else ""


def _titre_court(finding: dict) -> str:
    """Titre sur UNE ligne : il est rendu dans une puce markdown et dans un `<li>`, où
    un saut de ligne casserait la mise en forme."""
    return " ".join((finding.get("titre") or "").split())


# Statut de preuve d'un constat (schema write_diagnostic.py, 2026-09-29, trouvaille de
# veille « Silence Is Endorsement ») : `mesure` (defaut, retro-compatible), `a_verifier`,
# `hypothese`. Un constat non mesure DOIT se lire comme tel partout ou il est rendu :
# sans l'etiquette, le lecteur voit un constat affirme — perte de statut mesuree le
# 2026-09-29 (grep statut_preuve = 0 dans ce script et dans agents-supervision.md).
STATUTS_PREUVE_LIBELLES = {"a_verifier": "À VÉRIFIER", "hypothese": "HYPOTHÈSE"}


def _libelle_finding(finding: dict) -> str:
    """Titre court PRÉFIXÉ de son statut de preuve quand il n'est pas `mesure`. Une valeur
    inconnue est rendue telle quelle plutôt que tue : l'erreur sûre est de montrer le doute."""
    titre = _titre_court(finding)
    statut = str(finding.get("statut_preuve") or "mesure").strip() or "mesure"
    if not titre or statut == "mesure":
        return titre
    return f"[{STATUTS_PREUVE_LIBELLES.get(statut, statut.upper())}] {titre}"


def load_diagnostic() -> dict:
    """Constats qualitatifs de la skill agent-supervisor (étage 2) ; None si jamais lancée."""
    try:
        with open(DIAGNOSTIC_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def diagnostic_a_jour(diagnostic, runs: list = None) -> bool:
    """Périmé au-delà de la cadence temporelle, OU dès que trop d'orchestrations récentes
    (incrément C : seuil d'activité) ne sont pas couvertes par le dernier diagnostic."""
    if not diagnostic:
        return False
    generated = diagnostic.get("generated", "")
    d = days_since(generated)
    if d is None or d > DIAGNOSTIC_CADENCE_DAYS:
        return False
    non_couverts = sum(1 for r in runs or [] if (r.get("ts") or "") > generated)
    return non_couverts < DIAGNOSTIC_STALE_RUNS


def diagnostic_todos(diagnostic, arbitrages: list = None) -> list:
    """Top constats qualitatifs (étage 2), triés par priorité, pour fusion dans le TODO wiki.

    Un constat fermé par un arbitrage (`finding_arbitre` : même cible ET catégorie couverte)
    est exclu — même contrat que `build_todos()` pour les constats déterministes : une
    décision humaine ferme le TODO affiché, sans effacer la mesure réelle ni le diagnostic."""
    genere = _genere_le(diagnostic)
    findings = [
        f for f in _findings(diagnostic)
        if not finding_arbitre(f, arbitrages, posterieur_a=genere)
    ]
    findings.sort(key=lambda f: -(f.get("priorite") or 0))
    out = []
    # `[:5]` = le plafond de la skill (« 5 constats max ») ; `write_diagnostic.py` le
    # refuse désormais à l'écriture, donc plus rien ne se perd ici en silence.
    for f in findings[:5]:
        titre = _libelle_finding(f)
        if not titre:
            continue
        reco = (f.get("recommandation") or "").strip()
        prop = (f.get("proposition") or "").strip()
        item = f"**{titre}**" + (f" — {reco}" if reco else "")
        if prop:  # incrément C : changement concret proposé, à arbitrer (jamais auto-appliqué)
            item += f" · **Proposition** : {prop}"
        out.append(item)
    return out


def catalogue_gaps(runs: list) -> dict:
    """Trous du catalogue (incrément C) : agrégat des `resolution: <type> <nom>` notés par
    l'orchestrateur quand aucun agent ne couvrait la demande (restauration/évolution/création)."""
    gaps = {}
    for r in runs:
        for res, nom in re.findall(
            r"resolution:\s*(restauration|evolution|creation)\s+([\w./-]+)", r.get("notes") or ""
        ):
            gaps[(res, nom)] = gaps.get((res, nom), 0) + 1
    return gaps


def build_runs_stats(runs: list):
    """Plan vs réel (O-C) : taux de réussite par playbook et par agent, à partir de runs.jsonl.

    Approximation assumée : un run n'enregistre qu'un résultat global (log_run.py, format
    O-A/O-B inchangé), donc chaque agent du plan hérite du résultat et des reprises du run
    entier — pas de granularité par étape.

    `en-cours` (run journalisé dès la composition du plan, avant l'exécution) est compté
    à part : il ne dit encore ni réussite ni échec, donc l'inclure dans `n` fausserait les
    taux à la baisse. Un `en_cours` qui ne se solde jamais est le signal utile — c'est un
    run interrompu ou abandonné, exactement ce que l'ancien schéma « journaliser à la fin »
    perdait en silence.

    `en-attente-validation` et `partiel` suivent EXACTEMENT le même principe (finding
    mesuré 2026-08-31 : `evolution-flotte` = 36 runs = 30 succès + 4 en-attente-validation
    + 2 partiel, 0 échec — avant ce correctif ils gonflaient `n` sans jamais incrémenter
    `succes` ni `echecs`, ramenant le taux à 30/36 = 83 % alors qu'aucun des 36 runs n'a
    échoué). Ni l'un ni l'autre n'est un verdict terminal : R5 interdit de logger `succes`
    tant que l'utilisateur n'a pas validé, et un `partiel` attend encore la suite avant de
    se solder en `succes` ou `echec`. Les exclure de `n` sans les compter à part serait le
    même bug déplacé (des non-soldés qui disparaissent en silence au lieu de fausser le
    taux) — `en_attente_validation` et `partiels` restent donc visibles, comme `en_cours`.
    """
    NON_TERMINAUX = {
        "en-cours": "en_cours",
        "en-attente-validation": "en_attente_validation",
        "partiel": "partiels",
    }
    par_playbook, par_agent = {}, {}

    def cumuler(agg: dict, cle: str, resultat, reprises: int) -> None:
        e = agg.setdefault(cle, {
            "n": 0, "succes": 0, "echecs": 0, "reprises": 0,
            "en_cours": 0, "en_attente_validation": 0, "partiels": 0,
            "runs_fortes_reprises": 0,
        })
        # Compté AVANT le early-return non-terminal : une reprise pèse sur la
        # prudence qu'elle finisse en succès, partiel ou en attente (cf. commentaire
        # ECHEC_PRUDENCE_MIN plus haut — echec, lui, ne se déclenche jamais).
        if reprises >= REPRISE_PRUDENCE_SEUIL:
            e["runs_fortes_reprises"] += 1
        cle_non_terminale = NON_TERMINAUX.get(resultat)
        if cle_non_terminale:
            e[cle_non_terminale] += 1
            return
        e["n"] += 1
        e["reprises"] += reprises
        if resultat == "succes":
            e["succes"] += 1
        elif resultat == "echec":
            e["echecs"] += 1

    for r in runs:
        resultat = r.get("resultat")
        reprises = r.get("reprises") or 0
        playbook = r.get("playbook")
        if playbook:
            cumuler(par_playbook, playbook, resultat, reprises)
        for etape in r.get("plan") or []:
            agent = etape.get("agent")
            if agent:
                cumuler(par_agent, agent, resultat, reprises)
    return par_playbook, par_agent


# Skills dont la portee est le HUB de supervision, jamais le depot ou elles sont
# installees : `audit-technique/SKILL.md` le declare noir sur blanc (« Portee :
# artefact HUB uniquement, aucun miroir local », « s invoque depuis le hub, en ciblant
# un projet de la flotte — jamais depuis le projet cible lui-meme »). Leur `n=0` sur
# une CIBLE n est donc pas un defaut d usage : c est le fonctionnement nominal, et
# aucun usage local ne le corrigera jamais. Les melanger aux skills locales sans usage
# produit un TODO definitivement incorrigible (finding `skills-projet-sans-usage`,
# volet hub-only, diagnostic VSCode du 2026-09-04 — le commit 6de0e92 avait clarifie
# la portee dans le SKILL.md sans que le scan, lui, l apprenne).
#
# Liste STATIQUE et non detection textuelle, contrairement a `non_invocation_skills` :
# la declaration de portee vit en PROSE dans le SKILL.md, pas dans un champ lisible a
# coup sur — un grep sur « hub » y attraperait n importe quelle skill qui parle du hub.
# Une liste courte, nommee et commentee dit mieux la verite qu une heuristique qui
# devine. Elle vaut pour TOUS les projets cibles, le canon etant propage a tous.
HUB_ONLY_SKILLS = ("audit-technique",)


def skills_hub_only(fam: dict) -> list:
    """Les skills hub-only INSTALLEES ici (triees) — vide sur un depot qui n en a pas."""
    return sorted(k for k in (fam or {}) if k in HUB_ONLY_SKILLS)


def _cadence_propre() -> set:
    """Noms dont la fraicheur est DEJA suivie, et affichee, ailleurs sur cette meme
    page — les compter une seconde fois en « sommeil » est un double-compte.

    Deterministe pour la part qui peut l etre : tout hook `.claude/hooks/remind_*.py`
    EST un mecanisme de cadence, et son nom de fichier designe ce qu il rappelle
    (`remind_veille_agentic.py` -> `veille-agentic`). S y ajoute `agent-supervisor`,
    dont la cadence de 14 jours n est pas portee par un hook mais par CE script
    (`DIAGNOSTIC_CADENCE_DAYS`, rendu en toutes lettres section « Diagnostic
    qualitatif » de la meme page) — le seul nom code en dur, et pour cette raison-la.
    """
    noms = {"agent-supervisor"}
    for chemin in glob.glob(os.path.join(REPO, ".claude", "hooks", "remind_*.py")):
        base = os.path.splitext(os.path.basename(chemin))[0]
        noms.add(base[len("remind_"):].replace("_", "-"))
    return noms


def hors_perimetre_sommeil(noms) -> set:
    """Parmi `noms`, ceux qu un TODO « en sommeil » ne peut pas rendre actionnables.

    Finding `en-sommeil-indicateur` (diagnostic VSCode du 2026-09-04). La liste
    publiee ce jour-la tenait en deux entrees, et aucune des deux n etait elaguable :

    - `Explore` — un type de sous-agent NATIF du harnais. Aucun `.claude/agents/
      Explore.md` n existe, il n y a donc rien a reveiller ni a desinstaller. Idem
      `Plan`, `general-purpose`, `(defaut)` ;
    - `agent-supervisor` — dont la cadence de 14 jours est deja suivie, et affichee,
      plus haut sur la MEME page. Il en est d ailleurs sorti tout seul entre deux
      scans, sans aucune decision humaine : un signal qui se corrige en silence n en
      etait pas un.

    Un TODO dont aucune entree n appelle de geste n est pas une liste imprecise,
    c est une liste inactionnable — et elle use l attention qu on devra a la
    prochaine, vraie. On mesure toujours le sommeil pareil (`dormants` reste la
    definition unique) ; on ne PROPOSE que ce sur quoi un geste existe.

    Est « natif » un nom qui n est ni une skill installee (`installed_skills`) ni un
    sous-agent declare par un `.claude/agents/<nom>.md` du depot.
    """
    fam = installed_skills()
    cadences = _cadence_propre()
    exclus = set()
    for nom in noms:
        if nom in cadences:
            exclus.add(nom)
            continue
        if nom in fam:
            continue
        if os.path.isfile(os.path.join(REPO, ".claude", "agents", f"{nom}.md")):
            continue
        exclus.add(nom)     # sous-agent natif : rien a reveiller, rien a desinstaller
    return exclus


def mesure_non_fiable(state: dict) -> bool:
    """La mesure d usage couvre-t-elle encore ce qu elle pretend couvrir ?

    Une seule definition, lue par routing-hints.json ET par l avertissement du bloc
    TODO : deux reponses a la meme question, sur la meme page, ne peuvent pas etre
    toutes les deux la mesure (meme raison que `dormants`)."""
    mesure = (state or {}).get("mesure_incomplete") or {}
    return bool(mesure.get("transcripts_absents") or mesure.get("journal_usage_muet"))


def avertissement_mesure(state: dict) -> str:
    """L avertissement a afficher EN TETE du bloc TODO quand la mesure n est pas
    fiable, ou "" quand elle l est.

    Le drapeau existait depuis le 2026-09-02 — mais seulement dans routing-hints.json
    et dans une note d arbitrage. Il manquait la ou l humain arbitre : sur le bloc
    qu il lit pour decider de desinstaller. Un `n=0` sur une base fondue ne veut plus
    dire « jamais invoquee », il veut dire « on ne regarde plus » ; rien ne
    distinguait les deux a l endroit ou ca compte."""
    mesure = (state or {}).get("mesure_incomplete") or {}
    absents = mesure.get("transcripts_absents") or 0
    total = mesure.get("total_fichiers") or 0
    causes = []
    if absents:
        causes.append(f"{absents} transcript(s) sur {total} absent(s) du disque")
    if mesure.get("journal_usage_muet"):
        causes.append("journal d usage muet (offset jamais pose)")
    if not causes:
        return ""
    return (
        "⚠️ **Mesure incomplète** — " + " ; ".join(causes) + ". Un `n=0` ne veut "
        "plus dire « jamais invoquée » mais « on ne le voit plus » : les listes "
        "ci-dessous sous-estiment l'usage réel. Ne rien désinstaller sur cette base."
    )


def veille_sans_production(state: dict) -> str:
    """L avertissement « veille lancee, rien produit », ou "" quand tout va bien.

    Finding `veille-sans-depot` (diagnostic VSCode du 2026-09-10), volet (b). Le
    sous-agent `veille-agentic` avait tourne le 2026-09-04 et n avait rien depose :
    `.claude/veille/` etait absent du disque quatre heures plus tard, et le volet
    « etat de l art agentic » du diagnostic sortait donc structurellement vide. Rien
    ne distinguait ce cas d une veille JAMAIS LANCEE — deux situations qui n appellent
    pas le meme geste (relancer avec un brief corrige vs simplement lancer).

    Deterministe, 0 token : on compare la derniere invocation mesuree (tous canaux,
    `derniers_usages`) a ce que le fichier declare (`derniere_veille`). Une invocation
    POSTERIEURE au dernier artefact veut dire que le tour s est perdu.

    Fail-open a chaque etape (fichier absent mais jamais lancee -> rien a signaler ;
    JSON illisible -> pas d alarme inventee) : un scan ne tombe jamais sur cette
    lecture, et n accuse jamais sur un doute."""
    dernier_lancement = (derniers_usages(state) or {}).get("veille-agentic", "")
    if not dernier_lancement:
        return ""          # jamais lancee ici : ce n est pas le cas que ce signal vise
    chemin = os.path.join(REPO, ".claude", "veille", "veille.json")
    try:
        with open(chemin, encoding="utf-8") as fh:
            derniere_veille = (json.load(fh) or {}).get("derniere_veille", "") or ""
    except FileNotFoundError:
        return (
            f"⚠️ **Veille lancée le {dernier_lancement[:10]}, aucun artefact produit** — "
            "`.claude/veille/veille.json` est absent de ce dépôt : le volet « état de "
            "l'art agentic » est structurellement vide, et une veille qui échoue reste "
            "indistinguable d'une veille jamais lancée. Relancer avec un brief imposant "
            "le chemin ABSOLU d'écriture."
        )
    except (OSError, ValueError):
        return ""          # illisible : on ne fabrique pas une alarme sur un doute
    if derniere_veille and derniere_veille >= dernier_lancement:
        return ""
    return (
        f"⚠️ **Veille lancée le {dernier_lancement[:10]}, artefact plus ancien** — "
        f"`.claude/veille/veille.json` date du {(derniere_veille or '?')[:10]} : le "
        "dernier tour n'a rien déposé. Vérifier où sa sortie a atterri avant de relancer."
    )


def dormants(state):
    """Les noms dont l usage LE PLUS RECENT, tous canaux confondus, depasse le seuil.

    Definition UNIQUE du sommeil (finding scan_transcripts.py:807, 2026-09-01). Le
    script la calculait a deux endroits sur deux ensembles differents : `{**skills,
    **subagents}` pour routing-hints.json, `skills` seul pour le TODO du wiki. Mesure
    du desaccord : 6 noms d un cote, 7 de l autre, et les deux faux autrement.

    Le mecanisme : `{**skills, **subagents}` ECRASE l entree skill par celle du
    sous-agent de meme nom. `agent-supervisor` et `veille-agentic` vivent dans les
    deux canaux et leur usage recent est cote sous-agent, si bien que le TODO du wiki
    proposait d eteindre `agent-supervisor` LE JOUR OU il avait tourne, et omettait
    `bmad-recherche`, sous-agent pur, donc le seul reellement dormant.

    On ne choisit pas entre les deux ensembles — l ecrasement tombait juste ICI par
    hasard, le sous-agent etant le plus recent. On prend le MAXIMUM des deux dates :
    une entite qui a servi dans un canal quelconque n est pas endormie.
    """
    derniers = derniers_usages(state)
    endormis = sorted(
        nom for nom, last in derniers.items()
        if (lambda d: d is not None and d > DORMANT_DAYS)(days_since(last))
    )
    # Le filtre vit ICI, dans la definition unique, et pas chez l un des deux
    # consommateurs : routing-hints.json et le TODO du wiki doivent continuer de lire
    # exactement la meme liste (c est le finding scan_transcripts.py:807 ci-dessus).
    hors = hors_perimetre_sommeil(endormis)
    return [nom for nom in endormis if nom not in hors]


# Colonnes fixes de `/skill-doctor` (skill_doctor_snapshot.py) : 2 espaces de marge,
# puis skill/source/context/tokens en un seul mot chacun, "N×" pour les usages (jamais
# d'espace entre le nombre et le ×), et "last used" en fin de ligne qui peut porter
# plusieurs mots ("92 days", "today") — d'où le groupe final non-greedy plutôt qu'un
# split() par espaces, qui casserait sur "last used".
_RE_SKILL_DOCTOR_LIGNE = re.compile(
    r"^\s{2}(?P<skill>\S+)\s+\S+\s+\S+\s+\S+\s+(?P<uses>\d+)×\s+.+?\s*$"
)
_RE_SKILL_DOCTOR_DATE = re.compile(r"^# Instantane /skill-doctor -- (?P<date>\S+)", re.MULTILINE)


def _lire_skill_doctor(chemin: str = None) -> tuple[dict, str]:
    """(usages, date_rapport) depuis le dernier instantané `/skill-doctor`
    (`skill_doctor_snapshot.py`, capturé à la demande, jamais par ce scan).

    Fail-open total — fichier absent, illisible, ou d'un format inattendu rendent
    ({}, "") : ce croisement AMÉLIORE la mesure de `jamais_utilises`, il n'a jamais
    à faire échouer le scan qui tourne à chaque session (finding
    verification-manquante:jamais-utilises-vs-skill-doctor, 2026-09-15 — 7 des 21
    noms de `jamais_utilises` étaient contredits par cet instantané, jamais croisés).
    """
    chemin = chemin or SKILL_DOCTOR_PATH
    try:
        with open(chemin, encoding="utf-8") as fh:
            texte = fh.read()
    except OSError:
        return {}, ""
    m_date = _RE_SKILL_DOCTOR_DATE.search(texte)
    date_rapport = m_date.group("date") if m_date else ""
    usages: dict = {}
    for ligne in texte.splitlines():
        m = _RE_SKILL_DOCTOR_LIGNE.match(ligne)
        if not m:
            continue
        try:
            usages[m.group("skill")] = int(m.group("uses"))
        except ValueError:
            continue
    return usages, date_rapport


def _skill_doctor_perime(date_rapport: str, max_jours: int = None) -> bool:
    """True si un instantané /skill-doctor existe mais est trop vieux pour contredire
    quoi que ce soit (finding skill_doctor_snapshot:instantane-perime-17j, arbitré le
    2026-09-26 : toutes les entrées `contredit_par_skill_doctor` reposaient sur un
    rapport de 17 jours).

    Date absente (pas d'instantané) -> False : rien à périmer, le croisement est vide.
    Date illisible -> True : un instantané dont la fraîcheur n'est pas établissable ne
    doit pas sortir un nom de `jamais_utilises`.
    """
    if max_jours is None:
        max_jours = SKILL_DOCTOR_MAX_JOURS
    if not date_rapport:
        return False
    try:
        date = dt.datetime.fromisoformat(date_rapport)
    except ValueError:
        return True
    if date.tzinfo is None:
        date = date.astimezone()
    delta = dt.datetime.now().astimezone() - date
    # Date FUTURE (horloge décalée, rapport édité) : sans cette garde, delta négatif
    # rendait False à perpétuité — l'instantané corrompu blanchissait pour toujours
    # (revue code-review-crew du 2026-09-26, constat Boundary/Dana).
    if delta < dt.timedelta(0):
        return True
    return delta >= dt.timedelta(days=max_jours)


def _racines_skills() -> list:
    """Les endroits où Claude Code sert réellement des skills sur ce poste, hors dépôt :
    les skills utilisateur (~/.claude/skills) et celles des plugins. Ne rend que les
    dossiers existants.

    Le dépôt n'est PAS dans cette liste : il est l'inventaire de RÉFÉRENCE (cf.
    `_noms_fantomes`), les autres racines ne servent qu'à innocenter un nom."""
    racines = [os.path.expanduser(os.path.join("~", ".claude", "skills"))]
    racines += sorted(glob.glob(os.path.expanduser(
        os.path.join("~", ".claude", "plugins", "*", "skills"))))
    return [r for r in racines if os.path.isdir(r)]


def _noms_fantomes(noms) -> set:
    """Parmi `noms`, ceux sans `<racine>/<nom>/SKILL.md` dans AUCUNE des racines de
    skills du poste (finding scan_transcripts.py:jamais_utilises-faux-nom-synced,
    arbitré le 2026-09-26, durci par la revue code-review-crew du même jour : la
    première version ne regardait que le dépôt et publiait `skill-creator` — skill
    utilisateur vivante sous ~/.claude/skills — comme reliquat à nettoyer).

    Le dépôt est l'inventaire de RÉFÉRENCE : c'est lui qui peut condamner un nom.
    Les autres racines (`_racines_skills`) ne font qu'innocenter — sans cette
    asymétrie, un dépôt dont le dossier de skills manque (canon chargé hors de son
    arbre, déploiement partiel) faisait déclarer fantôme TOUT le catalogue, parce
    que le ~/.claude du poste fournissait à lui seul un inventaire non vide
    (mesuré le 2026-09-27 : `bmad-prd` sorti de `jamais_utilises` à tort,
    test_les_hints_jamais_utilises_excluent_le_journal rouge).

    Trois fail-open, tous justifiés par « on ne juge pas une absence sans
    inventaire » :
    - pas de dossier de skills dans le dépôt -> rien n'est fantôme ;
    - dossier présent mais AUCUNE skill dedans (déploiement partiel) -> rien n'est
      fantôme ; la première version déclarait alors TOUT fantôme et vidait
      `jamais_utilises` en silence ;
    - nom à espace de noms (`plugin:skill`) -> jamais fantôme : il n'est pas un
      dossier plat, on ne peut pas le vérifier par ce chemin.
    """
    reference = os.path.join(REPO, ".claude", "skills")
    if not os.path.isdir(reference):
        return set()
    if not glob.glob(os.path.join(reference, "*", "SKILL.md")):
        return set()
    racines = [reference] + _racines_skills()
    return {n for n in noms
            if ":" not in n
            and not any(os.path.isfile(os.path.join(r, n, "SKILL.md"))
                        for r in racines)}


def build_routing_hints(state: dict, fam: dict, par_playbook: dict, par_agent: dict, diagnostic,
                        runs: list = None, arbitrages: list = None) -> dict:
    """Sens superviseur → orchestrateur (conception §6) : ce que le scan mesure, appliqué
    par la skill agent-orchestrator lors de la composition d'un plan."""
    skills = state.get("skills", {})
    # `eprouves` et `verifications_oubliees` lisaient encore le SEUL canal transcripts,
    # six lignes sous le commentaire qui annonce les quatre canaux (revue 2026-09-02) :
    # routing-hints.json, que l'orchestrateur consomme, continuait donc d'affirmer
    # exactement ce que cet increment pretend corriger.
    vu_skills = usage_affiche(state, "skills")
    combined = {**vu_skills, **usage_affiche(state, "subagents")}
    eprouves = sorted(k for k, e in combined.items() if e["n"] >= PROVEN_MIN)
    libref = non_invocation_skills(fam)
    # « Jamais utilise » se juge sur les QUATRE canaux (derniers_usages), pas sur le
    # seul canal transcripts : 126 transcripts sur 137 avaient disparu le 2026-09-02
    # et 5 skills invoquees le jour meme etaient publiees « jamais utilisees ».
    vus = set(skills) | set(derniers_usages(state))
    hub_only = skills_hub_only(fam)
    jamais_bruts = [k for k, v in fam.items()
                    if k not in vus and k not in libref and k not in hub_only]
    # Un nom sans SKILL.md sur le disque est un fantôme (reliquat d'état, ex. « synced »)
    # : il sort de jamais_utilises et se publie à part (arbitré le 2026-09-26).
    fantomes = _noms_fantomes(jamais_bruts)
    jamais_bruts = [k for k in jamais_bruts if k not in fantomes]
    # Second instrument, independant des transcripts : un nom que /skill-doctor
    # rapporte reellement invoque (uses > 0) sort de jamais_utilises et va dans
    # contredit_par_skill_doctor plutot que d etre presente comme mort a l arbitrage
    # (finding verification-manquante:jamais-utilises-vs-skill-doctor, 2026-09-15).
    usages_sd, date_sd = _lire_skill_doctor()
    # Un instantané périmé (> SKILL_DOCTOR_MAX_JOURS) ne contredit rien : mieux vaut
    # re-présenter un nom à l'arbitrage que le blanchir sur une mesure glissée
    # (arbitré le 2026-09-26).
    skill_doctor_perime = _skill_doctor_perime(date_sd)
    if skill_doctor_perime:
        usages_sd = {}
    contredit_par_skill_doctor = [
        {"skill": k, "uses_skill_doctor": usages_sd[k], "rapport_date": date_sd}
        for k in sorted(jamais_bruts) if usages_sd.get(k, 0) > 0
    ]
    _contredits = {c["skill"] for c in contredit_par_skill_doctor}
    jamais = sorted(k for k in jamais_bruts if k not in _contredits)
    bibliotheque = sorted(k for k in libref if k not in vus)
    en_sommeil = dormants(state)
    verifs_oubliees = []
    if "revue-increment" in fam and "revue-increment" not in vus:
        verifs_oubliees.append(
            "revue-increment jamais invoquee malgre le rappel SessionStart -> l'inserer d'office en etape terminale des plans de dev"
        )
    prudence = []
    for f in _findings(diagnostic):
        if (
            f.get("categorie") in ("ko-repete", "inefficacite")
            and f.get("cible")
            # Arbitrage couvrant la catégorie -> ne pèse plus sur le routage. Un
            # `re_challenge` NE rouvre PAS le routage (respecter_re_challenge=False) :
            # il rouvre le débat devant l'humain, qui tranche — le superviseur propose,
            # il n'applique pas.
            and not finding_arbitre(f, arbitrages, respecter_re_challenge=False)
        ):
            prudence.append({"cible": f["cible"], "raison": _libelle_finding(f)})
    # Incrément C — prudence déterministe : échecs répétés dans le journal d'orchestration,
    # sans attendre le diagnostic LLM (dédupliqué sur les cibles déjà signalées).
    deja = {p["cible"] for p in prudence}
    for agent, e in sorted(par_agent.items()):
        if agent in deja:
            continue
        if e["echecs"] >= ECHEC_PRUDENCE_MIN and e["echecs"] > e["succes"]:
            prudence.append({
                "cible": agent,
                "raison": f"échecs répétés en orchestration ({e['echecs']}/{e['n']} runs)",
            })
        elif e.get("runs_fortes_reprises", 0) >= REPRISES_PRUDENCE_MIN_RUNS:
            prudence.append({
                "cible": agent,
                "raison": (
                    f"reprises répétées en orchestration "
                    f"({e['runs_fortes_reprises']} run(s) à >= {REPRISE_PRUDENCE_SEUIL} "
                    f"reprise(s) sur {e['n']})"
                ),
            })
    gaps = catalogue_gaps(runs or [])
    # Fiabilité de la mesure elle-même (finding state-transcripts-absents,
    # 2026-09-02) : `mesure_incomplete` est calculé et persisté dans state.json par
    # main() (cf. mesure_incomplete()) — on le RELIT ici plutôt que de le
    # recalculer, pour ne jamais publier une lecture différente de celle écrite sur
    # disque. Absent (état pas encore réécrit, ou appel direct de cette fonction
    # dans un test) -> 0 transcript absent, jamais un KeyError.
    mesure = state.get("mesure_incomplete") or {
        "transcripts_absents": 0, "total_fichiers": 0, "dernier_evenement": "",
    }
    return {
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "eprouves": eprouves,
        "jamais_utilises": jamais,
        # Noms sortis de jamais_utilises parce qu'un instantané /skill-doctor
        # (indépendant des transcripts) les rapporte réellement invoqués — vide si
        # skill_doctor_last.txt est absent ou périmé au point de ne rien contredire.
        "contredit_par_skill_doctor": contredit_par_skill_doctor,
        # True quand un instantané /skill-doctor existe mais dépasse
        # SKILL_DOCTOR_MAX_JOURS : ses usages sont alors ignorés — le rafraîchir
        # (skill_doctor_snapshot.py) avant de trier jamais_utilises.
        "mesure_skill_doctor_perimee": skill_doctor_perime,
        # Noms retirés de jamais_utilises faute de SKILL.md sur le disque : reliquats
        # d'état à nettoyer, pas des skills mortes à arbitrer.
        "absents_du_disque": sorted(fantomes),
        # Skills-bibliothèque/référence : usage réel non capté par le compteur
        # d'invocations (constat #2) — sortis de jamais_utilises pour que
        # l'orchestrateur ne les traite pas comme morts.
        "bibliotheque_reference": bibliotheque,
        # Skills hub-only (cf. HUB_ONLY_SKILLS) : leur `n=0` sur une cible est le
        # fonctionnement nominal, pas un defaut d usage — sorties de jamais_utilises
        # pour que l orchestrateur ne les propose ni a l elagage ni au routage local.
        "hub_only": hub_only,
        "en_sommeil": en_sommeil,
        "verifications_oubliees": verifs_oubliees,
        "playbooks": par_playbook,
        "agents": par_agent,
        "prudence": prudence,
        "trous_catalogue": [
            {"resolution": res, "nom": nom, "n": n}
            for (res, nom), n in sorted(gaps.items(), key=lambda kv: -kv[1])
        ],
        "diagnostic_a_jour": diagnostic_a_jour(diagnostic, runs),
        # Boucle propose→arbitre : décisions humaines à respecter lors du routage
        # (un jamais-utilisé arbitré "conserver" se propose via son playbook, sans re-nagguer).
        "arbitrages": load_arbitrages(),
        # Base de mesure fondue (transcripts purgés depuis le dernier passage) :
        # les compteurs ci-dessus restent publiés TELS QUELS (aucune suppression),
        # mais ce drapeau dit qu'ils ne couvrent plus tout l'historique attendu —
        # à l'orchestrateur/au lecteur de ne pas les lire comme une mesure fraîche.
        "mesure_incomplete": mesure,
        "mesure_non_fiable": mesure_non_fiable(state),
    }


def build_todos(skills: dict, fam: dict, gaps: dict = None,
                arbitrages: list = None, state: dict = None) -> list:
    # Les TODO déterministes de cette fonction sont TOUS de catégorie `agent-mort`
    # (skill installée sans usage). Ne retenir donc que les arbitrages qui ferment
    # cette catégorie-là (2026-07-28) : jusqu'ici la cible seule suffisait, si bien
    # qu'une décision portant sur la VÉRIFICATION (ex. les deux arbitrages
    # `run-dev-server`) aurait éteint un futur constat d'usage sur la même skill —
    # précisément la friction que le champ `categories` a supprimée côté étage 2,
    # laissée intacte de ce côté-ci.
    arbitres = {
        a["cible"] for a in arbitrages or []
        if not a.get("categories") or "agent-mort" in a["categories"]
    }
    todos = []
    # Incrément C : un même agent demandé/recréé plusieurs fois ad hoc = trou récurrent.
    for (res, nom), n in sorted((gaps or {}).items(), key=lambda kv: -kv[1]):
        if n >= 2:
            todos.append(
                f"**Trou récurrent du catalogue** : `{nom}` a nécessité une résolution ad hoc "
                f"×{n} ({res}) — l'ancrer pour de bon (création/restauration à arbitrer)."
            )
    # `skills` (canal transcripts) est complete par les autres canaux quand `state`
    # est fourni : une skill vue par le journal du hook n est pas « jamais invoquee »
    # (cf. scan_journal_usage — le TODO « 43/46 » du 2026-09-02 comptait comme mortes
    # 5 skills invoquees le jour meme).
    vus = set(skills) | (set(derniers_usages(state)) if state is not None else set())
    non_fiable = mesure_non_fiable(state)
    bmad = [k for k, v in fam.items() if v == "BMAD"]
    bmad_unused = [k for k in bmad if k not in vus]
    if "famille:BMAD" in arbitres:
        bmad_unused = []  # tri déjà arbitré par l'humain — ne pas re-nagguer
    # (a) Le SEUL élagage qui ne dépende pas de notre mesure d'usage : les shims que
    # BMAD lui-même déclare dépréciés, chacun avec son remplaçant nommé. Ceux-là se
    # proposent toujours, mesure fiable ou non — la déprécation est une déclaration
    # de l'éditeur, pas une déduction de notre compteur.
    deprecies = {k: v for k, v in skills_depreciees(fam).items()
                 if k in bmad and k not in arbitres}
    if deprecies:
        listing = ", ".join(f"`{k}`" + (f" → `{v}`" if v else "")
                            for k, v in sorted(deprecies.items()))
        todos.append(
            f"**Désinstaller les shims BMAD dépréciés** ({len(deprecies)}) : {listing} — "
            "dépréciés par BMAD dans leur propre `description`, chacun avec son "
            "remplaçant ; le seul élagage qui ne repose pas sur notre mesure d'usage."
        )
    # (b) Tout le reste : un `n=0` sur une base de mesure fondue ne prouve aucune
    # inutilité. Le TODO « N/M jamais invoqués » a failli faire désinstaller, le
    # 2026-09-01 sur VSCode, `bmad-forge-idea` et `bmad-party-mode` — les deux skills
    # qui ont porté toute la refonte du produit COMOP jusqu'à un `forged-idea.md`
    # HARDENED trois jours plus tard. Tant que `mesure_non_fiable` est vrai, on ne
    # propose donc RIEN sur ce lot : une skill inutilisée aujourd'hui n'a peut-être
    # pas encore eu son moment, et l'avertissement en tête du bloc dit pourquoi la
    # liste ne s'y trouve pas (finding `bmad-catalogue-elagage`, 2026-09-04).
    reste_unused = [k for k in bmad_unused if k not in deprecies]
    if bmad and reste_unused and not non_fiable:
        if len(reste_unused) == len(bmad):
            todos.append(
                f"**Trier les skills BMAD** : {len(bmad)} installés, 0 invocation à ce jour — "
                "décider lesquels garder, customiser ou désinstaller."
            )
        else:
            todos.append(
                f"**Élaguer les skills BMAD** : {len(reste_unused)}/{len(bmad)} jamais invoqués — "
                "confirmer l'utilité des non-utilisés."
            )
    # Les skills-bibliothèque/référence (constat #2) ne sont pas des « sans usage » :
    # leur valeur passe par scripts/sous-agent, invisible au compteur d'invocations.
    libref = non_invocation_skills(fam)
    # Les skills hub-only ne sont pas des « sans usage » locales : cf. HUB_ONLY_SKILLS.
    # Elles s'affichent a part, section « Skills hub-only » de la page — pas en TODO,
    # puisqu'il n'existe aucun geste local a proposer.
    hub_only = skills_hub_only(fam)
    proj_unused = sorted(
        k for k, v in fam.items()
        if v == "projet" and k not in vus and k not in arbitres and k not in libref
        and k not in hub_only
    )
    if "revue-increment" in proj_unused:
        proj_unused.remove("revue-increment")
        todos.append(
            "**`revue-increment` jamais invoquée** malgré le rappel SessionStart à chaque session — "
            "revoir son déclencheur (l'ancrer au flux de commit ?) ou la simplifier."
        )
    if proj_unused:
        todos.append(
            "**Skills projet sans usage** : "
            + ", ".join(f"`{s}`" for s in proj_unused)
            + " — vérifier pertinence et déclencheurs."
        )
    # Le sommeil ne consulte PAS `arbitres`, et c'est délibéré (2026-07-28) : « cette
    # skill n'est pas morte » (agent-mort, décidé un jour donné) ne dit rien de « elle
    # dort depuis deux mois » — signal différent, sur une skill qui a bel et bien servi.
    # Filtrer ici éteindrait définitivement le sommeil de bmad-code-review,
    # restitution-deck-design et slide-text-polish, toutes arbitrées et actives.
    # `state` et non `skills` : le sommeil se lit sur les DEUX canaux. Un repli
    # silencieux sur `{"skills": skills}` recreerait le defaut corrige le
    # 2026-09-01 — un TODO qui propose d eteindre ce qui vient de servir.
    dormant = dormants(state if state is not None else {"skills": skills})
    if dormant:
        todos.append(
            f"**Skills en sommeil (>{DORMANT_DAYS} j sans usage)** : "
            + ", ".join(f"`{s}`" for s in dormant)
            + "."
        )
    return todos[:5]


def _fmt_date(ts: str) -> str:
    return ts[:10] if ts else "?"


def _usage_table(agg: dict, fam: dict = None) -> list:
    lines = []
    if fam is not None:
        lines.append("| Skill | Famille | Invocations | Première | Dernière |")
        lines.append("| --- | --- | --- | --- | --- |")
    else:
        lines.append("| Sous-agent | Lancements | Premier | Dernier |")
        lines.append("| --- | --- | --- | --- |")
    for name, e in sorted(agg.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        if fam is not None:
            family = fam.get(name, "(builtin/session)")
            lines.append(
                f"| `{name}` | {family} | {e['n']} | {_fmt_date(e.get('first', ''))} | {_fmt_date(e.get('last', ''))} |"
            )
        else:
            lines.append(
                f"| `{name}` | {e['n']} | {_fmt_date(e.get('first', ''))} | {_fmt_date(e.get('last', ''))} |"
            )
    if len(lines) == 2:
        lines.append("| _(aucun)_ |" + " |" * (3 if fam is not None else 2))
    return lines


def openhub_stats():
    """Couverture OpenHub (incrément C, VSCode2) : lit la table agent_results de l'app
    (SQLite, lecture seule) — résultats réels vs fallback simulé (opencode absent). None
    si base ou table absente : la couverture reste optionnelle, jamais bloquante — les
    projets sans app OpenHub (la majorité de la flotte) ne voient simplement rien."""
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{OPENHUB_DB}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT agent_label, runtime_available, created_at FROM agent_results"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    par_agent = {}
    reels = 0
    last = ""
    for label, runtime, created in rows:
        par_agent[label] = par_agent.get(label, 0) + 1
        reels += 1 if runtime else 0
        last = max(last, created or "")
    return {"n": len(rows), "reels": reels, "simules": len(rows) - reels,
            "last": last, "par_agent": par_agent}


def build_page(state: dict, fam: dict, todos: list, diag_todos: list = None, diag_a_jour: bool = False,
               openhub: dict = None, arbitrages: list = None, diagnostic_ran: bool = False,
               masques: list = None) -> str:
    skills = usage_affiche(state, "skills")
    subagents = usage_affiche(state, "subagents")
    nb_files = len(state.get("files", {}))
    total_skill = sum(e["n"] for e in skills.values())
    total_sub = sum(e["n"] for e in subagents.values())
    L = [
        "---",
        f"updated: {dt.date.today().isoformat()}",
        "generated-by: .claude/supervision/scan_transcripts.py (superviseur d'agents, étage 1)",
        "---",
        "",
        "# Supervision des agents — tableau de bord d'usage",
        "",
        "> ⚠️ **Page générée automatiquement** (hook SessionStart → `.claude/supervision/scan_transcripts.py`).",
        "> **Ne pas éditer à la main** — toute modification serait écrasée au prochain scan.",
        "",
        f"Dernier scan : {state.get('last_scan', '?')} · **{nb_files} sessions** (transcripts) · "
        f"**{total_skill}** invocations de skills · **{total_sub}** lancements de sous-agents.",
        "",
        "## Skills — usage réel",
        "",
    ]
    L += _usage_table(skills, fam)
    L += ["", "## Sous-agents", ""]
    L += _usage_table(subagents)
    libref = non_invocation_skills(fam)
    # Les 4 canaux, pas le seul canal transcripts (2026-09-02) : 126 transcripts sur
    # 137 avaient disparu du disque, et la page listait « jamais utilisées » cinq
    # skills que le journal du hook avait vu tourner le jour même.
    vus = set(skills) | set(derniers_usages(state))
    L += ["", "## Jamais utilisés", ""]
    unused_by_family = {}
    libref_unused = []
    hub_only_unused = []
    for name, family in fam.items():
        if name in vus:
            continue
        if name in HUB_ONLY_SKILLS:
            hub_only_unused.append(name)
        elif name in libref:
            libref_unused.append(name)
        else:
            unused_by_family.setdefault(family, []).append(name)
    if not unused_by_family:
        L.append(
            "_(aucun — hors skills bibliothèque/référence ci-dessous)_"
            if libref_unused
            else "_(tous les skills installés ont déjà été invoqués)_"
        )
    for family in ("projet", "BMAD", "global"):
        names = sorted(unused_by_family.get(family, []))
        if not names:
            continue
        total_family = sum(1 for v in fam.values() if v == family)
        L.append(f"**{family}** — {len(names)}/{total_family} jamais invoqués :")
        L.append("")
        if len(names) > 8:
            L.append("<details><summary>Voir la liste</summary>")
            L.append("")
            L.append(", ".join(f"`{n}`" for n in names))
            L.append("")
            L.append("</details>")
        else:
            L.append(", ".join(f"`{n}`" for n in names))
        L.append("")
    if hub_only_unused:
        L += [
            "## Skills hub-only", "",
            "_S'invoquent DEPUIS le hub de supervision, en ciblant ce projet — jamais "
            "depuis ce dépôt (leur `SKILL.md` le déclare). Leur `n=0` ici est le "
            "fonctionnement nominal, pas un défaut d'usage : aucun usage local ne le "
            "corrigera, il n'y a donc rien à en conclure ni rien à désinstaller._", "",
            ", ".join(f"`{n}`" for n in sorted(hub_only_unused)), "",
        ]
    if libref_unused:
        L += [
            "## Skills bibliothèque / référence", "",
            "_Consommés en lisant/exécutant leurs `scripts/`, ou via un sous-agent qui les "
            "suit (ex. `ppt-designer`, qui n'a pas l'outil Skill) — le compteur d'invocations "
            "ne peut structurellement pas les voir. `n=0` n'y vaut donc PAS « mort » : ne pas "
            "désinstaller sur ce seul signal (constat superviseur #2)._", "",
            ", ".join(f"`{n}`" for n in sorted(libref_unused)), "",
        ]
    if openhub and openhub["n"]:
        L += ["## Agents OpenHub (app)", ""]
        L.append(
            f"**{openhub['n']}** résultat(s) en base (`agent_results`) — {openhub['reels']} réel(s), "
            f"{openhub['simules']} simulé(s) (fallback sans `opencode`) · dernier : {_fmt_date(openhub['last'])}."
        )
        L.append("")
        L.append(", ".join(f"`{k}` ×{v}" for k, v in sorted(openhub["par_agent"].items())))
        L.append("")
    L += ["## TODO agents (constats automatiques)", ""]
    avert = avertissement_mesure(state)
    if avert:
        L += [avert, ""]
    avert_veille = veille_sans_production(state)
    if avert_veille:
        L += [avert_veille, ""]
    if todos:
        L += [f"{i}. {t}" for i, t in enumerate(todos, 1)]
    else:
        L.append("_(aucun constat — rien à signaler sur les données actuelles)_")
    if arbitrages:
        L += [
            "",
            "## Arbitrages enregistrés",
            "",
            "_Constats clos par décision humaine (`.claude/supervision/arbitrages.json`) — "
            "l'usage réel reste mesuré ci-dessus._",
            "",
        ]
        L += [f"- **`{a['cible']}`** ({a.get('date', '?')}) : {a['decision']}" for a in arbitrages]
    L += ["", "## Diagnostic qualitatif (étage 2 — `agent-supervisor`)", ""]
    if diag_todos:
        statut = "à jour" if diag_a_jour else f"⚠️ à relancer (> {DIAGNOSTIC_CADENCE_DAYS} j)"
        L.append(f"_Diagnostic {statut}._")
        L.append("")
        L += [f"{i}. {t}" for i, t in enumerate(diag_todos, 1)]
    elif diagnostic_ran:
        # Diagnostic déjà lancé mais tous ses constats sont arbitrés (cf. Arbitrages
        # enregistrés ci-dessus) — distinct de « jamais lancé », sinon le rappel
        # SessionStart induirait en erreur (on ne relance pas ce qui n'a rien à signaler).
        statut = "à jour" if diag_a_jour else f"⚠️ à relancer (> {DIAGNOSTIC_CADENCE_DAYS} j)"
        L.append(f"_Diagnostic {statut} — rien à signaler, tous les constats précédents ont été arbitrés._")
    else:
        L.append(
            "_Jamais lancé — invoquer la skill `agent-supervisor` (intégrée à `revue-increment`) "
            "pour un diagnostic qualitatif (KO répétés, efficacité, interactions entre agents)._"
        )
    if masques:
        # Filtrage rendu auditable (2026-07-28) : sans cette ligne, un constat écarté par
        # un arbitrage disparaissait sans laisser de trace — l'humain qui a arbitré ne
        # pouvait pas savoir que sa décision continuait de fermer des constats NEUFS.
        L += [
            "",
            f"_{len(masques)} constat(s) de ce diagnostic écarté(s) par un arbitrage "
            "— pour en rouvrir un, demander au superviseur un `re_challenge` avec des "
            "données nouvelles :_",
            "",
        ]
        L += [f"- ~~{m['titre']}~~ (`{m['cible']}`)" for m in masques]
    # Le ratio de qualification — la mesure que la décision n°2 de la conception
    # attendait depuis juillet. Affichée ICI et non dans routing-hints.json : c'est
    # une donnée d'arbitrage humain, pas de routage.
    ratio = ratio_qualification(load_jsonl(RUNS_PATH))
    L += ["", "## Seuil de qualification — la mesure", ""]
    if not ratio:
        L.append(
            "_Journal de prompts vide : `orchestrator_gate.py` vient d'être outillé "
            "(finding `VScode5:seuil-qualification-non-mesurable`). Le ratio "
            "apparaîtra dès les premières demandes vues._"
        )
    else:
        part = "?" if ratio["part"] is None else f"{ratio['part'] * 100:.0f} %"
        L.append(
            f"Depuis le {_fmt_date(ratio['depuis'])} : **{ratio['prompts']}** demande(s) "
            f"vue(s) hors commande slash (+ {ratio['slash']} slash), **{ratio['runs']}** "
            f"run(s) orchestré(s) journalisé(s) sur la même fenêtre — soit **{part}** "
            "des demandes orchestrées."
        )
        if not ratio.get("part_fiable", True):
            # Chasse aux cas limites, 2026-09-02 : plus de runs que de demandes hors
            # slash (des runs proviennent de commandes slash, hors dénominateur par
            # construction) rend ce pourcentage > 100 % — le dire plutôt que publier
            # un chiffre absurde sans avertissement.
            L.append(
                "_⚠️ Ratio non fiable ici : plus de runs journalisés que de demandes "
                "hors slash sur la fenêtre — une partie des runs provient probablement "
                "de commandes slash (déjà tranchées « orchestrer », hors dénominateur). "
                "Se lira mieux avec plus d'historique._"
            )
        L.append(
            "_Ce chiffre ne dit pas ce qui AURAIT dû être orchestré : le hook compte, "
            "il ne juge pas. Il donne le dénominateur qui manquait pour arbitrer le "
            "seuil sur données plutôt que sur habitude._"
        )
    L += [
        "",
        "---",
        "",
        "_Étage O-C (croisement modèle × tâche × reprises, exploitation de `runs.jsonl`) : "
        "voir `.claude/orchestration/routing-hints.json`, régénéré à chaque session._",
        "",
    ]
    return "\n".join(L)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_inline(s: str) -> str:
    """Convertit le gras/code markdown des libellés TODO en HTML (le reste est échappé)."""
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def _html_usage_rows(agg: dict, fam: dict = None) -> str:
    rows = []
    for name, e in sorted(agg.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        cells = [f"<td><code>{_esc(name)}</code></td>"]
        if fam is not None:
            cells.append(f"<td>{_esc(fam.get(name, '(builtin/session)'))}</td>")
        cells += [
            f"<td>{e['n']}</td>",
            f"<td>{_esc(_fmt_date(e.get('first', '')))}</td>",
            f"<td>{_esc(_fmt_date(e.get('last', '')))}</td>",
        ]
        rows.append("            <tr>" + "".join(cells) + "</tr>")
    if not rows:
        span = 5 if fam is not None else 4
        rows.append(f'            <tr><td colspan="{span}"><em>(aucun)</em></td></tr>')
    return "\n".join(rows)


def build_html_section(state: dict, fam: dict, todos: list, diag_todos: list = None, diag_a_jour: bool = False,
                       openhub: dict = None, arbitrages: list = None, diagnostic_ran: bool = False,
                       masques: list = None) -> str:
    skills = usage_affiche(state, "skills")
    subagents = usage_affiche(state, "subagents")
    nb_files = len(state.get("files", {}))
    total_skill = sum(e["n"] for e in skills.values())
    total_sub = sum(e["n"] for e in subagents.values())
    today = dt.date.today().isoformat()
    libref = non_invocation_skills(fam)
    # Les QUATRE canaux, comme build_page (revue 2026-09-02) : cette section-ci est le
    # canal SERVI, celui que CLAUDE.md demande de controler ; elle republiait
    # « jamais invoques » ce que le journal du hook avait vu tourner le jour meme.
    vus = set(skills) | set(derniers_usages(state))
    unused_by_family = {}
    libref_unused = []
    hub_only_unused = []
    for name, family in fam.items():
        if name in vus:
            continue
        if name in HUB_ONLY_SKILLS:
            hub_only_unused.append(name)
        elif name in libref:
            libref_unused.append(name)
        else:
            unused_by_family.setdefault(family, []).append(name)
    unused_html = []
    for family in ("projet", "BMAD", "global"):
        names = sorted(unused_by_family.get(family, []))
        if not names:
            continue
        total_family = sum(1 for v in fam.values() if v == family)
        listing = ", ".join(f"<code>{_esc(n)}</code>" for n in names)
        if len(names) > 8:
            listing = f"<details><summary>Voir la liste ({len(names)})</summary><p>{listing}</p></details>"
        unused_html.append(
            f"      <p><strong>{family}</strong> — {len(names)}/{total_family} jamais invoqués : {listing}</p>"
        )
    if hub_only_unused:
        listing = ", ".join(f"<code>{_esc(n)}</code>" for n in sorted(hub_only_unused))
        unused_html.append(
            "      <p><strong>hub-only</strong> — s'invoquent depuis le hub de "
            "supervision en ciblant ce projet, jamais depuis ce dépôt : n=0 est ici "
            f"le fonctionnement nominal, pas un défaut d'usage : {listing}</p>"
        )
    if libref_unused:
        listing = ", ".join(f"<code>{_esc(n)}</code>" for n in sorted(libref_unused))
        unused_html.append(
            "      <p><strong>bibliothèque / référence</strong> — usage via scripts/sous-agent, "
            f"non capté par le compteur (n=0 ≠ mort, constat #2) : {listing}</p>"
        )
    todo_html = []
    avert = avertissement_mesure(state)
    if avert:
        todo_html.append(f'      <p class="avertissement"><em>{_md_inline(avert)}</em></p>')
    avert_veille = veille_sans_production(state)
    if avert_veille:
        todo_html.append(
            f'      <p class="avertissement"><em>{_md_inline(avert_veille)}</em></p>')
    for t in todos:
        todo_html.append(
            '      <div class="critical">\n'
            f"        <p>{_md_inline(t)}</p>\n"
            '        <span class="tag tag-confirme">CONFIRMÉ</span>\n'
            f'        <div class="tag-source">scan_transcripts.py · {today} · transcripts de session</div>\n'
            "      </div>"
        )
    if not todo_html:
        todo_html.append("      <p><em>(aucun constat — rien à signaler sur les données actuelles)</em></p>")
    diag_html = []
    for t in diag_todos or []:
        diag_html.append(
            '      <div class="critical">\n'
            f"        <p>{_md_inline(t)}</p>\n"
            '        <span class="tag tag-confirme">CONFIRMÉ</span>\n'
            f'        <div class="tag-source">agent-supervisor · étage 2</div>\n'
            "      </div>"
        )
    if diag_html:
        diag_statut = "à jour" if diag_a_jour else f"⚠️ à relancer (&gt; {DIAGNOSTIC_CADENCE_DAYS} j)"
        diag_body = f'      <p><em>Diagnostic {diag_statut}.</em></p>\n' + chr(10).join(diag_html)
    elif diagnostic_ran:
        diag_statut = "à jour" if diag_a_jour else f"⚠️ à relancer (&gt; {DIAGNOSTIC_CADENCE_DAYS} j)"
        diag_body = (
            f"      <p><em>Diagnostic {diag_statut} — rien à signaler, tous les constats "
            "précédents ont été arbitrés.</em></p>"
        )
    else:
        diag_body = (
            "      <p><em>Jamais lancé — invoquer la skill <code>agent-supervisor</code> "
            "(intégrée à <code>revue-increment</code>).</em></p>"
        )
    if masques:  # filtrage auditable (2026-07-28) — cf. build_page
        items = "".join(
            f"<li><s>{_esc(m['titre'])}</s> (<code>{_esc(m['cible'])}</code>)</li>" for m in masques
        )
        diag_body += (
            f"\n      <p><em>{len(masques)} constat(s) de ce diagnostic écarté(s) par un "
            "arbitrage — pour en rouvrir un, demander au superviseur un "
            f"<code>re_challenge</code> avec des données nouvelles :</em></p>\n      <ul>{items}</ul>"
        )
    if arbitrages:
        items = "\n".join(
            f"        <li><strong><code>{_esc(a['cible'])}</code></strong> ({_esc(a.get('date', '?'))}) : "
            f"{_esc(a['decision'])}</li>"
            for a in arbitrages
        )
        arbitrages_html = (
            "      <h3>Arbitrages enregistrés</h3>\n"
            "      <p><em>Constats clos par décision humaine (<code>.claude/supervision/arbitrages.json</code>) — "
            "l'usage réel reste mesuré ci-dessus.</em></p>\n"
            f"      <ul>\n{items}\n      </ul>\n"
        )
    else:
        arbitrages_html = ""
    if openhub and openhub["n"]:
        detail = ", ".join(f"<code>{_esc(k)}</code> ×{v}" for k, v in sorted(openhub["par_agent"].items()))
        openhub_html = (
            "      <h3>Agents OpenHub (app)</h3>\n"
            f"      <p><strong>{openhub['n']}</strong> résultat(s) en base (<code>agent_results</code>) — "
            f"{openhub['reels']} réel(s), {openhub['simules']} simulé(s) (fallback sans <code>opencode</code>) · "
            f"dernier : {_esc(_fmt_date(openhub['last']))}. {detail}</p>\n"
        )
    else:
        openhub_html = ""
    return f"""
    <section class="doc" id="agents-supervision">
      <p class="eyebrow">Projet</p>
      <h2>Supervision des agents — tableau de bord d'usage</h2>
      <p class="file-meta"><span>docs/wiki/technical/agents-supervision.md</span><span>généré : {_esc(state.get('last_scan', '?'))}</span></p>

      <div class="fact">
        <p><strong>Bloc généré automatiquement</strong> à chaque session (hook SessionStart → <code>.claude/supervision/scan_transcripts.py</code>, scan incrémental des transcripts, 0 token LLM) — ne pas éditer à la main. <strong>{nb_files} sessions</strong> couvertes · <strong>{total_skill}</strong> invocations de skills · <strong>{total_sub}</strong> lancements de sous-agents. Diagnostic qualitatif : skill <code>agent-supervisor</code> (étage 2, section diagnostic ci-dessous).</p>
        <span class="tag tag-confirme">CONFIRMÉ</span>
        <div class="tag-source">scan_transcripts.py · {today} · ~/.claude/projects/&lt;slug&gt;/*.jsonl</div>
      </div>

      <h3>Skills — usage réel</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Skill</th><th>Famille</th><th>Invocations</th><th>Première</th><th>Dernière</th></tr></thead>
          <tbody>
{_html_usage_rows(skills, fam)}
          </tbody>
        </table>
      </div>

      <h3>Sous-agents</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Sous-agent</th><th>Lancements</th><th>Premier</th><th>Dernier</th></tr></thead>
          <tbody>
{_html_usage_rows(subagents)}
          </tbody>
        </table>
      </div>

      <h3>Jamais utilisés</h3>
{chr(10).join(unused_html) if unused_html else "      <p><em>(tous les skills installés ont déjà été invoqués)</em></p>"}

      <h3>TODO agents — chantiers à lancer (constats automatiques)</h3>
{chr(10).join(todo_html)}
{openhub_html}
{arbitrages_html}      <h3>Diagnostic qualitatif (étage 2 — agent-supervisor)</h3>
{diag_body}
    </section>
"""


def update_wiki_html(state: dict, fam: dict, todos: list, diag_todos: list = None, diag_a_jour: bool = False,
                     openhub: dict = None, arbitrages: list = None, diagnostic_ran: bool = False,
                     masques: list = None) -> bool:
    """Remplace le bloc entre marqueurs TODO-AGENTS-HTML de docs/wiki.html.

    Ne fait rien si la page ou les marqueurs n'existent pas (les marqueurs sont posés
    une fois à la main dans la page ; ce script n'insère jamais à l'aveugle dans du HTML).

    Trois issues distinctes, parce que deux d'entre elles se confondaient dans le message
    de fin et faisaient crier à l'anomalie sur des projets parfaitement sains :
    True (bloc à jour), "absent" (pas de page HTML — cas NORMAL d'un projet cible, seul le
    hub publie un wiki HTML), False (page présente mais sans marqueurs — vraie anomalie).
    """
    try:
        with open(WIKI_HTML, encoding="utf-8") as fh:
            txt = fh.read()
    except OSError:
        return "absent"
    if HTML_MARK_START not in txt or HTML_MARK_END not in txt:
        return False
    block = (
        f"{HTML_MARK_START} — bloc généré par .claude/supervision/scan_transcripts.py, ne pas éditer à la main -->"
        + build_html_section(state, fam, todos, diag_todos, diag_a_jour, openhub, arbitrages,
                             diagnostic_ran, masques)
        + HTML_MARK_END
    )
    pattern = re.escape(HTML_MARK_START) + r".*?" + re.escape(HTML_MARK_END)
    new_txt = re.sub(pattern, lambda m: block, txt, flags=re.DOTALL)
    if new_txt != txt:
        with open(WIKI_HTML, "w", encoding="utf-8") as fh:
            fh.write(new_txt)
    return True


def update_index(todos: list, avertissement: str = "") -> None:
    """`avertissement` : la ligne « mesure incomplète » d'`avertissement_mesure()`,
    reprise ICI aussi — l'index est la première page lue, et un extrait de trois
    constats y est plus exposé encore que la page complète."""
    bullets = "\n".join(f"- {t}" for t in todos[:3]) or "- _(aucun constat automatique)_"
    if avertissement:
        bullets = f"{avertissement}\n\n{bullets}"
    block = (
        f"{MARK_START} — section générée par .claude/supervision/scan_transcripts.py, ne pas éditer à la main -->\n"
        "## TODO agents 🤖\n"
        "\n"
        "Constats automatiques du superviseur d'agents (usage mesuré dans les transcripts de session) :\n"
        "\n"
        f"{bullets}\n"
        "\n"
        "Tableau de bord complet : [technical/agents-supervision.md](technical/agents-supervision.md) — régénéré à chaque session.\n"
        f"{MARK_END}"
    )
    try:
        with open(WIKI_INDEX, encoding="utf-8") as fh:
            txt = fh.read()
    except FileNotFoundError:
        txt = ""   # premier passage : la page est créée avec le bloc seul
    except OSError as exc:
        # Un échec de LECTURE ne doit JAMAIS devenir un ÉCRASEMENT. Rabattre sur ""
        # puis réécrire en "w" détruisait la page rédigée à la main (reproduit :
        # 1466 -> 422 octets, sans un message). On renonce à la mise à jour et on le
        # dit : fail-open — la section TODO n'est pas rafraîchie, rien de plus, le
        # démarrage de session n'est pas cassé pour autant.
        print(f"  index.md non mis a jour : lecture impossible "
              f"({exc.__class__.__name__}) - section TODO agents laissee en l'etat.")
        return
    if MARK_START in txt and MARK_END in txt:
        pattern = re.escape(MARK_START) + r".*?" + re.escape(MARK_END)
        txt = re.sub(pattern, lambda m: block, txt, flags=re.DOTALL)
    else:
        txt = (txt.rstrip("\n") + "\n\n" if txt else "") + block + "\n"
    with open(WIKI_INDEX, "w", encoding="utf-8") as fh:
        fh.write(txt)


# Seuil au-delà duquel un run en-attente-validation est signalé au démarrage.
RUN_A_SOLDER_H = 24


def runs_a_solder(runs, maintenant=None):
    """Runs `en-attente-validation` avec leur âge en heures, du plus vieux au
    plus récent (constat interaction VSCode2 2026-07-29 : 2 runs oubliés 4 j et
    1 j, le lot précédent n'ayant été soldé que sur relance explicite de
    l'utilisateur). Déterministe, 0 token — le solde reste manuel via
    `log_run.py --solde`, seule la VISIBILITÉ est automatisée."""
    maintenant = maintenant or dt.datetime.now().astimezone()
    ouverts = []
    def _ascii(texte):
        # `demande` est du texte libre : le journal porte déjà des caractères hors
        # cp1252 (U+FFFD hérité d'un mojibake). Les rendre inoffensifs AVANT le
        # print — sinon la ligne relance l'incident qu'elle documente.
        return str(texte).encode("ascii", "replace").decode("ascii")

    for run in runs:
        if run.get("resultat") != "en-attente-validation":
            continue
        try:
            ts = dt.datetime.fromisoformat(str(run.get("ts", "")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=maintenant.tzinfo)
        heures = (maintenant - ts).total_seconds() / 3600
        if heures >= RUN_A_SOLDER_H:
            ouverts.append({"ts": run.get("ts"), "heures": int(heures),
                            "demande": _ascii(run.get("demande", ""))[:70]})
    return sorted(ouverts, key=lambda r: -r["heures"])


def agents_apparus(state) -> list:
    """Sous-agents (`.claude/agents/*.md`) apparus depuis le passage précédent du hook.

    Finding `agents:types-non-charges-en-session` (diagnostic 2026-07-30, arbitré le
    jour même) : le registre des types d'agents est chargé au DÉMARRAGE de session — un
    sous-agent écrit en cours de séance n'est pas adressable par l'outil Agent tout de
    suite, et rien ne disait QUAND il le devenait. Constaté en vrai : `subagent_type:
    agent-supervisor` refusé dans la session qui venait d'écrire le fichier. Ce hook,
    lui, tourne au démarrage : ce qu'il annonce ici est adressable dans la séance qui
    s'ouvre.

    Premier passage : la liste est enregistrée SANS rien annoncer — sinon tous les
    agents déjà en place seraient signalés comme neufs. Fail-open : dossier absent ou
    illisible -> aucune annonce, jamais d'erreur (ce script ne bloque jamais un
    démarrage de session)."""
    try:
        presents = sorted(f[:-3] for f in os.listdir(os.path.join(REPO, ".claude", "agents"))
                          if f.endswith(".md"))
    except OSError:
        presents = []
    connus = state.get("agents_connus")
    state["agents_connus"] = presents
    if connus is None:
        return []
    return [a for a in presents if a not in connus]


def arbre_sale():
    """Fichiers modifiés/non suivis du dépôt (hors données générées du scan).

    Constat ko-repete VSCode2 2026-07-29 : une séance a été close sur du code
    produit jamais commité ni journalisé — invisible de l'historique comme de la
    supervision. Le signal se pose donc au DÉMARRAGE de la séance suivante.
    Fail-open : git indisponible -> aucune ligne, jamais d'erreur."""
    ignores = ("docs/wiki", ".claude/supervision/", ".claude/orchestration/routing-hints.json",
               ".claude/orchestration/runs.jsonl", ".claude/orchestration/prompts.jsonl")
    try:
        res = subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                             capture_output=True, text=True, encoding="utf-8", timeout=8)
    except Exception:
        return []
    if res.returncode != 0:
        return []
    fichiers = []
    for ligne in res.stdout.splitlines():
        chemin = ligne[3:].strip().replace("\\", "/")
        if chemin and not chemin.startswith(ignores):
            # Même contrainte que runs_a_solder : un nom de fichier accentué ne
            # doit pas casser stdout capturé en cp1252 par les tests des cibles.
            fichiers.append(chemin.encode("ascii", "replace").decode("ascii"))
    return fichiers


def commits_non_journalises():
    """Commits réels faits depuis le dernier run journalisé, sans qu'aucun run ne
    les couvre — le trou qui a laissé une demande utilisateur (permissions Bash,
    2026-09-03 19:30:50Z) traitée SANS laisser de trace arbitrable : `arbre_sale()`
    ne voit qu'un travail encore NON commité, mais la même faute vaut pour un commit
    réel qu'aucun run ne journalise ensuite (finding
    `VScode5:seance-non-journalisee-2026-09-03`, 2026-09-04). Dénominateur : le
    dernier `ts` de `runs.jsonl` (append-only, donc croissant) comparé à
    `git log --since=<ce ts>`.

    N'est PAS un détecteur précis de « travail non journalisé » — une exécution
    directe (étape 1 de la skill agent-orchestrator) commite parfois sans se
    journaliser PAR CONCEPTION, et ce n'est pas une faute. C'est un simple rappel :
    « voici ce qui a été commité depuis le dernier run, vérifier qu'aucune demande
    ne s'y est perdue » — au lecteur de juger, comme pour `arbre_sale()`.

    Fail-open : aucun run encore journalisé, ou git indisponible -> liste vide."""
    runs = [r for r in load_jsonl(RUNS_PATH) if isinstance(r, dict)]
    horodates = sorted(r["ts"] for r in runs
                       if isinstance(r.get("ts"), str) and r["ts"])
    if not horodates:
        return []
    dernier_run = horodates[-1]
    try:
        res = subprocess.run(
            ["git", "log", f"--since={dernier_run}", "--format=%h|%cI|%s"],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10)
    except Exception:
        return []
    if res.returncode != 0:
        return []
    commits = []
    for ligne in res.stdout.splitlines():
        parts = ligne.split("|", 2)
        if len(parts) == 3:
            hash_, ts, sujet = parts
            commits.append({
                "hash": hash_,
                "sujet": sujet.encode("ascii", "replace").decode("ascii"),
            })
    return commits


def commits_non_pousses():
    """Commits locaux jamais poussés sur la branche amont — signale un correctif
    réel (ex. un CSRF ou un DoS corrigé et vérifié) resté invisible hors du poste
    qui l'a produit, tant qu'il n'est pas poussé (finding
    `flotte:commits-jamais-pousses`, 2026-09-04 : 36 commits non poussés mesurés
    sur les 6 dépôts de la flotte, dont deux correctifs de sécurité du jour même).

    Dénominateur : `git log <amont>..HEAD` si un amont est configuré (`@{u}`),
    sinon liste vide (fail-open — poste sans remote suivi, ou premier commit
    d'une branche encore non poussée : pas une faute à signaler).

    Volontairement un simple compteur informatif, jamais un déclencheur qui
    pousserait à la place de l'utilisateur : `git push` reste hors de
    l'allowlist du dispositif par choix délibéré (arbitrage
    `VScode5:allowlist-permissions-inoperante`, 2026-09-04) — la friction sur ce
    geste est le prix de la revue humaine avant publication, pas un défaut à
    corriger en l'automatisant."""
    try:
        amont = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10)
    except Exception:
        return []
    if amont.returncode != 0:
        return []
    ref_amont = amont.stdout.strip()
    if not ref_amont:
        return []
    try:
        res = subprocess.run(
            ["git", "log", f"{ref_amont}..HEAD", "--format=%h|%s"],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10)
    except Exception:
        return []
    if res.returncode != 0:
        return []
    commits = []
    for ligne in res.stdout.splitlines():
        parts = ligne.split("|", 1)
        if len(parts) == 2:
            hash_, sujet = parts
            commits.append({
                "hash": hash_,
                "sujet": sujet.encode("ascii", "replace").decode("ascii"),
            })
    return commits


def main(argv) -> int:
    state = {} if "--full" in argv else load_state()
    new_events = scan(state)
    # Troisieme canal (2026-09-02) : le journal du hook PostToolUse survit a la purge
    # des transcripts. Sans cet appel, scan_journal_usage() est une fonction definie
    # et jamais lue — exactement l'etat dans lequel la seance precedente l'a laissee.
    new_events += scan_journal_usage(state)
    apparus = agents_apparus(state)   # avant save_state : la liste connue s'y enregistre
    # Fiabilité de la mesure (finding state-transcripts-absents, 2026-09-02) : après
    # scan() donc sur le state['files'] à jour du passage courant, avant save_state
    # pour que le constat soit persisté avec le reste de l'état.
    state["mesure_incomplete"] = mesure_incomplete(state)
    save_state(state)
    fam = installed_skills()
    runs = load_jsonl(RUNS_PATH)
    arbitrages = load_arbitrages()
    todos = build_todos(state.get("skills", {}), fam, catalogue_gaps(runs),
                        arbitrages, state=state)

    par_playbook, par_agent = build_runs_stats(runs)
    diagnostic = load_diagnostic()
    diag_todos = diagnostic_todos(diagnostic, arbitrages)
    masques = diagnostic_masques(diagnostic, arbitrages)
    diag_a_jour = diagnostic_a_jour(diagnostic, runs)
    hints = build_routing_hints(state, fam, par_playbook, par_agent, diagnostic, runs, arbitrages)
    # Cle NEUVE `variantes` (jamais `playbooks`, dont les lecteurs ne la connaissent pas) :
    # verdicts de `optimiseur.py`, calcules sur les memes runs. Fail-open, mais pas muet :
    # module absent (ImportError) = rien d'ecrit ; tout autre echec laisse sa trace
    # `{"erreur": <type>}` que le wiki affiche au lieu d'un faux « rejouer le scan ».
    try:
        if SUP_DIR not in sys.path:
            sys.path.append(SUP_DIR)
        import optimiseur
        hints["variantes"] = optimiseur.calculer(runs, optimiseur.charger_seuils())
    except ImportError:
        pass
    except Exception as exc:
        hints["variantes"] = {"erreur": type(exc).__name__}
    hints_dir = os.path.dirname(ROUTING_HINTS_PATH)
    if hints_dir:
        os.makedirs(hints_dir, exist_ok=True)
    with open(ROUTING_HINTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(hints, fh, ensure_ascii=False, indent=1)

    page_dir = os.path.dirname(WIKI_PAGE)
    if page_dir:
        os.makedirs(page_dir, exist_ok=True)
    diagnostic_ran = diagnostic is not None
    openhub = openhub_stats()
    # Le CONTENU est calcule AVANT l'ouverture du fichier (chasse aux cas limites,
    # 2026-09-02) : `open(..., "w")` tronque des l'ouverture, et une exception levee
    # PENDANT `build_page(...)` (celui-ci a gagne deux nouvelles sources d'exception
    # avec cet increment) laissait la page a 0 octet, `rc=0`, scan silencieusement
    # degrade -- exactement le defaut que ce fichier existe pour signaler ailleurs.
    contenu_page = build_page(state, fam, todos, diag_todos, diag_a_jour, openhub, arbitrages,
                              diagnostic_ran, masques)
    with open(WIKI_PAGE, "w", encoding="utf-8") as fh:
        fh.write(contenu_page)
    update_index(todos, avertissement_mesure(state))
    html_ok = update_wiki_html(state, fam, todos, diag_todos, diag_a_jour, openhub, arbitrages,
                               diagnostic_ran, masques)
    missing = state.get("transcript_dir_missing")
    detail = f" (transcripts introuvables : {missing})" if missing else ""
    mesure = state.get("mesure_incomplete") or {}
    if mesure.get("transcripts_absents"):
        # La base a fondu : le dire au fil du démarrage, pas seulement dans les
        # fichiers générés — c'est ce qui manquait le 2026-09-01 (finding
        # state-transcripts-absents), quand le scan a publié 75 skills « jamais
        # utilisées » sur une base dont personne n'avait dit qu'elle s'était vidée.
        detail += (f" (mesure non fiable : {mesure['transcripts_absents']}/"
                   f"{mesure.get('total_fichiers', '?')} transcript(s) references "
                   "disparus du disque)")
    if mesure.get("journal_usage_muet"):
        detail += (" (journal d'usage present mais illisible : le 3e canal ne mesure "
                    "plus rien depuis un scan indetermine)")
    if html_ok is False:
        detail += " (wiki.html sans marqueurs TODO-AGENTS-HTML : bloc HTML non mis a jour)"
    if not diag_a_jour:
        detail += " (diagnostic agent-supervisor a lancer ou perime)"
    if masques:
        # Le filtrage ne doit jamais être silencieux : le superviseur peut écrire des
        # constats justes et n'en afficher aucun (constat prio 5 du 2026-07-28).
        detail += f" ({len(masques)} constat(s) du diagnostic ecarte(s) par arbitrage)"
    inconnues = categories_inconnues(arbitrages)
    if inconnues:
        detail += (" (arbitrages.json : categorie(s) hors vocabulaire, sans effet -> "
                   + ", ".join(inconnues) + ")")
    illisibles = LIGNES_ILLISIBLES.get(RUNS_PATH, 0)
    if illisibles:
        detail += f" ({illisibles} ligne(s) illisible(s) dans runs.jsonl, ignoree(s))"
    print(
        f"Supervision agents : +{new_events} evenement(s), {len(state.get('files', {}))} sessions couvertes, "
        f"{len(todos)} TODO, {len(runs)} run(s) orchestrateur -> agents-supervision.md, index.md"
        f"{' et wiki.html' if html_ok is True else ''}, routing-hints.json a jour.{detail}"
    )
    # stdout du scan : ASCII strict. Les tests du dispositif capturent ce flux en
    # subprocess (console cp1252 sur Windows) — un caractere hors cp1252 y leve
    # UnicodeDecodeError et rend stdout None (incident verifie le 2026-07-29).
    for run in runs_a_solder(runs):
        # ts COMPLET, jamais tronque : `log_run.py --solde` exige EXACTEMENT une
        # correspondance de prefixe et rend rc=1 sinon. Tronquer a l'heure ([:13])
        # rendait donc la commande officielle inutilisable des que deux runs
        # partageaient l'heure -- mesure du 2026-08-31 sur le journal reel : 24
        # prefixes horaires sur 36 en collision, les 8 runs en attente touches. Or
        # R5 interdit l'edition manuelle du journal : sans prefixe unique, la
        # boucle en-attente-validation ne se referme plus.
        print(f"  run a solder (il y a {run['heures']} h) : {run['demande']} "
              f"-> py .claude/orchestration/log_run.py --solde \"{run['ts']}\" succes \"note\"")
    if apparus:
        print(f"  sous-agent(s) desormais adressable(s) par l'outil Agent : "
              f"{', '.join(apparus)} - ecrit(s) hors de cette session, donc utilisable(s) "
              f"a partir de ce demarrage.")
    # Le point qui repare la visibilite de la DUREE : un plantage precedent a defile
    # dans un demarrage de session et disparu. Le scan suivant le remonte, puis
    # l acquitte pour ne pas le repeter indefiniment.
    incidents = incidents_a_signaler()
    if incidents:
        # Meme prudence que dans signaler_incident : on ne sait pas ou le scan
        # s est arrete, donc on ne prononce pas sur ce qui a ete mesure ou non.
        print(f"  ATTENTION : {len(incidents)} scan(s) interrompu(s) depuis le "
              "dernier demarrage sain - leurs mesures sont partielles ou absentes :")
        for ligne in incidents[-5:]:
            print("    " + ligne)
        acquitter_incidents()
    reliquat = arbre_sale()
    if reliquat:
        apercu = ", ".join(reliquat[:5]) + ("..." if len(reliquat) > 5 else "")
        print(f"  reliquat de la seance precedente : {len(reliquat)} fichier(s) "
              f"non commite(s) ({apercu}) - committer ou nommer avant toute nouvelle demande.")
    commits_non_journalises_ = commits_non_journalises()
    if commits_non_journalises_:
        apercu = ", ".join(f"{c['hash']} {c['sujet']}" for c in commits_non_journalises_[:3])
        suite = "..." if len(commits_non_journalises_) > 3 else ""
        print(f"  {len(commits_non_journalises_)} commit(s) depuis le dernier run "
              f"journalise ({apercu}{suite}) - verifier qu'aucune demande ne s'y "
              "est perdue sans run ni arbitrage.")
    commits_non_pousses_ = commits_non_pousses()
    if commits_non_pousses_:
        apercu = ", ".join(f"{c['hash']} {c['sujet']}" for c in commits_non_pousses_[:3])
        suite = "..." if len(commits_non_pousses_) > 3 else ""
        print(f"  {len(commits_non_pousses_)} commit(s) local(-aux) jamais pousse(s) "
              f"({apercu}{suite}) - git push reste hors allowlist (choix delibere), "
              "a valider par vous.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # jamais bloquer le démarrage de session
        sys.exit(signaler_incident(exc))
