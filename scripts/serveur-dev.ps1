# Lanceur fiable du serveur dev Interview-to-Deck (Windows PowerShell 5.1).
#
# Pourquoi ce script existe (saga « toujours KO » / « port empoisonné », 2026-07-22) :
# un uvicorn --reload spawne son worker via multiprocessing.spawn — la ligne de
# commande du worker ne contient PAS « uvicorn », donc tous les kills filtrés sur
# %uvicorn% tuaient le parent et laissaient le worker orphelin continuer à servir
# du code périmé (netstat attribue le socket au PID du parent MORT → le port a
# l'air hanté). Ce script tue le parent ET les workers, vérifie que le port ne
# répond réellement plus, relance proprement, et prouve la fraîcheur du contenu
# servi (octets servis == octets sur disque). cf. .claude/skills/run-dev-server.
#
# Durci (revue adversariale 2026-07-22) : purge SCOPÉE à ce repo (ExecutablePath
# sous la racine — ne tue jamais l'uvicorn d'un projet frère ni les workers spawn
# d'une appli tierce) ; refus de tuer un listener non-python ; -KeepIfFresh
# (auto-start VS Code : ne redémarre pas un serveur déjà frais — une génération
# IA en cours n'est pas avortée à la réouverture du dossier).
#
# Élargi (2026-07-27) : le scope « ExecutablePath sous la racine » laissait
# passer un serveur lancé avec un python HORS venv (python système). Son worker
# orphelin tenait le port 8040 sans être vu par la purge, et le kill du listener
# le ratait aussi puisque le socket est attribué au PID du PARENT, mort. La
# purge cherche désormais, en plus, les workers dont le parent mort détient le
# socket du port visé — quel que soit leur interpréteur (cf.
# Get-WorkersOrphelinsDuPort).
#
# Scopé au PORT (2026-09-08) : la purge tuait TOUS les serveurs du repo, sur
# n'importe quel port. Lancer un second serveur (-Port 8040) pendant qu'un
# entretien s'enregistrait sur 8020 a tué ce dernier en vol : 9 segments de
# transcription perdus côté navigateur, un job de répartition figé « running ».
# Les racines sont désormais filtrées sur le `--port <N>` de leur ligne de
# commande (cf. Test-CommandeSurLePort) ; les orphelins restent cherchés par
# le socket du port visé. Deux serveurs du même repo coexistent donc.
#
# Authentifié (diagnostic superviseur du 2026-09-29, arbitré le jour même) :
# le « OK » ne regardait que /static/ et un code HTTP quelconque sur / — les
# SEULES routes publiques. Un serveur sans APP_AUTH_PASSWORD, qui rend 503 sur
# toute page réelle, était déclaré sain ; l'utilisateur l'a vu KO trois fois.
# Désormais : refus de démarrer sans mot de passe, et le OK exige un 200 sur
# une route PROTÉGÉE (/missions) avec `Authorization: Bearer`. -CheckOnly fait
# ce contrôle sur un serveur en marche sans rien purger ni relancer.
#
# Usage :  powershell -ExecutionPolicy Bypass -File scripts/serveur-dev.ps1
#          [-Port 8020] [-StopOnly] [-KeepIfFresh] [-CheckOnly]

param(
    # 8020 : le port de l'utilisateur (arbitrage du 2026-09-29). Il était hanté
    # en juillet ; la cause (worker orphelin d'un redirecteur venv) est traitée
    # par la purge ci-dessous depuis le 2026-07-27.
    [int]$Port = 8020,
    [switch]$StopOnly,
    [switch]$KeepIfFresh,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$racine = Split-Path -Parent $PSScriptRoot   # scripts/ -> racine du repo
$python = Join-Path $racine ".venv\Scripts\python.exe"
$journal = Join-Path $env:TEMP ("uvicorn_dev_" + $Port + ".log")

function Get-DescendantsProcessus {
    # Descendance COMPLÈTE de PID racines, par parenté OS (ParentProcessId) et
    # par marqueur `parent_pid=` de multiprocessing.spawn. Les deux liens sont
    # nécessaires : le premier rate le worker spawn quand son parent est déjà
    # mort, le second rate un enfant qui n'est pas un worker spawn.
    param([int[]]$Racines)
    $tous = @(Get-CimInstance Win32_Process)
    $resultat = @()
    $vus = @{}
    $frontiere = @($Racines)
    while ($frontiere.Count -gt 0) {
        $suivante = @()
        foreach ($id in $frontiere) {
            if ($vus.ContainsKey($id)) { continue }
            $vus[$id] = $true
            foreach ($enfant in @($tous | Where-Object {
                [int]$_.ParentProcessId -eq $id -or $_.CommandLine -like "*parent_pid=$id*"
            })) {
                $resultat += $enfant
                $suivante += [int]$enfant.ProcessId
            }
        }
        $frontiere = $suivante
    }
    return $resultat
}

function Test-CommandeSurLePort {
    # Vrai si cette ligne de commande uvicorn sert LE port visé (`--port 8040`
    # comme `--port=8040`). Sans `--port` explicite, uvicorn écoute sur 8000.
    param([string]$Commande, [int]$NumPort)
    if ($Commande -match '--port[ =]"?(\d+)') { return ([int]$Matches[1] -eq $NumPort) }
    return ($NumPort -eq 8000)
}

function Get-ProcessusServeur {
    param([int]$NumPort)
    # Tous les process liés au serveur DE CE REPO. Les RACINES restent scopées à
    # $racine (ExecutablePath sous la racine + ligne de commande uvicorn) — on
    # ne tue jamais l'uvicorn d'un repo frère ni un process tiers. Mais on tue
    # toute leur DESCENDANCE, sans la re-filtrer sur l'exécutable.
    #
    # Pourquoi (cause racine de la saga des ports 8010/8020/8030/8040, trouvée
    # le 2026-07-27) : `.venv\Scripts\python.exe` est un REDIRECTEUR — le vrai
    # processus uvicorn et son worker `--reload` tournent sous le python de BASE
    # (C:\PythonXXX\python.exe), donc HORS de $racine. Le filtre par exécutable
    # était structurellement aveugle au worker de NOTRE PROPRE serveur : chaque
    # purge tuait le redirecteur et le parent, laissait le worker vivant, et
    # celui-ci continuait de servir sur un socket que netstat attribue au PID du
    # parent mort. Un port de plus condamné à chaque redémarrage.
    $racines = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object {
            $_.ExecutablePath -like "$racine\*" -and $_.CommandLine -like "*uvicorn app.main*" -and
            (Test-CommandeSurLePort -Commande $_.CommandLine -NumPort $NumPort)  # jamais un autre port de CE repo (2026-09-08)
        })
    if ($racines.Count -eq 0) { return @() }
    return @($racines) + @(Get-DescendantsProcessus -Racines @($racines | ForEach-Object { [int]$_.ProcessId }))
}

function Get-WorkersDeParent {
    # Tous les process VIVANTS qu'un multiprocessing.spawn a fait naître du PID
    # $PidParent — quel que soit leur interpréteur (python système, pythonw,
    # python d'un autre venv). Le lien n'est pas déduit de ParentProcessId (que
    # Windows recycle et qui ne prouve rien une fois le parent mort) mais du
    # `parent_pid=<pid>` que spawn_main écrit LITTÉRALEMENT dans la ligne de
    # commande de l'enfant : un PID recyclé ne peut pas produire ce marqueur par
    # hasard, donc aucun risque de tuer le worker d'une appli tierce.
    param([int]$PidParent)
    return @(Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -like "*multiprocessing*" -and
            # Fin de nombre OBLIGATOIRE : « *parent_pid=123* » matchait aussi
            # parent_pid=1234 — le worker d'un AUTRE serveur (revue 2026-09-29).
            $_.CommandLine -match "parent_pid=$PidParent(\D|$)"
        })
}

function Get-WorkersOrphelinsDuPort {
    # Cas vécu le 2026-07-27 : le port répond, mais son PID propriétaire n'existe
    # plus. C'est le worker orphelin d'un uvicorn --reload dont le parent est
    # mort ; netstat continue d'attribuer le socket au parent. La purge scopée au
    # repo (Get-ProcessusServeur) ne le voit pas quand le serveur a été lancé
    # avec un python hors venv, et le kill du listener ne le voit pas non plus
    # puisque le PID du listener est mort. On remonte donc du PID mort à ses
    # workers — le seul chemin qui reste.
    param([int]$NumPort)
    $orphelins = @()
    foreach ($c in @(Get-NetTCPConnection -LocalPort $NumPort -State Listen -ErrorAction SilentlyContinue)) {
        $pidProprio = [int]$c.OwningProcess
        # Propriétaire vivant : c'est un vrai serveur, traité par la purge
        # normale (qui refuse de tuer un listener non-python).
        if (Get-Process -Id $pidProprio -ErrorAction SilentlyContinue) { continue }
        $trouves = @(Get-WorkersDeParent -PidParent $pidProprio)
        Write-Host ("Socket fantôme sur $NumPort : PID propriétaire $pidProprio est mort, " +
                    "$($trouves.Count) worker(s) orphelin(s) rattaché(s).")
        $orphelins += $trouves
    }
    return $orphelins
}

function Get-WorkersOrphelinsDuJournal {
    # Cas vécu le 2026-09-29 : un --reload déclenché par une édition de tests/
    # a vu mourir son reloader ; le worker respawné restait vivant SANS écouter
    # (port muet, ERR_CONNECTION_REFUSED dans Chrome) mais tenait le journal
    # ouvert — la rotation échouait et le relancement avec. Aucun socket, donc
    # Get-WorkersOrphelinsDuPort ne le voit pas. Le journal .err du lancement
    # précédent porte « Started reloader process [<pid>] » : si ce PID est mort,
    # ses workers (marqueur littéral parent_pid=) sont des orphelins sûrs.
    param([string]$JournalErr)
    if (-not (Test-Path $JournalErr)) { return @() }
    $orphelins = @()
    foreach ($m in (Select-String -Path $JournalErr -Pattern 'Started reloader process \[(\d+)\]' -AllMatches)) {
        foreach ($g in $m.Matches) {
            $pidReloader = [int]$g.Groups[1].Value
            if (Get-Process -Id $pidReloader -ErrorAction SilentlyContinue) { continue }
            $trouves = @(Get-WorkersDeParent -PidParent $pidReloader)
            if ($trouves.Count -gt 0) {
                Write-Host "Reloader $pidReloader mort (journal) : $($trouves.Count) worker(s) orphelin(s) muet(s)."
            }
            $orphelins += $trouves
        }
    }
    return $orphelins
}

function Test-PortRepond {
    param([int]$NumPort)
    try {
        $req = [System.Net.WebRequest]::Create("http://127.0.0.1:$NumPort/")
        $req.Timeout = 2000
        $rep = $req.GetResponse(); $rep.Close()
        return $true
    } catch [System.Net.WebException] {
        # Une réponse HTTP même en erreur (404…) prouve qu'un serveur écoute.
        if ($_.Exception.Response) { return $true }
        return $false
    } catch { return $false }
}

function Test-ContenuFrais {
    # Preuve de fraîcheur : le contenu STATIQUE servi == le fichier sur disque,
    # comparé en OCTETS (DownloadString décoderait en Latin-1 sans charset
    # déclaré → faux positif sur les accents, vu au premier run réel). Au moins
    # UNE comparaison doit avoir réellement eu lieu (un renommage des deux
    # actifs ne doit pas valider dans le vide).
    param([int]$NumPort)
    $wc = New-Object System.Net.WebClient
    $nbCompares = 0
    foreach ($actif in @("app.css", "busy.js")) {
        $disque = Join-Path $racine "app\static\$actif"
        if (-not (Test-Path $disque)) { continue }
        try { $servi = $wc.DownloadData("http://127.0.0.1:$NumPort/static/$actif") } catch { return $false }
        $attendu = [System.IO.File]::ReadAllBytes($disque)
        if ($servi.Length -ne $attendu.Length) { return $false }
        for ($i = 0; $i -lt $servi.Length; $i++) {
            if ($servi[$i] -ne $attendu[$i]) { return $false }
        }
        $nbCompares++
    }
    return ($nbCompares -gt 0)
}

function Get-MotDePasseApp {
    # Le mot de passe tel que l'APP le verra : .env chargé par python-dotenv,
    # sans écraser une variable déjà posée (même règle que app/main.py), puis
    # lu par app.auth.mot_de_passe(). Transporté en base64 UTF-8 : la console
    # PS 5.1 mutile un accent. Jamais affiché.
    $code = "import base64,sys; from pathlib import Path; from dotenv import load_dotenv; " +
            "load_dotenv(Path(r'$racine') / '.env'); from app.auth import mot_de_passe; " +
            "sys.stdout.write(base64.b64encode((mot_de_passe() or '').encode('utf-8')).decode('ascii'))"
    Push-Location $racine
    try { $b64 = (& $python -c $code 2>$null | Select-Object -Last 1) } finally { Pop-Location }
    if (-not $b64) { return $null }
    return [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64.Trim()))
}

function Get-CodeRouteProtegee {
    # Code HTTP de GET /missions AUTHENTIFIÉ. 200 = le site sert vraiment ;
    # 503 = mot de passe absent côté serveur ; 401 = mot de passe différent.
    param([int]$NumPort, [string]$Mdp)
    try {
        $req = [System.Net.WebRequest]::Create("http://127.0.0.1:$NumPort/missions")
        $req.Timeout = 10000
        $req.Headers.Add("Authorization", "Bearer $Mdp")
        $rep = $req.GetResponse(); $code = [int]$rep.StatusCode; $rep.Close()
        return $code
    } catch [System.Net.WebException] {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    } catch { return 0 }
}

function Test-PythonFrais {
    # /__fraicheur : empreinte capturée à l'IMPORT par le worker == empreinte du
    # disque (recalculée par le même code). C'est LA détection du --reload qui a
    # raté une modif — diagnostic superviseur 2026-07-23. La route est PROTÉGÉE
    # depuis le 2026-09-19 : sans l'en-tête, ce contrôle échouait toujours.
    param([int]$NumPort)
    try {
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add("Authorization", "Bearer $script:motDePasse")
        $servie = ($wc.DownloadString("http://127.0.0.1:$NumPort/__fraicheur") |
            ConvertFrom-Json).empreinte
        $disque = (& $python -c "from app.main import empreinte_code; print(empreinte_code())" 2>$null | Select-Object -Last 1).Trim()
        return ($servie -and $disque -and $servie -eq $disque)
    } catch { return $false }
}

# ---- 0a. Mot de passe : AVANT toute purge — ne jamais tuer un serveur pour en
# relancer un qui rendrait 503 partout. ----
$script:motDePasse = $null
if (-not $StopOnly) {
    $script:motDePasse = Get-MotDePasseApp
    if (-not $script:motDePasse) {
        # Pas Write-Error : sous EAP Stop il TERMINE le script en code 1, avant
        # le `exit 2` qui distingue ce refus d'une panne.
        $host.UI.WriteErrorLine(("APP_AUTH_PASSWORD absent (ni variable d'environnement ni .env) : le site " +
                     "rendrait 503 sur toute page hors /connexion. Poser la ligne " +
                     "APP_AUTH_PASSWORD=... dans .env puis relancer. Rien n'a été arrêté."))
        exit 2
    }
}

# ---- 0b. -CheckOnly : vérifier le serveur EN MARCHE, sans le toucher ----
if ($CheckOnly) {
    if (-not (Test-PortRepond -NumPort $Port)) {
        Write-Error "KO : rien n'écoute sur http://127.0.0.1:$Port."
        exit 1
    }
    $codeProtege = Get-CodeRouteProtegee -NumPort $Port -Mdp $script:motDePasse
    $frais = Test-ContenuFrais -NumPort $Port
    $fraisPy = Test-PythonFrais -NumPort $Port
    if ($codeProtege -eq 200 -and $frais -and $fraisPy) {
        Write-Host "OK : http://127.0.0.1:$Port sert /missions authentifié (200), statique ET python frais."
        exit 0
    }
    Write-Error ("KO : /missions authentifié -> $codeProtege (503 = le SERVEUR n'a pas le mot de passe, " +
                 "à relancer ; 401 = mot de passe du serveur différent du .env), statique frais: $frais, " +
                 "python frais: $fraisPy.")
    exit 1
}

# ---- 0. -KeepIfFresh (auto-start VS Code) : ne pas avorter un serveur sain ----
# Un POST de génération IA synchrone peut durer plusieurs minutes — une purge à
# chaque folderOpen le tuerait en vol. Conservé UNIQUEMENT si statique ET python
# servis == disque : un serveur au python périmé est purgé et relancé.
if ($KeepIfFresh -and -not $StopOnly -and (Test-PortRepond -NumPort $Port)) {
    if ((Test-ContenuFrais -NumPort $Port) -and (Test-PythonFrais -NumPort $Port) -and
        ((Get-CodeRouteProtegee -NumPort $Port -Mdp $script:motDePasse) -eq 200)) {
        Write-Host "OK : serveur déjà frais (statique + python) sur http://127.0.0.1:$Port — conservé (-KeepIfFresh)."
        exit 0
    }
    Write-Host "Serveur présent mais contenu périmé — purge et relance."
}

# ---- 1. Purge : parents uvicorn + toute leur descendance + listener du port ----
# @( ) OBLIGATOIRE : PS 5.1 déroule un retour de fonction à 1 élément en scalaire
# et `+=` sur un CimInstance scalaire lève op_Addition sous EAP Stop — le
# scénario phare (exactement 1 fantôme) tuait le script avant la purge.
#
# DEUX passes : tant que le parent est vivant, ses workers ne sont pas encore
# des « orphelins du port » (Get-WorkersOrphelinsDuPort passe son chemin sur un
# PID propriétaire vivant). Un worker qui survit à la 1re passe devient donc
# détectable seulement à la 2e — sans elle, le port était déclaré hanté alors
# qu'une simple reprise suffisait (vécu le 2026-07-27).
foreach ($passe in 1..2) {
    $aTuer = @(Get-ProcessusServeur -NumPort $Port) + @(Get-WorkersOrphelinsDuPort -NumPort $Port) +
             @(Get-WorkersOrphelinsDuJournal -JournalErr ($journal + ".err"))
    $ecoute = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in @($ecoute)) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -ne "python") {
            # Jamais de kill aveugle d'une appli tierce légitime sur ce port.
            Write-Error "Le port $Port est occupé par '$($p.ProcessName)' (PID $($p.Id)) — non tué. Choisir un autre port (-Port $($Port + 10))."
            exit 1
        }
        if ($p) { $aTuer += @(Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)") }
    }
    $ids = @($aTuer | ForEach-Object { $_.ProcessId } | Sort-Object -Unique)
    if ($ids.Count -gt 0) {
        Write-Host "Purge (passe $passe) de $($ids.Count) processus serveur (uvicorn + descendance) : $($ids -join ', ')"
        foreach ($id in $ids) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
    }
    if (-not (Test-PortRepond -NumPort $Port)) { break }
}

# Le port doit avoir VRAIMENT cessé de répondre (pas seulement netstat propre).
if (Test-PortRepond -NumPort $Port) {
    Write-Error ("Le port $Port répond ENCORE après purge : fantôme hors de portée (ni process du repo, " +
                 "ni worker rattaché au PID propriétaire du socket, cherché par parent_pid=). " +
                 "Relancer avec -Port $($Port + 10) — et reporter ce port dans .vscode/tasks.json " +
                 "(2 occurrences), sinon l'auto-start rejouera l'échec à chaque ouverture du dossier.")
    exit 1
}
if ($StopOnly) { Write-Host "Serveur arrêté, port $Port libre."; exit 0 }

# ---- 2. Bytecode : écarter l'hypothèse __pycache__ périmé ----
Get-ChildItem -Path (Join-Path $racine "app") -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# ---- 3. Lancement --reload (obligatoire, cf. run-dev-server) ----
if (-not (Test-Path $python)) { Write-Error "venv introuvable : $python"; exit 1 }
# Rotation du journal (2026-09-08) : -RedirectStandardOutput ECRASE le fichier a
# chaque lancement, et le journal de la session precedente disparaissait avec lui
# (les deux premieres heures d'un entretien reel, le jour ou il aurait fallu le
# lire). Une generation est conservee : <journal>.prev et <journal>.err.prev.
foreach ($ancien in @($journal, ($journal + ".err"))) {
    if (Test-Path $ancien) {
        try { Move-Item -Force -Path $ancien -Destination ($ancien + ".prev") -ErrorAction Stop }
        catch {
            # Journal tenu par un process hors d'atteinte de la purge : ne pas
            # mourir ici (site KO sans explication) — nommer le fautif et
            # lancer sur un journal horodaté.
            Write-Warning "Journal $ancien verrouillé ($($_.Exception.Message)) — nouveau journal horodaté."
            $journal = Join-Path $env:TEMP ("uvicorn_dev_" + $Port + "_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
            break
        }
    }
}
# --reload-dir app : seul le code servi relance le serveur. Le 2026-09-29, une
# édition de tests/ a déclenché un rechargement pendant lequel le reloader est
# mort — site KO pour une modification qui ne change rien à ce qui est servi.
$proc = Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "$Port", "--reload", "--reload-dir", "app" `
    -WorkingDirectory $racine -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $journal -RedirectStandardError ($journal + ".err")
Write-Host "uvicorn lancé (PID $($proc.Id)), journal : $journal"

# ---- 4. Health-check ----
$pret = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 1
    if (Test-PortRepond -NumPort $Port) { $pret = $true; break }
    if ($proc.HasExited) { break }
}
if (-not $pret) {
    Write-Error "Le serveur n'a pas démarré en 30 s — voir $journal et $journal.err"
    exit 1
}

# ---- 5. Preuve de fraîcheur (statique + PYTHON) + unicité du listener ----
# Python : /__fraicheur renvoie l'empreinte capturée à l'IMPORT par le worker ;
# on la compare à l'empreinte recalculée du DISQUE (même algorithme, app.main).
# C'est LA preuve que le --reload n'a pas servi du code périmé (diagnostic
# superviseur 2026-07-23 — la preuve octets ne couvrait que le statique).
$frais = Test-ContenuFrais -NumPort $Port
$fraisPy = Test-PythonFrais -NumPort $Port
$codeProtege = Get-CodeRouteProtegee -NumPort $Port -Mdp $script:motDePasse
$nbEcoute = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique).Count
if ($frais -and $fraisPy -and $codeProtege -eq 200 -and $nbEcoute -eq 1) {
    Write-Host "OK : serveur FRAIS sur http://127.0.0.1:$Port (1 seul listener, statique ET python servis = disque, /missions authentifié = 200)."
} else {
    Write-Error "Serveur lancé mais suspect (listeners uniques: $nbEcoute, statique frais: $frais, python frais: $fraisPy, /missions authentifié: $codeProtege) — ne pas s'en servir tel quel."
    exit 1
}
