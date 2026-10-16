"""scripts/serveur-dev.ps1 ne déclare plus « OK » un site qui rend 503.

Diagnostic superviseur du 2026-09-29 : le contrôle de santé n'exerçait que les
routes publiques (/static/, et un code HTTP quelconque sur /). Un serveur sans
APP_AUTH_PASSWORD était déclaré sain pendant que l'utilisateur voyait KO au
clic sur Connexion, trois fois. Ces tests figent les deux gardes qui se jouent
SANS lancer de serveur : refus sans mot de passe, et -CheckOnly qui ne lance
rien. Port 8099 : jamais celui de l'utilisateur (8020).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
SCRIPT = RACINE / "scripts" / "serveur-dev.ps1"
PORT_LIBRE = "8099"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None,
    reason="script PowerShell 5.1 propre au poste Windows",
)


def _lancer(*args: str, mot_de_passe: str) -> subprocess.CompletedProcess:
    # Variable POSÉE (même vide) : python-dotenv n'écrase jamais une variable
    # existante, donc le .env du poste ne peut pas fausser le test.
    env = dict(os.environ, APP_AUTH_PASSWORD=mot_de_passe, SERVEUR_DEV_PYTHON=sys.executable)
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(SCRIPT), "-Port", PORT_LIBRE, *args],
        cwd=RACINE, env=env, capture_output=True, timeout=180,
    )


def test_sans_mot_de_passe_le_script_refuse_avant_toute_purge() -> None:
    res = _lancer(mot_de_passe="")
    sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
    assert res.returncode == 2, sortie
    assert "APP_AUTH_PASSWORD" in sortie
    assert "Purge" not in sortie  # aucun serveur tué pour rien


def test_check_only_ne_lance_rien_et_dit_ko_sur_un_port_muet() -> None:
    res = _lancer("-CheckOnly", mot_de_passe="mdp-de-test")
    sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
    assert res.returncode == 1, sortie
    assert "uvicorn lancé" not in sortie
    assert "Purge" not in sortie


def test_stop_purge_le_worker_muet_d_un_reloader_mort_lu_dans_le_journal(tmp_path) -> None:
    """2026-09-29 : reloader mort pendant un --reload, worker respawné vivant
    mais MUET (aucun socket) qui verrouillait le journal — Chrome en
    ERR_CONNECTION_REFUSED et relance impossible. Le PID du reloader n'est lu
    que dans le journal .err du lancement précédent."""
    mort = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    pid_mort = int(mort.stdout.strip())
    (tmp_path / f"uvicorn_dev_{PORT_LIBRE}.log.err").write_text(
        f"INFO:     Started reloader process [{pid_mort}] using WatchFiles\n", encoding="utf-8")
    # Même forme de ligne de commande qu'un worker multiprocessing.spawn.
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                               "multiprocessing", f"parent_pid={pid_mort}"])
    # Faux ami : parent_pid=<pid_mort>9 est le worker d'un AUTRE serveur — il doit
    # survivre (le motif avait un joker final, revue 2026-09-29).
    voisin = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                               "multiprocessing", f"parent_pid={pid_mort}9"])
    try:
        env = dict(os.environ, APP_AUTH_PASSWORD="mdp-de-test", TEMP=str(tmp_path), TMP=str(tmp_path))
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(SCRIPT), "-Port", PORT_LIBRE, "-StopOnly"],
            cwd=RACINE, env=env, capture_output=True, timeout=180)
        sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
        assert res.returncode == 0, sortie
        worker.wait(timeout=10)  # tué par la purge, sinon TimeoutExpired
        assert voisin.poll() is None, "le worker d'un autre serveur a été tué"
    finally:
        for proc in (worker, voisin):
            if proc.poll() is None:
                proc.kill()


def test_stop_relit_aussi_le_journal_horodate_d_un_lancement_verrouille(tmp_path) -> None:
    """Revue 2026-09-29 : un lancement dont le journal était verrouillé écrit
    dans uvicorn_dev_<port>_<horodatage>.log ; le lancement suivant ne relisait
    que le journal par défaut et laissait vivre le worker muet de celui-là."""
    mort = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    pid_mort = int(mort.stdout.strip())
    (tmp_path / f"uvicorn_dev_{PORT_LIBRE}_20260929_120000.log.err").write_text(
        f"INFO:     Started reloader process [{pid_mort}] using WatchFiles\n", encoding="utf-8")
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                               "multiprocessing", f"parent_pid={pid_mort}"])
    try:
        env = dict(os.environ, APP_AUTH_PASSWORD="mdp-de-test", TEMP=str(tmp_path), TMP=str(tmp_path))
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(SCRIPT), "-Port", PORT_LIBRE, "-StopOnly"],
            cwd=RACINE, env=env, capture_output=True, timeout=180)
        assert res.returncode == 0, (res.stdout + res.stderr).decode("utf-8", errors="replace")
        worker.wait(timeout=10)  # tué par la purge, sinon TimeoutExpired
    finally:
        if worker.poll() is None:
            worker.kill()


def test_stop_ne_garde_que_les_trois_journaux_horodates_les_plus_recents(tmp_path) -> None:
    """Revue 2026-09-30 : les journaux de repli s'accumulaient sans fin."""
    import time
    for i in range(5):
        for suffixe in (".log", ".log.err"):
            f = tmp_path / f"uvicorn_dev_{PORT_LIBRE}_2026093{i}_120000{suffixe}"
            f.write_text("x", encoding="utf-8")
            os.utime(f, (time.time() - (5 - i) * 60,) * 2)
    env = dict(os.environ, APP_AUTH_PASSWORD="mdp-de-test", TEMP=str(tmp_path), TMP=str(tmp_path))
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(SCRIPT), "-Port", PORT_LIBRE, "-StopOnly"],
        cwd=RACINE, env=env, capture_output=True, timeout=180)
    assert res.returncode == 0, (res.stdout + res.stderr).decode("utf-8", errors="replace")
    restants = sorted(p.name for p in tmp_path.glob(f"uvicorn_dev_{PORT_LIBRE}_*"))
    assert restants == sorted(f"uvicorn_dev_{PORT_LIBRE}_2026093{i}_120000{s}"
                              for i in (2, 3, 4) for s in (".log", ".log.err"))


# --- Garde de démarrage (2026-10-15) ------------------------------------------
# Le site 8020 est tombé plusieurs fois : -KeepIfFresh purgeait un serveur qui ne
# répondait PAS ENCORE. Tous ces tests visent le port factice 8099, jamais 8020.

def _ps_fonctions(expression: str, *params: str, timeout: int = 120) -> str:
    """Charge les FONCTIONS du script (dot-sourcing, sans agir) et évalue."""
    env = dict(os.environ, SERVEUR_DEV_FONCTIONS_SEULES="1", APP_AUTH_PASSWORD="mdp-de-test",
               SERVEUR_DEV_PYTHON=sys.executable)
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         f". '{SCRIPT}' -Port {PORT_LIBRE} {' '.join(params)}; {expression}"],
        cwd=RACINE, env=env, capture_output=True, timeout=timeout)
    sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
    assert res.returncode == 0, sortie
    return sortie.strip()


def test_le_script_est_en_utf8_avec_bom_et_se_parse_en_ps51() -> None:
    assert SCRIPT.read_bytes()[:3] == b"\xef\xbb\xbf", "BOM perdu : PS 5.1 lira mal les accents"
    cmd = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::"
           f"ParseFile('{SCRIPT}',[ref]$t,[ref]$e); 'ERREURS=' + $e.Count")
    res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                         capture_output=True, timeout=60)
    assert "ERREURS=0" in res.stdout.decode("utf-8", errors="replace"), res.stdout + res.stderr


def test_parametres_par_defaut_de_la_garde() -> None:
    sortie = _ps_fonctions("\"$GraceDemarrageS $StabiliteS $AttenteDemarrageS $VerrouAttenteS\"")
    assert sortie.splitlines()[-1] == "900 120 300 60"


def test_decision_keepiffresh_table() -> None:
    cas = [  # sain, age_serveur, age_modif, repond -> décision
        ("$true", "$null", "$null", "$true", "sain"),
        ("$false", "30", "9999", "$false", "demarrage"),      # démarrage : ne répond pas encore
        ("$false", "30", "9999", "$true", "demarrage"),       # jeune mais périmé
        ("$false", "1000", "30", "$true", "stabilite"),       # --reload va rattraper
        ("$false", "1000", "500", "$true", "purger"),
        ("$false", "$null", "$null", "$false", "purger"),     # rien ne tourne
        ("$false", "1000", "10", "$false", "purger"),         # muet et vieux : la stabilité ne s'applique pas
        ("$false", "800", "9999", "$false", "demarrage"),     # 13 min : sous la grâce de 900 s (Whisper)
    ]
    for sain, age, modif, repond, attendu in cas:
        expr = (f"Get-DecisionKeepIfFresh -Sain {sain} -AgeServeurS {age} -AgeModifS {modif} "
                f"-Repond {repond} -Grace 900 -Stabilite 120")
        assert _ps_fonctions(expr).splitlines()[-1] == attendu, (sain, age, modif, repond)


def test_worker_actif_port_ferme_n_est_pas_purge_mais_inactif_si() -> None:
    base = "-Sain $false -AgeServeurS 5000 -AgeModifS 9999 -Grace 900 -Stabilite 120"
    assert _ps_fonctions(f"Get-DecisionKeepIfFresh {base} -Repond $false -Actif $true"
                         ).splitlines()[-1] == "demarrage"
    assert _ps_fonctions(f"Get-DecisionKeepIfFresh {base} -Repond $false -Actif $false"
                         ).splitlines()[-1] == "purger"
    # port ouvert et périmé : l'activité CPU n'excuse rien
    assert _ps_fonctions(f"Get-DecisionKeepIfFresh {base} -Repond $true -Actif $true"
                         ).splitlines()[-1] == "purger"


def _faux_serveur_uvicorn() -> subprocess.Popen:
    # Même forme qu'un uvicorn de CE repo sur le port factice : exécutable sous
    # la racine (venv), ligne de commande « uvicorn app.main » + « --port 8099 ».
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                             "uvicorn app.main:app", "--port", PORT_LIBRE])


def _tuer_arbre(proc: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)


def test_keepiffresh_ne_purge_pas_un_serveur_jeune_qui_ne_repond_pas_encore() -> None:
    if not Path(sys.executable).resolve().is_relative_to(RACINE):
        pytest.skip("le faux serveur doit tourner sous le venv du dépôt")
    faux = _faux_serveur_uvicorn()
    try:
        res = _lancer("-KeepIfFresh", mot_de_passe="mdp-de-test")
        sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
        assert res.returncode == 0, sortie
        assert "rien purg" in sortie, sortie
        assert "Purge" not in sortie and "uvicorn lancé" not in sortie
        assert faux.poll() is None, "le serveur jeune a été tué"
    finally:
        _tuer_arbre(faux)


def _tenir_le_verrou(secondes: int) -> subprocess.Popen:
    code = ("$m = New-Object System.Threading.Mutex($false, 'Global\\VSCode2-serveur-dev-"
            f"{PORT_LIBRE}'); [void]$m.WaitOne(0); Write-Host PRIS; Start-Sleep {secondes}")
    proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", code],
                            stdout=subprocess.PIPE)
    assert proc.stdout.readline().strip() == b"PRIS"
    return proc


def test_verrou_tenu_keepiffresh_sort_0_sans_rien_faire() -> None:
    tenant = _tenir_le_verrou(60)
    try:
        res = _lancer("-KeepIfFresh", mot_de_passe="mdp-de-test")
        sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
        assert res.returncode == 0, sortie
        assert "tient le verrou" in sortie and "Purge" not in sortie
    finally:
        _tuer_arbre(tenant)


def test_verrou_tenu_lancement_manuel_attend_puis_refuse_clairement() -> None:
    tenant = _tenir_le_verrou(60)
    try:
        res = _lancer("-VerrouAttenteS", "3", mot_de_passe="mdp-de-test")
        sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
        assert res.returncode == 3, sortie
        assert "tient le verrou" in sortie and "refus" in sortie
        assert "Purge" not in sortie
    finally:
        _tuer_arbre(tenant)


def test_le_verrou_est_relache_a_la_sortie() -> None:
    # Deux -CheckOnly/-KeepIfFresh de suite : le second ne doit pas voir un verrou
    # laissé par le premier.
    # Un faux serveur JEUNE : chaque passage sort en 0 sans lancer de vrai serveur
    # (sans lui, -KeepIfFresh lancerait un uvicorn réel sur le port factice).
    if not Path(sys.executable).resolve().is_relative_to(RACINE):
        pytest.skip("le faux serveur doit tourner sous le venv du dépôt")
    faux = _faux_serveur_uvicorn()
    try:
        premier = _lancer("-KeepIfFresh", mot_de_passe="mdp-de-test")
        res = _lancer("-KeepIfFresh", mot_de_passe="mdp-de-test")
        assert premier.returncode == 0 and res.returncode == 0
        assert "tient le verrou" not in (res.stdout + res.stderr).decode("utf-8", errors="replace")
        assert "rien purg" in (res.stdout + res.stderr).decode("utf-8", errors="replace")
    finally:
        _tuer_arbre(faux)


def test_attente_de_demarrage_300_s_et_message_peut_etre_lent() -> None:
    texte = SCRIPT.read_text(encoding="utf-8-sig")
    assert "foreach ($i in 1..$AttenteDemarrageS)" in texte
    assert "PEUT-ÊTRE simplement démarré lentement" in texte
    assert "1..30" not in texte
    assert "120 s" not in texte.split("# ---- 4. Health-check ----")[1].split("# ---- 5.")[0]


def test_retest_avant_kill_sort_si_le_serveur_est_devenu_sain_et_sinon_laisse_tuer() -> None:
    """Exécute le re-test : Resolve-DecisionKeepIfFresh est remplacée par un
    double qui rend « demarrage » ; la fonction doit SORTIR (exit 0, message
    « rien purgé », jamais « APRES ») seulement en -KeepIfFresh, hors -StopOnly,
    avec au moins un PID à tuer."""
    def appel(keep: str, stop: str, nb: int) -> str:
        return _ps_fonctions(
            "function Resolve-DecisionKeepIfFresh { 'demarrage' }; "
            f"Invoke-RetestAvantKill -KeepIfFreshActif {keep} -StopOnlyActif {stop} "
            f"-NbIds {nb} -NumPort {PORT_LIBRE}; 'APRES'")
    sortie = appel("$true", "$false", 2)
    assert "rien purg" in sortie and "APRES" not in sortie, sortie
    for keep, stop, nb in (("$false", "$false", 2), ("$true", "$true", 2), ("$true", "$false", 0)):
        sortie = appel(keep, stop, nb)
        assert "APRES" in sortie and "rien purg" not in sortie, (keep, stop, nb, sortie)


def test_la_boucle_de_purge_appelle_le_retest_avant_stop_process() -> None:
    texte = SCRIPT.read_text(encoding="utf-8-sig")
    boucle = texte[texte.index("foreach ($passe in 1..2)"):]
    assert boucle.index("Invoke-RetestAvantKill") < boucle.index("Stop-Process")


def test_serveur_vieux_mais_actif_port_ferme_n_est_pas_purge_via_la_mesure_cpu() -> None:
    """Chemin réel de la panne du 8020 : Resolve-DecisionKeepIfFresh mesure le
    CPU (Get-CpuServeurS) d'un faux serveur VIEUX (-GraceDemarrageS 0) au port
    fermé. Actif (boucle CPU) -> demarrage ; inactif -> purger. Port factice
    (jamais 8020). Get-ProcessusServeur est remplacée par un double qui désigne
    le faux serveur par PID (le filtre « exécutable sous la racine » exclut tout
    process quand le dépôt est une copie) ; l'âge, la mesure CPU et la décision
    sont les VRAIES fonctions du script."""
    port = "8098"
    for code, attendu in (("while True: pass", "demarrage"), ("import time; time.sleep(120)", "purger")):
        faux = subprocess.Popen([sys.executable, "-c", code, "uvicorn app.main:app", "--port", port])
        try:
            time.sleep(2)
            expr = ("function Get-ProcessusServeur { param([int]$NumPort) "
                    f"$r = @(Get-CimInstance Win32_Process -Filter 'ProcessId={faux.pid}'); "
                    "$r + @(Get-DescendantsProcessus -Racines @([int]$r[0].ProcessId)) }; "
                    "$script:motDePasse='x'; Resolve-DecisionKeepIfFresh -NumPort " + port)
            sortie = _ps_fonctions(expr, "-GraceDemarrageS", "0", "-StabiliteS", "0")
            assert sortie.splitlines()[-1] == attendu, sortie
        finally:
            _tuer_arbre(faux)


def test_python_borne_echappe_backslash_avant_guillemet() -> None:
    sortie = _ps_fonctions(r"""Invoke-PythonBorne -Code 'print(len("a\\"))' -DelaiS 30""")
    assert sortie.splitlines()[-1] == "2", sortie


def test_python_borne_tue_l_arbre_au_delai(tmp_path) -> None:
    pidfile = (tmp_path / "enfant.pid").as_posix()
    code = ('import subprocess,sys,time; '
            'c=subprocess.Popen([sys.executable,"-c","import time;time.sleep(90)"]); '
            f'open("{pidfile}","w").write(str(c.pid)); time.sleep(90)')
    t0 = time.time()
    sortie = _ps_fonctions(f"$r = Invoke-PythonBorne -Code '{code}' -DelaiS 4; "
                           "if ($null -eq $r) { 'NUL' } else { 'RENDU' }")
    assert sortie.splitlines()[-1] == "NUL", sortie
    assert time.time() - t0 < 60
    pid = (tmp_path / "enfant.pid").read_text()
    try:
        vivant = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                capture_output=True, text=True).stdout
        assert pid not in vivant, "le petit-enfant du python borné est resté orphelin"
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True)


def test_parametres_numeriques_sont_bornes() -> None:
    sortie = _ps_fonctions('"$AttenteDemarrageS $VerrouAttenteS"', "-AttenteDemarrageS", "0",
                           "-VerrouAttenteS", "-5")
    assert sortie.splitlines()[-1] == "1 0", sortie


def test_verrou_attente_negative_refuse_proprement_au_lieu_de_lever() -> None:
    tenant = _tenir_le_verrou(60)
    try:
        res = _lancer("-VerrouAttenteS", "-5", mot_de_passe="mdp-de-test")
        sortie = (res.stdout + res.stderr).decode("utf-8", errors="replace")
        assert res.returncode == 3, sortie
    finally:
        _tuer_arbre(tenant)


def test_get_octets_http_abandonne_un_serveur_qui_ne_repond_jamais() -> None:
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    try:
        t0 = time.time()
        sortie = _ps_fonctions(
            f"try {{ [void](Get-OctetsHttp -Url http://127.0.0.1:{port}/ -DelaiMs 1500); 'RENDU' }} "
            "catch { 'ABANDON' }", timeout=25)
        assert sortie.splitlines()[-1] == "ABANDON", sortie
        assert time.time() - t0 < 20
    finally:
        srv.close()


def test_les_appels_reseau_et_python_du_script_sont_bornes() -> None:
    texte = SCRIPT.read_text(encoding="utf-8-sig")
    assert ".DownloadData(" not in texte and ".DownloadString(" not in texte
    code = " ".join(ligne for ligne in texte.splitlines()
                    if not ligne.lstrip().startswith("#"))
    assert "& $python -c" not in code and "WaitForExit($DelaiS * 1000)" in code


def test_retest_apres_kill_ne_sort_pas_sur_un_respawn_jeune_mais_sort_si_sain() -> None:
    """R3 : en passe 2, « demarrage » = respawn de ce qu'on vient de tuer, pas une
    preuve de santé -> on n'exit pas 0 (le serveur peut être mort) ; « sain » sort."""
    def appel(decision: str, apres: str) -> str:
        return _ps_fonctions(
            f"function Resolve-DecisionKeepIfFresh {{ '{decision}' }}; "
            "Invoke-RetestAvantKill -KeepIfFreshActif $true -StopOnlyActif $false "
            f"-NbIds 2 -NumPort {PORT_LIBRE} -ApresKill {apres}; 'APRES'")
    for decision in ("demarrage", "stabilite"):
        sortie = appel(decision, "$true")
        assert "APRES" in sortie and "rien purg" not in sortie, (decision, sortie)
        assert "rien purg" in appel(decision, "$false")  # passe 1 : inchangé
    sortie = appel("sain", "$true")
    assert "OK : serveur" in sortie and "APRES" not in sortie, sortie


def test_la_passe_2_declare_apres_kill_au_retest() -> None:
    texte = SCRIPT.read_text(encoding="utf-8-sig")
    boucle = texte[texte.index("foreach ($passe in 1..2)"):]
    assert "-ApresKill ($passe -gt 1)" in boucle


def test_grace_et_stabilite_sont_bornees() -> None:
    sortie = _ps_fonctions('"$GraceDemarrageS $StabiliteS"', "-GraceDemarrageS", "-5",
                           "-StabiliteS", "99999999")
    assert sortie.splitlines()[-1] == "0 86400", sortie


def test_le_mutex_attrape_toute_erreur_d_ouverture_pas_seulement_unauthorized() -> None:
    texte = SCRIPT.read_text(encoding="utf-8-sig")
    bloc = texte.split("$script:verrou = $null")[1].split("if (-not $verrouPris)")[0]
    assert "catch [System.Threading.AbandonedMutexException]" in bloc
    assert "catch [System.UnauthorizedAccessException]" not in bloc
    assert "catch {" in bloc
