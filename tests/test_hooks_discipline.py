"""Tests-contrat des hooks de discipline (.claude/hooks/) — créés le 2026-07-29
en appliquant les constats superviseur arbitrés du jour. Jusqu'ici ces hooks
n'avaient AUCUN test (le constat `sync-canon` — un commit dispositif sans test
cassant la suite — vaut aussi pour eux) : chaque hook est exercé ici comme un
sous-processus réel, JSON sur stdin, exactement comme Claude Code l'invoque.

Les filets « reliquat de séance » et « validations en attente » vivent dans le
CANON du hub (scan_transcripts.py, synchronisé le même jour — arbre_sale /
runs_a_solder), pas ici : une première implémentation locale dans le hook remind
a été retirée le jour même pour ne pas dupliquer le canon en divergeant
(seuil 24 h, exclusions du churn généré). Cf. tests/test_agent_supervision.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
REMIND = REPO / ".claude" / "hooks" / "remind_revue_increment.py"
WARN = REPO / ".claude" / "hooks" / "warn_verif_before_commit.py"


def _run_hook(script: Path, payload) -> subprocess.CompletedProcess:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(script)],
        input=raw, capture_output=True, text=True, timeout=30,
    )


def _context(result: subprocess.CompletedProcess) -> str:
    """additionalContext émis par le hook (chaîne vide si silencieux)."""
    if not result.stdout.strip():
        return ""
    data = json.loads(result.stdout)
    return data.get("hookSpecificOutput", {}).get("additionalContext", "")


@pytest.fixture
def tmp_path(tmp_path_git):
    """`git init` dans le `tmp_path` de pytest échoue quand la suite tourne
    élevée (ACL restrictive de `mode=0o700` sous Python 3.13+) — cf. conftest."""
    return tmp_path_git


def _git(args, cwd) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@test", "-c", "user.name=t"] + args,
        cwd=cwd, check=True, capture_output=True, timeout=30,
    )


def _transcript(tmp_path: Path, commands: list) -> Path:
    """Transcript de session minimal : une ligne JSONL par tool_use Bash."""
    path = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({"message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}
        ]}})
        for cmd in commands
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# remind_revue_increment — rappel de discipline (les filets git/validations
# vivent dans le canon scan_transcripts.py, cf. docstring du module)
# --------------------------------------------------------------------------- #

def test_remind_rappelle_la_discipline(tmp_path):
    ctx = _context(_run_hook(REMIND, {"cwd": str(tmp_path)}))
    assert "Discipline qualité" in ctx
    assert "revue-increment" in ctx


def test_remind_fail_open_sur_stdin_invalide(tmp_path):
    r = _run_hook(REMIND, "pas du json")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


# --------------------------------------------------------------------------- #
# warn_verif_before_commit — zone app/ (message pytest) + zone dispositif
# --------------------------------------------------------------------------- #

def _repo_avec_stage(tmp_path: Path, relpath: str) -> Path:
    _git(["init", "-q"], tmp_path)
    f = tmp_path / Path(relpath)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# contenu\n", encoding="utf-8")
    _git(["add", relpath], tmp_path)
    return tmp_path


def _payload(tmp_path: Path, transcript: Path = None) -> dict:
    return {
        "tool_input": {"command": 'git commit -m "x"'},
        "cwd": str(tmp_path),
        "transcript_path": str(transcript) if transcript else "",
    }


def test_warn_commit_dispositif_sans_contrat_avertit_et_nomme_les_deux_fichiers(tmp_path):
    """Constat superviseur `sync-canon` 2026-07-29 (arbitré) : 5eb121b (+23 lignes
    log_run.py, 0 test) a cassé un test-contrat, vu seulement à la revue suivante.
    Le gate exécutable : commit touchant le dispositif sans trace des
    fichiers-contrat cette session → avertissement les nommant."""
    _repo_avec_stage(tmp_path, ".claude/orchestration/nouveau_script.py")
    r = _run_hook(WARN, _payload(tmp_path))
    ctx = _context(r)
    assert "tests/test_agent_orchestration.py" in ctx
    assert "tests/test_agent_supervision.py" in ctx


def test_warn_commit_dispositif_silencieux_si_fichiers_contrat_joues(tmp_path):
    _repo_avec_stage(tmp_path, ".claude/supervision/outil.py")
    transcript = _transcript(
        tmp_path, ["pytest tests/test_agent_supervision.py tests/test_agent_orchestration.py -q"])
    r = _run_hook(WARN, _payload(tmp_path, transcript))
    assert r.stdout.strip() == ""


def test_warn_commit_dispositif_silencieux_si_suite_complete_jouee(tmp_path):
    """`pytest -q` (suite complète, aucun chemin tests/...) inclut de fait les
    fichiers-contrat : pas d'avertissement."""
    _repo_avec_stage(tmp_path, ".claude/hooks/un_hook.py")
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    r = _run_hook(WARN, _payload(tmp_path, transcript))
    assert r.stdout.strip() == ""


def test_warn_commit_app_sans_verif_cite_pytest_pas_npm_test(tmp_path):
    """Régression du message hérité du portage VSCode1 : `_VERIF_BASH` détecte
    pytest mais le texte disait encore « npm test » — corrigé le 2026-07-29."""
    _repo_avec_stage(tmp_path, "app/services/quelque_chose.py")
    ctx = _context(_run_hook(WARN, _payload(tmp_path)))
    assert "pytest" in ctx
    assert "npm test" not in ctx


def test_warn_commit_app_et_dispositif_cumule_les_deux_avertissements(tmp_path):
    _repo_avec_stage(tmp_path, "app/services/quelque_chose.py")
    autre = tmp_path / ".claude" / "orchestration" / "outil.py"
    autre.parent.mkdir(parents=True, exist_ok=True)
    autre.write_text("# x\n", encoding="utf-8")
    _git(["add", "."], tmp_path)
    ctx = _context(_run_hook(WARN, _payload(tmp_path)))
    assert "code applicatif" in ctx
    assert "fichiers-contrat" in ctx


def test_warn_commit_hors_zones_reste_silencieux(tmp_path):
    _repo_avec_stage(tmp_path, "docs/notes.md")
    r = _run_hook(WARN, _payload(tmp_path))
    assert r.stdout.strip() == ""


# --------------------------------------------------------------------------- #
# guard_destructive_git — le garde-fou de l'ARBRE, pas seulement de l'historique
# --------------------------------------------------------------------------- #
# Ajoutés le 2026-09-02, sur incident réel : un sous-agent de revue a joué
# `git checkout --` sur deux templates pour mesurer le code d'avant, effaçant
# les correctifs non commités de la session appelante. Le hook ne connaissait
# alors que `push --force` et `reset --hard` — deux commandes qui touchent
# l'HISTORIQUE, quand le travail perdu ce jour-là était dans l'ARBRE.
#
# Le hook n'avait par ailleurs AUCUN test, alors que deux autres hooks
# l'importent (`check_ci_after_push`, `warn_verif_before_commit`) : une
# régression dans son tokenizer les cassait tous les trois en silence.

GUARD = REPO / ".claude" / "hooks" / "guard_destructive_git.py"


def _refus(commande: str, cwd: Path | None = None) -> str:
    """Motif de refus rendu par le garde-fou (chaîne vide s'il laisse passer)."""
    payload = {"tool_name": "Bash", "tool_input": {"command": commande}}
    env = None
    if cwd is not None:
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)}
    r = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload), capture_output=True, text=True, timeout=30, env=env,
    )
    if not r.stdout.strip():
        return ""
    sortie = json.loads(r.stdout)["hookSpecificOutput"]
    assert sortie["permissionDecision"] == "deny"
    return sortie["permissionDecisionReason"]


@pytest.fixture
def depot(tmp_path):
    """Un dépôt minimal portant un fichier suivi — de quoi distinguer un chemin
    réel d'un nom de branche."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "ecran.html").write_text("<p>x</p>\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "commande",
    [
        "git checkout -- app/ecran.html",
        "git checkout HEAD -- app/ecran.html",
        "git checkout -- .",
        "git checkout app/ecran.html",
        "cd /tmp && git checkout -- app/ecran.html",
        'eval "git checkout -- app/ecran.html"',
    ],
)
def test_le_garde_fou_refuse_d_ecraser_un_fichier_de_l_arbre(commande, depot):
    """La commande exacte de l'incident, et ses formes voisines. Chacune écrase
    les modifications non commitées du fichier, sans copie de secours."""
    motif = _refus(commande, depot)
    assert motif, "commande destructive laissée passer : %s" % commande
    assert "git show HEAD:" in motif, (
        "le refus doit dire comment lire le code d'avant sans rien détruire — "
        "sinon il empêche un besoin légitime de revue sans offrir d'issue"
    )


@pytest.mark.parametrize(
    "commande",
    [
        "git checkout main",
        "git checkout -b nouvelle-branche",
        "git checkout -B nouvelle-branche",
        "git status",
        "git diff app/ecran.html",
        "git show HEAD:app/ecran.html",
        "git restore --staged app/ecran.html",
    ],
)
def test_le_garde_fou_laisse_passer_ce_qui_ne_detruit_rien(commande, depot):
    """La contrepartie, sans laquelle le garde-fou serait inutilisable : changer
    de branche, en créer une, lire un fichier ou DÉSINDEXER n'écrase aucun
    travail. `git restore --staged` en particulier ne touche que l'index — il
    sert précisément à retirer d'un commit un fichier hors périmètre."""
    assert _refus(commande, depot) == "", "commande légitime bloquée : %s" % commande


def test_le_garde_fou_refuse_restore_du_fichier_mais_pas_de_l_index(depot):
    """`git restore` sans `--staged` écrit dans le fichier ; avec, il ne touche
    que l'index. Les deux formes ne diffèrent que d'un drapeau, et se confondre
    coûte un fichier."""
    assert _refus("git restore app/ecran.html", depot)
    assert _refus("git restore --staged app/ecran.html", depot) == ""
    # Cumulées, elles écrasent bien le fichier : le drapeau `--staged` ne suffit
    # plus à rendre la commande inoffensive.
    assert _refus("git restore --staged --worktree app/ecran.html", depot)


@pytest.mark.parametrize("commande", ["git clean -f", "git clean -fd", "git clean -xdf"])
def test_le_garde_fou_refuse_de_supprimer_les_fichiers_non_suivis(commande, depot):
    """Le danger propre à `git clean` : il emporte les fichiers NEUFS, pas
    encore ajoutés — un test qu'on vient d'écrire, typiquement. Les formes
    groupées comptent : c'est `-fd` qu'on écrit en pratique, pas `-f -d`."""
    motif = _refus(commande, depot)
    assert motif and "git clean -n" in motif


def test_le_garde_fou_laisse_lister_avant_de_supprimer(depot):
    """`git clean -n` ne fait que lister : c'est l'issue que le refus propose,
    elle doit rester ouverte."""
    assert _refus("git clean -n", depot) == ""


@pytest.mark.parametrize("commande", ["git stash drop", "git stash clear"])
def test_le_garde_fou_refuse_de_jeter_une_remise(commande, depot):
    """Une remise supprimée n'est plus récupérable par aucune commande
    ordinaire — contrairement à `git stash push`, qui reste autorisé."""
    assert _refus(commande, depot)


def test_le_garde_fou_laisse_remiser_et_reprendre(depot):
    assert _refus("git stash push -m wip", depot) == ""
    assert _refus("git stash pop", depot) == ""


def test_les_garde_fous_d_historique_tiennent_toujours(depot):
    """Non-régression des deux règles d'origine : l'extension à l'arbre ne doit
    pas les avoir déplacées."""
    assert _refus("git push --force", depot)
    assert _refus("git reset --hard HEAD~1", depot)
    assert _refus("git push --force-with-lease", depot) == ""


def test_un_message_de_commit_qui_DECRIT_la_commande_passe(depot):
    """Le piège déjà payé sur ce hook : un message de commit qui parle de la
    commande gardée n'est pas un appel à cette commande. Le corps d'un heredoc
    est de la donnée."""
    commande = (
        "git commit -F - <<'EOF'\n"
        "Garde-fou : git checkout -- <fichier> est desormais bloque\n"
        "EOF"
    )
    assert _refus(commande, depot) == ""


# --------------------------------------------------------------------------- #
# Gardes « périmètre » et « plafond de lot » (2026-09-10)
#
# Arbitrées par l'utilisateur sur le plan du superviseur, après une séance où
# 4 rondes de revue adversariale ont trouvé 3 bloquants et 13 majeurs dans des
# correctifs fraîchement écrits — dont 3 de la même forme : une garde posée sur
# un chemin, ses frères laissés nus.
#
# La garde « périmètre » a été calibrée TROIS fois, et c'est la mesure sur les
# commits réels qui a tranché, jamais l'intuition :
#   - « le diff ajoute un motif de garde »            -> 62 % de déclenchement,
#     et ZÉRO des trois cas que la garde citait nommément ;
#   - « … plus les mots d'exhaustivité dans le code » -> 70 %, parce que
#     « dernier », « toutes les », « plus aucun » sont du français courant ;
#   - « la forme gardée est ajoutée ET sa forme NUE subsiste ailleurs » -> 22 %,
#     en nommant les fichiers frères.
# Une garde qui parle à deux commits sur trois ne se fait même pas débrancher :
# on cesse de la lire, et rien ne le signale.
# --------------------------------------------------------------------------- #

def _payload_msg(tmp_path: Path, message: str, transcript: Path = None) -> dict:
    return {
        "tool_input": {"command": 'git commit -m "' + message + '"'},
        "cwd": str(tmp_path),
        "transcript_path": str(transcript) if transcript else "",
    }


def _depot_avec_frere_nu(tmp_path: Path) -> Path:
    """Un dépôt où le commit pose la garde et où un AUTRE fichier, déjà
    committé, conserve la forme nue."""
    _git(["init", "-q"], tmp_path)
    app = tmp_path / "app"
    app.mkdir(parents=True, exist_ok=True)
    (app / "frere.py").write_text(
        "async def autre(file):\n    contenu = await file.read()\n", encoding="utf-8")
    _git(["add", "app/frere.py"], tmp_path)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"], tmp_path)
    (app / "garde.py").write_text(
        "async def route(file):\n    contenu = await lire_upload_borne(file)\n",
        encoding="utf-8")
    _git(["add", "app/garde.py"], tmp_path)
    return tmp_path


def test_garde_perimetre_nomme_le_frere_reste_nu(tmp_path):
    """Le cœur de la garde : elle ne dit pas « attention », elle donne le
    `grep` qu'on aurait dû lancer — le fichier frère, par son nom."""
    _depot_avec_frere_nu(tmp_path)
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    r = _run_hook(WARN, _payload_msg(tmp_path, "Borne posee sur la route", transcript))
    ctx = _context(r)
    assert "PÉRIMÈTRE" in ctx
    assert "app/frere.py" in ctx, "la garde n'a pas nomme le frere reste nu"
    assert "await file.read()" in ctx, "la garde ne dit pas QUELLE forme reste nue"


def test_garde_perimetre_voit_le_frere_nu_DANS_LE_FICHIER_TOUCHE(tmp_path):
    """Le défaut fondateur : « garde posée sur 1 chemin d'écriture sur 3 ».

    Ces trois chemins vivent d'ordinaire dans le MÊME fichier. La première
    version excluait en bloc tous les fichiers touchés par le commit, donc se
    taisait exactement sur le cas qui l'a fait naître (revue du 2026-09-10, T3).
    """
    _git(["init", "-q"], tmp_path)
    app = tmp_path / "app"
    app.mkdir(parents=True, exist_ok=True)
    # Un seul fichier : une route gardée, DEUX routes encore nues.
    (app / "routes.py").write_text(
        "async def une(file):\n    return await lire_upload_borne(file)\n"
        "async def deux(file):\n    return await file.read()\n"
        "async def trois(file):\n    return await file.read()\n",
        encoding="utf-8")
    _git(["add", "app/routes.py"], tmp_path)
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    r = _run_hook(WARN, _payload_msg(tmp_path, "Borne posee sur une route", transcript))
    ctx = _context(r)
    assert "PÉRIMÈTRE" in ctx, (
        "la garde est aveugle au cas qui l'a fait naitre : les chemins freres "
        "dans le fichier qu'on vient de toucher"
    )
    assert "app/routes.py" in ctx, "la garde ne nomme pas le fichier concerne"
    assert "dans CE commit" in ctx, (
        "la garde ne dit pas que le site restant est dans le fichier du commit"
    )


def test_garde_perimetre_se_tait_quand_aucun_frere_ne_reste(tmp_path):
    """La contrepartie, et c'est elle qui rend la garde lisible : si la forme
    nue n'existe plus nulle part, silence."""
    _git(["init", "-q"], tmp_path)
    app = tmp_path / "app"
    app.mkdir(parents=True, exist_ok=True)
    (app / "garde.py").write_text(
        "async def route(file):\n    contenu = await lire_upload_borne(file)\n",
        encoding="utf-8")
    _git(["add", "app/garde.py"], tmp_path)
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    r = _run_hook(WARN, _payload_msg(tmp_path, "Borne posee", transcript))
    assert "PÉRIMÈTRE" not in _context(r)


def test_garde_perimetre_se_tait_si_le_perimetre_est_annonce(tmp_path):
    """Un périmètre partiel ASSUMÉ n'est pas un défaut. La ligne suffit, quel
    que soit son compte — et elle est acceptée SANS accents, parce que les
    commits de ce dépôt sont écrits ainsi (le premier jet exigeait
    « Périmètre: » accentué : inapplicable en pratique)."""
    _depot_avec_frere_nu(tmp_path)
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    message = "Borne. Perimetre: grep read app/ -> 2 sites, 1 garde, 1 differe : a part"
    r = _run_hook(WARN, _payload_msg(tmp_path, message, transcript))
    assert "PÉRIMÈTRE" not in _context(r)


def _hook_avec_config(tmp_path: Path, config: dict = None):
    """Le hook chargé comme s'il vivait dans un AUTRE dépôt, avec la config de
    ce dépôt-là (ou sans config du tout).

    `_config_path()` dérive du chemin du hook : pour observer ce que voit
    VSCode1/3/4, il faut donc une COPIE du fichier dans une arborescence à
    nous. Le charger sur place ne prouve rien — il lit alors la configuration
    de CE dépôt, où les deux clés sont posées.
    """
    import importlib.util
    import shutil

    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    copie = hooks / WARN.name
    shutil.copy(WARN, copie)
    if config is not None:
        (tmp_path / ".claude" / "warn_verif_before_commit.json").write_text(
            json.dumps(config), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("hook_autre_depot", copie)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_garde_perimetre_couvre_git_commit_a(tmp_path):
    """`git commit -a` valide les fichiers suivis sans passer par l'index.

    Deux lecteurs du périmètre répondaient différemment à « ce commit prend-il
    tout ? » : `_staged_files` ne connaissait que `-a`/`--all`, `_diff_ajoute` y
    ajoutait le littéral `-am`, et ni l'un ni l'autre ne voyait `-va`. Pour
    `git commit -am`, la liste des fichiers était vide et le hook sortait avant
    d'évaluer la moindre garde (revue du 2026-09-10, T7). C'est pourtant la
    forme la plus propice aux lots larges — celle que la garde vise.
    """
    _git(["init", "-q"], tmp_path)
    app = tmp_path / "app"
    app.mkdir(parents=True, exist_ok=True)
    (app / "frere.py").write_text(
        "async def autre(file):\n    contenu = await file.read()\n", encoding="utf-8")
    (app / "garde.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "app/frere.py", "app/garde.py"], tmp_path)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"], tmp_path)
    # Modifié mais JAMAIS mis en scène : seul `-a` le verra.
    (app / "garde.py").write_text(
        "async def route(file):\n    contenu = await lire_upload_borne(file)\n",
        encoding="utf-8")
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    payload = {
        "tool_input": {"command": 'git commit -am "Borne posee"'},
        "cwd": str(tmp_path),
        "transcript_path": str(transcript),
    }
    ctx = _context(_run_hook(WARN, payload))
    assert "app/frere.py" in ctx, (
        "la garde est aveugle a `git commit -am` : la forme la plus propice "
        "aux lots larges passe au travers"
    )


def test_un_message_court_n_est_pas_pris_pour_un_git_commit_a(tmp_path):
    """`git commit -mabc` porte le message « abc », pas un `-a`.

    Lire un groupe de drapeaux courts caractère par caractère sans s'arrêter au
    premier qui consomme une valeur ferait de tout message contenant un « a »
    un commit `--all`."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("hook_flags", WARN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._commit_prend_tout(["-am"]) is True
    assert mod._commit_prend_tout(["-va"]) is True
    assert mod._commit_prend_tout(["-a"]) is True
    assert mod._commit_prend_tout(["--all"]) is True
    assert mod._commit_prend_tout(["-mabc"]) is False, (
        "le 'a' du message a ete pris pour le drapeau --all"
    )
    assert mod._commit_prend_tout(["--amend"]) is False, "--amend pris pour --all"
    assert mod._commit_prend_tout([]) is False
    # Trouvés en ronde 2 : `-u<mode>` et `-S<keyid>` consomment aussi la fin du
    # groupe, et une valeur SÉPARÉE commençant par un tiret était relue comme
    # un groupe de drapeaux.
    assert mod._commit_prend_tout(["-uall"]) is False, "`-uall` pris pour --all"
    assert mod._commit_prend_tout(["-Sabc"]) is False, "`-Sabc` pris pour --all"
    assert mod._commit_prend_tout(["-m", "-analyse du lot"]) is False, (
        "un message separe commencant par un tiret est lu comme des drapeaux"
    )
    assert mod._commit_prend_tout(["-m", "msg", "-a"]) is True, (
        "un `-a` apres une valeur separee n'est plus vu"
    )


def test_plafond_de_lot_parle_meme_hors_perimetre_surveille(tmp_path):
    """Un lot de docs et de tests, sans une ligne de code surveillé, reste un
    lot trop large — et c'est même la forme qu'on veut le plus découper.

    Le premier jet évaluait le plafond APRÈS la sortie « rien sous un périmètre
    surveillé » : mesuré à 10 fichiers sous docs/tests/scripts, zéro
    avertissement, pendant que le commentaire affirmait l'inverse (ronde 2).
    """
    _git(["init", "-q"], tmp_path)
    for i in range(10):
        f = tmp_path / "docs" / f"note{i}.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x\n", encoding="utf-8")
        _git(["add", f"docs/note{i}.md"], tmp_path)
    ctx = _context(_run_hook(WARN, _payload_msg(tmp_path, "Dix notes")))
    assert "LOT TROP LARGE" in ctx, (
        "un lot de 10 fichiers hors perimetre surveille ne declenche rien"
    )
    assert "10 fichiers" in ctx


def test_committer_la_CONFIGURATION_ne_declenche_pas_la_garde(tmp_path):
    """Le fichier de configuration DÉCLARE les formes gardées : son diff les
    contient toutes. Lire le diff entier faisait donc déclencher la garde sur
    le commit qui touche cette configuration — cinq blocs fantômes mesurés en
    ronde 2, sur le commit même qui introduisait la clé."""
    _depot_avec_frere_nu(tmp_path)
    # `app/garde.py` posait une garde : on le remplace par un changement
    # applicatif ANODIN. Le commit reste donc sous le perimetre surveille --
    # sans quoi le hook sortirait avant toute garde et le test passerait pour
    # une mauvaise raison (constate en jouant la preuve P1).
    (tmp_path / "app" / "garde.py").write_text("VERSION = 2\n", encoding="utf-8")
    _git(["add", "app/garde.py"], tmp_path)
    # La SEULE mention d'une forme gardee vient du JSON de configuration.
    faux_config = tmp_path / "config_des_gardes.json"
    faux_config.write_text(
        json.dumps({"paires_de_garde": [
            ["lire_upload_borne", "await file.read()", "lecture d'upload"]]}),
        encoding="utf-8")
    _git(["add", "config_des_gardes.json"], tmp_path)
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    ctx = _context(_run_hook(WARN, _payload_msg(tmp_path, "Declare les paires", transcript)))
    assert "PÉRIMÈTRE" not in ctx, (
        "le fichier qui DECLARE les formes gardees declenche la garde : "
        "elle parle sur un commit qui n'ajoute aucune garde"
    )


def test_les_constantes_mortes_ne_reviennent_pas(tmp_path):
    """Deux constantes non référencées traînaient dans le hook. La pire,
    `_PLAFOND_FICHIERS_LOT = 6`, dupliquait EN DUR le seuil que la
    configuration porte : qui l'éditait ne changeait rien, et qui la lisait
    croyait tenir le seuil effectif (revue du 2026-09-10, T9)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("hook_mortes", WARN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert not hasattr(mod, "_PLAFOND_FICHIERS_LOT"), (
        "seconde source de verite pour le seuil : le seuil effectif est "
        "_PLAFOND_LOT, lu dans la configuration"
    )
    assert not hasattr(mod, "_MOTS_D_EXHAUSTIVITE"), (
        "vocabulaire d'un calibrage mesure puis abandonne (70 % de "
        "declenchement) : le garder laisse croire que la garde s'y appuie"
    )


def test_les_paires_de_garde_viennent_de_la_CONFIGURATION(tmp_path):
    """`lire_upload_borne` et consorts n'existent que dans VSCode2, et ce hook
    est publié verbatim dans cinq dépôts. Les paires étaient écrites en dur,
    enfreignant le contrat « le spécifique va dans le JSON » dans le fichier
    même qui l'énonce (revue du 2026-09-10, T13)."""
    sans = _hook_avec_config(tmp_path / "sans")
    assert sans._PAIRES_DE_GARDE == (), (
        "un depot qui n'a rien declare herite des formes gardees de VSCode2"
    )
    avec = _hook_avec_config(tmp_path / "avec", {
        "paires_de_garde": [["ecrire_borne", "open(", "ecriture"]],
    })
    assert avec._PAIRES_DE_GARDE == (("ecrire_borne", "open(", "ecriture"),)
    # Une entrée malformée est ignorée SEULE, sans emporter la garde entière.
    partiel = _hook_avec_config(tmp_path / "partiel", {
        "paires_de_garde": [["ok", "bare", "libelle"], ["trop", "court"], "pas une liste"],
    })
    assert partiel._PAIRES_DE_GARDE == (("ok", "bare", "libelle"),)


def test_deux_paires_de_meme_forme_nue_ne_font_qu_UN_bloc(tmp_path):
    """`lire_upload_borne` est un préfixe de `lire_upload_audio_borne`, et les
    deux gardent `await file.read()` : poser la borne audio déclenchait les
    deux paires et imprimait deux fois la même liste de fichiers sous deux
    libellés (revue du 2026-09-10, T13)."""
    _depot_avec_frere_nu(tmp_path)
    garde = tmp_path / "app" / "garde.py"
    garde.write_text(
        "async def route(file):\n    return await lire_upload_audio_borne(file)\n",
        encoding="utf-8")
    _git(["add", "app/garde.py"], tmp_path)
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    ctx = _context(_run_hook(WARN, _payload_msg(tmp_path, "Borne audio", transcript)))
    assert ctx.count("await file.read()") == 1, (
        "la meme forme nue est signalee deux fois : le message se lit comme "
        "deux defauts distincts alors qu'il n'y en a qu'un"
    )


def test_plafond_de_lot_compte_TOUT_le_lot_pas_seulement_app(tmp_path):
    """Le commit qui a motivé cette garde faisait 16 fichiers et 4 sujets :
    du code, des tests, du dispositif et des docs. En ne comptant que la zone
    surveillée, la garde serait restée muette dessus (revue du 2026-09-10, T8).
    """
    _git(["init", "-q"], tmp_path)
    for chemin in ("app/un.py", "tests/deux.py", "docs/trois.md",
                   ".claude/quatre.json", "scripts/cinq.ps1",
                   "six.txt", "sept.md", "huit.cfg"):
        f = tmp_path / chemin
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x\n", encoding="utf-8")
        _git(["add", chemin], tmp_path)
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    ctx = _context(_run_hook(WARN, _payload_msg(tmp_path, "Un lot melange", transcript)))
    assert "LOT TROP LARGE" in ctx, (
        "un lot de 8 fichiers sur 4 zones ne declenche rien : la garde ne voit "
        "que app/, donc pas le melange qui rend le lot non revuable"
    )
    assert "8 fichiers" in ctx, "le message doit donner le compte REEL du lot"


def test_la_porte_de_sortie_ne_s_ouvre_PAS_depuis_le_CODE(tmp_path):
    """La garde cherchait `Périmètre:` dans le message ET dans tout le diff
    ajouté — or son propre diff ajoute la constante `_LIGNE_PERIMETRE =
    "Périmètre:"`. Elle se désarmait donc elle-même, et avec elle tout commit
    touchant ce hook ou n'importe quel fichier portant un commentaire français
    « périmètre : » sans rapport (revue du 2026-09-10, T2).

    Ici le CODE contient la ligne, le MESSAGE non : la garde doit parler."""
    _depot_avec_frere_nu(tmp_path)
    garde = tmp_path / "app" / "garde.py"
    garde.write_text(
        '_LIGNE_PERIMETRE = "Perimetre:"\n'
        "async def route(file):\n    contenu = await lire_upload_borne(file)\n",
        encoding="utf-8")
    _git(["add", "app/garde.py"], tmp_path)
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    r = _run_hook(WARN, _payload_msg(tmp_path, "Borne posee sur la route", transcript))
    ctx = _context(r)
    assert "PÉRIMÈTRE" in ctx, (
        "une ligne de CODE a desarme la garde : n'importe quel commentaire "
        "francais suffirait, et ce hook eteint la sienne en se committant"
    )
    assert "app/frere.py" in ctx


def test_les_gardes_sont_OPT_IN_et_ne_s_heritent_pas(tmp_path):
    """Ce hook est publié tel quel dans CINQ dépôts, et son propre contrat dit
    qu'aucun n'hérite d'un signal sans le déclarer. Sans `perimetre_enabled` /
    `plafond_lot` dans sa configuration, un dépôt ne voit ni l'une ni l'autre —
    c'est ce qui protège VSCode1/3/4, où personne n'a rien arbitré (revue du
    2026-09-10, M11).

    Ce test n'assertait d'abord que les deux CONSTANTES `_DEFAULT_*`, sans
    jamais appeler le chargeur qui décide. Faire de `cfg.get("perimetre_enabled",
    True)` le défaut le laissait vert pendant que les quatre dépôts frères
    héritaient des deux signaux — exactement le défaut visé (revue du
    2026-09-10, T5). Ce qu'on vérifie ici, c'est la valeur EFFECTIVE.
    """
    # 1. Aucune configuration du tout (dépôt qui n'a jamais rien déclaré).
    sans_rien = _hook_avec_config(tmp_path / "vierge")
    assert sans_rien._PERIMETRE_ENABLED is False, "la garde perimetre s'herite"
    assert sans_rien._PLAFOND_LOT == 0, "le plafond de lot s'herite"

    # 2. Une configuration RÉELLE mais muette sur ces deux clés — le cas de
    #    VSCode1/3/4, qui ont un périmètre surveillé et rien arbitré au-delà.
    muette = _hook_avec_config(
        tmp_path / "muette", {"watched_prefixes": ["src/"], "verif_bash": ["pytest"]})
    assert muette._PERIMETRE_ENABLED is False, (
        "une config qui ne parle pas des gardes les active quand meme"
    )
    assert muette._PLAFOND_LOT == 0, "idem pour le plafond de lot"

    # 3. Et le dépôt qui les a bien arbitrées les voit.
    declaree = _hook_avec_config(
        tmp_path / "declaree", {"perimetre_enabled": True, "plafond_lot": 6})
    assert declaree._PERIMETRE_ENABLED is True
    assert declaree._PLAFOND_LOT == 6

    # 4. `True` n'est pas un plafond : en Python `isinstance(True, int)` vaut
    #    vrai, et un plafond de 1 refuserait tout commit de 2 fichiers.
    absurde = _hook_avec_config(tmp_path / "absurde", {"plafond_lot": True})
    assert absurde._PLAFOND_LOT == 0, "un booleen a ete accepte comme plafond"


def test_plafond_de_lot_avertit_au_dela_du_seuil(tmp_path):
    """16 fichiers / 1841 insertions / 4 sujets en un commit, le 2026-09-10 :
    4 rondes de revue. Le lot était trop gros pour être revu en un passage."""
    _git(["init", "-q"], tmp_path)
    for i in range(8):
        f = tmp_path / "app" / ("module" + str(i) + ".py")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x = 1\n", encoding="utf-8")
        _git(["add", "app/module" + str(i) + ".py"], tmp_path)
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    r = _run_hook(WARN, _payload_msg(tmp_path, "Un gros lot", transcript))
    ctx = _context(r)
    assert "LOT TROP LARGE" in ctx
    assert "8 fichiers" in ctx, "le message doit donner le compte reel"


def test_plafond_de_lot_se_tait_sur_un_commit_scope(tmp_path):
    """Un commit d'un ou deux fichiers — le cas normal — ne dit rien."""
    _git(["init", "-q"], tmp_path)
    for i in range(2):
        f = tmp_path / "app" / ("module" + str(i) + ".py")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x = 1\n", encoding="utf-8")
        _git(["add", "app/module" + str(i) + ".py"], tmp_path)
    transcript = _transcript(tmp_path, [".venv/Scripts/python.exe -m pytest -q"])
    r = _run_hook(WARN, _payload_msg(tmp_path, "Un correctif scope", transcript))
    assert "LOT TROP LARGE" not in _context(r)


def test_le_hook_survit_a_un_diff_non_decodable_en_cp1252(tmp_path):
    """BLOQUANT B1 du 2026-09-10, reproduit deux fois : `subprocess.run(text=True)`
    sans `encoding=` décode dans l'encodage de la console. Un diff portant un
    emoji à sélecteur de variante — il y en a dans des centaines de fichiers de
    ce dépôt, dont ce hook — rendait `stdout = None`, et le `.splitlines()`
    levait HORS du try. Le hook plantait, et les TROIS avertissements
    préexistants partaient avec lui : une garde neuve désactivait le garde-fou
    qu'elle venait renforcer.

    La première version de ce test n'assertait que `returncode == 0` et un
    contexte non vide — deux choses que l'avertissement « vérif réelle »
    préexistant satisfait à lui seul, quel que soit le retour de
    `_diff_ajoute`. Retirer `encoding="utf-8"` la laissait VERTE pendant que la
    garde Périmètre devenait muette sur tout diff portant un tel octet : la
    régression que ce test porte son nom d'empêcher passait au travers
    (revue adversariale du 2026-09-10, T4). On exige donc que la garde
    FONCTIONNE sur ce diff, pas seulement que le hook y survive.
    """
    _depot_avec_frere_nu(tmp_path)
    garde = tmp_path / "app" / "garde.py"
    garde.write_text(
        "# ⚠️ attention : caractere non definissable en cp1252\n"
        "async def route(file):\n    contenu = await lire_upload_borne(file)\n",
        encoding="utf-8")
    _git(["add", "app/garde.py"], tmp_path)
    r = _run_hook(WARN, _payload_msg(tmp_path, "Un commit avec un emoji"))
    ctx = _context(r)
    assert r.returncode == 0, "le hook a plante : " + (r.stderr or "")[:300]
    assert ctx, "le hook est muet : les avertissements preexistants sont perdus"
    assert "app/frere.py" in ctx, (
        "la garde Perimetre est muette sur un diff portant un emoji : "
        "`_diff_ajoute` a rendu une chaine vide au lieu du diff"
    )


def test_les_gardes_restent_NON_BLOQUANTES(tmp_path):
    """Comme tout ce hook : elles avertissent, elles n'empêchent pas de livrer.
    Un garde-fou qui bloque se fait débrancher la semaine suivante."""
    _depot_avec_frere_nu(tmp_path)
    r = _run_hook(WARN, _payload_msg(tmp_path, "Borne posee"))
    assert "permissionDecision" not in (r.stdout or ""), (
        "la garde bloque le commit : ce n'est pas le contrat de ce hook"
    )
