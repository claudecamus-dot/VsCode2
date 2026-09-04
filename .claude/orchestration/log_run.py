# +-- GÉNÉRÉ — NE PAS ÉDITER LOCALEMENT ---------------------------------------
# | Source de vérité : hub de supervision VScode5, .claude/dispositif/canon/log_run.py
# | Une correction faite ICI sera ÉCRASÉE à la prochaine propagation. Pour la
# | garder : la signaler au hub, qui corrige le canon et re-synchronise.
# | (Depuis le hub : « py .claude/dispositif/sync_dispositif.py » — ce script
# |  n'est pas déployé, il n'existe pas dans ce dépôt.)
# | Provenance canon : 3ddc950 du 2026-09-04 — permet, au prochain sync, de dire si
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


def solder(argv) -> int:
    """--solde <prefixe-ts> <resultat> [note] — requalifie un run existant."""
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
    avant = run.get("resultat")
    run["resultat"] = resultat
    date = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    run["notes"] = (str(run.get("notes", "")) + f" | solde {date} : {note}").strip(" |")
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
    run.setdefault("ts", datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
    with open(RUNS_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(run, ensure_ascii=False) + "\n")
    print(f"log_run : run journalise ({run['qualification']}, {len(run.get('plan', []))} etape(s))")
    avertir_validation_utilisateur(run)
    avertir_revue_increment(run)
    return 0


# Marqueurs d'un livrable CONSOMMÉ par l'utilisateur (deck exporté, écran) et
# d'une validation utilisateur explicite dans les notes. Diagnostic superviseur
# 2026-07-23 (arbitré) : 0/47 runs « en-attente-validation » alors que la règle
# l'exigeait — le garde-fou devient exécutable, en avertissement NON bloquant.
LIVRABLE_UTILISATEUR = ("deck", "slide", "pptx", "ecran", "écran", "export")
VALIDATION_UTILISATEUR = ("valide par l'utilisateur", "validé par l'utilisateur",
                          "valide par utilisateur", "ok utilisateur")


def avertir_revue_increment(run: dict) -> None:
    """Avertissement NON bloquant (finding playbook:evolution-flotte 2026-07-29) :
    un run orchestré journalisé `succes` doit porter une étape terminale
    revue-increment dans son plan (ou sa trace dans les notes — cas d'une revue
    de campagne couvrant plusieurs runs de la même séance). On mesure d'abord
    combien de runs déclenchent l'avertissement avant tout garde-fou dur."""
    if run.get("resultat") != "succes" or run.get("qualification") != "orchestre":
        return
    plan = run.get("plan") or []
    etapes = " ".join(str(e.get("etape", "")) + " " + str(e.get("agent", ""))
                      for e in plan if isinstance(e, dict)).lower()
    notes = str(run.get("notes", "")).lower()
    if "revue-increment" not in etapes and "revue-increment" not in notes:
        print(
            "log_run AVERTISSEMENT : run 'succes' sans etape terminale "
            "revue-increment au plan (ni trace dans notes) — la boucle de revue "
            "de fin d'increment est une etape obligatoire des playbooks depuis "
            "le 2026-07-29 (une revue de campagne en fin de seance suffit : la "
            "mentionner dans notes)."
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
