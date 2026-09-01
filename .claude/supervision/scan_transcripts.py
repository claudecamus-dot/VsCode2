# +-- GÉNÉRÉ — NE PAS ÉDITER LOCALEMENT ---------------------------------------
# | Source de vérité : hub de supervision VScode5, .claude/dispositif/canon/scan_transcripts.py
# | Une correction faite ICI sera ÉCRASÉE à la prochaine propagation. Pour la
# | garder : la signaler au hub, qui corrige le canon et re-synchronise.
# | (Depuis le hub : « py .claude/dispositif/sync_dispositif.py » — ce script
# |  n'est pas déployé, il n'existe pas dans ce dépôt.)
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
AGENT_SUPERVISION_OPENHUB_DB, AGENT_SUPERVISION_ARBITRAGES.
"""
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys

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
ROUTING_HINTS_PATH = os.environ.get("AGENT_SUPERVISION_ROUTING_HINTS") or os.path.join(
    REPO, ".claude", "orchestration", "routing-hints.json"
)
DIAGNOSTIC_PATH = os.environ.get("AGENT_SUPERVISION_DIAGNOSTIC") or os.path.join(
    SUP_DIR, "diagnostic.json"
)
OPENHUB_DB = os.environ.get("AGENT_SUPERVISION_OPENHUB_DB") or os.path.join(
    REPO, "data", "app.db"
)
ARBITRAGES_PATH = os.environ.get("AGENT_SUPERVISION_ARBITRAGES") or os.path.join(
    SUP_DIR, "arbitrages.json"
)
DORMANT_DAYS = 30
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
        with open(path, encoding="utf-8", errors="replace") as fh:
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


def _couvre(arbitrage: dict, categorie: str) -> bool:
    """Cet arbitrage ferme-t-il cette CATÉGORIE de constat ?

    `categories` absent = ferme tout (rétro-compatible). Liste = ferme exactement ces
    catégories — donc `[]` ne ferme rien, la lecture naturelle. Un champ mal formé
    (chaîne, nombre) ne ferme rien non plus : un `in` sur une chaîne matcherait par
    sous-chaîne (`"interaction" in "interactions-multiples"`), silencieusement faux."""
    cats = arbitrage.get("categories")
    if cats is None:
        return True
    return isinstance(cats, list) and categorie in cats


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
    couvrants = [a for a in arbitrages or [] if a.get("cible") == cible and _couvre(a, cat)]
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
    jour = (posterieur_a or "")[:10]
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
        {"titre": _titre_court(f), "cible": f.get("cible") or "?"}
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
        titre = _titre_court(f)
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


def openhub_stats():
    """Couverture OpenHub (incrément C) : lit la table agent_results de l'app (SQLite,
    lecture seule) — résultats réels vs fallback simulé (opencode absent). None si base
    ou table absente : la couverture reste optionnelle, jamais bloquante."""
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
        })
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


def build_routing_hints(state: dict, fam: dict, par_playbook: dict, par_agent: dict, diagnostic,
                        runs: list = None, arbitrages: list = None) -> dict:
    """Sens superviseur → orchestrateur (conception §6) : ce que le scan mesure, appliqué
    par la skill agent-orchestrator lors de la composition d'un plan."""
    skills = state.get("skills", {})
    subagents = state.get("subagents", {})
    combined = {**skills, **subagents}
    eprouves = sorted(k for k, e in combined.items() if e["n"] >= PROVEN_MIN)
    libref = non_invocation_skills(fam)
    jamais = sorted(k for k, v in fam.items() if k not in skills and k not in libref)
    bibliotheque = sorted(k for k in libref if k not in skills)
    en_sommeil = sorted(
        k for k, e in combined.items()
        if (lambda d: d is not None and d > DORMANT_DAYS)(days_since(e.get("last", "")))
    )
    verifs_oubliees = []
    if "revue-increment" in fam and "revue-increment" not in skills:
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
            prudence.append({"cible": f["cible"], "raison": _titre_court(f)})
    # Incrément C — prudence déterministe : échecs répétés dans le journal d'orchestration,
    # sans attendre le diagnostic LLM (dédupliqué sur les cibles déjà signalées).
    deja = {p["cible"] for p in prudence}
    for agent, e in sorted(par_agent.items()):
        if agent not in deja and e["echecs"] >= ECHEC_PRUDENCE_MIN and e["echecs"] > e["succes"]:
            prudence.append({
                "cible": agent,
                "raison": f"échecs répétés en orchestration ({e['echecs']}/{e['n']} runs)",
            })
    gaps = catalogue_gaps(runs or [])
    return {
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "eprouves": eprouves,
        "jamais_utilises": jamais,
        # Skills-bibliothèque/référence : usage réel non capté par le compteur
        # d'invocations (constat #2) — sortis de jamais_utilises pour que
        # l'orchestrateur ne les traite pas comme morts.
        "bibliotheque_reference": bibliotheque,
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
    }


def build_todos(skills: dict, fam: dict, gaps: dict = None, arbitrages: list = None) -> list:
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
    bmad = [k for k, v in fam.items() if v == "BMAD"]
    bmad_unused = [k for k in bmad if k not in skills]
    if "famille:BMAD" in arbitres:
        bmad_unused = []  # tri déjà arbitré par l'humain — ne pas re-nagguer
    if bmad and bmad_unused:
        if len(bmad_unused) == len(bmad):
            todos.append(
                f"**Trier les skills BMAD** : {len(bmad)} installés, 0 invocation à ce jour — "
                "décider lesquels garder, customiser ou désinstaller."
            )
        else:
            todos.append(
                f"**Élaguer les skills BMAD** : {len(bmad_unused)}/{len(bmad)} jamais invoqués — "
                "confirmer l'utilité des non-utilisés."
            )
    # Les skills-bibliothèque/référence (constat #2) ne sont pas des « sans usage » :
    # leur valeur passe par scripts/sous-agent, invisible au compteur d'invocations.
    libref = non_invocation_skills(fam)
    proj_unused = sorted(
        k for k, v in fam.items()
        if v == "projet" and k not in skills and k not in arbitres and k not in libref
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
    dormant = sorted(
        k
        for k, e in skills.items()
        if (lambda d: d is not None and d > DORMANT_DAYS)(days_since(e.get("last", "")))
    )
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


def build_page(state: dict, fam: dict, todos: list, diag_todos: list = None, diag_a_jour: bool = False,
               openhub: dict = None, arbitrages: list = None, diagnostic_ran: bool = False,
               masques: list = None) -> str:
    skills = state.get("skills", {})
    subagents = state.get("subagents", {})
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
    L += ["", "## Jamais utilisés", ""]
    unused_by_family = {}
    libref_unused = []
    for name, family in fam.items():
        if name in skills:
            continue
        if name in libref:
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
    skills = state.get("skills", {})
    subagents = state.get("subagents", {})
    nb_files = len(state.get("files", {}))
    total_skill = sum(e["n"] for e in skills.values())
    total_sub = sum(e["n"] for e in subagents.values())
    today = dt.date.today().isoformat()
    libref = non_invocation_skills(fam)
    unused_by_family = {}
    libref_unused = []
    for name, family in fam.items():
        if name in skills:
            continue
        if name in libref:
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
    if libref_unused:
        listing = ", ".join(f"<code>{_esc(n)}</code>" for n in sorted(libref_unused))
        unused_html.append(
            "      <p><strong>bibliothèque / référence</strong> — usage via scripts/sous-agent, "
            f"non capté par le compteur (n=0 ≠ mort, constat #2) : {listing}</p>"
        )
    todo_html = []
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

{openhub_html}
      <h3>TODO agents — chantiers à lancer (constats automatiques)</h3>
{chr(10).join(todo_html)}

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


def update_index(todos: list) -> None:
    bullets = "\n".join(f"- {t}" for t in todos[:3]) or "- _(aucun constat automatique)_"
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
               ".claude/orchestration/runs.jsonl")
    try:
        res = subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                             capture_output=True, text=True, timeout=8)
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


def main(argv) -> int:
    state = {} if "--full" in argv else load_state()
    new_events = scan(state)
    apparus = agents_apparus(state)   # avant save_state : la liste connue s'y enregistre
    save_state(state)
    fam = installed_skills()
    runs = load_jsonl(RUNS_PATH)
    arbitrages = load_arbitrages()
    todos = build_todos(state.get("skills", {}), fam, catalogue_gaps(runs), arbitrages)

    par_playbook, par_agent = build_runs_stats(runs)
    diagnostic = load_diagnostic()
    diag_todos = diagnostic_todos(diagnostic, arbitrages)
    masques = diagnostic_masques(diagnostic, arbitrages)
    diag_a_jour = diagnostic_a_jour(diagnostic, runs)
    openhub = openhub_stats()
    hints = build_routing_hints(state, fam, par_playbook, par_agent, diagnostic, runs, arbitrages)
    hints_dir = os.path.dirname(ROUTING_HINTS_PATH)
    if hints_dir:
        os.makedirs(hints_dir, exist_ok=True)
    with open(ROUTING_HINTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(hints, fh, ensure_ascii=False, indent=1)

    page_dir = os.path.dirname(WIKI_PAGE)
    if page_dir:
        os.makedirs(page_dir, exist_ok=True)
    diagnostic_ran = diagnostic is not None
    with open(WIKI_PAGE, "w", encoding="utf-8") as fh:
        fh.write(build_page(state, fam, todos, diag_todos, diag_a_jour, openhub, arbitrages,
                            diagnostic_ran, masques))
    update_index(todos)
    html_ok = update_wiki_html(state, fam, todos, diag_todos, diag_a_jour, openhub, arbitrages,
                               diagnostic_ran, masques)
    missing = state.get("transcript_dir_missing")
    detail = f" (transcripts introuvables : {missing})" if missing else ""
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
    reliquat = arbre_sale()
    if reliquat:
        apercu = ", ".join(reliquat[:5]) + ("..." if len(reliquat) > 5 else "")
        print(f"  reliquat de la seance precedente : {len(reliquat)} fichier(s) "
              f"non commite(s) ({apercu}) - committer ou nommer avant toute nouvelle demande.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # jamais bloquer le démarrage de session
        print(f"Supervision agents : scan ignore ({exc.__class__.__name__}: {exc})")
        sys.exit(0)
