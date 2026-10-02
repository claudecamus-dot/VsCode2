"""Scoring engine of the « Évaluation agentic » device — scale v3.

PURE module: stdlib only, no import of ``scan_projets`` (the import direction is
``scan_projets -> evaluation_agentic -> detection_generique``). It turns the
output of ``detection_generique.detecter(chemin)`` into anchored notes, so the
module and its detector ship together in the deployment kit.

Scale v2 (atelier n°7, user-arbitrated 2026-09-28): two referentials, both
PER PROJECT and project-agnostic —
  A « Pratiques de développement » (13 criteria),
  B « Pratiques agentic » (10 criteria in v2, 11 in v3).
Scale v3 (user-arbitrated 2026-09-29, « ils sont notés »): B gains ONE graded
criterion, ``structure_mandats_agents`` (reference template of agent mandates,
docs/reflexions/gabarit-agent.md); nothing else changes, so a v2 note is the
same computation without ``CRITERES_AJOUTES_V3`` (``globale_sans_ajouts``).
Every criterion's texts (name, definition, signals, why + public source, what
the score allows / does not allow to conclude) are READ from the docstring of
its detection function: one source, never a second copy to keep in sync.

Measure -> note (declared per criterion in ``MODES``):
  binaire     "mesure" + signals = present -> 10 ; "mesure" + no signal -> 2.
  echelle     niveau_orchestration: detector ``niveau`` 0..5 -> 2/2/4/6/8/10.
  ratio_inv   blocages_traces: share of failed executions, lower is better, 5 bands.
  part        structure_mandats_agents: mean share of template blocks present in
              the agent mandates, higher is better, 5 bands (SEUILS_STRUCTURE).
  « non mesuré » and « non applicable » (solution_agentic_maitrisee) are NEVER a number and never
  enter a mean (C5).

Constraints (C1-C5, atelier n°6, unchanged):
  C1 every criterion's ``fonction_mesure`` is the name of a function of
     ``detection_generique.FONCTIONS`` (checked at import AND by the tests).
  C2 security lock: an open priority-1 ``securite`` finding for the project (or
     ``flotte:*``) -> ``globale`` is the string ``"bloquant"``, never a number.
     Only an ACCEPTED arbitration closes such a finding; a refusal does not.
  C3 every score carries ``mesure_le`` (ISO, written by the CALLER — the scan —
     never by the detector) and ``perime``; a stale score renders « non mesuré ».
  C4 a note is never emitted without its ``criteres`` list (same dict).
  C5 absent source / undetermined detection -> « non mesuré ».

The OLD hub axes (reprises, refus de gardes, salles, tokens) left the scored
referential: they feed an ``informatif`` « dispositif » block read from hub
files only (``evaluer_hub``), never averaged, never shipped as a criterion.

Snapshots written before v2 keep their original version marker (the string v1) — history
is append-only and is NEVER re-scored (series break shown by the page).

Output shape::

    {"version_bareme": 3, "genere_le": ISO,
     "projets": {<nom>: {"axes": {"pratiques_dev": AXE, "pratiques_agentic": AXE},
                         "globale": float | "bloquant" | "non mesuré",
                         "axes_non_mesures": [...], "verrou_securite": {...},
                         "mesure_le": ISO | None}},
     "hub": {same keys, axes = the informatif « dispositif » block}}
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detection_generique as dg  # noqa: E402  (sibling module, stdlib only)

VERSION_BAREME = 3
# Criteria added by scale v3: a v2 note is the same mean without them.
CRITERES_AJOUTES_V3 = frozenset({"structure_mandats_agents"})
NON_MESURE = "non mesuré"
NON_APPLICABLE = "non applicable"
BLOQUANT = "bloquant"
NIVEAUX = (2, 4, 6, 8, 10)
PEREMPTION_JOURS = 7
FENETRE_RUNS = 50
FENETRE_JOURS = 7

NOTE_2_ETATS = ("ce critère n'a que 2 niveaux notés — présent (10) ou absent (2) ; "
                "quand le dépôt ne permet pas de conclure, il est « non mesuré », "
                "jamais noté")

# ------------------------------------------------ texts read from the detector
_SECTIONS = ("Définition", "Signaux exacts", "Pourquoi", "Permet de conclure",
             "Ne permet pas de conclure")
_RX_SECTION = re.compile(rf"^\s*({'|'.join(_SECTIONS)}) :\s*", re.M)


def textes_detecteur(nom):
    """{titre, Définition, Signaux exacts, ...} parsed from the function docstring."""
    doc = dg.FONCTIONS[nom].__doc__ or ""
    titre, _, reste = doc.strip().partition("\n")
    out = {"titre": titre.strip().rstrip(".")}
    morceaux = _RX_SECTION.split(reste)
    for cle, val in zip(morceaux[1::2], morceaux[2::2], strict=True):
        out[cle] = " ".join(val.split())
    manquants = [s for s in _SECTIONS if not out.get(s)]
    assert not manquants, (nom, manquants)
    return out


# Plain-language question PER CRITERION (review fix 5), for an outside team.
QUESTIONS = {
    "tests_automatises": "Le projet contient-il des tests qu'une machine peut rejouer ?",
    "couverture_configuree": "L'équipe peut-elle mesurer quelle part du code ses tests exécutent ?",
    "test_artefact_reel": "Au moins un test vérifie-t-il le résultat final comme l'utilisateur le reçoit ?",
    "co_evolution_tests": "Quand le code change, les tests changent-ils avec lui ?",
    "code_documente": "Les fonctions publiques du code disent-elles ce qu'elles font ?",
    "integration_continue": "Une chaîne automatique vérifie-t-elle chaque envoi, et les livraisons sont-elles repérées ?",
    "linter_configure": "Un outil attrape-t-il mécaniquement les défauts courants avant la relecture ?",
    "revue_avant_integration": "Un changement est-il approuvé par une autre personne que son auteur avant d'entrer ?",
    "epics_us_bien_formees": "Le besoin est-il écrit en user stories exploitables (rôle, critères, dépendances) ?",
    "criteres_acceptance": "Chaque story dit-elle à quoi on reconnaît qu'elle est finie ?",
    "securite_base": "Les secrets restent-ils hors du dépôt ?",
    "decisions_conception_tracees": "Les choix d'architecture sont-ils écrits, datés et versionnés ?",
    "tracabilite_demande_livrable": "Peut-on remonter d'un changement de code à la demande qu'il sert ?",
    "cadre_agentic_versionne": "Les instructions données aux assistants d'IA sont-elles versionnées avec le code ?",
    "garde_fous_agentic": "Des barrières empêchent-elles un assistant de faire des dégâts, et son travail reste-t-il réversible ?",
    "solution_agentic_maitrisee": "Si le produit appelle lui-même un modèle d'IA, ces appels sont-ils bornés et contrôlés ?",
    "amelioration_continue": "Le cadre donné aux assistants est-il révisé régulièrement ?",
    "gestion_prompts": "Les prompts sont-ils gérés comme des fichiers versionnés, voire évalués ?",
    "niveau_orchestration": "Jusqu'où le projet organise-t-il le travail de ses assistants ?",
    "blocages_traces": "Les exécutions des assistants sont-elles tracées, et combien échouent ?",
    "conformite_resultats": "Le travail confié aux assistants est-il livré du premier coup ?",
    "politique_modele_effort": "Le projet choisit-il le modèle ou l'effort selon la tâche, et suit-il le coût ?",
    "validation_humaine_tracee": "La recette d'un livrable par une autre personne que son auteur est-elle tracée ?",
    "structure_mandats_agents": "Les mandats des agents suivent-ils la structure de référence (fin écrite, outils décrits, ton, interdits, exemple, rappel final) ?",
}

# Measure -> note, declared per criterion.
MODES = {nom: "binaire" for ref in dg.REFERENTIELS.values() for nom in ref}
MODES["niveau_orchestration"] = "echelle"
MODES["blocages_traces"] = "ratio_inv"
MODES["structure_mandats_agents"] = "part"

SEUILS_ECHECS = (0.05, 0.1, 0.2, 0.35)  # estimated thresholds, not published ones
_ECHELLE = {0: 2, 1: 2, 2: 4, 3: 6, 4: 8, 5: 10}

_COMB_BINAIRE = ("10 si au moins un des signaux décrits est trouvé (pratique présente), "
                 "2 si aucun ne l'est (absente) ; « non mesuré » quand le dépôt ne "
                 "permet pas de conclure — hors moyenne.")
_COMBINAISONS = {
    "niveau_orchestration": (
        "Le niveau atteint sans trou fait la note : niveaux 0-1 → 2, 2 → 4, 3 → 6, "
        "4 → 8, 5 → 10. Avertissement : les niveaux 4 et 5 lisent des déclarations "
        "(fichiers présents), pas un comportement observé."),
    "blocages_traces": (
        "La note suit la part d'exécutions en ÉCHEC FRANC dans le journal : 10 sous 5 %, "
        "puis 8, 6 et 4 aux seuils 5 / 10 / 20 %, et 2 à partir de 35 % (seuils estimés). "
        "Une exécution « partielle » ou en attente de validation n'est PAS un échec. "
        "Sans journal d'exécution : « non mesuré »."),
    "structure_mandats_agents": (
        "Critère conditionnel et gradué : « non applicable » (hors moyenne) sans "
        "définition d'agent. Sinon, la part moyenne des 6 blocs lisibles sans jugement "
        "présents dans chaque mandat fait la note : 10 dès 95 %, puis 8, 6 et 4 aux "
        "seuils 65 / 45 / 25 %, 2 en dessous (seuils estimés). Mesure la présence du "
        "texte, pas le comportement de l'agent."),
    "solution_agentic_maitrisee": (
        "Critère conditionnel : « non applicable » (hors moyenne) si le produit ne "
        "déclare aucune dépendance à un modèle d'IA ; sinon 10 si au moins 2 des 4 "
        "protections sont trouvées, 2 sinon. Simple repérage : la maîtrise réelle "
        "relève d'un audit de code."),
    "gestion_prompts": (
        _COMB_BINAIRE + " Le score repose sur des DÉCLARATIONS (fichiers présents) ; "
        "l'absence de jeu d'évaluation reste « non mesuré », jamais une note devinée."),
    "conformite_resultats": (
        _COMB_BINAIRE + " Présent si au moins la moitié des exécutions closes sont "
        "livrées du premier coup. Le score repose sur ce que le journal DÉCLARE."),
    "politique_modele_effort": (
        _COMB_BINAIRE + " Présent si au moins 2 modèles ou efforts différents sont "
        "déclarés. Le score note une DÉCLARATION, pas une preuve d'application."),
}


def _ancres(nom, t):
    if MODES[nom] == "echelle":
        return {2: "niveau 0 ou 1 — au plus un cadre d'instructions écrit",
                4: "niveau 2 — des agents spécialisés sont définis",
                6: "niveau 3 — un orchestrateur est déclaré",
                8: "niveau 4 — des procédures (playbooks) sont décrites",
                10: "niveau 5 — un journal d'exécution est tenu"}
    if MODES[nom] == "ratio_inv":
        return {10: "moins de 5 % d'exécutions en échec", 8: "de 5 à 10 %",
                6: "de 10 à 20 %", 4: "de 20 à 35 %", 2: "35 % ou plus"}
    if MODES[nom] == "part":
        return {10: "95 % ou plus des blocs de la structure présents",
                8: "de 65 à 95 %", 6: "de 45 à 65 %", 4: "de 25 à 45 %",
                2: "moins de 25 %"}
    return {10: t["Définition"],
            2: "aucun des signaux décrits n'est trouvé dans le dépôt"}


def _crit(nom):
    """Declare one criterion from its detection function (single source)."""
    t = textes_detecteur(nom)
    observe = [f"{s.strip().rstrip('.')} — lu dans le dépôt"
               for s in re.split(r"\s*;\s*", t["Signaux exacts"]) if s.strip()]
    d = {"nom": nom, "libelle": t["titre"], "question": QUESTIONS[nom],
         "observe": observe,
         "combinaison": _COMBINAISONS.get(nom, _COMB_BINAIRE),
         "attendu": t["Définition"], "source": t["Pourquoi"],
         "conclut": t["Permet de conclure"],
         "ne_conclut_pas": t["Ne permet pas de conclure"],
         "fonction_mesure": nom, "mode": MODES[nom], "poids": 1,
         "ancres": _ancres(nom, t)}
    if MODES[nom] == "binaire":
        d["note_ancres"] = NOTE_2_ETATS
    if MODES[nom] == "ratio_inv":
        d["seuils"], d["croissant"] = SEUILS_ECHECS, False
    if MODES[nom] == "part":
        d["seuils"], d["croissant"] = dg.SEUILS_STRUCTURE, True
    return d


BAREMES = {
    "pratiques_dev": {
        "libelle": "Pratiques de développement", "portee": "projet",
        "question": "Le projet a-t-il en place les pratiques de base d'une équipe qui "
                    "développe sérieusement : besoin écrit, décisions tracées, "
                    "relecture, secrets protégés, tests, intégration ?",
        "criteres": [_crit(n) for n in dg.REFERENTIELS["A"]],
    },
    "pratiques_agentic": {
        "libelle": "Pratiques agentic", "portee": "projet",
        "question": "Le travail confié aux assistants d'IA est-il cadré, borné, "
                    "tracé et vérifié par un humain ?",
        "criteres": [_crit(n) for n in dg.REFERENTIELS["B"]],
    },
}
AXES_PROJET = ("pratiques_dev", "pratiques_agentic")
for _b in BAREMES.values():
    for _c in _b["criteres"]:
        assert _c["fonction_mesure"] in dg.FONCTIONS, _c["nom"]  # C1


# ------------------------------------------- informatif « dispositif » block
def _crit_hub(nom, fonction, ancres, libelle, question, observe, combinaison,
              attendu, **extra):
    d = {"nom": nom, "libelle": libelle, "question": question,
         "observe": list(observe), "combinaison": combinaison,
         "attendu": attendu, "source": "propre au dispositif de ce hub (hors référentiel)",
         "fonction_mesure": fonction, "poids": 1, "ancres": ancres}
    d.update(extra)
    return d


DISPOSITIF = {
    "fonctionnement": {
        "libelle": "Reprises par demande (dispositif)", "portee": "hub", "informatif": True,
        "question": "Quand on confie un travail à l'assistant, faut-il s'y reprendre plusieurs fois ?",
        "criteres": [_crit_hub(
            "reprises_par_run", "mesure_reprises",
            {10: "moyenne < 0.2 reprise/run", 8: "[0.2, 0.4[", 6: "[0.4, 0.6[",
             4: "[0.6, 0.8[", 2: ">= 0.8"},
            "Reprises par demande", "Combien de reprises par demande traitée ?",
            ["Le champ « reprises » des 50 derniers runs — journal d'orchestration du hub"],
            "Moyenne des reprises : 10 sous 0.2, puis 8, 6, 4 aux seuils 0.2/0.4/0.6, 2 dès 0.8.",
            "Un travail confié aboutit du premier coup.",
            seuils=(0.2, 0.4, 0.6, 0.8), croissant=False)],
    },
    "refus_gardes": {
        "libelle": "Refus de gardes, 7 j (dispositif)", "portee": "hub", "informatif": True,
        "question": "Les protections automatiques bloquent-elles souvent le travail ?",
        "criteres": [_crit_hub(
            "refus_7j", "mesure_refus",
            {10: "0 refus", 8: "[1, 3[", 6: "[3, 6[", 4: "[6, 11[", 2: ">= 11"},
            "Refus de gardes sur 7 jours", "Combien de refus de garde en 7 jours ?",
            ["Une ligne datée des 7 derniers jours — journal des refus du hub"],
            "10 pour 0 refus, puis 8, 6, 4 aux seuils 1/3/6, 2 dès 11.",
            "Les protections bloquent rarement le travail.",
            seuils=(1, 3, 6, 11), croissant=False)],
    },
    "salles": {
        "libelle": "Salles (dispositif)", "portee": "hub", "informatif": True,
        "question": "Les assistants consultés en parallèle rendent-ils tous leur avis ?",
        "criteres": [
            _crit_hub("voix_rendues", "mesure_salles_rendues",
                      {10: "part ok >= 0.95", 8: "[0.85, 0.95[", 6: "[0.7, 0.85[",
                       4: "[0.5, 0.7[", 2: "< 0.5"},
                      "Voix de salle rendues", "Quelle part des voix est rendue ?",
                      ["Les étapes « salle » des 50 derniers runs — journal d'orchestration du hub"],
                      "Part de voix rendues : 10 dès 0.95, puis 8, 6, 4 aux seuils 0.85/0.7/0.5.",
                      "Chaque voix consultée rend son avis.",
                      seuils=(0.5, 0.7, 0.85, 0.95), croissant=True),
            _crit_hub("clotures_forcees_7j", "mesure_clotures",
                      {10: "0 clôture forcée", 8: "[1, 2[", 6: "[2, 4[", 4: "[4, 6[",
                       2: ">= 6"},
                      "Clôtures forcées (7 jours)", "Combien de salles closes de force en 7 jours ?",
                      ["Une dérogation datée des 7 derniers jours — journal des dérogations du hub"],
                      "10 pour 0, puis 8, 6, 4 aux seuils 1/2/4, 2 dès 6.",
                      "Une discussion se conclut d'elle-même.",
                      seuils=(1, 2, 4, 6), croissant=False),
        ],
    },
    "tokens": {
        "libelle": "Tokens (dispositif : présence des données)", "portee": "hub",
        "informatif": True,
        "question": "Sait-on ce que la consommation de l'assistant a coûté ?",
        "criteres": [_crit_hub(
            "donnees_tokens", "mesure_tokens",
            {10: "total + par_jour + par_modele", 8: "total + par_jour",
             6: "total seul", 4: "0 message", 2: "sans total exploitable"},
            "Présence des données de consommation", "Les données de coût existent-elles ?",
            ["Les clés total / par_jour / par_modele — fichier de tokens du hub"],
            "10 avec total, jour et modèle ; 8 sans modèle ; 6 total seul ; 4 si 0 message ; 2 sinon.",
            "Les données de consommation existent et sont ventilées.")],
    },
}
AXES_HUB = tuple(DISPOSITIF)


# --------------------------------------------------------------- helpers
def bande(valeur, seuils, croissant):
    """Map a number to 2/4/6/8/10 with half-open bands ``[a, b[``."""
    rang = sum(1 for s in seuils if valeur >= s)  # 0..4
    return NIVEAUX[rang] if croissant else NIVEAUX[4 - rang]


def _parse_ts(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def est_perime(mesure_le, now, jours=PEREMPTION_JOURS):
    """True when ``mesure_le`` is missing, unparseable or older than ``jours``."""
    d = _parse_ts(mesure_le)
    return d is None or (now - d) > timedelta(days=jours)


def _mtime_iso(path):
    return datetime.fromtimestamp(os.path.getmtime(path), UTC).isoformat()


def _jsonl(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for ligne in fh:
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                out.append(json.loads(ligne))
            except ValueError:
                continue
    return out


def est_note(n):
    return isinstance(n, (int, float)) and not isinstance(n, bool)


# ------------------------------------------------ detection -> note (v2)
def noter(c, r):
    """(valeur, note) of criterion ``c`` from its detection result ``r``."""
    if not isinstance(r, dict):
        return None, NON_MESURE
    etat = r.get("etat")
    if etat == "non applicable":
        return r.get("signaux") or [], NON_APPLICABLE
    if etat != "mesure":
        return r.get("signaux") or [], NON_MESURE
    sig = list(r.get("signaux") or [])
    if c["mode"] == "echelle":
        niv = r.get("niveau")
        if not isinstance(niv, int) or niv not in _ECHELLE:
            return sig, NON_MESURE
        return {"niveau": niv, "signaux": sig}, _ECHELLE[niv]
    if c["mode"] in ("ratio_inv", "part"):
        ratio = r.get("ratio")
        if not est_note(ratio):
            return sig, NON_MESURE
        return ({"ratio": round(ratio, 3), "signaux": sig},
                bande(ratio, c["seuils"], c["croissant"]))
    val = sig
    extra = {k: r[k] for k in ("ratio", "lien_ac_test") if k in r}
    if extra:
        val = {"signaux": sig, **{k: round(v, 3) if isinstance(v, float) else v
                                  for k, v in extra.items()}}
    return val, 10 if sig else 2


# ------------------------------------------ hub-local readers (dispositif)
def mesure_reprises(runs_path, crit):
    runs = _jsonl(runs_path)[-FENETRE_RUNS:]
    if not runs:
        return None, NON_MESURE
    moy = sum(int(r.get("reprises") or 0) for r in runs) / len(runs)
    return round(moy, 2), bande(moy, crit["seuils"], crit["croissant"])


def _dans_fenetre(entrees, now):
    debut = now - timedelta(days=FENETRE_JOURS)
    return [e for e in entrees if (_parse_ts(e.get("ts")) or debut) > debut]


def mesure_refus(refus_path, crit, now):
    # a guard crash is journaled with issue "passe": the command was NOT refused
    refus = [e for e in _jsonl(refus_path) if e.get("issue") != "passe"]
    n = len(_dans_fenetre(refus, now))
    return n, bande(n, crit["seuils"], crit["croissant"])


def mesure_salles_rendues(runs_path, crit):
    etats = [p.get("etat") for r in _jsonl(runs_path)[-FENETRE_RUNS:]
             for p in (r.get("plan") or []) if isinstance(p, dict)
             and "salle" in str(p.get("agent", "")).lower()
             and p.get("etat") in ("ok", "echec", "non-rendu")]
    if not etats:
        return {"n": 0}, NON_MESURE
    part = etats.count("ok") / len(etats)
    return ({"n": len(etats), "part_ok": round(part, 2)},
            bande(part, crit["seuils"], crit["croissant"]))


def mesure_clotures(derog_path, crit, now):
    n = len(_dans_fenetre(_jsonl(derog_path), now))
    return n, bande(n, crit["seuils"], crit["croissant"])


def mesure_tokens(tokens_path):
    try:
        with open(tokens_path, encoding="utf-8") as fh:
            t = json.load(fh)
    except (OSError, ValueError):
        return None, NON_MESURE
    total = t.get("total") if isinstance(t, dict) else None
    if not isinstance(total, dict):
        return {"total": False}, 2
    if not total.get("messages"):
        return {"messages": 0}, 4
    note = 10 if t.get("par_jour") and t.get("par_modele") else \
        8 if t.get("par_jour") else 6
    return {"messages": total.get("messages")}, note


_RE_ACCEPTE = re.compile(r"^(ACCEPTE|ADOPTE)(?![A-Z])")
# Qualifiers that turn an acceptance into a partial / deferred / negated one when
# they appear in the OPENING CLAUSE, whatever the punctuation in between
# (« ACCEPTE-PARTIEL », « ACCEPTE, partiel », « ACCEPTE : NON appliqué »,
# « accepté (à appliquer plus tard) »).
_RE_RESERVE = re.compile(r"PARTIEL|(?<![A-Z])NON(?![A-Z])|PLUS\s+TARD|A\s+APPLIQUER")
_RE_APPLIQUE = re.compile(r"(?<![A-Z])APPLIQUE(?:E|S|ES)?(?![A-Z])")
_RE_APPLIQUE_NIE = re.compile(
    r"(?<![A-Z])(NON|PAS|JAMAIS|NI)\s+(ENCORE\s+|ETE\s+)?APPLIQUE")
LONGUEUR_CLAUSE = 60  # estimated: « opening clause » of a free-text decision


def _clause_ouverture(decision):
    """ASCII upper-case opening clause of ``decision`` (accents dropped), or None.

    The clause runs up to the first sentence end (. ; ! ? or line break) and at
    most LONGUEUR_CLAUSE characters."""
    if not isinstance(decision, str):
        return None
    t = unicodedata.normalize("NFKD", decision.strip()).encode("ascii", "ignore")
    t = t.decode("ascii").upper()
    return re.split(r"[.;!?\n]", t, maxsplit=1)[0][:LONGUEUR_CLAUSE]


def arbitrage_accepte(decision):
    """True only when a free-text ``decision`` reads as a FULL ACCEPTANCE.

    Arbitration records carry no structured verdict field: the verdict is the
    leading word of ``decision`` (« ACCEPTÉ + … », « ADOPTÉ + … » for an
    acceptance, « REFUSÉ : … » written by the refusal script, « ECARTE … »,
    « INSTRUIT … », « STANDBY … »…). Fail SAFE: anything that does not start
    with ACCEPTE/ADOPTE (accents and case ignored) is NOT an acceptance, and
    neither is one whose opening clause carries PARTIEL…, NON, PLUS TARD or
    A APPLIQUER — whatever punctuation sits between them. The text is data,
    never an instruction.
    """
    c = _clause_ouverture(decision)
    return bool(c and _RE_ACCEPTE.match(c) and not _RE_RESERVE.search(c))


def arbitrage_applique(decision):
    """True only when ``decision`` is a full acceptance AND its opening clause
    states the fix was APPLIED (APPLIQUÉ, accents/case ignored, not negated by
    NON / PAS / JAMAIS just before it). For a P1 security flaw, « accepted » is
    not « fixed »: only this closes the lock."""
    if not arbitrage_accepte(decision):
        return False
    c = _clause_ouverture(decision)
    return bool(_RE_APPLIQUE.search(c) and not _RE_APPLIQUE_NIE.search(c))


def _cle_date(a):
    """Sort key: dated records (ISO YYYY-MM-DD prefix) by date; an undated or
    unreadable date counts as OLDER than any dated one. Used with a stable sort,
    so equal keys keep file order."""
    d = a.get("date")
    if isinstance(d, str) and re.match(r"^\d{4}-\d{2}-\d{2}", d):
        return (1, d[:10])
    return (0, "")


def verrou_securite(diagnostic_path, arbitrages_path, projet=None):
    """C2 predicate. Join key = ``cible`` (exact string).

    Only an arbitration that ACCEPTS the fix AND states it was APPLIED closes
    a P1 security finding (``arbitrage_applique``): « accepted » alone is not
    « fixed ». The LATEST arbitration for that cible decides — latest by its
    ``date`` field (undated records count as older than dated ones; equal
    dates keep file order). A refusal, a set-aside, a partial or deferred
    acceptance, or any decision that cannot be classified keeps the lock OPEN.
    With ``projet``, a finding counts when its cible prefix (before ``:``)
    equals the project (case-insensitive) or is ``flotte``; with ``projet=None``
    (hub block) every open finding counts.
    ``statuts_preuve`` maps each open finding to its proof status
    (``mesure`` by default, else ``hypothese`` / ``a_verifier``): an unproven
    finding STILL blocks (bias to safety) but the page labels it as unproven.
    """
    if not diagnostic_path or not os.path.isfile(diagnostic_path):
        return {"etat": NON_MESURE, "findings": [], "statuts_preuve": {},
                "fonction_mesure": "verrou_securite"}
    with open(diagnostic_path, encoding="utf-8") as fh:
        findings = (json.load(fh) or {}).get("findings") or []
    derniere = {}
    if arbitrages_path and os.path.isfile(arbitrages_path):
        with open(arbitrages_path, encoding="utf-8") as fh:
            arbs = [a for a in (json.load(fh) or {}).get("arbitrages") or []
                    if isinstance(a, dict)]
        for a in sorted(arbs, key=_cle_date):  # stable: file order on ties
            derniere[a.get("cible")] = a.get("decision")
    fermes = {c for c, d in derniere.items() if arbitrage_applique(d)}
    ouverts, statuts = [], {}
    for f in findings:
        if not (isinstance(f, dict) and f.get("categorie") == "securite"
                and f.get("priorite") == 1 and f.get("cible") not in fermes):
            continue
        prefixe = str(f.get("cible", "")).split(":", 1)[0].lower()
        if projet is None or prefixe in (projet.lower(), "flotte"):
            ouverts.append(f.get("cible"))
            statuts[f.get("cible")] = str(f.get("statut_preuve") or "mesure").strip() \
                or "mesure"
    return {"etat": "ouvert" if ouverts else "ferme", "findings": ouverts,
            "statuts_preuve": statuts, "fonction_mesure": "verrou_securite"}


# ------------------------------------------------------------ aggregation
def _axe(bareme, criteres, mesure_le, now, raison=""):
    perime = est_perime(mesure_le, now)
    notes = [c for c in criteres if est_note(c["note"])]  # C5: never NM / NA
    if perime:
        note, raison = NON_MESURE, raison or "mesure périmée ou non datée"
    elif not notes:
        note, raison = NON_MESURE, raison or "source absente ou vide"
    else:
        note = round(sum(c["note"] * c["poids"] for c in notes)
                     / sum(c["poids"] for c in notes), 1)
    return {"libelle": bareme["libelle"], "question": bareme["question"],
            "portee": bareme["portee"],
            "note": note, "mesure_le": mesure_le, "perime": perime,
            "informatif": bool(bareme.get("informatif")), "raison": raison,
            "criteres": criteres}


_CHAMPS = ("nom", "libelle", "question", "observe", "combinaison", "attendu",
           "source", "conclut", "ne_conclut_pas", "fonction_mesure", "poids", "ancres")


def _critere(c, valeur, note, preuve=None):
    d = {k: c.get(k, "") for k in _CHAMPS}
    d["observe"] = list(c["observe"])
    d.update(valeur=valeur, note=note, note_ancres=c.get("note_ancres", ""),
             preuve=preuve or {})
    return d


def _globale(axes, verrou):
    if verrou["etat"] == "ouvert":  # C2 short-circuit
        return BLOQUANT
    notes = [a["note"] for a in axes.values()
             if est_note(a["note"]) and not a["informatif"]]
    return round(sum(notes) / len(notes), 1) if notes else NON_MESURE


def globale_sans_ajouts(bloc, exclus=CRITERES_AJOUTES_V3):
    """The previous-scale global note of ``bloc`` (an ``evaluer_projet`` result):
    the same means, without the criteria added since. Used by the page to say
    WHY a note moved (« v2 → v3 »), never stored as a score."""
    if bloc.get("globale") == BLOQUANT:
        return BLOQUANT
    notes = []
    for a in bloc["axes"].values():
        if a["informatif"] or not est_note(a["note"]):
            continue
        cs = [c for c in a["criteres"] if est_note(c["note"]) and c["nom"] not in exclus]
        if cs:
            notes.append(round(sum(c["note"] * c["poids"] for c in cs)
                               / sum(c["poids"] for c in cs), 1))
    return round(sum(notes) / len(notes), 1) if notes else NON_MESURE


def _bloc(axes, verrou, mesure_le):
    return {"axes": axes, "globale": _globale(axes, verrou),
            "axes_non_mesures": [k for k, a in axes.items() if a["note"] == NON_MESURE],
            "verrou_securite": verrou, "mesure_le": mesure_le}


# ------------------------------- declared « no product delivered » (generic)
# A project that delivers no application/product (e.g. a supervision or tooling
# repository) DECLARES it in an optional file at its root. The declaration is the
# team's, never guessed: undeclared = every criterion applies (default).
FICHIER_DECLARATION = ".evaluation.json"

# Product-only criteria: the ONLY ones turned « non applicable » by the
# declaration. Kept out on purpose (they still apply without a product):
# tracabilite_demande_livrable (work done on demand must cite the demand),
# test_artefact_reel (any deliverable — generated page, command — can be exercised
# through the user's channel), decisions_conception_tracees, conformite_resultats,
# validation_humaine_tracee (generic to any delivery). solution_agentic_maitrisee is conditional already.
CRITERES_PRODUIT = {
    "epics_us_bien_formees": (
        "non applicable : le projet déclare ne livrer aucun produit, il n'a donc pas "
        "de backlog d'épiques et de user stories produit à bien former"),
    "criteres_acceptance": (
        "non applicable : le projet déclare ne livrer aucun produit, il n'a donc pas "
        "de user stories produit portant des critères d'acceptation"),
}


CONTRADICTION_DECLARATION = (
    "déclaré sans produit, mais un backlog est détecté : la note mesurée est "
    "conservée, la déclaration ne la remplace pas")


def backlog_detecte(detection):
    """True when the detector MEASURED a backlog (non-empty signals)."""
    r = (detection or {}).get("epics_us_bien_formees")
    return bool(isinstance(r, dict) and r.get("etat") == "mesure" and r.get("signaux"))


def lire_declaration(chemin):
    """The project's declaration file as a dict; absent/invalid -> {} (fail safe)."""
    try:
        with open(os.path.join(chemin, FICHIER_DECLARATION), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def evaluer_projet(nom, detection, mesure_le, now, diagnostic_path=None,
                   arbitrages_path=None, declaration=None):
    """Referentials A and B of one project from ``detecter(chemin)``'s output.

    ``mesure_le`` is the CALLER's timestamp of the detection (C3).
    ``declaration`` = ``lire_declaration(chemin)``; only a strict
    ``livre_un_produit is False`` turns CRITERES_PRODUIT « non applicable » —
    unless the detector measured a real backlog (``backlog_detecte``): then the
    measured scores stay and each product criterion carries ``contradiction``."""
    detection = detection if isinstance(detection, dict) else {}
    sans_produit = isinstance(declaration, dict) and declaration.get("livre_un_produit") is False
    # A declaration never hides a measurement: a backlog actually detected
    # contradicts « no product » -> keep the measured scores, flag it visibly.
    contredit = sans_produit and backlog_detecte(detection)
    axes = {}
    for cle in AXES_PROJET:
        crits = []
        for c in BAREMES[cle]["criteres"]:
            if sans_produit and not contredit and c["nom"] in CRITERES_PRODUIT:
                crits.append(_critere(c, [CRITERES_PRODUIT[c["nom"]]], NON_APPLICABLE,
                                      {"fonction": "lire_declaration",
                                       "fichier": FICHIER_DECLARATION}))
                continue
            r = detection.get(c["nom"])
            v, n = noter(c, r)
            crits.append(_critere(c, v, n, (r or {}).get("preuve")
                                  if isinstance(r, dict) else None))
            if contredit and c["nom"] in CRITERES_PRODUIT:
                crits[-1]["contradiction"] = CONTRADICTION_DECLARATION
        axes[cle] = _axe(BAREMES[cle], crits, mesure_le, now)
    return _bloc(axes, verrou_securite(diagnostic_path, arbitrages_path, nom), mesure_le)


def evaluer_hub(racine, now):
    """Informatif « dispositif » block from the hub files; absent file -> non mesuré."""
    p = {
        "runs": os.path.join(racine, ".claude", "orchestration", "runs.jsonl"),
        "refus": os.path.join(racine, ".claude", "supervision", "refus_stdin.jsonl"),
        "derog": os.path.join(racine, ".claude", "supervision",
                              "convergence_derogations.jsonl"),
        "tokens": os.path.join(racine, ".claude", "supervision", "tokens.json"),
        "diag": os.path.join(racine, ".claude", "supervision", "diagnostic.json"),
        "arb": os.path.join(racine, ".claude", "supervision", "arbitrages.json"),
    }
    ok = {k: os.path.isfile(v) for k, v in p.items()}
    le = {k: (_mtime_iso(v) if ok[k] else None) for k, v in p.items()}
    d = DISPOSITIF

    def un(cle, src, fn, *args):
        c = d[cle]["criteres"][0]
        v, n = fn(*args) if ok[src] else (None, NON_MESURE)
        return _critere(c, v, n)

    axes = {
        "fonctionnement": _axe(d["fonctionnement"], [un(
            "fonctionnement", "runs", mesure_reprises, p["runs"],
            d["fonctionnement"]["criteres"][0])], le["runs"], now),
        "refus_gardes": _axe(d["refus_gardes"], [un(
            "refus_gardes", "refus", mesure_refus, p["refus"],
            d["refus_gardes"]["criteres"][0], now)], le["refus"], now),
    }
    cs = d["salles"]["criteres"]
    v1, n1 = mesure_salles_rendues(p["runs"], cs[0]) if ok["runs"] else (None, NON_MESURE)
    v2, n2 = mesure_clotures(p["derog"], cs[1], now) if ok["derog"] else (None, NON_MESURE)
    axes["salles"] = _axe(d["salles"], [_critere(cs[0], v1, n1), _critere(cs[1], v2, n2)],
                          le["runs"], now)
    tok_le = None
    if ok["tokens"]:
        try:
            with open(p["tokens"], encoding="utf-8") as fh:
                tok_le = (json.load(fh) or {}).get("genere")
        except (OSError, ValueError):
            tok_le = None
    axes["tokens"] = _axe(d["tokens"], [un("tokens", "tokens", mesure_tokens, p["tokens"])],
                          tok_le or le["tokens"], now)
    mesure_le = max((x for x in le.values() if x), default=None)
    return _bloc(axes, verrou_securite(p["diag"], p["arb"], None), mesure_le)


# --------------------------------- informatif « Qualité du produit » stage
# A SEPARATE stage, never scored: no note, no mean, no scale change. It sits at
# the top level of the evaluation (``qualite_produit``), outside every project
# block, so ``globale`` cannot read it. Arbitrated by the user 2026-09-30.
# « fix » = a commit whose subject starts with the Conventional Commits type
# ``fix`` : ``fix:``, ``fix!:``, ``fix(scope):`` or ``fix(scope)!:`` (lowercase).
RX_FIX = re.compile(r"^fix(\([^)]*\))?!?:")

QUALITE_LIBELLES = {
    "attendus": "Conformité aux attendus",
    "usage": "Usage réel",
    "bugs": "Correctifs (proxy de bugs)",
    "dette": "Dette technique",
}
QUALITE_NE_CONCLUT_PAS = {
    "attendus": ("un rapport de l'utilisateur simulé (1 agent, 0 humain) n'est pas "
                 "une recette : il ne dit pas que les vrais utilisateurs sont servis"),
    "usage": ("rien sur l'usage réel : qui s'en sert, combien, avec quel succès — "
              "ces données vivent hors du dépôt"),
    "bugs": ("PROXY, pas un compte de bugs : un correctif peut ne pas porter le "
             "préfixe « fix », un « fix » peut corriger un test ou une coquille, et "
             "un bug jamais corrigé n'y apparaît pas"),
    "dette": ("l'audit est un jugement daté, sur le code lu ce jour-là ; l'écart au "
              "lint ne compte que ce que ruff sait voir, pas la dette de conception"),
}


def _git_sujets(chemin):
    """Commit subjects of ``chemin`` (HEAD history), or None when not a git repo."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", chemin, "log", "--format=%s"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [s for s in r.stdout.splitlines()]


def ratio_fix(chemin):
    """(fix commits, all commits) of the project's HEAD history, or None."""
    sujets = _git_sujets(chemin) if chemin and os.path.isdir(chemin) else None
    if not sujets:
        return None
    return sum(1 for s in sujets if RX_FIX.match(s)), len(sujets)


def _audit_de(nom, audits_dir):
    """(risque_technique dimension, audit date, file) for ``nom``, or None."""
    if not audits_dir:
        return None
    f = os.path.join(audits_dir, f"{nom}.json")
    try:
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    dim = ((d or {}).get("dimensions") or {}).get("risque_technique")
    if not isinstance(dim, dict) or not dim.get("niveau"):
        return None
    return dim, d.get("date"), f


FICHIER_BASELINE_LINT = os.path.join("tests", "test_lint_baseline.py")
RX_TOTAL_BASELINE = re.compile(r"^_TOTAL_BASELINE\s*=\s*(\d+)", re.M)


RUFF_TIMEOUT_S = 60
RAISON_RUFF_DELAI = f"ruff a dépassé {RUFF_TIMEOUT_S} s"
RAISON_RUFF_INDISPO = "ruff indisponible ou en échec"
RAISON_RUFF_ILLISIBLE = "sortie de ruff illisible"


def _ruff_total(chemin, run=None):
    """Total ruff findings of ``chemin`` (same call as the lint gate), or a str
    giving why it could not be measured (fail-open: never raises)."""
    import subprocess
    run = run or subprocess.run
    try:
        r = run([sys.executable, "-m", "ruff", "check", ".",
                 "--output-format=json", "--quiet"], cwd=chemin,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=RUFF_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return RAISON_RUFF_DELAI
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return RAISON_RUFF_INDISPO
    if r.returncode not in (0, 1):
        return RAISON_RUFF_INDISPO
    try:
        return len(json.loads(r.stdout))
    except (ValueError, TypeError):
        return RAISON_RUFF_ILLISIBLE


def ecart_baseline_lint(chemin, mesurer=None):
    """(measured, baseline) when the project has a lint gate
    (``tests/test_lint_baseline.py`` with ``_TOTAL_BASELINE = N``), None when it
    has none, or a str reason when the gate exists but ruff could not measure."""
    if not chemin:
        return None
    try:
        with open(os.path.join(chemin, FICHIER_BASELINE_LINT), encoding="utf-8") as fh:
            m = RX_TOTAL_BASELINE.search(fh.read())
    except OSError:
        return None
    if not m:
        return None
    n = (mesurer or _ruff_total)(chemin)
    if isinstance(n, bool) or not isinstance(n, int):
        return n if isinstance(n, str) else RAISON_RUFF_INDISPO
    return n, int(m.group(1))


def _ligne(cle, valeur, source, mesure):
    return {"libelle": QUALITE_LIBELLES[cle], "valeur": valeur, "source": source,
            "mesure": mesure, "ne_permet_pas_de_conclure": QUALITE_NE_CONCLUT_PAS[cle]}


def qualite_produit(nom, chemin, audits_dir=None, mesurer_lint=None):
    """The informative « Qualité du produit » stage of one project: four lines,
    each with value, source and what it does NOT allow to conclude. Never a note."""
    # attendus: the utilisateur-produit agent RETURNS its report to the caller;
    # no storage location exists in the fleet (checked 2026-09-30) -> non mesuré.
    attendus = _ligne(
        "attendus", NON_MESURE,
        "aucun rapport utilisateur-produit conservé : l'agent rend son rapport à "
        "l'appelant, aucun emplacement de stockage n'existe", False)
    usage = _ligne("usage", NON_MESURE,
                   "données hors du dépôt (usage réel, support, télémétrie) : non "
                   "lues par cette évaluation", False)
    r = ratio_fix(chemin)
    if r is None:
        bugs = _ligne("bugs", NON_MESURE, "historique git illisible ou vide", False)
    else:
        n, total = r
        bugs = _ligne(
            "bugs", f"{n}/{total} commits « fix » ({round(100 * n / total)} %) — proxy",
            "git log --format=%s (sujets commençant par fix:, fix!:, fix(portée):)",
            True)
        bugs["fix"], bugs["total"] = n, total
    a = _audit_de(nom, audits_dir)
    lint = ecart_baseline_lint(chemin, mesurer_lint)
    valeurs, sources = [], []
    if isinstance(lint, str):
        sources.append(f"écart au lint non mesuré : {lint}")
        lint = None
    elif lint is not None:
        n, base = lint
        valeurs.append(f"lint : {n - base:+d} point(s) ruff vs baseline ({n} mesurés, "
                       f"baseline {base})")
        sources.append("ruff check --output-format=json vs _TOTAL_BASELINE de "
                       "tests/test_lint_baseline.py")
    else:
        sources.append("écart au lint non mesuré : pas de tests/test_lint_baseline.py")
    if a is not None:
        dim, date, f = a
        valeurs.append(f"risque technique : {dim['niveau']} (audit du {date or '?'})")
        sources.append(f".claude/audits/{os.path.basename(f)}")
    else:
        sources.append(f"aucun audit risque_technique dans .claude/audits/{nom}.json")
    dette = _ligne("dette", " ; ".join(valeurs) or NON_MESURE, " ; ".join(sources),
                   bool(valeurs))
    if lint is not None:
        dette["lint_mesure"], dette["lint_baseline"] = lint
    return {"informatif": True, "lignes": {"attendus": attendus, "usage": usage,
                                           "bugs": bugs, "dette": dette}}


def evaluer(detections_par_projet, racine_hub, mesure_le, now=None, declarations=None,
            chemins=None):
    """Full evaluation: every project (A + B) + the hub's informatif block.

    ``detections_par_projet`` = {nom: detection_generique.detecter(chemin)};
    ``mesure_le`` = the caller's detection timestamp (C3);
    ``declarations`` = {nom: lire_declaration(chemin)} (optional);
    ``chemins`` = {nom: chemin} (optional) -> adds the separate informative
    ``qualite_produit`` stage, which never enters any score."""
    now = now or datetime.now(UTC)
    sup = os.path.join(racine_hub, ".claude", "supervision")
    declarations = declarations or {}
    sortie = _evaluer_notes(detections_par_projet, racine_hub, mesure_le, now,
                            declarations, sup)
    if chemins:
        audits = os.path.join(racine_hub, ".claude", "audits")
        sortie["qualite_produit"] = {nom: qualite_produit(nom, chemins.get(nom), audits)
                                     for nom in sorted(detections_par_projet)}
    return sortie


def _evaluer_notes(detections_par_projet, racine_hub, mesure_le, now, declarations, sup):
    return {
        "version_bareme": VERSION_BAREME, "genere_le": now.isoformat(),
        "projets": {nom: evaluer_projet(nom, det, mesure_le, now,
                                        os.path.join(sup, "diagnostic.json"),
                                        os.path.join(sup, "arbitrages.json"),
                                        declarations.get(nom))
                    for nom, det in sorted(detections_par_projet.items())},
        "hub": evaluer_hub(racine_hub, now),
    }
