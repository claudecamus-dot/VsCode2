"""Génère le playbook cycle-produit-bmad depuis le catalogue BMAD (incrément O-B).

Source : _bmad/_config/bmad-help.csv (module « BMad Method »), qui encode déjà le DAG du
cycle produit (colonnes phase / preceded-by / required / outputs). Règle de sélection :
les étapes `required=true`, plus la fermeture transitive de leurs `preceded-by`, plus
EXTRAS (le CSV ne marque pas bmad-code-review comme required alors qu'il clôt le cycle
story DS→CR). L'ordre est (rang de phase, ordre d'apparition dans le CSV) — le CSV est
déjà écrit dans l'ordre logique à phase égale. Une étape terminale `revue-increment`
(obligation projet, hors CSV) est ajoutée d'office.

Sortie déterministe : le fichier généré est versionné et un test regénère puis compare
(dans VSCode2/VSCode3 ; ce hub n'a pas ce test — vérifié le 2026-08-31).
Ne jamais éditer le .md généré à la main.

Env (tests) : BMAD_HELP_CSV et PLAYBOOK_OUT redirigent les chemins.
"""
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = Path(os.environ.get("BMAD_HELP_CSV") or ROOT / "_bmad" / "_config" / "bmad-help.csv")
OUT_PATH = Path(os.environ.get("PLAYBOOK_OUT") or Path(__file__).parent / "playbooks" / "cycle-produit-bmad.md")

MODULE = "BMad Method"
EXTRAS = {"bmad-code-review"}
# La v6.12.0 a changé le VOCABULAIRE des phases du CSV : `plan` / `ship` / `anytime`
# remplacent `1-analysis` … `4-implementation` (seul `bmad-prd` traîne encore l'ancien
# `2-planning`). Les deux jeux sont gardés — une phase inconnue tombait à 99 et sortait
# en fin de playbook sans que rien ne le signale.
PHASE_RANK = {
    "1-analysis": 1, "plan": 2, "2-planning": 2, "3-solutioning": 3,
    "4-implementation": 4, "ship": 4, "anytime": 5,
}
CHECKPOINTS = {
    # `bmad-check-implementation-readiness` a disparu en v6.12.0 : la gate est absorbée
    # par `bmad-sprint-planning`, qui la porte désormais.
    "bmad-sprint-planning": "gate humain : PRD/UX/architecture/stories alignés avant d'engager l'implémentation",
    "bmad-code-review": "issues → retour bmad-build (une relance) ; approuvé → story suivante ou fin d'epic",
}
# Étapes sans colonne `outputs` exploitable dans le CSV.
CONTRAT_OVERRIDES = {
    # `bmad-dev-story` jusqu'en v6.10.0 ; `bmad-build` est l'implémentation officielle
    # depuis la v6.12.0 (l'ancien id n'est plus qu'un shim de redirection).
    "bmad-build": {
        "type": "deterministe",
        "critere": "story implémentée, suite du projet verte",
        "commande": "pytest -q",
    },
    "bmad-code-review": {
        "type": "llm",
        "critere": "revue adversariale rendue avec triage des findings (pas de vérification déterministe possible)",
    },
}
ETAPE_TERMINALE = {
    "id": "revue-increment",
    "agent": "revue-increment",
    "mode": "cascade",
    "modele": "(session)",
    "contrat": {
        "type": "reel",
        "critere": "definition-of-done projet : revue + correctifs appliqués + re-vérification réelle (obligation hors CSV, voir catalogue)",
    },
    "checkpoint": "avant tout commit — action difficilement réversible, proposer, ne pas exécuter unilatéralement",
}


def _cle(row):
    action = (row.get("action") or "").strip()
    return row["skill"] + (":" + action if action else "")


def _selectionner(rows):
    """required=true + fermeture transitive des preceded-by + EXTRAS."""
    par_cle = {_cle(r): r for r in rows}
    retenues = {c for c, r in par_cle.items()
                if r["required"].strip().lower() == "true" or r["skill"] in EXTRAS}
    frontiere = set(retenues)
    while frontiere:
        suivantes = set()
        for c in frontiere:
            pred = (par_cle[c].get("preceded-by") or "").strip()
            if pred and pred in par_cle and pred not in retenues:
                suivantes.add(pred)
        retenues |= suivantes
        frontiere = suivantes
    return [r for r in rows if _cle(r) in retenues]


def _ordonner(retenues, index):
    """Phase, puis ordre du CSV — mais JAMAIS avant son `preceded-by`.

    Le tri par (phase, index) seul suffisait tant que la phase portait l'ordre du cycle
    (`1-analysis` … `4-implementation`). La v6.12.0 l'a réduite à `plan`/`ship`/`anytime` :
    tout le cadrage retombe dans un seul rang, et l'ordre du CSV y place `bmad-prd` AVANT
    `bmad-product-brief` dont il déclare pourtant dépendre. Un playbook qui rédige le PRD
    avant le brief est faux sans être vide — donc silencieux. On dépile en topologique
    (Kahn), la paire (phase, index) ne servant plus que de départage stable.
    """
    par_cle = {_cle(r): r for r in retenues}
    prio = {c: (PHASE_RANK.get(r["phase"].strip(), 99), index[id(r)])
            for c, r in par_cle.items()}
    restants = dict(par_cle)
    sortie = []
    while restants:
        prets = [c for c, r in restants.items()
                 if (r.get("preceded-by") or "").strip() not in restants]
        if not prets:                      # cycle dans le CSV : on ne perd personne
            prets = sorted(restants, key=lambda c: prio[c])[:1]
        choisi = min(prets, key=lambda c: prio[c])
        sortie.append(restants.pop(choisi))
    return sortie


def _etape(row):
    cle = _cle(row)
    outputs = (row.get("outputs") or "").strip()
    localisation = (row.get("output-location") or "").strip()
    if row["skill"] in CONTRAT_OVERRIDES:
        contrat = CONTRAT_OVERRIDES[row["skill"]]
    elif outputs:
        critere = f"artefact « {outputs} » produit"
        if localisation:
            critere += f" dans {localisation}"
        contrat = {"type": "deterministe", "critere": critere}
    else:
        contrat = {"type": "llm", "critere": "étape déclarée terminée par la skill, sans artefact vérifiable dans le CSV"}
    return {
        "id": cle.replace(":", "-"),
        "agent": cle,
        "mode": "cascade",
        "modele": "(session)",
        "contrat": contrat,
        "checkpoint": CHECKPOINTS.get(row["skill"], False),
    }


def generer():
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if r["module"] == MODULE and r["skill"] != "_meta"]
    retenues = _selectionner(rows)
    index = {id(r): i for i, r in enumerate(rows)}
    retenues = _ordonner(retenues, index)

    playbook = {
        "nom": "cycle-produit-bmad",
        "description": "Cycle produit BMAD complet (v6.12.0) : brief → PRD → architecture → epics/stories → sprint (qui porte la gate readiness) → build → code-review, clos par revue-increment.",
        "statut": "jamais-joue",
        "source": "genere:generate_bmad_playbook.py",
        "declencheurs": [
            "dérouler le cycle produit BMAD sur une idée/feature",
            "demande explicite de workflow BMAD multi-étapes (sinon : bmad-help, une étape à la fois)",
        ],
        "etapes": [_etape(r) for r in retenues] + [ETAPE_TERMINALE],
        "regle_reprise": "une relance ciblée par étape en échec de contrat, puis escalade utilisateur avec l'état réel",
    }

    md = (
        "# Playbook `cycle-produit-bmad` — GÉNÉRÉ, ne pas éditer à la main\n\n"
        "Généré par `generate_bmad_playbook.py` depuis `_bmad/_config/bmad-help.csv`\n"
        "(module « BMad Method » : étapes required + fermeture des preceded-by + code-review).\n"
        "Pour modifier : éditer le script ou le CSV, puis regénérer :\n\n"
        "```bash\npy .claude/orchestration/generate_bmad_playbook.py\n```\n\n"
        "Statut `jamais-joue` assumé (garde-fou « playbooks morts ») : le cycle BMAD complet\n"
        "n'a jamais été déroulé sur ce projet — ne le proposer que sur demande explicite,\n"
        "conformément à la règle de routage BMAD du catalogue.\n\n"
        "```json\n" + json.dumps(playbook, ensure_ascii=False, indent=2) + "\n```\n"
    )
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(md, encoding="utf-8", newline="\n")
    return len(playbook["etapes"])


if __name__ == "__main__":
    try:
        n = generer()
    except Exception as exc:  # chemin CSV absent, colonne manquante…
        print(f"erreur: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"{OUT_PATH.name} : {n} etape(s)")
