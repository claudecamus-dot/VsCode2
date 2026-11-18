"""P5 (Story 3.7, A-12) : le brouillon local par onglet, rejoue dans un VRAI navigateur.

Meme harnais que `test_e2e_stories_3.py` (uvicorn reel + Edge/Chrome headless en CDP,
`confirm()` accepte par le pilote). Aucun micro : le brouillon est pose dans
`localStorage` puis repris par un vrai clic sur « Reprendre ».

Parcours : brouillon d'AVANT la story (cle sans onglet) -> clic « Reprendre » -> une seule
cle, propre a cet onglet ; brouillon d'un AUTRE onglet (cle etrangere, rien chez nous) ->
bandeau de reprise -> clic -> la copie etrangere est adoptee puis retiree.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from e2e_serveur import serveur_uvicorn
from navigateur_cdp import Navigateur
from test_e2e_stories_3 import (  # noqa: F401  (fixtures reutilisees)
    _SEED_MISSION, FauxOllama, _attendre, _python_sur_base, dossier, faux_ollama, nav,
)
import test_e2e_stories_3 as _s3

pytestmark = _s3.pytestmark


def _cles(nav: Navigateur, mid: str) -> list[str]:
    return sorted(json.loads(nav.evaluer(
        "JSON.stringify(Object.keys(localStorage).filter(function (k) { return k.indexOf("
        f"'i2d:brouillon-libre:{mid}') === 0; }}))")))


def test_c37_brouillon_par_onglet_dans_un_vrai_navigateur(
    dossier: Path, faux_ollama: FauxOllama, nav: Navigateur,
) -> None:
    mid = _python_sur_base(dossier, _SEED_MISSION)
    with serveur_uvicorn(dossier, AI_PROVIDER="ollama", OLLAMA_HOST=faux_ollama.hote,
                         OLLAMA_TIMEOUT="30") as base:
        ecran = f"{base}/missions/{mid}/interviews/record-libre"
        nav.naviguer(ecran)

        def poser(cle: str, texte: str, delta_ms: int = 0) -> None:
            brouillon = {"transcript": texte, "sessionToken": "tok-" + texte[:3],
                         "savedAt": int(time.time() * 1000) + delta_ms}
            nav.evaluer(f"localStorage.setItem({json.dumps(cle)}, {json.dumps(json.dumps(brouillon))}); true")

        def reprendre() -> None:
            nav.naviguer(ecran)
            _attendre(nav, lambda: nav.evaluer(
                "!document.getElementById('rec-draft-banner').hidden"), 15, "bannière de reprise")
            nav.cliquer("#rec-draft-restore")

        # 1. cle d'avant la story : reprise -> une seule cle, propre a l'onglet
        poser(f"i2d:brouillon-libre:{mid}", "Ancien brouillon sans onglet")
        reprendre()
        cles = _cles(nav, mid)
        assert len(cles) == 1 and cles[0] != f"i2d:brouillon-libre:{mid}", cles
        assert "Ancien brouillon" in nav.evaluer(f"localStorage.getItem({json.dumps(cles[0])})")
        propre = cles[0]

        # 2. brouillon d'un AUTRE onglet : cle etrangere, plus rien chez nous
        nav.evaluer(f"""Object.keys(localStorage).filter(function (k) {{ return k.indexOf('i2d:brouillon-libre:{mid}') === 0; }}).forEach(function (k) {{ localStorage.removeItem(k); }}); true""")
        etrangere = f"i2d:brouillon-libre:{mid}:autre-onglet"
        poser(etrangere, "Brouillon de l'autre onglet")
        nav.naviguer(ecran)
        _attendre(nav, lambda: nav.evaluer(
            "!document.getElementById('rec-draft-banner').hidden"), 15, "bannière de reprise")
        assert "autre onglet" in nav.evaluer("document.getElementById('rec-draft-text').textContent")
        nav.capturer(Path("C:/tmp/w37/render/bandeau_reprise.png"), page_entiere=False)
        # identite saisie : suit le brouillon (poste dans la copie etrangere, reprise au clic)
        nav.evaluer(f"""(function () {{ var k = {json.dumps(etrangere)};
          var b = JSON.parse(localStorage.getItem(k)); b.identite = {{interviewee_name: 'Alice E2E'}};
          localStorage.setItem(k, JSON.stringify(b)); return true; }})()""")
        nav.naviguer(ecran)   # le bandeau lit le brouillon au chargement
        _attendre(nav, lambda: nav.evaluer(
            "!document.getElementById('rec-draft-banner').hidden"), 15, "bannière (identité)")
        nav.cliquer("#rec-draft-restore")
        assert nav.evaluer("document.querySelector('#record-form input[name=interviewee_name]').value") == "Alice E2E"
        nav.evaluer(f"""Object.keys(localStorage).filter(function (k) {{ return k.indexOf('i2d:brouillon-libre:{mid}') === 0; }}).forEach(function (k) {{ localStorage.removeItem(k); }}); true""")
        poser(etrangere, "Brouillon de l'autre onglet")
        reprendre()
        cles = _cles(nav, mid)
        # (la navigation a changé l'identifiant d'onglet : M3, battement encore frais)
        assert len(cles) == 1 and etrangere not in cles, f"copie etrangere non adoptee/retiree : {cles}"
        assert "autre onglet" in nav.evaluer(f"localStorage.getItem({json.dumps(cles[0])})")
        assert nav.evaluer("document.getElementById('rec-transcript').value").startswith("Brouillon de l'autre")
        nav.drainer()
        assert not nav.erreurs_http(), nav.erreurs_http()
