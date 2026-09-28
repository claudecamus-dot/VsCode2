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
  - owner (str, posé par CE script s'il manque — défaut : la `cible`) : QUI doit traiter
    ce constat. Il n'y a qu'un opérateur humain sur la flotte, donc le défaut ne nomme
    personne de nouveau ; le champ existe pour être CHANGÉ (grille d'audit fournie le
    2026-09-20 : « pour chaque écart : preuve, risque, impact, recommandation,
    propriétaire, échéance »). Un constat sans propriétaire ni date n'expire jamais :
    11 des 19 constats attendaient dans cet état.
  - echeance (str « AAAA-MM-JJ », posée par CE script s'il manque) : `vu_le` +
    SEUIL_ECHEANCE_FINDING_JOURS. Dépassée, `point_du_jour.py` la NOMME au démarrage.
    L'appelant peut fournir la sienne (plus courte ou plus longue) : elle est respectée.
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

# `_preuve_mesuree` vit dans log_arbitrage.py (meme dossier, aucun effet de bord a
# l'import : le module n'y calcule que des chemins et garde son `if __name__`).
# IMPORTE et non recopie : l'oracle de « preuve mesuree » doit etre LE MEME a
# l'arbitrage et a l'ecriture du constat, sinon la plus laxiste des deux sert.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from log_arbitrage import _preuve_mesuree  # noqa: E402

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
    # Volet 2 — securite (agent-securite installe le 2026-09-13, commit d9b838e).
    # Absente jusqu'au 2026-09-21 : un arbitrage du 2026-09-11 portant cette
    # categorie (VSCode1,VSCode2:permissions-hors-git-exec-arbitraire) etait
    # inoperant depuis son ecriture -- _couvre() ne peut jamais matcher une
    # categorie hors vocabulaire, et write_diagnostic refusait meme d'ECRIRE un
    # finding qui la porterait. Distincte de "securite" (volet 3, dimension
    # d'audit) : celle-ci couvre les PRATIQUES d'ingenierie securite, pas le
    # niveau mesure par audit-technique sur un projet donne.
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
# Plafond de la skill agent-supervisor (§ « 5 constats max, priorisés ») — appliqué ici
# parce que le scan n'affiche que les 5 premiers : au-delà, un constat se perdait sans
# trace. Repris de VSCode2 avec le registre à état : les deux vont ensemble, puisque
# c'est le report des non-arbitrés qui fait monter le total.
MAX_FINDINGS = 5

# ECHEANCE D'UN CONSTAT (volet gouvernance, 2026-09-20). Mesure qui fixe la valeur, faite
# par script sur arbitrages.json + diagnostic.json (jamais en ouvrant les fichiers) : sur
# les 13 constats du registre courant qu'un arbitrage a fermes APRES leur premiere vue, le
# delai median de decision est de 0 jour, le maximum de 3 jours ; le plus ancien constat
# encore ouvert a 8 jours. Un seuil de 14 jours est donc ~4,7 fois le pire delai
# d'arbitrage reellement observe : il ne crie pas sur le rythme normal, il n'attrape que
# l'abandon. Le raccourcir a 3 j (le maximum mesure) ferait sonner la moitie du registre
# chaque semaine — un rappel qu'on apprend a ignorer, motif deja paye
# (SEUIL_DERIVE_BLOQUANTE_JOURS dans point_du_jour.py).
SEUIL_ECHEANCE_FINDING_JOURS = 14


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


def _gouvernance(finding: dict, seuil: int = SEUIL_ECHEANCE_FINDING_JOURS) -> dict:
    """Pose `owner` et `echeance` sur un constat qui n'en porte pas — et JAMAIS sur un
    constat qui en porte deja.

    Retro-compatibilite : les 19 constats ecrits avant ce champ les recoivent a la
    prochaine ecriture, sans qu'aucun autre champ ne bouge. Le defaut d'`owner` est la
    `cible` : il n'y a qu'un operateur humain, le champ n'est pas la pour repartir une
    charge mais pour etre MODIFIE sans toucher au reste du constat.

    L'echeance se calcule sur `vu_le` (premiere vue), pas sur la date d'ecriture : sinon
    un constat reconduit repousserait sa propre echeance a chaque diagnostic et
    n'expirerait jamais — exactement le defaut que `_ferme` evite deja pour la fenetre
    d'arbitrage. Une valeur fournie par l'appelant est respectee (une echeance plus
    courte est une decision, pas une erreur) ; une valeur vide ou illisible est
    REMPLACEE par le defaut, parce qu'une echeance illisible n'expire jamais non plus.
    """
    if not str(finding.get("owner") or "").strip():
        finding["owner"] = str(finding.get("cible") or "").strip() or "(sans cible)"
    if not str(finding.get("echeance") or "").strip():
        try:
            base = datetime.date.fromisoformat(str(finding.get("vu_le") or "")[:10])
        except ValueError:
            base = datetime.date.today()
        finding["echeance"] = (base + datetime.timedelta(days=seuil)).isoformat()
    return finding


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


# Bascule de l'amnistie de l'heritage (cf. `_couvre`) : avant cette date, un
# arbitrage sans `categories` ne ferme rien ; apres, `log_arbitrage.py` garantit
# qu'il y en a toujours un.
AMNISTIE_HERITAGE = "2026-09-19"


def _couvre(arbitrage: dict, categorie: str) -> bool:
    """Miroir de `_couvre` dans scan_transcripts.py : `categories` absent ferme tout, une
    liste ferme exactement ces catégories, un champ mal formé ne ferme rien (un `in` sur
    une chaîne matcherait par sous-chaîne, silencieusement faux)."""
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
    # PASSE SANS CONSTAT (2026-09-24). Un projet sain ne pouvait pas enregistrer sa passe
    # d'etage 2 : liste vide refusee, donc la cadence (14 j) restait en retard sur
    # VSCode/VSCode1/VSCode4 alors que la passe du jour les avait verifies (tests, lint,
    # git) — seule issue offerte : inventer un constat. `rien_a_signaler` date la passe
    # sans toucher aux constats existants, et sa justification subit le MEME controle
    # de preuve mesuree qu'un constat : « tout va bien » en prose ne passe pas.
    rien = str(diag.get("rien_a_signaler") or "").strip() if isinstance(diag, dict) else ""
    if isinstance(findings, list) and not findings and rien:
        if not _preuve_mesuree(rien):
            print(f"write_diagnostic : rien_a_signaler NON MESURE — « {rien[:160]} »\n"
                  "  Citer les commandes rejouees et leurs chiffres (tests, lint, git).")
            return 1
        anciens, _date_precedente, avertissement = _precedent()
        if avertissement:
            print(avertissement)
        maintenant = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        out = {"generated": maintenant, "findings": anciens,
               "passe_sans_constat": {"date": maintenant, "preuve": rien}}
        tmp = DIAGNOSTIC_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, DIAGNOSTIC_PATH)
        print(f"write_diagnostic : passe sans constat enregistree ({len(anciens)} constat(s) "
              f"existant(s) conserve(s)) -> {DIAGNOSTIC_PATH}")
        return 0
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
        # La PRESENCE de `preuve` ne prouvait rien : une phrase d'opinion passait.
        # Cas reels (2026-09-22) : le finding « CRLF » invente et la fausse alerte
        # « facteur 33 », tous deux ecrits avec une `preuve` en prose. Meme oracle
        # que `log_arbitrage._preuve_mesuree` — importe, jamais recopie : deux
        # copies d'une regle finissent par diverger, et c'est la plus laxiste qui
        # sert. Le controle porte sur ce qui est ECRIT DANS CETTE PASSE, jamais sur
        # les findings REPORTES du diagnostic precedent (ceux-la sont deja stockes,
        # les refuser retroactivement ferait perdre des constats ouverts).
        if not _preuve_mesuree(str(f.get("preuve") or "")):
            print(f"write_diagnostic : finding #{i} preuve NON MESUREE — "
                  f"« {str(f.get('preuve'))[:160]} »\n"
                  "  Une preuve doit porter un chiffre (compte, sha, date, nombre de "
                  "tests) ou une commande rejouable (grep, pytest, git, py, --check). "
                  "Une phrase d'opinion n'ancre pas un constat : va mesurer, puis "
                  "reecris la preuve avec la commande qui l'a produite.")
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
    # UN CONSTAT NEUF NAÎT VISIBLE (finding `VScode5:constat-neuf-masque-par-arbitrage-
    # anterieur`, arbitré le 2026-09-19). Côté scan, `finding_arbitre()` masque dès qu'un
    # arbitrage partage (cible, catégorie) : avec 250 décisions accumulées au hub, presque
    # toute paire est déjà couverte, si bien que 14 des 15 constats du diagnostic du 18/09
    # — y compris ceux écrits le jour même — naissaient masqués par des décisions rendues
    # sur un tout autre sujet. Le canon prévoit déjà l'échappatoire (`re_challenge: true`
    # ne cède qu'à un arbitrage du jour du diagnostic ou postérieur), mais RIEN ne la
    # posait : ni ce script, ni la skill `agent-supervisor`. On la pose ici, et seulement
    # pour une paire (cible, titre) JAMAIS VUE — un constat reconduit garde exactement le
    # sort que l'humain lui a donné, et un `re_challenge` explicite de l'appelant prime.
    # C'est l'option (b) du finding ; l'option (a) (masquer par titre strict) a été
    # écartée le 2026-09-18 (commit bb61f24) : aucun des 250 arbitrages ne porte de titre.
    paires_connues = {(f.get("cible"), f.get("titre")) for f in anciens}
    for f in findings:
        if "re_challenge" not in f and (f.get("cible"), f.get("titre")) not in paires_connues:
            f["re_challenge"] = True
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
    # Gouvernance (2026-09-20) : tout constat ECRIT ou RECONDUIT porte un proprietaire et
    # une echeance. Pose ici, au dernier moment, pour couvrir d'un seul geste les trois
    # provenances — constats neufs, reportes non arbitres, et conserves du mode fusion.
    for f in findings + reportes:
        _gouvernance(f)
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
