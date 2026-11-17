"""Story 3.1, AC3.1.5 — scripts/serveur-dev.ps1 (findings D-6, D-8, D-9).

Functions only (SERVEUR_DEV_FONCTIONS_SEULES=1, dot-sourcing), fake port 8099,
fake history file: nothing is purged, nothing is launched.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
SCRIPT = RACINE / "scripts" / "serveur-dev.ps1"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None,
    reason="script PowerShell 5.1 propre au poste Windows",
)


def _ps(expression: str, env_sup: dict | None = None, timeout: int = 120) -> str:
    env = dict(os.environ, SERVEUR_DEV_FONCTIONS_SEULES="1", APP_AUTH_PASSWORD="mdp-de-test",
               SERVEUR_DEV_PYTHON=sys.executable)
    env.update(env_sup or {})
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         f". '{SCRIPT}' -Port 8099; {expression}"],
        cwd=RACINE, env=env, capture_output=True, timeout=timeout)
    sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
    assert res.returncode == 0, sortie
    return sortie.strip()


def test_descendants_bornes_ne_prennent_pas_le_worker_d_un_autre_serveur() -> None:
    """D-6: parent_pid=<racine>9 is ANOTHER server's worker, never a descendant."""
    racine = 2147480001  # never a live PID: only the spawn marker can link
    vrai = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                             "multiprocessing", f"parent_pid={racine}"])
    faux = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                             "multiprocessing", f"parent_pid={racine}9"])
    try:
        sortie = _ps(f"(@(Get-DescendantsProcessus -Racines @({racine})) | "
                     "ForEach-Object { [int]$_.ProcessId }) -join ','")
        ids = sortie.splitlines()[-1].split(",") if sortie else []
        assert str(vrai.pid) in ids, sortie
        assert str(faux.pid) not in ids, "le worker d'un AUTRE serveur serait purge"
    finally:
        for p in (vrai, faux):
            p.kill()
            p.wait(timeout=30)


def test_python_borne_hors_delai_marque_l_etat_indetermine() -> None:
    """D-8 (1/2): a timeout sets the « indéterminé » flag, an answer does not."""
    sortie = _ps("$script:pythonIndetermine = $false; "
                 "$null = Invoke-PythonBorne -Code 'import time; time.sleep(30)' -DelaiS 3; "
                 "$a = $script:pythonIndetermine; $script:pythonIndetermine = $false; "
                 "$null = Invoke-PythonBorne -Code 'print(1)' -DelaiS 60; "
                 "\"$a $($script:pythonIndetermine)\"")
    assert sortie.splitlines()[-1] == "True False", sortie


def test_preuve_python_indeterminee_ne_purge_pas_un_serveur_qui_repond() -> None:
    """D-8 (2/2): -KeepIfFresh on a responding, old server whose python freshness
    proof timed out decides « indetermine » (nothing purged), not « purger »."""
    faux = ("function Test-PortRepond { param([int]$NumPort) $true }; "
            "function Test-ContenuFrais { param([int]$NumPort) $true }; "
            "function Get-CodeRouteProtegee { param([int]$NumPort, [string]$Mdp) 200 }; "
            "function Get-AgeServeurS { param([int]$NumPort) 5000 }; "
            "function Get-AgeModifAppS { 9999 }; "
            "function Get-CpuServeurS { param([int]$NumPort) 1 }; "
            "function Get-OctetsHttp { param($Url, $Bearer) "
            "[System.Text.Encoding]::UTF8.GetBytes('{\"empreinte\":\"abc\"}') }; ")
    # Stand-in for a TIMEOUT of the real Invoke-PythonBorne (its flag is proven above).
    hors_delai = "function Invoke-PythonBorne { param($Code, $DelaiS) $script:pythonIndetermine = $true; $null }; "
    rend_autre = "function Invoke-PythonBorne { param($Code, $DelaiS) 'autre-empreinte' }; "
    assert _ps(faux + hors_delai + "Resolve-DecisionKeepIfFresh -NumPort 8099").splitlines()[-1] \
        == "indetermine"
    # Contrepart: a real « stale » answer still purges.
    assert _ps(faux + rend_autre + "Resolve-DecisionKeepIfFresh -NumPort 8099").splitlines()[-1] \
        == "purger"


def test_historique_horodate_et_fin_du_err(tmp_path) -> None:
    """D-9: launches/deaths are traced with a timestamp; a dead server's .err tail is readable."""
    hist = tmp_path / "hist.log"
    err = tmp_path / "u.log.err"
    err.write_text("\n".join(f"ligne {i}" for i in range(30)) + "\nSchemaEnRetard: migrer\n",
                   encoding="utf-8")
    sortie = _ps(f"Write-Historique 'mort port=8099 code=3'; Get-FinJournalErr -Chemin '{err}' -NbLignes 3",
                 {"SERVEUR_DEV_HISTORIQUE": str(hist)})
    assert "SchemaEnRetard: migrer" in sortie and "ligne 10" not in sortie, sortie
    ligne = hist.read_text(encoding="utf-8").strip()
    import re
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d pid=\d+ mort port=8099 code=3$", ligne), ligne


def test_un_serveur_mort_au_demarrage_n_est_pas_dit_lent() -> None:
    """D-9: the health-check has an ExitCode branch BEFORE the « démarré lentement » one."""
    texte = SCRIPT.read_text(encoding="utf-8-sig")
    i_mort = texte.index("if (-not $pret -and $proc.HasExited)")
    i_lent = texte.index("PEUT-ÊTRE simplement démarré lentement")
    assert i_mort < i_lent
    assert "$proc.ExitCode" in texte[i_mort:i_lent]


@pytest.mark.parametrize("contenu_env, attendu", [
    (None, "False"),                                   # no .env at all
    ("AUTRE=1\n", "False"),
    ("APP_AUTH_PASSWORD=\n", "False"),
    ("APP_AUTH_PASSWORD=  # vide\n", "True"),       # python-dotenv reads '# vide' (measured)
    ("APP_AUTH_PASSWORD=x # commentaire\n", "True"),
    ('APP_AUTH_PASSWORD=""\n', "False"),
    ("APP_AUTH_PASSWORD=s3cret\n", "True"),
    ("export APP_AUTH_PASSWORD='é à # x'\n", "True"),
    ("APP_AUTH_PASSWORD=s3cret\nAPP_AUTH_PASSWORD=\n", "False"),  # last line wins
])
def test_mot_de_passe_present_se_decide_sans_python(tmp_path, contenu_env, attendu) -> None:
    """Story 3.1: « absent » is decided from env/.env, never from a python probe."""
    if contenu_env is not None:
        (tmp_path / ".env").write_text(contenu_env, encoding="utf-8")
    sortie = _ps(f"Remove-Item Env:APP_AUTH_PASSWORD; Test-MotDePassePresent -Racine '{tmp_path}'")
    assert sortie.splitlines()[-1] == attendu, (contenu_env, sortie)


def test_variable_d_environnement_l_emporte_sur_le_env(tmp_path) -> None:
    """Same rule as python-dotenv: a SET variable is never overridden by .env.
    (An EMPTY variable cannot be tested here: on Windows .NET drops it from
    the environment it sees — measured — so the python child would not see
    it either; the function mirrors what that child would read.)"""
    (tmp_path / ".env").write_text("APP_AUTH_PASSWORD=\n", encoding="utf-8")
    assert _ps(f"Test-MotDePassePresent -Racine '{tmp_path}'",
               {"APP_AUTH_PASSWORD": "depuis-l-env"}).splitlines()[-1] == "True"


# --------------------------------------------------------------------------- #
# Story 3.1 review round
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("decision, attendu", [
    ("faute-de-frappe", "cision inconnue"),
    ("stabilite", "rien purg"),
])
def test_decision_non_purge_sort_en_0_sans_purger(decision, attendu) -> None:
    # « indetermine » is NOT in this list any more: it exits 6 (re-review M2).
    sortie = _ps(f"Write-DecisionEtSortir -Decision '{decision}' -NumPort 8099; 'A_CONTINUE_VERS_LA_PURGE'")
    assert attendu in sortie and "A_CONTINUE_VERS_LA_PURGE" not in sortie, sortie


def test_decision_indeterminee_sort_en_6_sur_stderr_sans_purger() -> None:
    """Re-review M2: « indetermine » exited 0, so the VS Code task showed
    success while the site could be dead. Distinct code 6, message on stderr."""
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         f". '{SCRIPT}' -Port 8099; Write-DecisionEtSortir -Decision 'indetermine' -NumPort 8099; "
         "'A_CONTINUE_VERS_LA_PURGE'"],
        cwd=RACINE, env=dict(os.environ, SERVEUR_DEV_FONCTIONS_SEULES="1", APP_AUTH_PASSWORD="x",
                             SERVEUR_DEV_PYTHON=sys.executable),
        capture_output=True, timeout=120)
    sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
    assert res.returncode == 6, sortie
    assert "la preuve de fra" in res.stderr.decode("utf-8", errors="replace"), sortie
    assert "A_CONTINUE_VERS_LA_PURGE" not in sortie, sortie


def test_decision_purger_continue_vers_la_purge() -> None:
    sortie = _ps("Write-DecisionEtSortir -Decision 'purger' -NumPort 8099; 'A_CONTINUE_VERS_LA_PURGE'")
    assert sortie.splitlines()[-1] == "A_CONTINUE_VERS_LA_PURGE", sortie


def test_preuve_python_hors_delai_affichee_indeterminee_pas_false() -> None:
    sortie = _ps("$script:pythonIndetermine = $true; Format-EtatPython -Frais $false; "
                 "$script:pythonIndetermine = $false; Format-EtatPython -Frais $false")
    lignes = sortie.splitlines()
    assert "python hors d" in lignes[-2] and lignes[-1] == "False", sortie


@pytest.mark.parametrize("contenu_env, attendu", [
    ('APP_AUTH_PASSWORD="s3cret" # commentaire\n', "True"),
    ('APP_AUTH_PASSWORD=""  # vide\n', "False"),
    ("APP_AUTH_PASSWORD=''  # vide\n", "False"),
])
def test_commentaire_apres_guillemet_fermant(tmp_path, contenu_env, attendu) -> None:
    (tmp_path / ".env").write_text(contenu_env, encoding="utf-8")
    sortie = _ps(f"Remove-Item Env:APP_AUTH_PASSWORD; Test-MotDePassePresent -Racine '{tmp_path}'")
    assert sortie.splitlines()[-1] == attendu, (contenu_env, sortie)


def test_env_verrouille_donne_inconnu_sans_exception(tmp_path) -> None:
    env_fichier = tmp_path / ".env"
    env_fichier.write_text("APP_AUTH_PASSWORD=s3cret\n", encoding="utf-8")
    sortie = _ps(f"Remove-Item Env:APP_AUTH_PASSWORD; "
                 f"$fs = [System.IO.File]::Open('{env_fichier}', 'Open', 'ReadWrite', 'None'); "
                 f"try {{ $r = Test-MotDePassePresent -Racine '{tmp_path}' }} finally {{ $fs.Close() }}; "
                 "if ($null -eq $r) { 'INCONNU' } else { \"$r\" }")
    assert sortie.splitlines()[-1] == "INCONNU", sortie


_FAUX_PYTHON = {
    # The script's interpreter, faked by a .bat (no real server, no app import).
    "mort": ('@echo off\r\nif "%~1"=="-c" goto sonde\r\n'
             'echo SchemaEnRetard: migrer avec APP_DB_MIGRATE=1 1>&2\r\nexit /b 3\r\n'
             ':sonde\r\necho bWRwLWRlLXRlc3Q=\r\nexit /b 0\r\n'),
    "echec": '@echo off\r\necho ImportError: No module named dotenv 1>&2\r\nexit /b 1\r\n',
    "lent": '@echo off\r\nping -n 40 127.0.0.1 >nul\r\nexit /b 0\r\n',
}


def _lancer_script(tmp_path: Path, faux: str, *args: str, env_sup: dict | None = None):
    bat = tmp_path / f"faux_python_{faux}.bat"
    bat.write_text(_FAUX_PYTHON[faux], encoding="ascii", newline="")
    env = dict(os.environ, APP_AUTH_PASSWORD="mdp-de-test", SERVEUR_DEV_PYTHON=str(bat),
               TEMP=str(tmp_path), TMP=str(tmp_path),
               SERVEUR_DEV_HISTORIQUE=str(tmp_path / "hist.log"), **(env_sup or {}))
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
         "-Port", "8099", "-AttenteDemarrageS", "20", *args],
        cwd=RACINE, env=env, capture_output=True, timeout=150)
    return res.returncode, (res.stdout + res.stderr).decode("utf-8", errors="replace")


def test_lecture_du_mot_de_passe_en_echec_n_est_pas_absent(tmp_path) -> None:
    code, sortie = _lancer_script(tmp_path, "echec")
    assert code == 5, sortie
    assert "CHEC : python a rendu le code 1" in sortie and "ImportError" in sortie and "absent (ni" not in sortie, sortie


def test_lecture_du_mot_de_passe_hors_delai_sort_en_4(tmp_path) -> None:
    code, sortie = _lancer_script(tmp_path, "lent", env_sup={"SERVEUR_DEV_DELAI_PYTHON_S": "2"})
    assert code == 4, sortie
    assert "de l'app IND" in sortie, sortie


def test_un_serveur_mort_au_demarrage_est_dit_mort_avec_son_err(tmp_path) -> None:
    code, sortie = _lancer_script(tmp_path, "mort")
    assert code == 1, sortie
    assert "MORT au d" in sortie and "SchemaEnRetard" in sortie, sortie
    assert "lentement" not in sortie, sortie
    assert " mort port=8099 " in (tmp_path / "hist.log").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Story 3.1 final review
# --------------------------------------------------------------------------- #
# A server that RESPONDS, is old (5000 s) and whose app/ is untouched (9999 s):
# only the freshness probes decide between « purger » and « indetermine ».
_VIEUX_QUI_REPOND = (
    "function Test-PortRepond { param([int]$NumPort) $true }; "
    "function Get-AgeServeurS { param([int]$NumPort) 5000 }; "
    "function Get-AgeModifAppS { 9999 }; "
    "function Get-CpuServeurS { param([int]$NumPort) 1 }; "
    "$script:motDePasse = 'x'; ")
_STATIQUE_FRAIS = "function Test-ContenuFrais { param([int]$NumPort) $true }; "
_ROUTE_200 = "function Get-CodeRouteProtegee { param([int]$NumPort, [string]$Mdp) 200 }; "
_FRAICHEUR_ABC = ("function Get-OctetsHttp { param($Url, $Bearer) "
                  "[System.Text.Encoding]::UTF8.GetBytes('{\"empreinte\":\"abc\"}') }; ")
# A REAL .NET request that gets no HTTP reply (port 1: connection refused),
# wrapped by PowerShell exactly like a timeout of Get-OctetsHttp.
_HTTP_SANS_REPONSE = ("function Get-OctetsHttp { param($Url, $Bearer) "
                      "$r = [System.Net.WebRequest]::Create('http://127.0.0.1:1/'); "
                      "$r.Timeout = 3000; $r.GetResponse() }; ")


def _decision(expression: str, env_sup: dict | None = None) -> str:
    return _ps(expression + "Resolve-DecisionKeepIfFresh -NumPort 8099", env_sup).splitlines()[-1]


def test_sonde_python_en_echec_ne_purge_pas(tmp_path) -> None:
    """F3: python RAN and FAILED (exit 1, nothing on stdout) through the REAL
    Invoke-PythonBorne: no fingerprint, so no verdict — not « périmé »."""
    bat = tmp_path / "faux_python_echec.bat"
    bat.write_text(_FAUX_PYTHON["echec"], encoding="ascii", newline="")
    assert _decision(_VIEUX_QUI_REPOND + _STATIQUE_FRAIS + _ROUTE_200 + _FRAICHEUR_ABC,
                     {"SERVEUR_DEV_PYTHON": str(bat)}) == "indetermine"


def test_echec_python_d_une_decision_precedente_est_oublie() -> None:
    """F3: pythonEchec is reset by each decision — a stale flag (password probe)
    must not turn a definitive « stale » answer into « indetermine »."""
    rend_autre = "function Invoke-PythonBorne { param($Code, $DelaiS) 'autre-empreinte' }; "
    assert _decision("$script:pythonEchec = $true; " + _VIEUX_QUI_REPOND + _STATIQUE_FRAIS
                     + _ROUTE_200 + _FRAICHEUR_ABC + rend_autre) == "purger"


def test_fraicheur_http_sans_reponse_ne_purge_pas() -> None:
    """F3: /__fraicheur without any HTTP reply (10 s timeout under load)."""
    rend_abc = "function Invoke-PythonBorne { param($Code, $DelaiS) 'abc' }; "
    assert _decision(_VIEUX_QUI_REPOND + _STATIQUE_FRAIS + _ROUTE_200 + _HTTP_SANS_REPONSE
                     + rend_abc) == "indetermine"


def test_statique_http_sans_reponse_ne_purge_pas() -> None:
    """F3: same for the static-bytes probe (real Test-ContenuFrais)."""
    assert _decision(_VIEUX_QUI_REPOND + _ROUTE_200 + _HTTP_SANS_REPONSE) == "indetermine"


def test_route_protegee_sans_reponse_ne_purge_pas_mais_un_401_si() -> None:
    """F3: the protected route without any reply (real Get-CodeRouteProtegee,
    nothing listens on 8099) is undetermined; a real 401 reply still purges."""
    rend_abc = "function Invoke-PythonBorne { param($Code, $DelaiS) 'abc' }; "
    base = _VIEUX_QUI_REPOND + _STATIQUE_FRAIS + _FRAICHEUR_ABC + rend_abc
    assert _decision(base) == "indetermine"
    assert _decision(base + "function Get-CodeRouteProtegee { param([int]$NumPort, [string]$Mdp) 401 }; ") \
        == "purger"


def test_plafond_actif_port_ferme_decouple_de_la_grace() -> None:
    """F4: grace 120 s must not lower the « active, port closed » ceiling to
    240 s; it is its own setting (1800 s)."""
    def d(worker: str) -> str:
        return _ps("Get-DecisionKeepIfFresh -Sain $false -AgeServeurS 5000 -AgeModifS 9999 "
                   f"-Repond $false -Grace 120 -Stabilite 120 -Actif $true -AgeWorkerS {worker}"
                   ).splitlines()[-1]
    assert d("300") == "demarrage"
    assert d("1799") == "demarrage"
    assert d("1800") == "purger"
    assert _ps('"$GraceDemarrageS $PlafondActifS"').splitlines()[-1] == "120 1800"


def test_plafond_actif_transmis_par_resolve() -> None:
    """F4: Resolve-DecisionKeepIfFresh passes -PlafondActifS (port closed, old
    root, CPU progressing, worker 300 s old)."""
    actif = ("function Test-PortRepond { param([int]$NumPort) $false }; "
             "function Get-AgeServeurS { param([int]$NumPort) 5000 }; "
             "function Get-AgeModifAppS { 9999 }; "
             "$script:cpu = 0; function Get-CpuServeurS { param([int]$NumPort) $script:cpu += 10; $script:cpu }; "
             "function Get-AgeWorkerS { param([int]$NumPort) 300 }; ")
    assert _ps(actif + "Resolve-DecisionKeepIfFresh -NumPort 8099").splitlines()[-1] == "demarrage"
    sortie = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         f". '{SCRIPT}' -Port 8099 -PlafondActifS 200; {actif}Resolve-DecisionKeepIfFresh -NumPort 8099"],
        cwd=RACINE, env=dict(os.environ, SERVEUR_DEV_FONCTIONS_SEULES="1", APP_AUTH_PASSWORD="x",
                             SERVEUR_DEV_PYTHON=sys.executable),
        capture_output=True, timeout=120)
    assert sortie.stdout.decode("utf-8", errors="replace").strip().splitlines()[-1] == "purger", sortie


def test_delai_python_non_numerique_ne_casse_pas_le_chargement() -> None:
    """F10: a non-numeric SERVEUR_DEV_DELAI_PYTHON_S falls back to 90 s."""
    assert _ps('"$script:DelaiPythonS"', {"SERVEUR_DEV_DELAI_PYTHON_S": "abc"}).splitlines()[-1] == "90"
    assert _ps('"$script:DelaiPythonS"', {"SERVEUR_DEV_DELAI_PYTHON_S": "7"}).splitlines()[-1] == "7"


# --------------------------------------------------------------------------- #
# Story 3.1 re-review (2026-11-17) — M1: the undetermined state is bounded
# --------------------------------------------------------------------------- #
def test_indetermine_borne_par_le_plafond_de_l_age_du_worker() -> None:
    """M1: undetermined + worker older than the ceiling -> purge; young or
    unknown worker -> nothing purged."""
    def d(age: str, plafond: str = "1800") -> str:
        return _ps(f"Get-DecisionIndeterminee -AgeWorkerS {age} -PlafondActif {plafond}").splitlines()[-1]
    assert d("300") == "indetermine"
    assert d("1799") == "indetermine"
    assert d("$null") == "indetermine"
    assert d("1800") == "purger"
    assert d("90000") == "purger"
    assert d("250", "200") == "purger"


def test_serveur_malade_qui_repond_encore_est_purge_apres_le_plafond() -> None:
    """M1 through Resolve-DecisionKeepIfFresh: `/` answers, the freshness
    probes get no HTTP reply (thread pool saturated). Old worker -> purge with
    a clear message; young worker -> still « indetermine »."""
    malade = _VIEUX_QUI_REPOND + _ROUTE_200 + _HTTP_SANS_REPONSE
    vieux = _ps(malade + "function Get-AgeWorkerS { param([int]$NumPort) 5000 }; "
                "Resolve-DecisionKeepIfFresh -NumPort 8099")
    assert vieux.splitlines()[-1] == "purger", vieux
    assert "probante et worker" in vieux and "(>= 1800 s) : purge" in vieux, vieux
    jeune = _ps(malade + "function Get-AgeWorkerS { param([int]$NumPort) 300 }; "
                "Resolve-DecisionKeepIfFresh -NumPort 8099")
    assert jeune.splitlines()[-1] == "indetermine", jeune


# --------------------------------------------------------------------------- #
# Story 3.1 re-review (2026-11-17) — A1/A2/A3: the REAL Get-AgeWorkerS, with
# stubbed process/port sources (no real server, no real port 8020)
# --------------------------------------------------------------------------- #
_MALADE = _VIEUX_QUI_REPOND + _ROUTE_200 + _HTTP_SANS_REPONSE


def _proc(pid: int, cmd: str, age_s: int) -> str:
    return (f"[pscustomobject]@{{ProcessId={pid}; CommandLine='{cmd}'; "
            f"CreationDate=(Get-Date).AddSeconds(-{age_s})}}")


def test_racine_introuvable_repli_sur_le_pid_a_l_ecoute_qui_purge() -> None:
    """A1 (i): the repo-root filter finds nothing (dead redirector, or null
    ExecutablePath seen from a non-elevated shell); the PID listening on the
    port is 6000 s old -> the ceiling applies -> purge."""
    stubs = ("function Get-ProcessusServeur { param([int]$NumPort) @() }; "
             "function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction) "
             "[pscustomobject]@{OwningProcess=4242} }; "
             "function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) "
             f"if ($Filter -eq 'ProcessId=4242') {{ {_proc(4242, 'python -m uvicorn app.main:app --port 8099', 6000)} }} }}; ")
    sortie = _ps(_MALADE + stubs + "Resolve-DecisionKeepIfFresh -NumPort 8099")
    assert sortie.splitlines()[-1] == "purger", sortie
    # A3: the message states what is known, not « indéterminé depuis plus de ».
    assert "probante et worker" in sortie and "de 6000 s (>= 1800 s) : purge" in sortie, sortie
    assert "depuis plus de" not in sortie, sortie


def test_repli_listener_mort_ne_rend_aucun_age() -> None:
    """(re-review 2026-11-17) Dead listener: nothing proves it was THIS repo's
    uvicorn, so a worker carrying its parent_pid= marker gives no age ($null ->
    « indetermine »), never a purgeable one."""
    stubs = ("function Get-ProcessusServeur { param([int]$NumPort) @() }; "
             "function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction) "
             "[pscustomobject]@{OwningProcess=4242} }; "
             "function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) "
             f"if (-not $Filter) {{ {_proc(4300, 'python -c from multiprocessing.spawn import spawn_main; parent_pid=4242', 7000)} }} }}; "
             "function Get-Process { param($Id, $ErrorAction) throw 'mort' }; ")
    sortie = _ps(_MALADE + stubs + "$null -eq (Get-AgeWorkerS -NumPort 8099)")
    assert sortie.splitlines()[-1] == "True", sortie


def test_age_illisible_reste_indetermine_et_le_dit() -> None:
    """A1 (ii): neither the repo filter nor the listener lookup yields an age:
    still « indetermine », but the message says the age is unreadable."""
    stubs = ("function Get-ProcessusServeur { param([int]$NumPort) @() }; "
             "function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction) }; ")
    sortie = _ps(_MALADE + stubs + "Resolve-DecisionKeepIfFresh -NumPort 8099")
    assert sortie.splitlines()[-1] == "indetermine", sortie
    assert "du serveur illisible" in sortie, sortie


def test_enfant_court_ne_rajeunit_pas_un_worker_malade() -> None:
    """A2 (iii): a sick uvicorn worker of 5000 s that still spawns a 10 s old
    child (ffmpeg / transcription spawn worker) reaches the ceiling -> purge."""
    procs = ", ".join([
        _proc(100, 'python -m uvicorn app.main:app --reload --port 8099', 9000),
        _proc(101, 'python -c from multiprocessing.spawn import spawn_main; parent_pid=100', 5000),
        _proc(102, 'python -c from multiprocessing.spawn import spawn_main; parent_pid=101', 10),
        _proc(103, 'ffmpeg -i x.webm', 10),
    ])
    stubs = f"function Get-ProcessusServeur {{ param([int]$NumPort) @({procs}) }}; "
    sortie = _ps(_MALADE + stubs + "Resolve-DecisionKeepIfFresh -NumPort 8099")
    assert sortie.splitlines()[-1] == "purger", sortie
    assert "de 5000 s (>= 1800 s) : purge" in sortie, sortie
    # Without --reload (no uvicorn worker): the uvicorn process itself.
    seul = f"function Get-ProcessusServeur {{ param([int]$NumPort) @({_proc(100, 'python -m uvicorn app.main:app --port 8099', 4000)}, {_proc(105, 'ffmpeg', 5)}) }}; "
    assert _ps(seul + "[int](Get-AgeWorkerS -NumPort 8099)").splitlines()[-1] == "4000"


# --------------------------------------------------------------------------- #
# Story 3.1 re-review (2026-11-17), last round — listener scope, no-reload
# spawn children, WMI failure
# --------------------------------------------------------------------------- #
def _proc_exe(pid: int, cmd: str, age_s: int, exe: str, parent: int = 0) -> str:
    return (f"[pscustomobject]@{{ProcessId={pid}; CommandLine='{cmd}'; ExecutablePath='{exe}'; "
            f"ParentProcessId={parent}; CreationDate=(Get-Date).AddSeconds(-{age_s})}}")


def _listener(*procs_par_filtre: tuple[str, str], pids: tuple[int, ...] = (4242,)) -> str:
    connexions = ", ".join(f"[pscustomobject]@{{OwningProcess={p}}}" for p in pids)
    branches = " ".join(f"if ($Filter -eq '{f}') {{ $script:lectures++; {proc} }}"
                        for f, proc in procs_par_filtre)
    return ("$script:lectures = 0; "
            "function Get-ProcessusServeur { param([int]$NumPort) @() }; "
            "function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction) "
            f"@({connexions}) }}; "
            "function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) "
            f"{branches} }}; ")


@pytest.mark.parametrize("cmd, exe", [
    ("python server.py", r"C:\Python314\python.exe"),
    ("python -m uvicorn app.main:app --port 8099", r"C:\AutreRepo\.venv\Scripts\python.exe"),
])
def test_listener_tiers_ne_devient_jamais_purgeable(cmd, exe) -> None:
    """A third-party python listener 7000 s old (another app, or a sibling
    repo's uvicorn app.main) gives no age -> « indetermine », age unreadable."""
    stubs = _listener(("ProcessId=4242", _proc_exe(4242, cmd, 7000, exe)))
    assert _ps(stubs + "$null -eq (Get-AgeServeurParListener -NumPort 8099)").splitlines()[-1] == "True"
    sortie = _ps(_MALADE + stubs + "Resolve-DecisionKeepIfFresh -NumPort 8099")
    assert sortie.splitlines()[-1] == "indetermine", sortie
    assert "du serveur illisible" in sortie, sortie


def test_listener_ipv4_ipv6_meme_pid_lu_une_fois() -> None:
    """Two listening sockets (IPv4 + IPv6) of the same PID: one listener."""
    exe = str(RACINE / ".venv" / "Scripts" / "python.exe")
    stubs = _listener(("ProcessId=4242", _proc_exe(4242, "python -m uvicorn app.main:app --port 8099", 6000, exe)),
                      pids=(4242, 4242))
    sortie = _ps(stubs + "$a = [int](Get-AgeServeurParListener -NumPort 8099); \"$a $script:lectures\"")
    assert sortie.splitlines()[-1] == "6000 1", sortie


def test_listener_uvicorn_du_repo_rend_son_age() -> None:
    """This repo's uvicorn: executable under the repo .venv, or base python
    whose parent (the .venv redirector) is under the repo -> its age."""
    venv = str(RACINE / ".venv" / "Scripts" / "python.exe")
    cmd = "python -m uvicorn app.main:app --port 8099"
    direct = _listener(("ProcessId=4242", _proc_exe(4242, cmd, 6000, venv)))
    assert _ps(direct + "[int](Get-AgeServeurParListener -NumPort 8099)").splitlines()[-1] == "6000"
    base = _listener(("ProcessId=4242", _proc_exe(4242, cmd, 6000, r"C:\Python314\python.exe", 4100)),
                     ("ProcessId=4100", _proc_exe(4100, cmd, 6001, venv)))
    assert _ps(base + "[int](Get-AgeServeurParListener -NumPort 8099)").splitlines()[-1] == "6000"


def test_sans_reload_un_enfant_spawn_n_est_pas_un_worker() -> None:
    """Without --reload, a transcription spawn child carries parent_pid=<uvicorn>:
    it is NOT a uvicorn worker, the uvicorn process (5000 s) decides."""
    procs = ", ".join([
        _proc(100, 'python -m uvicorn app.main:app --port 8099', 5000),
        _proc(101, 'python -c from multiprocessing.spawn import spawn_main; parent_pid=100', 10),
    ])
    stubs = f"function Get-ProcessusServeur {{ param([int]$NumPort) @({procs}) }}; "
    assert _ps(stubs + "[int](Get-AgeWorkerS -NumPort 8099)").splitlines()[-1] == "5000"


def test_panne_wmi_workers_de_parent_vide_sans_avorter() -> None:
    """A WMI failure under $ErrorActionPreference=Stop yields no worker, not an abort."""
    stubs = "function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) throw 'WMI en panne' }; "
    sortie = _ps(stubs + "\"n=$(@(Get-WorkersDeParent -PidParent 4242).Count)\"")
    assert sortie.splitlines()[-1] == "n=0", sortie
