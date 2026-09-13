"""Durcissement du workflow CI — finding audit-technique sécurité du 2026-09-13.

Le workflow se déclenche sur `pull_request`, donc sur du code proposé de
l'extérieur, et ne déclarait AUCUN bloc `permissions:` : le `GITHUB_TOKEN` y
gardait la portée par défaut du dépôt, potentiellement en écriture, pour un job
qui ne fait que lire (checkout, install, lint, tests).

Ce test existe parce que rien, dans le dépôt, ne couvrait le fichier de CI : le
bloc pouvait disparaître au premier remaniement sans que quoi que ce soit le
dise. Il vire au rouge sur la version d'avant (aucun `permissions`).

PAS de PyYAML ici, et c'est délibéré : la première version de ce fichier faisait
`pytest.importorskip("yaml")`, or PyYAML n'est dans AUCUN des requirements du
projet. Le test aurait donc été SKIPPÉ en CI — un garde-fou vert par
construction, exactement là où il compte. La lecture ci-dessous est textuelle et
bornée à ce dont le test a besoin : un bloc de premier niveau dans un fichier
court et écrit à la main.

Ce que ce test NE prétend PAS couvrir : l'épinglage des actions sur des SHA de
commit (`actions/checkout@v4` est un tag MUTABLE). C'est le second volet du
finding et il reste OUVERT — épingler demande de relever les SHA réels en amont
chez GitHub, et un SHA écrit de mémoire serait pire que le tag qu'il remplace.
"""
from __future__ import annotations

import pathlib

WORKFLOW = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _lignes() -> list[str]:
    return WORKFLOW.read_text(encoding="utf-8").splitlines()


def _bloc_de_premier_niveau(nom: str) -> list[str] | None:
    """Lignes indentées qui suivent `<nom>:` en colonne 0, ou None si absent."""
    lignes = _lignes()
    for i, ligne in enumerate(lignes):
        if ligne.rstrip() != f"{nom}:":
            continue
        bloc = []
        for suivante in lignes[i + 1:]:
            if not suivante.strip() or suivante.lstrip().startswith("#"):
                continue
            if not suivante[:1].isspace():
                break
            bloc.append(suivante.strip())
        return bloc
    return None


def test_le_workflow_declare_un_jeton_en_lecture_seule():
    texte = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in texte, (
        "si ce workflow ne se declenche plus sur pull_request, revoir ce test "
        "AVANT de le supprimer : c'est ce declencheur qui rend la portee du "
        "jeton critique"
    )
    bloc = _bloc_de_premier_niveau("permissions")
    assert bloc is not None, (
        "aucun bloc `permissions:` de premier niveau : le GITHUB_TOKEN herite "
        "de la portee par defaut du depot sur un workflow declenche par "
        "pull_request"
    )
    assert bloc == ["contents: read"], (
        f"portee elargie sans raison ecrite : {bloc}"
    )


def test_aucune_action_de_publication_sous_un_jeton_en_lecture():
    """Garde-fou du garde-fou : `contents: read` n'est tenable que tant qu'aucune
    étape n'a besoin d'écrire. Si une publication arrive un jour, ce test le dit
    au lieu de laisser quelqu'un élargir `permissions` en silence."""
    utilisees = [
        ligne.split("uses:", 1)[1].strip().split("@")[0]
        for ligne in _lignes()
        if ligne.strip().startswith("- uses:") or ligne.strip().startswith("uses:")
    ]
    assert utilisees, "aucune action lue : le parseur de ce test ne voit plus rien"
    interdites = {
        "actions/upload-release-asset", "peter-evans/create-pull-request",
        "actions/create-release", "softprops/action-gh-release",
    }
    assert not (set(utilisees) & interdites), (
        f"action de publication ajoutee sous `contents: read` : {utilisees}"
    )
