"""Preuve P1 : un test de régression DOIT échouer sur le code d'avant.

P1 et P2 (CLAUDE.md) exigent qu'un correctif arrive avec un test qui échoue sur
le code précédent. Cette exigence se vérifiait à la main : on remet le code
d'avant, on joue le test, on regarde, on restaure. Trois choses ont mal tourné
dans cette procédure artisanale, toutes mesurées les 2026-09-10 et 2026-09-11 :

1. **Des faux négatifs d'échappement.** Deux mutations passées en argument de
   `python -c` à travers un shell ont vu leurs retours à la ligne mangés : le
   marqueur n'était pas trouvé, la mutation n'était donc PAS appliquée, et le
   test « passait sur le code d'avant ». Conclusion tirée : « le test ne prouve
   rien ». Conclusion vraie : « je n'ai rien testé ». Les deux se ressemblent
   au point de se confondre.
2. **Un test qui passait pour une MAUVAISE raison.** Un test de la garde
   Périmètre ne mettait aucun fichier surveillé dans le commit : le hook sortait
   avant d'évaluer quoi que ce soit, donc le test passait — avec ET sans le
   correctif. Seule la mutation l'a révélé.
3. **Un fichier laissé muté.** Une preuve interrompue a déjà laissé un dépôt sur
   le code d'avant pendant des heures, suite et revue jouées dessus.

Principe directeur : **ne jamais conclure dans le doute.** Un outil de preuve
qui se trompe est pire que pas d'outil, parce qu'on lui fait confiance. Toute
situation ambiguë rend un code d'ERREUR D'OUTILLAGE, distinct du verdict.

## Codes de sortie

Ce tableau est le contrat ; `tests/test_preuve_p1.py` l'épingle.

===  =========================================================================
  0  PREUVE TENUE — vert sur le code actuel, rouge sur le code d'avant.
  1  PREUVE ÉCHOUÉE — le test passe AUSSI sur le code d'avant : il ne prouve rien.
  2  Erreur d'USAGE — arguments, fichier cible, fichier de marqueurs, cible hors
     du dépôt, mode suppression non confirmé.
  3  Marqueur NON EXPLOITABLE : absent, présent plusieurs fois, ou non aligné sur
     des lignes entières (il pourrait tomber dans un commentaire ou au milieu
     d'une ligne). Une mutation non appliquée ou appliquée ailleurs donne un faux
     négatif — c'est le défaut n°1. `--fragment` assume le non-aligné.
  4  Le test est DÉJÀ ROUGE avant mutation : la preuve serait vide.
  5  La mutation n'a RIEN changé au fichier.
  6  RESTAURATION ÉCHOUÉE — le fichier reste muté. Critique, même si la preuve
     tenait : le dépôt est sur le code d'avant. C'est le défaut n°3.
  7  Erreur d'OUTILLAGE pytest — collecte impossible, aucun test collecté, arrêt
     interne. NE PROUVE RIEN : un code non nul n'est pas forcément un test rouge.
  8  Le fichier cible a CHANGÉ pendant la preuve (édition concurrente).
  9  Une preuve PRÉCÉDENTE a été INTERROMPUE et son fichier est resté muté.
     Refus d'en lancer une autre : la sauvegarde est nommée, `--restaurer` la
     remet. Couvre le `taskkill` ou le crash, que le `finally` ne voit pas.
===  =========================================================================

## Usage

    py scripts/preuve_p1.py <fichier> <test> --marqueur-fichier <chemin>

Le fichier de marqueurs porte le code d'AVANT, une ligne `---PREUVE-P1---`, puis
le code ACTUEL. Il est passé par FICHIER et non en argument : c'est l'échappement
en ligne de commande qui a fabriqué le défaut n°1. `--avant` / `--apres`
existent pour les marqueurs d'une seule ligne, sans retour à la ligne possible.

`<test>` est passé tel quel à pytest (`chemin`, ou `chemin::test`) ; `--k` ajoute
un motif. Quand un sélecteur est donné, l'outil EXIGE que le test rouge soit bien
celui-là — sinon un test voisin qui échoue ferait conclure à tort.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SEPARATEUR = "---PREUVE-P1---"

TENUE, ECHOUEE, USAGE, MARQUEUR, DEJA_ROUGE = 0, 1, 2, 3, 4
SANS_EFFET, RESTAURATION, OUTILLAGE, CONCURRENCE, INTERROMPUE = 5, 6, 7, 8, 9

# Trace d'une preuve EN COURS. Le `finally` couvre les sorties normales et les
# exceptions ; il ne couvre pas un `taskkill`, un plantage de l'interpreteur ni
# une coupure de courant. Cette trace, elle, survit : la preuve suivante la voit
# et refuse de partir sur un depot reste sur le code d'avant (defaut n3, vu une
# fois pour de vrai sur ce depot). Chemin FIXE par FICHIER CIBLE, pour etre
# retrouvable sans rien savoir du run qui l'a laissee : la preuve suivante sur
# le meme fichier la trouve avec le seul argument qu'elle a deja (le fichier).
# Un chemin unique pour toute la machine (2026-09-28) faisait qu'une preuve en
# cours sur X refusait, code 9, une preuve sur Y lancee a cote — deux preuves
# sur deux fichiers n'ont rien a se dire. La trace d'avant ce changement,
# SENTINELLE_HERITEE, reste honoree : une preuve interrompue par l'ancien outil
# ne doit pas disparaitre avec lui.
SENTINELLE_HERITEE = Path(tempfile.gettempdir()) / "preuve_p1_interrompue.json"


def _sentinelle(cible: Path) -> Path:
    cle = hashlib.sha1(str(cible.resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"preuve_p1_interrompue_{cle}.json"

# pytest : 0 = tout passe, 1 = des tests ont échoué. Tout le reste est un
# problème d'OUTILLAGE (2 interrompu, 3 erreur interne, 4 usage, 5 aucun test
# collecté) et ne dit rien du comportement teste.
PYTEST_VERDICTS = (0, 1)


def _empreinte(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def _jouer(test: str, motif: str | None, basetemp: Path, timeout: int):
    """(verdict, sortie) où verdict vaut True (vert), False (rouge) ou None
    (outillage : on ne peut pas conclure)."""
    args = [sys.executable, "-m", "pytest", test, "-q", "-rf",
            "--basetemp", str(basetemp)]
    if motif:
        args += ["-k", motif]
    # Pas de bytecode écrit par le fils : la mutation garde souvent la même
    # TAILLE (`x * 2` -> `x + 2`) et un .pyc est jugé valide sur (mtime à la
    # seconde, taille) — sous Linux le run vert dure 0,03 s, la mutation tombe
    # dans la même seconde et le second run rejoue l'ancien code : « le test
    # PASSE aussi sur le code d'avant », à tort (CI du 2026-09-30, 2 tests).
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        r = subprocess.run(args, cwd=str(RACINE), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env=env)
    except subprocess.TimeoutExpired as exc:
        return None, f"pytest n'a pas rendu en {timeout} s : {exc}"
    sortie = (r.stdout or "") + (r.stderr or "")
    if r.returncode not in PYTEST_VERDICTS:
        return None, f"pytest a rendu le code {r.returncode} (outillage)\n{sortie}"
    return r.returncode == 0, sortie


TETE, QUEUE = 800, 2500


def _extrait(sortie: str) -> str:
    """Tête ET queue de la sortie capturée, jointes par un marqueur d'omission.

    La seule queue (`[-1500:]`) coupait le début : une erreur de collecte ou la
    tête d'une trace disparaissait. Une sortie courte est rendue entière."""
    if len(sortie) <= TETE + QUEUE:
        return sortie
    omis = len(sortie) - TETE - QUEUE
    return f"{sortie[:TETE]}\n[… {omis} caractères omis …]\n{sortie[-QUEUE:]}"


def _echecs(sortie: str) -> list[str]:
    """Les lignes `FAILED …` du resume court (`-rf`)."""
    return [ligne.strip() for ligne in sortie.splitlines()
            if ligne.strip().startswith("FAILED ")]


def _lire_marqueurs(args):
    """(avant, apres) ou lève SystemExit(USAGE)."""
    if args.marqueur_fichier:
        chemin = Path(args.marqueur_fichier)
        if not chemin.is_file():
            _sortir(f"fichier de marqueurs introuvable : {chemin}")
        brut = chemin.read_text(encoding="utf-8")
        morceaux = brut.split("\n" + SEPARATEUR + "\n")
        if len(morceaux) != 2:
            _sortir(
                f"le fichier de marqueurs doit contenir EXACTEMENT deux blocs "
                f"separes par une ligne '{SEPARATEUR}' : le code d'avant, puis "
                f"le code actuel. Trouve {len(morceaux)} bloc(s)."
            )
        return morceaux[0].rstrip("\n"), morceaux[1].rstrip("\n")
    if not args.avant or not args.apres:
        _sortir("il faut --marqueur-fichier, ou le couple --avant/--apres.")
    for nom, val in (("--avant", args.avant), ("--apres", args.apres)):
        if "\n" in val:
            _sortir(f"{nom} contient un retour a la ligne : passer par "
                    "--marqueur-fichier, le shell mange les retours a la ligne "
                    "et c'est le defaut n1 que cet outil ferme.")
    return args.avant, args.apres


def _sortir(message: str):
    print("ERREUR D'USAGE : " + message)
    raise SystemExit(USAGE)


def _ligne_du_marqueur(texte: str, marqueur: str) -> int:
    return texte[:texte.index(marqueur)].count("\n") + 1


def _aligne_sur_des_lignes(texte: str, marqueur: str) -> bool:
    """Le marqueur occupe-t-il des lignes ENTIÈRES ?

    Un marqueur unique peut tomber au milieu d'une ligne, ou dans un commentaire
    ou une docstring qui cite le code : la mutation s'applique alors ailleurs que
    voulu, et la preuve ne mesure rien (revue du 2026-09-11, R3-11).

    `\\r` compte comme une fin de ligne : le fichier est lu en OCTETS, sans
    traduction, donc sur un arbre CRLF — ce dépôt l'est entièrement,
    `core.autocrlf=true` — le caractère qui suit un marqueur aligné est `\\r` et
    non `\\n`. Sans ce cas, tout marqueur était déclaré non aligné.
    """
    debut = texte.index(marqueur)
    fin = debut + len(marqueur)
    return ((debut == 0 or texte[debut - 1] in "\n\r")
            and (fin == len(texte) or texte[fin] in "\n\r"))


def _fin_de_ligne(texte: str) -> str:
    """La fin de ligne dominante du fichier : `\\r\\n` ou `\\n`."""
    return "\r\n" if "\r\n" in texte else "\n"


def _aux_fins_de_ligne(bloc: str, fin: str) -> str:
    """Le bloc de marqueur, réécrit aux fins de ligne du FICHIER CIBLE.

    Sans cette conversion, un marqueur MULTI-LIGNES écrit en LF ne correspondait
    jamais à un fichier CRLF : l'outil rendait « apparait 0 fois », c'est-à-dire
    exactement le faux négatif qu'il existe pour empêcher (trouvé en dogfoodant
    l'outil sur ce dépôt, dont l'arbre de travail est entièrement en CRLF). Mes
    premières preuves n'avaient tenu que parce que leurs marqueurs faisaient une
    seule ligne.
    """
    return bloc.replace("\r\n", "\n").replace("\n", fin)


def _traiter_la_sentinelle(cible_demandee: Path, restaurer: bool) -> int | None:
    """None si la voie est libre, sinon le code de sortie à rendre.

    Deux traces peuvent parler : celle du fichier demande, et la trace heritee
    (chemin unique d'avant le 2026-09-28). La premiere qui existe decide."""
    for sentinelle in (_sentinelle(cible_demandee), SENTINELLE_HERITEE):
        verdict = _traiter_une_sentinelle(sentinelle, restaurer)
        if verdict is not None:
            return verdict
    return None


def _traiter_une_sentinelle(sentinelle: Path, restaurer: bool) -> int | None:
    if not sentinelle.is_file():
        return None
    try:
        trace = json.loads(sentinelle.read_text(encoding="utf-8"))
        cible = Path(trace["cible"])
        sauvegarde = Path(trace["sauvegarde"])
        attendue = trace["empreinte_depart"]
    except Exception as exc:
        print(f"sentinelle illisible ({exc}) : {sentinelle}\n"
              "  la supprimer a la main apres avoir verifie l'arbre.")
        return INTERROMPUE
    if cible.is_file() and _empreinte(cible) == attendue:
        # La restauration avait eu lieu ; seule la trace est restee.
        sentinelle.unlink(missing_ok=True)
        print("sentinelle perimee retiree : le fichier etait deja restaure.")
        return None
    if restaurer:
        if not sauvegarde.is_file():
            print(f"RESTAURATION IMPOSSIBLE : sauvegarde introuvable "
                  f"({sauvegarde}). Recuperer {cible} depuis git.")
            return INTERROMPUE
        shutil.copy(sauvegarde, cible)
        if _empreinte(cible) != attendue:
            print(f"RESTAURATION INCOMPLETE : {cible} ne correspond pas a son "
                  f"etat de depart. Sauvegarde : {sauvegarde}")
            return INTERROMPUE
        sentinelle.unlink(missing_ok=True)
        print(f"{cible} restaure depuis {sauvegarde} (octet pour octet).")
        return TENUE
    print(f"PREUVE PRECEDENTE INTERROMPUE : {cible} est probablement reste MUTE.\n"
          f"  sauvegarde  : {sauvegarde}\n"
          f"  sentinelle  : {sentinelle}\n"
          "Ce depot est peut-etre sur le code d'avant : une suite ou une revue "
          "jouee maintenant mesurerait la mauvaise version.\n"
          "  py scripts/preuve_p1.py --restaurer <fichier> <test> --avant x --apres y\n"
          "…ou restaurer a la main, puis relancer.")
    return INTERROMPUE


def main() -> int:
    # Windows : la console par défaut est cp1252. Cet outil REIMPRIME la sortie
    # pytest capturee, et cette capture est decodee en `errors="replace"` : un
    # octet indecodable y devient U+FFFD, qu'un stdout cp1252 ne sait pas
    # encoder. Le print levait alors UnicodeEncodeError, l'outil sortait en 1 —
    # or 1 est le code de PREUVE ECHOUEE, la conclusion INVERSE du refus. Cinq
    # sites d'impression portent de la sortie capturee, chemin de succes inclus :
    # la reconfiguration se fait donc ICI, une fois, avant tout print et avant
    # `parse_args`. Meme idiome que compter_triage.py, log_usage.py,
    # write_diagnostic.py, git_agents_inventory.py et log_run.py ; cet outil en
    # etait le seul ecart. `errors="replace"` : la sortie d'un test n'est pas le
    # verdict, mieux vaut un losange qu'un refus de conclure.
    # (L'octet indecodable vient d'un message Windows francise — « Accès refusé »
    # dans un PytestCacheWarning. Sur une machine en locale anglaise le defaut
    # est present quand meme, simplement muet.)
    for _flux in (sys.stdout, sys.stderr):
        if hasattr(_flux, "reconfigure"):
            _flux.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(
        description="Prouve qu'un test de regression echoue sur le code d'avant.")
    p.add_argument("fichier", help="fichier a muter")
    p.add_argument("test", help="cible pytest : fichier, ou fichier::test")
    p.add_argument("--k", dest="motif", help="motif -k passe a pytest")
    p.add_argument("--avant", help="code D'AVANT, une seule ligne")
    p.add_argument("--apres", help="code ACTUEL, une seule ligne")
    p.add_argument("--marqueur-fichier",
                   help=f"fichier : code d'avant, ligne '{SEPARATEUR}', code actuel")
    p.add_argument("--timeout", type=int, default=900,
                   help="secondes accordees a chaque run pytest (defaut 900). "
                        "L'appelant doit en accorder davantage : le tuer pendant "
                        "la fenetre mutee laisse le fichier muté.")
    p.add_argument("--hors-depot", action="store_true",
                   help="autorise une cible hors du depot (jamais en routine)")
    p.add_argument("--fragment", action="store_true",
                   help="assume un marqueur non aligne sur des lignes entieres")
    p.add_argument("--restaurer", action="store_true",
                   help="remet le fichier d'une preuve INTERROMPUE, puis sort")
    args = p.parse_args()

    demande = Path(args.fichier)
    cible = (demande if demande.is_absolute() else (RACINE / demande)).resolve()

    # AVANT tout : une preuve precedente a-t-elle laisse CE fichier mute ?
    # Partir sans regarder ferait mesurer la mauvaise version du code.
    refus = _traiter_la_sentinelle(cible, args.restaurer)
    if refus is not None:
        return refus
    if args.restaurer:
        print("rien a restaurer : aucune preuve interrompue.")
        return TENUE
    sentinelle = _sentinelle(cible)

    if not cible.is_file():
        print(f"ERREUR D'USAGE : fichier introuvable : {cible}")
        return USAGE
    if not args.hors_depot and not cible.is_relative_to(RACINE):
        print(f"ERREUR D'USAGE : {cible} est hors du depot. Une faute de frappe "
              "muterait un fichier quelconque de la machine. --hors-depot pour "
              "l'assumer.")
        return USAGE

    avant, apres = _lire_marqueurs(args)
    if not avant.strip():
        print("ERREUR D'USAGE : le bloc AVANT est vide — la mutation supprimerait "
              "le marqueur, et le rouge viendrait d'une erreur de syntaxe, pas du "
              "comportement. Donner le vrai code d'avant.")
        return USAGE

    # Lecture/écriture en OCTETS : `write_text` traduit les fins de ligne selon
    # la plateforme, donc réécrirait tout un fichier LF en CRLF sur Windows. Le
    # rouge viendrait alors des fins de ligne et non de la mutation.
    octets = cible.read_bytes()
    try:
        texte = octets.decode("utf-8")
    except UnicodeDecodeError as exc:
        print(f"ERREUR D'USAGE : {cible} n'est pas de l'UTF-8 ({exc}).")
        return USAGE

    # Les marqueurs sont réécrits aux fins de ligne du fichier cible : l'auteur
    # les tape en LF, l'arbre de travail est en CRLF, et la comparaison est faite
    # sur des octets non traduits.
    fin = _fin_de_ligne(texte)
    avant, apres = _aux_fins_de_ligne(avant, fin), _aux_fins_de_ligne(apres, fin)

    occurrences = texte.count(apres)
    if occurrences != 1:
        print(f"MARQUEUR NON UNIQUE : le code actuel apparait {occurrences} fois "
              f"dans {args.fichier} (il en faut exactement 1).\nUne mutation non "
              "appliquee, ou appliquee ailleurs, produit un faux negatif "
              "indistinguable d'un test qui ne prouve rien. C'est le defaut n1, "
              "vu deux fois le 2026-09-11.")
        return MARQUEUR
    ligne = _ligne_du_marqueur(texte, apres)
    if not _aligne_sur_des_lignes(texte, apres) and not args.fragment:
        print(f"MARQUEUR NON ALIGNE : il commence ou finit au milieu d'une ligne "
              f"(ligne {ligne} de {args.fichier}).\nUn marqueur unique peut tomber "
              "dans un commentaire, une docstring ou une ligne plus longue : la "
              "mutation s'appliquerait ailleurs que voulu, et la preuve ne "
              "mesurerait rien.\nDonner les lignes ENTIERES, ou --fragment pour "
              "l'assumer.")
        return MARQUEUR
    print(f"marqueur unique, ligne {ligne} de {args.fichier}")
    for i, contenu in enumerate(apres.split("\n")[:4]):
        print(f"    - {ligne + i}: {contenu[:100]}")

    empreinte_depart = hashlib.sha256(octets).hexdigest()
    abri = Path(tempfile.mkdtemp(prefix="preuve_p1_"))
    sauvegarde = abri / cible.name
    shutil.copy(cible, sauvegarde)
    basetemp = Path(tempfile.mkdtemp(prefix="preuve_p1_pytest_"))
    mutation_posee = False
    restauree = True  # vrai tant qu'aucune mutation n'a ete posee

    def _corps() -> int:
        """Le deroule de la preuve. Extrait pour que la restauration
        soit decidee APRES lui, sans `return` dans un `finally` : Python
        3.14 l'avertit, et ca masque le verdict de facon illisible.

        `nonlocal` est indispensable : sans lui, `mutation_posee = True` creerait
        une variable LOCALE a cette fonction, et la restauration ne se
        declencherait jamais -- le fichier resterait mute, c'est-a-dire le
        defaut n3 reintroduit par le refactor qui devait l'eviter."""
        nonlocal mutation_posee
        print("1/2 — le test doit PASSER sur le code actuel…")
        verdict, sortie = _jouer(args.test, args.motif, basetemp / "vert", args.timeout)
        if verdict is None:
            print("OUTILLAGE : impossible de jouer le test. Ce n'est PAS un "
                  "verdict.\n" + _extrait(sortie))
            return OUTILLAGE
        if not verdict:
            print("DEJA ROUGE : le test echoue avant toute mutation. Un test "
                  "rouge au depart ne peut rien prouver sur le code d'avant.\n"
                  + _extrait(sortie))
            return DEJA_ROUGE
        print("    vert.")

        if _empreinte(cible) != empreinte_depart:
            print("CONCURRENCE : le fichier a change pendant le run vert. "
                  "Restaurer maintenant ecraserait cette modification.")
            return CONCURRENCE

        # La sentinelle est posee AVANT la mutation : entre les deux, un crash
        # laisserait le fichier intact, ce qui est le bon sens de l'erreur.
        sentinelle.write_text(json.dumps({
            "cible": str(cible), "sauvegarde": str(sauvegarde),
            "empreinte_depart": empreinte_depart,
        }), encoding="utf-8")
        cible.write_bytes(texte.replace(apres, avant).encode("utf-8"))
        mutation_posee = True
        if _empreinte(cible) == empreinte_depart:
            print("SANS EFFET : le fichier est inchange apres mutation.")
            return SANS_EFFET

        print("2/2 — mutation posee ; le test doit ECHOUER…")
        verdict, sortie = _jouer(args.test, args.motif, basetemp / "rouge", args.timeout)
        if verdict is None:
            print("OUTILLAGE : la mutation empeche de jouer le test (collecte, "
                  "import, syntaxe). Un code non nul n'est PAS un test rouge : "
                  "cette preuve ne conclut rien. Choisir une mutation qui laisse "
                  "le fichier executable.\n" + _extrait(sortie))
            return OUTILLAGE
        if verdict:
            print("PREUVE ECHOUEE : le test PASSE aussi sur le code d'avant.\n"
                  "Il ne prouve pas le correctif. Deux causes vues sur ce depot :\n"
                  "  - il n'observe pas ce qu'il croit (assertions satisfaites par "
                  "autre chose) ;\n"
                  "  - il passe pour une mauvaise raison (le chemin teste n'est "
                  "jamais atteint).")
            return ECHOUEE

        echecs = _echecs(sortie)
        selecteur = args.motif or (args.test.split("::")[-1]
                                   if "::" in args.test else None)
        if selecteur and not any(selecteur in e for e in echecs):
            print(f"PREUVE NON CONCLUANTE : des tests echouent, mais aucun ne "
                  f"correspond a '{selecteur}'.\nUn test VOISIN qui tombe ne "
                  "prouve rien du correctif.\n  " + "\n  ".join(echecs[:5]))
            return ECHOUEE
        print("    rouge, et c'est bien le test vise :")
        for e in echecs[:5]:
            print("      " + e[:160])
        print("\nPREUVE P1 TENUE : vert sur le code actuel, rouge sur le code "
              "d'avant.")
        return TENUE

    verdict = OUTILLAGE  # si `_corps` leve, on ne conclut rien
    try:
        verdict = _corps()
    except Exception:
        # Le commentaire ci-dessus decrivait une garde ABSENTE : sans `except`,
        # l'exception remontait et l'interpreteur sortait en 1, c'est-a-dire
        # PREUVE ECHOUEE — un plantage de l'outil signait la conclusion inverse
        # du refus. `Exception` et non `BaseException` : `_sortir` leve
        # SystemExit(USAGE) et un KeyboardInterrupt doit remonter (le cas tue est
        # couvert par la SENTINELLE, pas par ce bloc). La trace est IMPRIMEE :
        # un OUTILLAGE muet ne se diagnostique pas.
        verdict = OUTILLAGE
        print("OUTILLAGE : l'outil a leve une exception inattendue — aucun "
              "verdict n'est rendu.")
        traceback.print_exc(file=sys.stdout)
    finally:
        if mutation_posee:
            try:
                shutil.copy(sauvegarde, cible)
                restauree = _empreinte(cible) == empreinte_depart
            except OSError as exc:
                restauree = False
                print(f"  la copie de restauration a leve : {exc}")
            if restauree:
                # La sentinelle ne part qu'une fois la restauration VERIFIEE :
                # la retirer plus tot effacerait la seule trace utile.
                sentinelle.unlink(missing_ok=True)
                print("restauration : OK (octet pour octet)")
            else:
                print(f"RESTAURATION ECHOUEE : {cible} reste MUTE.\n"
                      f"  sauvegarde : {sauvegarde}\n"
                      "  Le depot est sur le code d'avant : restaurer a la main "
                      "AVANT toute suite de tests ou revue.")
        shutil.rmtree(basetemp, ignore_errors=True)
        if restauree:
            shutil.rmtree(abri, ignore_errors=True)

    # La restauration PRIME sur le verdict : un code 0 rendu sur un depot reste
    # sur le code d'avant serait le defaut n3 muni d'un tampon de conformite.
    return verdict if restauree else RESTAURATION


if __name__ == "__main__":
    raise SystemExit(main())
