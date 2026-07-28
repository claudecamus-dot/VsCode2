"""Écriture validée du diagnostic étage 2 (.claude/supervision/diagnostic.json).

Utilisé par la skill `agent-supervisor` : elle compose les constats (LLM), ce script
garantit le schéma que `scan_transcripts.py` consomme (fusion wiki + routing-hints).

Usage : py .claude/supervision/write_diagnostic.py '<json>'   (ou JSON sur stdin)
Schéma attendu : {"findings": [{"categorie", "titre", "preuve", ...}]}
  - categorie : ko-repete | inefficacite | agent-mort | interaction |
    verification-manquante | autre. `ko-repete` et `inefficacite` avec une `cible`
    alimentent la liste `prudence` de routing-hints.json (l'orchestrateur les évite).
  - titre (str, requis) : le constat en une phrase.
  - preuve (str, requis) : le signal objectif qui l'ancre (comptage, erreur, reprise,
    correction utilisateur) — garde-fou anti-auto-complaisance : jamais de constat
    sans donnée à l'appui.
  - priorite (int 1-5, optionnel, défaut 1), cible (str, optionnel),
    recommandation (str, optionnel).
  - re_challenge (bool, optionnel — 2026-07-28) : ce constat re-challenge une décision
    déjà arbitrée sur la même cible, avec des données NOUVELLES. Il échappe alors au
    filtre `finding_arbitre` du scan et s'affiche au tableau de bord. Exige une `cible`
    (sinon il ne conteste rien) ; à n'utiliser que si la `preuve` est postérieure à
    l'arbitrage — sans quoi c'est une redite que l'humain a déjà tranchée.
  - proposition (str, optionnel — incrément C « challenger ») : le changement concret
    proposé (nouveau déclencheur de skill, contrat de playbook amendé, désinstallation…),
    en une phrase ou un mini-diff inline. Rendue dans le wiki avec le constat ;
    JAMAIS appliquée par le superviseur — l'humain arbitre, l'orchestrateur applique
    la version validée (gouvernance : agent-orchestrateur.md §6).
`generated` est posé par ce script (horodatage courant) ; le fichier est réécrit en
entier à chaque diagnostic (pas un journal). Gitignoré — donnée machine.
Env (tests) : AGENT_SUPERVISION_DIAGNOSTIC. Conception : docs/reflexions/agent-superviseur.md.
"""
import datetime
import json
import os
import sys

DIAGNOSTIC_PATH = os.environ.get("AGENT_SUPERVISION_DIAGNOSTIC") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "diagnostic.json"
)
CATEGORIES = (
    "ko-repete", "inefficacite", "agent-mort", "interaction",
    "verification-manquante", "non-convergence", "autre",
)
# Plafond de la skill agent-supervisor (§ « 5 constats max, priorisés ») — appliqué ici
# parce que le scan n'affiche que les 5 premiers : au-delà, un constat se perdait sans trace.
MAX_FINDINGS = 5


def main(argv) -> int:
    # Console Windows en cp1252 : le JSON arrive/repart toujours en UTF-8.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raw = argv[0] if argv else sys.stdin.read()
    try:
        diag = json.loads(raw)
    except ValueError as exc:
        print(f"write_diagnostic : JSON invalide ({exc})")
        return 1
    findings = diag.get("findings") if isinstance(diag, dict) else None
    if not isinstance(findings, list) or not findings:
        print("write_diagnostic : un objet {\"findings\": [...]} non vide est attendu")
        return 1
    if len(findings) > MAX_FINDINGS:
        # Le plafond est une règle de la skill (« 5 constats max, priorisés »), mais le
        # scan tronquait en silence à l'affichage : au-delà de 5, les suivants
        # disparaissaient sans trace. Refuser à l'ÉCRITURE rend la perte impossible et
        # force la priorisation là où elle doit se faire — chez le superviseur.
        print(f"write_diagnostic : {len(findings)} constats, maximum {MAX_FINDINGS} "
              "(prioriser avant d'ecrire : un rapport que personne ne lit ne sert a rien)")
        return 1
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            print(f"write_diagnostic : finding #{i} n'est pas un objet")
            return 1
        missing = [k for k in ("categorie", "titre", "preuve") if not f.get(k)]
        if missing:
            print(f"write_diagnostic : finding #{i} sans {', '.join(missing)} "
                  "(un constat sans preuve objective ne se journalise pas)")
            return 1
        if f["categorie"] not in CATEGORIES:
            print(f"write_diagnostic : finding #{i} categorie invalide "
                  f"(attendu : {' | '.join(CATEGORIES)})")
            return 1
        prio = f.setdefault("priorite", 1)
        if not isinstance(prio, int) or not 1 <= prio <= 5:
            print(f"write_diagnostic : finding #{i} priorite invalide (int 1-5)")
            return 1
        # Typé strictement : ce champ est un passe-droit sur une décision humaine, une
        # valeur truthy accidentelle (la chaîne "false") ne doit pas l'activer.
        if "re_challenge" in f and not isinstance(f["re_challenge"], bool):
            print(f"write_diagnostic : finding #{i} re_challenge doit valoir true ou false "
                  f"(recu : {f['re_challenge']!r})")
            return 1
        if f.get("re_challenge") and not f.get("cible"):
            print(f"write_diagnostic : finding #{i} re_challenge sans cible "
                  "(un re-challenge conteste un arbitrage, donc une cible precise)")
            return 1
    out = {
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "findings": findings,
    }
    with open(DIAGNOSTIC_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"write_diagnostic : {len(findings)} constat(s) -> {os.path.basename(DIAGNOSTIC_PATH)} "
          "(relancer le scan pour propager wiki + routing-hints)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
