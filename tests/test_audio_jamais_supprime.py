"""L'audio ne se supprime QUE par une action de l'utilisateur sur le site
(règle projet du 2026-09-01).

Avant cette date, quatre chemins effaçaient de l'audio tout seuls : le succès
du job d'import, la purge à 7 jours, le refus de reprise, et deux chemins
d'erreur de l'import. Le plus coûteux était le premier — une fois la
transcription réussie, la SOURCE de l'entretien disparaissait, donc aucun rejeu
possible si l'on découvrait ensuite un défaut de transcription ou
d'extraction. C'est exactement le moment où l'on en a besoin, et c'est le
chemin que prennent les enregistrements Meet/Teams importés.

L'entretien enregistré au micro, lui, gardait son audio
(`Interview.audio_backup_path`, décrit dans le modèle comme un « filet de
sécurité en cas de souci de transcription/extraction ») : l'import était le
chemin frère privé du même filet.

Deux invariants sont figés ici :

1. **structurel** — aucun `unlink` d'audio ne subsiste hors de la route de
   suppression du site. C'est le test qui tient dans le temps : il échoue si
   quelqu'un réintroduit un helper de suppression, quel que soit son nom ;
2. **comportemental** — un fichier importé survit au succès du job, à la purge,
   et à un échec de création de job.

Plus la condition qui rend la règle TENABLE : le fichier importé porte le
préfixe de mission, donc il apparaît dans l'onglet Backup, donc l'utilisateur
peut effectivement le supprimer. Sans ce préfixe, « ne jamais supprimer
automatiquement » produisait un fichier fantôme qu'aucun écran n'atteignait.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent


def setup_module() -> None:
    """Crée les tables si un fichier de tests précédent a supprimé la base.

    Volontairement SANS `unlink` : ce module n'a pas besoin d'une base vierge,
    et le motif `dispose() + unlink(DB_PATH)` recopié hors de son contexte a
    déjà détruit des données réelles sur ce projet. `init_db()` est idempotent.
    """
    from app.db import init_db

    init_db()


# --------------------------------------------------------------------------- #
# 1. Invariant structurel — la seule suppression d'audio du dépôt
# --------------------------------------------------------------------------- #
# Verbes qui font DISPARAÎTRE un chemin existant. `rename`/`replace`/`move` en
# font partie : un fichier déplacé hors de `recordings/` est aussi perdu pour
# l'utilisateur qu'un fichier effacé — l'onglet Backup ne le voit plus, donc
# plus personne ne peut ni l'écouter ni le supprimer.
#
# Le détecteur discrimine par la FORME de l'appel, pas par le nom seul :
# `str.replace(a, b)` prend deux positionnels et `datetime.replace(tzinfo=…)`
# des mots-clés, quand `Path.replace(cible)` en prend exactement un. Sans cette
# discrimination, 22 des 28 appels remontés étaient du bruit (`slugify`,
# `_epoch_creation`, les prompts IA…) — et un test qui crie tout le temps finit
# désarmé.
_MODULE_VERBES = {
    "os": ("remove", "unlink", "rmdir", "rename", "replace", "removedirs", "truncate"),
    # `copyfile`/`copy2` ÉCRASENT leur destination : recopier par-dessus un
    # enregistrement le détruit aussi sûrement qu'un `unlink`.
    "shutil": ("rmtree", "move", "copyfile", "copy2", "copy"),
}
# Modules entièrement dédiés à l'audio : une suppression y est suspecte même si
# la fonction qui la porte ne nomme pas `RECORDINGS_DIR` (le chemin peut lui
# être passé en argument).
_MODULES_AUDIO = ("audio", "backup", "record")
# Verbes dont aucun usage dans `app/` n'est anodin : ils se justifient un par
# un dans `_EXCEPTIONS`, sans passer par l'heuristique d'audio-pertinence — la
# seule partie du détecteur qu'on puisse contourner en nommant bien ses
# variables (constat A4).
_TOUJOURS_A_JUSTIFIER = (
    "unlink", "rmdir", "truncate",
    "os.remove", "os.unlink", "os.rmdir", "os.rename", "os.replace",
    "os.removedirs", "os.truncate",
    "shutil.rmtree", "shutil.move", "shutil.copyfile", "shutil.copy2", "shutil.copy",
    "open(écriture)", "open(mode variable)",
)
_INDICES_AUDIO = (
    "RECORDINGS_DIR", "recordings_dir", "recordings",
    "audio_backup_path", "audio_segments", "audio_file", ".webm",
)


def _alias_des_imports(arbre: ast.AST) -> dict[str, str]:
    """{nom local -> nom canonique} pour les formes d'import qui masquaient le
    détecteur (revue adversariale du 2026-09-01, constat A3).

    Trois façons d'appeler `os.remove` sans jamais écrire « os.remove » :
    `import shutil as sh` (le module change de nom), `from os import remove`
    (le module disparaît), `from os import remove as rm` (les deux). La
    première version ne comparait qu'à `os`/`shutil` littéraux : les trois
    passaient. Un arbre jetable portant six suppressions d'audio écrites ainsi
    rendait `{}` — le détecteur attestait donc une couverture qu'il n'avait pas.
    """
    alias: dict[str, str] = {}
    for node in ast.walk(arbre):
        if isinstance(node, ast.Import):
            for nom in node.names:
                if nom.name in _MODULE_VERBES:
                    alias[nom.asname or nom.name] = nom.name
        elif isinstance(node, ast.ImportFrom):
            if node.module in _MODULE_VERBES:
                for nom in node.names:
                    if nom.name in _MODULE_VERBES[node.module]:
                        alias[nom.asname or nom.name] = f"{node.module}.{nom.name}"
    return alias


def _verbe_destructeur(node: ast.Call, alias: dict[str, str] | None = None) -> str | None:
    """Nom du verbe si cet appel détruit ou déplace un chemin, sinon None."""
    alias = alias or {}
    if isinstance(node.func, ast.Name):
        # `remove(chemin)` après `from os import remove` — le module a disparu
        # du site d'appel, seul l'import le dit.
        if node.func.id in alias:
            return alias[node.func.id]
        if node.func.id != "open":
            return None
        modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
        modes += [
            k.value.value
            for k in node.keywords
            if k.arg == "mode" and isinstance(k.value, ast.Constant)
        ]
        if not modes:
            # Mode calculé (`open(chemin, mode)`) : indécidable statiquement, donc
            # traité comme destructeur. Le détecteur doit pencher du côté du
            # faux positif — une exception se documente, un audio effacé, non.
            return "open(mode variable)"
        ecrit = any(
            isinstance(m, str) and any(c in m for c in "wa+") for m in modes
        )
        return "open(écriture)" if ecrit else None
    if not isinstance(node.func, ast.Attribute):
        return None

    attr = node.func.attr
    module = node.func.value.id if isinstance(node.func.value, ast.Name) else None
    nargs, nkw = len(node.args), len(node.keywords)

    if attr in ("unlink", "rmdir", "write_bytes", "write_text"):
        return attr
    if attr == "truncate" and nargs <= 1 and nkw == 0:
        return attr
    canonique = alias.get(module, module)
    for mod, verbes in _MODULE_VERBES.items():
        if canonique == mod and attr in verbes:
            return f"{mod}.{attr}"
    if attr in ("replace", "rename") and nargs == 1 and nkw == 0:
        return f"Path.{attr}"
    return None


def _fonctions(arbre: ast.AST) -> list[tuple[str, ast.AST]]:
    """(nom qualifié, nœud) de chaque fonction, imbriquées comprises."""
    trouvees: list[tuple[str, ast.AST]] = []

    def visite(node: ast.AST, prefixe: str) -> None:
        for enfant in ast.iter_child_nodes(node):
            if isinstance(enfant, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nom = f"{prefixe}{enfant.name}"
                trouvees.append((nom, enfant))
                visite(enfant, nom + ".")
            elif isinstance(enfant, ast.ClassDef):
                visite(enfant, f"{prefixe}{enfant.name}.")
            else:
                visite(enfant, prefixe)

    visite(arbre, "")
    return trouvees


def _appels_destructeurs_sur_audio(racine: Path) -> dict[tuple[str, str], list[str]]:
    """{(fichier, fonction): [verbes]} pour tout `app/**.py`."""
    trouves: dict[tuple[str, str], list[str]] = {}
    for chemin in sorted((racine / "app").rglob("*.py")):
        source = chemin.read_text(encoding="utf-8")
        arbre = ast.parse(source)
        fns = _fonctions(arbre)
        alias = _alias_des_imports(arbre)
        module_audio = any(m in chemin.name for m in _MODULES_AUDIO)
        for node in ast.walk(arbre):
            if not isinstance(node, ast.Call):
                continue
            verbe = _verbe_destructeur(node, alias)
            if not verbe:
                continue
            # La fonction la plus INTERNE qui contient la ligne : une closure
            # d'écriture (`_ecrire`) doit être nommée pour elle-même, pas
            # confondue avec la route qui la porte.
            candidates = [
                (nom, fn)
                for nom, fn in fns
                if fn.lineno <= node.lineno <= (fn.end_lineno or fn.lineno)
            ]
            if candidates:
                nom, fn = max(candidates, key=lambda c: c[1].lineno)
                bloc = ast.get_source_segment(source, fn) or ""
            else:
                nom, bloc = "<module>", source
            # Le filtre d'« audio-pertinence » est le point faible assumé de
            # tout détecteur de forme, et c'est le bypass que C8 nommait mot pour
            # mot : `def _oublier(chemin): chemin.unlink()`, appelé d'ailleurs
            # avec `RECORDINGS_DIR / job.filename`. La fonction ne nomme rien
            # d'audio, son fichier non plus. La réponse n'est pas d'élargir
            # encore le filtre — c'est de le SUPPRIMER pour les verbes qui ne
            # peuvent jamais être anodins sur un chemin. Un `unlink`, un
            # `rmtree`, un `shutil.move` dans `app/` se justifient un par un,
            # dans `_EXCEPTIONS`, qu'ils touchent de l'audio ou non : ils sont
            # assez rares (6 dans tout `app/` au 2026-09-01) pour que cette
            # exigence ne coûte rien, et c'est le seul filtre qu'on ne peut pas
            # contourner en choisissant bien ses noms de variables.
            if verbe not in _TOUJOURS_A_JUSTIFIER:
                if not module_audio and not any(i in bloc for i in _INDICES_AUDIO):
                    continue
            cle = (chemin.relative_to(racine).as_posix(), nom)
            trouves.setdefault(cle, []).append(f"L{node.lineno} {verbe}")
    return trouves


# Les SEULS endroits du dépôt autorisés à faire disparaître ou à écraser un
# fichier d'audio, avec la raison. Une liste explicite, et pas un « sauf dans
# interviews.py » : la permission par fichier laissait passer n'importe quel
# helper ajouté dans ce fichier, et ne disait pas POURQUOI l'exception existe.
_EXCEPTIONS = {
    ("app/routers/interviews.py", "delete_record_backup"): (
        "LA suppression d'audio du site : l'utilisateur clique dans l'onglet "
        "Backup de la mission"
    ),
    ("app/routers/missions.py", "delete_mission"): (
        "cascade d'une suppression de mission demandée par l'utilisateur. Sans "
        "elle le fichier reste sur le disque sans écran pour l'atteindre — et "
        "SQLite réattribuant l'identifiant libéré, la mission suivante "
        "hériterait de l'audio de la précédente"
    ),
    ("app/routers/missions.py", "delete_audio_orphelin"): (
        "écran « audio sans mission » : la seule façon d'atteindre un fichier "
        "dont la mission a déjà disparu"
    ),
    ("app/routers/interviews.py", "save_record_backup._ecrire"): (
        "CRÉATION, pas écrasement : le nom porte un horodatage ET un uuid "
        "(`{mission}_{ts}_{uuid8}.webm`), il ne peut pas viser un fichier "
        "existant"
    ),
    ("app/routers/interviews.py", "transcribe_file._ecrire"): (
        "CRÉATION sous un nom unique lui aussi (`{mission}_import_{ts}_{uuid}`)"
    ),
}


def test_aucune_disparition_d_audio_hors_des_gestes_de_l_utilisateur() -> None:
    """Aucun chemin d'audio n'est effacé, déplacé ni écrasé hors de la liste
    d'exceptions ci-dessus.

    Ce test remplace (2026-09-01, constat C8 de la revue de `c79e8b5`) une
    version à fenêtre de lignes qui se contournait de trois façons : elle ne
    cherchait que `unlink`/`rmtree`/`os.remove` — donc `shutil.move`,
    `os.replace`, `Path.rename` et une troncature passaient toutes ; elle
    exigeait le mot `RECORDINGS_DIR` dans les 12 lignes précédentes — donc un
    répertoire rangé dans un attribut ou reçu en argument la rendait aveugle ;
    et elle autorisait un FICHIER entier (`app/routers/interviews.py`) plutôt
    que des fonctions nommées. Elle affirmait pourtant, dans sa propre
    docstring, attraper « la réintroduction d'un helper, quel que soit son
    nom ».

    Ce qu'il ne prouve toujours pas, et qu'il ne faut pas lui faire dire : que
    l'audio n'est pas supprimé à l'exécution. Il prouve qu'aucune suppression
    n'a été ÉCRITE. Le comportement, lui, est tenu par les tests de la
    section 3 et par `tests/test_mission_backups.py`.
    """
    trouves = _appels_destructeurs_sur_audio(RACINE)

    inattendus = {k: v for k, v in trouves.items() if k not in _EXCEPTIONS}
    assert not inattendus, (
        "un chemin d'audio est effacé, déplacé ou écrasé hors des gestes de "
        "l'utilisateur — l'audio d'un entretien ne se supprime QUE par une "
        f"action sur le site : {inattendus}.\nSi ce chemin est légitime, "
        "ajoute-le à `_EXCEPTIONS` AVEC sa raison ; ne relâche pas le "
        "détecteur."
    )

    # Une exception qui ne correspond plus à rien est une permission ouverte
    # pour du code futur — elle doit tomber avec le code qui la justifiait.
    disparues = sorted(set(_EXCEPTIONS) - set(trouves))
    assert not disparues, (
        "ces exceptions ne couvrent plus aucun appel réel : retire-les de "
        f"`_EXCEPTIONS` plutôt que de les laisser couvrir un chemin à venir : {disparues}"
    )


def test_le_detecteur_voit_les_verbes_que_l_ancienne_version_ratait() -> None:
    """Un détecteur structurel qui ne se teste pas lui-même finit par ne plus
    rien détecter. Ces quatre appels passaient tous sous l'ancienne regex."""
    extraits = {
        "shutil.move": "shutil.move(chemin, ailleurs)",
        "os.replace": "os.replace(src, dst)",
        "Path.rename": "chemin.rename(autre)",
        "truncate": "poignee.truncate()",
        "unlink": "chemin.unlink()",
        "open(écriture)": "open(chemin, 'wb')",
    }
    for attendu, code in extraits.items():
        node = ast.parse(code, mode="eval").body
        assert isinstance(node, ast.Call)
        assert _verbe_destructeur(node) == attendu, (
            f"le détecteur ne voit pas `{code}` — un audio supprimé par ce "
            "verbe passerait sans bruit"
        )

    # Et il ne crie PAS sur les homonymes, sans quoi il serait désarmé.
    for code in ("texte.replace('a', 'b')", "moment.replace(tzinfo=utc)",
                 "elements.remove(x)", "open(chemin, 'rb')"):
        node = ast.parse(code, mode="eval").body
        assert _verbe_destructeur(node) is None, f"faux positif sur `{code}`"


# Six suppressions d'audio, écrites des six façons qui passaient sous le
# détecteur du matin même (revue adversariale du 2026-09-01, constats A3 et A4).
# Aucune n'écrit littéralement « os.remove » ni « RECORDINGS_DIR » à côté du
# verbe, et le fichier ne s'appelle pas « audio ».
_ARBRE_DE_CONTOURNEMENT = {
    "app/nettoyage.py": """
from os import remove
from os import unlink as jeter
import shutil as sh

RACINE = None


def _oublier(chemin):
    # Le bypass que C8 nommait mot pour mot : le chemin arrive en argument,
    # rien dans cette fonction ne dit qu'il s'agit d'audio.
    chemin.unlink(missing_ok=True)


def purger(chemin, autre, mode):
    remove(chemin)
    jeter(autre)
    sh.move(chemin, autre)
    sh.copyfile(autre, chemin)
    with open(chemin, mode) as f:
        f.write(b"")
""",
}


def test_le_detecteur_attrape_les_contournements_sur_un_arbre_jetable(tmp_path) -> None:
    """Un détecteur structurel ne se croit pas sur parole : on lui donne du code
    qui essaie de passer, et on compte.

    C'est la leçon que ce fichier a déjà payée deux fois. La version à fenêtre
    de lignes affirmait attraper « un helper, quel que soit son nom » et n'en
    attrapait aucun ; sa remplaçante en AST, écrite le même jour, ratait encore
    `from os import remove`, `import shutil as sh`, `open(chemin, mode)` avec un
    mode calculé, et le helper qui reçoit son chemin en argument. Les deux fois,
    l'auto-test ne jouait que les formes qui marchaient déjà — il attestait une
    couverture au lieu de la prouver.
    """
    for rel, source in _ARBRE_DE_CONTOURNEMENT.items():
        chemin = tmp_path / rel
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(source, encoding="utf-8")

    trouves = _appels_destructeurs_sur_audio(tmp_path)
    verbes = sorted(v for appels in trouves.values() for v in appels)

    assert trouves, (
        "le détecteur ne voit AUCUNE des six suppressions de cet arbre : il "
        "atteste une couverture qu'il n'a pas"
    )
    # Le helper anonyme — celui que le constat C8 nommait explicitement.
    assert ("app/nettoyage.py", "_oublier") in trouves, (
        "un helper qui reçoit son chemin en argument, dans un fichier au nom "
        f"neutre, reste invisible : {sorted(trouves)}"
    )
    for attendu in ("os.remove", "os.unlink", "shutil.move", "shutil.copyfile",
                    "unlink", "open(mode variable)"):
        assert any(attendu in v for v in verbes), (
            f"forme non détectée : {attendu} — trouvé {verbes}"
        )


def test_le_detecteur_ne_crie_pas_sur_un_arbre_sain(tmp_path) -> None:
    """Le pendant obligatoire : un détecteur qui remonte tout est désarmé aussi
    sûrement qu'un détecteur aveugle — on finit par élargir `_EXCEPTIONS`
    jusqu'à ce qu'elle ne dise plus rien."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "texte.py").write_text(
        "from datetime import timezone\n\n\n"
        "def normaliser(txt, moment, elements, x):\n"
        "    txt = txt.replace('a', 'b')\n"
        "    moment = moment.replace(tzinfo=timezone.utc)\n"
        "    elements.remove(x)\n"
        "    with open('rapport.txt', 'r') as f:\n"
        "        return f.read()\n",
        encoding="utf-8",
    )
    assert _appels_destructeurs_sur_audio(tmp_path) == {}, (
        "faux positifs sur du code sans le moindre chemin détruit"
    )


def test_le_module_des_jobs_audio_ne_supprime_plus_rien() -> None:
    """`audio_file_jobs` portait les trois suppressions automatiques. Il ne doit
    plus exposer de helper de suppression : un helper qui traîne finit par être
    rappelé, et c'est précisément ce qui s'était passé (`release_audio_file`
    était le « nom public » de `_remove_audio` pour un appelant externe)."""
    from app.services import audio_file_jobs

    for nom in ("_remove_audio", "release_audio_file"):
        assert not hasattr(audio_file_jobs, nom), (
            f"`audio_file_jobs.{nom}` est de retour : c'est un chemin de "
            "suppression automatique d'audio, contraire à la règle du projet"
        )


# --------------------------------------------------------------------------- #
# 2. Condition qui rend la règle tenable — le fichier est ATTEIGNABLE
# --------------------------------------------------------------------------- #
def test_le_fichier_importe_porte_le_prefixe_de_mission() -> None:
    """Sans ce préfixe, l'onglet Backup ne voit pas le fichier
    (`mission_backups.lister_backups` cherche par `glob("{mission.id}_*")`) :
    « ne jamais supprimer automatiquement » produirait alors un fichier
    fantôme, invisible ET indestructible. La règle et le nommage tiennent
    ensemble."""
    source = (RACINE / "app" / "routers" / "interviews.py").read_text(encoding="utf-8")
    # Fenêtre bornée par la route SUIVANTE, jamais par un nombre de caractères :
    # un `[:3000]` s'arrêtait avant la ligne de nommage dès qu'on ajoutait des
    # validations en tête de fonction, et l'échec accusait alors le code d'avoir
    # perdu le préfixe — qu'il portait toujours. La borne structurelle est aussi
    # plus STRICTE : elle ne peut plus se satisfaire d'une occurrence trouvée
    # dans la route d'à côté.
    debut = source.index('@router.post("/audio/transcribe-file")')
    fin = source.find("\n@router.", debut + 1)
    bloc = source[debut : fin if fin != -1 else len(source)]
    assert "mission_id" in bloc, (
        "la route d'import ne reçoit plus `mission_id` : le fichier ne peut "
        "plus porter le préfixe de mission, donc plus apparaître dans l'onglet "
        "Backup, donc plus être supprimé par l'utilisateur"
    )
    assert re.search(r'f"\{prefixe\}import_|f"\{mission_id\}_import_', bloc), (
        "le nom du fichier importé ne porte plus le préfixe de mission"
    )


def test_un_import_orphelin_est_visible_dans_l_onglet_backup(tmp_path) -> None:
    """Bout de la chaîne : un fichier importé que plus aucun entretien ne
    référence doit rester LISTÉ, sinon l'utilisateur ne peut pas le supprimer
    et la règle l'enferme dans un disque qui gonfle."""
    from app.services import mission_backups

    class _Mission:
        id = 42
        interviews: list = []

    (tmp_path / "42_import_1234_abcd.m4a").write_bytes(b"audio")
    (tmp_path / "99_import_9999_zzzz.m4a").write_bytes(b"autre mission")

    listing = mission_backups.lister_backups(_Mission(), tmp_path)

    noms = [e["filename"] for e in listing["orphelins"]]
    assert "42_import_1234_abcd.m4a" in noms, (
        "l'import orphelin de CETTE mission n'apparaît pas dans l'onglet "
        "Backup : l'utilisateur n'a aucun moyen de le supprimer"
    )
    assert "99_import_9999_zzzz.m4a" not in noms, (
        "l'onglet Backup d'une mission montre l'audio d'une autre mission"
    )


# --------------------------------------------------------------------------- #
# 2 bis. Le fichier importé est RATTACHÉ, donc rejouable
# --------------------------------------------------------------------------- #
def test_le_statut_d_import_expose_le_nom_du_fichier() -> None:
    """Le client en a besoin pour rattacher l'audio importé à l'entretien.
    Sans rattachement, le fichier survit mais reste orphelin : `_tranches_audio`
    ne le voit pas, donc « Relancer la transcription » ne peut pas rejouer
    depuis lui — le geste même que tout ce chantier existe pour rendre
    possible."""
    source = (RACINE / "app" / "routers" / "interviews.py").read_text(encoding="utf-8")
    bloc = source[source.index("def transcribe_file_status") :][:4000]
    assert '"filename"' in bloc, (
        "le statut d'import n'expose plus le nom du fichier : le client ne peut "
        "plus rattacher l'audio importé, qui redevient un orphelin injouable"
    )
    assert "job.filenames" in bloc, (
        "le nom doit être vide pour une RETRANSCRIPTION (`filenames`), dont les "
        "tranches sont déjà rattachées — sinon on les ré-attacherait en double"
    )


@pytest.mark.parametrize(
    "ecran,champ",
    [
        # Le mode libre porte des TRANCHES (`audio_segments`, rotation du
        # backupRecorder) ; le mode structuré n'a qu'un chemin unique
        # (`startBackupRecorder` : « l'arrêt du backup est toujours final »).
        # Les deux rattachent, mais PAS par le même champ : une copie du code
        # de l'un vers l'autre écrirait dans un champ inexistant.
        ("record_libre.html", "enregistrerAudioImporte"),
        ("record.html", "backupPathHidden.value = data.filename"),
    ],
)
def test_chaque_ecran_rattache_l_import_par_SON_mecanisme(ecran: str, champ: str) -> None:
    source = (RACINE / "app" / "templates" / "interviews" / ecran).read_text(
        encoding="utf-8"
    )
    assert champ in source, (
        f"{ecran} ne rattache plus le fichier importé à l'entretien : il "
        "survivra sur le disque en orphelin, sans rejeu possible"
    )
    assert "formData.append('mission_id'" in source, (
        f"{ecran} n'envoie plus `mission_id` à l'import : le fichier perdra le "
        "préfixe de mission, donc toute visibilité dans l'onglet Backup"
    )


def test_le_mode_structure_n_ecrit_pas_dans_un_champ_de_tranches_inexistant() -> None:
    """Garde anti-copier-coller : `record.html` n'a pas de champ
    `audio_segments` (grep : le `<input>` n'existe que dans le mode libre).
    Y écrire depuis le chemin d'import lèverait un TypeError sur `null` au
    moment précis où la transcription vient d'aboutir."""
    source = (RACINE / "app" / "templates" / "interviews" / "record.html").read_text(
        encoding="utf-8"
    )
    assert 'name="audio_segments"' not in source, (
        "record.html a gagné un champ `audio_segments` — ce test et le "
        "rattachement d'import doivent être revus ensemble"
    )
    assert "audioSegmentsHidden" not in source, (
        "record.html manipule `audioSegmentsHidden`, qui n'y existe pas : "
        "copier-coller depuis record_libre.html"
    )


# --------------------------------------------------------------------------- #
# 3. Invariant comportemental — le fichier survit
# --------------------------------------------------------------------------- #
def test_la_purge_efface_les_lignes_de_base_mais_pas_l_audio(tmp_path, monkeypatch) -> None:
    """La purge à 7 j garde sa raison d'être — les lignes portent du texte
    d'entretien — mais ne touche plus au fichier, qui appartient à
    l'utilisateur."""
    from app.db import SessionLocal
    from app.models import AudioFileJob
    from app.services import audio_file_jobs

    monkeypatch.setattr(audio_file_jobs, "RECORDINGS_DIR", tmp_path)
    fichier = tmp_path / "7_import_1_aaaa.m4a"
    fichier.write_bytes(b"audio")

    db = SessionLocal()
    try:
        vieux = AudioFileJob(
            session_token="purge-audio",
            filename=fichier.name,
            status="done",
            blocks=["du texte d'entretien"],
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(days=30),
        )
        db.add(vieux)
        db.commit()
        job_id = vieux.id

        audio_file_jobs.purge_stale_audio_file_jobs(db)

        assert db.get(AudioFileJob, job_id) is None, (
            "la purge ne fait plus son travail sur les lignes de base"
        )
        assert fichier.is_file(), (
            "la purge a supprimé l'audio de l'utilisateur — elle ne doit "
            "effacer que les lignes de base"
        )
    finally:
        db.close()
