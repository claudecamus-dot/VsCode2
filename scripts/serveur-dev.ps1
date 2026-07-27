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
# Usage :  powershell -ExecutionPolicy Bypass -File scripts/serveur-dev.ps1
#          [-Port 8020] [-StopOnly] [-KeepIfFresh]

param(
    # 8040 : 8010, 8020 puis 8030 sont HANTÉS sur ce poste (socket détenu par un PID
    # mort, insensible aux kills des deux namespaces — vécu 2026-07-22 ×3).
    [int]$Port = 8040,
    [switch]$StopOnly,
    [switch]$KeepIfFresh
)

$ErrorActionPreference = "Stop"
$racine = Split-Path -Parent $PSScriptRoot   # scripts/ -> racine du repo
$python = Join-Path $racine ".venv\Scripts\python.exe"
$journal = Join-Path $env:TEMP ("uvicorn_dev_" + $Port + ".log")

function Get-ProcessusServeur {
    # Tous les process liés au serveur DE CE REPO : parents uvicorn + workers
    # multiprocessing. Scopé sur ExecutablePath sous $racine (le python du venv)
    # — jamais l'uvicorn d'un repo frère ni les workers spawn d'une appli tierce.
    $tous = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.ExecutablePath -like "$racine\*" }
    $parents = @($tous | Where-Object { $_.CommandLine -like "*uvicorn app.main*" })
    $idsParents = @($parents | ForEach-Object { $_.ProcessId })
    $vivants = @{}
    foreach ($p in $tous) { $vivants[[uint32]$p.ProcessId] = $true }
    # Workers spawn : enfant d'un parent uvicorn connu, OU orphelin (parent mort)
    # — c'est le fantôme qui a empoisonné 8010 puis 8020.
    $workers = @($tous | Where-Object {
        $_.CommandLine -like "*multiprocessing.spawn*" -and
        ($idsParents -contains $_.ParentProcessId -or -not $vivants.ContainsKey([uint32]$_.ParentProcessId))
    })
    return @($parents) + @($workers)
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
            $_.CommandLine -like "*parent_pid=$PidParent*"
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

function Test-PythonFrais {
    # /__fraicheur : empreinte capturée à l'IMPORT par le worker == empreinte du
    # disque (recalculée par le même code). C'est LA détection du --reload qui a
    # raté une modif — diagnostic superviseur 2026-07-23.
    param([int]$NumPort)
    try {
        $servie = ((New-Object System.Net.WebClient).DownloadString("http://127.0.0.1:$NumPort/__fraicheur") |
            ConvertFrom-Json).empreinte
        $disque = (& $python -c "from app.main import empreinte_code; print(empreinte_code())" 2>$null | Select-Object -Last 1).Trim()
        return ($servie -and $disque -and $servie -eq $disque)
    } catch { return $false }
}

# ---- 0. -KeepIfFresh (auto-start VS Code) : ne pas avorter un serveur sain ----
# Un POST de génération IA synchrone peut durer plusieurs minutes — une purge à
# chaque folderOpen le tuerait en vol. Conservé UNIQUEMENT si statique ET python
# servis == disque : un serveur au python périmé est purgé et relancé.
if ($KeepIfFresh -and -not $StopOnly -and (Test-PortRepond -NumPort $Port)) {
    if ((Test-ContenuFrais -NumPort $Port) -and (Test-PythonFrais -NumPort $Port)) {
        Write-Host "OK : serveur déjà frais (statique + python) sur http://127.0.0.1:$Port — conservé (-KeepIfFresh)."
        exit 0
    }
    Write-Host "Serveur présent mais contenu périmé — purge et relance."
}

# ---- 1. Purge : parents uvicorn + workers spawn du repo + listener du port ----
# @( ) OBLIGATOIRE : PS 5.1 déroule un retour de fonction à 1 élément en scalaire
# et `+=` sur un CimInstance scalaire lève op_Addition sous EAP Stop — le
# scénario phare (exactement 1 fantôme) tuait le script avant la purge.
$aTuer = @(Get-ProcessusServeur) + @(Get-WorkersOrphelinsDuPort -NumPort $Port)
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
    Write-Host "Purge de $($ids.Count) processus serveur (parents uvicorn + workers spawn) : $($ids -join ', ')"
    foreach ($id in $ids) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
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
$proc = Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "$Port", "--reload" `
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
$nbEcoute = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique).Count
if ($frais -and $fraisPy -and $nbEcoute -eq 1) {
    Write-Host "OK : serveur FRAIS sur http://127.0.0.1:$Port (1 seul listener, statique ET python servis = disque)."
} else {
    Write-Error "Serveur lancé mais suspect (listeners uniques: $nbEcoute, statique frais: $frais, python frais: $fraisPy) — ne pas s'en servir tel quel."
    exit 1
}
