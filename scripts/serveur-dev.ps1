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
# Usage :  powershell -ExecutionPolicy Bypass -File scripts/serveur-dev.ps1
#          [-Port 8020] [-StopOnly] [-KeepIfFresh]

param(
    [int]$Port = 8020,
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

# ---- 0. -KeepIfFresh (auto-start VS Code) : ne pas avorter un serveur sain ----
# Un POST de génération IA synchrone peut durer plusieurs minutes — une purge à
# chaque folderOpen le tuerait en vol. NB : la fraîcheur prouvée porte sur le
# STATIQUE ; après une modif de code python, relancer SANS -KeepIfFresh.
if ($KeepIfFresh -and -not $StopOnly -and (Test-PortRepond -NumPort $Port)) {
    if (Test-ContenuFrais -NumPort $Port) {
        Write-Host "OK : serveur déjà frais sur http://127.0.0.1:$Port — conservé (-KeepIfFresh)."
        exit 0
    }
    Write-Host "Serveur présent mais contenu périmé — purge et relance."
}

# ---- 1. Purge : parents uvicorn + workers spawn du repo + listener du port ----
# @( ) OBLIGATOIRE : PS 5.1 déroule un retour de fonction à 1 élément en scalaire
# et `+=` sur un CimInstance scalaire lève op_Addition sous EAP Stop — le
# scénario phare (exactement 1 fantôme) tuait le script avant la purge.
$aTuer = @(Get-ProcessusServeur)
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
    Write-Error "Le port $Port répond ENCORE après purge : fantôme hors de portée. Relancer avec -Port $($Port + 10)."
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

# ---- 5. Preuve de fraîcheur + unicité du listener ----
$frais = Test-ContenuFrais -NumPort $Port
$nbEcoute = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique).Count
if ($frais -and $nbEcoute -eq 1) {
    Write-Host "OK : serveur FRAIS sur http://127.0.0.1:$Port (1 seul listener, contenu servi = disque)."
} else {
    Write-Error "Serveur lancé mais suspect (listeners uniques: $nbEcoute, frais: $frais) — ne pas s'en servir tel quel."
    exit 1
}
