"""Les actions GitHub de la CI sont epinglees sur un SHA de commit, pas un tag.

Un tag (`@v4`) est MUTABLE : qui controle le depot de l'action peut le
re-pointer sur un autre commit, qui s'executera dans la CI de ce projet avec le
GITHUB_TOKEN du workflow. L'epinglage par SHA est la contrepartie, cote chaine
CI, de l'epinglage `==` deja applique aux dependances Python (finding
audit-technique securite, 2026-09-13).
"""

from __future__ import annotations

import re
from pathlib import Path

CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"

_USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.M)
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _references() -> list[str]:
    return _USES.findall(CI.read_text(encoding="utf-8"))


def test_la_ci_utilise_bien_des_actions():
    # Garde-fou du garde-fou : une CI sans `uses:` rendrait le test vert par
    # construction.
    assert len(_references()) >= 2, _references()


def test_chaque_action_est_epinglee_sur_un_sha_de_commit():
    flottantes = []
    for reference in _references():
        _, _, version = reference.partition("@")
        if not _SHA.match(version):
            flottantes.append(reference)
    assert flottantes == [], flottantes


def test_le_tag_lisible_reste_en_commentaire():
    # Un SHA nu est illisible : le tag d'origine doit rester a cote pour qu'une
    # montee de version reste possible sans archeologie.
    texte = CI.read_text(encoding="utf-8")
    for ligne in texte.splitlines():
        if re.match(r"^\s*-?\s*uses:", ligne):
            assert re.search(r"#\s*v\d", ligne), ligne
