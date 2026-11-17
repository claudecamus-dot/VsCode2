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
# raté juste après le démarrage) est épargné jusqu'à -GraceDemarrageS (120 s)
# avec le message « démarrage en cours » ; il n'est purgé qu'ensuite. Ramenée
# de 900 à 120 s (Story 3.1, D-7) : depuis le 2026-11-05 les préchauffages
# Whisper/Ollama tournent dans des fils d'arrière-plan (app/main.py, lifespan),
# le port s'ouvre dès les réconciliations faites ; 900 s épargnaient 15 min un
# worker périmé au nom d'un port fermé qui ne l'est plus.
# Paramètres numériques bornés au chargement (cf. Limiter-Entier). Le mutex
# Global\ entre une instance élevée et une non élevée peut lever
# UnauthorizedAccessException : traité comme « verrou tenu » (NON prouvé ici,
# il faudrait une élévation pour le reproduire).
#
# Usage :  powershell -ExecutionPolicy Bypass -File scripts/serveur-dev.ps1
#          [-Port 8020] [-StopOnly] [-KeepIfFresh] [-CheckOnly]
#          [-GraceDemarrageS 900] [-StabiliteS 120] [-AttenteDemarrageS 300]
#          [-VerrouAttenteS 60] [-PlafondActifS 1800]
#
# Codes de sortie :
#   0  succès (serveur lancé, conservé sain/en démarrage/en stabilisation,
#      -StopOnly fait, -CheckOnly OK, verrou tenu en -KeepIfFresh)
#   1  panne (KO -CheckOnly, venv introuvable, serveur mort ou pas prêt au démarrage...)
#   2  APP_AUTH_PASSWORD absent
#   3  verrou tenu par une autre instance (lancement manuel, après -VerrouAttenteS)
#   4  mot de passe indéterminé (python borné hors délai)
#   5  lecture du mot de passe en échec (python a rendu un code non nul)
#   6  -KeepIfFresh : état INDÉTERMINÉ (preuve de fraîcheur non établie, rien
#      purgé tant que le worker a moins de -PlafondActifS s ; au-delà : purge)

param(
    # 8020 : le port de l'utilisateur (arbitrage du 2026-09-29). Il était hanté
    # en juillet ; la cause (worker orphelin d'un redirecteur venv) est traitée
    # par la purge ci-dessous depuis le 2026-07-27.
    [int]$Port = 8020,
    [switch]$StopOnly,
    [switch]$KeepIfFresh,
    [switch]$CheckOnly,
    # 120 s (Story 3.1, D-7 ; 900 s avant) : la valeur de 2026-10-15 venait d'un
    # démarrage qui tenait le port FERMÉ >= 8 min pendant le préchauffage Whisper.
    # Depuis le 2026-11-05 ce préchauffage part en fil d'arrière-plan : le port
    # s'ouvre après init_db + réconciliations. 120 s couvre ce démarrage sous
    # charge (le plafond « actif port fermé » est -PlafondActifS, 1800 s) sans épargner
    # 15 min un worker périmé.
    [int]$GraceDemarrageS = 120,
    [int]$StabiliteS = 120,
    [int]$AttenteDemarrageS = 300,
    [int]$VerrouAttenteS = 60,
    # (Story 3.1 final review, F4) Ceiling of the « active, port closed » state,
    # on the youngest server process (the worker). It used to be 2 x Grace, so
    # lowering the grace 900 -> 120 s silently lowered it 1800 -> 240 s: a Whisper
    # warm-up burning CPU before the port opens was purged after 4 min.
    [int]$PlafondActifS = 1800
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
$PlafondActifS = Limiter-Entier $PlafondActifS 0 86400
$racine = Split-Path -Parent $PSScriptRoot   # scripts/ -> racine du repo
$python = Join-Path $racine ".venv\Scripts\python.exe"
# Interpréteur surchargeable (tests en copie du dépôt, sans .venv) ; usage normal : le venv.
if ($env:SERVEUR_DEV_PYTHON) { $python = $env:SERVEUR_DEV_PYTHON }
$journal = Join-Path $env:TEMP ("uvicorn_dev_" + $Port + ".log")
# (Story 3.1, D-9) Dated history of launches, purges and deaths: the 8020 site
# died several times with no trace of WHEN nor of what this script did.
$historique = Join-Path $env:TEMP ("serveur-dev_" + $Port + ".history.log")
if ($env:SERVEUR_DEV_HISTORIQUE) { $historique = $env:SERVEUR_DEV_HISTORIQUE }

# Delay of the python probes (s); overridable for tests only (default 90).
$script:DelaiPythonS = 90
# (Story 3.1 final review, F10) Parsed defensively: `[int]'abc'` threw at load
# under EAP Stop and the script died before doing anything.
$delaiLu = 0
if ($env:SERVEUR_DEV_DELAI_PYTHON_S -and [int]::TryParse($env:SERVEUR_DEV_DELAI_PYTHON_S, [ref]$delaiLu)) {
    $script:DelaiPythonS = Limiter-Entier $delaiLu 1 3600
}

function Format-EtatPython {
    # (Story 3.1 review) A probe that timed out is « indéterminé », not False.
    param($Frais)
    if (-not $Frais -and $script:pythonIndetermine) { return "indéterminé (python hors délai)" }
    return "$Frais"
}

function Get-FinJournalErr {
    # Last lines of a .err journal, read SHARED (the dead process may still hold it).
    param([string]$Chemin, [int]$NbLignes = 20)
    try {
        $fs = [System.IO.File]::Open($Chemin, 'Open', 'Read', 'ReadWrite')
        try { $texte = (New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)).ReadToEnd() } finally { $fs.Dispose() }
        return ((@($texte -split "`r?`n") | Select-Object -Last $NbLignes) -join "`r`n")
    } catch { return "<journal illisible : $($_.Exception.Message)>" }
}

function Write-Historique {
    # One dated line per event; never fatal (a locked history must not stop the script).
    param([string]$Message)
    try {
        $ligne = (Get-Date -Format "yyyy-MM-ddTHH:mm:ss") + " pid=$PID " + $Message
        [System.IO.File]::AppendAllText($historique, $ligne + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
    } catch { }
}

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
                # Bounded number (Story 3.1, D-6): « *parent_pid=123* » also
                # matched parent_pid=1234, the worker of ANOTHER server — the
                # fix of Get-WorkersOrphelinsDuJournal was never ported here.
                [int]$_.ParentProcessId -eq $id -or $_.CommandLine -match "parent_pid=$id(\D|$)"
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
    try { $proc = [System.Diagnostics.Process]::Start($psi) }
    catch {
        # (Story 3.1 final review, F5) Interpreter missing or not startable: under
        # EAP Stop this threw out of the script (exit 1). It is a python FAILURE,
        # never « absent » nor « périmé ».
        Write-Host ("python introuvable ou non lançable ($python) : " + $_.Exception.Message)
        $script:pythonEchec = $true; $script:pythonCode = "non lancé"
        return $null
    }
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
        # (Story 3.1, D-8) A TIMEOUT is not an answer: callers must read
        # « indéterminé », never « mot de passe absent » nor « périmé ».
        $script:pythonIndetermine = $true
        return $null
    }
    $proc.WaitForExit()
    # (Story 3.1 review) python RAN and FAILED (ImportError...): neither a
    # timeout nor an answer — callers must not read it as « absent ».
    if ($proc.ExitCode -ne 0) { $script:pythonEchec = $true; $script:pythonCode = $proc.ExitCode }
    $lignes = @(($sortie.Result -split "`r?`n") | Where-Object { $_.Trim() })
    if ($lignes.Count -eq 0) {
        # Show python's stderr instead of failing silently (D-8).
        $err = "$($erreur.Result)".Trim()
        if ($err) { Write-Host ("python (code $($proc.ExitCode)) stderr : " + ((@($err -split "`r?`n") | Select-Object -Last 5) -join " | ")) }
        return $null
    }
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
    param([bool]$Sain, $AgeServeurS, $AgeModifS, [bool]$Repond, [int]$Grace, [int]$Stabilite, [bool]$Actif = $false, $AgeWorkerS = $null, [int]$PlafondActif = 1800)
    if ($Repond -and $Sain) { return "sain" }
    if ($null -ne $AgeServeurS -and [double]$AgeServeurS -lt $Grace) { return "demarrage" }
    # Plafond sur l'âge du process serveur le PLUS JEUNE ($AgeWorkerS : le worker, relancé à chaque
    # reload), jamais sur $AgeServeurS = racine/reloader qui vit des jours : un worker en plein
    # (re)démarrage est jeune ; un worker gelé qui brûle du CPU port fermé finit par dépasser
    # $PlafondActif (1800 s, réglage PROPRE : plus 2 x $Grace — Story 3.1 final review, F4).
    # $AgeWorkerS = $null (inconnu) : pas de plafond, comportement d'origine.
    if (-not $Repond -and $Actif) {
        if ($null -ne $AgeWorkerS -and [double]$AgeWorkerS -ge $PlafondActif) { return "purger" }
        return "demarrage"
    }
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

function Get-AgeDepuisCreations {
    # Âge (s) de la création la plus RÉCENTE de la liste ; $null si aucune date lisible.
    param([object[]]$Creations)
    $dates = @($Creations | Where-Object { $_ })
    if ($dates.Count -eq 0) { return $null }
    $plusRecent = ($dates | Sort-Object -Descending | Select-Object -First 1)
    return [math]::Max(0, ((Get-Date) - $plusRecent).TotalSeconds)
}

function Select-WorkersUvicorn {
    # (Story 3.1 re-review 2026-11-17, A2) Les workers UVICORN seuls, jamais toute
    # la descendance : un enfant court (ffmpeg, worker spawn de transcription)
    # remettait l'âge à ~0 et un serveur malade qui engendre encore des enfants
    # n'atteignait jamais le plafond. Worker uvicorn = process spawn dont le
    # `parent_pid=` désigne un process « uvicorn app.main » (le reloader). Sans
    # --reload (aucun worker de ce type) : les process uvicorn eux-mêmes.
    param([object[]]$Processus)
    $uvicorn = @($Processus | Where-Object { $_.CommandLine -like "*uvicorn app.main*" })
    $idsUvicorn = @($uvicorn | ForEach-Object { [int]$_.ProcessId })
    $workers = @($Processus | Where-Object {
        $_.CommandLine -like "*multiprocessing*" -and $_.CommandLine -match 'parent_pid=(\d+)(\D|$)' -and
        ($idsUvicorn -contains [int]$Matches[1])
    })
    if ($workers.Count -gt 0) { return $workers }
    return $uvicorn
}

function Get-InfoProcessus {
    # Win32_Process d'un PID, ou $null s'il n'existe pas (ou n'est pas lisible).
    param([int]$Id)
    return (@(Get-CimInstance Win32_Process -Filter "ProcessId=$Id" -ErrorAction SilentlyContinue) |
        Select-Object -First 1)
}

function Get-CreationProcessus {
    # Date de création d'un PID : CIM d'abord, puis Get-Process (StartTime), $null sinon.
    param([int]$Id, $Info = $null)
    if ($Info -and $Info.CreationDate) { return $Info.CreationDate }
    try {
        $p = Get-Process -Id $Id -ErrorAction Stop
        if ($p.StartTime) { return $p.StartTime }
    } catch { }
    return $null
}

function Get-AgeServeurParListener {
    # (Story 3.1 re-review 2026-11-17, A1) Repli quand le filtre de racine ne
    # trouve rien (racine/redirecteur mort avec un worker orphelin qui sert
    # encore, ou ExecutablePath/CommandLine nuls pour un process élevé vu d'un
    # shell non élevé) : on part du PID qui ÉCOUTE sur le port. Ses workers spawn
    # (marqueur parent_pid=) s'il est mort, s'il est un reloader (--reload) ou si
    # sa ligne de commande est illisible ; sinon le listener lui-même.
    param([int]$NumPort)
    $creations = @()
    foreach ($c in @(Get-NetTCPConnection -LocalPort $NumPort -State Listen -ErrorAction SilentlyContinue)) {
        $pidL = [int]$c.OwningProcess
        if ($pidL -le 0) { continue }
        $info = Get-InfoProcessus -Id $pidL
        $cmd = if ($info) { [string]$info.CommandLine } else { "" }
        if (-not $info -or -not $cmd -or $cmd -like "*--reload*") {
            $dates = @(Get-WorkersDeParent -PidParent $pidL | ForEach-Object { $_.CreationDate } | Where-Object { $_ })
            if ($dates.Count -gt 0) { $creations += $dates; continue }
        }
        $creation = Get-CreationProcessus -Id $pidL -Info $info
        if ($creation) { $creations += $creation }
    }
    return (Get-AgeDepuisCreations -Creations $creations)
}

function Get-AgeWorkerS {
    # Âge (s) du worker uvicorn le plus jeune de ce repo et de ce port (A2 : pas
    # des enfants courts) ; repli sur le PID à l'écoute (A1). $null = illisible.
    param([int]$NumPort)
    $workers = @(Select-WorkersUvicorn -Processus @(Get-ProcessusServeur -NumPort $NumPort))
    $age = Get-AgeDepuisCreations -Creations @($workers | ForEach-Object { $_.CreationDate })
    if ($null -ne $age) { return $age }
    return (Get-AgeServeurParListener -NumPort $NumPort)
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
    $script:pythonIndetermine = $false
    # (Story 3.1 final review, F3) Every probe that could not ANSWER (python
    # failed/timed out, HTTP timeout) sets this flag; reset both per decision.
    $script:preuveIndeterminee = $false
    $script:pythonEchec = $false
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
                -Repond $repond -Grace $GraceDemarrageS -Stabilite $StabiliteS -Actif $true `
                -AgeWorkerS (Get-AgeWorkerS -NumPort $NumPort) -PlafondActif $PlafondActifS
        }
    }
    # (Story 3.1, D-8) The freshness proof could not be computed (python timed
    # out under load): « périmé » would be a guess, and -KeepIfFresh purged a
    # healthy loaded server on that guess. Undetermined = nothing purged.
    # (F3) Same for a python probe that FAILED and for an HTTP probe without any
    # reply (timeout): only a definitive answer (bytes/fingerprint/code that
    # differ) may purge — the probes are short-circuited at the first « no ».
    if ($decision -eq "purger" -and ($script:pythonIndetermine -or $script:preuveIndeterminee -or $script:pythonEchec)) {
        # (Story 3.1 re-review, M1) Bounded: a sick server that still answers `/`
        # (thread pool saturated: async middleware OK, `def` routes time out)
        # used to stay « indetermine » forever under the -KeepIfFresh auto-start.
        $ageWorker = Get-AgeWorkerS -NumPort $NumPort
        $decision = Get-DecisionIndeterminee -AgeWorkerS $ageWorker -PlafondActif $PlafondActifS
        if ($decision -eq "purger") {
            Write-Historique "keepiffresh port=$NumPort decision=purger (indetermine, worker de $([int]$ageWorker) s >= plafond $PlafondActifS s)"
            # (A3) The undetermined state may date from ONE probe: say what is known.
            Write-Host "Serveur sans réponse probante et worker âgé de $([int]$ageWorker) s (>= $PlafondActifS s) : purge."
        } elseif ($null -eq $ageWorker) {
            # (A1) Unknown age: the ceiling cannot apply — say it, never silently.
            Write-Historique "keepiffresh port=$NumPort decision=indetermine (age du serveur illisible)"
            Write-Host "Âge du serveur illisible (aucun worker identifié, ni par le repo ni par le PID à l'écoute sur $NumPort) : plafond de $PlafondActifS s inapplicable, rien purgé."
        }
        return $decision
    }
    return $decision
}

function Get-DecisionIndeterminee {
    # Décision PURE (testable) quand la preuve de fraîcheur n'a pas pu être
    # établie (Story 3.1 re-review, M1). Ne rien purger tant que le worker (le
    # process serveur le plus JEUNE, relancé à chaque reload) a moins de
    # $PlafondActif s ; au-delà, l'indétermination n'est plus une charge passagère :
    # purger. $AgeWorkerS = $null (inconnu) : pas de plafond, « indetermine ».
    param($AgeWorkerS, [int]$PlafondActif = 1800)
    if ($null -ne $AgeWorkerS -and [double]$AgeWorkerS -ge $PlafondActif) { return "purger" }
    return "indetermine"
}

function Invoke-RetestAvantKill {
    # Limite connue (revue S5) : si la passe 1 n'avait tué que le reloader et qu'un
    # worker survivant servait du contenu frais, « sain » sort en 0 sans --reload.
    # La purge tue parent + descendance (Get-DescendantsProcessus) : cas résiduel
    # rare, la prochaine instance -KeepIfFresh le verrait sans reloader. Non corrigé.
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
    # Les décisions qui ne purgent pas : message, puis exit 0 (« indetermine » : exit 6).
    param([string]$Decision, [int]$NumPort)
    switch ($Decision) {
        "sain" { Write-Host "OK : serveur déjà frais (statique + python) sur http://127.0.0.1:$NumPort — conservé (-KeepIfFresh)."; exit 0 }
        "demarrage" {
            $age = "?"; $cpu = "?"
            if ($script:etatServeur) {
                if ($null -ne $script:etatServeur.Age) { $age = [int]$script:etatServeur.Age }
                $cpu = [int]$script:etatServeur.Cpu
            }
            Write-Host "Démarrage ou rechargement en cours depuis $age s (CPU des process serveur : $cpu s), rien purgé (grâce de démarrage : $GraceDemarrageS s ; les préchauffages Whisper/Ollama tournent en arrière-plan, port ouvert). La prochaine instance retestera."
            exit 0
        }
        "indetermine" {
            Write-Historique "keepiffresh port=$NumPort decision=indetermine (sonde python en echec/hors delai ou HTTP sans reponse)"
            # (Story 3.1 re-review, M2) Not a success: the site may be dead. Distinct
            # code 6 so the VS Code task does not show green; message on stderr too.
            $msg = "État indéterminé : la preuve de fraîcheur n'a pas pu être établie (python en échec ou hors délai, ou requête HTTP sans réponse — machine chargée ?), rien purgé (purge si le worker dépasse $PlafondActifS s). La prochaine instance retestera."
            Write-Host $msg
            [Console]::Error.WriteLine($msg)
            exit 6
        }
        "stabilite" { Write-Host "Serveur périmé mais app/ modifié il y a moins de $StabiliteS s : le --reload va rattraper, rien purgé. La prochaine instance retestera."; exit 0 }
        "purger" { return }
        default {
            # (Story 3.1 review) Safe default: an unknown decision (typo, new
            # state) must NEVER fall through into the purge.
            Write-Historique "keepiffresh port=$NumPort decision_inconnue=$Decision"
            Write-Host "Décision inconnue « $Decision » : rien purgé (par prudence). La prochaine instance retestera."
            exit 0
        }
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
        try { $servi = Get-OctetsHttp -Url "http://127.0.0.1:$NumPort/static/$actif" }
        catch {
            # (F3) No HTTP reply at all (timeout under load) is not « stale ».
            if (-not (Test-ReponseHttpRecue $_)) { $script:preuveIndeterminee = $true }
            return $false
        }
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
    $b64 = Invoke-PythonBorne -Code $code -DelaiS $script:DelaiPythonS
    if (-not $b64) { return $null }
    return [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64.Trim()))
}

function Test-MotDePassePresent {
    # Deterministic, python-free: is APP_AUTH_PASSWORD non-empty for the app?
    # Env variable SET (even empty) decides, as python-dotenv never overrides it;
    # otherwise the last `APP_AUTH_PASSWORD=` line of <Racine>/.env (optional
    # `export `, quotes, inline ` #` comment on unquoted values).
    # Returns $true / $false, or $null = UNKNOWN (.env unreadable, e.g. locked):
    # the caller then falls back to the python probe instead of guessing.
    param([string]$Racine)
    $env_val = [Environment]::GetEnvironmentVariable("APP_AUTH_PASSWORD")
    if ($null -ne $env_val) { return ($env_val -ne "") }
    $fichier = Join-Path $Racine ".env"
    if (-not (Test-Path -LiteralPath $fichier)) { return $false }
    try { $lignes = [System.IO.File]::ReadAllLines($fichier) } catch { return $null }
    $valeur = $null
    foreach ($ligne in $lignes) {
        if ($ligne -match '^\s*(?:export\s+)?APP_AUTH_PASSWORD\s*=\s*(.*)$') {
            $v = $Matches[1].Trim()
            # Quoted value: python-dotenv drops what follows the closing quote
            # (`"x" # c`, `""  # c`).
            if ($v -match '^"([^"]*)"(\s.*)?$' -or $v -match "^'([^']*)'(\s.*)?$") { $v = $Matches[1] }
            else { $v = ($v -replace '\s+#.*$', '').Trim() }
            $valeur = $v
        }
    }
    return ($null -ne $valeur -and $valeur -ne "")
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
    } catch {
        $e = $_.Exception
        while ($e) {
            if ($e -is [System.Net.WebException] -and $e.Response) { return [int]$e.Response.StatusCode }
            $e = $e.InnerException
        }
        # (F3) 0 = NO reply (timeout...): not an answer, the freshness verdict
        # built on it is undetermined.
        $script:preuveIndeterminee = $true
        return 0
    }
}

function Test-ReponseHttpRecue {
    # (F3) Did the failed request get an HTTP REPLY (4xx/5xx: a definitive
    # answer) or none at all (timeout, reset: undetermined)? PowerShell wraps
    # .NET exceptions (MethodInvocationException), hence the inner-chain walk.
    param($Erreur)
    $e = $Erreur.Exception
    while ($e) {
        if ($e -is [System.Net.WebException] -and $e.Response) { return $true }
        $e = $e.InnerException
    }
    return $false
}

function Test-PythonFrais {
    # /__fraicheur : empreinte capturée à l'IMPORT par le worker == empreinte du
    # disque (recalculée par le même code). C'est LA détection du --reload qui a
    # raté une modif — diagnostic superviseur 2026-07-23. La route est PROTÉGÉE
    # depuis le 2026-09-19 : sans l'en-tête, ce contrôle échouait toujours.
    param([int]$NumPort)
    try {
        $octets = Get-OctetsHttp -Url "http://127.0.0.1:$NumPort/__fraicheur" -Bearer $script:motDePasse
    } catch {
        # (F3) A 10 s timeout under load is not an answer; a 4xx/5xx reply is.
        if (-not (Test-ReponseHttpRecue $_)) { $script:preuveIndeterminee = $true }
        return $false
    }
    try {
        $servie = ([System.Text.Encoding]::UTF8.GetString($octets) | ConvertFrom-Json).empreinte
        $disque = Invoke-PythonBorne -Code "from app.main import empreinte_code; print(empreinte_code())" -DelaiS $script:DelaiPythonS
        if ($disque) { $disque = $disque.Trim() }
        # (F3) python failed (non-zero exit), timed out or printed nothing: the
        # disk fingerprint is UNKNOWN — never read that as « stale ».
        if (-not $disque) { $script:preuveIndeterminee = $true; return $false }
        return ($servie -and $servie -eq $disque)
    } catch { return $false }
}

# Tests unitaires (dot-sourcing) : charger les FONCTIONS seulement, sans agir.
if ($env:SERVEUR_DEV_FONCTIONS_SEULES -eq "1") { return }

# ---- 0a. Mot de passe : AVANT toute purge — ne jamais tuer un serveur pour en
# relancer un qui rendrait 503 partout. ----
$script:motDePasse = $null
if (-not $StopOnly) {
    $script:pythonIndetermine = $false
    # (Story 3.1) « Absent » is decided WITHOUT python: a probe that times out
    # under load used to turn into a false « absent » (D-8), and the reverse
    # fix made a truly absent password depend on that same probe (85 s measured
    # under load against a 90 s limit). Same rule as the app: an env variable,
    # even empty, wins over .env (python-dotenv never overrides it).
    $script:pythonEchec = $false
    if ($false -eq (Test-MotDePassePresent -Racine $racine)) {
        Write-Historique "refus port=$Port APP_AUTH_PASSWORD absent"
        $host.UI.WriteErrorLine(("APP_AUTH_PASSWORD absent (ni variable d'environnement ni .env) : le site " +
                     "rendrait 503 sur toute page hors /connexion. Poser la ligne " +
                     "APP_AUTH_PASSWORD=... dans .env puis relancer. Rien n'a été arrêté."))
        exit 2
    }
    $script:motDePasse = Get-MotDePasseApp
    if (-not $script:motDePasse -and $script:pythonIndetermine) {
        # (Story 3.1, D-8) Timeout is not « absent »: say so, purge nothing.
        Write-Historique "refus port=$Port mot de passe indetermine (python borne hors delai)"
        $host.UI.WriteErrorLine("Mot de passe de l'app INDÉTERMINÉ : python n'a pas rendu dans le délai (machine chargée ?). Rien n'a été arrêté ; réessayer.")
        exit 4
    }
    if (-not $script:motDePasse -and $script:pythonEchec) {
        # (Story 3.1 review) python ran and FAILED (ImportError, broken venv...):
        # the password is declared, python is what is broken — not « absent ».
        Write-Historique "refus port=$Port lecture du mot de passe en echec (python code $script:pythonCode)"
        $host.UI.WriteErrorLine("Lecture du mot de passe de l'app en ÉCHEC : python a rendu le code $script:pythonCode (voir son stderr ci-dessus ; venv ou import cassé ?). APP_AUTH_PASSWORD est déclaré. Rien n'a été arrêté.")
        exit 5
    }
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
    $script:pythonIndetermine = $false
    $fraisPy = Test-PythonFrais -NumPort $Port
    $etatPy = Format-EtatPython -Frais $fraisPy
    if ($codeProtege -eq 200 -and $frais -and $fraisPy) {
        Write-Host "OK : http://127.0.0.1:$Port sert /missions authentifié (200), statique ET python frais."
        exit 0
    }
    Write-Error ("KO : /missions authentifié -> $codeProtege (503 = le SERVEUR n'a pas le mot de passe, " +
                 "à relancer ; 401 = mot de passe du serveur différent du .env), statique frais: $frais, " +
                 "python frais: $etatPy.")
    exit 1
}

# ---- 0c. Verrou nommé (2026-10-15) : une seule instance agit à la fois ----
# -KeepIfFresh (tâche planifiée) : verrou tenu -> exit 0 sans rien faire. Lancement
# manuel : attente jusqu'à -VerrouAttenteS puis refus clair (code 3). -StopOnly et
# -CheckOnly n'en prennent pas (comportement inchangé). Un mutex est rendu par
# l'OS à la mort de son process : un script tué ne le garde pas.
$script:verrou = $null
$script:erreurVerrou = $null
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
        $script:erreurVerrou = $_.Exception.GetType().Name + " : " + $_.Exception.Message
    }
    if (-not $verrouPris) {
        if ($KeepIfFresh) {
            Write-Host ("Une autre instance de serveur-dev.ps1 tient le verrou du port $Port : rien fait (-KeepIfFresh)." + $(if ($script:erreurVerrou) { " [ouverture du verrou impossible : $script:erreurVerrou]" }))
            exit 0
        }
        $host.UI.WriteErrorLine("Une autre instance de serveur-dev.ps1 tient le verrou du port $Port depuis plus de $VerrouAttenteS s : refus de lancer en parallèle (elle purge ou démarre peut-être). Réessayer, ou arrêter cette instance." + $(if ($script:erreurVerrou) { " [ouverture du verrou impossible, pas forcément tenu : $script:erreurVerrou]" }))
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
        Write-Historique "purge port=$Port passe=$passe ids=$($ids -join ',')"
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
Write-Historique "lancement port=$Port uvicorn_pid=$($proc.Id) journal=$journal"

# ---- 4. Health-check ----
$pret = $false
# -AttenteDemarrageS (300 s par défaut) et non 30 (2026-10-15) : un démarrage sous charge dépasse la minute.
foreach ($i in 1..$AttenteDemarrageS) {
    Start-Sleep -Seconds 1
    if (Test-PortRepond -NumPort $Port) { $pret = $true; break }
    if ($proc.HasExited) { break }
}
if (-not $pret -and $proc.HasExited) {
    # (Story 3.1, D-9) Already DEAD (e.g. SchemaEnRetard): it did not « start
    # slowly ». Say it, with its exit code and the end of its .err.
    $finErr = Get-FinJournalErr -Chemin ($journal + ".err")
    Write-Historique "mort port=$Port uvicorn_pid=$($proc.Id) code=$($proc.ExitCode)"
    $host.UI.WriteErrorLine("Le serveur est MORT au démarrage (PID $($proc.Id), code $($proc.ExitCode)). Fin de $journal.err :`r`n$finErr")
    exit 1
}
if (-not $pret) {
    # PAS de Stop-Process ici : le processus lancé continue de démarrer.
    Write-Historique "demarrage_lent port=$Port uvicorn_pid=$($proc.Id) attente=$AttenteDemarrageS"
    Write-Error ("Le serveur n'a pas répondu en $AttenteDemarrageS s — il a PEUT-ÊTRE simplement démarré lentement " +
                 "(machine très chargée, ou init_db/réconciliations lentes — les préchauffages ne tiennent plus le port depuis le 2026-11-05 : à examiner dans les journaux). Il N'A PAS été tué (PID $($proc.Id)) : " +
                 "revérifier avec -CheckOnly avant de le relancer. Journaux : $journal et $journal.err")
    exit 1
}

# ---- 5. Preuve de fraîcheur (statique + PYTHON) + unicité du listener ----
# Python : /__fraicheur renvoie l'empreinte capturée à l'IMPORT par le worker ;
# on la compare à l'empreinte recalculée du DISQUE (même algorithme, app.main).
# C'est LA preuve que le --reload n'a pas servi du code périmé (diagnostic
# superviseur 2026-07-23 — la preuve octets ne couvrait que le statique).
$frais = Test-ContenuFrais -NumPort $Port
$script:pythonIndetermine = $false
$fraisPy = Test-PythonFrais -NumPort $Port
$etatPy = Format-EtatPython -Frais $fraisPy
$codeProtege = Get-CodeRouteProtegee -NumPort $Port -Mdp $script:motDePasse
$nbEcoute = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique).Count
if ($frais -and $fraisPy -and $codeProtege -eq 200 -and $nbEcoute -eq 1) {
    Write-Host "OK : serveur FRAIS sur http://127.0.0.1:$Port (1 seul listener, statique ET python servis = disque, /missions authentifié = 200)."
} else {
    Write-Error "Serveur lancé mais suspect (listeners uniques: $nbEcoute, statique frais: $frais, python frais: $etatPy, /missions authentifié: $codeProtege) — ne pas s'en servir tel quel."
    exit 1
}

} finally {
    # Toujours relâché, quel que soit le `exit` ou l'erreur ci-dessus.
    if ($verrouPris -and $script:verrou) {
        try { $script:verrou.ReleaseMutex() } catch { }
    }
    if ($script:verrou) { $script:verrou.Dispose() }
}
