"""Standalone HTML page of the « Évaluation agentic » kit — scale v3.

``ecrire_page(evaluation, chemin)`` renders the output dict of
``evaluation_agentic.evaluer()`` into ONE self-contained HTML file (inline CSS
and JS, no external dependency) with its own local tablist. The page is written
for a team that receives the kit and does not know where it comes from.

* « Résultats »  — per project: global note, then the two referentials side by
  side (A « Pratiques de développement », B « Pratiques agentic »); every
  criterion shows its code (A1…B10), its own plain question and its state.
  The three tabs read the SAME groups in the SAME order, from
  ``detection_generique.GROUPES`` (single source); codes are numbered in it.
  The hub's « dispositif » block is informative only (never scored) and is
  shown only when something in it was actually measured.
* « Écarts »     — per project and referential, every criterion below the
  maximum with what was found and ACTION sentences (never raw anchor text).
* « Référentiel » — every criterion under the 5-part template: definition /
  what is looked at / why + public source / what the note allows to conclude
  / what it does NOT — plus its question, its levels and its detection
  function (C1). Stable id ``ref-<code>`` (e.g. ``ref-A1``).

Measure states are never confused (review fix 4): « non mesuré » (the
repository did not allow a conclusion), « périmé » with the date of the last
measure, « jamais mesuré » (no measure date at all), and « non applicable »
(solution_agentic_maitrisee, conditional criterion) — none of them is a number (C5).

Navigation: with JS, a deep link opens the right tab (``hashchange`` too,
review fix 8); without JS, the ``:root:not(.js) …:has(:target)`` rule reopens
the panel (review fix 1 — the ``js`` class is set by an inline HEAD script).
Every interpolated string goes through ``html.escape``.

CLI: ``py scripts/evaluation_page.py [--racine DIR] [--sortie PATH]``. Inside
the hub, the projects come from the scanner configuration; in a receiving
repository (no scanner), the repository itself is the one project evaluated.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys

ICI = os.path.dirname(os.path.abspath(__file__))
if ICI not in sys.path:
    sys.path.insert(0, ICI)

import evaluation_agentic as ea  # noqa: E402

dg = ea.dg
NON_MESURE = ea.NON_MESURE
NON_APPLICABLE = ea.NON_APPLICABLE
BLOQUANT = ea.BLOQUANT
PERIME = "périmé"
JAMAIS = "jamais mesuré"
MAX = max(ea.NIVEAUX)

# Series breaks: say WHY a note moved, not only that the scale changed.
BAREME_V2_DEPUIS = "2026-09-28"
BAREME_V3_DEPUIS = "2026-09-29"
RUPTURE = (f"barème 3 depuis le {BAREME_V3_DEPUIS} : critères ajoutés ; une note qui "
           "a baissé peut n'avoir rien perdu, elle a été notée sur davantage de "
           f"critères (barème v2 depuis le {BAREME_V2_DEPUIS} — les notes v1 antérieures "
           "ne sont pas comparables)")

# Referential letter of each scored axis, and the public code of each criterion.
LETTRES = {"pratiques_dev": "A", "pratiques_agentic": "B"}
CODES = {nom: f"{lettre}{i}" for lettre, noms in dg.REFERENTIELS.items()
         for i, nom in enumerate(noms, 1)}


def _code_tri(code):
    return (code[:1], int(code[1:] or 0))


def codes_de(noms) -> str:
    """« B2, B3 » — public codes of ``noms``, in reading order (never typed)."""
    return ", ".join(sorted((CODES[n] for n in noms), key=_code_tri))


# Criteria whose note rests on what the project DECLARES, not on a proof.
# Keyed by function name: the public codes follow the reading order and are
# renumbered when it changes (lot 9), the functions are not.
DECLARATIFS = frozenset({"gestion_prompts", "niveau_orchestration",
                         "conformite_resultats", "politique_modele_effort"})
# Proof-status labels of an unproven finding (same words as the hub wiki).
STATUTS_PREUVE = {"a_verifier": "À VÉRIFIER", "hypothese": "HYPOTHÈSE"}
MARQUE_DECLARATION = "note fondée sur des déclarations"
ICONE_DECLARATION = "⚠"
RAPPEL_PRESENCE = ("Présence ≠ fonctionnement : ces notes constatent qu'une "
                   "pratique existe dans les fichiers, pas qu'elle marche.")

# Readable words for the keys of a measured value (never an internal key).
CLES_LISIBLES = {"ratio": "part mesurée", "niveau": "niveau atteint",
                 "signaux": "signaux", "part_ok": "part rendue", "n": "nombre",
                 "lien_ac_test": "stories citées par un test",
                 "messages": "messages"}

# Review fix 6: what to DO, one action sentence per criterion, keyed by
# detection function name (codes are renumbered with the reading order).
ACTIONS = {
    "tests_automatises": (
        "Ajouter des tests automatisés dans des fichiers que les outils "
        "reconnaissent (test_*.py, *.test.ts, *.spec.js, *Test.java…) et les "
        "lancer avec le lanceur de tests du langage."),
    "couverture_configuree": (
        "Brancher la mesure de couverture sur le lanceur de tests (pytest-cov, "
        "c8, nyc, JaCoCo…) et versionner sa configuration."),
    "test_artefact_reel": (
        "Écrire au moins un test qui exerce le résultat final comme "
        "l'utilisateur le reçoit : piloter la page servie avec un navigateur "
        "automatisé, appeler l'API en HTTP, rouvrir le document produit, ou "
        "lancer la commande comme en production."),
    "co_evolution_tests": (
        "Livrer les tests dans le même commit que le code qu'ils vérifient, "
        "pour qu'au moins un commit de code sur deux touche aussi un test."),
    "code_documente": (
        "Écrire sur chaque fonction publique une docstring utile : ce qu'elle "
        "fait, ce qu'elle attend, ce qu'elle rend."),
    "integration_continue": (
        "Déclarer une chaîne d'intégration continue (.github/workflows/*.yml, "
        ".gitlab-ci.yml…) qui lance les tests à chaque envoi, et repérer les "
        "livraisons par une étiquette git par version ou un fichier CHANGELOG.md."),
    "linter_configure": (
        "Configurer un analyseur statique adapté au langage (ruff ou flake8 "
        "pour Python, ESLint ou Biome pour JavaScript…) et versionner son "
        "fichier de configuration."),
    "revue_avant_integration": (
        "Faire approuver chaque changement par une autre personne que son "
        "auteur avant la fusion, et en laisser la trace dans git : fusion "
        "d'une demande de fusion approuvée, mention Reviewed-by:, ou fichier "
        "CODEOWNERS."),
    "epics_us_bien_formees": (
        "Écrire le besoin en user stories dans les fichiers du projet (pas "
        "dans les gabarits d'un outil) : rôle (« En tant que… »), critères "
        "d'acceptation attachés, dépendances déclarées."),
    "criteres_acceptance": (
        "Attacher à chaque story ses critères d'acceptation (ou des scénarios "
        "Given/When/Then), puis citer la story ou le critère dans le test qui "
        "le vérifie."),
    "securite_base": (
        "Exclure les fichiers de secrets locaux du suivi git (.env dans "
        ".gitignore), retirer tout secret versionné et le révoquer : il reste "
        "lisible dans l'historique."),
    "decisions_conception_tracees": (
        "Consigner chaque décision d'architecture dans un fichier daté et "
        "versionné (par exemple docs/adr/0001-titre.md : contexte, décision, "
        "conséquences)."),
    "tracabilite_demande_livrable": (
        "Citer dans chaque message de commit la demande qu'il sert : numéro "
        "d'issue (#123), identifiant de story ou de ticket (PROJ-42)."),
    "cadre_agentic_versionne": (
        "Écrire les instructions données aux assistants d'IA dans des fichiers "
        "versionnés du dépôt (CLAUDE.md, AGENTS.md, "
        ".github/copilot-instructions.md…), relus comme du code."),
    "garde_fous_agentic": (
        "Déclarer des interdictions et des crochets qui bloquent les commandes "
        "destructrices (reset --hard, push --force…), protéger la branche "
        "principale, et garder le travail des assistants réversible et "
        "identifiable (commits co-signés)."),
    "solution_agentic_maitrisee": (
        "Encadrer chaque appel de modèle fait par le produit : délai "
        "d'attente, plafond de sortie, validation de la réponse par un schéma, "
        "filtrage des entrées — au moins deux de ces quatre protections."),
    "amelioration_continue": (
        "Réviser le cadre des assistants à chaque leçon tirée d'une erreur — "
        "au moins trois révisions en 90 jours — et garder une trace des "
        "rétrospectives."),
    "gestion_prompts": (
        "Ranger les prompts dans des fichiers versionnés (dossier prompts/ ou "
        "fichiers *.prompt.md) et leur adjoindre un jeu d'évaluation (dossier "
        "evals/)."),
    "niveau_orchestration": (
        "Monter l'échelle sans sauter d'échelon : cadre d'instructions, puis "
        "agents spécialisés, orchestrateur, procédures écrites, journal "
        "d'exécution."),
    "blocages_traces": (
        "Tenir un journal d'exécution (une ligne JSON par exécution, avec son "
        "statut) et traiter d'abord les causes des échecs les plus fréquents."),
    "conformite_resultats": (
        "Préciser la demande avant de la confier (critères d'acceptation, "
        "exemples) pour que plus de la moitié des exécutions soient livrées "
        "sans reprise, et consigner les reprises dans le journal."),
    "politique_modele_effort": (
        "Déclarer, dans l'en-tête de chaque définition d'agent ou de skill, le "
        "modèle ou l'effort adapté à la tâche (au moins deux valeurs "
        "différentes), et suivre la consommation dans un fichier de coûts."),
    "structure_mandats_agents": (
        "Réécrire chaque mandat d'agent sur la structure de référence "
        "(docs/reflexions/gabarit-agent.md) : condition d'arrêt écrite sans "
        "maxTurns, chaque outil décrit, ligne de ton, interdits motivés, exemple "
        "de départ, contrat de sortie et provenance en fin de texte."),
    "validation_humaine_tracee": (
        "Faire prononcer la recette d'un livrable par une autre personne que "
        "son auteur, et la tracer : mention Approved-by: ou Tested-by: dans le "
        "commit, ou passage d'un état « en attente de validation » à un état "
        "clos dans le journal."),
}

# Level-by-level actions of the 5-level ladder (niveau_orchestration), keyed by the note reached.
ACTIONS_NIVEAU = {
    4: "définir des agents spécialisés, un fichier par rôle (.claude/agents/*.md "
       "ou agents/*.md), en plus du cadre d'instructions.",
    6: "déclarer un orchestrateur qui répartit le travail entre ces agents.",
    8: "décrire les procédures récurrentes dans des playbooks versionnés "
       "(dossier playbooks/).",
    10: "tenir un journal d'exécution : une ligne JSON par exécution, avec son "
        "statut.",
}


def _e(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


# Review fix 7: file names and paths inside prose are wrapped in <code>.
_RX_JETON = re.compile(r"[\w.*/|-]+")


def _est_chemin(t) -> bool:
    """Path-like token, or a code identifier (``max_tokens``) quoted from code."""
    t = t.rstrip(".")
    if len(t) < 2 or not re.search(r"[A-Za-z]", t):
        return False
    return (t.startswith(".") or t.endswith("/") or "*" in t
            or bool(re.search(r"\w\.[A-Za-z*]", t))
            or bool(re.fullmatch(r"[A-Za-z]+(?:_[A-Za-z0-9]+)+", t)))


def _cle_txt(k) -> str:
    return CLES_LISIBLES.get(k, str(k).replace("_", " "))


def _code_txt(texte) -> str:
    """Escape ``texte`` and wrap every path-like token in ``<code>``."""
    texte = "" if texte is None else str(texte)
    out, pos = [], 0
    for m in _RX_JETON.finditer(texte):
        jeton = m.group(0)
        if not _est_chemin(jeton):
            continue
        coeur = jeton.rstrip(".")
        out.append(_e(texte[pos:m.start()]))
        out.append(f"<code>{_e(coeur)}</code>{_e(jeton[len(coeur):])}")
        pos = m.end()
    out.append(_e(texte[pos:]))
    return "".join(out)


def _valeur_txt(v) -> str:
    """Readable measured value: dicts/lists flattened, never raw braces."""
    if isinstance(v, dict):
        return ", ".join(f"{_cle_txt(k)} = ({_valeur_txt(x)})" if isinstance(x, dict)
                         else f"{_cle_txt(k)} = {_valeur_txt(x)}" for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return ", ".join(_valeur_txt(x) for x in v)
    if v is None:
        return "—"
    return str(v)


def _est_nombre(n) -> bool:
    return isinstance(n, (int, float)) and not isinstance(n, bool)


def _note_txt(note) -> str:
    """C5: a number only when the note IS a number."""
    if not _est_nombre(note):
        connus = (NON_MESURE, BLOQUANT, NON_APPLICABLE, PERIME, JAMAIS)
        return _e(note if note in connus else NON_MESURE)
    return f"{note:g}/10"


def _classe(note) -> str:
    if note == BLOQUANT:
        return "bloquant"
    if note == NON_APPLICABLE:
        return "na"
    if note == PERIME:
        return "perime"
    if note == JAMAIS:
        return "jamais"
    if not _est_nombre(note):
        return "nm"
    return "haut" if note >= 8 else "moyen" if note >= 5 else "bas"


def _date(iso) -> str:
    return str(iso)[:10] if iso else ""


def etat_mesure(axe):
    """Review fix 4 — (class, text) of a stale or never-measured axis, else None.

    Three renders never confused: « jamais mesuré » (no measure date at all),
    « périmé — mesuré le <date> » (a measure exists but is too old), and the
    plain « non mesuré » carried by the note itself (fresh but inconclusive).
    """
    if not axe.get("mesure_le"):
        return "jamais", JAMAIS
    if axe.get("perime"):
        return "perime", f"{PERIME} — mesuré le {_date(axe['mesure_le'])}"
    return None


def _pastille(note, texte=None) -> str:
    return ("<span class='n {cl}'><span class='pastille'></span>{t}</span>"
            .format(cl=_classe(note), t=texte if texte is not None
                    else _note_txt(note)))


def _pastille_axe(axe) -> str:
    etat = etat_mesure(axe)
    if etat:
        return (f"<span class='n {etat[0]}'><span class='pastille'></span>{_e(etat[1])}</span>")
    return _pastille(axe.get("note"))


def _pastille_crit(c, axe) -> str:
    etat = etat_mesure(axe)
    if etat:  # a stale axis never shows its criteria as numbers
        return ("<span class='n {cl}'><span class='pastille'></span>{t}</span>"
                .format(cl=etat[0], t=_e(PERIME if etat[0] == "perime" else JAMAIS)))
    note = c.get("note")
    if _est_nombre(note) and _mode(c) == "binaire" and note in (2, 10):
        mot = "présent" if note == MAX else "absent"
        return _pastille(note, f"{mot} · {note:g}/10")
    return _pastille(note)


def ancre_ref(axe, nom) -> str:
    """Stable id of a criterion's Référentiel entry: ``ref-A1`` … ``ref-B10``."""
    return f"ref-{CODES.get(nom) or f'{axe}-{nom}'}"


def ancre_ecart(projet_id, axe) -> str:
    """Stable id of a project + referential entry of the « Écarts » tab."""
    return f"ecart-{projet_id}-{axe}"


def _lib(c) -> str:
    """Displayed name of a criterion — its label, else its public code, never
    the internal detection function name (review fix 6)."""
    return c.get("libelle") or CODES.get(c.get("nom")) or "critère"


def _action(nom):
    return ACTIONS.get(nom)


def _par_declaration(c) -> bool:
    """A criterion made « non applicable » by the project's own declaration."""
    return bool(c) and c.get("note") == NON_APPLICABLE and \
        (c.get("preuve") or {}).get("fonction") == "lire_declaration"


def _marque_declaration(nom, c=None) -> str:
    """Review fix 2: same icon + words everywhere; text, never colour alone.
    Lot 8: also on a criterion set « non applicable » by a declaration."""
    if nom not in DECLARATIFS and not _par_declaration(c):
        return ""
    return (f"<span class='declaration' title='Le score lit ce que le projet "
            f"déclare (fichiers présents), pas une preuve de fonctionnement.'>"
            f"<span class='decl-ic' aria-hidden='true'>{ICONE_DECLARATION}</span>"
            f"{MARQUE_DECLARATION}</span>")


def _mode(c) -> str:
    return ea.MODES.get(c.get("nom"), "binaire")


def _groupes(cle, criteres) -> list:
    """[(group title, [criteria])] — the reading order of the single source
    (``dg.GROUPES``), every group kept even when empty, so that the three tabs
    show the same groups in the same order. The page never re-sorts."""
    par_nom = {c.get("nom"): c for c in criteres}
    out = [(titre, [par_nom[n] for n in noms if n in par_nom])
           for titre, noms in dg.GROUPES.get(LETTRES.get(cle, ""), ())]
    restes = [c for c in criteres if c.get("nom") not in dg.GROUPE_DE]
    if restes:  # never drop a criterion the source forgot to group
        out.append(("Autres critères", restes))
    return out


def _titre_groupe(titre, balise="h5") -> str:
    return f"<{balise} class='groupe-t'>{_e(titre)}</{balise}>"


def _dominant(axe):
    """The criterion that weighs most on the note: lowest, ties by order."""
    crits = [c for c in axe.get("criteres") or [] if _est_nombre(c.get("note"))]
    return min(crits, key=lambda c: c["note"]) if crits else None


def _pese_le_plus(axe) -> str:
    """A FEW WORDS naming what holds the note back — no number, no jargon."""
    if axe.get("note") == NON_MESURE:
        return "mesure absente"
    dom = _dominant(axe)
    if dom is None:
        return "mesure absente"
    if dom["note"] >= MAX:
        return "rien à signaler"
    return _lib(dom)


# --------------------------------------------------------------- « Résultats »
def _crit_resultat(cle, c, axe) -> str:
    """One criterion row. The declaration mark sits BEFORE the score (fix 2)."""
    nom = c.get("nom")
    return (
        "<li class='crit-res'><span class='code-c'>{code}</span>"
        "<div class='cr-corps'><a class='lien-ref' href='#{href}'>{lib}</a>"
        "<div class='question-c'>{q}</div>{contra}</div>"
        "<div class='cr-note'>{decl}{note}</div></li>").format(
        code=_e(CODES.get(nom, "")), href=_e(ancre_ref(cle, nom)),
        lib=_e(_lib(c)), decl=_marque_declaration(nom, c),
        q=_e(c.get("question")), note=_pastille_crit(c, axe),
        contra=_contradiction(c))


def _contradiction(c) -> str:
    """Lot 8: a declaration contradicted by a measurement is said in words."""
    t = c.get("contradiction")
    return f"<div class='contradiction'>Contradiction : {_e(t)}</div>" if t else ""


def _cible_verrou(cible, statuts) -> str:
    """Lot 8: an unproven lock finding is labelled ([HYPOTHÈSE] / [À VÉRIFIER])."""
    s = str((statuts or {}).get(cible) or "mesure")
    if s == "mesure":
        return _e(cible)
    return f"[{_e(STATUTS_PREUVE.get(s, s.upper()))}] {_e(cible)}"


def _carte_axe(projet_id, cle, axe) -> str:
    """Résultats: one referential of one project — note, then every criterion."""
    pese = _pese_le_plus(axe)
    if pese == "rien à signaler":
        cellule = "<span class='rien'>rien à signaler</span>"
    else:
        cellule = (f"<a class='lien-ecart' href='#{_e(ancre_ecart(projet_id, cle))}'>"
                   f"{_e(pese)}</a>")
    crits = "".join(
        _titre_groupe(titre) + "<ul class='crits-res'>"
        + "".join(_crit_resultat(cle, c, axe) for c in cs) + "</ul>"
        for titre, cs in _groupes(cle, axe.get("criteres") or []))
    lettre = LETTRES.get(cle, "")
    return (
        "<div class='axe-carte' data-axe='{cle}'><header>"
        "<h4><span class='lettre'>{lettre}</span>{lib}</h4>{note}</header>"
        "<p class='question'>{q}</p>"
        "<p class='pese'><span class='obs-titre'>Ce qui pèse le plus</span> {pese}</p>"
        "{crits}</div>").format(
        cle=_e(cle), lettre=_e(lettre), lib=_e(axe.get("libelle")),
        note=_pastille_axe(axe), q=_e(axe.get("question")), pese=cellule,
        crits=crits)


def _globale_txt(bloc):
    axes = list((bloc.get("axes") or {}).values())
    g = bloc.get("globale")
    if g == NON_MESURE and axes:
        etats = [etat_mesure(a) for a in axes]
        if all(etats):
            return etats[0]
    return None


def _bloc(titre, bloc, id_, projet_id) -> str:
    """Résultats: a project card — global note, then A and B side by side."""
    verrou = bloc.get("verrou_securite") or {}
    ouvert = verrou.get("etat") == "ouvert"
    statuts = verrou.get("statuts_preuve") or {}
    cibles = ", ".join(_cible_verrou(f, statuts)
                       for f in verrou.get("findings") or []) or "—"
    non_prouve = any(str(statuts.get(f) or "mesure") != "mesure"
                     for f in verrou.get("findings") or [])
    alerte = ("<p class='alerte'>Un problème de sécurité important est "
              "signalé et n'a pas encore été traité : tant qu'il est ouvert, "
              f"aucune moyenne n'est affichée. Point concerné : {cibles}."
              + (" Un point marqué [HYPOTHÈSE] ou [À VÉRIFIER] n'est pas encore "
                 "prouvé : il bloque quand même, par prudence." if non_prouve else "")
              + "</p>" if ouvert else "")
    cartes = "".join(_carte_axe(projet_id, k, a)
                     for k, a in (bloc.get("axes") or {}).items())
    etat = _globale_txt(bloc)
    g = bloc.get("globale")
    glob = (f"<span class='globale n {etat[0]}'><span class='pastille'></span>"
            f"<span class='g-lib'>Note globale</span> {_e(etat[1])}</span>"
            if etat else
            f"<span class='globale n {_classe(g)}'><span class='pastille'></span>"
            f"<span class='g-lib'>Note globale</span> {_note_txt(g)}</span>")
    le = _date(bloc.get("mesure_le")) or JAMAIS
    return (
        "<section class='bloc' id='{id}'><header><h3>{t}</h3>{glob}</header>"
        f"<p class='rappel-presence'>{_e(RAPPEL_PRESENCE)}</p>"
        "<p class='meta'>Mesuré le {le} · la note globale est la moyenne des deux "
        "référentiels ; un critère « non mesuré » ou « non applicable » n'y entre "
        "pas.</p>{alerte}<div class='deux-ref'>{cartes}</div></section>").format(
        id=_e(id_), t=_e(titre), glob=glob, le=_e(le), alerte=alerte,
        cartes=cartes)


def _hub_mesure(hub) -> bool:
    """True when at least one informative criterion was actually measured."""
    return any(c.get("note") != NON_MESURE
               for a in (hub.get("axes") or {}).values()
               for c in a.get("criteres") or [])


def _bloc_dispositif(hub, id_) -> str:
    """The hub's own informative block: values only, never a note."""
    cartes = []
    for cle, axe in (hub.get("axes") or {}).items():
        lignes = "".join(
            "<li><b>{lib}</b> — {q}<div class='valeur'>Valeur relevée : {v}</div></li>"
            .format(lib=_e(c.get("libelle")), q=_e(c.get("question")),
                    v=_e(_valeur_txt(c.get("valeur"))
                         if c.get("note") != NON_MESURE else NON_MESURE))
            for c in axe.get("criteres") or [])
        cartes.append(
            "<div class='axe-carte info' data-axe='{cle}'><h4>{lib}</h4>"
            "<p class='question'>{q}</p><ul class='dispo'>{l}</ul></div>".format(
                cle=_e(cle), lib=_e(axe.get("libelle")), q=_e(axe.get("question")),
                l=lignes))
    return (
        "<section class='bloc' id='{id}'><header><h3>Dispositif de l'équipe qui "
        "publie ce kit</h3><span class='tag'>informatif — non noté</span></header>"
        "<p class='meta'>Ces relevés décrivent l'outillage propre à l'équipe "
        "émettrice. Ils ne font partie d'aucun des deux référentiels, ne reçoivent "
        "aucune note et n'entrent dans aucune moyenne.</p>"
        "<div class='deux-ref'>{c}</div></section>").format(
        id=_e(id_), c="".join(cartes))


# ------------------------------------------------------------------ « Écarts »
def _conditions_non_mesure(c) -> str:
    """The « non mesuré » conditions stated by the criterion itself."""
    phrases = re.split(r"(?<=[.;])\s+", c.get("ne_conclut_pas") or "")
    return " ".join(p for p in phrases if "non mesur" in p)


def _recommandations(c, perime=None) -> list:
    """1-3 ACTION sentences (review fix 6) — never the raw anchor text."""
    nom, note = c.get("nom"), c.get("note")
    if perime:
        return [f"Relancer la mesure : la dernière date du {perime}, au-delà de "
                f"{ea.PEREMPTION_JOURS} jours elle ne vaut plus comme note."]
    if note == NON_APPLICABLE:
        return []
    action = _action(nom)
    if not _est_nombre(note):
        cond = _conditions_non_mesure(c)
        recos = ["Rendre le critère mesurable : la détection n'a pas pu conclure "
                 "sur ce dépôt." + (f" Cas prévus : {cond}" if cond else "")]
        if action:
            recos.append(f"Puis, si la pratique manque : {action[0].lower()}{action[1:]}")
        return recos
    superieurs = [n for n in sorted(c.get("ancres") or {}) if n > note]
    if not superieurs:
        return []
    suivant = superieurs[0]
    mode = _mode(c)
    if mode == "echelle":
        recos = [f"Pour atteindre {suivant}/10 : {ACTIONS_NIVEAU[suivant]}"]
        if len(superieurs) > 1:
            recos.append(f"Pour atteindre le maximum ({superieurs[-1]}/10) : "
                         f"{ACTIONS_NIVEAU[superieurs[-1]]}")
        recos.append(action)
        return recos[:3]
    if mode == "ratio_inv":
        seuils = sorted(c.get("seuils") or ea.SEUILS_ECHECS)
        # 10 -> under the first threshold, 8 -> the second, … 4 -> the last
        cible = seuils[max(0, min(len(seuils) - 1, (MAX - suivant) // 2))]
        return [f"Pour atteindre {suivant}/10 : ramener la part d'exécutions en "
                f"échec sous {cible:.0%}.".replace("%", " %"),
                action]
    if mode == "part":
        seuils = sorted(c.get("seuils") or dg.SEUILS_STRUCTURE)
        cible = seuils[max(0, min(len(seuils) - 1, suivant // 2 - 2))]
        return [f"Pour atteindre {suivant}/10 : porter à {cible:.0%} la part des "
                "blocs de la structure présents dans les mandats d'agents."
                .replace("%", " %"), action]
    return [f"Pour atteindre {suivant}/10 : {action}" if action else
            f"Pour atteindre {suivant}/10 : mettre en place ce que décrit le "
            "référentiel pour ce critère."]


def _trouve(c) -> str:
    """What the detection actually found, as a list — paths in <code>."""
    v = c.get("valeur")
    sig = v.get("signaux") if isinstance(v, dict) else v
    extra = {k: x for k, x in v.items() if k != "signaux"} if isinstance(v, dict) else {}
    items = "".join(f"<li>{_code_txt(s)}</li>" for s in (sig or []))
    info = (f"<p class='meta'>Relevé : {_e(_valeur_txt(extra))}</p>" if extra else "")
    if not items:
        return "<p class='rien'>Rien trouvé.</p>" + info
    return f"<ul class='trouve'>{items}</ul>{info}"


def _entree_ecart(projet_id, nom_projet, cle, axe) -> str:
    """One project + referential entry: each criterion below the maximum."""
    etat = etat_mesure(axe)
    perime = _date(axe.get("mesure_le")) if etat and etat[0] == "perime" else None
    corps, groupes = [], []
    for titre, cs in _groupes(cle, axe.get("criteres") or []):
        avant = len(corps)
        for c in cs:
            _ecart_crit(c, cle, axe, etat, perime, corps)
        groupes.append(_titre_groupe(titre) + ("".join(corps[avant:]) or
                       "<p class='rien'>Rien à signaler dans ce groupe.</p>"))
    corps = groupes if corps else []
    if not corps:
        corps = ["<p class='rien'>Rien à signaler : tous les critères mesurés de "
                 "ce référentiel sont au maximum.</p>"]
    return (
        "<div class='ecart' id='{id}'><h4><span class='lettre'>{lettre}</span>"
        "{proj} — {lib}</h4><p class='meta'>Note du référentiel : {note}</p>"
        "<p class='honnete'>La mesure constate une présence dans les fichiers, "
        "critère par critère ; elle ne dit pas que la pratique fonctionne.</p>"
        "{corps}</div>").format(
        id=_e(ancre_ecart(projet_id, cle)), lettre=_e(LETTRES.get(cle, "")),
        proj=_e(nom_projet), lib=_e(axe.get("libelle")),
        note=_pastille_axe(axe), corps="".join(corps))


def _ecart_crit(c, cle, axe, etat, perime, corps) -> None:
    """Append the gap block of ONE criterion below the maximum to ``corps``."""
    note = c.get("note")
    if not etat and ((_est_nombre(note) and note >= MAX) or note == NON_APPLICABLE):
        return
    nom = c.get("nom")
    recos = _recommandations(c, perime)
    if etat and etat[0] == "jamais":
        recos = ["Lancer une première mesure : ce projet n'a jamais été mesuré."]
    liste_recos = ("<ul class='recos'>"
                   + "".join(f"<li>{_code_txt(r)}</li>" for r in recos) + "</ul>"
                   if recos else "")
    corps.append((
        "<div class='ecart-crit'><h5><span class='code-c'>{code}</span>{lib} "
        "{note}</h5>{decl}<p class='question-c'>{q}</p>"
        "<p class='obs-titre'>Ce qui a été trouvé</p>{trouve}"
        "<p class='obs-titre'>Ce qu'il faudrait faire</p>{recos}"
        "<p class='meta'><a class='lien-ref' href='#{ref}'>Voir la définition "
        "complète de ce critère</a></p></div>").format(
            code=_e(CODES.get(nom, "")), lib=_e(_lib(c)),
            note=_pastille_crit(c, axe), decl=_marque_declaration(nom, c),
            q=_e(c.get("question")),
            trouve=(_trouve(c) if not etat else
                    "<p class='rien'>Mesure trop ancienne ou absente.</p>"),
            recos=liste_recos, ref=_e(ancre_ref(cle, nom))))


def _axe_au_max(axe) -> bool:
    return (not etat_mesure(axe) and _est_nombre(axe.get("note"))
            and axe["note"] >= MAX)


def _pane_ecarts(projets) -> str:
    """Per project, A and B side by side; an axis at the maximum has no entry."""
    out = []
    for projet_id, nom, bloc in projets:
        entrees = [_entree_ecart(projet_id, nom, cle, axe)
                   for cle, axe in (bloc.get("axes") or {}).items()
                   if not _axe_au_max(axe)]
        if entrees:
            out.append(f"<h3 class='ec-projet'>{_e(nom)}</h3>"
                       f"<div class='deux-ref'>{''.join(entrees)}</div>")
    if not out:
        return ("<p class='rien'>Aucun écart : tous les référentiels mesurés sont "
                "au maximum.</p>")
    return "".join(out)


# ------------------------------------------------------------- « Référentiel »
def _section(titre, corps) -> str:
    return f"<p class='obs-titre'>{_e(titre)}</p>{corps}"


def _ref_critere(cle, c) -> str:
    nom = c["nom"]
    ancres = "".join(
        "<li><b class='n {cl}'>{n}/10</b> <span>{t}</span></li>".format(
            cl=_classe(n), n=n, t=_e(c["ancres"].get(n, "")))
        for n in sorted(c["ancres"], reverse=True))
    note = (f"<p class='note-ancres'>{_e(c.get('note_ancres'))}</p>"
            if c.get("note_ancres") else "")
    observe = "".join(f"<li>{_code_txt(x)}</li>" for x in c.get("observe") or [])
    conditionnel = (
        "<p class='conditionnel'>Critère conditionnel : il ne concerne que les "
        "projets dont le produit appelle lui-même un modèle d'IA. Sinon il est "
        "« non applicable » et n'entre dans aucune moyenne.</p>"
        if nom == "solution_agentic_maitrisee" else "")
    return (
        "<div class='ref' id='{id}' data-critere='{nom}'>"
        "<h4><span class='code-c'>{code}</span>{lib}</h4>{decl}"
        "<p class='question'>{q}</p>{cond}"
        + _section("1. Définition", "<p class='attendu'>{attendu}</p>")
        + _section("2. Ce qu'on regarde", "<ul class='observe'>{observe}</ul>"
                   "<p class='combinaison'>{comb}</p>")
        + _section("3. Pourquoi — et la source publique", "<p class='source'>{source}</p>")
        + _section("4. Ce que la note permet de conclure", "<p class='conclut'>{conclut}</p>")
        + _section("5. Ce qu'elle ne permet pas de conclure",
                   "<p class='ne-conclut'>{ne}</p>")
        + "<p class='ancres-titre'>Niveaux de note</p><ol class='ancres'>{a}</ol>{note}"
        "<p class='meta'>Code {code} · fonction de détection <code>{f}</code> · "
        "poids {po}</p></div>").format(
        id=_e(ancre_ref(cle, nom)), nom=_e(nom), code=_e(CODES.get(nom, "")),
        lib=_e(_lib(c)), decl=_marque_declaration(nom),
        q=_e(c.get("question")), cond=conditionnel,
        attendu=_code_txt(c.get("attendu")), observe=observe,
        comb=_code_txt(c.get("combinaison")), source=_code_txt(c.get("source")),
        conclut=_code_txt(c.get("conclut")), ne=_code_txt(c.get("ne_conclut_pas")),
        a=ancres, note=note, f=_e(c["fonction_mesure"]), po=_e(c["poids"]))


def _referentiel() -> str:
    colonnes = []
    for cle, b in ea.BAREMES.items():
        crits = "".join(
            _titre_groupe(titre, "h4") + "".join(_ref_critere(cle, c) for c in cs)
            for titre, cs in _groupes(cle, b["criteres"]))
        colonnes.append(
            "<div class='ref-col' data-axe='{cle}'><h3 class='ref-titre'>"
            "<span class='lettre'>{l}</span>{lib} — {n} critères</h3>"
            "<p class='question'>{q}</p>{crits}</div>".format(
                cle=_e(cle), l=_e(LETTRES.get(cle, "")), lib=_e(b["libelle"]),
                n=len(b["criteres"]), q=_e(b.get("question")), crits=crits))
    return "<div class='deux-ref'>" + "".join(colonnes) + "</div>"


INTRO_RESULTATS = (
    "<div class='intro'><h2 class='intro-t'>Comment lire cette page</h2>"
    "<p>Chaque projet est regardé à travers deux référentiels : "
    "<b>A — Pratiques de développement</b> ({n_a} critères : besoin écrit, décisions "
    "tracées, relecture, secrets, tests, intégration continue…) et "
    "<b>B — Pratiques agentic</b> ({n_b} critères : ce qui cadre, borne, trace et "
    "fait valider le travail confié aux assistants d'IA). Tout est lu dans les "
    "fichiers et l'historique git du projet ; rien n'est jugé à l'œil.</p>"
    "<ul class='intro-l'>"
    "<li><b>Présent · 10/10 / absent · 2/10</b> — la plupart des critères n'ont "
    "que ces deux niveaux ; trois critères ont une échelle ({c_orch} niveau "
    "d'orchestration, {c_echecs} part d'échecs, {c_structure} structure des "
    "mandats d'agents).</li>"
    "<li><b>Note d'un référentiel</b> — la moyenne de ses critères notés ; "
    "<b>note globale</b> — la moyenne des deux référentiels.</li>"
    "<li><b>« non mesuré »</b> — le dépôt ne permet pas de conclure (pas "
    "d'historique git, pas de journal…) : aucune note n'est devinée, et le "
    "critère sort de la moyenne.</li>"
    "<li><b>« non applicable »</b> — le critère ne concerne pas ce projet ({c_solution} "
    "quand le produit n'appelle aucun modèle d'IA, {c_structure} quand le projet "
    "ne définit aucun agent) ; hors moyenne.</li>"
    "<li><b>« périmé »</b> — la dernière mesure date de plus de {j} jours (sa "
    "date est affichée) ; <b>« jamais mesuré »</b> — aucune mesure n'existe.</li>"
    "<li><b>« bloquant »</b> — un problème de sécurité important est ouvert ; "
    "tant qu'il l'est, aucune moyenne ne s'affiche.</li>"
    "<li><b>Présence, pas fonctionnement</b> — la page constate qu'un test, une "
    "chaîne ou une règle existe, pas qu'il passe ou qu'elle est respectée. Les "
    "critères marqués « note fondée sur des déclarations » ({c_decl}) "
    "lisent ce que le projet déclare, pas une preuve.</li></ul>"
    "<p class='intro-p'>Cliquer un critère ouvre sa définition complète dans "
    "l'onglet « Référentiel ».</p></div>")

def _intro_resultats() -> str:
    """The Résultats intro with every code derived from the single source."""
    valeurs = {"{j}": str(ea.PEREMPTION_JOURS),
               "{c_orch}": CODES["niveau_orchestration"],
               "{c_echecs}": CODES["blocages_traces"],
               "{c_solution}": CODES["solution_agentic_maitrisee"],
               "{c_structure}": CODES["structure_mandats_agents"],
               "{n_a}": str(len(dg.REFERENTIELS["A"])),
               "{n_b}": str(len(dg.REFERENTIELS["B"])),
               "{c_decl}": codes_de(DECLARATIFS)}
    texte = INTRO_RESULTATS
    for cle, v in valeurs.items():
        texte = texte.replace(cle, _e(v))
    return texte


INTRO_ECARTS = (
    "<div class='intro'><h2 class='intro-t'>Ce que dit cet onglet</h2>"
    "<p>Pour chaque référentiel qui n'est pas au maximum : les critères "
    "manquants, ce que la détection a trouvé, et l'action qui ferait monter la "
    "note. Un critère au maximum ou « non applicable » n'apparaît pas.</p></div>")

GLOSSAIRE = (
    ("Assistant / agent", "le programme d'IA à qui l'on confie une tâche dans le "
     "projet ; un « sous-agent » est un assistant lancé par un autre pour une "
     "tâche précise."),
    ("Cadre agentic", "l'ensemble des fichiers qui disent aux assistants comment "
     "travailler : règles du projet, définitions d'agents, autorisations, "
     "mémoire."),
    ("Skill", "une fiche de procédure que l'assistant charge pour savoir "
     "comment faire une tâche donnée."),
    ("Crochet (hook)", "un petit programme lancé automatiquement à un moment "
     "précis, par exemple avant une commande, pour la bloquer si elle est "
     "dangereuse."),
    ("Journal d'exécution", "un fichier où chaque travail confié aux assistants "
     "laisse une ligne avec son issue (réussi, échoué, en attente de "
     "validation)."),
    ("Déclaration", "ce que le projet affirme dans ses fichiers (un niveau, un "
     "modèle, un statut). Une note fondée sur des déclarations n'est pas une "
     "preuve de fonctionnement."),
    ("Verrou sécurité", "la règle qui remplace la note globale par « bloquant » "
     "tant qu'un problème de sécurité important reste ouvert."),
    ("Péremption", "au-delà de {j} jours, une mesure n'est plus affichée comme "
     "un chiffre : elle devient « périmé », avec sa date."),
    ("Fonction de détection", "le nom du morceau de programme qui fait la "
     "mesure, montré pour qu'on puisse aller la relire."),
    ("INVEST", "grille de qualité d'une user story (B. Wake, 2003) : "
     "Indépendante, Négociable, porteuse de Valeur, Estimable, petite (Small), "
     "Testable. Cette page n'en contrôle que la forme ; I, N, V et E demandent "
     "une lecture humaine (audit)."),
    ("ADR", "Architecture Decision Record : une fiche courte et datée par "
     "décision d'architecture (contexte, décision, conséquences)."),
    ("DORA", "programme de recherche sur la performance de livraison "
     "logicielle. Ses quatre indicateurs d'origine sont la fréquence de "
     "déploiement, le délai de mise en production, le taux d'échec des "
     "changements et le délai de rétablissement. Cette page n'en mesure "
     "AUCUN : elle reprend seulement des pratiques associées et constate leur "
     "présence."),
    ("OWASP", "fondation qui publie des listes de risques de sécurité (Top 10 "
     "web, Top 10 des applications LLM et agentic). Cette page n'en vérifie "
     "qu'un reflet : la présence de protections, jamais l'absence de faille."),
    ("ISO/IEC 25010", "la norme qui décrit les qualités attendues d'un logiciel "
     "(adéquation fonctionnelle, fiabilité, sécurité…). Citée comme référence, "
     "elle n'est pas mesurée en tant que telle."),
)


def _intro_referentiel() -> str:
    entrees = "".join(
        "<div class='glo-e'><dt>{t}</dt><dd>{d}</dd></div>".format(
            t=_e(t), d=_e(d.replace("{j}", str(ea.PEREMPTION_JOURS))))
        for t, d in GLOSSAIRE)
    return (
        "<div class='intro'><h2 class='intro-t'>À quoi sert ce référentiel</h2>"
        "<p>Chaque critère est décrit en cinq parties : <b>1.</b> sa définition, "
        "<b>2.</b> ce qu'on regarde exactement dans le dépôt, <b>3.</b> pourquoi "
        "c'est important, avec sa source publique, <b>4.</b> ce que la note "
        "permet de conclure, <b>5.</b> ce qu'elle ne permet pas de conclure. "
        "Suivent les niveaux de note et le nom de la fonction de détection.</p>"
        "<p>Ce qui demande une lecture humaine n'est pas noté ici et relève d'un "
        "audit : les qualités I, N, V, E de la grille INVEST ("
        + CODES["epics_us_bien_formees"] + "), et la maîtrise réelle d'une "
        "solution qui embarque un modèle d'IA ("
        + CODES["solution_agentic_maitrisee"] + ").</p>"
        "<h3 class='glo-t'>Les mots de la page</h3>"
        "<dl class='glossaire'>" + entrees + "</dl></div>")


CSS = """
:root{--bg:#f4f5f7;--fg:#16181d;--mut:#5d6472;--card:#fff;--bd:#e0e3e9;
--ok:#15703a;--ok-bg:#e6f5ec;--mid:#8a5a00;--mid-bg:#fdf1dc;--ko:#a81f16;
--ko-bg:#fbe9e7;--nm:#606773;--nm-bg:#eceef1;--acc:#2b5fb3;--acc-bg:#e8effb;
--old:#7a4d9a;--old-bg:#f3ebf8;--sh:0 1px 2px rgba(16,20,30,.06),0 4px 14px rgba(16,20,30,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#121316;--fg:#e9ebee;--mut:#9aa1ad;
--card:#1c1e23;--bd:#31353d;--ok:#6ed592;--ok-bg:#16301f;--mid:#f0c368;
--mid-bg:#33280f;--ko:#ff8a80;--ko-bg:#371815;--nm:#9aa1ad;--nm-bg:#24272c;
--acc:#8db4ff;--acc-bg:#1a2437;--old:#d3a8f0;--old-bg:#2b1f35;
--sh:0 1px 2px rgba(0,0,0,.4)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
overflow-wrap:break-word}
/* Lot 9 (user): the page uses the whole window, with side margins that scale
   with it (16 px on a phone, up to 64 px on a wide screen) — no centred cap. */
main{margin:0;padding:0 clamp(16px,4vw,64px) 64px}
main,section,div,p,li,td,th,dd{min-width:0}
h1{font-size:1.9rem;margin:28px 0 6px;letter-spacing:-.01em}
h2{font-size:1.15rem;margin:34px 0 10px;color:var(--mut);
text-transform:uppercase;letter-spacing:.06em;font-weight:700}
/* Lot 9: header, intro and questions span the full width of the tab rule. */
.chapeau{color:var(--mut);font-size:.92rem;margin:0 0 4px}
.meta{color:var(--mut);font-size:.82rem;margin:.25em 0}
code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.86em;
background:var(--nm-bg);border-radius:4px;padding:0 4px}
.rupture{display:inline-block;background:var(--old-bg);color:var(--old);
border-radius:8px;padding:4px 12px;font-size:.84rem;font-weight:600;margin:6px 0}
[role=tablist]{position:sticky;top:0;z-index:5;display:flex;gap:6px;
background:var(--bg);border-bottom:2px solid var(--bd);margin:18px 0 8px;
padding-top:8px}
[role=tab]{border:0;background:none;color:var(--mut);padding:10px 18px;
font:inherit;font-weight:600;cursor:pointer;border-bottom:3px solid transparent;
margin-bottom:-2px;border-radius:6px 6px 0 0}
[role=tab]:hover{background:var(--acc-bg);color:var(--fg)}
[role=tab][aria-selected=true]{border-bottom-color:var(--acc);color:var(--fg)}
[role=tab]:focus-visible,a:focus-visible,[role=tabpanel]:focus-visible{
outline:3px solid var(--acc);outline-offset:2px}
/* Review fix 3: every deep-link target lands below the sticky tab bar. */
[id^=ref-],[id^=ecart-],.bloc,[role=tabpanel]{scroll-margin-top:84px}
/* Sans JS seulement : une ancre vers un critere rouvre le panneau qui le
   contient. Avec JS, la classe .js (posee dans le HEAD) neutralise la regle. */
:root:not(.js) [role=tabpanel][hidden]:has(:target){display:block!important}
.deux-ref{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,470px),1fr));
gap:16px;align-items:start}
.bloc.qualite-produit{margin-top:28px;border-style:dashed;border-width:2px}
.bloc{background:var(--card);border:1px solid var(--bd);border-radius:14px;
padding:18px 20px 12px;margin:16px 0;box-shadow:var(--sh)}
.bloc>header,.axe-carte>header{display:flex;justify-content:space-between;
align-items:center;gap:12px;flex-wrap:wrap}
.bloc h3{margin:0;font-size:1.3rem}
.globale{font-size:1.4rem;font-weight:800;border-radius:12px;padding:6px 16px;
display:flex;align-items:center;gap:10px}
.globale .g-lib{font-size:.72rem;font-weight:700;text-transform:uppercase;
letter-spacing:.08em;opacity:.85}
.axe-carte{border:1px solid var(--bd);border-radius:12px;padding:14px 16px;
background:var(--bg)}
.axe-carte.info{background:var(--card)}
.axe-carte h4{margin:0;font-size:1.05rem;display:flex;align-items:center;gap:8px}
.lettre{display:inline-grid;place-items:center;width:1.7em;height:1.7em;
border-radius:6px;background:var(--acc);color:var(--card);font-weight:800;
font-size:.85rem;flex:none;margin-right:6px}
.code-c{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-weight:700;
font-size:.78rem;color:var(--acc);background:var(--acc-bg);border-radius:6px;
padding:1px 7px;margin-right:8px;flex:none;min-width:3.4em;text-align:center}
.n{font-weight:700;display:inline-flex;align-items:center;gap:6px;
white-space:nowrap}
.pastille{width:10px;height:10px;border-radius:50%;background:currentColor;flex:none}
.axe-carte>header .n,.crit-res>.n,.ecart-crit h5 .n{border-radius:999px;
padding:2px 10px;font-size:.85rem}
.haut{color:var(--ok)}.globale.haut,.n.haut{background:var(--ok-bg)}
.moyen{color:var(--mid)}.globale.moyen,.n.moyen{background:var(--mid-bg)}
.bas,.bloquant{color:var(--ko)}.n.bas,.n.bloquant,.globale.bas,.globale.bloquant{background:var(--ko-bg)}
.nm{color:var(--nm);font-style:italic}.n.nm,.globale.nm{background:var(--nm-bg)}
.na{color:var(--nm);font-weight:600}
.n.na{background:repeating-linear-gradient(135deg,var(--nm-bg) 0 6px,transparent 6px 12px);
border:1px dashed var(--nm)}
.na .pastille{background:transparent;border:2px solid currentColor}
.perime{color:var(--old)}.n.perime,.globale.perime{background:var(--old-bg)}
.perime .pastille{background:transparent;border:2px dotted currentColor}
.jamais{color:var(--nm)}.n.jamais,.globale.jamais{background:transparent;
border:1px solid var(--bd)}
.jamais .pastille{background:transparent;border:2px solid var(--bd)}
.ancres .n{background:none!important;border:0;padding:0}
.tag{font-size:.7rem;border:1px solid var(--bd);border-radius:999px;
padding:1px 9px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}
/* Review fix 2: solid, high-contrast mark placed before the score. */
.declaration{display:inline-flex;align-items:center;gap:5px;font-size:.74rem;
font-weight:800;color:var(--card);background:var(--mid);border:1px solid var(--mid);
border-radius:6px;padding:1px 8px;margin:2px 0;letter-spacing:.01em;white-space:nowrap}
.decl-ic{font-size:.95em;line-height:1}
.cr-note{display:flex;flex-direction:column;align-items:flex-end;gap:3px;flex:none}
.rappel-presence{margin:8px 0 2px;font-size:.86rem;font-weight:600;color:var(--fg);
background:var(--nm-bg);border-left:4px solid var(--mid);border-radius:0 8px 8px 0;
padding:6px 12px}
.question{font-size:.92rem;margin:6px 0 4px}
.groupe-t{font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;
color:var(--acc);font-weight:800;margin:14px 0 2px;padding-bottom:3px;
border-bottom:2px solid var(--acc-bg)}
.ref-col>.groupe-t{margin:22px 0 0;font-size:.8rem}
.question-c{font-size:.82rem;color:var(--mut);margin:1px 0 0}
.pese{font-size:.88rem;margin:6px 0 8px}
ul.crits-res{list-style:none;margin:0;padding:0;display:grid;gap:2px}
li.crit-res{display:flex;align-items:flex-start;gap:6px;padding:7px 6px;
border-top:1px solid var(--bd)}
.cr-corps{flex:1;min-width:0}
li.crit-res a.lien-ref{font-weight:600;color:var(--fg);text-decoration:none;
border-bottom:1px dotted var(--acc)}
li.crit-res a.lien-ref:hover{color:var(--acc)}
ul.dispo{margin:0;padding-left:18px;font-size:.88rem;display:grid;gap:6px}
.valeur{color:var(--mut);font-size:.82rem}
.obs-titre{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;
color:var(--mut);font-weight:700;margin:12px 0 4px}
.pese .obs-titre{margin:0 6px 0 0}
ul.observe,ul.trouve{margin:0;padding:0;list-style:none;display:grid;gap:3px}
ul.observe li,ul.trouve li{font-size:.86rem;padding-left:16px;position:relative}
ul.observe li::before,ul.trouve li::before{content:"";position:absolute;left:3px;
top:.62em;width:5px;height:5px;border-radius:50%;background:var(--acc);opacity:.7}
.combinaison{font-size:.82rem;color:var(--mut);margin:7px 0 0;
border-top:1px dashed var(--bd);padding-top:6px}
.intro{background:var(--card);border:1px solid var(--bd);border-radius:14px;
padding:18px 22px;margin:14px 0 6px;box-shadow:var(--sh)}
.intro-t{margin:0 0 8px;font-size:1.05rem;text-transform:none;letter-spacing:0;
color:var(--fg)}
.intro p{margin:.4em 0;font-size:.92rem}
.intro-l{margin:10px 0;padding-left:18px;font-size:.9rem;display:grid;gap:5px}
.intro-p{color:var(--mut)}
.glo-t{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;
color:var(--mut);margin:16px 0 6px}
.glossaire{margin:0;display:grid;gap:5px}
.glo-e{display:grid;grid-template-columns:20ch 1fr;gap:12px;background:var(--bg);
border-radius:8px;padding:6px 10px;font-size:.86rem}
.glo-e dt{font-weight:700}.glo-e dd{margin:0;color:var(--mut)}
@media (max-width:620px){.glo-e{grid-template-columns:1fr;gap:2px}}
.ref-titre{font-size:1.1rem;margin:10px 0 0;display:flex;align-items:center}
.ref{background:var(--card);border:1px solid var(--bd);border-radius:14px;
padding:16px 20px;margin:14px 0;box-shadow:var(--sh);scroll-margin-top:72px}
.ref h4{margin:0;font-size:1.08rem;display:flex;align-items:baseline}
.attendu{font-size:.95rem;background:var(--acc-bg);border-left:3px solid var(--acc);
border-radius:0 8px 8px 0;padding:8px 12px;margin:0;font-weight:600}
.source,.conclut,.ne-conclut{font-size:.88rem;margin:0;padding-left:12px;
border-left:3px solid var(--bd)}
.conclut{border-left-color:var(--ok)}.ne-conclut{border-left-color:var(--ko)}
.conditionnel{font-size:.85rem;border:1px dashed var(--nm);border-radius:8px;
padding:6px 10px;color:var(--mut)}
.ancres-titre{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;
color:var(--mut);font-weight:700;margin:12px 0 4px}
.ancres{margin:0;padding:0;list-style:none;display:grid;gap:4px}
.ancres li{display:flex;gap:12px;align-items:baseline;padding:5px 10px;
border-radius:8px;background:var(--bg);font-size:.86rem}
.ancres li b{min-width:5.5ch}
.note-ancres{font-size:.8rem;color:var(--mut);font-style:italic;margin:8px 0 0}
.cible{outline:3px solid var(--acc);outline-offset:3px;box-shadow:0 0 0 8px var(--acc-bg)}
a.lien-ecart{color:var(--ko);font-weight:700;text-decoration:none;
border-bottom:1px dotted var(--ko)}
.rien{color:var(--mut);font-size:.88rem}
.contradiction{color:var(--ko);font-size:.8rem;font-weight:600;margin-top:3px}
.alerte{background:var(--ko-bg);color:var(--ko);border-radius:10px;
padding:10px 14px;font-size:.88rem;font-weight:600;margin:10px 0}
.ec-projet{margin:26px 0 4px;font-size:1.2rem}
.ecart{background:var(--card);border:1px solid var(--bd);border-radius:14px;
padding:16px 20px;box-shadow:var(--sh);scroll-margin-top:72px}
.ecart h4{margin:0 0 2px;font-size:1.08rem;display:flex;align-items:center}
.ecart-crit{border-top:1px solid var(--bd);margin-top:12px;padding-top:10px}
.ecart-crit h5{margin:0;font-size:.98rem;display:flex;gap:8px;align-items:baseline;
flex-wrap:wrap}
.honnete{font-size:.8rem;color:var(--mut);font-style:italic;margin:2px 0 4px}
ul.recos{margin:0;padding:0;list-style:none;display:grid;gap:5px}
ul.recos li{font-size:.88rem;background:var(--acc-bg);border-radius:8px;
padding:8px 12px;border-left:3px solid var(--acc)}
.sous-tablist{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 4px;
border-bottom:0;position:static;padding:0}
.sous-tab{display:flex;align-items:center;gap:10px;border:1px solid var(--bd);
background:var(--card);color:var(--fg);border-radius:999px;padding:7px 8px 7px 16px;
font:inherit;cursor:pointer;box-shadow:var(--sh);margin:0}
.sous-tab:hover{border-color:var(--acc)}
.sous-tab[aria-selected=true]{border-color:var(--acc);background:var(--acc-bg)}
.st-nom{font-weight:700;font-size:.93rem}
.st-note{font-size:.82rem;border-radius:999px;padding:2px 10px}
@media (max-width:760px){main{padding-bottom:48px}h1{font-size:1.5rem}
.bloc,.ref,.ecart{padding:14px}.globale{font-size:1.2rem}
[role=tab]{padding:10px 10px}}
"""

# Review fix 1: set before first paint, so the no-JS CSS rule never fires with JS.
JS_TETE = "document.documentElement.classList.add('js');"

JS = """
function liste(el){var p=el;while(p&&p.getAttribute('role')!=='tablist'){
 p=p.parentNode;}return p;}
function activer(onglet){
 var l=liste(onglet);if(!l){return;}
 l.querySelectorAll('[role=tab]').forEach(function(u){
  var on=u===onglet;
  u.setAttribute('aria-selected',on);
  u.setAttribute('tabindex',on?'0':'-1');
  var pane=document.getElementById(u.getAttribute('aria-controls'));
  if(pane){pane.hidden=!on;}});}
function activerId(id){
 var t=document.querySelector('[role=tab][aria-controls="'+id+'"]');
 if(t){activer(t);}}
document.querySelectorAll('[role=tab]').forEach(function(t){
 t.addEventListener('click',function(){activer(t);});
 t.addEventListener('keydown',function(ev){
  /* WAI-ARIA tabs pattern: arrows cycle, Home/End jump to the ends. */
  var k=ev.key,sens={ArrowRight:1,ArrowLeft:-1,Home:0,End:0};
  if(!(k in sens)){return;}
  var l=liste(t);var tabs=Array.prototype.slice.call(
   l.querySelectorAll('[role=tab]'));
  var i=k==='Home'?0:k==='End'?tabs.length-1:tabs.indexOf(t)+sens[k];
  var n=tabs[(i+tabs.length)%tabs.length];
  ev.preventDefault();activer(n);n.focus();});});
/* Opens every tabpanel containing the target - the target itself included
   when it IS a tabpanel (review fix 8) - highlights it at once, then scrolls
   to it once the new layout is painted (review fix 3: a scroll computed
   before the panel chain is laid out landed on ~800 px of blank). */
function versAncre(id){
 var el=document.getElementById(id);
 if(!el){return false;}
 var p=el,chaine=[];
 while(p&&p.getAttribute){
  if(p.getAttribute('role')==='tabpanel'&&p.id){chaine.unshift(p.id);}
  p=p.parentNode;}
 chaine.forEach(activerId);
 document.querySelectorAll('.cible').forEach(function(x){
  x.classList.remove('cible');});
 if(el.getAttribute('role')!=='tabpanel'){el.classList.add('cible');}
 el.scrollIntoView({block:'start'});
 requestAnimationFrame(function(){requestAnimationFrame(function(){
  el.scrollIntoView({block:'start'});});});
 return true;}
document.querySelectorAll('a.lien-ref,a.lien-ecart').forEach(function(a){
 a.addEventListener('click',function(ev){
  var id=a.getAttribute('href').slice(1);
  if(!document.getElementById(id)){return;}
  ev.preventDefault();
  history.replaceState(null,'','#'+id);
  versAncre(id);});});
function suivreHash(){var h=location.hash.slice(1);if(h){versAncre(h);}}
if('scrollRestoration' in history){history.scrollRestoration='manual';}
window.addEventListener('hashchange',suivreHash);
window.addEventListener('load',suivreHash);
suivreHash();
"""


def _sous_onglet(id_, libelle, note_html, actif) -> str:
    """One sub-tab of the Résultats pane: project name + its global note."""
    return (
        "<button role='tab' class='sous-tab' id='sub-{id}' aria-controls='{id}' "
        "aria-selected='{a}' tabindex='{tb}'><span class='st-nom'>{lib}</span>"
        "{n}</button>").format(
        id=_e(id_), a="true" if actif else "false", tb="0" if actif else "-1",
        lib=_e(libelle), n=note_html)


def _note_onglet(bloc) -> str:
    etat = _globale_txt(bloc)
    if etat:
        return (f"<span class='st-note n {etat[0]}'><span class='pastille'></span>"
                f"{_e(PERIME if etat[0] == 'perime' else JAMAIS)}</span>")
    g = bloc.get("globale")
    return (f"<span class='st-note n {_classe(g)}'><span class='pastille'></span>"
            f"{_note_txt(g)}</span>")


def _projets(evaluation):
    """[(id, displayed name, block)] of the evaluated projects."""
    return [(f"projet-{i}", nom, b) for i, (nom, b)
            in enumerate(sorted((evaluation.get("projets") or {}).items()))]


def _bloc_qualite(qp) -> str:
    """The separate informative « Qualité du produit » stage: never a note."""
    if not isinstance(qp, dict) or not qp.get("lignes"):
        return ""
    lignes = "".join(
        "<li><b>{lib}</b><div class='valeur'>Valeur : {v}</div>"
        "<div class='meta'>Source : {s}</div>"
        "<div class='meta'>Ne permet pas de conclure : {n}</div></li>".format(
            lib=_e(l.get("libelle")), v=_e(l.get("valeur")), s=_e(l.get("source")),
            n=_e(l.get("ne_permet_pas_de_conclure")))
        for l in qp["lignes"].values())
    return (
        "<section class='bloc qualite-produit'><header><h3>Qualité du produit</h3>"
        "<span class='tag'>informatif — non noté</span></header>"
        "<p class='meta'>Étage séparé : ces relevés ne reçoivent aucune note et "
        "n'entrent ni dans la note globale ni dans aucune moyenne.</p>"
        f"<ul class='dispo'>{lignes}</ul></section>")


def _panneaux_resultats(projets, hub, qualite=None) -> tuple:
    """(sub-tablist, panels) — one panel per project, plus the informative hub."""
    qualite = qualite or {}
    montrer_hub = bool(hub) and _hub_mesure(hub)
    if not projets and not montrer_hub:
        return "", "<p class='meta'>Aucun projet évalué.</p>"
    onglets, panneaux = [], []
    for i, (id_, nom, b) in enumerate(projets):
        onglets.append(_sous_onglet(id_, nom, _note_onglet(b), i == 0))
        panneaux.append(
            f"<div role='tabpanel' class='sous-pane' id='{_e(id_)}' "
            f"aria-labelledby='sub-{_e(id_)}'{'' if i == 0 else ' hidden'}>"
            f"{_bloc(nom, b, 'bloc-' + id_, id_)}{_bloc_qualite(qualite.get(nom))}</div>")
    if montrer_hub:
        premier = not projets
        onglets.append(_sous_onglet(
            "hub", "Dispositif (informatif)",
            "<span class='st-note n nm'><span class='pastille'></span>non noté</span>",
            premier))
        panneaux.append(
            "<div role='tabpanel' class='sous-pane' id='hub' aria-labelledby='sub-hub'"
            f"{'' if premier else ' hidden'}>{_bloc_dispositif(hub, 'bloc-hub')}</div>")
    liste = ("<div role='tablist' class='sous-tablist' aria-label='Projets évalués'>"
             + "".join(onglets) + "</div>")
    return liste, "".join(panneaux)


def _ecarts_bareme(projets) -> str:
    """One « v2 → v3 » line per project whose global note moved with the scale.

    The v2 note is recomputed from the SAME evaluation without the criteria
    added by v3 (``ea.globale_sans_ajouts``): nothing is stored twice."""
    lignes = []
    for _id, nom, b in projets:
        if not isinstance(b, dict) or "axes" not in b:
            continue
        v3 = b.get("globale")
        v2 = ea.globale_sans_ajouts(b)
        if v2 != v3:
            lignes.append(f"<li>{_e(nom)} : v2 → v3 : {_e(v2)} → {_e(v3)}</li>")
    if not lignes:
        return ""
    return ("<ul class='ecarts-bareme meta' aria-label='Notes déplacées par le barème 3'>"
            + "".join(lignes) + "</ul>")


def rendre_page(evaluation: dict) -> str:
    evaluation = evaluation or {}
    projets = _projets(evaluation)
    sous_tabs, panneaux = _panneaux_resultats(projets, evaluation.get("hub"),
                                              evaluation.get("qualite_produit"))
    return (
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Évaluation agentic</title>"
        "<script>" + JS_TETE + "</script>"
        "<style>" + CSS + "</style>"
        # Sans JS : aucun panneau ne reste caché, et les barres d'onglets
        # (inertes sans JS) s'effacent — la page redevient une page longue.
        "<noscript><style>[role=tabpanel][hidden]{display:block!important}"
        "[role=tablist]{display:none}</style></noscript>"
        "</head><body><main>"
        "<h1>Évaluation agentic</h1>"
        "<p class='chapeau'>Cette page note, projet par projet, les pratiques de "
        f"développement (référentiel A, {len(dg.REFERENTIELS['A'])} critères) et les "
        "pratiques de travail avec des assistants d'IA (référentiel B, "
        f"{len(dg.REFERENTIELS['B'])} critères). Chaque note vient "
        "de constats faits dans les fichiers et l'historique git du projet ; "
        "l'onglet « Référentiel » dit, pour chaque critère, ce qui est regardé, "
        "pourquoi, et ce que la note permet ou non de conclure.</p>"
        f"<p class='rupture'>{_e(RUPTURE)}</p>{_ecarts_bareme(projets)}"
        f"<p class='meta'>Barème {_e(evaluation.get('version_bareme'))} · généré le "
        f"{_e(_date(evaluation.get('genere_le')) or evaluation.get('genere_le'))} · "
        f"une mesure de plus de {ea.PEREMPTION_JOURS} jours devient « périmé » · "
        "poids égaux</p>"
        "<div role='tablist' aria-label='Évaluation'>"
        "<button role='tab' id='tab-resultats' aria-controls='pane-resultats' "
        "aria-selected='true' tabindex='0'>Résultats</button>"
        "<button role='tab' id='tab-ecarts' aria-controls='pane-ecarts' "
        "aria-selected='false' tabindex='-1'>Écarts</button>"
        "<button role='tab' id='tab-referentiel' aria-controls='pane-referentiel' "
        "aria-selected='false' tabindex='-1'>Référentiel</button></div>"
        "<div role='tabpanel' id='pane-resultats' aria-labelledby='tab-resultats'>"
        + _intro_resultats()
        + sous_tabs + panneaux
        + "</div><div role='tabpanel' id='pane-ecarts' "
        "aria-labelledby='tab-ecarts' hidden>"
        + INTRO_ECARTS
        + "<h2>Écarts — ce qui manque, et quoi faire</h2>"
        + _pane_ecarts(projets)
        + "</div><div role='tabpanel' id='pane-referentiel' "
        "aria-labelledby='tab-referentiel' hidden>"
        + _intro_referentiel()
        + "<h2>Référentiel — les 23 critères</h2>"
        + _referentiel()
        + "</div></main><script>" + JS + "</script></body></html>\n")


def ecrire_page(evaluation: dict, chemin: str) -> None:
    """Write the self-contained page. Signature frozen (called by the scan)."""
    dossier = os.path.dirname(os.path.abspath(chemin))
    os.makedirs(dossier, exist_ok=True)
    with open(chemin, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(rendre_page(evaluation))


def _detections(racine):
    """{project: detecter(path)} — the scanner's projects inside the hub, else
    the evaluated repository itself (receiving team)."""
    try:
        import scan_projets as sp
    except ImportError:
        sp = None
    if sp is not None:
        config = sp.read_json(sp.CONFIG_PATH) or {}
        projets = [p for p in config.get("projets", [])
                   if os.path.isdir(p.get("chemin", ""))]
        if projets:
            return {p["nom"]: dg.detecter(p["chemin"]) for p in projets}
    nom = os.path.basename(os.path.abspath(racine)) or "projet"
    return {nom: dg.detecter(racine)}


def _chemins(racine, detections):
    """{project: path} for the « Qualité du produit » stage (same source as
    ``_detections``: the scanner configuration, else the evaluated repository)."""
    try:
        import scan_projets as sp
        config = sp.read_json(sp.CONFIG_PATH) or {}
        connus = {p["nom"]: p["chemin"] for p in config.get("projets", [])
                  if os.path.isdir(p.get("chemin", ""))}
    except ImportError:
        connus = {}
    return {nom: connus.get(nom, racine) for nom in detections}


def main(argv=None) -> int:
    # hub: scripts/ -> root ; kit: .claude/evaluation/ -> root
    racine_def = os.path.dirname(ICI)
    if os.path.basename(racine_def) == ".claude":
        racine_def = os.path.dirname(racine_def)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--racine", default=racine_def, help="racine du dépôt évalué")
    ap.add_argument("--sortie", default=None, help="page HTML à écrire")
    args = ap.parse_args(argv)
    sortie = args.sortie or os.path.join(args.racine, "docs", "evaluation-agentic.html")
    now = ea.datetime.now(ea.UTC)
    detections = _detections(args.racine)
    ecrire_page(ea.evaluer(detections, args.racine, now.isoformat(), now,
                           chemins=_chemins(args.racine, detections)), sortie)
    print(f"page écrite : {sortie} ({len(detections)} projet(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
