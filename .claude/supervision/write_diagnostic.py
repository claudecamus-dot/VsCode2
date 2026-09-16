"""Écriture validée du diagnostic étage 2 (.claude/supervision/diagnostic.json).

Utilisé par la skill `agent-supervisor` : elle compose les constats (LLM), ce script
garantit le schéma que `scan_transcripts.py` consomme (fusion wiki + routing-hints).

Usage : py .claude/supervision/write_diagnostic.py '<json>'   (ou JSON sur stdin)
        py .claude/supervision/write_diagnostic.py --fusionner '<json>'
  Mode par défaut — REGISTRE À ÉTAT (repris de VSCode2 le 2026-09-12, finding
    `flotte:write-diagnostic-du-hub-ecrase-les-findings-non-arbitres`) : avant d'écrire,
    les constats du diagnostic précédent que personne n'a tranchés sont REPORTÉS dans le
    nouveau. Un constat ne disparaît que FERMÉ par un arbitrage, jamais parce qu'un
    diagnostic plus récent a été écrit. Identité d'un constat : `_identite` (cible +
    catégorie), fermeture : `_ferme`.
  --fusionner : conserve tous les findings précédents sauf ceux dont (cible, titre) est
    repris dans ce json (mis à jour sur place), et ajoute les findings vraiment nouveaux.
    Pour écrire dans le diagnostic.json d'un AUTRE dépôt de la flotte
    (AGENT_SUPERVISION_DIAGNOSTIC pointé dessus) sans détruire ses findings ouverts
    propres (finding `flotte:23-items-cadres-sans-canal-arbitrable`, 2026-09-04). Ce mode
    ne consulte AUCUN arbitrage : `ARBITRAGES_PATH` est dérivé de `__file__`, donc du
    HUB — fermer un constat d'un autre dépôt avec les décisions du hub serait faux. Il ne
    plafonne pas non plus le total conservé : la conservation y est l'objet même du mode.
Schéma attendu : {"findings": [{"categorie", "titre", "preuve", ...}]}
  - categorie : ko-repete | inefficacite | agent-mort | interaction |
    verification-manquante | non-convergence | pratique-* | autre. `ko-repete` et
    `inefficacite` avec une `cible` alimentent la liste `prudence` de routing-hints.json
    (l'orchestrateur les évite).
  - titre (str, requis) : le constat en une phrase.
  - preuve (str, requis) : le signal objectif qui l'ancre (comptage, erreur, reprise,
    correction utilisateur) — garde-fou anti-auto-complaisance : jamais de constat
    sans donnée à l'appui.
  - priorite (int 1-5, optionnel, défaut 1), recommandation (str, optionnel).
  - cible (str, requis, non vide) : sans elle un constat reste invisible pour
    point_du_jour.py (findings_non_arbitres saute les findings sans cible) — et aucun
    arbitrage ne peut le fermer, donc il serait reporté à perpétuité.
  - re_challenge (bool, optionnel — repris de VSCode2) : ce constat re-challenge une
    décision déjà arbitrée sur la même cible, avec des données NOUVELLES. Il échappe
    alors au filtre `finding_arbitre` du scan et s'affiche au tableau de bord. Typé
    STRICTEMENT : la chaîne "false" est truthy en Python, elle ne doit pas ouvrir un
    passe-droit sur une décision humaine. Il exige une `cible` — exigence que la `cible`
    requise non vide ci-dessus couvre déjà pour tout finding : pas de second contrôle,
    une branche qui ne peut pas s'atteindre n'est pas un garde-fou.
  - proposition (str, optionnel — incrément C « challenger ») : le changement concret
    proposé (nouveau déclencheur de skill, contrat de playbook amendé, désinstallation…),
    en une phrase ou un mini-diff inline. Rendue dans le wiki avec le constat ;
    JAMAIS appliquée par le superviseur — l'humain arbitre, l'orchestrateur applique
    la version validée (gouvernance : règle R4 de CLAUDE.md, et
    .claude/skills/agent-orchestrator/SKILL.md § 2 bis).
  - vu_le (str « AAAA-MM-JJ », posé par CE script, jamais par l'appelant) : date de
    PREMIÈRE vue du constat. Un constat reconduit la conserve — c'est ce qui distingue
    « vu hier et toujours pas tranché » de « trouvé aujourd'hui », et ce qui date la
    fenêtre d'arbitrage (cf. `_ferme`).

`generated` est posé par ce script (horodatage courant). Gitignoré — donnée machine.
Env (tests) : AGENT_SUPERVISION_DIAGNOSTIC, AGENT_SUPERVISION_ARBITRAGES.
Conception : docs/reflexions/conception-agent-supervisor.md (le POURQUOI, repris de
VSCode2 le 2026-09-02) ; le QUOI operationnel est dans .claude/skills/agent-supervisor/
SKILL.md. Entre le 2026-08-31 et cette reprise, ce champ a pointe un docs/reflexions qui
n'existait pas.
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
    # Volet 1 — usage des agents
    "ko-repete", "inefficacite", "agent-mort", "interaction",
    "verification-manquante", "non-convergence",
    # Volet 2 — pratiques d'ingénierie (test, dev, revue, design)
    "pratique-test", "pratique-dev", "pratique-revue", "pratique-design",
    # Volet 2 — documentation et cadrage produit
    "pratique-doc", "pratique-produit",
    "autre",
)
# Plafond de la skill agent-supervisor (§ « 5 constats max, priorisés ») — appliqué ici
# parce que le scan n'affiche que les 5 premiers : au-delà, un constat se perdait sans
# trace. Repris de VSCode2 avec le registre à état : les deux vont ensemble, puisque
# c'est le report des non-arbitrés qui fait monter le total.
MAX_FINDINGS = 5


def _identite(finding: dict) -> tuple:
    """Ce qui fait qu'un constat est « le même » d'un diagnostic à l'autre.

    `(cible, categorie)` — exactement la granularité à laquelle un arbitrage ferme un
    constat côté scan (`finding_arbitre` + `_couvre`). Le titre serait plus fin mais
    reconduirait un doublon à chaque reformulation ; la seule cible confondrait deux
    constats de nature différente sur le même fichier.
    Un constat SANS cible ne peut être fermé par aucun arbitrage : on le distingue alors
    par son titre, faute de mieux, pour ne pas fusionner deux constats indépendants. Ce
    repli n'est pas mort malgré la `cible` requise à l'écriture : les ANCIENS relus sur
    disque n'ont jamais été validés par cette version du script."""
    cible = str(finding.get("cible") or "").strip()
    if not cible:
        cible = "~" + str(finding.get("titre") or "")
    return (cible, str(finding.get("categorie") or ""))


def _charger_arbitrages() -> list:
    """Décisions humaines (fichier versionné, JAMAIS écrit ici). Même lecture tolérante
    que le scan (`load_arbitrages`) : un fichier absent ou illisible ne ferme aucun
    constat — la direction sûre, l'erreur inverse perdant un constat en le croyant
    tranché."""
    try:
        with open(ARBITRAGES_PATH, encoding="utf-8") as fh:
            entries = json.load(fh).get("arbitrages", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("cible") and e.get("decision")]


def _couvre(arbitrage: dict, categorie: str) -> bool:
    """Miroir de `_couvre` dans scan_transcripts.py : `categories` absent ferme tout, une
    liste ferme exactement ces catégories, un champ mal formé ne ferme rien (un `in` sur
    une chaîne matcherait par sous-chaîne, silencieusement faux)."""
    cats = arbitrage.get("categories")
    if cats is None:
        return True
    return isinstance(cats, list) and categorie in cats


def _ferme(finding: dict, arbitrages: list) -> bool:
    """Un arbitrage postérieur à la PREMIÈRE vue du constat le ferme.

    La comparaison porte sur `vu_le`, pas sur la date du diagnostic courant : sans quoi un
    constat reconduit repousserait indéfiniment sa propre fenêtre d'arbitrage et ne serait
    jamais reconnu comme tranché.
    Différence assumée avec `finding_arbitre` du scan, qui ferme dès qu'un arbitrage
    couvrant existe, quelle que soit sa date : ici la décision de SUPPRIMER un constat du
    registre se prend, donc on exige la preuve que l'humain a vu ce constat-là — un
    arbitrage antérieur à sa première vue n'a pas pu le trancher. C'est aussi ce qui
    protège un `re_challenge` sans avoir à le traiter à part : il naît avec un `vu_le` du
    jour, donc l'arbitrage qu'il conteste lui est antérieur et ne le ferme pas.
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
    """(constats du diagnostic précédent, sa date d'écriture, avertissement éventuel).

    TROIS états et non deux (apport du hub conservé, correctif du 2026-08-31) : ABSENT
    (premier diagnostic — muet, il n'y a rien à perdre), SAIN, et ILLISIBLE — bruyant,
    car c'est exactement le cas où le report des non-arbitrés ne peut PAS jouer. Le
    `except (OSError, ValueError): anciens = []` d'origine confondait les trois et
    taisait l'alarme quand elle servait le plus."""
    try:
        with open(DIAGNOSTIC_PATH, encoding="utf-8") as fh:
            precedent = json.load(fh)
    except FileNotFoundError:
        return [], "", ""
    except (OSError, ValueError) as exc:
        return [], "", (
            f"write_diagnostic AVERTISSEMENT : le diagnostic precedent est ILLISIBLE "
            f"({exc}) — aucun constat non arbitre ne peut en etre REPORTE. Le fichier va "
            "etre remplace : recuperer la version saine (git / sauvegarde) si des "
            "constats ouverts doivent etre repris.")
    anciens = precedent.get("findings") if isinstance(precedent, dict) else None
    if not isinstance(anciens, list):
        return [], "", (
            "write_diagnostic AVERTISSEMENT : le diagnostic precedent est ILLISIBLE "
            "(structure inattendue, pas de liste 'findings') — meme consequence : aucun "
            "constat non arbitre ne peut en etre REPORTE.")
    return ([f for f in anciens if isinstance(f, dict)],
            str(precedent.get("generated") or "")[:10], "")


def main(argv) -> int:
    # Console Windows en cp1252 : le JSON arrive/repart toujours en UTF-8.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    fusionner = "--fusionner" in argv
    positionnels = [a for a in argv if a != "--fusionner"]
    raw = positionnels[0] if positionnels else sys.stdin.read()
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
        if not str(f.get("cible") or "").strip():
            print(f"write_diagnostic : finding #{i} sans cible "
                  "(un constat sans cible non vide reste invisible pour point_du_jour.py, "
                  "et aucun arbitrage ne peut le fermer)")
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
    # --- Registre à état (repris de VSCode2, 2026-09-12) -------------------------------
    # Avant : le hub AVERTISSAIT que des constats ouverts disparaissaient, puis les
    # écrasait. Mesuré chez VSCode2 (commentaire d'origine) : des 5 constats du
    # 2026-09-01T23:00, UN SEUL avait été arbitré quand l'écriture du 2026-09-02T12:12
    # les a tous remplacés. La boucle propose→arbitre→applique fuyait à son premier
    # maillon. Un avertissement que rien ne lit n'est pas un garde-fou.
    anciens, date_precedente, avertissement = _precedent()
    if avertissement:
        print(avertissement)
    aujourdhui = datetime.date.today().isoformat()
    # Un diagnostic écrit avant l'existence de `vu_le` n'en porte pas : sa date d'écriture
    # fait foi. Sans ce repli, un `vu_le` vide rendrait `date >= ""` vrai pour n'importe
    # quel arbitrage et refermerait en silence exactement ce qu'on cherche à sauver.
    for f in anciens:
        f.setdefault("vu_le", date_precedente or aujourdhui)
    connus = {_identite(f): f for f in anciens}
    for f in findings:
        # Constat reconduit : il garde sa date de première vue — c'est elle qui dit depuis
        # quand l'humain ne l'a pas tranché. `vu_le` est posé ICI, jamais accepté de
        # l'appelant : un superviseur qui se date lui-même pourrait repousser sa propre
        # fenêtre d'arbitrage.
        ancien = connus.get(_identite(f))
        f["vu_le"] = (ancien or {}).get("vu_le") or aujourdhui
    if fusionner:
        # Fusion : les findings precedents non repris sont CONSERVES tels quels, seuls
        # ceux dont (cible, titre) correspond exactement a un finding de cette passe sont
        # remplaces (mise a jour intentionnelle, pas une perte). Ni arbitrages ni plafond
        # combine ici : cf. docstring du module.
        nouvelles_cles = {(f.get("cible"), f.get("titre")) for f in findings}
        conserves = [f for f in anciens
                     if (f.get("cible"), f.get("titre")) not in nouvelles_cles]
        remplaces = [f for f in anciens
                     if (f.get("cible"), f.get("titre")) in nouvelles_cles]
        if remplaces:
            print(f"write_diagnostic (fusion) : {len(remplaces)} finding(s) existant(s) "
                  "mis a jour (cible+titre identiques) :")
            for f in remplaces:
                print(f"  - {f.get('cible', '?')} : {f.get('titre', '?')}")
        sortants = findings
        findings = conserves + findings
        print(f"write_diagnostic (fusion) : {len(conserves)} finding(s) precedent(s) "
              f"conserve(s) tel(s) quel(s), {len(sortants)} ecrit(s) cette passe.")
        reportes = []
    else:
        arbitrages = _charger_arbitrages()
        neufs = {_identite(f) for f in findings}
        restants = [f for f in anciens if _identite(f) not in neufs]
        reportes = [f for f in restants if not _ferme(f, arbitrages)]
        fermes = [f for f in restants if _ferme(f, arbitrages)]
        if fermes:
            print(f"write_diagnostic : {len(fermes)} constat(s) precedent(s) FERME(s) par "
                  "un arbitrage posterieur a leur premiere vue, non reporte(s) :")
            for f in fermes:
                print(f"  - [{f.get('categorie')}] {f.get('cible')} : "
                      f"{str(f.get('titre'))[:90]} (vu le {f.get('vu_le')})")
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
    # Ecriture atomique (meme motif que canon/log_run.solder) : un "w" direct laisse
    # un diagnostic.json tronque si l'ecriture est interrompue — et un diagnostic
    # tronque est precisement ce qui empeche de REPORTER quoi que ce soit au tour
    # suivant (cf. _precedent : etat ILLISIBLE).
    tmp = DIAGNOSTIC_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, DIAGNOSTIC_PATH)
    report = f", {len(reportes)} reporte(s) non arbitre(s)" if reportes else ""
    print(f"write_diagnostic : {len(findings)} constat(s){report} -> "
          f"{os.path.basename(DIAGNOSTIC_PATH)} "
          "(relancer le scan pour propager wiki + routing-hints)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
