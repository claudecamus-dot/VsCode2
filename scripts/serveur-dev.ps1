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
# Garde de démarrage (2026-10-15) : le site 8020 est tombé plusieurs fois les
# 2026-10-14/15. Cause probable (NON PROUVÉE) : la tâche planifiée relance ce
# script en -KeepIfFresh toutes les 5 min ; un serveur qui ne répond PAS ENCORE
# (démarrage ou rechargement lent sous charge, > 1 min observé) tombait dans la
# purge « il ne répond pas, donc périmé », et l'instance suivante le tuait en
# plein démarrage. Pire (mesuré 11:31 -> 11:33) : la DÉCISION de purger était
# prise à 11:31 et exécutée 2 min plus tard, sur un serveur devenu sain entre-temps.
# D'où : (a) un serveur JEUNE (< -GraceDemarrageS) n'est jamais purgé en
# -KeepIfFresh ; (b) un serveur périmé dont app/ a été modifié depuis moins de
# -StabiliteS n'est pas purgé (le --reload va rattraper) ; (c) l'attente de
# démarrage passe à -AttenteDemarrageS (300 s) ; (d) un mutex nommé empêche deux
# instances d'agir en même temps ; (e) la décision est RE-TESTÉE juste avant le
# premier kill et entre les deux passes de purge. Les appels réseau et le
# sous-processus python sont bornés (une instance bloquée 2 jours a été vue).
# Limite assumée de (a) : un serveur JEUNE qui sert du code PÉRIMÉ (--reload
# raté juste après le démarrage) est épargné jusqu'à -GraceDemarrageS (900 s)
# avec le message « démarrage en cours » ; il n'est purgé qu'ensuite. Choix
# délibéré (mieux vaut 15 min de code périmé qu'un démarrage Whisper tué).
# Paramètres numériques bornés au chargement (cf. Limiter-Entier). Le mutex
# Global\ entre une instance élevée et une non élevée peut lever
# UnauthorizedAccessException : traité comme « verrou tenu » (NON prouvé ici,
# il faudrait une élévation pour le reproduire).
#
# Usage :  powershell -ExecutionPolicy Bypass -File scripts/serveur-dev.ps1
#          [-Port 8020] [-StopOnly] [-KeepIfFresh] [-CheckOnly]
#          [-GraceDemarrageS 900] [-StabiliteS 120] [-AttenteDemarrageS 300]
#          [-VerrouAttenteS 60]

param(
    # 8020 : le port de l'utilisateur (arbitrage du 2026-09-29). Il était hanté
    # en juillet ; la cause (worker orphelin d'un redirecteur venv) est traitée
    # par la purge ci-dessous depuis le 2026-07-27.
    [int]$Port = 8020,
    [switch]$StopOnly,
    [switch]$KeepIfFresh,
    [switch]$CheckOnly,
    # 900 s : mesuré le 2026-10-15, le démarrage (lifespan : réconciliations puis
    # préchauffage Whisper audio_transcribe.warm_up(), puis Ollama) dure >= 8 min
    # sous charge — 1 Go de RAM, 83 s de CPU, port fermé pendant tout ce temps.
    [int]$GraceDemarrageS = 900,
    [int]$StabiliteS = 120,
    [int]$AttenteDemarrageS = 300,
    [int]$VerrouAttenteS = 60
)

$ErrorActionPreference = "Stop"

function Limiter-Entier {
    # Borne une valeur de paramètre : 1..0 itérerait à l'envers, WaitOne(<-1) lève.
    param([int]$Valeur, [int]$Min, [int]$Max)
    return [math]::Min($Max, [math]::Max($Min, $Valeur))
}
$AttenteDemarrageS = Limiter-Entier $AttenteDemarrageS 1 86400
$VerrouAttenteS = Limiter-Entier $VerrouAttenteS 0 3600
$GraceDemarrageS = Limiter-Entier $GraceDemarrageS 0 86400
$StabiliteS = Limiter-Entier $StabiliteS 0 86400
$racine = Split-Path -Parent $PSScriptRoot   # scripts/ -> racine du repo
$python = Join-Path $racine ".venv\Scripts\python.exe"
# Interpréteur surchargeable (tests en copie du dépôt, sans .venv) ; usage normal : le venv.
if ($env:SERVEUR_DEV_PYTHON) { $python = $env:SERVEUR_DEV_PYTHON }
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

function Get-OctetsHttp {
    # GET borné (2026-10-15) : WebClient.DownloadData/DownloadString n'ont AUCUN
    # délai — un serveur qui accepte la connexion sans répondre bloquait le
    # script indéfiniment (instances vieilles de 2 jours vues vivantes).
    param([string]$Url, [string]$Bearer, [int]$DelaiMs = 10000)
    $req = [System.Net.WebRequest]::Create($Url)
    $req.Timeout = $DelaiMs
    $req.ReadWriteTimeout = $DelaiMs
    if ($Bearer) { $req.Headers.Add("Authorization", "Bearer $Bearer") }
    $rep = $req.GetResponse()
    try {
        $ms = New-Object System.IO.MemoryStream
        $rep.GetResponseStream().CopyTo($ms)
        return , $ms.ToArray()
    } finally { $rep.Close() }
}

function Invoke-PythonBorne {
    # python -c <code> avec un DÉLAI DUR (2026-10-15) : `& $python -c ...` n'en a
    # pas, et un import d'app sous charge ou un serveur de base verrouillé le
    # bloquait sans fin. Rend la dernière ligne de stdout, ou $null (délai/échec).
    param([string]$Code, [int]$DelaiS = 90)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $python
    # Échappement CRT : les \ qui précèdent un " sont doublés (sinon `\"` mange le
    # guillemet), idem ceux en fin de chaîne avant le " fermant.
    $echappe = [regex]::Replace($Code, '(\\*)"', { param($m) ($m.Groups[1].Value * 2) + '\"' })
    $echappe = [regex]::Replace($echappe, '(\\+)$', { param($m) $m.Groups[1].Value * 2 })
    $psi.Arguments = '-c "' + $echappe + '"'
    $psi.WorkingDirectory = $racine
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $sortie = $proc.StandardOutput.ReadToEndAsync()
    $erreur = $proc.StandardError.ReadToEndAsync()
    if (-not $proc.WaitForExit($DelaiS * 1000)) {
        # Arbre entier : le python du venv est un REDIRECTEUR, Kill() laisserait le
        # vrai python (enfant) orphelin.
        # Descendance relevée AVANT la mort de la racine (ensuite le lien de parenté
        # est perdu : taskkill /T seul a laissé le vrai python orphelin, mesuré).
        try {
            $desc = @(Get-DescendantsProcessus -Racines @($proc.Id))
            foreach ($d in $desc) { Stop-Process -Id ([int]$d.ProcessId) -Force -ErrorAction SilentlyContinue }
        } catch { }
        try { $proc.Kill() } catch { }
        Write-Host "python n'a pas rendu en $DelaiS s — abandonné (sous-processus tué)."
        return $null
    }
    $proc.WaitForExit()
    $lignes = @(($sortie.Result -split "`r?`n") | Where-Object { $_.Trim() })
    if ($lignes.Count -eq 0) { return $null }
    return $lignes[-1]
}

function Get-DecisionKeepIfFresh {
    # Décision PURE (testable) du mode -KeepIfFresh. Rend :
    #   sain       serveur frais, rien à faire
    #   demarrage  un process serveur JEUNE (< $Grace s) : ne rien purger
    #   stabilite  serveur périmé mais app/ modifié il y a < $Stabilite s : le
    #              --reload va rattraper, ne rien purger
    #   purger     sinon
    # $AgeServeurS / $AgeModifS : $null = inconnu (aucun process / aucun fichier).
    # $Actif : le CPU des process serveur PROGRESSE (worker vivant qui travaille,
    # p. ex. préchauffage Whisper) alors que le port est fermé -> ne pas purger.
    param([bool]$Sain, $AgeServeurS, $AgeModifS, [bool]$Repond, [int]$Grace, [int]$Stabilite, [bool]$Actif = $false)
    if ($Repond -and $Sain) { return "sain" }
    if ($null -ne $AgeServeurS -and [double]$AgeServeurS -lt $Grace) { return "demarrage" }
    if (-not $Repond -and $Actif) { return "demarrage" }
    if ($Repond -and $null -ne $AgeModifS -and [double]$AgeModifS -lt $Stabilite) { return "stabilite" }
    return "purger"
}

function Get-AgeServeurS {
    # Âge (s) du process RACINE uvicorn le plus jeune de ce repo et de ce port.
    # Le worker spawn n'est PAS reconnaissable à « uvicorn app.main » (sa ligne
    # de commande est « -c from multiprocessing.spawn import spawn_main… ») :
    # on ne s'appuie que sur la racine, le worker est jugé par son CPU (ci-dessous).
    param([int]$NumPort)
    $creations = @(Get-ProcessusServeur -NumPort $NumPort |
        Where-Object { $_.CommandLine -like "*uvicorn app.main*" } |
        ForEach-Object { $_.CreationDate } | Where-Object { $_ })
    if ($creations.Count -eq 0) { return $null }
    $plusRecent = ($creations | Sort-Object -Descending | Select-Object -First 1)
    return [math]::Max(0, ((Get-Date) - $plusRecent).TotalSeconds)
}

function Get-CpuServeurS {
    # CPU cumulé (s) de la racine ET de toute sa descendance (workers inclus).
    param([int]$NumPort)
    $total = 0.0
    foreach ($p in @(Get-ProcessusServeur -NumPort $NumPort)) {
        $total += ([double]$p.UserModeTime + [double]$p.KernelModeTime) / 1e7
    }
    return $total
}

function Get-AgeModifAppS {
    # Âge (s) de la dernière modification d'un fichier SOUS app/ (hors __pycache__).
    $dernier = Get-ChildItem -Path (Join-Path $racine "app") -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' } |
        Measure-Object LastWriteTime -Maximum
    if (-not $dernier.Maximum) { return $null }
    return [math]::Max(0, ((Get-Date) - $dernier.Maximum).TotalSeconds)
}

function Resolve-DecisionKeepIfFresh {
    # Mesure l'état ACTUEL puis décide. À rappeler juste avant d'agir : une
    # décision vieille de 2 min a tué un serveur devenu sain (2026-10-15).
    param([int]$NumPort)
    $repond = Test-PortRepond -NumPort $NumPort
    $sain = $false
    if ($repond) {
        $sain = [bool]((Test-ContenuFrais -NumPort $NumPort) -and (Test-PythonFrais -NumPort $NumPort) -and
            ((Get-CodeRouteProtegee -NumPort $NumPort -Mdp $script:motDePasse) -eq 200))
    }
    $age = Get-AgeServeurS -NumPort $NumPort
    $modif = Get-AgeModifAppS
    $script:etatServeur = @{ Age = $age; Cpu = (Get-CpuServeurS -NumPort $NumPort) }
    $decision = Get-DecisionKeepIfFresh -Sain $sain -AgeServeurS $age -AgeModifS $modif `
        -Repond $repond -Grace $GraceDemarrageS -Stabilite $StabiliteS
    if ($decision -eq "purger" -and -not $repond -and $null -ne $age) {
        # Port fermé et serveur « vieux » : vivant (CPU qui progresse en 3 s) ou mort ?
        $avant = $script:etatServeur.Cpu
        Start-Sleep -Seconds 3
        $apres = Get-CpuServeurS -NumPort $NumPort
        $script:etatServeur.Cpu = $apres
        if (($apres - $avant) -gt 0.2) {
            $decision = Get-DecisionKeepIfFresh -Sain $sain -AgeServeurS $age -AgeModifS $modif `
                -Repond $repond -Grace $GraceDemarrageS -Stabilite $StabiliteS -Actif $true
        }
    }
    return $decision
}

function Invoke-RetestAvantKill {
    # Re-test de la décision juste avant de tuer (2026-10-15) : si le serveur est
    # devenu sain / jeune entre l'inventaire et le kill, Write-DecisionEtSortir sort.
    param([bool]$KeepIfFreshActif, [bool]$StopOnlyActif, [int]$NbIds, [int]$NumPort, [bool]$ApresKill = $false)
    if ($KeepIfFreshActif -and -not $StopOnlyActif -and $NbIds -gt 0) {
        $decision = Resolve-DecisionKeepIfFresh -NumPort $NumPort
        # Après NOTRE kill (passe 2) : un processus jeune « en démarrage » est le
        # respawn de ce qu'on vient de tuer, pas une preuve de santé : sortir en 0
        # laisserait un serveur peut-être mort. Seul « sain » autorise la sortie.
        if ($ApresKill -and $decision -ne "sain") { return }
        Write-DecisionEtSortir -Decision $decision -NumPort $NumPort
    }
}

function Write-DecisionEtSortir {
    # Les décisions qui ne purgent pas : message, puis exit 0.
    param([string]$Decision, [int]$NumPort)
    switch ($Decision) {
        "sain" { Write-Host "OK : serveur déjà frais (statique + python) sur http://127.0.0.1:$NumPort — conservé (-KeepIfFresh)."; exit 0 }
        "demarrage" {
            $age = "?"; $cpu = "?"
            if ($script:etatServeur) {
                if ($null -ne $script:etatServeur.Age) { $age = [int]$script:etatServeur.Age }
                $cpu = [int]$script:etatServeur.Cpu
            }
            Write-Host "Démarrage ou rechargement en cours depuis $age s (CPU des process serveur : $cpu s), rien purgé. Un démarrage peut durer plusieurs minutes (préchauffage Whisper). La prochaine instance retestera."
            exit 0
        }
        "stabilite" { Write-Host "Serveur périmé mais app/ modifié il y a moins de $StabiliteS s : le --reload va rattraper, rien purgé. La prochaine instance retestera."; exit 0 }
    }
}

function Test-ContenuFrais {
    # Preuve de fraîcheur : le contenu STATIQUE servi == le fichier sur disque,
    # comparé en OCTETS (DownloadString décoderait en Latin-1 sans charset
    # déclaré → faux positif sur les accents, vu au premier run réel). Au moins
    # UNE comparaison doit avoir réellement eu lieu (un renommage des deux
    # actifs ne doit pas valider dans le vide).
    param([int]$NumPort)
    $nbCompares = 0
    foreach ($actif in @("app.css", "busy.js")) {
        $disque = Join-Path $racine "app\static\$actif"
        if (-not (Test-Path $disque)) { continue }
        try { $servi = Get-OctetsHttp -Url "http://127.0.0.1:$NumPort/static/$actif" } catch { return $false }
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
    $b64 = Invoke-PythonBorne -Code $code -DelaiS 90
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
        $octets = Get-OctetsHttp -Url "http://127.0.0.1:$NumPort/__fraicheur" -Bearer $script:motDePasse
        $servie = ([System.Text.Encoding]::UTF8.GetString($octets) | ConvertFrom-Json).empreinte
        $disque = Invoke-PythonBorne -Code "from app.main import empreinte_code; print(empreinte_code())" -DelaiS 90
        if ($disque) { $disque = $disque.Trim() }
        return ($servie -and $disque -and $servie -eq $disque)
    } catch { return $false }
}

# Tests unitaires (dot-sourcing) : charger les FONCTIONS seulement, sans agir.
if ($env:SERVEUR_DEV_FONCTIONS_SEULES -eq "1") { return }

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

# ---- 0c. Verrou nommé (2026-10-15) : une seule instance agit à la fois ----
# -KeepIfFresh (tâche planifiée) : verrou tenu -> exit 0 sans rien faire. Lancement
# manuel : attente jusqu'à -VerrouAttenteS puis refus clair (code 3). -StopOnly et
# -CheckOnly n'en prennent pas (comportement inchangé). Un mutex est rendu par
# l'OS à la mort de son process : un script tué ne le garde pas.
$script:verrou = $null
$verrouPris = $false
if (-not $StopOnly) {
    $attenteMs = 0
    if (-not $KeepIfFresh) { $attenteMs = $VerrouAttenteS * 1000 }
    try {
        $script:verrou = New-Object System.Threading.Mutex($false, "Global\VSCode2-serveur-dev-$Port")
        $verrouPris = $script:verrou.WaitOne($attenteMs)
    }
    catch [System.Threading.AbandonedMutexException] { $verrouPris = $true }
    catch {
        # UnauthorizedAccessException (mutex d'un autre niveau d'élévation : tenu par
        # quelqu'un d'autre, NON reproduit ici) ou tout autre échec d'ouverture :
        # dans le doute le verrou n'est PAS pris -> refus propre, pas de crash.
        $verrouPris = $false
    }
    if (-not $verrouPris) {
        if ($KeepIfFresh) {
            Write-Host "Une autre instance de serveur-dev.ps1 tient le verrou du port $Port : rien fait (-KeepIfFresh)."
            exit 0
        }
        $host.UI.WriteErrorLine("Une autre instance de serveur-dev.ps1 tient le verrou du port $Port depuis plus de $VerrouAttenteS s : refus de lancer en parallèle (elle purge ou démarre peut-être). Réessayer, ou arrêter cette instance.")
        exit 3
    }
}
try {
# (Pas de ré-indentation du bloc try : diff lisible. Le finally relâche le verrou.)

# ---- 0. -KeepIfFresh (auto-start VS Code) : ne pas avorter un serveur sain ----
# Un POST de génération IA synchrone peut durer plusieurs minutes — une purge à
# chaque folderOpen le tuerait en vol. Conservé UNIQUEMENT si statique ET python
# servis == disque : un serveur au python périmé est purgé et relancé.
# (2026-10-15) Mais jamais un serveur qui démarre ou recharge (cf. l'en-tête).
if ($KeepIfFresh -and -not $StopOnly) {
    $decision = Resolve-DecisionKeepIfFresh -NumPort $Port
    Write-DecisionEtSortir -Decision $decision -NumPort $Port
    if (Test-PortRepond -NumPort $Port) { Write-Host "Serveur présent mais contenu périmé — purge et relance." }
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
# Journaux .err à relire : celui par défaut ET les journaux horodatés de repli
# (journal verrouillé au lancement précédent) — sans eux, le PID du reloader
# de ce lancement-là était perdu et son worker muet jamais purgé (revue
# 2026-09-29).
$journauxErr = @(($journal + ".err")) + @(Get-ChildItem -Path $env:TEMP -Filter ("uvicorn_dev_" + $Port + "_*.log.err") -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
foreach ($passe in 1..2) {
    $aTuer = @(Get-ProcessusServeur -NumPort $Port) + @(Get-WorkersOrphelinsDuPort -NumPort $Port) +
             @($journauxErr | ForEach-Object { Get-WorkersOrphelinsDuJournal -JournalErr $_ })
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
    # (2026-10-15) RE-TEST juste avant de tuer (et donc aussi entre les deux
    # passes) : l'inventaire ci-dessus peut avoir pris des secondes à des
    # minutes sous charge, pendant lesquelles le serveur a pu devenir sain.
    Invoke-RetestAvantKill -KeepIfFreshActif $KeepIfFresh.IsPresent -StopOnlyActif $StopOnly.IsPresent -NbIds $ids.Count -NumPort $Port -ApresKill ($passe -gt 1)
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
# Journaux horodatés de repli : relus plus haut pour purger leurs workers,
# ils ne servent plus ensuite. On garde les 3 plus récents (diagnostic), le
# reste part — sinon ils s'accumulent et chaque lancement les relit tous
# (revue 2026-09-30). Un fichier encore tenu ouvert est laissé sans bruit.
Get-ChildItem -Path $env:TEMP -Filter ("uvicorn_dev_" + $Port + "_*.log*") -ErrorAction SilentlyContinue |
    Group-Object { $_.Name -replace '\.err$', '' } |
    Sort-Object { ($_.Group | Measure-Object LastWriteTime -Maximum).Maximum } -Descending |
    Select-Object -Skip 3 |
    ForEach-Object { $_.Group | Remove-Item -Force -ErrorAction SilentlyContinue }
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
# -AttenteDemarrageS (300 s par défaut) et non 30 (2026-10-15) : un démarrage sous charge dépasse la minute.
foreach ($i in 1..$AttenteDemarrageS) {
    Start-Sleep -Seconds 1
    if (Test-PortRepond -NumPort $Port) { $pret = $true; break }
    if ($proc.HasExited) { break }
}
if (-not $pret) {
    # PAS de Stop-Process ici : le processus lancé continue de démarrer.
    Write-Error ("Le serveur n'a pas répondu en $AttenteDemarrageS s — il a PEUT-ÊTRE simplement démarré lentement " +
                 "(préchauffage Whisper sous charge : ce n'est pas forcément un échec). Il N'A PAS été tué (PID $($proc.Id)) : " +
                 "revérifier avec -CheckOnly avant de le relancer. Journaux : $journal et $journal.err")
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

} finally {
    # Toujours relâché, quel que soit le `exit` ou l'erreur ci-dessus.
    if ($verrouPris -and $script:verrou) {
        try { $script:verrou.ReleaseMutex() } catch { }
    }
    if ($script:verrou) { $script:verrou.Dispose() }
}
