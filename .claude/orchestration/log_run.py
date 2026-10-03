# +-- GÉNÉRÉ — NE PAS ÉDITER LOCALEMENT ---------------------------------------
# | Source de vérité : hub de supervision VScode5, .claude/dispositif/canon/log_run.py
# | Une correction faite ICI sera ÉCRASÉE à la prochaine propagation. Pour la
# | garder : la signaler au hub, qui corrige le canon et re-synchronise.
# | (Depuis le hub : « py .claude/dispositif/sync_dispositif.py » — ce script
# |  n'est pas déployé, il n'existe pas dans ce dépôt.)
# | Provenance canon : 2659606 du 2026-10-03 — permet, au prochain sync, de dire si
# | une différence vient d'une édition locale ou d'une avance du canon (voir
# | `determiner_cause` dans sync_dispositif.py au hub).
# +---------------------------------------------------------------------------

"""Journal des orchestrations (étage O-A) — append d'un run dans runs.jsonl.

Usage : py .claude/orchestration/log_run.py '<json>'   (ou JSON sur stdin)
Lecture seule : `--stats [--champ X] [--depot Y]` compte, `--lister <champ>
<valeur>` DÉTAILLE (numéro de ligne, ts, notes intégrales) — c'est par là qu'on
voit le stock de runs `partiel` jamais relus, invisible d'un compteur.
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

VALIDATION UTILISATEUR — deux pieces indissociables (salle `inspection-critique`
du 2026-09-19, arbitree). Tout run porte un champ OBLIGATOIRE
`livrable_utilisateur` (booleen) + une justification courte
(`livrable_utilisateur_motif`, exigee quand il vaut `false`). A `true`, le run
`succes` doit porter une quittance NOMMEE
`validation: {par, artefact_ouvert, quand}` : `par` = UN NOM DE PERSONNE,
`artefact_ouvert` = un chemin/URL/capture NON NULLABLE, `quand` = horodatage.
Quatre refus mecaniques (cf. `verifier_validation_utilisateur`) : champ absent ;
`true` sans `validation` ; `artefact_ouvert` vide meme si `par` est rempli ;
`par` portant une identite d'AGENT (veille « Lost in Simulation », adoptee le
2026-09-19). NON RETROACTIF : au `--solde`, un run
qui ne porte pas le champ (les 194 d'avant le deploiement) n'est pas controle.

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
import math
import os
import re
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

# Vocabulaire FERME de la FORME de la tache d'un run -- veille du 2026-09-20,
# « Lois de scaling conditionnelles du multi-agent ». [S5] (arXiv 2512.08296,
# Google, PREPRINT, et juge et partie) mesure sur 260 configurations un ecart de
# +80,8 % (tache decomposable) a -70 % (tache sequentielle) : l'effet du
# multi-agent suit la FORME de la tache, pas le NOMBRE d'agents. Un plafond
# « <= 4 salles » borne donc la mauvaise variable. Le champ est DECLARE et non
# deduit : typer la forme depuis l'etage deterministe est hors de portee, et une
# heuristique textuelle en ferait un champ decoratif (meme raisonnement que
# `livrable_utilisateur`, qui a DEJA ete arbitre en declare).
#
# NON RETROACTIF, et c'est le point : absent = accepte en silence. Les 197 runs
# deja journalises n'en portent aucun, et les requalifier a posteriori
# fabriquerait exactement la donnee que l'ablation mono-agent doit aller
# chercher. Ce champ ne PROUVE rien ; il rend l'ablation stratifiable, c'est-a-dire
# possible (salle `anti-consensus` du 2026-09-20 : un lot heterogene rendrait UN
# chiffre generalise a tort sur une plage de variance de 150 points).
FORMES_TACHE = ("decomposable", "sequentiel", "inconnu")
CHAMP_FORME = "forme_tache"
CHAMP_FORME_MOTIF = "forme_tache_motif"
# Seule `decomposable` exige un motif ecrit : c'est la valeur qui ACHETE le
# fan-out. `sequentiel` et `inconnu` n'autorisent rien, donc ne justifient rien --
# et exiger un motif pour les trois rendrait le champ couteux a renseigner
# honnetement, donc renseigne a `decomposable` par reflexe. La contrainte est
# placee la ou elle coute quelque chose.
FORME_A_JUSTIFIER = "decomposable"

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


def deja_solde(run: dict) -> bool:
    """Vrai si le run a deja ete solde. Champ structure `solde` d'abord (ecrit
    depuis 2026-09-27) ; sinon repli textuel pour les runs historiques : un
    segment de `notes` (decoupe sur " | ") qui commence par "solde " — couvre la
    note d'un run a notes vides, ecrite sans pipe par `.strip(" |")`.
    `validation` n'est PAS un marqueur (il s'ecrit aussi a l'append) et
    `demande` n'est jamais lue."""
    solde = run.get("solde")
    if isinstance(solde, dict) and solde:
        return True
    notes = str(run.get("notes") or "")
    # Format EXACT ecrit par `solder()` (« solde <date ISO> : ») : un segment
    # « solde faux : ... » ne suffit plus a forger un solde (P3, 2026-09-28).
    return any(_SEGMENT_SOLDE.match(seg.strip()) for seg in notes.split(" | "))


_SEGMENT_SOLDE = re.compile(r"solde \d{4}-\d{2}-\d{2}")


def verifier_re_solde(run: dict, re_solder: bool) -> str | None:
    """Un run deja solde ne se re-solde que par geste delibere (`--re-solder`),
    y compris un `partiel` deja note. Appelee avant toute mutation."""
    if not deja_solde(run) or re_solder:
        return None
    solde = run.get("solde") if isinstance(run.get("solde"), dict) else {}
    quand = solde.get("quand")
    if not quand:
        segs = [s.strip() for s in str(run.get("notes") or "").split(" | ")
                if s.strip().startswith("solde ")]
        quand = segs[-1][len("solde "):].split(" : ", 1)[0] if segs else "?"
    anterieur = solde.get("resultat") or run.get("resultat")
    ident = run.get("ts") or str(run.get("demande") or "")[:60]
    return (f"log_run REFUS : run {ident} deja solde le {quand} ({anterieur} -> ...)"
            " - relance avec --re-solder si le re-soldage est un geste delibere")


def solder(argv) -> int:
    """--solde <prefixe-ts> <resultat> [note] — requalifie un run existant.

    Si le dépôt a activé `solde_revue_requise` (opt-in, cf. docstring du module),
    la note doit commencer par « revue: » dès que le plan du run nomme un de ses
    `watched_prefixes` — sinon le solde est refusé, le journal reste intact.

    `--par <nom> --artefact <chemin|url>` (2026-09-23) : pose la quittance NOMMEE
    qu'exige un run `livrable_utilisateur: true` pour passer `succes`. Sans ces
    options, un tel run ne pouvait JAMAIS etre solde en succes par la CLI, meme
    valide par l'utilisateur (constate sur VSCode3). Les controles de
    `verifier_validation_utilisateur` s'appliquent au bloc ainsi construit.

    `--demande <fragment>` (2026-09-27) : selectionne le run par un fragment de
    sa `demande` au lieu du prefixe de `ts`. Motif mesure : deux runs du journal
    du hub portent `ts: null`, donc `str(r.get("ts","")).startswith(prefixe)`
    matche les DEUX sur « None » et le solde est refuse a jamais. Un INDEX de
    ligne aurait ete un identifiant muet dans un journal opposable (R5) ; le
    fragment dit de quel run on parle. Le selecteur ne contourne RIEN : le run
    choisi passe par les memes quatre gardes, et `len(cibles) != 1` reste un
    refus."""
    options = {}
    positionnels = []
    i = 0
    while i < len(argv):
        if argv[i] in ("--par", "--artefact", "--demande") and i + 1 < len(argv):
            options[argv[i][2:]] = argv[i + 1]
            i += 2
        elif argv[i] == "--re-solder":
            options["re-solder"] = True
            i += 1
        elif argv[i] == "--demande":
            print("log_run --solde : --demande attend un fragment de demande (valeur absente)")
            return 1
        else:
            positionnels.append(argv[i])
            i += 1
    argv = positionnels
    # None = flag absent ; "" / blanc = flag present mais vide -> refus explicite,
    # jamais un repli silencieux en mode prefixe (positionnels decales).
    fragment = options.get("demande")
    if fragment is not None and not fragment.strip():
        print("log_run --solde : --demande vide refuse (fragment de demande requis)")
        return 1
    if len(argv) < (1 if fragment is not None else 2):
        print(f"log_run --solde : usage : --solde <prefixe-ts> <{'|'.join(RESULTATS_SOLDE)}> [note]"
              " [--par <nom> --artefact <chemin>] [--re-solder]\n"
              f"  ou, pour un run sans ts : --solde --demande <fragment> <{'|'.join(RESULTATS_SOLDE)}> [note]")
        return 1
    if fragment is not None:
        prefixe, resultat = None, argv[0]
        note = argv[1] if len(argv) > 1 else "valide par l'utilisateur"
    else:
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
    if fragment is not None:
        cibles = [r for r in runs
                  if fragment.lower() in str(r.get("demande") or "").lower()]
        critere = f"fragment de demande '{fragment}'"
    else:
        cibles = [r for r in runs if str(r.get("ts", "")).startswith(prefixe)]
        critere = f"prefixe '{prefixe}'"
    if len(cibles) != 1:
        print(f"log_run --solde : {len(cibles)} run(s) pour le {critere} — il en faut exactement 1")
        for r in cibles:
            # `or ''` : un run a demande null faisait planter en TypeError la
            # branche meme qui doit servir a desambiguiser (reproduit 2026-08-31).
            print(f"  - {r.get('ts')} | {str(r.get('demande') or '')[:60]}")
        return 1
    run = cibles[0]
    # Contrôle AVANT toute mutation : un refus laisse le journal exactement dans
    # l'état où il était (aucune réécriture, aucun `notes` allongé d'un solde qui
    # n'a pas eu lieu).
    refus_re_solde = verifier_re_solde(run, bool(options.get("re-solder")))
    if refus_re_solde:
        print(refus_re_solde)
        return 1
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
    refus_forme = verifier_forme_tache({**run, "resultat": resultat})
    if refus_forme:
        print(refus_forme)
        return 1
    refus_gt = verifier_gabarit_topologie(run)
    if refus_gt:
        print(refus_gt)
        return 1
    # NON RETROACTIF : `au_solde=True` fait sortir sans rien controler tout run
    # qui ne PORTE PAS `livrable_utilisateur` — c'est-a-dire les 194 runs ecrits
    # avant le deploiement du champ. Un run qui le porte a ete ecrit apres, et
    # reste controle : sans cela, `--solde` serait la porte de derriere du
    # garde-fou, exactement comme `verifier_etapes_du_plan` l'a ete jusqu'au
    # 2026-09-09.
    if options.get("par") or options.get("artefact"):
        run = {**run}
        runs[runs.index(cibles[0])] = run
        run["validation"] = {
            "par": options.get("par", ""),
            "artefact_ouvert": options.get("artefact", ""),
            "quand": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "rapport": note,
        }
    refus_validation = verifier_validation_utilisateur(
        {**run, "resultat": resultat}, au_solde=True)
    if refus_validation:
        print(refus_validation)
        return 1
    # Lot nominatif : controle a l'append ET au solde, comme les etapes du plan
    # (le solde a deja servi de porte de derriere a un garde-fou, 2026-09-09).
    refus_lot = verifier_lot({**run, "resultat": resultat})
    if refus_lot:
        print(refus_lot)
        return 1
    avant = run.get("resultat")
    run["resultat"] = resultat
    date = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    # `or ""` et non `get("notes", "")` : le journal porte des runs a `"notes": null`,
    # dont le solde ecrivait litteralement « None | solde ... » (meme motif que le
    # `or ''` de la desambiguisation ci-dessus, revue bmad-code-review 2026-09-09).
    run["notes"] = (str(run.get("notes") or "") + f" | solde {date} : {note}").strip(" |")
    # Champ structure (2026-09-27) : lu en premier par `deja_solde`, le texte
    # ci-dessus reste pour les humains.
    entree = {"quand": date, "avant": avant, "resultat": resultat, "note": note}
    # Historique (2026-09-28) : `solde` (dict) ne garde que le DERNIER solde et
    # reste lu tel quel par les lecteurs existants ; `soldes` les empile tous,
    # chacun disant s'il a ete force par `--re-solder`. Une ligne ancienne qui
    # ne porte que le dict l'amorce, pour ne pas perdre le solde anterieur.
    soldes = run.get("soldes") if isinstance(run.get("soldes"), list) else []
    if not soldes and isinstance(run.get("solde"), dict) and run["solde"]:
        soldes = [{**run["solde"], "re_solder": False}]
    run["soldes"] = soldes + [{**entree, "re_solder": bool(options.get("re-solder"))}]
    run["solde"] = entree
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


# --- Champs optionnels de l'optimiseur (chantier `optimiser`, 2026-10-03) -------
# Tous OPTIONNELS : absent = accepte (non retroactif, `--solde` compris) ; present
# mais invalide = refus qui liste les valeurs permises. Ils alimentent
# `optimiseur.py` (cellule playbook x gabarit x topologie), jamais le journal lui-meme.
TOPOLOGIES = ("agent-seul", "fan-out", "salle", "workflow", "cascade")
BRAS = ("temoin", "variante", "topologie-reduite")
CHAMPS_ENTIERS_OPT = ("tokens", "duree_s", "budget_tokens")
# Lot 2 (2026-10-03) : entiers >= 0 stricts (pas de float, pas de bool, pas de null).
CHAMPS_ENTIERS_STRICTS = ("branches_lancees", "duree_branche_max_s")
# Liste blanche : un nom de fichier de prompts/ sans extension (fullmatch : pas de
# saut de ligne final toleré). Un `.md` final, quelle que soit la casse, est refuse.
_RE_GABARIT_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


def _entier_positif(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def verifier_salle_voix(run: dict) -> str | None:
    """Refus si `tour2`, `voix`, `famille`, `branches_lancees` ou `duree_branche_max_s`
    sont presents mais invalides. Champ ABSENT -> None.

    `tour2` : bool (true = desaccord au tour 1 ET tour 2 joue ; false = desaccord sans
    tour 2). `voix` : liste de {nom: str non vide, modele: str non vide, duree_s: nombre
    fini >= 0, trouvailles_retenues: entier >= 0}. `famille` : chaine non vide.
    """
    if "tour2" in run and not isinstance(run["tour2"], bool):
        return (f"log_run REFUS : tour2 invalide : {run['tour2']!r}.\n"
                "  Attendu : true | false (booleen) - ou champ absent.")
    if "famille" in run and not (isinstance(run["famille"], str) and run["famille"].strip()):
        return (f"log_run REFUS : famille invalide : {run['famille']!r}.\n"
                "  Attendu : chaine non vide (famille de taches) - ou champ absent.")
    for champ in CHAMPS_ENTIERS_STRICTS:
        if champ in run and not _entier_positif(run[champ]):
            return (f"log_run REFUS : {champ} invalide : {run[champ]!r}.\n"
                    "  Attendu : entier >= 0 - ou champ absent.")
    if "voix" in run:
        voix = run["voix"]
        attendu = ("  Attendu : liste de {nom: str, modele: str, duree_s: nombre fini "
                   ">= 0, trouvailles_retenues: entier >= 0} - ou champ absent.")
        if not isinstance(voix, list):
            return f"log_run REFUS : voix invalide : {voix!r}.\n{attendu}"
        for i, v in enumerate(voix):
            ok = (isinstance(v, dict)
                  and isinstance(v.get("nom"), str) and v["nom"].strip()
                  and isinstance(v.get("modele"), str) and v["modele"].strip()
                  and isinstance(v.get("duree_s"), (int, float))
                  and not isinstance(v.get("duree_s"), bool)
                  and math.isfinite(v["duree_s"]) and v["duree_s"] >= 0
                  and _entier_positif(v.get("trouvailles_retenues")))
            if not ok:
                return f"log_run REFUS : voix[{i}] invalide : {v!r}.\n{attendu}"
        noms = [v["nom"].strip().casefold() for v in voix]
        doublons = sorted({n for n in noms if noms.count(n) > 1})
        if doublons:
            return (f"log_run REFUS : voix en double : {', '.join(doublons)}.\n"
                    "  Attendu : un nom de voix UNIQUE par run (une seance par lentille).")
    return None


def verifier_gabarit_topologie(run: dict) -> str | None:
    """Refus si un champ optionnel de l'optimiseur est present mais invalide.

    `gabarit` : nom de fichier de `prompts/*.md` sans extension, ou null.
    `topologie` : TOPOLOGIES. `bras` : BRAS. `tache_id` : chaine non vide.
    `tokens`, `duree_s`, `budget_tokens` : nombre >= 0 (bool exclu), ou null.
    Champ ABSENT -> None : un run anterieur n'en porte aucun, et c'est valide.
    Les champs du lot 2 (salles / fan-out) sont verifies par `verifier_salle_voix`.
    """
    refus_lot2 = verifier_salle_voix(run)
    if refus_lot2:
        return refus_lot2
    if "gabarit" in run:
        g = run["gabarit"]
        if g is not None and not (isinstance(g, str) and _RE_GABARIT_OK.fullmatch(g)
                                  and not g.lower().endswith(".md")):
            return (f"log_run REFUS : gabarit invalide : {g!r}.\n  Attendu : nom de "
                    "fichier de prompts/*.md SANS extension ni chemin, ou null - ou "
                    "champ absent.")
    if "topologie" in run and run["topologie"] not in TOPOLOGIES:
        return (f"log_run REFUS : topologie invalide : {run['topologie']!r}.\n"
                f"  Attendu : {' | '.join(TOPOLOGIES)} - ou champ absent.")
    if "bras" in run and run["bras"] not in BRAS:
        return (f"log_run REFUS : bras invalide : {run['bras']!r}.\n"
                f"  Attendu : {' | '.join(BRAS)} - ou champ absent.")
    if "tache_id" in run and not (isinstance(run["tache_id"], str)
                                  and run["tache_id"].strip()):
        return (f"log_run REFUS : tache_id invalide : {run['tache_id']!r}.\n"
                "  Attendu : chaine non vide (identifiant commun aux deux bras "
                "d'une paire) - ou champ absent.")
    for champ in CHAMPS_ENTIERS_OPT:
        if champ in run:
            v = run[champ]
            if v is not None and (isinstance(v, bool)
                                  or not isinstance(v, (int, float))
                                  or not math.isfinite(v) or v < 0):
                return (f"log_run REFUS : {champ} invalide : {v!r}.\n"
                        "  Attendu : nombre fini >= 0 ou null - ou champ absent.")
    return None


def verifier_forme_tache(run: dict) -> str | None:
    """Refus si la forme de tache declaree contredit le vocabulaire ou s'affirme
    `decomposable` sans motif ecrit -- sinon None.

    Trois cas, et le premier est le plus important :
      - champ ABSENT -> None, sans un mot. Non retroactif par construction : un
        avertissement sur les runs anterieurs transformerait une donnee neuve en
        bruit de fond, et la premiere reaction serait de le faire taire.
      - valeur hors vocabulaire -> REFUS si `succes` (un `forme_tache: "mixte"`
        ecrit de bonne foi ne doit pas passer pour une declaration valide), simple
        AVERTISSEMENT sinon : R5 exige que le run rate soit journalise, et le
        refuser sur une faute de frappe en perdrait la trace (meme arbitrage que
        `verifier_etapes_du_plan`, revue bmad-code-review du 2026-09-09).
      - `decomposable` sans motif -> REFUS a tout resultat, celui-la. Ce n'est pas
        une faute de frappe rattrapable : c'est l'affirmation qui justifie de payer
        un fan-out, et une affirmation non motivee est precisement ce que la veille
        reproche au plafond « <= 4 ». L'auteur a toujours l'issue d'omettre le champ
        ou de declarer `inconnu` -- aucune trace n'est perdue.
    """
    if CHAMP_FORME not in run:
        return None
    forme = run.get(CHAMP_FORME)
    if forme not in FORMES_TACHE:
        message = (
            f"{CHAMP_FORME} hors vocabulaire : {forme!r}.\n"
            f"  Attendu : {' | '.join(FORMES_TACHE)} - ou champ absent (un run "
            "anterieur au 2026-09-20 n'en porte aucun, et c'est valide)."
        )
        if run.get("resultat") == "succes":
            return "log_run REFUS : " + message
        print("log_run AVERTISSEMENT : " + message)
        return None
    if forme == FORME_A_JUSTIFIER and not str(
            run.get(CHAMP_FORME_MOTIF) or "").strip():
        return (
            f"log_run REFUS : {CHAMP_FORME} '{FORME_A_JUSTIFIER}' declare sans "
            f"'{CHAMP_FORME_MOTIF}'.\n"
            "  C'est cette declaration qui justifie de payer un fan-out : dire EN "
            "QUOI la tache se decoupe (sous-questions independantes, pas un meme "
            "brief lu en parallele par N salles - ce dernier cas est de la "
            "redondance, pas du decoupage).\n"
            f"  Sinon : '{CHAMP_FORME}': 'inconnu', ou omettre le champ."
        )
    return None


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


# --- Piece 1 : le declencheur DECLARE ; piece 2 : la quittance NOMMEE --------
# Salle `inspection-critique` du 2026-09-19, sur pieces : l'appel au role
# utilisateur/QA n'etait obligatoire NULLE PART en fin d'increment (ni dans la
# skill `agent-orchestrator`, ou la validation utilisateur est conditionnelle au
# type de livrable, ni dans `revue-increment`, ou le role utilisateur n'est pas
# une etape, ni dans aucun des 4 playbooks). Mesure : 1 run
# `en-attente-validation` sur 194, contre 165 `succes`. R5 dit quoi ECRIRE,
# jamais ce qu'il faut FAIRE pour que le statut change.
#
# Pourquoi un champ DECLARE et non une detection : typer automatiquement « ce run
# porte-t-il un livrable consomme par un humain ? » est impossible a 0 token
# depuis l'etage deterministe (l'heuristique textuelle `LIVRABLE_UTILISATEUR`
# plus bas, un simple avertissement, le montre : elle ne voit que des mots). Le
# champ deplace le point dur de la DETECTION vers la DECLARATION au moment du
# plan, qui est verifiable a cout nul : absence = refus.
#
# CE QUE CETTE GARDE NE FERME PAS (concede par la salle ; le taire serait
# malhonnete) :
#   - rien ne prouve que l'artefact cite a ete REELLEMENT ouvert : une
#     declaration suffit a passer. Il faudrait un hash du fichier ou un log
#     d'acces pour que `artefact_ouvert` soit une preuve et non une affirmation ;
#   - les executions DIRECTES, hors `log_run.py`, echappent structurellement a
#     toute garde qui vit dedans. Elle protege le journal, pas le travail.
CHAMP_LIVRABLE = "livrable_utilisateur"
CHAMP_LIVRABLE_MOTIF = "livrable_utilisateur_motif"
CHAMPS_QUITTANCE = ("par", "artefact_ouvert", "quand")
# `utilisateur-produit` est un utilisateur SIMULE. Il repond a « qui a ete simule
# en train d'ouvrir la page », pas a « qui a ouvert la page ». Sans le refus (d)
# ci-dessous, la garde SE SIGNERAIT ELLE-MEME : le meme dispositif produirait le
# livrable, simulerait son utilisateur, et signerait la quittance.
AGENT_UTILISATEUR_SIMULE = "utilisateur-produit"
# SIGNAUX_ECHEC_PRODUIT et _texte_quittance ont ete RETIRES le 2026-09-19 : la
# regle « une quittance signee par un agent ne vaut pas quittance » refuse
# desormais l'identite AVANT qu'on ait a lire son rapport, et les elargir aux
# quittances humaines ferait refuser « non operationnel avant correction » ecrit
# honnetement (le refus pousserait a mentir, ce que R5 interdit plus surement
# qu'un faux succes). Ils sont partis plutot que laisses en place : un commentaire
# ecrit ici affirmait qu'ils restaient utilises ailleurs, ce que `vulture
# --min-confidence 60` a dementi le jour meme (instruction de la trouvaille
# « vulture »). Une constante morte gardee « au cas ou » est un filet non appele.

# Identites NON HUMAINES pour la quittance. Liste de MOTIFS, pas de noms : un
# sous-agent neuf ne doit pas passer parce qu'il n'etait pas prevu.
PREFIXES_NON_HUMAINS = ("agent-", "agent ", "sous-agent", "subagent", "bot-",
                        "claude-", "gpt-")
EXACTS_NON_HUMAINS = ("agent", "sous-agent", "subagent", "claude", "claude code",
                      "llm", "ia", "ai", AGENT_UTILISATEUR_SIMULE)


def est_identite_non_humaine(par: str) -> bool:
    """`par` designe-t-il un agent plutot qu'une personne ?

    Regle NEGATIVE inscrite au referentiel le 2026-09-19 : une quittance
    produite par un agent simulant l'utilisateur ne vaut pas quittance. On
    reconnait un agent par son ECRITURE (prefixe `agent-`, mention
    `sous-agent`, nom de modele) - on ne peut pas prouver qu'un nom est humain,
    on peut refuser ceux qui s'annoncent comme ne l'etant pas. Un faux negatif
    (« Jean Agent ») reste possible : la garde exige que la quittance soit
    SIGNEE de facon relisible a la main, elle ne fait pas l'etat civil.
    """
    p = " ".join(str(par or "").strip().lower().replace("_", "-").split())
    if not p:
        return False
    if p in EXACTS_NON_HUMAINS:
        return True
    return p.startswith(PREFIXES_NON_HUMAINS)


def verifier_validation_utilisateur(run: dict, au_solde: bool = False) -> str | None:
    """Les 4 refus mecaniques sur `resultat: succes` - sinon None.

    (a) `livrable_utilisateur` absent du run ;
    (b) `livrable_utilisateur: true` et bloc `validation` absent ;
    (c) `artefact_ouvert` vide ou absent, MEME si `par` est rempli ;
    (d) `par: utilisateur-produit` avec un signal d'echec produit dans son
        rapport (ou dans les notes du run) : `succes` interdit, au mieux
        `en-attente-validation`.

    NON RETROACTIF : au `--solde`, un run qui ne PORTE PAS le champ a ete ecrit
    avant le deploiement - il sort sans aucun controle. Les 194 runs deja
    journalises restent lisibles et soldables. A l'APPEND, le champ est exige :
    tout run ecrit desormais nait avec.

    Meme style que `verifier_etapes_du_plan` : rien n'est ecrit sur refus, et le
    message NOMME le champ fautif plutot que de dire "quittance incomplete"."""
    if run.get("resultat") != "succes":
        return None
    if au_solde and CHAMP_LIVRABLE not in run:
        return None
    if CHAMP_LIVRABLE not in run:                                       # (a)
        return (
            f"log_run REFUS : champ '{CHAMP_LIVRABLE}' (booleen) absent - tout run "
            "orchestre doit DECLARER s'il porte un livrable consomme par un "
            "humain, avec une justification courte.\n"
            f"  Rejouer avec '{CHAMP_LIVRABLE}': true + un bloc 'validation' "
            f"{{{', '.join(CHAMPS_QUITTANCE)}}}, ou '{CHAMP_LIVRABLE}': false + "
            f"'{CHAMP_LIVRABLE_MOTIF}': '<pourquoi aucun humain ne consomme "
            "d'artefact ici>'."
        )
    declare = run.get(CHAMP_LIVRABLE)
    if not isinstance(declare, bool):
        return (f"log_run REFUS : '{CHAMP_LIVRABLE}' doit etre un booleen "
                f"(recu : {declare!r}).")
    if declare is False:
        # Un `false` nu serait la case a cocher qui desarme la garde sans rien
        # dire ; le motif rend la declaration relisible par le superviseur.
        if not str(run.get(CHAMP_LIVRABLE_MOTIF) or "").strip():
            return (
                f"log_run REFUS : '{CHAMP_LIVRABLE}': false sans "
                f"'{CHAMP_LIVRABLE_MOTIF}' - une declaration d'absence de livrable "
                "se justifie, sinon 'false' devient la case a cocher qui desarme "
                "la garde. Ex. : 'run interne, aucun artefact ouvert par un humain'."
            )
        return None
    validation = run.get("validation")
    if not isinstance(validation, dict) or not validation:              # (b)
        return (
            f"log_run REFUS : '{CHAMP_LIVRABLE}': true sans bloc 'validation' - un "
            "livrable consomme par un humain ne se journalise 'succes' qu'avec une "
            "quittance NOMMEE.\n"
            f"  Attendu : 'validation': {{'par': '<sous-agent du plan ou nom "
            "d'humain>', 'artefact_ouvert': '<chemin, URL ou capture>', 'quand': "
            "'<horodatage>'}}. Sans quittance : 'en-attente-validation'."
        )
    par = str(validation.get("par") or "").strip()
    if not par:
        return ("log_run REFUS : 'validation.par' vide - la quittance doit NOMMER "
                "une PERSONNE. Un nom de sous-agent n'y suffit pas (cf. "
                "est_identite_non_humaine) : sans personne pour signer, le run "
                "reste 'en-attente-validation'.")
    if est_identite_non_humaine(par):
        return (
            f"log_run REFUS : 'validation.par' nomme '{par}', une identite "
            "d'AGENT - une quittance produite par un agent simulant "
            "l'utilisateur NE VAUT PAS quittance. Le nom en sortie doit etre "
            "celui d'une PERSONNE.\n"
            "  Mesure : le taux de succes d'un agent varie jusqu'a 9 points de "
            "pourcentage selon le LLM qui joue l'utilisateur, avec une "
            "miscalibration systematique (Lost in Simulation, veille adoptee le "
            "2026-09-19). Mettre le nom d'agent AU PLAN ne repare rien : il "
            "repond a 'qui a ete simule en train d'ouvrir l'artefact', pas a "
            "'qui l a ouvert'.\n"
            "  Sans personne pour signer : 'en-attente-validation'."
        )
    if not str(validation.get("artefact_ouvert") or "").strip():        # (c)
        return (
            "log_run REFUS : 'validation.artefact_ouvert' vide ou absent - MEME "
            f"avec 'par': '{par}' rempli. Un 'succes' sur livrable utilisateur "
            "exige de dire QUEL artefact a ete ouvert : chemin de fichier, URL "
            "servie, ou capture.\n"
            "  (Cette garde ne prouve PAS l'ouverture reelle : elle exige qu'elle "
            "soit nommee, donc verifiable a la main.)"
        )
    if not str(validation.get("quand") or "").strip():
        return ("log_run REFUS : 'validation.quand' vide ou absent - une quittance "
                "sans horodatage ne se rattache a aucune version de l'artefact.")
    # (d) ABSORBE le 2026-09-19. Ce refus visait `par: utilisateur-produit` dont
    # le rapport portait un signal d'echec produit ; depuis que TOUTE identite
    # d'agent est refusee plus haut (regle Lost in Simulation), il ne peut plus
    # etre atteint - un `par` valide est desormais un nom de personne, et les
    # signaux d'echec ne doivent PAS s'y appliquer : un humain qui ecrit
    # "non operationnel avant correction" dans une quittance honnete ne doit pas
    # se faire refuser. Sa constante et son aide de lecture ont ete retirees avec
    # lui (cf. le commentaire en tete de fichier) plutot que laissees mortes.
    return None




# --- Lot nominatif de constats (remede 2, superviseur 2026-09-20) ------------
# Mesure : 172 constats crees contre 156 fermes entre le 2026-07-30 et le
# 2026-09-20 (469 snapshots), avec des pics de creation NETTE les jours memes ou
# l'utilisateur demandait qu'on traite les ecarts (01/09 +14 : la demande
# « traite les 34 constats » a fait passer le compteur de 5 a 19 dans la
# journee). Un `succes` adosse a un compteur vivant ne dit donc RIEN de la
# demande. Un run peut desormais porter `"lot": "<id>"` : la liste NOMINATIVE
# des constats vises, gelee a l'ouverture par
# `.claude/supervision/lot.py --ouvrir`. Le run ne se solde `succes` que si
# 100 % du lot est ferme, et le refus NOMME les manquants.
#
# NON RETROACTIF, comme `livrable_utilisateur` : un run sans champ `lot` (les
# 195 anterieurs, et tous les depots de la flotte ou le lot n'existe pas) n'est
# pas controle. Le canon est propage a 6 depots : aucun signal nouveau ne doit
# s'y allumer par heritage.
CHAMP_LOT = "lot"


def _module_lot():
    """Charge `.claude/supervision/lot.py` du depot courant, ou None."""
    import importlib.util
    # Remontee des ancetres, jamais un nombre fixe de dirname() : ce fichier vit
    # en canon (.claude/dispositif/canon/) ET en copie generee
    # (.claude/orchestration/) — deux profondeurs differentes.
    chemin, d = None, os.path.dirname(os.path.abspath(__file__))
    while True:
        cand = os.path.join(d, ".claude", "supervision", "lot.py")
        if os.path.exists(cand):
            chemin = cand
            break
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    spec = importlib.util.spec_from_file_location("_lot_log_run", chemin)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:                                   # noqa: BLE001
        return None
    return mod


def verifier_lot(run: dict) -> str | None:
    """Refus si `resultat: succes` alors que le lot nomme n'est pas a 100 %.

    Trois refus : (a) lot nomme mais instrument absent ; (b) lot INTROUVABLE —
    refus DUR, solder contre un lot inexistant serait inoperant en silence ;
    (c) lot incomplet — le message NOMME chaque constat qui manque, « lot
    incomplet » ferait exactement ce que ce remede reproche au compteur."""
    if run.get("resultat") != "succes":
        return None
    lot_id = str(run.get(CHAMP_LOT) or "").strip()
    if not lot_id:
        return None
    mod = _module_lot()
    if mod is None:                                                     # (a)
        return (f"log_run REFUS : le run nomme le lot '{lot_id}' mais "
                ".claude/supervision/lot.py est introuvable dans ce depot - "
                "impossible de verifier que le lot est ferme. Retirer le champ "
                f"'{CHAMP_LOT}' ou installer l'instrument.")
    try:
        fermes, total, restants = mod.etat_lot(lot_id)
    except mod.LotIntrouvable:                                          # (b)
        return (f"log_run REFUS : lot '{lot_id}' INTROUVABLE. Solder un run "
                "'succes' contre un lot qui n'existe pas serait inoperant en "
                "silence : la promesse faite a l'utilisateur ne serait rattachee "
                "a rien. Ouvrir le lot avec "
                "`py .claude/supervision/lot.py --ouvrir <id> --projet <projet>`, "
                "ou corriger l'identifiant.")
    if restants:                                                        # (c)
        return ("log_run REFUS : lot '" + lot_id + f"' a {fermes}/{total} fermes - "
                "un 'succes' declarerait satisfaite une demande qui ne l'est pas.\n  "
                + "\n  ".join(restants)
                + "\n  Journaliser 'partiel' (ce qui a ete ferme), ou fermer les "
                  "constats ci-dessus. Un constat CREE depuis l'ouverture du lot "
                  "n'y entre jamais : il ne peut ni le gonfler ni le sauver.")
    return None


def compter_succes_sans_oracle(runs=None):
    """(sans_oracle, total_succes) — la DETTE laissee par la non-retroactivite.

    `verifier_validation_utilisateur(au_solde=True)` laisse sortir sans aucun
    controle tout run qui ne porte pas `livrable_utilisateur`. C'est DELIBERE
    (les runs anterieurs au deploiement restent soldables), mais la mesure du
    superviseur — 165 des 166 runs `succes` sans le champ — etait muette :
    personne ne pouvait la relire. Cette fonction la rend executable.
    `py log_run.py --dette-oracle`."""
    if runs is None:
        try:
            with open(RUNS_PATH, encoding="utf-8") as fh:
                runs = [json.loads(l) for l in fh if l.strip()]
        except (OSError, ValueError):
            return 0, 0
    succes = [r for r in runs if isinstance(r, dict) and r.get("resultat") == "succes"]
    sans = [r for r in succes if CHAMP_LIVRABLE not in r]
    return len(sans), len(succes)


# --- Ordre du playbook deck : le RENDU avant toute hypothese ----------------
# Cas reel du 2026-09-22 (run « deck PPT Trucks KO ») : 5 hypotheses fausses et 3
# analyses python-pptx infructueuses (geometrie, largeurs de colonnes, marges,
# polices, structure des paragraphes) avant qu'un rendu reel ne montre le defaut
# en une passe. python-pptx est un parseur TOLERANT : il est aveugle par
# construction a ce que le rendu expose. Le playbook `export-ppt-verifie` impose
# donc une etape 0 « rendu comparatif avant toute hypothese ».
# Ce que la garde REFUSE : un plan de CE playbook ou une etape d'analyse
# python-pptx precede la premiere etape de rendu. Ce qu'elle ne ferme pas : un
# plan dont les libelles ne nomment ni l'un ni l'autre n'est pas vu — garde-fou
# textuel, pas preuve.
PLAYBOOK_DECK = "export-ppt-verifie"
_MARQ_RENDU = ("rendu", "render", "libreoffice", "pptx-verify", "screenshot",
               "pymupdf", "capture", "pdf de reference", "com ")
_MARQ_ANALYSE = ("python-pptx", "python pptx", "xml brut", "analyse du xml",
                 "inspection xml", "parse du pptx", "parsing pptx")


def verifier_ordre_playbook_ppt(run: dict) -> str | None:
    """Refus si une analyse python-pptx precede le rendu — sinon None."""
    if str(run.get("playbook") or "") != PLAYBOOK_DECK:
        return None
    rang_rendu = rang_analyse = None
    libelle_analyse = ""
    for rang, etape in enumerate(run.get("plan") or [], start=1):
        if not isinstance(etape, dict):
            continue
        texte = (str(etape.get("etape", "")) + " " + str(etape.get("agent", ""))).lower()
        if rang_analyse is None and any(m in texte for m in _MARQ_ANALYSE):
            rang_analyse, libelle_analyse = rang, str(etape.get("etape", ""))
        if rang_rendu is None and any(m in texte for m in _MARQ_RENDU):
            rang_rendu = rang
    if rang_analyse is None:
        return None
    if rang_rendu is not None and rang_rendu < rang_analyse:
        return None
    ou = (f"le rendu arrive a l'etape {rang_rendu}"
          if rang_rendu is not None else "aucune etape de rendu n'est au plan")
    return (
        f"log_run REFUS : playbook '{PLAYBOOK_DECK}' — analyse python-pptx a "
        f"l'etape {rang_analyse} (« {libelle_analyse} ») alors que {ou}.\n"
        "  Etape 0 OBLIGATOIRE de ce playbook : rendu comparatif (original vs "
        "regenere) AVANT toute hypothese. Mesure du 2026-09-22 : 5 hypotheses "
        "fausses et 3 analyses python-pptx infructueuses avant que le rendu ne "
        "montre le defaut en une passe — python-pptx est aveugle a ce defaut par "
        "construction.\n"
        "  Rejouer en placant l'etape de rendu comparatif AVANT l'analyse."
    )


# --- Interrogateur du journal (--stats) -------------------------------------
# Pourquoi ce mode existe : `runs.jsonl` fait ~486 Ko et un `Read` entier coute
# ~109 000 jetons, donc la regle d'economie du depot ne laissait que `grep`. C'est
# elle qui a fabrique une mesure FAUSSE le 2026-09-22 :
#   grep -c "en-attente-validation" -> 14 « runs en attente » chez VSCode3,
#   alors que 0 run portait ce RESULTAT — la chaine etait dans `notes`, texte
#   libre. Un compte par SOUS-CHAINE n'est pas un compte par CHAMP.
# Le mode lit le fichier LIGNE A LIGNE (streaming, jamais en memoire entiere) et
# compte la valeur d'un CHAMP JSON. `--depot` filtre sur ce que le run NOMME
# (demande + plan + notes) : filtre textuel assume, borne explicitement dans la
# sortie, jamais une preuve d'appartenance.
CHAMPS_STATS = ("resultat", "qualification", "playbook", CHAMP_FORME,
                "reprises", CHAMP_LIVRABLE)


def _texte_du_run(run: dict) -> str:
    morceaux = [str(run.get("demande") or ""), str(run.get("notes") or "")]
    for e in run.get("plan") or []:
        if isinstance(e, dict):
            morceaux.append(str(e.get("etape", "")) + " " + str(e.get("agent", "")))
    return " ".join(morceaux).lower()


def stats(champ: str = "resultat", depot: str | None = None, chemin: str | None = None):
    """(compteur {valeur: n}, total_lu, total_retenu, lignes_illisibles).

    Streaming : une ligne a la fois, rien n'est accumule hors le compteur.
    """
    compteur, total, retenus, illisibles = {}, 0, 0, 0
    with open(chemin or RUNS_PATH, encoding="utf-8") as fh:
        for ligne in fh:
            ligne = ligne.strip()
            if not ligne:
                continue
            total += 1
            try:
                run = json.loads(ligne)
            except ValueError:
                illisibles += 1
                continue
            if not isinstance(run, dict):
                illisibles += 1
                continue
            if depot and depot.lower() not in _texte_du_run(run):
                continue
            retenus += 1
            valeur = run.get(champ, "(absent)")
            if isinstance(valeur, (dict, list)):
                valeur = "(structure)"
            compteur[str(valeur)] = compteur.get(str(valeur), 0) + 1
    return compteur, total, retenus, illisibles


def imprimer_stats(argv) -> int:
    opts, i = {}, 0
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    champ = opts.get("champ", "resultat")
    depot = opts.get("depot")
    try:
        compteur, total, retenus, illisibles = stats(champ, depot)
    except OSError as exc:
        print(f"log_run --stats : journal illisible ({exc})")
        return 1
    entete = f"log_run --stats : champ '{champ}'"
    if champ not in CHAMPS_STATS:
        entete += " (hors champs usuels : " + ", ".join(CHAMPS_STATS) + ")"
    if depot:
        entete += (f" · filtre textuel '{depot}' — le run le NOMME dans demande/plan/"
                   "notes ; un run qui ne le nomme pas n'est pas vu")
    print(entete)
    print(f"  {retenus} run(s) retenu(s) sur {total} lu(s)"
          + (f", {illisibles} ligne(s) illisible(s)" if illisibles else ""))
    for valeur, n in sorted(compteur.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {n:5d}  {valeur}")
    if not compteur:
        print("  (aucun run retenu)")
    return 0


def lister_runs(champ: str, valeur: str, chemin: str | None = None):
    """Itere (numero_de_ligne, run) pour les runs dont `champ` vaut `valeur`.

    Streaming, calque sur `stats()` : une ligne a la fois, rien n'est accumule.
    `runs.jsonl` fait 526 Ko au hub et un `Read` entier coute ~109 000 tokens —
    materialiser les 231 runs pour en afficher 39 serait payer ce prix-la.
    Tolerance identique a `stats()` : une ligne illisible est sautee, jamais
    fatale.
    """
    with open(chemin or RUNS_PATH, encoding="utf-8") as fh:
        for numero, ligne in enumerate(fh, start=1):
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                run = json.loads(ligne)
            except ValueError:
                continue
            if not isinstance(run, dict):
                continue
            brut = run.get(champ, "(absent)")
            if isinstance(brut, (dict, list)):
                brut = "(structure)"
            if str(brut) == str(valeur):
                yield numero, run


def lister(argv) -> int:
    """--lister <champ> <valeur> — LECTURE SEULE : les runs qui matchent.

    Rend le numero de ligne, le `ts` et les `notes` INTEGRALES. Pas de coupe :
    on ne decide pas de solder (ou de classer sans suite) un run dont l'objet
    est illisible — meme motif que `read_runs` dans `scripts/scan_projets.py`.
    Aucun classement automatique ouvert/ferme : l'outil PRESENTE, l'humain
    requalifie (un classement qui se trompe solderait un reste reel, R5).
    """
    if len(argv) < 2:
        print("log_run --lister : usage : --lister <champ> <valeur>"
              f"  (champs usuels : {', '.join(CHAMPS_STATS)})")
        return 1
    champ, valeur = argv[0], argv[1]
    n = 0
    try:
        print(f"log_run --lister : runs dont '{champ}' == '{valeur}' "
              "(lecture seule, aucun run n'est solde)")
        for numero, run in lister_runs(champ, valeur):
            n += 1
            ts = run.get("ts")
            # `or ''` et pas `get(..., '')` : le journal porte des `null`, et un
            # `ts: null` s'affiche « None » — c'est justement le cas que cette
            # commande doit rendre VISIBLE (2 runs du hub, mesure 2026-09-27).
            print(f"  L{numero:<5d} ts={ts!s:<26} {str(run.get('demande') or '')[:70]}")
            print(f"         notes: {str(run.get('notes') or '')}")
    except OSError as exc:
        print(f"log_run --lister : journal illisible ({exc})")
        return 1
    print(f"  {n} run(s) liste(s).")
    if n:
        print("  Solder l'un d'eux : --solde <prefixe-ts> <resultat> \"note\","
              " ou --solde --demande <fragment de demande> <resultat> \"note\""
              " quand le `ts` est absent.")
    return 0


def main(argv) -> int:
    if argv and argv[0] == "--solde":
        return solder(argv[1:])
    if argv and argv[0] == "--stats":
        return imprimer_stats(argv[1:])
    # AVANT le repli d'append (`raw = argv[0]` plus bas) : sans ce routage, un
    # `--lister` mal forme tomberait dans le parseur JSON d'append et se
    # plaindrait d'un « JSON invalide ». Meme motif que la porte de derriere
    # de `--solde` documentee dans `solder()`.
    if argv and argv[0] == "--lister":
        return lister(argv[1:])
    if argv and argv[0] == "--dette-oracle":
        sans, total = compter_succes_sans_oracle()
        print(f"log_run : {sans} run(s) 'succes' sur {total} ne portent aucun "
              f"'{CHAMP_LIVRABLE}' - non controles au solde (non-retroactivite "
              "deliberee). La dette est desormais mesuree, plus muette.")
        return 0
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
    refus_ordre = verifier_ordre_playbook_ppt(run)
    if refus_ordre:
        print(refus_ordre)
        return 1
    refus_forme = verifier_forme_tache(run)
    if refus_forme:
        print(refus_forme)
        return 1
    refus_gt = verifier_gabarit_topologie(run)
    if refus_gt:
        print(refus_gt)
        return 1
    refus_validation = verifier_validation_utilisateur(run, au_solde=False)
    if refus_validation:
        print(refus_validation)
        return 1
    refus_lot = verifier_lot(run)
    if refus_lot:
        print(refus_lot)
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
