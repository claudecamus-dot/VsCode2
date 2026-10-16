"""Mode COPIE de `scripts/preuve_p1.py` (2026-10-15).

Les 2026-10-14/15 le site 8020 (`--reload` sur `app/`) est tombe plusieurs fois
pendant des preuves qui mutaient `app/` EN PLACE. Par defaut l'outil mute
desormais une COPIE du depot : le depot reel n'est jamais ecrit.

Le test central ne se contente pas de comparer les octets APRES la preuve (une
restauration octet pour octet les rendrait identiques meme en place) : le test du
mini-projet enregistre, PENDANT chacun de ses deux runs, ce que contient le
fichier ORIGINAL et d'ou vient le module importe. En place, le second run voit
l'original mute ; en copie, jamais.
"""
from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
OUTIL = RACINE / "scripts" / "preuve_p1.py"
SEPARATEUR = "---PREUVE-P1---"
MODULE = "def double(x):\n    return x * 2\n"
AVANT, APRES = "    return x + 2", "    return x * 2"

# Le test du mini-projet note, a chaque run, l'ORIGINAL et le module importe.
TEST_ESPION = (
    "import os, sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, str(Path(__file__).parent))\n"
    "import sujet\n"
    "def test_double():\n"
    "    original = Path(os.environ['P1_ORIGINAL']).read_text(encoding='utf-8')\n"
    "    with open(os.environ['P1_LOG'], 'a', encoding='utf-8') as f:\n"
    "        f.write(repr(original) + '|' + str(Path(sujet.__file__).resolve()) + '\\n')\n"
    "    assert sujet.double(3) == 6\n"
)


def _charger_outil():
    spec = importlib.util.spec_from_file_location("preuve_p1_sous_test", OUTIL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _projet(tmp_path: Path, test: str = TEST_ESPION) -> Path:
    depot = tmp_path / "depot"
    depot.mkdir()
    (depot / "sujet.py").write_bytes(MODULE.encode("utf-8"))
    (depot / "test_sujet.py").write_bytes(test.encode("utf-8"))
    return depot


def _lancer(tmp_path: Path, depot: Path, *extra: str, avant: str = AVANT,
            apres: str = APRES) -> subprocess.CompletedProcess:
    marqueurs = tmp_path / "marqueurs.txt"
    marqueurs.write_text(avant + "\n" + SEPARATEUR + "\n" + apres, encoding="utf-8")
    systmp = tmp_path / "systmp"
    systmp.mkdir(exist_ok=True)
    env = {**os.environ, "TMP": str(systmp), "TEMP": str(systmp), "TMPDIR": str(systmp),
           "P1_ORIGINAL": str(depot / "sujet.py"), "P1_LOG": str(tmp_path / "log.txt")}
    return subprocess.run(
        [sys.executable, str(OUTIL), "sujet.py", "test_sujet.py", "--depot", str(depot),
         "--python", sys.executable, "--marqueur-fichier", str(marqueurs), *extra],
        cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=600, env=env)


def test_par_defaut_l_original_n_est_jamais_ecrit_pas_meme_pendant_la_preuve(tmp_path):
    depot = _projet(tmp_path)
    original = depot / "sujet.py"
    avant_octets, avant_mtime = original.read_bytes(), original.stat().st_mtime_ns
    r = _lancer(tmp_path, depot)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PREUVE P1 TENUE" in r.stdout
    assert "mode COPIE" in r.stdout and "copie supprimee" in r.stdout
    assert original.read_bytes() == avant_octets
    # mtime : une mutation en place suivie d'une restauration le change.
    assert original.stat().st_mtime_ns == avant_mtime
    lignes = (tmp_path / "log.txt").read_text(encoding="utf-8").splitlines()
    assert len(lignes) == 2, lignes  # run vert + run rouge
    for ligne in lignes:
        contenu, _, module_importe = ligne.rpartition("|")
        assert contenu == repr(MODULE), "l'ORIGINAL a ete vu mute pendant la preuve"
        # le module importe vient de la copie, pas du depot
        assert not Path(module_importe).is_relative_to(depot.resolve()), module_importe
    assert not list((tmp_path / "systmp").glob("p1c_*")), "copie non supprimee"


def test_en_place_reste_fonctionnel_et_mute_bien_l_original(tmp_path):
    depot = _projet(tmp_path)
    r = _lancer(tmp_path, depot, "--en-place")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PREUVE P1 TENUE" in r.stdout
    assert "restauration : OK" in r.stdout and "mode COPIE" not in r.stdout
    lignes = (tmp_path / "log.txt").read_text(encoding="utf-8").splitlines()
    assert len(lignes) == 2
    assert lignes[0].startswith(repr(MODULE)) and lignes[1].startswith(repr(
        MODULE.replace(APRES, AVANT))), "en place, le 2e run doit voir l'original mute"
    assert (depot / "sujet.py").read_bytes() == MODULE.encode("utf-8")


def test_les_refus_de_marqueur_et_de_test_deja_rouge_restent_en_mode_copie(tmp_path):
    depot = _projet(tmp_path)
    r = _lancer(tmp_path, depot, apres="    return x * 3")  # absent
    assert r.returncode == 3 and "MARQUEUR NON UNIQUE" in r.stdout, r.stdout
    autre = tmp_path / "b"
    autre.mkdir()
    rouge = _projet(autre, "def test_double():\n    assert False\n")
    r = _lancer(tmp_path, rouge)
    assert r.returncode == 4 and "DEJA ROUGE" in r.stdout, r.stdout
    assert (rouge / "sujet.py").read_bytes() == MODULE.encode("utf-8")


def test_la_copie_exclut_venv_git_data_env_caches_et_worktrees(tmp_path):
    outil = _charger_outil()
    depot = (tmp_path / "d").resolve()
    for d in (".venv", ".git", "data", "_bmad", ".claude/worktrees", "app/__pycache__",
              ".claude/skills", "app", "tests", "scripts"):
        (depot / d).mkdir(parents=True, exist_ok=True)
        (depot / d / "f.txt").write_text("x", encoding="utf-8")
    (depot / ".env").write_text("SECRET=1", encoding="utf-8")
    (depot / "CLAUDE.md").write_text("x", encoding="utf-8")
    copie = outil._copier_depot(depot, tmp_path)
    try:
        presents = {p.relative_to(copie).as_posix() for p in copie.rglob("*")}
        for exclu in (".venv", ".git", "data", "_bmad", ".claude/worktrees",
                      "app/__pycache__", ".env"):
            assert exclu not in presents, exclu
        for garde in ("app/f.txt", "tests/f.txt", "scripts/f.txt",
                      ".claude/skills/f.txt", "CLAUDE.md"):
            assert garde in presents, garde
    finally:
        assert outil._supprimer_copie(copie)
    assert not copie.exists()


def test_un_gros_fichier_est_lie_mais_la_cible_mutee_ne_touche_pas_l_original(tmp_path):
    """Lien dur au-dela de 5 Mo : la mutation fait unlink + ecriture, donc le
    fichier du depot reste intact meme si la cible etait un lien."""
    outil = _charger_outil()
    depot = (tmp_path / "d").resolve()
    depot.mkdir()
    gros = depot / "gros.bin"
    gros.write_bytes(b"0" * (outil.GROS_FICHIER + 1))
    copie = outil._copier_depot(depot, tmp_path)
    try:
        cible = copie / "gros.bin"
        cible.unlink()
        cible.write_bytes(b"mute")
        assert gros.stat().st_size == outil.GROS_FICHIER + 1
    finally:
        outil._supprimer_copie(copie)


def test_le_nettoyage_ne_chmod_pas_un_fichier_lie_au_depot(tmp_path):
    """Finding G1-1 : `reessayer` faisait `chmod 0o700` sur un lien dur, donc sur
    l'inode du DEPOT. Un original en lecture seule doit le rester."""
    outil = _charger_outil()
    depot = (tmp_path / "d").resolve()
    depot.mkdir()
    gros = depot / "gros.bin"
    gros.write_bytes(b"0" * (outil.GROS_FICHIER + 1))
    os.chmod(gros, stat.S_IREAD)
    mode_avant = gros.stat().st_mode
    copie = outil._copier_depot(depot, tmp_path)
    try:
        assert gros.stat().st_nlink > 1, "le gros fichier devait etre lie"
        outil._supprimer_copie(copie)  # le lien en lecture seule resiste : normal
        assert gros.stat().st_mode == mode_avant, "chmod propage a l'original"
        assert gros.read_bytes() == b"0" * (outil.GROS_FICHIER + 1)
    finally:
        os.chmod(gros, stat.S_IWRITE | stat.S_IREAD)
        outil._supprimer_copie(copie)


def test_main_ne_reecrit_pas_l_original_lie_octets_ni_mtime(tmp_path, monkeypatch, capsys):
    """Finding G1-2 : GROS_FICHIER reduit, le sujet est donc un LIEN DUR ; seul
    `fichier_mut.unlink()` de `main` empeche la mutation d'ecrire l'original."""
    outil = _charger_outil()
    monkeypatch.setattr(outil, "GROS_FICHIER", 10)
    depot = _projet(tmp_path)
    original = depot / "sujet.py"
    octets, mtime = original.read_bytes(), original.stat().st_mtime_ns
    marqueurs = tmp_path / "marqueurs.txt"
    marqueurs.write_text(AVANT + "\n" + SEPARATEUR + "\n" + APRES, encoding="utf-8")
    systmp = tmp_path / "systmp"
    systmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(systmp))
    monkeypatch.setenv("P1_ORIGINAL", str(original))
    monkeypatch.setenv("P1_LOG", str(tmp_path / "log.txt"))
    monkeypatch.setattr(sys, "argv", [
        "preuve_p1.py", "sujet.py", "test_sujet.py", "--depot", str(depot),
        "--python", sys.executable, "--marqueur-fichier", str(marqueurs)])
    code = outil.main()
    sortie = capsys.readouterr().out
    assert code == 0, sortie
    assert original.read_bytes() == octets, "l'original lie a ete ecrit"
    assert original.stat().st_mtime_ns == mtime
    lignes = (tmp_path / "log.txt").read_text(encoding="utf-8").splitlines()
    assert len(lignes) == 2 and all(l.startswith(repr(MODULE)) for l in lignes), lignes


def _copie_factice(tmp_path: Path, init: str) -> Path:
    copie = tmp_path / "copie"
    (copie / "app").mkdir(parents=True)
    (copie / "app" / "__init__.py").write_text(init, encoding="utf-8")
    return copie


def test_import_vient_de_accepte_un_app_qui_vient_de_la_copie(tmp_path):
    outil = _charger_outil()
    copie = _copie_factice(tmp_path, "")
    assert outil._import_vient_de(copie, sys.executable) is None


def test_import_vient_de_refuse_un_app_qui_vient_d_ailleurs(tmp_path):
    outil = _charger_outil()
    ailleurs = tmp_path / "ailleurs" / "app" / "__init__.py"
    copie = _copie_factice(tmp_path, f"__file__ = {str(ailleurs)!r}\n")
    refus = outil._import_vient_de(copie, sys.executable)
    assert refus and "ne vient PAS de la copie" in refus, refus


def test_import_vient_de_capture_le_timeout_au_lieu_de_lever(tmp_path, monkeypatch):
    outil = _charger_outil()
    copie = _copie_factice(tmp_path, "")

    def lent(*a, **k):
        raise subprocess.TimeoutExpired(cmd="python", timeout=120)

    monkeypatch.setattr(outil.subprocess, "run", lent)
    refus = outil._import_vient_de(copie, sys.executable)
    assert refus and "n'a pas rendu" in refus, refus


def test_main_refuse_de_conclure_si_app_importe_vient_d_ailleurs(tmp_path):
    """R2 : l'appel `_import_vient_de` de main() (pas seulement la fonction) —
    sans lui, la preuve tournerait sur un `app` du depot reel sans le dire."""
    depot = _projet(tmp_path)
    ailleurs = tmp_path / "ailleurs" / "app" / "__init__.py"
    (depot / "app").mkdir()
    (depot / "app" / "__init__.py").write_text(f"__file__ = {str(ailleurs)!r}\n",
                                               encoding="utf-8")
    r = _lancer(tmp_path, depot)
    assert r.returncode != 0 and "ne vient PAS de la copie" in r.stdout, r.stdout + r.stderr
    assert "PREUVE P1 TENUE" not in r.stdout
    assert not (tmp_path / "log.txt").exists(), "le test a ete joue malgre le refus"


def test_sans_app_init_le_controle_d_import_est_annonce_non_verifie(tmp_path):
    """S4 : plus de saut silencieux du controle d'import."""
    depot = _projet(tmp_path)
    r = _lancer(tmp_path, depot)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "import NON verifie" in r.stdout
