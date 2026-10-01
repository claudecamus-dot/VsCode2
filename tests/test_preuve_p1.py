"""Tests de `scripts/preuve_p1.py` — l'outil qui vérifie qu'un test de
régression échoue bien sur le code d'avant.

Un outil de preuve non testé serait exactement le défaut qu'il combat. Ce qui
compte ici n'est pas qu'il dise « oui », c'est qu'il sache dire **non pour la
bonne raison** : marqueur absent ou ambigu, test déjà rouge, test qui passe des
deux côtés, mutation qui casse la collecte, restauration manquée. Chacun de ces
refus a été payé une fois sur ce dépôt.

Les assertions portent sur le CODE DE SORTIE (contrat documenté dans le
docstring de l'outil) **et** sur un élément de sortie propre au chemin visé —
une sortie générique laisserait le test passer pour une mauvaise raison, ce qui
serait le comble ici. La première version de ce fichier en contenait deux
(revue adversariale du 2026-09-11).
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
OUTIL = RACINE / "scripts" / "preuve_p1.py"
SEPARATEUR = "---PREUVE-P1---"

# Le tableau des codes de sortie, tel que le docstring de l'outil le documente.
TENUE, ECHOUEE, USAGE, MARQUEUR, DEJA_ROUGE = 0, 1, 2, 3, 4
SANS_EFFET, RESTAURATION, OUTILLAGE, CONCURRENCE, INTERROMPUE = 5, 6, 7, 8, 9

# Un module d'une ligne et son test : `double(3)` vaut 6 avec `x * 2`, 5 avec
# `x + 2`. C'est le couple qui sert de support a presque tous les cas.
MODULE = "def double(x):\n    return x * 2\n"
AVANT, APRES = "    return x + 2", "    return x * 2"
IMPORTE = ("import sys\n"
           "sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))\n"
           "from sujet import double\n")


def _abri_outil() -> Path:
    """Un dossier comme celui que l'outil cree pour sa sauvegarde (mkdtemp,
    prefixe `preuve_p1_`, dans le temporaire systeme) : seule une sauvegarde
    placee la est restauree."""
    return Path(tempfile.mkdtemp(prefix="preuve_p1_"))


def _bac(tmp_path: Path, corps_module: str, corps_test: str) -> tuple[Path, Path]:
    """Un module et son test, dans un dossier à ce test seul.

    Les chemins rendus sont ABSOLUS : le bac à sable vit dans le temporaire
    système, hors du dépôt, et l'outil doit accepter cette forme.

    Écrits en OCTETS, donc en LF : `write_text` traduirait les fins de ligne en
    CRLF sur Windows, et la vérification « restauré octet pour octet » ne dirait
    plus rien de l'outil — elle mesurerait la plateforme. En LF, elle prouve en
    prime que l'outil PRÉSERVE les fins de ligne d'un fichier LF sur une machine
    Windows, ce que `write_text` lui ferait perdre (revue du 2026-09-11).
    """
    dossier = tmp_path / "bac"
    dossier.mkdir(parents=True, exist_ok=True)
    module = dossier / "sujet.py"
    module.write_bytes(corps_module.encode("utf-8"))
    test = dossier / "test_sujet.py"
    test.write_bytes(corps_test.encode("utf-8"))
    return module, test


def _lancer(module: Path, test: Path, avant: str = AVANT, apres: str = APRES,
            extra: list[str] | None = None) -> subprocess.CompletedProcess:
    """L'outil, en sous-processus, avec les marqueurs passés par FICHIER.

    Jamais en ligne de commande : c'est précisément l'échappement qui a fabriqué
    deux faux négatifs le 2026-09-11, et l'outil existe pour fermer ce trou.
    """
    marqueurs = test.parent / "marqueurs.txt"
    marqueurs.write_text(avant + "\n" + SEPARATEUR + "\n" + apres, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(OUTIL), str(module), str(test),
         "--hors-depot", "--marqueur-fichier", str(marqueurs)] + (extra or []),
        cwd=str(RACINE), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800,
    )


def test_une_vraie_preuve_est_acceptee(tmp_path):
    """Le cas nominal : le test observe le comportement corrigé, donc il passe
    sur le code actuel et échoue sur le code d'avant."""
    module, test = _bac(tmp_path, MODULE,
                        IMPORTE + "def test_double():\n    assert double(3) == 6\n")
    r = _lancer(module, test)
    assert r.returncode == TENUE, r.stdout + r.stderr
    assert "PREUVE P1 TENUE" in r.stdout
    assert "marqueur unique, ligne 2" in r.stdout, (
        "l'outil doit dire OU il a mute : un marqueur unique mais situe dans un "
        "commentaire ne prouverait rien"
    )
    assert "c'est bien le test vise" in r.stdout
    # Octets, pas texte : une traduction de fins de ligne se verrait ici et
    # passerait inapercue a travers `read_text` (revue du 2026-09-11).
    assert module.read_bytes() == MODULE.encode("utf-8")


def test_un_test_qui_passe_AUSSI_sur_le_code_d_avant_est_refuse(tmp_path):
    """Le défaut central : un test qui ne distingue pas les deux versions.

    Vécu le 2026-09-11 sur la garde Périmètre — le test passait des deux côtés
    parce qu'il n'atteignait jamais le chemin qu'il croyait tester."""
    module, test = _bac(
        tmp_path, MODULE,
        # `double(2)` vaut 4 avec `x * 2` ET avec `x + 2` : le seul point ou les
        # deux versions se confondent. Un premier jet utilisait `double(0) == 0`,
        # qui les distingue (0 contre 2) -- la fixture n'etait pas aveugle, et
        # c'est l'outil qui avait raison de l'accepter.
        IMPORTE + "def test_faible():\n    assert double(2) == 4\n")
    r = _lancer(module, test)
    assert r.returncode == ECHOUEE, r.stdout + r.stderr
    assert "PREUVE ECHOUEE" in r.stdout
    assert "passe pour une mauvaise raison" in r.stdout


def test_un_marqueur_ambigu_est_REFUSE_et_non_devine(tmp_path):
    """Deux occurrences : muter « quelque part » donne un résultat qu'on ne peut
    pas interpréter. L'outil refuse plutôt que de choisir."""
    module, test = _bac(
        tmp_path,
        "def a(x):\n    return x * 2\ndef b(x):\n    return x * 2\n",
        "def test_rien():\n    assert True\n")
    r = _lancer(module, test)
    assert r.returncode == MARQUEUR, r.stdout + r.stderr
    assert "apparait 2 fois" in r.stdout


def test_un_marqueur_ABSENT_est_refuse(tmp_path):
    """Le faux négatif d'échappement, reproduit : si le marqueur n'est pas là,
    la mutation ne s'applique pas et le test « passe sur le code d'avant » sans
    que rien n'ait été testé. Les deux situations se ressemblent ; seule
    celle-ci est un bug de l'outillage, et elle doit le dire."""
    module, test = _bac(tmp_path, MODULE, "def test_rien():\n    assert True\n")
    r = _lancer(module, test, apres="    return x // 2  # absent du fichier")
    assert r.returncode == MARQUEUR, r.stdout + r.stderr
    assert "apparait 0 fois" in r.stdout


def test_un_test_deja_rouge_ne_prouve_rien(tmp_path):
    """Si le test échoue avant toute mutation, la preuve est vide : on ne sait
    pas si le correctif change quoi que ce soit."""
    module, test = _bac(tmp_path, MODULE,
                        IMPORTE + "def test_casse():\n    assert double(3) == 7\n")
    r = _lancer(module, test)
    assert r.returncode == DEJA_ROUGE, r.stdout + r.stderr
    assert "DEJA ROUGE" in r.stdout


def test_une_mutation_qui_casse_la_COLLECTE_ne_prouve_rien(tmp_path):
    """Le défaut le plus sournois, trouvé par la revue du 2026-09-11 : une
    mutation qui rend le fichier inimportable fait sortir pytest en code 2, que
    la première version comptait comme « rouge ». L'outil certifiait donc une
    preuve à partir d'une erreur de syntaxe."""
    module, test = _bac(tmp_path, MODULE,
                        IMPORTE + "def test_double():\n    assert double(3) == 6\n")
    r = _lancer(module, test, avant="    return x * 2 +  # syntaxe cassee")
    assert r.returncode == OUTILLAGE, r.stdout + r.stderr
    assert "n'est PAS un test rouge" in r.stdout


def test_une_cible_pytest_invalide_est_une_erreur_D_OUTILLAGE(tmp_path):
    """Aucun test collecté (chemin faux, `-k` qui ne matche rien) : pytest sort
    en code 5. Le compter comme rouge ferait conclure une preuve sur du vide —
    c'est le défaut n°1 sous un autre habit."""
    module, test = _bac(tmp_path, MODULE, "def test_rien():\n    assert True\n")
    r = _lancer(module, test, extra=["--k", "motif_qui_ne_matche_rien"])
    assert r.returncode == OUTILLAGE, r.stdout + r.stderr
    assert "Ce n'est PAS un verdict" in r.stdout


def test_un_test_VOISIN_qui_tombe_ne_prouve_pas_le_correctif(tmp_path):
    """La cible est un FICHIER : si un test sans rapport échoue sous la
    mutation, le rouge n'est pas celui qu'on cherchait. L'outil exige que
    l'échec corresponde au sélecteur."""
    module, test = _bac(
        tmp_path, MODULE,
        IMPORTE
        # Vise : insensible a la mutation (4 des deux cotes).
        + "def test_vise():\n    assert double(2) == 4\n"
        # Voisin : c'est LUI qui tombera.
        + "def test_voisin():\n    assert double(3) == 6\n")
    r = _lancer(module, test, extra=["--k", "test_vise"])
    # `-k test_vise` ne collecte que le test insensible : il passe des deux
    # cotes, donc PREUVE ECHOUEE -- et surtout pas TENUE sur le dos du voisin.
    assert r.returncode == ECHOUEE, r.stdout + r.stderr


def test_un_bloc_AVANT_vide_est_refuse(tmp_path):
    """Sans code d'avant, la mutation SUPPRIME le marqueur : le rouge viendrait
    d'une erreur de syntaxe et non du comportement. Foot-gun fermé."""
    module, test = _bac(tmp_path, MODULE, "def test_rien():\n    assert True\n")
    r = _lancer(module, test, avant="")
    assert r.returncode == USAGE, r.stdout + r.stderr
    assert "bloc AVANT est vide" in r.stdout


def test_une_cible_HORS_DEPOT_est_refusee_sans_le_drapeau(tmp_path):
    """Une faute de frappe ne doit pas pouvoir muter un fichier quelconque de la
    machine. Les autres tests de ce fichier passent `--hors-depot` justement
    parce que leur bac à sable est dehors."""
    module, test = _bac(tmp_path, MODULE, "def test_rien():\n    assert True\n")
    marqueurs = test.parent / "marqueurs.txt"
    marqueurs.write_text(AVANT + "\n" + SEPARATEUR + "\n" + APRES, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(OUTIL), str(module), str(test),
         "--marqueur-fichier", str(marqueurs)],
        cwd=str(RACINE), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert r.returncode == USAGE, r.stdout + r.stderr
    assert "hors du depot" in r.stdout


def test_un_fichier_cible_introuvable_est_une_erreur_d_usage(tmp_path):
    module, test = _bac(tmp_path, MODULE, "def test_rien():\n    assert True\n")
    r = _lancer(tmp_path / "bac" / "inexistant.py", test)
    assert r.returncode == USAGE, r.stdout + r.stderr
    assert "introuvable" in r.stdout


def test_un_marqueur_multiligne_en_ligne_de_commande_est_refuse(tmp_path):
    """La cause racine du défaut n°1 : un multi-lignes passé par le shell. On le
    refuse au lieu de le laisser se faire tronquer en silence."""
    module, test = _bac(tmp_path, MODULE, "def test_rien():\n    assert True\n")
    r = subprocess.run(
        [sys.executable, str(OUTIL), str(module), str(test), "--hors-depot",
         "--avant", "ligne1\nligne2", "--apres", APRES],
        cwd=str(RACINE), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert r.returncode == USAGE, r.stdout + r.stderr
    assert "retour a la ligne" in r.stdout


def test_un_marqueur_MULTILIGNE_marche_sur_un_fichier_CRLF(tmp_path):
    """Le dépôt entier est en CRLF (`core.autocrlf=true`), et l'outil lit en
    OCTETS, sans traduction. Un marqueur multi-lignes tapé en LF ne correspondait
    donc JAMAIS : « apparait 0 fois », soit exactement le faux négatif que
    l'outil existe pour empêcher. Trouvé en dogfoodant l'outil sur ce dépôt ; les
    preuves precedentes n'avaient tenu que parce que leurs marqueurs faisaient
    une seule ligne."""
    module_crlf = "def double(x):\n    y = x\n    return y * 2\n".replace("\n", "\r\n")
    module, test = _bac(tmp_path, "placeholder\n",
                        IMPORTE + "def test_double():\n    assert double(3) == 6\n")
    module.write_bytes(module_crlf.encode("utf-8"))
    # Marqueur de DEUX lignes, tape en LF comme le ferait n'importe qui.
    r = _lancer(module, test,
                avant="    y = x\n    return y + 2",
                apres="    y = x\n    return y * 2")
    assert r.returncode == TENUE, r.stdout + r.stderr
    assert module.read_bytes() == module_crlf.encode("utf-8"), (
        "les fins de ligne CRLF n'ont pas ete preservees"
    )


def test_une_mutation_SANS_EFFET_est_signalee(tmp_path):
    """Deux blocs identiques : le fichier ne change pas, donc le « rouge »
    éventuel ne viendrait pas de la mutation. Code 5 — il n'était épinglé par
    aucun test (revue du 2026-09-11, R3-13)."""
    module, test = _bac(tmp_path, MODULE,
                        IMPORTE + "def test_double():\n    assert double(3) == 6\n")
    r = _lancer(module, test, avant=APRES, apres=APRES)
    assert r.returncode == SANS_EFFET, r.stdout + r.stderr
    assert "SANS EFFET" in r.stdout


def test_un_marqueur_au_MILIEU_d_une_ligne_est_refuse(tmp_path):
    """Un marqueur unique peut tomber dans un commentaire ou au milieu d'une
    ligne : la mutation s'applique ailleurs que voulu et la preuve ne mesure
    rien (revue du 2026-09-11, R3-11)."""
    module, test = _bac(tmp_path, MODULE,
                        IMPORTE + "def test_double():\n    assert double(3) == 6\n")
    r = _lancer(module, test, avant="x + 2", apres="x * 2")
    assert r.returncode == MARQUEUR, r.stdout + r.stderr
    assert "NON ALIGNE" in r.stdout
    assert "--fragment" in r.stdout
    # …et le drapeau d'echappement fonctionne, sinon la regle serait un mur.
    r2 = _lancer(module, test, avant="x + 2", apres="x * 2", extra=["--fragment"])
    assert r2.returncode == TENUE, r2.stdout + r2.stderr


def test_une_preuve_INTERROMPUE_bloque_la_suivante_et_se_restaure(tmp_path):
    """Le `finally` couvre les sorties normales et les exceptions, pas un
    `taskkill`. Une sentinelle survit, elle : la preuve suivante refuse de
    partir sur un dépôt resté sur le code d'avant, et `--restaurer` le remet
    (revue du 2026-09-11, R3-10)."""
    import json

    module, test = _bac(tmp_path, MODULE,
                        IMPORTE + "def test_double():\n    assert double(3) == 6\n")
    # On simule l'interruption : fichier mute, sauvegarde a cote, sentinelle en
    # place — exactement l'etat que laisse un processus tue.
    sauvegarde = _abri_outil() / "sujet.py"
    sauvegarde.write_bytes(MODULE.encode("utf-8"))
    empreinte = hashlib.sha256(MODULE.encode("utf-8")).hexdigest()
    module.write_bytes(MODULE.replace(APRES, AVANT).encode("utf-8"))
    sentinelle = Path(tempfile.gettempdir()) / "preuve_p1_interrompue.json"
    sentinelle.write_text(json.dumps({
        "cible": str(module), "sauvegarde": str(sauvegarde),
        "empreinte_depart": empreinte,
    }), encoding="utf-8")
    try:
        r = _lancer(module, test)
        assert r.returncode == INTERROMPUE, r.stdout + r.stderr
        assert "PREUVE PRECEDENTE INTERROMPUE" in r.stdout
        assert str(sauvegarde) in r.stdout, "la sauvegarde doit etre NOMMEE"
        assert module.read_bytes() != MODULE.encode("utf-8"), (
            "l'outil a restaure de lui-meme : il doit d'abord prevenir"
        )

        r2 = _lancer(module, test, extra=["--restaurer"])
        assert r2.returncode == TENUE, r2.stdout + r2.stderr
        assert module.read_bytes() == MODULE.encode("utf-8")
        assert not sentinelle.exists(), "la sentinelle survit a la restauration"
    finally:
        sentinelle.unlink(missing_ok=True)


def test_une_sentinelle_PERIMEE_ne_bloque_rien(tmp_path):
    """Si la restauration avait eu lieu et que seule la trace est restée, il ne
    faut pas bloquer : le fichier est déjà bon."""
    import json

    module, test = _bac(tmp_path, MODULE,
                        IMPORTE + "def test_double():\n    assert double(3) == 6\n")
    sentinelle = Path(tempfile.gettempdir()) / "preuve_p1_interrompue.json"
    sentinelle.write_text(json.dumps({
        "cible": str(module), "sauvegarde": str(tmp_path / "absente.py"),
        "empreinte_depart": hashlib.sha256(MODULE.encode("utf-8")).hexdigest(),
    }), encoding="utf-8")
    try:
        r = _lancer(module, test)
        assert r.returncode == TENUE, r.stdout + r.stderr
        assert "sentinelle perimee retiree" in r.stdout
    finally:
        sentinelle.unlink(missing_ok=True)


def test_le_fichier_est_restaure_meme_quand_la_preuve_echoue(tmp_path):
    """La restauration est dans un `finally`, et c'est non négociable : une
    preuve interrompue a déjà laissé ce dépôt sur le code d'avant pendant des
    heures, suite et revue jouées dessus.

    On exige que la mutation ait REELLEMENT ete posee avant de conclure : sans
    cette assertion, le test passerait aussi sur une erreur d'usage survenue
    avant toute ecriture -- il passerait pour une mauvaise raison, le defaut
    meme que cet outil traque (revue du 2026-09-11)."""
    module, test = _bac(tmp_path, MODULE,
                        IMPORTE + "def test_faible():\n    assert double(2) == 4\n")
    r = _lancer(module, test)
    assert r.returncode == ECHOUEE
    assert "mutation posee" in r.stdout, "aucune mutation n'a ete posee"
    assert "restauration : OK (octet pour octet)" in r.stdout
    assert module.read_bytes() == MODULE.encode("utf-8"), (
        "l'outil a laisse le fichier mute : c'est le defaut qu'il doit empecher"
    )


# --------------------------------------------------------------------------
# Les deux refus que l'outil rendait en code 1, c'est-à-dire en PREUVE ECHOUEE
# — la conclusion INVERSE. Un plantage de l'outil ne doit jamais ressembler à
# un verdict (constat du 2026-09-27).
# --------------------------------------------------------------------------

# Une semence d'octets INDECODABLES en UTF-8, fabriquée par le test lui-même.
# Sur cette machine le vrai octet vient d'un message Windows francisé (« Accès
# refusé » dans un PytestCacheWarning) ; le fabriquer ici rend le test vrai sur
# n'importe quelle machine, locale anglaise et CI Linux comprises.
SEMENCE_BRUTE = b"SEMENCE-P1-\xe8\xe9-SEMENCE-P1\n"
SEMENCE_VUE = "SEMENCE-P1-"

# Le test du bac à sable écrit ces octets DIRECTEMENT dans le descripteur,
# capture pytest suspendue : passer par `sys.stdout` les ferait décoder (et
# remplacer) avant d'atteindre l'outil, et la semence ne traverserait rien.
#
# Dans fd 2 et non fd 1, pour une raison mesurée le 2026-09-27 : l'outil ne
# réimprime que les 1500 DERNIERS caractères de `stdout + stderr`. Un `os.write`
# est non tamponné là où l'afficheur pytest l'est, donc une semence posée sur
# fd 1 arrive en TÊTE du stdout (position 0 sur 1848) et se fait couper par la
# queue. Sur fd 2, `stderr` étant par ailleurs vide, elle est garantie dans la
# fenêtre réimprimée.
_SEMEUR = (
    "import os\n"
    f"SEMENCE = {SEMENCE_BRUTE!r}\n"
    "def _semer(request):\n"
    "    cap = request.config.pluginmanager.getplugin('capturemanager')\n"
    "    cap.suspend_global_capture(in_=False)\n"
    "    try:\n"
    "        os.write(2, SEMENCE)\n"
    "    finally:\n"
    "        cap.resume_global_capture()\n"
)


def _lancer_avec_env(module: Path, test: Path, env_sup: dict[str, str],
                     avant: str = AVANT, apres: str = APRES):
    """Comme `_lancer`, mais en imposant des variables d'environnement.

    `_lancer` n'en prend pas, et sa signature ne bouge pas : la preuve par
    OBSERVATION DIRECTE des trois tests déjà rouges n'est recevable que si ce
    fichier n'a subi AUCUNE suppression de ligne.
    """
    import os

    marqueurs = test.parent / "marqueurs_env.txt"
    marqueurs.write_text(avant + "\n" + SEPARATEUR + "\n" + apres, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(OUTIL), str(module), str(test),
         "--hors-depot", "--marqueur-fichier", str(marqueurs)],
        cwd=str(RACINE), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800,
        env={**os.environ, **env_sup},
    )


def test_une_sortie_pytest_INDECODABLE_ne_change_pas_le_verdict(tmp_path):
    """L'outil RÉIMPRIME la sortie pytest, capturée en `errors="replace"` : un
    octet indécodable y devient U+FFFD, qu'un stdout cp1252 ne sait pas encoder.
    Le print levait UnicodeEncodeError et l'outil sortait en 1 — or 1 est
    PREUVE ECHOUEE, soit « le test passe aussi sur le code d'avant », l'inverse
    du refus qu'il devait rendre. Ici le test est DÉJÀ ROUGE : le verdict dû est
    4, quoi que contienne la sortie.

    `PYTHONIOENCODING=cp1252:strict` est imposé au fils pour que le défaut se
    reproduise partout : sans lui, le test serait un no-op muet sur une machine
    déjà en UTF-8 (donc sur la CI Linux).
    """
    module, test = _bac(
        tmp_path, MODULE,
        IMPORTE + _SEMEUR
        + "def test_casse(request):\n"
          "    _semer(request)\n"
          "    assert double(3) == 7\n")
    r = _lancer_avec_env(module, test, {"PYTHONIOENCODING": "cp1252:strict"})
    sortie = r.stdout + r.stderr
    assert r.returncode == DEJA_ROUGE, sortie
    assert "DEJA ROUGE" in r.stdout, sortie
    assert "Traceback" not in sortie, (
        "l'outil a plante au lieu de rendre son refus : " + sortie
    )
    # META : la semence a-t-elle REELLEMENT traverse l'outil ? Sans cette
    # assertion, le jour ou pytest change sa capture le test passerait au vert
    # pour une mauvaise raison — sans jamais exercer le chemin indecodable.
    assert SEMENCE_VUE in r.stdout, (
        "la semence d'octets n'a pas atteint la sortie de l'outil : ce test "
        "n'exerce plus rien\n" + sortie
    )
    assert "�" in r.stdout, (
        "la semence est arrivee DECODABLE : le caractere de remplacement "
        "U+FFFD, seul responsable du plantage cp1252, n'est pas dans la "
        "sortie\n" + sortie
    )


def test_une_exception_INATTENDUE_rend_OUTILLAGE_et_jamais_un_verdict(tmp_path):
    """Le commentaire « si `_corps` leve, on ne conclut rien » décrivait une
    garde absente : il n'y avait qu'un `try/finally`, donc l'exception remontait
    et l'interpréteur sortait en 1 — PREUVE ECHOUEE, la conclusion inverse.

    L'exception est déclenchée DE L'EXTÉRIEUR, l'outil tournant en
    sous-processus : le test du bac à sable supprime son propre module après être
    passé au vert, et le contrôle de concurrence qui suit lève FileNotFoundError.
    Aucune mutation n'a encore été posée : l'arbre reste intact.
    """
    module, test = _bac(
        tmp_path, MODULE,
        IMPORTE
        + "import pathlib\n"
          "def test_double():\n"
          "    assert double(3) == 6\n"
          "    pathlib.Path(__file__).parent.joinpath('sujet.py').unlink()\n")
    r = _lancer(module, test)
    sortie = r.stdout + r.stderr
    assert r.returncode == OUTILLAGE, sortie
    assert "exception inattendue" in r.stdout, sortie
    assert "FileNotFoundError" in r.stdout, (
        "la trace doit etre IMPRIMEE : un OUTILLAGE muet ne se diagnostique "
        "pas\n" + sortie
    )


def test_une_sortie_LONGUE_garde_sa_TETE_et_sa_QUEUE(tmp_path):
    """Le refus réimprimait `sortie[-1500:]` : la seule queue. Une erreur de
    collecte ou la tête d'une trace disparaissait — une graine placée en tête
    d'une sortie de 1848 caractères a failli rendre un test muet. La tête
    (source du test, où vit `MARQUEUR-DE-TETE`) et la queue (résumé final, qui
    porte le nom du test) doivent TOUTES DEUX atteindre la sortie de l'outil,
    avec le compte explicite de ce qui est omis au milieu."""
    corps = (IMPORTE
             + "def test_marqueur_de_queue():\n"
             + "    # MARQUEUR-DE-TETE\n"
             # Le volume va dans la sortie capturée (au MILIEU), pas dans le message
             # d'assertion : sous `CI=true` pytest n'écourte plus la ligne FAILED du
             # résumé, et 6000 « y » y chassaient le nom du test hors de la queue
             # (CI du 2026-09-30 ; reproduit en local avec CI=true).
             + "    print('y' * 6000)\n"
             + "    assert double(3) == 7\n")
    module, test = _bac(tmp_path, MODULE, corps)
    r = _lancer(module, test)
    assert r.returncode == DEJA_ROUGE, r.stdout + r.stderr
    assert "MARQUEUR-DE-TETE" in r.stdout, r.stdout[:3000]
    assert "failed" in r.stdout.rsplit("MARQUEUR-DE-TETE", 1)[-1]
    fin = r.stdout.rsplit("caractères omis", 1)[-1]
    assert "test_marqueur_de_queue" in fin, r.stdout[-3000:]
    assert "caractères omis …]" in r.stdout


def _sentinelle_de(cible: Path) -> Path:
    """Le chemin de la sentinelle d'un fichier, tel que l'outil le derive : un
    nom FIXE par fichier cible (sha1 du chemin absolu resolu), pour qu'une preuve
    interrompue sur X soit retrouvee par la preuve suivante sur X — et par elle
    seule."""
    cle = hashlib.sha1(str(cible.resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"preuve_p1_interrompue_{cle}.json"


def test_deux_preuves_sur_deux_fichiers_ne_se_bloquent_pas(tmp_path):
    """Mesuré le 2026-09-28 : une sentinelle unique pour toute la machine faisait
    qu'une preuve EN COURS sur X refusait (code 9) une preuve sur Y lancée à
    côté, dans le même dépôt. La sentinelle est désormais propre au fichier
    cible : une trace laissée sur A ne concerne pas B, mais bloque toujours A."""
    import json

    corps_test = IMPORTE + "def test_double():\n    assert double(3) == 6\n"
    module_a, test_a = _bac(tmp_path / "a", MODULE, corps_test)
    module_b, test_b = _bac(tmp_path / "b", MODULE, corps_test)
    sauvegarde = _abri_outil() / "sujet.py"
    sauvegarde.write_bytes(MODULE.encode("utf-8"))
    module_a.write_bytes(MODULE.replace(APRES, AVANT).encode("utf-8"))
    sentinelle_a = _sentinelle_de(module_a)
    sentinelle_a.write_text(json.dumps({
        "cible": str(module_a), "sauvegarde": str(sauvegarde),
        "empreinte_depart": hashlib.sha256(MODULE.encode("utf-8")).hexdigest(),
    }), encoding="utf-8")
    try:
        r_b = _lancer(module_b, test_b)
        assert r_b.returncode == TENUE, (
            "une preuve interrompue sur A ne doit pas refuser B\n" + r_b.stdout + r_b.stderr)
        assert "PREUVE PRECEDENTE INTERROMPUE" not in r_b.stdout
        assert sentinelle_a.exists(), "la preuve sur B n'a pas a toucher la trace de A"

        r_a = _lancer(module_a, test_a)
        assert r_a.returncode == INTERROMPUE, (
            "la preuve suivante sur A doit toujours etre refusee\n" + r_a.stdout + r_a.stderr)
        assert "PREUVE PRECEDENTE INTERROMPUE" in r_a.stdout
        assert module_a.read_bytes() != MODULE.encode("utf-8")

        r_a2 = _lancer(module_a, test_a, extra=["--restaurer"])
        assert r_a2.returncode == TENUE, r_a2.stdout + r_a2.stderr
        assert module_a.read_bytes() == MODULE.encode("utf-8")
        assert not sentinelle_a.exists(), "la sentinelle de A survit a la restauration"
    finally:
        sentinelle_a.unlink(missing_ok=True)


def test_la_preuve_n_ecrit_aucun_bytecode_dans_le_bac(tmp_path):
    """CI du 2026-09-30 : la mutation garde la TAILLE du fichier et un .pyc est
    valide sur (mtime à la seconde, taille) ; sous Linux le run vert dure 0,03 s,
    la mutation tombe dans la même seconde et le second run rejouait l'ancien code
    (« le test PASSE aussi sur le code d'avant », à tort). Le fils n'écrit donc
    aucun .pyc : vérifiable ici sans dépendre de l'horloge."""
    module, test = _bac(tmp_path, MODULE,
                        IMPORTE + "def test_double():\n    assert double(3) == 6\n")
    r = _lancer(module, test)
    assert r.returncode == TENUE, r.stdout + r.stderr
    assert list(module.parent.rglob("*.pyc")) == []


def test_une_sentinelle_FORGEE_ne_fait_pas_copier_un_fichier_sur_un_chemin_quelconque(tmp_path):
    """Revue securite : la sentinelle vit dans le temporaire partage sous un nom
    previsible et son JSON etait cru sur parole -- `--restaurer` copiait n'importe
    quelle "sauvegarde" sur n'importe quelle "cible". Trois forgeries : une
    sauvegarde hors d'un dossier cree par l'outil, une cible hors depot sans
    --hors-depot, une cible qui n'est pas celle de la cle de la sentinelle."""
    import json

    corps_test = IMPORTE + "def test_double():\n    assert double(3) == 6\n"
    module, test = _bac(tmp_path, MODULE, corps_test)
    sentinelle = _sentinelle_de(module)
    empreinte_attendue = hashlib.sha256(b"etat de depart attendu").hexdigest()
    voulu = b"CONTENU DE L'ATTAQUANT\n"
    mute = MODULE.replace(APRES, AVANT).encode("utf-8")

    def forger(cible: Path, sauvegarde: Path, extra: list[str]):
        sauvegarde.write_bytes(voulu)
        sentinelle.write_text(json.dumps({
            "cible": str(cible), "sauvegarde": str(sauvegarde),
            "empreinte_depart": empreinte_attendue,
        }), encoding="utf-8")
        return _lancer(module, test, extra=["--restaurer"] + extra)

    abri_tool = _abri_outil()
    autre = tmp_path / "autre.txt"
    autre.write_bytes(b"intact\n")
    try:
        # 1. sauvegarde hors d'un dossier preuve_p1_* (ici : tmp_path).
        module.write_bytes(mute)
        r = forger(module, tmp_path / "piege.bin", [])
        assert r.returncode == USAGE, r.stdout + r.stderr
        assert "REFUSEE" in r.stdout
        assert module.read_bytes() == mute, "la cible ne doit pas etre ecrasee"

        # 2. cible hors depot, outil lance SANS --hors-depot.
        (abri_tool / "piege.bin").write_bytes(voulu)
        sentinelle.write_text(json.dumps({
            "cible": str(module), "sauvegarde": str(abri_tool / "piege.bin"),
            "empreinte_depart": empreinte_attendue,
        }), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(OUTIL), "--restaurer", str(module), str(test)],
            cwd=str(RACINE), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600)
        assert r.returncode == USAGE, r.stdout + r.stderr
        assert "hors du depot" in r.stdout
        assert module.read_bytes() == mute

        # 3. cible qui n'est pas celle de la cle de la sentinelle.
        r = forger(autre, abri_tool / "piege.bin", [])
        assert r.returncode == USAGE, r.stdout + r.stderr
        assert "cle" in r.stdout
        assert autre.read_bytes() == b"intact\n", "un fichier quelconque a ete ecrase"
    finally:
        sentinelle.unlink(missing_ok=True)


def test_un_verrou_pose_sur_A_refuse_une_seconde_preuve_avant_toute_mutation(tmp_path):
    """Deux preuves simultanees sur le MEME fichier ne s'excluaient pas : la
    sentinelle n'etait posee qu'apres le run vert. Un verrou exclusif est pris
    avant le premier run ; s'il existe, la preuve refuse sans rien toucher. B
    reste independant, et un run normal ne laisse aucun verrou."""
    corps_test = IMPORTE + "def test_double():\n    assert double(3) == 6\n"
    module_a, test_a = _bac(tmp_path / "a", MODULE, corps_test)
    module_b, test_b = _bac(tmp_path / "b", MODULE, corps_test)
    verrou_a = _sentinelle_de(module_a).with_suffix(".lock")
    verrou_b = _sentinelle_de(module_b).with_suffix(".lock")
    verrou_a.write_text("preuve concurrente", encoding="utf-8")
    try:
        r_a = _lancer(module_a, test_a)
        assert r_a.returncode == INTERROMPUE, r_a.stdout + r_a.stderr
        assert "PREUVE PRECEDENTE EN COURS" in r_a.stdout
        assert "1/2" not in r_a.stdout, "aucun run ne doit partir sous verrou"
        assert module_a.read_bytes() == MODULE.encode("utf-8")
        assert verrou_a.exists(), "le verrou d'autrui n'est pas a nous"

        r_b = _lancer(module_b, test_b)
        assert r_b.returncode == TENUE, r_b.stdout + r_b.stderr
        assert not verrou_b.exists(), "un run normal laisse son verrou"
    finally:
        verrou_a.unlink(missing_ok=True)
        verrou_b.unlink(missing_ok=True)
