"""Les `onclick="return confirm('…')"` de la carte de statut doivent être du JS VALIDE.

Une apostrophe droite dans le texte (« L'aperçu ») ferme la chaîne JS : SyntaxError,
le gestionnaire ne s'exécute jamais et le formulaire part SANS confirmation
(revue adversariale du 2026-10-19)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

GABARITS = [
    Path(__file__).resolve().parent.parent / "app" / "templates" / "interviews" / nom
    for nom in ("_structuration_statut.html", "libre_detail.html")
]


def _onclicks(src: str) -> list[str]:
    # contenu des attributs onclick="..." ; les balises Jinja {% %} {{ }} sont retirées
    sortie = []
    for m in re.finditer(r'onclick="([^"]*)"', src):
        code = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", "", m.group(1), flags=re.S)
        if "confirm(" in code:
            sortie.append(code)
    return sortie


@pytest.mark.parametrize("gabarit", GABARITS, ids=lambda p: p.name)
def test_les_confirm_inline_sont_du_js_valide(gabarit):
    if shutil.which("node") is None:
        pytest.fail("node est requis pour valider le JS inline.")
    src = gabarit.read_text(encoding="utf-8")
    for code in _onclicks(src):
        res = subprocess.run(
            ["node", "-e", "new Function(process.argv[1]);", code],
            capture_output=True, timeout=30,
        )
        assert res.returncode == 0, (code, res.stderr.decode(errors="replace")[:300])


def test_le_gabarit_de_statut_porte_bien_des_confirm():
    # garde-fou : le test ci-dessus ne doit pas passer à vide
    assert _onclicks(GABARITS[0].read_text(encoding="utf-8"))
