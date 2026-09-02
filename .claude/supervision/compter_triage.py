"""Compteur déterministe du backlog de revue (.claude/triage/*.md) — étage 1, 0 token.

Pourquoi ce script existe (constat superviseur du 2026-09-02, catégorie `autre`) : les
revues adversariales sont la dépense la plus lourde du dispositif, et ce qu'elles laissent
OUVERT n'était mesuré nulle part. Ni le scan, ni `routing-hints.json`, ni le wiki ne
comptaient ces constats — 115 identifiants dormaient dans trois fichiers sans qu'aucun
compteur ne les voie. Une revue facturée dont les constats ne sont ni corrigés ni arbitrés
est une dépense sans achat.

Il est SÉPARÉ de `scan_transcripts.py` à dessein : ce dernier porte la bannière « GÉNÉRÉ —
NE PAS ÉDITER LOCALEMENT » (source de vérité au hub VSCode5). Y ajouter le compteur
localement le ferait écraser à la prochaine propagation. La mesure vit donc ici, où elle
survit ; son intégration au tableau de bord est un chantier du hub.

Usage : py .claude/supervision/compter_triage.py [--json]
Env (tests) : AGENT_SUPERVISION_TRIAGE (dossier des fichiers de triage).
"""
import datetime as dt
import glob
import json
import os
import re
import sys

TRIAGE_DIR = os.environ.get("AGENT_SUPERVISION_TRIAGE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "triage"
)

# Vocabulaire figé des statuts (gabarit documenté dans revue-increment/SKILL.md). Mesuré
# sur les fichiers réels du 2026-09-02 avant d'être figé : `differe` 47, `corrige` 16,
# `partiel` — un vocabulaire inventé aurait compté zéro sur le parc existant.
OUVERTS = ("ouvert", "differe", "partiel")
CLOS = ("corrige", "traite", "ecarte")
STATUTS = OUVERTS + CLOS
# Une ligne de séparation Markdown (`| --- | --- |`) n'est pas un constat.
SEPARATEUR = re.compile(r"^[\s|:-]+$")


def _normaliser(cellule: str) -> str:
    """« **Corrigé** (2bf44b7, complet) » -> « corrige ».

    Le texte libre APRÈS le statut est conservé dans les fichiers et il est utile (il dit
    quel commit a corrigé quoi) : on ne le supprime pas, on ne le compte simplement pas.
    Les accents sont repliés parce que les deux graphies coexistent dans le parc.
    """
    texte = cellule.strip().strip("*_` ").lower()
    for accent, plat in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("ç", "c")):
        texte = texte.replace(accent, plat)
    return texte


def statut_de(cellule: str) -> str:
    """Le statut porté par cette cellule, ou "" si elle n'en porte aucun."""
    texte = _normaliser(cellule)
    for statut in STATUTS:
        if texte.startswith(statut):
            return statut
    return ""


def compter_fichier(chemin: str) -> dict:
    """{statut: n} + `sans_statut` : lignes de tableau qui ressemblent à un constat mais
    dont la dernière cellule ne porte aucun statut du vocabulaire — donc invisibles au
    comptage. Les signaler vaut mieux que les ignorer : c'est exactement la raison pour
    laquelle personne ne comptait ce backlog."""
    par_statut, sans_statut = {}, 0
    with open(chemin, encoding="utf-8") as fh:
        for ligne in fh:
            if not ligne.startswith("|") or SEPARATEUR.match(ligne):
                continue
            cellules = [c.strip() for c in ligne.strip().strip("|").split("|")]
            if len(cellules) < 2:
                continue
            # Un en-tête de tableau n'est pas un constat.
            if _normaliser(cellules[0]) in ("id", "constat", "promesse", "sev.", "severite"):
                continue
            statut = statut_de(cellules[-1])
            if statut:
                par_statut[statut] = par_statut.get(statut, 0) + 1
            else:
                sans_statut += 1
    par_statut["sans_statut"] = sans_statut
    return par_statut


def mesurer(dossier: str = None) -> dict:
    dossier = dossier or TRIAGE_DIR
    fichiers, total = {}, {}
    for chemin in sorted(glob.glob(os.path.join(dossier, "*.md"))):
        compte = compter_fichier(chemin)
        fichiers[os.path.basename(chemin)] = compte
        for statut, n in compte.items():
            total[statut] = total.get(statut, 0) + n
    ouverts = sum(total.get(s, 0) for s in OUVERTS)
    # Âge du plus ancien fichier portant ENCORE un constat ouvert : un backlog de 3 jours
    # et un backlog de 3 mois ne disent pas la même chose du dispositif.
    plus_ancien, jours = "", 0
    for nom, compte in fichiers.items():
        if not any(compte.get(s) for s in OUVERTS):
            continue
        # Les fichiers sont nommés `AAAA-MM-JJ-<sujet>.md` (convention des revues).
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", nom)
        if not m:
            continue
        age = (dt.date.today() - dt.date(*map(int, m.groups()))).days
        if age >= jours:
            plus_ancien, jours = nom, age
    return {"fichiers": fichiers, "total": total, "ouverts": ouverts,
            "plus_ancien": plus_ancien, "age_jours": jours}


def main(argv) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    m = mesurer()
    if "--json" in argv:
        print(json.dumps(m, ensure_ascii=False, indent=1))
        return 0
    if not m["fichiers"]:
        print("compter_triage : aucun fichier de triage.")
        return 0
    for nom, compte in m["fichiers"].items():
        detail = ", ".join(f"{s}={n}" for s, n in sorted(compte.items()) if n)
        print(f"  {nom} : {detail or 'aucun constat'}")
    print(f"compter_triage : {m['ouverts']} constat(s) de revue ouvert(s)"
          + (f", le plus ancien depuis {m['age_jours']} j ({m['plus_ancien']})"
             if m["plus_ancien"] else ""))
    hors = m["total"].get("sans_statut", 0)
    if hors:
        print(f"  ({hors} ligne(s) de tableau sans statut du vocabulaire "
              f"{'/'.join(STATUTS)} — non comptees, gabarit dans revue-increment)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
