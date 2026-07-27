"""Garde-fous sur le lanceur `scripts/serveur-dev.ps1` (2026-07-27).

Le 2026-07-27, le port 8040 est resté « hanté » alors que le script venait de
tourner : un serveur lancé avec le python SYSTÈME (hors venv du repo) avait
laissé un worker `multiprocessing.spawn` orphelin. La purge ne le voyait pas —
elle est scopée aux exécutables sous la racine du repo — et le kill du listener
ne le voyait pas non plus, puisque Windows attribue le socket au PID du PARENT,
mort. Le script sait désormais remonter d'un PID propriétaire mort à ses
workers via le marqueur `parent_pid=<pid>` que `spawn_main` écrit dans leur
ligne de commande.

Portée de ces tests : ils vérifient le CONTRAT du script (la logique est
présente ET branchée dans la purge), pas son exécution — un `.ps1` ne se
dot-source pas sans déclencher la purge elle-même. La détection a été vérifiée
en réel le 2026-07-27 en fabriquant un vrai orphelin (parent tué, enfant
survivant sous `C:\\Python314\\python.exe`, retrouvé par la fonction). Ce qui
casserait sans bruit et que ces tests attrapent : re-scoper la recherche au
venv, ou définir la fonction sans l'appeler.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "serveur-dev.ps1"


@pytest.fixture(scope="module")
def source() -> str:
    assert SCRIPT.exists(), f"lanceur introuvable : {SCRIPT}"
    return SCRIPT.read_text(encoding="utf-8-sig")


def test_le_script_reste_encode_avec_bom(source: str) -> None:
    """Un `.ps1` accentué sans BOM casse le parseur PowerShell 5.1 — le script
    ne démarre alors plus du tout (piège déjà payé le 2026-07-22)."""
    assert SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf")


def test_la_recherche_de_workers_est_independante_de_l_interpreteur(source: str) -> None:
    """Le worker fantôme du 2026-07-27 tournait sous le python système. Re-scoper
    cette recherche (au venv, à `Name='python.exe'`…) la rendrait aveugle au cas
    qu'elle existe précisément pour couvrir."""
    debut = source.index("function Get-WorkersDeParent")
    corps = source[debut:source.index("function", debut + 10)]
    assert "parent_pid=$PidParent" in corps, "le lien parent->worker doit venir de parent_pid="
    assert "Name='python.exe'" not in corps
    assert ".venv" not in corps and "$racine" not in corps


def test_la_purge_appelle_la_recherche_d_orphelins(source: str) -> None:
    """Défaut le plus probable en cas de retouche : la fonction reste définie
    mais plus personne ne l'appelle — le port redevient hanté en silence."""
    ligne = next(l for l in source.splitlines() if l.startswith("$aTuer ="))
    assert "Get-WorkersOrphelinsDuPort" in ligne
    assert "Get-ProcessusServeur" in ligne, "la purge scopée au repo reste nécessaire"


def test_un_listener_non_python_n_est_jamais_tue(source: str) -> None:
    """Garde-fou préexistant (revue 2026-07-22) : l'élargissement ne doit pas
    l'avoir dilué — on ne tue pas l'appli tierce qui occupe le port."""
    assert 'ProcessName -ne "python"' in source
    assert "non tué" in source
