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
  - vu_le (str « AAAA-MM-JJ », posé par CE script) : date de PREMIÈRE apparition du
    constat. Un constat reconduit la conserve — c'est ce qui distingue « vu hier et
    toujours pas tranché » de « trouvé aujourd'hui », et ce qui date la fenêtre
    d'arbitrage (cf. `_ferme`).

`generated` est posé par ce script (horodatage courant). Le fichier est un REGISTRE À
ÉTAT et non un instantané (2026-09-02) : avant d'écrire, les constats du diagnostic
précédent que personne n'a tranchés sont REPORTÉS. Un constat ne disparaît que fermé par
un arbitrage, jamais parce qu'un diagnostic plus récent a été écrit. Gitignoré — donnée
machine.
Env (tests) : AGENT_SUPERVISION_DIAGNOSTIC, AGENT_SUPERVISION_ARBITRAGES.
Conception : docs/reflexions/agent-superviseur.md.
"""
import datetime
import json
import os
import sys

DIAGNOSTIC_PATH = os.environ.get("AGENT_SUPERVISION_DIAGNOSTIC") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "diagnostic.json"
)
ARBITRAGES_PATH = os.environ.get("AGENT_SUPERVISION_ARBITRAGES") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "arbitrages.json"
)
CATEGORIES = (
    "ko-repete", "inefficacite", "agent-mort", "interaction",
    "verification-manquante", "non-convergence", "autre",
)
# Plafond de la skill agent-supervisor (§ « 5 constats max, priorisés ») — appliqué ici
# parce que le scan n'affiche que les 5 premiers : au-delà, un constat se perdait sans trace.
MAX_FINDINGS = 5


def _identite(finding: dict) -> tuple:
    """Ce qui fait qu'un constat est « le même » d'un diagnostic à l'autre.

    `(cible, categorie)` — exactement la granularité à laquelle un arbitrage ferme un
    constat côté scan (`finding_arbitre` + `_couvre`). Le titre serait plus fin mais
    reconduirait un doublon à chaque reformulation ; la seule cible confondrait deux
    constats de nature différente sur le même fichier.
    Un constat SANS cible ne peut être fermé par aucun arbitrage : on le distingue alors
    par son titre, faute de mieux, pour ne pas fusionner deux constats indépendants."""
    cible = str(finding.get("cible") or "").strip()
    if not cible:
        cible = "~" + str(finding.get("titre") or "")
    return (cible, str(finding.get("categorie") or ""))


def _charger_arbitrages() -> list:
    """Décisions humaines (fichier versionné, JAMAIS écrit ici). Même lecture tolérante
    que le scan : un fichier absent ou illisible ne ferme aucun constat — la direction
    sûre, l'erreur inverse perdant un constat en le croyant tranché."""
    try:
        with open(ARBITRAGES_PATH, encoding="utf-8") as fh:
            entries = json.load(fh).get("arbitrages", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("cible") and e.get("decision")]


def _couvre(arbitrage: dict, categorie: str) -> bool:
    """Miroir de `_couvre` dans scan_transcripts.py : `categories` absent ferme tout, une
    liste ferme exactement ces catégories, un champ mal formé ne ferme rien."""
    cats = arbitrage.get("categories")
    if cats is None:
        return True
    return isinstance(cats, list) and categorie in cats


def _ferme(finding: dict, arbitrages: list) -> bool:
    """Un arbitrage postérieur à la PREMIÈRE vue du constat le ferme.

    La comparaison porte sur `vu_le`, pas sur la date du diagnostic courant : sans quoi un
    constat reconduit repousserait indéfiniment sa propre fenêtre d'arbitrage et ne serait
    jamais reconnu comme tranché.
    Sans `cible`, aucun arbitrage ne peut le viser ; sans `vu_le` exploitable, on ne peut
    pas PROUVER qu'un arbitrage lui est postérieur. Dans les deux cas on GARDE le constat :
    le coût d'un doublon visible est très inférieur à celui d'une perte silencieuse — c'est
    tout l'objet de ce mécanisme."""
    cible = finding.get("cible")
    if not cible:
        return False
    vu_le = str(finding.get("vu_le") or "")[:10]
    if not vu_le:
        return False
    for a in arbitrages:
        if a.get("cible") != cible or not _couvre(a, finding.get("categorie")):
            continue
        if str(a.get("date") or "")[:10] >= vu_le:
            return True
    return False


def _precedent() -> tuple:
    """(constats du diagnostic précédent, sa date d'écriture) — ([], "") s'il n'y en a pas."""
    try:
        with open(DIAGNOSTIC_PATH, encoding="utf-8") as fh:
            precedent = json.load(fh)
        anciens = precedent.get("findings", [])
    except (OSError, ValueError, AttributeError):
        return [], ""
    if not isinstance(anciens, list):
        return [], ""
    return ([f for f in anciens if isinstance(f, dict)],
            str(precedent.get("generated") or "")[:10])


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
    # --- Registre à état (2026-09-02) -------------------------------------------------
    # Avant cette date : open(w) + dump du seul contenu neuf. Écrire un diagnostic
    # EFFAÇAIT donc les constats précédents non arbitrés, sans trace ni avertissement.
    # Mesuré : des 5 constats du 2026-09-01T23:00, un seul avait été arbitré quand
    # l'écriture du 2026-09-02T12:12 les a tous remplacés. La boucle
    # propose→arbitre→applique fuyait à son premier maillon.
    anciens, date_precedente = _precedent()
    arbitrages = _charger_arbitrages()
    aujourdhui = datetime.date.today().isoformat()
    # Un diagnostic écrit avant l'existence de `vu_le` n'en porte pas : sa date d'écriture
    # fait foi. Sans ce repli, un `vu_le` vide rendrait `date >= ""` vrai pour n'importe
    # quel arbitrage et refermerait en silence exactement ce qu'on cherche à sauver.
    for f in anciens:
        f.setdefault("vu_le", date_precedente or aujourdhui)
    connus = {_identite(f): f for f in anciens}
    for f in findings:
        # Constat reconduit : il garde sa date de première vue — c'est elle qui dit depuis
        # quand l'humain ne l'a pas tranché.
        ancien = connus.get(_identite(f))
        f["vu_le"] = (ancien or {}).get("vu_le") or aujourdhui
    neufs = {_identite(f) for f in findings}
    reportes = [f for f in anciens if _identite(f) not in neufs and not _ferme(f, arbitrages)]
    if len(findings) + len(reportes) > MAX_FINDINGS:
        # Le plafond force alors l'ARBITRAGE humain au lieu de provoquer un oubli : on
        # refuse d'écrire plutôt que d'écraser des constats que personne n'a tranchés.
        print(f"write_diagnostic : {len(findings)} constat(s) neuf(s) + {len(reportes)} "
              f"reporte(s) = {len(findings) + len(reportes)}, maximum {MAX_FINDINGS}.")
        print("  En attente d'arbitrage (les fermer dans arbitrages.json, "
              "ou les reprendre dans ce diagnostic) :")
        for f in reportes:
            print(f"   - [{f.get('categorie')}] {f.get('cible') or '(sans cible)'} : "
                  f"{str(f.get('titre'))[:90]} (vu le {f.get('vu_le')})")
        return 1
    out = {
        "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "findings": findings + reportes,
    }
    with open(DIAGNOSTIC_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    report = f", {len(reportes)} reporte(s) non arbitre(s)" if reportes else ""
    print(f"write_diagnostic : {len(findings)} constat(s){report} -> "
          f"{os.path.basename(DIAGNOSTIC_PATH)} "
          "(relancer le scan pour propager wiki + routing-hints)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
