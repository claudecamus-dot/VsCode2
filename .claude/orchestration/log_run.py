# +-- GÉNÉRÉ — NE PAS ÉDITER LOCALEMENT ---------------------------------------
# | Source de vérité : hub de supervision VScode5, .claude/dispositif/canon/log_run.py
# | Une correction faite ICI sera ÉCRASÉE à la prochaine propagation. Pour la
# | garder : la signaler au hub, qui corrige le canon et re-synchronise.
# | (Depuis le hub : « py .claude/dispositif/sync_dispositif.py » — ce script
# |  n'est pas déployé, il n'existe pas dans ce dépôt.)
# | Provenance canon : bb61f24 du 2026-09-18 — permet, au prochain sync, de dire si
# | une différence vient d'une édition locale ou d'une avance du canon (voir
# | `determiner_cause` dans sync_dispositif.py au hub).
# +---------------------------------------------------------------------------

"""Journal des orchestrations (étage O-A) — append d'un run dans runs.jsonl.

Usage : py .claude/orchestration/log_run.py '<json>'   (ou JSON sur stdin)
Champs requis : demande (str), qualification (orchestre|direct-signale).
Champs usuels : plan (liste d'étapes {etape, agent, mode, modele}), resultat
(en-cours|succes|en-attente-validation|partiel|echec), reprises (int), notes (str),
playbook (str|null : nom du playbook instancié, incrément O-B — null en composition
libre). `ts` est ajouté si absent.

Un run 'succes'/'orchestre' SANS étape revue-increment au plan ni trace dans notes
est REFUSÉ (rien n'est écrit) sauf champ `derogation_revue` (str, motif explicite —
cf. `verifier_revue_increment`, finding
`VScode5:revue-obligatoire-sautee-sept-runs-sur-dix`, 2026-09-04).

Chaque étape du plan accepte un champ OPTIONNEL `etat` valant `ok`, `echec` ou
`non-rendu` (veille « OrchestraBench », arXiv:2608.05263, adoptée le 2026-09-08).
Un run `succes` dont au moins une étape porte `echec` ou `non-rendu` est REFUSÉ en
nommant l'étape : un fan-out dont une branche a échoué n'est pas un succès entier,
et c'est exactement ce que l'étude mesure — l'échec d'un sous-agent dilué dans une
synthèse lissée. `partiel` et `echec` passent : ils DISENT l'échec. Une valeur hors
des trois BLOQUE un `succes` (sans quoi `"etat": "KO"` passerait pour inoffensif),
mais laisse passer un `echec`/`partiel` avec un avertissement — R5 exige que le run
raté soit journalisé, et le refuser sur une faute de frappe en perdrait la trace
(revue bmad-code-review, 2026-09-09). Le contrôle vaut à l'append ET au `--solde`.
Consommé à terme par le superviseur étage 2 (métrique « plan vs réel »).

JOURNALISER DÈS LA COMPOSITION DU PLAN, PAS À LA FIN (constat superviseur VSCode
2026-07-27 : runs.jsonl inexistant 4 jours après le déploiement du dispositif alors
que des enchaînements multi-étapes avaient bien eu lieu — journaliser en dernier
revient à ne rien journaliser dès que le run est interrompu, or c'est précisément
là que le signal vaut le plus). L'orchestrateur écrit donc la ligne à l'étape 2 avec
`"resultat": "en-cours"`, puis la solde à la remise. Un `en-cours` qui traîne est un
run abandonné : le scan le compte à part et ne le mêle pas aux taux de réussite.

Solde d'un run ouvert ou en attente (constat superviseur 2026-07-23 : la boucle
en-attente-validation ne se refermait jamais sans édition manuelle du journal) :

    py .claude/orchestration/log_run.py --solde <prefixe-ts> <resultat> "note"

Requalifie LE run dont le ts commence par <prefixe-ts> (erreur si 0 ou >1
correspondance) et trace la validation dans notes (`solde <date> : <note>`).

CAPACITÉ OPT-IN — « solde sous revue » (`solde_revue_requise`, ÉTEINTE PARTOUT
par défaut). Un projet peut exiger que le solde d'un run dont le plan touche SA
zone surveillée porte une note commençant par « revue: » — la trace, dans le
journal, que la boucle de revue a bien eu lieu avant de déclarer le run soldé.
Elle se déclare dans la configuration LOCALE du dépôt,
`<repo>/.claude/warn_verif_before_commit.json` (le même fichier que le hook
`warn_verif_before_commit.py`, dont ce mécanisme reprend exactement la
mécanique : `_DEFAULT_* = False`, lecture fail-open, activation projet par
projet, jamais de nouveau signal hérité sans déclaration explicite) :

    {"watched_prefixes": ["src/", "server.js"], "solde_revue_requise": true}

Sans ce fichier, sans la clé, avec la clé à `false`, ou avec un
`watched_prefixes` vide : comportement inchangé, aucun refus possible. Activée,
elle REFUSE (code 1) le solde d'un run dont la `demande` ou le `plan` nomme un
des préfixes surveillés tant que la note ne commence pas par « revue: ». La
détection est textuelle (le plan cite le chemin ou non) : c'est un garde-fou, pas
une preuve — un plan qui ne nomme aucun chemin n'est pas vu. Un run soldé `echec`
est concerné comme les autres : la note « revue: abandonné, rien livré » suffit.
"""
import datetime
import json
import os
import sys

# Windows : la console par défaut est cp1252 — un message avec tiret cadratin ou
# un JSON accenté sur stdin passerait en mojibake (ou casserait un lecteur UTF-8).
# stdin en utf-8-sig : un pipe PowerShell 5.1 ('...' | py log_run.py) préfixe un
# BOM qui casserait json.loads (vécu 2026-07-23) ; sans BOM, utf-8-sig == utf-8.
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8-sig")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RUNS_PATH = os.environ.get("AGENT_ORCHESTRATION_RUNS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "runs.jsonl"
)
QUALIFICATIONS = ("orchestre", "direct-signale")
# Un run ouvert à la composition vaut « en-cours » ; il se solde ensuite vers l'un des
# états terminaux, dont « en-attente-validation » — état par défaut d'un livrable que
# l'utilisateur doit approuver, et qui manquait ici (la skill l'exige pourtant, il
# n'était donc atteignable qu'en éditant le journal à la main).
RESULTATS_SOLDE = ("succes", "en-attente-validation", "partiel", "echec")
# A l'APPEND, « en-cours » s'ajoute aux etats terminaux. Sans cette liste, `resultat`
# n'etait valide qu'au solde : « succès » (accent, faute de frappe naturelle en
# francais) et « nimportequoi » entraient dans le journal en exit 0, faussaient le
# taux de reussite et echappaient au controle « en-attente-validation » (reproduit
# le 2026-08-31).
RESULTATS_APPEND = ("en-cours",) + RESULTATS_SOLDE
# Vocabulaire FERMÉ de l'état d'une étape du plan. Fermé et non libre : la moitié de
# l'intérêt du champ est qu'un `"etat": "KO"` — écrit de bonne foi — ne passe PAS pour
# un état inoffensif à côté d'un `resultat: succes`.
ETATS_ETAPE = ("ok", "echec", "non-rendu")
ETATS_ETAPE_FAUTIFS = ("echec", "non-rendu")

# --- « Solde sous revue » : capacité OPT-IN, ÉTEINTE PARTOUT par défaut ------
# Mécanique reprise TELLE QUELLE de `.claude/hooks/warn_verif_before_commit.py`
# (`_DEFAULT_DOD_ENABLED = False` + clé lue dans la config JSON locale du dépôt) :
# le canon est propagé à 6 dépôts, un signal nouveau ne doit JAMAIS s'y allumer
# par héritage. Chaque projet active ce qu'il a explicitement déclaré, dans SON
# fichier — aucun autre n'en hérite. Le fichier est le MÊME que celui du hook :
# la zone surveillée d'un projet n'a pas à être décrite deux fois, et une zone
# décrite deux fois finit par diverger.
_CONFIG_FILENAME = "warn_verif_before_commit.json"
_DEFAULT_SOLDE_REVUE_REQUISE = False   # opt-in : aucun projet ne l'a par défaut
# Motif attendu en tête de note. « revue: » et pas « revue-increment » : un solde
# peut tracer une revue de campagne, une revue humaine ou la skill — c'est
# l'intention de revue qui est exigée, pas un outil précis.
_MOTIF_NOTE_REVUE = "revue:"


def _config_path() -> str:
    """`<repo>/.claude/warn_verif_before_commit.json`, dérivé de l'emplacement de
    CE fichier (`<repo>/.claude/orchestration/log_run.py`) — jamais du cwd, un
    solde lancé depuis un sous-dossier doit lire la même configuration."""
    return os.environ.get("AGENT_ORCHESTRATION_CONFIG") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _CONFIG_FILENAME
    )


def config_solde_revue():
    """(requise, prefixes_surveilles) pour CE dépôt.

    Fail-open intégral, comme `_read_config_dict()` du hook : fichier absent,
    illisible, JSON invalide, mauvais type → capacité éteinte, aucun refus
    possible. Un dispositif de journalisation ne se met pas en travers du
    journal parce qu'un fichier de configuration est abîmé."""
    requise, prefixes = _DEFAULT_SOLDE_REVUE_REQUISE, ()
    try:
        with open(_config_path(), encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        return requise, prefixes
    if not isinstance(cfg, dict):
        return requise, prefixes
    if isinstance(cfg.get("solde_revue_requise"), bool):
        requise = cfg["solde_revue_requise"]
    brut = cfg.get("watched_prefixes")
    if isinstance(brut, list):
        prefixes = tuple(p for p in brut if isinstance(p, str) and p)
    return requise, prefixes


def zones_touchees(run: dict, prefixes) -> list:
    """Les préfixes surveillés que la `demande` ou le `plan` du run NOMMENT.

    Détection textuelle assumée : `runs.jsonl` ne porte pas la liste des fichiers
    touchés, seulement une demande et des étapes en prose. Un plan qui cite
    `comop-pptx-prototype/src/` est vu, un plan qui dit « corriger le générateur »
    ne l'est pas. C'est donc un rappel, jamais une preuve de couverture — le
    docstring du module le dit au lecteur qui active la capacité."""
    if not prefixes:
        return []
    morceaux = [str(run.get("demande") or "")]
    for e in run.get("plan") or []:
        if isinstance(e, dict):
            morceaux.append(str(e.get("etape", "")) + " " + str(e.get("agent", "")))
    texte = " ".join(morceaux).lower()
    return [p for p in prefixes if p.lower() in texte]


def verifier_note_de_revue(run: dict, note: str) -> str | None:
    """Message de refus si ce dépôt exige une note « revue: » et que le plan
    touche sa zone surveillée, sinon None (cas de tous les projets par défaut)."""
    requise, prefixes = config_solde_revue()
    if not requise:
        return None
    touchees = zones_touchees(run, prefixes)
    if not touchees:
        return None
    if str(note or "").strip().lower().startswith(_MOTIF_NOTE_REVUE):
        return None
    return (
        "log_run --solde REFUS : ce depot exige une note de revue pour solder un "
        "run dont le plan touche sa zone surveillee "
        f"({', '.join(touchees)}) — `solde_revue_requise: true` dans "
        f".claude/{_CONFIG_FILENAME}.\n"
        f"  Rejouer avec une note commencant par '{_MOTIF_NOTE_REVUE}', qui dit "
        "ce qui a ete revu et comment (ex. \"revue: smoke-test rejoue + rendu "
        "PowerPoint inspecte\"). Un run abandonne se solde de meme : "
        "\"revue: abandonne, rien livre\"."
    )


def solder(argv) -> int:
    """--solde <prefixe-ts> <resultat> [note] — requalifie un run existant.

    Si le dépôt a activé `solde_revue_requise` (opt-in, cf. docstring du module),
    la note doit commencer par « revue: » dès que le plan du run nomme un de ses
    `watched_prefixes` — sinon le solde est refusé, le journal reste intact."""
    if len(argv) < 2:
        print(f"log_run --solde : usage : --solde <prefixe-ts> <{'|'.join(RESULTATS_SOLDE)}> [note]")
        return 1
    prefixe, resultat = argv[0], argv[1]
    note = argv[2] if len(argv) > 2 else "valide par l'utilisateur"
    if resultat not in RESULTATS_SOLDE:
        print(f"log_run --solde : resultat attendu : {' | '.join(RESULTATS_SOLDE)}")
        return 1
    try:
        with open(RUNS_PATH, encoding="utf-8") as fh:
            runs = [json.loads(l) for l in fh if l.strip()]
    except (OSError, ValueError) as exc:
        print(f"log_run --solde : lecture impossible ({exc})")
        return 1
    cibles = [r for r in runs if str(r.get("ts", "")).startswith(prefixe)]
    if len(cibles) != 1:
        print(f"log_run --solde : {len(cibles)} run(s) pour le prefixe '{prefixe}' — il en faut exactement 1")
        for r in cibles:
            # `or ''` : un run a demande null faisait planter en TypeError la
            # branche meme qui doit servir a desambiguiser (reproduit 2026-08-31).
            print(f"  - {r.get('ts')} | {str(r.get('demande') or '')[:60]}")
        return 1
    run = cibles[0]
    # Contrôle AVANT toute mutation : un refus laisse le journal exactement dans
    # l'état où il était (aucune réécriture, aucun `notes` allongé d'un solde qui
    # n'a pas eu lieu).
    refus = verifier_note_de_revue(run, note)
    if refus:
        print(refus)
        return 1
    # « Une etape en echec interdit le succes » s'appliquait au seul APPEND : `main()`
    # route `--solde` vers `solder()` et retourne AVANT `verifier_etapes_du_plan`. Un
    # run ouvert `en-cours` avec une branche `echec` se requalifiait donc `succes` par
    # la porte de derriere — le garde-fou du 2026-09-08 contourne d'une commande
    # (revue bmad-code-review, 2026-09-09). Controle sur une COPIE portant le resultat
    # vise, comme a l'append, et toujours avant toute mutation.
    refus_etapes = verifier_etapes_du_plan({**run, "resultat": resultat})
    if refus_etapes:
        print(refus_etapes)
        return 1
    avant = run.get("resultat")
    run["resultat"] = resultat
    date = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    # `or ""` et non `get("notes", "")` : le journal porte des runs a `"notes": null`,
    # dont le solde ecrivait litteralement « None | solde ... » (meme motif que le
    # `or ''` de la desambiguisation ci-dessus, revue bmad-code-review 2026-09-09).
    run["notes"] = (str(run.get("notes") or "") + f" | solde {date} : {note}").strip(" |")
    # Ecriture atomique (meme convention que .claude/supervision/scan_transcripts.py) : "w" direct sur
    # RUNS_PATH tronque les 94 Ko du journal a mi-parcours si l'ecriture est interrompue
    # (Ctrl-C, coupure, disque plein). Le temporaire vit dans le meme repertoire pour
    # que os.replace reste atomique (meme volume, Windows comme POSIX).
    tmp = RUNS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in runs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, RUNS_PATH)
    print(f"log_run --solde : run {run.get('ts')} requalifie {avant} -> {resultat}")
    return 0


def verifier_etapes_du_plan(run: dict) -> str | None:
    """Refus si le plan contredit le `resultat` — sinon None.

    Veille « OrchestraBench » (arXiv:2608.05263), adoptée le 2026-09-08 : les
    orchestrateurs évalués diluent l'échec d'un sous-agent dans une synthèse lissée,
    et le plan final ne dit plus qu'une branche n'a rien rendu. Le seul point
    DÉTERMINISTE du dispositif étant le journal, c'est ici que le contrôle tient :
    un `succes` dont une étape porte `echec` ou `non-rendu` ne s'écrit pas.

    Le message NOMME l'étape (rang, libellé, agent). « une étape a échoué » ferait
    exactement ce que la trouvaille reproche : dire l'échec sans dire lequel.
    Le champ reste OPTIONNEL — les runs déjà journalisés n'en portent aucun, et une
    étape mal formée (non-dict) est ignorée plutôt que transformée en TypeError."""
    fautives, inconnues = [], []
    for rang, etape in enumerate(run.get("plan") or [], start=1):
        if not isinstance(etape, dict) or "etat" not in etape:
            continue
        etat = etape.get("etat")
        libelle = f"etape {rang} ('{etape.get('etape', '')}', agent '{etape.get('agent', '')}')"
        if etat not in ETATS_ETAPE:
            inconnues.append(f"{libelle} : etat {etat!r}")
        elif etat in ETATS_ETAPE_FAUTIFS:
            fautives.append(f"{libelle} : etat '{etat}'")
    if inconnues:
        message = (
            "etat d'etape hors vocabulaire -\n  "
            + "\n  ".join(inconnues)
            + f"\n  Attendu : {' | '.join(ETATS_ETAPE)} (champ optionnel : une etape "
              "sans 'etat' reste acceptee)."
        )
        # R5 (« verite du journal ») prime sur la police du vocabulaire : un `etat`
        # mal orthographie faisait REFUSER l'ecriture d'un run `echec`/`partiel`,
        # c'est-a-dire perdre la trace du run rate — precisement ce que R5 rend
        # obligatoire de journaliser. Seul un `succes` reste bloque : c'est lui que
        # `"etat": "KO"` rendrait faussement inoffensif (revue bmad-code-review,
        # 2026-09-09).
        if run.get("resultat") == "succes":
            return "log_run REFUS : " + message
        print("log_run AVERTISSEMENT : " + message)
    if run.get("resultat") == "succes" and fautives:
        return (
            "log_run REFUS : resultat 'succes' alors que le plan porte une etape en "
            "echec -\n  "
            + "\n  ".join(fautives)
            + "\n  Un fan-out dont une branche a echoue n'est pas un succes entier. "
              "Journaliser 'partiel' (ce qui a ete rendu) ou 'echec', et dire dans "
              "notes ce que l'etape n'a pas rendu - pas de synthese lissee."
        )
    return None


def main(argv) -> int:
    if argv and argv[0] == "--solde":
        return solder(argv[1:])
    raw = argv[0] if argv else sys.stdin.read()
    try:
        run = json.loads(raw)
    except ValueError as exc:
        print(f"log_run : JSON invalide ({exc})")
        return 1
    if not isinstance(run, dict):
        print("log_run : un objet JSON est attendu")
        return 1
    missing = [k for k in ("demande", "qualification") if not run.get(k)]
    if missing:
        print(f"log_run : champ(s) requis manquant(s) : {', '.join(missing)}")
        return 1
    if run["qualification"] not in QUALIFICATIONS:
        print(f"log_run : qualification invalide (attendu : {' | '.join(QUALIFICATIONS)})")
        return 1
    # `resultat` ABSENT reste accepte (un run peut s'ouvrir sans) ; present, il doit
    # etre l'un des etats connus — sinon le journal accumule des valeurs qu'aucun
    # calcul de taux ne sait lire.
    if "resultat" in run and run["resultat"] not in RESULTATS_APPEND:
        print(f"log_run : resultat invalide ({run['resultat']!r}) — attendu : "
              f"{' | '.join(RESULTATS_APPEND)}")
        return 1
    # AVANT `verifier_revue_increment` : un plan qui se contredit lui-meme doit etre
    # signale pour ce qu'il est, pas renvoye vers la boucle de revue.
    refus_etapes = verifier_etapes_du_plan(run)
    if refus_etapes:
        print(refus_etapes)
        return 1
    refus_revue = verifier_revue_increment(run)
    if refus_revue:
        print(refus_revue)
        return 1
    run.setdefault("ts", datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
    with open(RUNS_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(run, ensure_ascii=False) + "\n")
    print(f"log_run : run journalise ({run['qualification']}, {len(run.get('plan', []))} etape(s))")
    avertir_validation_utilisateur(run)
    return 0


# Marqueurs d'un livrable CONSOMMÉ par l'utilisateur (deck exporté, écran) et
# d'une validation utilisateur explicite dans les notes. Diagnostic superviseur
# 2026-07-23 (arbitré) : 0/47 runs « en-attente-validation » alors que la règle
# l'exigeait — le garde-fou devient exécutable, en avertissement NON bloquant.
LIVRABLE_UTILISATEUR = ("deck", "slide", "pptx", "ecran", "écran", "export")
VALIDATION_UTILISATEUR = ("valide par l'utilisateur", "validé par l'utilisateur",
                          "valide par utilisateur", "ok utilisateur")


def verifier_revue_increment(run: dict) -> str | None:
    """REFUS franchissable par motif explicite (finding
    `VScode5:revue-obligatoire-sautee-sept-runs-sur-dix`, 2026-09-04) : un run
    orchestré journalisé `succes` doit porter une étape terminale revue-increment
    dans son plan, sa trace dans les notes (revue de campagne couvrant plusieurs
    runs), OU une dérogation motivée explicite (champ `derogation_revue`).

    Remplace l'ancien `avertir_revue_increment`, un simple print() NON bloquant
    (finding playbook:evolution-flotte, 2026-07-29) dont l'avertissement se
    déclenchait sur 51 des 74 runs éligibles (68 %, mesuré 2026-09-04) sans
    JAMAIS bloquer une seule fois en 43 jours de service — la mesure promise par
    son propre commentaire d'origine (« on mesure d'abord … avant tout garde-fou
    dur ») a été faite, et le résultat est qu'un avertissement ignorable 51 fois
    de suite n'est plus un garde-fou, c'est du bruit. Retourne le message de
    refus si aucune des trois échappatoires n'est présente, sinon None (le run
    s'écrit normalement)."""
    if run.get("resultat") != "succes" or run.get("qualification") != "orchestre":
        return None
    plan = run.get("plan") or []
    etapes = " ".join(str(e.get("etape", "")) + " " + str(e.get("agent", ""))
                      for e in plan if isinstance(e, dict)).lower()
    notes = str(run.get("notes", "")).lower()
    if "revue-increment" in etapes or "revue-increment" in notes:
        return None
    if str(run.get("derogation_revue") or "").strip():
        return None
    return (
        "log_run REFUS : run 'succes' orchestre sans etape terminale "
        "revue-increment (ni trace dans notes, ni derogation motivee) — la "
        "boucle de revue de fin d'increment est obligatoire depuis le "
        "2026-07-29. Rejouer avec soit une etape revue-increment au plan, soit "
        "une mention 'revue-increment' dans notes (revue de campagne couvrant "
        "plusieurs runs de la seance), soit le champ "
        "'derogation_revue': '<raison explicite>' si aucune des deux ne "
        "s'applique."
    )


def avertir_validation_utilisateur(run: dict) -> None:
    if run.get("resultat") != "succes":
        return
    texte = " ".join(str(run.get(k, "")) for k in ("demande", "notes")).lower()
    if any(m in texte for m in LIVRABLE_UTILISATEUR) and not any(
        v in texte for v in VALIDATION_UTILISATEUR
    ):
        print(
            "log_run AVERTISSEMENT : livrable utilisateur detecte sans mention de "
            "validation — « en-attente-validation » est le statut attendu tant que "
            "l'utilisateur n'a pas valide l'artefact exact (sinon, noter « valide "
            "par l'utilisateur » dans notes)."
        )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
