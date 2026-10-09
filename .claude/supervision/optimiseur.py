"""Optimiseur (chantier `optimiser`, 2026-10-03) : compare des variantes de pilotage
sur les runs REELLEMENT journalises, et dit si une variante a gagne sa place.

Cellule de comparaison = (playbook, gabarit, topologie). Une cellule dont l'un des trois
champs manque est `inconnu` : elle est EXCLUE de toute porte et comptee dans
`sans_champ` -- jamais devinee. Les runs qui portent `bras` (temoin|variante) sortent
des cellules et ne se comparent QUE par paires (meme `tache_id` aux deux bras).

La porte est une CONJONCTION (aucun score compose, aucune ponderation) :
  n_resolus >= n_min aux deux bras  ET  delta_succes > seuil_succes
  ET  cout_cand <= cout_ref * (1 + budget)  ET  delta_p90 <= p90_max.
Cout ou p90 manquant d'un bras -> `donnees-insuffisantes`, jamais `gagnant`.

Ne PROPOSE rien de plus qu'un verdict : n'ecrit ni `runs.jsonl`, ni les playbooks, ni
`arbitrages.json` ; `applique` vaut toujours False (R4 : le superviseur propose,
l'utilisateur arbitre). Stdlib seule, deterministe (aucune horloge, aucun alea).

  py .claude/supervision/optimiseur.py --rapport    # tableau + verdict par playbook
  py .claude/supervision/optimiseur.py --json       # meme calcul, JSON
  py .claude/supervision/optimiseur.py --salles     # tour 2 joue, rendement par lentille

Lot 2 (2026-10-03) : bras `topologie-reduite` (compare a `variante`, le bras multi-agent) ;
un candidat n'est `gagnant` que s'il gagne sur >= 2 familles de taches (`famille`) ;
`fan-out degenere` (branches_lancees <= 1 ou duree ~ somme des branches) compte par cellule ;
tendance tokens/run resolu par moitie de cellule : rapport seul, jamais dans la porte.
"""
import json
import math
import os
import re
import sys
import unicodedata

_ICI = os.path.dirname(os.path.abspath(__file__))


def _percentile_local(valeurs_triees, p):
    """Copie de `convergence.percentile` (un test verrouille la parite)."""
    if not valeurs_triees:
        return None
    k = (len(valeurs_triees) - 1) * p
    bas, haut = math.floor(k), math.ceil(k)
    if bas == haut:
        return float(valeurs_triees[bas])
    return valeurs_triees[bas] + (valeurs_triees[haut] - valeurs_triees[bas]) * (k - bas)


try:
    if _ICI not in sys.path:
        sys.path.append(_ICI)
    from convergence import percentile  # noqa: E402
except ImportError:  # kit propage sans convergence.py : meme calcul, copie locale
    percentile = _percentile_local

ROOT = os.path.dirname(os.path.dirname(_ICI))
RUNS_PATH = os.environ.get("AGENT_ORCHESTRATION_RUNS") or os.path.join(
    ROOT, ".claude", "orchestration", "runs.jsonl")
CONFIG_PATH = os.environ.get("AGENT_OPTIMISEUR_CONFIG") or os.path.join(
    ROOT, ".claude", "orchestration", "optimiseur.json")

INCONNU = "inconnu"
VERDICTS = ("gagnant", "egalite", "donnees-insuffisantes", "rejete")
MIN_DUREES_P90 = 5   # en dessous, un p90 n'est pas une mesure
BRAS_CANDIDATS = ("variante", "topologie-reduite")  # candidats d'une paire (ref : temoin / variante)
TOPOLOGIES_FAN_OUT = ("fan-out", "salle", "workflow")
MIN_FAMILLES_GAGNANT = 2   # estimation non mesuree : familles de taches gagnees pour etre `gagnant`
MIN_TACHES_FAMILLE = 3     # estimation non mesuree : taches appariees minimales par famille
MIN_BASCULES_FAMILLE = 2   # estimation non mesuree : taches basculees en succes pour gagner une famille
FACTEUR_SERIE = 0.9        # estimation non mesuree : duree_s >= 0.9 x branches x branche_max = serie
MIN_TENDANCE = 4           # estimation non mesuree : runs resolus chiffres requis pour une tendance

# Seuils par defaut -- TOUS « estimation non mesuree » : aucun n'est tire d'une mesure
# de la flotte (0 run porte gabarit/topologie/tokens/duree au 2026-10-03). Ils attendent
# l'arbitrage de l'utilisateur ; `optimiseur.json` les remplace champ par champ.
SEUILS_DEFAUT = {
    "n_min": 10,            # estimation non mesuree : runs resolus requis PAR bras
    "seuil_succes": 0.15,   # estimation non mesuree : gain de taux de succes exige (strict)
    "budget": 0.0,          # estimation non mesuree : surcout de tokens tolere (0 = aucun)
    "p90_max": 0.20,        # estimation non mesuree : degradation tolerée du p90 de duree
}

# Copie de `scripts/scan_projets.MARQUEURS_VERIFICATION` (le scan n'est pas propage aux
# cibles) ; un test verrouille la parite.
MARQUEURS_VERIFICATION = (
    r"pytest", r"py -m", r"mesur", r"rendu regard", r"\bouvert",
    r"\b[0-9a-f]{7,40}\b", r"passed", r"--check",
)


# --- chargement ------------------------------------------------------------------

def charger(chemin=None):
    """(runs lisibles, nombre de lignes illisibles) de `runs.jsonl`. Lecture seule.

    Une ligne qui n'est pas un objet JSON est comptee, jamais perdue en silence."""
    chemin = chemin or RUNS_PATH
    runs, illisibles = [], 0
    try:
        fh = open(chemin, encoding="utf-8", errors="replace")
    except OSError:
        return runs, illisibles
    with fh:
        for ligne in fh:
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                run = json.loads(ligne)
            except ValueError:
                illisibles += 1
                continue
            if isinstance(run, dict):
                runs.append(run)
            else:
                illisibles += 1
    return runs, illisibles


def charger_runs(chemin=None):
    """Runs lisibles seuls (voir `charger`)."""
    return charger(chemin)[0]


def charger_seuils(chemin=None):
    """Defauts, surcharges champ par champ par `optimiseur.json` s'il existe et est valide."""
    seuils = dict(SEUILS_DEFAUT)
    try:
        with open(chemin or CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return seuils
    if isinstance(cfg, dict):
        for k in seuils:
            v = cfg.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                seuils[k] = v
    return seuils


# --- classification d'un run -----------------------------------------------------

def _champ(run, nom):
    v = run.get(nom)
    return v.strip() if isinstance(v, str) and v.strip() else INCONNU


def cle_groupe(run):
    """(playbook, gabarit, topologie) ; `inconnu` pour tout champ absent ou vide."""
    return (_champ(run, "playbook"), _champ(run, "gabarit"), _champ(run, "topologie"))


def cle_complete(cle):
    return INCONNU not in cle


def est_resolu(run):
    """Run terminal compte au denominateur : tout sauf en-cours / en-attente-validation."""
    return run.get("resultat") in ("succes", "partiel", "echec")


def est_succes(run):
    """True ssi `succes` ET pas un livrable utilisateur declare sans quittance ouverte."""
    if run.get("resultat") != "succes":
        return False
    if run.get("livrable_utilisateur") is True:
        val = run.get("validation")
        if not (isinstance(val, dict) and str(val.get("artefact_ouvert") or "").strip()):
            return False
    return True


def _reprises_invalides(run):
    """`reprises` present mais pas un nombre fini >= 0 (None = absent, pas invalide)."""
    return run.get("reprises") is not None and _nombre(run.get("reprises")) is None


def _nombre(v):
    """Nombre fini >= 0, sinon None : NaN/inf ne sont jamais une mesure."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0:
        return v
    return None


def _sans_accents(txt):
    return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()


def est_fan_out_degenere(run):
    """True si un run fan-out/salle/workflow s'est en fait deroule en serie.

    `branches_lancees` <= 1, OU `duree_branche_max_s` present et
    duree_s >= FACTEUR_SERIE x branches_lancees x duree_branche_max_s (la duree totale ne
    vaut pas mieux que les branches mises bout a bout : aucun parallelisme gagne).
    Champs absents/invalides -> False : on ne devine pas un effondrement."""
    if run.get("topologie") not in TOPOLOGIES_FAN_OUT:
        return False
    b = _nombre(run.get("branches_lancees"))
    if b is None or b == 0:        # 0 branche : non mesurable, jamais « degenere »
        return False
    if b <= 1 and run.get("topologie") != "workflow":   # un workflow peut n'avoir qu'une branche
        return True
    dmax, d = _nombre(run.get("duree_branche_max_s")), _nombre(run.get("duree_s"))
    # un tour 2 rejoue les voix : la serie attendue est doublee (estimation non mesuree)
    tours = 2 if run.get("tour2") is True else 1
    return (b > 1 and dmax is not None and d is not None
            and d >= FACTEUR_SERIE * b * dmax * tours)


def tendance_tokens(runs):
    """Tokens par run RESOLU, par rang chronologique (`ts`) dans la cellule : moyenne de la 1re moitie contre la
    2de. None sous MIN_TENDANCE runs chiffres. Lecture seule : jamais dans la porte."""
    # tri STABLE par `ts` (les runs sans `ts` passent en tete, dans l'ordre du fichier)
    ordonnes = sorted(runs, key=lambda r: r["ts"] if isinstance(r.get("ts"), str) else "")
    vals = [v for v in (_nombre(r.get("tokens")) for r in ordonnes if est_resolu(r))
            if v is not None]
    if len(vals) < MIN_TENDANCE:
        return None
    h = len(vals) // 2
    return {"n": len(vals), "premiere": sum(vals[:h]) / h, "seconde": sum(vals[-h:]) / h}


def _a_marqueur(run):
    notes = _sans_accents(str(run.get("notes") or "")).lower()
    return any(re.search(m, notes) for m in MARQUEURS_VERIFICATION)


# --- statistiques ----------------------------------------------------------------

def stats_groupe(runs):
    """Statistiques d'un groupe de runs (denominateur = runs resolus uniquement)."""
    resolus = [r for r in runs if est_resolu(r)]
    n = len(resolus)
    succes = [r for r in resolus if est_succes(r)]
    couts = [_nombre(r.get("tokens")) for r in resolus]
    couts = [c for c in couts if c is not None]
    durees = sorted(d for d in (_nombre(r.get("duree_s")) for r in resolus) if d is not None)
    return {
        "n": len(runs),
        "n_resolus": n,
        "n_succes": len(succes),
        "taux_succes": (len(succes) / n) if n else None,
        "reprises_par_run": (sum((_nombre(r.get("reprises")) or 0) for r in resolus) / n)
                            if n else None,
        "reprises_non_numeriques": sum(1 for r in resolus if _reprises_invalides(r)),
        "n_cout": len(couts),
        "cout_moy": (sum(couts) / len(couts)) if couts else None,
        "p90_duree": percentile(durees, 0.9) if len(durees) >= MIN_DUREES_P90 else None,
        "succes_avec_marqueur": (sum(1 for r in succes if _a_marqueur(r)) / len(succes))
                                if succes else None,
        "fan_out_degenere": sum(1 for r in runs if est_fan_out_degenere(r)),
        "tendance_tokens": tendance_tokens(runs),
    }


# --- la porte --------------------------------------------------------------------

def evaluer_porte(ref, cand, seuils=None):
    """Verdict de `cand` face a `ref` (deux dicts de `stats_groupe`).

    Retourne {verdict, motifs, n_ref, n_cand, n_requis, delta_succes}. Les n et le n
    requis sont TOUJOURS presents : un verdict sans son effectif ne se lit pas.
    """
    s = {**SEUILS_DEFAUT, **(seuils or {})}
    n_ref, n_cand = ref.get("n_resolus") or 0, cand.get("n_resolus") or 0
    out = {"verdict": "donnees-insuffisantes", "motifs": [], "n_ref": n_ref,
           "n_cand": n_cand, "n_requis": s["n_min"], "delta_succes": None}
    motifs = out["motifs"]
    if n_ref < s["n_min"] or n_cand < s["n_min"]:
        motifs.append(f"effectif insuffisant : {n_ref} / {n_cand} runs resolus, "
                      f"{s['n_min']} requis par bras")
        return out
    manque = []
    if (ref.get("cout_moy") is None or cand.get("cout_moy") is None
            or (ref.get("n_cout") or 0) < s["n_min"] or (cand.get("n_cout") or 0) < s["n_min"]):
        manque.append(f"cout non mesure (tokens sur >= {s['n_min']} runs par bras)")
    if ref.get("p90_duree") is None or cand.get("p90_duree") is None:
        manque.append(f"p90 duree (>= {MIN_DUREES_P90} durees par bras)")
    if manque:
        motifs.append("mesure manquante sur un bras : " + ", ".join(manque))
        return out
    delta = round(cand["taux_succes"] - ref["taux_succes"], 9)
    out["delta_succes"] = delta
    cout_ok = cand["cout_moy"] <= ref["cout_moy"] * (1 + s["budget"])
    if ref["p90_duree"] > 0:
        d_p90 = (cand["p90_duree"] - ref["p90_duree"]) / ref["p90_duree"]
        p90_ok = round(d_p90, 9) <= s["p90_max"]
    else:
        d_p90, p90_ok = None, cand["p90_duree"] <= ref["p90_duree"]
    if delta < -s["seuil_succes"]:
        out["verdict"] = "rejete"
        motifs.append(f"succes en baisse de {-delta:.0%} (seuil {s['seuil_succes']:.0%})")
    elif delta <= s["seuil_succes"]:
        out["verdict"] = "egalite"
        motifs.append(f"ecart de succes {delta:+.0%} dans le seuil ({s['seuil_succes']:.0%}, strict)")
    elif cout_ok and p90_ok:
        out["verdict"] = "gagnant"
        motifs.append(f"succes {delta:+.0%}, cout et p90 dans le budget")
    else:
        out["verdict"] = "rejete"
        motifs.append(f"succes {delta:+.0%} mais " + " et ".join(
            m for ok, m in ((cout_ok, "cout au-dessus du budget"),
                            (p90_ok, "p90 duree degrade au-dela du seuil")) if not ok))
    return out


# --- calcul par playbook ---------------------------------------------------------

def _issue_de_tache(runs_tache):
    """Une tache = une issue par bras : le dernier run `succes` si l'un existe (un bras qui
    reessaie n'est pas penalise), sinon le dernier run resolu. None si aucun n'est resolu."""
    resolus = [r for r in runs_tache if est_resolu(r)]
    if not resolus:
        return None
    gagnants = [r for r in resolus if est_succes(r)]
    return (gagnants or resolus)[-1]


def _tache(runs_tache):
    """Issue de la tache (voir `_issue_de_tache`) portant le COUT REEL : tokens et duree
    sont la SOMME de tous les runs resolus de la tache, tentatives echouees comprises.
    Une tentative sans valeur numerique rend la somme inconnue (None), jamais sous-estimee."""
    issue = _issue_de_tache(runs_tache)
    if issue is None:
        return None
    resolus = [r for r in runs_tache if est_resolu(r)]
    out = dict(issue)
    for champ in ("tokens", "duree_s"):
        vals = [_nombre(r.get(champ)) for r in resolus]
        out[champ] = sum(vals) if all(v is not None for v in vals) else None
    return out


def _famille(run):
    """Famille normalisee (espaces repliés, casse ignoree) : « Dev » et « dev  » sont UNE
    famille -- sinon une faute de frappe fabrique la 2e famille que la regle exige."""
    f = run.get("famille")
    if isinstance(f, str) and f.strip():
        return " ".join(f.split()).casefold()
    return None


def _familles_des_taches(issues_ref, issues_cand, communs):
    """({famille: [taches]}, nb sans famille, nb a famille discordante entre les bras).
    Une tache a famille discordante est rangee sous la famille de la reference."""
    fam, sans, discord = {}, 0, 0
    for t in communs:
        a, b = _famille(issues_ref[t]), _famille(issues_cand[t])
        if a and b and a != b:
            discord += 1
        f = a or b
        if f:
            fam.setdefault(f, []).append(t)
        else:
            sans += 1
    return fam, sans, discord


def regle_familles(issues_ref, issues_cand, communs, seuils=None):
    """Regle des >= 2 familles : (verdict a imposer ou None, motif, familles_gagnees).

    Une famille est ELIGIBLE a >= MIN_TACHES_FAMILLE taches appariees. Elle est GAGNEE si
    le taux de succes du candidat depasse celui de la reference de plus que `seuil_succes`
    (strict) ET qu'au moins MIN_BASCULES_FAMILLE taches ont bascule en succes (un seul
    bascule sur 3 taches = 0.33 de gain, du bruit). Une famille eligible PERDUE (baisse de
    plus que `seuil_succes`) fait `rejete`, quel que soit le reste.
    Moins de 2 familles distinctes renseignees -> `donnees-insuffisantes` (une seule
    famille ne prouve aucune generalite) ; >= 2 familles mais moins de 2 gagnees -> `egalite`."""
    s = {**SEUILS_DEFAUT, **(seuils or {})}
    fam = _familles_des_taches(issues_ref, issues_cand, communs)[0]
    if len(fam) < MIN_FAMILLES_GAGNANT:
        return ("donnees-insuffisantes",
                f"une seule famille de taches renseignee ({len(fam)} sur "
                f"{MIN_FAMILLES_GAGNANT} requises) : `famille` absent ou unique", [])
    gagnees, perdues = [], []
    for f in sorted(fam):
        if len(fam[f]) < MIN_TACHES_FAMILLE:
            continue
        a = stats_groupe([issues_ref[t] for t in fam[f]])
        b = stats_groupe([issues_cand[t] for t in fam[f]])
        delta = round(b["taux_succes"] - a["taux_succes"], 9)
        if delta < -s["seuil_succes"]:
            perdues.append(f)
        elif delta > s["seuil_succes"] and b["n_succes"] - a["n_succes"] >= MIN_BASCULES_FAMILLE:
            gagnees.append(f)
    if perdues:
        return ("rejete", f"famille perdue : {', '.join(perdues)}", gagnees)
    if len(gagnees) < MIN_FAMILLES_GAGNANT:
        return ("egalite", f"gagnant global mais {len(gagnees)} famille(s) gagnee(s) "
                f"sur {MIN_FAMILLES_GAGNANT} requises", gagnees)
    return None, f"{len(gagnees)} famille(s) gagnee(s) : {', '.join(gagnees)}", gagnees


def comparer_bras(runs, seuils=None, ref_bras="temoin", cand_bras="variante"):
    """Reference vs candidat (defaut : temoin vs variante) sur les seules taches PAIREES et
    resolues aux DEUX bras. `topologie-reduite` se compare a `variante` (le bras
    multi-agent) par le meme chemin. Un `gagnant` doit en plus gagner sur >= 2 familles.

    Les runs d'une meme (bras, tache_id) sont replies en une issue ; l'effectif se compte
    en taches appariees. Garder une tache resolue d'un seul cote ferait une selection
    biaisee (les echecs d'un bras disparaitraient de la comparaison)."""
    par_bras = {ref_bras: {}, cand_bras: {}}
    for r in runs:
        b, t = r.get("bras"), r.get("tache_id")
        if b in par_bras and isinstance(t, str) and t.strip():
            par_bras[b].setdefault(t, []).append(r)
    issues = {b: {t: _tache(rs) for t, rs in par_bras[b].items()} for b in par_bras}
    communs = sorted(t for t in set(issues[ref_bras]) & set(issues[cand_bras])
                     if issues[ref_bras][t] is not None and issues[cand_bras][t] is not None)
    exclues = len((set(issues[ref_bras]) | set(issues[cand_bras])) - set(communs))
    ref = stats_groupe([issues[ref_bras][t] for t in communs])
    cand = stats_groupe([issues[cand_bras][t] for t in communs])
    if not communs:
        s = {**SEUILS_DEFAUT, **(seuils or {})}
        return {"verdict": "donnees-insuffisantes", "motifs": ["non apparie : aucune tache "
                "(tache_id) resolue aux deux bras"], "n_ref": 0, "n_cand": 0,
                "n_requis": s["n_min"], "delta_succes": None, "paires": 0,
                "taches_exclues": exclues, ref_bras: ref, cand_bras: cand}
    res = evaluer_porte(ref, cand, seuils)
    res.update({"paires": len(communs), "taches_exclues": exclues,
                ref_bras: ref, cand_bras: cand})
    _, sans_fam, discord = _familles_des_taches(issues[ref_bras], issues[cand_bras], communs)
    res["taches_sans_famille"], res["taches_famille_discordante"] = sans_fam, discord
    if sans_fam:
        res["motifs"].append(f"{sans_fam} tache(s) appariee(s) sans `famille` (hors regle)")
    if discord:
        res["motifs"].append(f"{discord} tache(s) dont la `famille` differe entre les bras "
                             "(rangee(s) sous celle de la reference)")
    if res["verdict"] == "gagnant":
        verdict, motif, gagnees = regle_familles(issues[ref_bras], issues[cand_bras],
                                                 communs, seuils)
        res["familles_gagnees"] = gagnees
        res["motifs"].append(motif)
        if verdict:
            res["verdict"] = verdict
    return res


_ORDRE = ("gagnant", "rejete", "egalite", "donnees-insuffisantes")


def calculer(runs, seuils=None, illisibles=0):
    """Rapport complet, pur et deterministe. `applique` est toujours False."""
    s = {**SEUILS_DEFAUT, **(seuils or {})}
    cellules, bras_runs, sans_champ, bras_inconnu = {}, {}, 0, 0
    reprises_nn = sum(1 for r in runs if _reprises_invalides(r))
    for r in runs:
        cle = cle_groupe(r)
        pb = cle[0]
        if r.get("bras") in ("temoin", "variante", "topologie-reduite"):
            if pb != INCONNU:
                bras_runs.setdefault(pb, []).append(r)
            else:
                sans_champ += 1
            continue
        if r.get("bras") is not None:       # valeur hors enum : ignoree, mais comptee
            bras_inconnu += 1
            continue
        if not cle_complete(cle):
            sans_champ += 1
            continue
        cellules.setdefault(pb, {}).setdefault(cle, []).append(r)
    playbooks = {}
    for pb in sorted(set(cellules) | set(bras_runs)):
        cells = {c: stats_groupe(rs) for c, rs in cellules.get(pb, {}).items()}
        ordre = sorted(cells, key=lambda c: (-cells[c]["n_resolus"], c))
        verdicts, ref = [], (ordre[0] if ordre else None)
        for c in ordre[1:]:
            v = evaluer_porte(cells[ref], cells[c], s)
            v.update({"reference": list(ref), "candidat": list(c)})
            verdicts.append(v)
        entree = {
            "cellules": [{"cle": list(c), **cells[c]} for c in ordre],
            "verdicts": verdicts,
            "n": max([cells[c]["n_resolus"] for c in ordre] or [0]),
            "n_requis": s["n_min"],
        }
        if pb in bras_runs:
            entree["bras"] = comparer_bras(bras_runs[pb], s)
            verdicts = verdicts + [entree["bras"]]
            if any(x.get("bras") == "topologie-reduite" for x in bras_runs[pb]):
                entree["bras_topologie_reduite"] = comparer_bras(
                    bras_runs[pb], s, "variante", "topologie-reduite")
                verdicts = verdicts + [entree["bras_topologie_reduite"]]
        if not verdicts:
            entree["motifs"] = ["une seule cellule connue : rien a comparer"]
        entree["verdict"] = next((v for v in _ORDRE
                                  if any(x["verdict"] == v for x in verdicts)),
                                 "donnees-insuffisantes")
        playbooks[pb] = entree
    return {"applique": False, "seuils": s, "sans_champ": sans_champ,
            "bras_inconnu": bras_inconnu, "lignes_illisibles": illisibles,
            "reprises_non_numeriques": reprises_nn,
            "n_runs": len(runs), "playbooks": playbooks}


def rapport_texte(rap):
    s = rap["seuils"]
    lignes = [f"optimiseur : {rap['n_runs']} run(s), {rap['sans_champ']} sans "
              "(playbook, gabarit, topologie) complets (exclus des portes). applique=false.",
              f"{rap.get('lignes_illisibles', 0)} ligne(s) illisible(s) ignoree(s), "
              f"{rap.get('bras_inconnu', 0)} run(s) a `bras` hors enum ignore(s), "
              f"{rap.get('reprises_non_numeriques', 0)} run(s) a reprises non numeriques "
              "(comptees 0).",
              f"seuils (estimation non mesuree) : n_min={s['n_min']} "
              f"seuil_succes={s['seuil_succes']} budget={s['budget']} p90_max={s['p90_max']}"]
    if not rap["playbooks"]:
        lignes.append("aucune cellule complete : verdict donnees-insuffisantes partout.")
    for pb, e in rap["playbooks"].items():
        lignes.append(f"\n{pb} : {e['verdict']} (n={e['n']}/{e['n_requis']})")
        for c in e["cellules"]:
            f = lambda v, p=2: "-" if v is None else f"{v:.{p}f}"  # noqa: E731
            lignes.append(f"  {'/'.join(c['cle'])}: n_resolus={c['n_resolus']} "
                          f"succes={f(c['taux_succes'])} reprises/run={f(c['reprises_par_run'])} "
                          f"cout={f(c['cout_moy'], 0)} p90={f(c['p90_duree'], 0)} "
                          f"marqueur={f(c['succes_avec_marqueur'])} "
                          f"fan-out degenere={c.get('fan_out_degenere', 0)}")
            t = c.get("tendance_tokens")
            if t:
                lignes.append(f"    tokens/run resolu (rapport seul, hors porte) : "
                              f"1re moitie={t['premiere']:.0f} 2de moitie={t['seconde']:.0f} "
                              f"(n={t['n']})")
        for v in e["verdicts"]:
            nom = "/".join(v["candidat"]) if "candidat" in v else (
                "bras topologie-reduite" if "topologie-reduite" in v else "bras variante")
            lignes.append(f"  -> {nom} : {v['verdict']} (n {v['n_ref']}/{v['n_cand']}, "
                          f"requis {v['n_requis']}) {'; '.join(v['motifs'])}")
    return "\n".join(lignes)


# --- indicateurs des salles (lecture seule du journal) -----------------------------

DERNIERES_SALLES = 10   # estimation non mesuree : fenetre « dernieres salles »
SEANCES_LENTILLE_A_ZERO = 5   # estimation non mesuree : seances sans trouvaille retenue


def _voix_de(run):
    v = run.get("voix")
    if not isinstance(v, list):
        return []
    vus, out = set(), []
    for x in v:
        if isinstance(x, dict) and isinstance(x.get("nom"), str) and x["nom"] not in vus:
            vus.add(x["nom"])       # une seance par (run, nom) : un doublon ne compte pas deux fois
            out.append(x)
    return out


# Miroir de MODES_SALLE du canon log_run.py (verrouille par tests/test_optimiseur_lot2.py : un 4e mode ajoute
# d'un seul cote fait echouer le test au lieu d'etre compte « classique » en silence).
MODES_SALLE = ("classique", "neuronale", "neuronale-augmentee")


def _par_mode(salles):
    """Ventilation par `mode_salle` (absent ou invalide = classique), ratios inchanges.
    Une valeur non textuelle (liste, dict) ecrite a la main dans le journal ne plante pas."""
    out = {m: {"salles": 0, "desaccords": 0, "tour2_joues": 0} for m in MODES_SALLE}
    for r in salles:
        v = r.get("mode_salle")
        m = v if isinstance(v, str) and v in out else "classique"
        out[m]["salles"] += 1
        if isinstance(r.get("tour2"), bool):
            out[m]["desaccords"] += 1
            out[m]["tour2_joues"] += 1 if r["tour2"] else 0
    return out


def indicateurs_salles(runs, n_dernieres=DERNIERES_SALLES, seances_min=SEANCES_LENTILLE_A_ZERO):
    """Mesure des salles : tour 2 joue, trouvailles retenues par lentille.

    `tour2` est requis pour toute salle (log_run) : true = tour 2 joue, false = desaccord
    sans tour 2, "na" = pas de desaccord (ignore ici : seuls les booleens comptent). `voix[].trouvailles_retenues` donne le rendement."""
    salles = [r for r in runs if r.get("topologie") == "salle"]
    avec_t2 = [r for r in salles if isinstance(r.get("tour2"), bool)]
    jouees = sum(1 for r in avec_t2 if r["tour2"])
    avec_voix = [r for r in salles if _voix_de(r)]
    fenetre = avec_voix[-n_dernieres:] if n_dernieres > 0 else []

    def somme(rs):
        out, vus = {}, {}
        for r in rs:
            for x in _voix_de(r):
                n = _nombre(x.get("trouvailles_retenues")) or 0
                out[x["nom"]] = out.get(x["nom"], 0) + n
                vus[x["nom"]] = vus.get(x["nom"], 0) + 1
        return out, vus

    retenues_fenetre, _ = somme(fenetre)
    retenues_tot, seances = somme(avec_voix)
    return {"salles": len(salles), "desaccords": len(avec_t2), "tour2_joues": jouees,
            "part_tour2": (jouees / len(avec_t2)) if avec_t2 else None,
            "fenetre": len(fenetre), "retenues_par_lentille": retenues_fenetre,
            "lentilles_a_zero": sorted(n for n, k in seances.items()
                                       if k >= seances_min and retenues_tot[n] == 0),
            "seances_min": seances_min, "par_mode": _par_mode(salles)}


def rapport_salles(ind):
    p = ind["part_tour2"]
    lg = ["NB : `tour2` vaut true/false pour une salle a desaccord au tour 1, \"na\" sans "
          "desaccord ; les runs "
          "sans true/false (\"na\", ou salle anterieure sans le champ) ne sont pas comptes.",
          f"salles : {ind['salles']} run(s) topologie=salle ; desaccords au tour 1 "
          f"renseignes : {ind['desaccords']} ; tour 2 joue : {ind['tour2_joues']}/"
          f"{ind['desaccords']}" + (f" ({p:.0%})" if p is not None else " (non mesurable)"),
          f"trouvailles retenues par lentille sur les {ind['fenetre']} dernieres salles "
          "a voix journalisees :"]
    for n, k in sorted(ind["retenues_par_lentille"].items()):
        lg.append(f"  {n}: {k}")
    if not ind["retenues_par_lentille"]:
        lg.append("  (aucune voix journalisee)")
    for m, v in ind.get("par_mode", {}).items():
        lg.append(f"mode {m} : {v['salles']} salle(s) ; tour 2 joue {v['tour2_joues']}/{v['desaccords']}")
    lg.append(f"lentilles a 0 retenue sur >= {ind['seances_min']} seances : "
              + (", ".join(ind["lentilles_a_zero"]) or "aucune"))
    return "\n".join(lg)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--salles":
        print(rapport_salles(indicateurs_salles(charger_runs())))
        return 0
    if not argv or argv[0] not in ("--rapport", "--json"):
        print(__doc__)
        return 0 if not argv else 2
    runs, illisibles = charger()
    rap = calculer(runs, charger_seuils(), illisibles)
    if argv[0] == "--json":
        print(json.dumps(rap, ensure_ascii=False, indent=1))
    else:
        print(rapport_texte(rap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
