"""Jalons visibles et garde de budget par etape (E0-E4) : 0 token, le script tient le chronometre.

Etat : `<dir>/jalons.json` (par etape : debut, budget_min, condition d'arret, jalons[]) et
`<dir>/JALON.txt` (UNE ligne, le dernier jalon : ce que l'utilisateur lit sans ouvrir de log).

  jalons.py debut  --dir D --etape E1 --budget-min 15 [--stop "condition d'arret"]
  jalons.py jalon  --dir D --etape E1 --texte "6 slides rendues, 0 defaut geometrie"
  jalons.py garde  --dir D --etape E1 [--stop-apres N]

`garde` : exit 0 = CONTINUER ; exit 1 = STOP (duree ecoulee > budget_min, ou >= N jalons si
--stop-apres) ; exit 2 = usage / etape jamais demarree / etat illisible. Un STOP se rapporte a
l'utilisateur avec l'etat reel, il ne se contourne pas en relancant `debut` (refuse : l'etape est
deja demarree). `--maintenant ISO8601` injecte l'horloge (tests).
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone

LIGNE_MAX = 160


def _etat_chemin(dossier):
    return os.path.join(dossier, "jalons.json")


def _lire(dossier):
    try:
        with open(_etat_chemin(dossier), encoding="utf-8") as fh:
            etat = json.load(fh)
    except FileNotFoundError:
        return {}
    if not isinstance(etat, dict):
        raise ValueError("jalons.json: objet attendu")
    for nom, e in etat.items():
        ok = (isinstance(e, dict) and isinstance(e.get("jalons"), list) and isinstance(e.get("debut"), str)
              and isinstance(e.get("budget_min"), (int, float)) and not isinstance(e.get("budget_min"), bool)
              and math.isfinite(e["budget_min"]))
        if not ok:
            raise ValueError(f"jalons.json: etape {nom!r} malformee (debut, budget_min, jalons attendus)")
        datetime.fromisoformat(e["debut"])
    return etat


def _ecrire(dossier, etat):
    os.makedirs(dossier, exist_ok=True)
    tmp = _etat_chemin(dossier) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(etat, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, _etat_chemin(dossier))


def _maintenant(a):
    if a:
        d = datetime.fromisoformat(a)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def debut(dossier, etape, budget_min, stop="", maintenant=None):
    if not math.isfinite(budget_min) or budget_min <= 0:
        raise ValueError("budget_min doit etre > 0")
    etat = _lire(dossier)
    if etape in etat:
        raise ValueError(f"etape {etape} deja demarree a {etat[etape]['debut']} (STOP/relance : rapporter, ne pas redemarrer)")
    etat[etape] = {"debut": (maintenant or _maintenant(None)).isoformat(), "budget_min": budget_min,
                   "stop": stop, "jalons": []}
    _ecrire(dossier, etat)


def jalon(dossier, etape, texte, maintenant=None):
    texte = " ".join(texte.split())
    if not texte:
        raise ValueError("texte de jalon vide")
    if len(texte) > LIGNE_MAX:
        raise ValueError(f"jalon de {len(texte)} caracteres > {LIGNE_MAX} : une ligne, pas un rapport")
    etat = _lire(dossier)
    if etape not in etat:
        raise ValueError(f"etape {etape} non demarree (jalons.py debut)")
    t = (maintenant or _maintenant(None)).isoformat()
    etat[etape]["jalons"].append({"t": t, "texte": texte})
    _ecrire(dossier, etat)
    with open(os.path.join(dossier, "JALON.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"{etape} | {t[11:16]} | {texte}\n")


def garde(dossier, etape, stop_apres=None, maintenant=None):
    """(continuer: bool, message) ; ValueError si l'etape n'a pas demarre."""
    etat = _lire(dossier)
    if etape not in etat:
        raise ValueError(f"etape {etape} non demarree (jalons.py debut)")
    e = etat[etape]
    ecoule = ((maintenant or _maintenant(None)) - datetime.fromisoformat(e["debut"])).total_seconds() / 60
    n = len(e["jalons"])
    if ecoule > e["budget_min"]:
        return False, f"STOP budget {etape} depasse : {ecoule:.1f} min > {e['budget_min']} min ({n} jalon(s)). Condition d'arret : {e['stop'] or 'non precisee'}"
    if stop_apres is not None and n >= stop_apres:
        return False, f"STOP {etape} : {n} jalon(s) >= {stop_apres} ({ecoule:.1f}/{e['budget_min']} min)"
    return True, f"CONTINUER {etape} : {ecoule:.1f}/{e['budget_min']} min, {n} jalon(s)"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("commande", choices=("debut", "jalon", "garde"))
    ap.add_argument("--dir", required=True, dest="dossier")
    ap.add_argument("--etape", required=True)
    ap.add_argument("--budget-min", type=float)
    ap.add_argument("--stop", default="")
    ap.add_argument("--texte")
    ap.add_argument("--stop-apres", type=int)
    ap.add_argument("--maintenant")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        now = _maintenant(a.maintenant) if a.maintenant else None
        if a.commande == "debut":
            if a.budget_min is None:
                raise ValueError("--budget-min requis")
            debut(a.dossier, a.etape, a.budget_min, a.stop, now)
            print(f"jalons : {a.etape} demarree, budget {a.budget_min:g} min")
        elif a.commande == "jalon":
            if a.texte is None:
                raise ValueError("--texte requis")
            jalon(a.dossier, a.etape, a.texte, now)
            print(f"jalons : {a.etape} jalon note")
        else:
            ok, msg = garde(a.dossier, a.etape, a.stop_apres, now)
            print(msg)
            return 0 if ok else 1
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as e:
        print(f"jalons : etat ou argument invalide : {e!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
