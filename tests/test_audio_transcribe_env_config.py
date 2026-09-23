"""Les 5 paramètres d'environnement de `audio_transcribe.py`
(WHISPER_BEAM_SIZE, WHISPER_FILE_BLOCK_S, WHISPER_PARALLEL_THRESHOLD_S,
WHISPER_MAX_WORKERS, WHISPER_CPU_THREADS) étaient lus par un `int(os.environ...)`
exécuté au NIVEAU MODULE, à l'import. Une valeur non entière (faute de frappe
dans le déploiement) ou absente sans défaut valide plantait alors l'import du
module ENTIER avec un `ValueError` cryptique au démarrage du serveur, au lieu
d'un repli explicite sur le défaut documenté — même défaut que celui déjà
corrigé pour `app.uploads._entier_env` / `app.services.pptx_export.images._entier_env`
(finding audit-technique robustesse, confirmé salle conseil-flotte 2026-09-16).

Un `import audio_transcribe` classique dans le process de test ne suffit pas à
observer un import qui plante : le module est déjà importé (et mis en cache
par d'autres tests / par conftest) avant qu'on ait pu positionner la variable
d'environnement invalide. Le test relance donc un VRAI import, dans un
sous-process Python isolé, avec la variable d'environnement invalide déjà
posée avant le premier import — comme un déploiement réel démarrerait.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

_VARS_MODULE_LEVEL = [
    "WHISPER_BEAM_SIZE",
    "WHISPER_FILE_BLOCK_S",
    "WHISPER_PARALLEL_THRESHOLD_S",
    "WHISPER_MAX_WORKERS",
    "WHISPER_CPU_THREADS",
]


def _import_dans_sous_process(env_var: str, valeur: str) -> subprocess.CompletedProcess:
    code = "import app.services.audio_transcribe"
    import os

    env = dict(os.environ)
    env[env_var] = valeur
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
    )


@pytest.mark.parametrize("env_var", _VARS_MODULE_LEVEL)
def test_valeur_non_entiere_n_empeche_pas_l_import(env_var: str) -> None:
    """Une valeur non entière dans l'environnement (faute de frappe de
    déploiement) ne doit plus faire planter l'import du module : repli sur le
    défaut documenté, pas de `ValueError` au démarrage du serveur."""
    resultat = _import_dans_sous_process(env_var, "pas-un-entier")
    assert resultat.returncode == 0, (
        f"import de audio_transcribe a plante avec {env_var}=pas-un-entier :\n"
        f"{resultat.stderr}"
    )


@pytest.mark.parametrize("env_var", _VARS_MODULE_LEVEL)
def test_valeur_vide_n_empeche_pas_l_import(env_var: str) -> None:
    resultat = _import_dans_sous_process(env_var, "")
    assert resultat.returncode == 0, (
        f"import de audio_transcribe a plante avec {env_var}='' :\n{resultat.stderr}"
    )
