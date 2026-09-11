r"""PreToolUse hook (Bash/PowerShell) — soft, NON-blocking reminder that warns
when a project's watched code paths are about to be committed without a real
verification having run in the current session.

Provenance : proposition du constat #1 du superviseur d'agents (étage 2),
arbitrée puis appliquée le 2026-07-21. Le diagnostic (voir
`docs/wiki/technical/agents-supervision.md`) montrait que la vérif réelle de
fin d'incrément était systématiquement sautée : `revue-increment` n=0 sur 14
sessions, `pptx-verify` figé à 1 usage, alors que du code continuait d'être
commité. Le rappel SessionStart passif (`remind_revue_increment.py`) ne
suffit pas — rien n'oblige à le suivre. Ce hook déplace le rappel AU BON
INSTANT : le commit.

Conception (delta assumé vs. la proposition brute) :
- **Non bloquant** : émet un `systemMessage` (visible utilisateur) + un
  `additionalContext` (visible modèle si supporté), SANS `permissionDecision`.
  Le commit passe — on avertit, on ne bloque pas (cf. guard_destructive_git.py,
  lui, bloque : ce sont deux niveaux de sévérité volontairement distincts).
- **Zone surveillée et preuves de vérif CONFIGURABLES par projet**, pas
  figées dans le code (voir bloc « Configuration par projet » plus bas).
  Historique du défaut corrigé le 2026-09-02 (revue de sécurité du
  2026-09-01, finding « le kit publié embarque les chemins surveillés d'un
  AUTRE projet ») : ce fichier est la SOURCE que le hub de supervision publie
  dans le kit agentic installé par cinq dépôts
  (`export_agentic.GENERIQUE` pointe `~/Documents/VSCode3/.claude/hooks`).
- **Détection de trace de vérif = vraie exécution d'outil**, pas une simple
  mention : on parse le transcript de la session (tool_use Bash/PowerShell
  correspondant à `_VERIF_BASH` / Skill correspondant à `_VERIF_SKILL`),
  même structure que scan_transcripts.py — sinon toute session qui *parle*
  de vérif se faux-négativerait.
- **Fail-open partout** : toute erreur (parsing, git indisponible, transcript
  illisible, import, configuration de projet illisible/malformée) rend la
  main SANS avertir. Un bug ici ne doit jamais ajouter de friction ni
  bloquer un commit.

Fusion du 2026-09-03 (arbitrage utilisateur « propage le mécanisme
anti-hallucination ») : trois branches de ce hook avaient évolué
INDÉPENDAMMENT sur trois dépôts cibles sans jamais être réconciliées —
1. la généralisation JSON-config décrite ci-dessus (hub / VSCode3, 2026-09-02) ;
2. le second signal « definition-of-done assumée » (VSCode1, constats
   superviseur #1/#2 du 2026-07-28) : des tests verts ne valent pas une
   definition-of-done — silence sur la zone surveillée seulement si
   `/revue-increment` a tourné, OU qu'un run a été journalisé (`log_run.py`),
   OU que le message de commit assume explicitement « DoD allégée » ;
3. le troisième signal « dispositif sans fichiers-contrat » (VSCode2, constat
   superviseur `sync-canon` du 2026-07-29, sur incident réel : 5eb121b a cassé
   un test-contrat, vu seulement à la revue suivante) — un commit touchant
   `.claude/orchestration|supervision|hooks` sans trace des tests-contrat du
   dépôt dans la session.
Les deux signaux ajoutés sont OPT-IN par configuration (`dod_enabled`,
`dispositif_tests`) — DÉSACTIVÉS par défaut. Ce n'est pas une demi-mesure :
VSCode3 (source de ce fichier) verrouille par test la SILENCE totale quand
seule une vérif classique a tourné (`test_vscode3_silencieux_si_pytest_a_deja_tourne`)
— y activer le signal DoD sans arbitrage romprait ce contrat. Chaque dépôt
active ce qu'il a explicitement choisi via sa propre configuration ; aucun
n'hérite d'un nouveau signal sans le déclarer.

Le tokenizer shell robuste (heredocs, segments quote-safe) est réutilisé de
`guard_destructive_git.py` (même répertoire) pour ne pas diverger d'un second
parseur du même problème ; si l'import échoue, dégradation en silence.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import unicodedata

try:  # réutilise le tokenizer éprouvé du guard voisin ; sinon, dégrade en silence
    from guard_destructive_git import _strip_heredocs, _segments
except Exception:  # pragma: no cover - fail-open
    _strip_heredocs = None
    _segments = None

# --- Configuration par projet ------------------------------------------------
# Mécanisme retenu : un fichier JSON optionnel, `warn_verif_before_commit.json`,
# à la racine `.claude/` du dépôt CIBLE (celui où le hook s'exécute) — pas une
# auto-détection de `app/` vs `src/` vs `docs/...`, qui devinerait le périmètre
# applicatif d'un dépôt inconnu plutôt que de le lire explicitement. Le chemin
# est dérivé de l'emplacement de CE fichier (`<repo>/.claude/hooks/…`), jamais
# du `cwd` transmis par l'outil : un commit lancé depuis un sous-dossier ne
# doit pas faire manquer la configuration du dépôt.
#
# Absente, illisible ou JSON malformée : repli intégral sur un canal générique
# (fail-open) — jamais une erreur, jamais un hook silencieux par construction.
# Une config partielle (un seul champ renseigné) ne complète que les champs
# manquants avec ce même repli, plutôt que de tout invalider.
_CONFIG_FILENAME = "warn_verif_before_commit.json"

# Repli générique : le canal historique de ce hook avant son adaptation à
# VSCode3 (VSCode1, 2026-07-21). Il n'a plus vocation à décrire UN projet —
# seulement à garantir qu'un dépôt sans configuration obtient un déclencheur
# non vide plutôt qu'un hook silencieux par défaut.
_DEFAULT_WATCHED_PREFIXES = ("app/",)
_DEFAULT_VERIF_BASH = ("npm test", "pytest", "-m pytest")
_DEFAULT_VERIF_SKILL = ("revue-increment",)

# Signaux additionnels (fusion du 2026-09-03) : OPT-IN, désactivés tant qu'un
# projet ne les déclare pas explicitement dans sa configuration.
_DEFAULT_DOD_ENABLED = False
_DEFAULT_DISPOSITIF_PREFIXES = (".claude/orchestration/", ".claude/supervision/", ".claude/hooks/")
_DEFAULT_DISPOSITIF_TESTS = ()  # vide = signal dispositif desactive
# Gardes du 2026-09-10, OPT-IN elles aussi. Ce fichier est publié tel quel dans
# cinq dépôts, et son contrat (bloc du haut) dit : « Chaque dépôt active ce
# qu'il a explicitement choisi via sa propre configuration ; aucun n'hérite
# d'un nouveau signal sans le déclarer. » Les livrer inconditionnelles aurait
# imposé à VSCode1/3/4 un signal que personne n'y a arbitré — et cassé le
# contrat dans le fichier qui l'énonce (revue du 2026-09-10, M11).
_DEFAULT_PERIMETRE_ENABLED = False
_DEFAULT_PLAFOND_LOT = 0  # 0 = signal desactive


def _config_path():
    """`<repo>/.claude/warn_verif_before_commit.json`, dérivé de l'emplacement
    de ce fichier (`<repo>/.claude/hooks/…`) — jamais du cwd du commit."""
    hooks_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(hooks_dir), _CONFIG_FILENAME)


def _as_str_tuple(value, default):
    """Liste JSON -> tuple de chaines non vides, ou `default` si `value` n'est
    pas une liste exploitable (absente, mauvais type, vide)."""
    if not isinstance(value, list):
        return default
    cleaned = tuple(v for v in value if isinstance(v, str) and v)
    return cleaned or default


def _read_config_dict():
    """Le dict JSON de configuration du dépôt cible, ou None si absent,
    illisible ou malformé — jamais d'exception propagée."""
    try:
        with open(_config_path(), encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else None
    except Exception:
        return None


def _load_config():
    """(watched_prefixes, verif_bash, verif_skill) effectifs pour ce dépôt —
    contrat d'arité STABLE (3-tuple), verrouillé par le test de non-régression
    de VSCode3 qui l'unpack directement. Les signaux ajoutés en 2026-09-03 se
    chargent séparément via `_load_extra_config()`, pour ne jamais changer
    cette arité.

    Fail-open champ par champ : un fichier absent, illisible ou dont le JSON
    est invalide retombe entièrement sur le repli générique ; une config
    présente mais partielle complète uniquement les champs manquants.
    """
    watched, verif_bash, verif_skill = (
        _DEFAULT_WATCHED_PREFIXES, _DEFAULT_VERIF_BASH, _DEFAULT_VERIF_SKILL,
    )
    cfg = _read_config_dict()
    if cfg is not None:
        watched = _as_str_tuple(cfg.get("watched_prefixes"), watched)
        verif_bash = _as_str_tuple(cfg.get("verif_bash"), verif_bash)
        verif_skill = _as_str_tuple(cfg.get("verif_skill"), verif_skill)
    return watched, verif_bash, verif_skill


def _load_extra_config():
    """(dod_enabled, dispositif_prefixes, dispositif_tests) — les trois signaux
    ajoutés par la fusion du 2026-09-03, tous OPT-IN (désactivés par défaut).
    Fonction séparée de `_load_config()` pour ne jamais changer son arité
    (contrat testé sur VSCode3)."""
    dod_enabled = _DEFAULT_DOD_ENABLED
    disp_prefixes, disp_tests = _DEFAULT_DISPOSITIF_PREFIXES, _DEFAULT_DISPOSITIF_TESTS
    cfg = _read_config_dict()
    if cfg is not None:
        if isinstance(cfg.get("dod_enabled"), bool):
            dod_enabled = cfg["dod_enabled"]
        disp_prefixes = _as_str_tuple(cfg.get("dispositif_prefixes"), disp_prefixes)
        disp_tests = _as_str_tuple(cfg.get("dispositif_tests"), ())
    return dod_enabled, disp_prefixes, disp_tests


def _load_gardes_config():
    """(perimetre_enabled, plafond_lot) — les deux gardes du 2026-09-10, OPT-IN.
    Fonction séparée, encore, pour ne changer l'arité d'aucune des deux
    précédentes (contrat testé sur VSCode3)."""
    perimetre, plafond = _DEFAULT_PERIMETRE_ENABLED, _DEFAULT_PLAFOND_LOT
    cfg = _read_config_dict()
    if cfg is not None:
        if isinstance(cfg.get("perimetre_enabled"), bool):
            perimetre = cfg["perimetre_enabled"]
        valeur = cfg.get("plafond_lot")
        if isinstance(valeur, int) and not isinstance(valeur, bool) and valeur >= 0:
            plafond = valeur
    return perimetre, plafond


# Périmètre et preuves EFFECTIFS de ce dépôt : lus une fois au chargement du
# hook (chaque commit relance ce script comme process neuf, donc pas besoin
# de rechargement à chaud).
_WATCHED_PREFIXES, _VERIF_BASH, _VERIF_SKILL = _load_config()
_DOD_ENABLED, _DISPOSITIF_PREFIXES, _DISPOSITIF_TESTS = _load_extra_config()
_PERIMETRE_ENABLED, _PLAFOND_LOT = _load_gardes_config()

# Signaux de definition-of-done : la boucle DoD complète (skill), ou le run
# d'orchestration journalisé (où la DoD assumée se trace dans `notes`). Fixes
# et identiques partout — `revue-increment` et `log_run.py` sont le canon du
# hub, pas une particularité d'un dépôt.
_DOD_SKILL = ("revue-increment",)
_JOURNAL_BASH = ("log_run.py",)
# Échappatoire versionnée : DoD assumée explicitement dans le message de commit.
# Volontairement PAS « revue-increment » : un commit peut parler du skill
# lui-même (« Versionne le skill revue-increment ») — le citer suffirait à faire
# taire le garde-fou sans que la boucle ait tourné. Seul le mot DoD marque
# l'intention.
_DOD_MESSAGE_MARKERS = ("definition-of-done", "definition of done")
_DOD_MESSAGE_RE = re.compile(r"\bdod\b", re.IGNORECASE)

_GIT_OPTS_WITH_VALUE = ("-C", "-c", "--git-dir", "--work-tree", "--namespace")


def _git_commit_flags(segment):
    """-> liste des tokens d'un `git commit` réel, ou None si le segment n'en est pas un."""
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return None  # quotes déséquilibrées, substitution… — on ne devine pas
    if not tokens:
        return None
    start = 0
    while start < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[start]):
        start += 1  # saute les affectations VAR=value en tête
    if start >= len(tokens) or tokens[start].lower() != "git":
        return None
    rest = tokens[start + 1:]
    # Sous-commande = premier token non-option (en sautant -C/-c <val> globaux).
    i = 0
    sub = None
    while i < len(rest):
        t = rest[i]
        if t.startswith("-"):
            i += 2 if t in _GIT_OPTS_WITH_VALUE else 1
            continue
        sub = t
        break
    if sub != "commit":
        return None
    if "--dry-run" in rest:
        return None  # ne crée pas de commit
    return rest


def _commit_message(commit_flags):
    """-> message du commit reconstitué depuis les -m/--message (chaîne vide si aucun)."""
    parts = []
    i = 0
    while i < len(commit_flags):
        t = commit_flags[i]
        if t in ("-m", "--message"):
            if i + 1 < len(commit_flags):
                parts.append(commit_flags[i + 1])
                i += 2
                continue
        elif t.startswith("--message="):
            parts.append(t.split("=", 1)[1])
        elif t.startswith("-") and not t.startswith("--") and "m" in t:
            # options courtes groupées : -mwip, -am wip, -amwip
            after = t[t.index("m") + 1:]
            if after:
                parts.append(after)
            elif i + 1 < len(commit_flags):
                parts.append(commit_flags[i + 1])
                i += 2
                continue
        i += 1
    return "\n".join(parts)


def _dod_assumee(message):
    """True si le message de commit assume explicitement la definition-of-done."""
    low = (message or "").lower()
    return bool(_DOD_MESSAGE_RE.search(low) or any(m in low for m in _DOD_MESSAGE_MARKERS))


def _staged_files(cwd, commit_flags):
    """Tous les fichiers qui seront réellement commités (le filtrage par zone se
    fait chez l'appelant), ou None si indéterminable."""
    def _run(args):
        try:
            r = subprocess.run(
                ["git"] + args, cwd=cwd or None,
                capture_output=True, text=True, timeout=8,
                encoding="utf-8", errors="replace",
            )
        except Exception:
            return None
        if r.returncode != 0:
            return None
        return [ln.strip().replace("\\", "/") for ln in r.stdout.splitlines() if ln.strip()]

    files = _run(["diff", "--cached", "--name-only"])
    if files is None:
        return None
    # `git commit -a/--all` valide aussi les modifs de fichiers suivis non stagés :
    # les ajouter, sinon on manquerait le périmètre réel du commit.
    if any(f in ("-a", "--all") for f in commit_flags):
        unstaged = _run(["diff", "--name-only"])
        if unstaged:
            files = list(dict.fromkeys(files + unstaged))
    return files


def _iter_tool_uses(obj):
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if not isinstance(content, list):
        return
    for blk in content:
        if isinstance(blk, dict) and blk.get("type") == "tool_use":
            yield blk


def _session_signals(transcript_path, verif_bash=None, verif_skill=None, dispositif_tests=None):
    """-> {"verif", "dod", "journal", "dispositif"} (bool) d'après les VRAIES
    exécutions d'outils du transcript de session — une seule lecture pour les
    signaux. Les trois derniers paramètres défaultent aux valeurs CONFIGURÉES
    de ce dépôt (module-level) ; explicites uniquement pour des tests qui
    veulent isoler un canal."""
    if verif_bash is None:
        verif_bash = _VERIF_BASH
    if verif_skill is None:
        verif_skill = _VERIF_SKILL
    if dispositif_tests is None:
        dispositif_tests = _DISPOSITIF_TESTS

    sig = {"verif": False, "dod": False, "journal": False, "dispositif": False}
    if not transcript_path or not os.path.isfile(transcript_path):
        return sig
    try:
        with open(transcript_path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue  # préfiltre octet bon marché (cf. scan_transcripts.py)
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                for blk in _iter_tool_uses(obj):
                    name = blk.get("name")
                    inp = blk.get("input") or {}
                    # PowerShell est le shell PRIMAIRE de cet environnement : ne
                    # reconnaitre que Bash rendait le garde-fou aveugle a la majorite
                    # des verifications reellement lancees (faux negatif constate en
                    # production, run 2026-08-31T21:59). Les deux outils exposent la
                    # commande sous la meme cle `input.command`.
                    if name in ("Bash", "PowerShell"):
                        cmd = (inp.get("command") or "").lower()
                        if any(k in cmd for k in verif_bash):
                            sig["verif"] = True
                        if any(k in cmd for k in _JOURNAL_BASH):
                            sig["journal"] = True
                        if dispositif_tests and "pytest" in cmd:
                            cmd_norm = cmd.replace("\\", "/")
                            if any(t in cmd_norm for t in dispositif_tests):
                                sig["dispositif"] = True
                            elif "tests/" not in cmd_norm:  # suite complète : les inclut de fait
                                sig["dispositif"] = True
                    elif name == "Skill":
                        skill = (inp.get("skill") or "").lower()
                        if skill in verif_skill:
                            sig["verif"] = True
                        if skill in _DOD_SKILL:
                            sig["dod"] = True
                if sig["verif"] and sig["dod"] and sig["journal"] and (not dispositif_tests or sig["dispositif"]):
                    return sig
    except Exception:
        return {"verif": False, "dod": False, "journal": False, "dispositif": False}
    return sig


def _verif_ran(transcript_path):
    """True si une vraie exécution de vérif est présente dans le transcript de session."""
    return _session_signals(transcript_path)["verif"]


def _matched_prefixes(files, prefixes):
    """Sous-ensemble de `prefixes` réellement responsable du déclenchement, dans
    l'ordre de déclaration — pour nommer dans le message CE qui a matché, pas
    la configuration entière du projet."""
    return [p for p in prefixes if any(f.startswith(p) for f in files)]


def _zones_txt(prefixes):
    return ", ".join(f"`{p}`" for p in prefixes) if prefixes else "le périmètre surveillé"


def _build_warning(prefixes, verif_bash, verif_skill):
    """Message dérivé des constantes RÉELLES (config du dépôt cible) reçues en
    paramètre — jamais d'un canal figé en dur indépendant d'elles. Voir le
    docstring du module pour l'historique du défaut que ceci corrige."""
    zones = _zones_txt(prefixes)
    primary = verif_bash[0] if verif_bash else None
    autres = list(verif_bash[1:]) if verif_bash else []
    if primary:
        bash_txt = f"`{primary}`"
        if autres:
            bash_txt += " (ou " + " / ".join(f"`{c}`" for c in autres) + ")"
    else:
        bash_txt = "une exécution réelle de vérif"
    skills_txt = ""
    if verif_skill:
        skills_txt = " ou skill " + " ou ".join(f"`{s}`" for s in verif_skill)
    return (
        "⚠️ Vérif de fin d'incrément non détectée dans cette session : des "
        f"fichiers sous {zones} sont sur le point d'être commités sans trace "
        f"de {bash_txt} ni de rendu réel{skills_txt}. Lancer la vérif RÉELLE "
        "avant de committer le code applicatif, ou confirmer que c'est "
        "volontaire. (Garde-fou projet non bloquant — constat superviseur #1.)"
    )


def _build_warning_dod(prefixes):
    return (
        f"⚠️ Trace de definition-of-done absente : ce commit touche {_zones_txt(prefixes)} "
        "sans que `/revue-increment` ait tourné, sans run journalisé (`log_run.py`) et "
        "sans DoD assumée dans le message. Des tests verts ne valent PAS une "
        "definition-of-done. Trois sorties : lancer /revue-increment, journaliser le "
        "run d'orchestration, ou assumer explicitement la DoD allégée dans le message "
        "de commit (ex. « DoD allégée : tests verts, pas de rendu réel »). "
        "(Garde-fou projet non bloquant — constats superviseur #1 et #2 du 2026-07-28.)"
    )


def _build_warning_dispositif(prefixes, tests):
    return (
        f"⚠️ Commit touchant le dispositif ({_zones_txt(prefixes)}) sans trace des "
        f"fichiers-contrat cette session : lancer `pytest {' '.join(tests)} -q` avant "
        "de committer — un commit de sync canon sans test a déjà cassé une suite "
        "ailleurs dans la flotte (constat superviseur sync-canon du 2026-07-29). "
        "Garde-fou non bloquant."
    )


# --------------------------------------------------------------------------- #
# Garde « PÉRIMÈTRE » et plafond de lot (2026-09-10)
#
# Arbitrées par l'utilisateur sur le plan du superviseur, après une séance où
# QUATRE rondes de revue adversariale ont trouvé 3 bloquants et 13 majeurs dans
# des correctifs fraîchement écrits. Le superviseur a refusé d'en conclure
# « ajoutons une 5e ronde » : la revue avait parfaitement fonctionné, et c'est
# le problème — elle a fait, à 4x le prix et APRÈS coup, un travail mécanique
# qui coûtait un `grep` AVANT d'écrire.
#
# Les deux causes qu'il a nommées, chacune mesurée :
#
# 1. Le périmètre n'est jamais énuméré avant d'écrire. Forme commune de la
#    majorité des défauts du jour : « garde posée sur 1 chemin d'écriture sur
#    3 » ; « équivalence rompue dont 3 consommateurs en aval dépendaient » ;
#    « dernier résidu du message d'exception brut » démenti par 5 sites frères.
#    La leçon existait déjà en mémoire (« appliquer la leçon aux chemins
#    frères ») : elle est écrite et elle ne tient pas, parce qu'elle dépend
#    d'une vigilance et non d'une commande.
#
# 2. Le lot mélange les sujets, donc rien n'est revuable en un passage. Mesuré :
#    un commit de 16 fichiers / 1841 insertions portant 4 dimensions d'audit,
#    le lendemain d'un triage qui relevait déjà « commit non scopé (4 sujets) ».
#    Le profil 2 -> 1 -> 0 -> 0 bloquants ne dit pas « la revue a convergé », il
#    dit « le lot était trop gros pour être revu une fois ».
#
# Ces deux gardes sont NON BLOQUANTES, comme le reste de ce hook : elles
# avertissent. Un garde-fou qui empêche de livrer se fait débrancher la semaine
# suivante — mais un garde-fou muet ne sert à rien non plus, et c'est la leçon
# du hook voisin (`check_ci_after_push`, qui s'est tu pendant sept semaines).
# --------------------------------------------------------------------------- #

# Mots qui annoncent une EXHAUSTIVITÉ : celui qui les écrit affirme avoir
# regardé tous les sites. C'est précisément l'affirmation qui a été fausse deux
# fois dans la séance du 2026-09-10.
_MOTS_D_EXHAUSTIVITE = (
    "tous les", "toutes les", "dernier", "derniere", "dernière",
    "partout", "plus aucun", "plus aucune", "idempotent", "idempotente",
    "aucun autre", "aucune autre", "l'ensemble des", "chaque site",
)

# La ligne que la garde réclame. Volontairement une COMMANDE et un COMPTE : une
# phrase d'intention (« j'ai vérifié les autres appels ») ne prouve rien et ne
# se rejoue pas.
_LIGNE_PERIMETRE = "Périmètre:"


def _sans_accents(texte: str) -> str:
    """Comparaison INSENSIBLE aux accents.

    Les messages de commit de ce dépôt sont écrits sans accents (console
    Windows), et le premier jet de cette garde exigeait littéralement
    « Périmètre: » : elle aurait été inapplicable en pratique et se serait fait
    débrancher — le mode de défaillance que ce hook cherche justement à éviter.
    Trouvé par ses propres tests avant commit.
    """
    return unicodedata.normalize("NFKD", texte or "").encode(
        "ascii", "ignore").decode("ascii").lower()

_PLAFOND_FICHIERS_LOT = 6


def _diff_ajoute(cwd, commit_flags) -> str:
    """Les lignes AJOUTÉES par le commit en préparation. Chaîne vide si git est
    indéterminable — fail-open, comme le reste du hook.

    `encoding`/`errors` EXPLICITES, comme `_staged_files` : sans eux,
    `text=True` décode dans l'encodage de la console (cp1252 ici) et un diff
    portant un octet qu'elle ne définit pas — un emoji à sélecteur de variante,
    présent dans des centaines de fichiers de ce dépôt — rend `stdout = None`.
    Le `.splitlines()` levait alors HORS du try : le hook plantait et les TROIS
    avertissements préexistants partaient avec lui. Une garde neuve qui
    désactive le garde-fou qu'elle vient renforcer est pire que pas de garde
    (revue adversariale du 2026-09-10, bloquant B1, reproduit deux fois).

    `--all` : `git commit -a` met en scène les modifications suivies au moment
    du commit, que `--cached` ne voit pas encore. On lit alors `HEAD` pour
    rester cohérent avec `_staged_files`, qui unionne déjà le non-stagé.
    """
    args = ["git", "diff", "--cached", "--unified=0"]
    if commit_flags and any(f in commit_flags for f in ("-a", "--all", "-am")):
        args = ["git", "diff", "HEAD", "--unified=0"]
    try:
        r = subprocess.run(args, cwd=cwd or None, capture_output=True,
                           encoding="utf-8", errors="replace",
                           text=True, timeout=10)
    except Exception:
        return ""
    if r.returncode != 0 or r.stdout is None:
        return ""
    return chr(10).join(
        l for l in r.stdout.splitlines() if l.startswith("+") and not l.startswith("+++")
    )


# PAIRES (forme NON gardée, forme gardée, quoi chercher) — le cœur de la garde.
#
# Le défaut visé n'est pas « une garde a été posée » mais « une garde a été
# posée ICI pendant que ses frères restent NUS ». C'est la forme commune des
# 3 bloquants du 2026-09-10, et de deux affirmations d'exhaustivité fausses.
#
# Deux calibrages précédents ont été MESURÉS puis jetés, et c'est la mesure qui
# a tranché, pas l'intuition :
#   - « le diff ajoute un motif de garde » -> 62 % des commits `app/` récents,
#     et ZÉRO des trois cas que la garde citait nommément ;
#   - « … plus les mots d'exhaustivité cherchés dans le code » -> 70 %, parce
#     que « dernier », « toutes les », « plus aucun » sont du français courant
#     dans des commentaires.
# Une garde qui parle à deux commits sur trois ne se fait même pas débrancher :
# on cesse de la lire, et rien ne le signale.
#
# Ce calibrage-ci ne parle que si le compte est VÉRIFIABLE : la forme gardée
# apparaît dans les lignes ajoutées, et la forme nue subsiste ailleurs sous le
# périmètre surveillé. Le message donne alors les fichiers exacts — c'est-à-dire
# le `grep` qu'on aurait dû lancer avant d'écrire.
_PAIRES_DE_GARDE = (
    # (forme gardée ajoutée, forme NUE à compter, libellé pour le message)
    ("lire_upload_borne", "await file.read()", "lecture d'upload en mémoire"),
    ("lire_upload_audio_borne", "await file.read()", "lecture d'audio en mémoire"),
    ("ecrire_audio_borne", "shutil.copyfileobj", "écriture d'audio sur disque"),
    ("verifier_zip_borne", "zipfile.ZipFile", "ouverture d'archive zip"),
    ("logger.exception", "str(exc)", "message d'exception rendu au client"),
    ("on_conflict_do_nothing", "db.add(", "création concurrente (lire-puis-écrire)"),
)


def _sites_nus(cwd, forme_nue: str, prefixes) -> list[str]:
    """Les fichiers du périmètre surveillé qui portent ENCORE la forme nue.

    `git grep` plutôt qu'un parcours Python : il respecte `.gitignore`, ignore
    `.venv` et `__pycache__`, et coûte quelques millisecondes. Fail-open : une
    erreur rend une liste vide, donc pas d'avertissement — jamais de friction
    fabriquée par une panne d'outil.
    """
    try:
        r = subprocess.run(
            ["git", "grep", "-l", "--fixed-strings", forme_nue, "--", *prefixes],
            cwd=cwd or None, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
    except Exception:
        return []
    if r.returncode not in (0, 1) or not r.stdout:
        return []
    return [l for l in r.stdout.splitlines() if l.strip()]


def _freres_nus(cwd, diff_ajoute: str, fichiers_du_commit, prefixes) -> list[tuple]:
    """[(libellé, forme nue, sites portant ENCORE la forme nue)].

    Les fichiers touchés par le commit ne sont PAS exclus — ils sont signalés
    comme tels. La première version les retirait en bloc, ce qui rendait la
    garde aveugle au défaut exact qui l'a fait naître : « garde posée sur 1
    chemin d'écriture sur 3 », qui vit typiquement dans le MÊME fichier que la
    garde qu'on vient d'ajouter (revue adversariale du 2026-09-10, T3). La
    garde se taisait précisément là où on l'avait bâtie pour parler.

    Aucun risque de faux positif sur un fichier réellement traité : `git grep`
    lit l'arbre de travail, donc un site corrigé n'y figure déjà plus.
    """
    touches = set(fichiers_du_commit or ())
    trouves = []
    for forme_gardee, forme_nue, libelle in _PAIRES_DE_GARDE:
        if forme_gardee not in diff_ajoute:
            continue
        restants = [
            f + ("   <- dans CE commit" if f in touches else "")
            for f in _sites_nus(cwd, forme_nue, prefixes)
        ]
        if restants:
            trouves.append((libelle, forme_nue, restants))
    return trouves


def _build_warning_perimetre(freres) -> str:
    lignes = [
        "PÉRIMÈTRE — ce commit pose une garde, et sa forme NUE subsiste ailleurs :",
        "",
    ]
    for libelle, forme_nue, restants in freres:
        lignes.append(f"  • {libelle} : `{forme_nue}` encore présent dans")
        for f in restants[:4]:
            lignes.append(f"      {f}")
        if len(restants) > 4:
            lignes.append(f"      … et {len(restants) - 4} autre(s)")
    lignes += [
        "",
        "C'est la forme exacte des 3 bloquants du 2026-09-10 : une garde posée "
        "sur un chemin, ses frères oubliés — trouvés par quatre rondes de revue "
        "adversariale, à 4x le prix du `grep` ci-dessus.",
        "",
        "Deux sorties, l'une comme l'autre acceptable :",
        "  1. garder les frères dans ce commit ;",
        "  2. dire pourquoi pas, avec le compte réel, dans le MESSAGE DE "
        "COMMIT (seul endroit lu — pas un commentaire de code) :",
        f"     {_LIGNE_PERIMETRE} 3 sites, 1 gardé, 2 différés : <raison>",
        "",
        "Un périmètre partiel ASSUMÉ n'est pas un défaut ; un périmètre partiel "
        "qui s'annonce complet en est un.",
    ]
    return "\n".join(lignes)


def _build_warning_lot(fichiers_app, plafond) -> str:
    return (
        f"LOT TROP LARGE — {len(fichiers_app)} fichiers sous un périmètre "
        f"surveillé dans un seul commit (seuil : {plafond}).\n\n"
        "Mesuré le 2026-09-10 : un commit de 16 fichiers / 1841 insertions "
        "portant 4 sujets a demandé QUATRE rondes de revue adversariale, et le "
        "triage de la veille relevait déjà « commit non scopé (4 sujets) ». Le "
        "profil 2 → 1 → 0 → 0 bloquants ne dit pas que la revue a convergé : il "
        "dit que le lot était trop gros pour être revu en un passage. R2 demande "
        "un commit scopé au périmètre.\n\n"
        "Découper par SUJET (un correctif = un commit) rend chaque passage "
        "revuable, et un `git bisect` ultérieur utilisable. Si le lot est "
        "réellement indivisible — une migration et son appelant, par exemple — "
        "dis-le dans le message.\n"
        "Fichiers : " + ", ".join(sorted(fichiers_app)[:8])
        + (" …" if len(fichiers_app) > 8 else "")
    )


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    strip = _strip_heredocs or (lambda s: s)
    segs = _segments(cmd) if _segments else [cmd]
    try:
        cmd = strip(cmd)
        segs = _segments(cmd) if _segments else [cmd]
    except Exception:
        return  # fail-open

    commit_flags = None
    for seg in segs:
        commit_flags = _git_commit_flags(seg)
        if commit_flags is not None:
            break
    if commit_flags is None:
        return  # pas un git commit

    files = _staged_files(data.get("cwd"), commit_flags)
    if not files:
        return  # rien à committer (ou git indéterminable) — silence

    watched = [f for f in files if f.startswith(_WATCHED_PREFIXES)]
    watched_disp = ([f for f in files if f.startswith(_DISPOSITIF_PREFIXES)]
                     if _DISPOSITIF_TESTS else [])
    if not watched and not watched_disp:
        return  # rien sous un périmètre surveillé dans ce commit — silence

    sig = _session_signals(data.get("transcript_path"))

    avertissements = []
    if watched and not sig["verif"]:
        avertissements.append(_build_warning(_matched_prefixes(watched, _WATCHED_PREFIXES),
                                              _VERIF_BASH, _VERIF_SKILL))
    if _DOD_ENABLED and watched and not (
            sig["dod"] or sig["journal"] or _dod_assumee(_commit_message(commit_flags))):
        avertissements.append(_build_warning_dod(_matched_prefixes(watched, _WATCHED_PREFIXES)))
    if watched_disp and not sig["dispositif"]:
        avertissements.append(_build_warning_dispositif(
            _matched_prefixes(watched_disp, _DISPOSITIF_PREFIXES), _DISPOSITIF_TESTS))
    # Gardes du 2026-09-10 (voir le bloc au-dessus de `main`).
    message_commit = _commit_message(commit_flags)
    if watched:
        diff = _diff_ajoute(data.get("cwd"), commit_flags) if _PERIMETRE_ENABLED else ""
        # La porte de sortie est cherchée dans le MESSAGE DE COMMIT seul.
        # La première version la cherchait aussi dans tout le diff ajouté — et
        # le diff de cette garde ajoute la ligne `_LIGNE_PERIMETRE =
        # "Périmètre:"` : elle se désarmait donc elle-même, et avec elle tout
        # commit touchant ce fichier, ou n'importe quel fichier portant un
        # commentaire français « périmètre : » sans le moindre rapport
        # (revue adversariale du 2026-09-10, T2). Le message de commit est le
        # seul endroit que l'auteur écrit délibérément, commit par commit.
        ligne_presente = _sans_accents(_LIGNE_PERIMETRE) in _sans_accents(message_commit)
        if _PERIMETRE_ENABLED and not ligne_presente:
            freres = _freres_nus(data.get("cwd"), diff, files, _WATCHED_PREFIXES)
            if freres:
                avertissements.append(_build_warning_perimetre(freres))
        if _PLAFOND_LOT and len(watched) > _PLAFOND_LOT:
            avertissements.append(_build_warning_lot(watched, _PLAFOND_LOT))

    if not avertissements:
        return

    message = "\n\n".join(avertissements)
    print(json.dumps({
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        },
    }))


if __name__ == "__main__":
    main()
