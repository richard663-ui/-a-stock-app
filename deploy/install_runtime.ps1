<#
.SYNOPSIS
  Install, inspect or roll back the pinned AStock QMT background runtime.

.DESCRIPTION
  Only an exact 40-character commit id can be installed. Code is fetched from
  GitHub (codeload archive of that commit) or from a local git clone (git
  archive of that commit), staged under releases\<commit>, given a SHA-256
  manifest and tested with the same Python the services use. Only after every
  check passes are the services stopped, the "current" junction switched and
  the watchdogs regenerated. Each watchdog re-verifies the manifest before every
  (re)start, so code that differs from the reviewed commit never runs.

  Market data, models, logs and secrets are never touched:
    %USERPROFILE%\AStockData, %USERPROFILE%\.a_stock_qmt, <root>\runtime\*.log

  Examples
    install_runtime.ps1 -Status
    install_runtime.ps1 -Commit <40-hex> -Source C:\Users\me\AStockDev\a-stock-app
    install_runtime.ps1 -Commit <40-hex>                 (downloads from GitHub)
    install_runtime.ps1 -Rollback                         (previous pinned release)
#>
[CmdletBinding()]
param(
    [string]$Commit = '',
    [string]$Source = 'github',
    [switch]$Rollback,
    [switch]$Status,
    [switch]$AllowDuringMarketHours,
    [switch]$NoStart,
    [switch]$SkipPreflight,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'AStockQMT'),
    [string]$PythonExe = '',
    [string]$StartupDir = '',
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
$Repo = 'richard663-ui/-a-stock-app'
$DefaultRoot = Join-Path $env:LOCALAPPDATA 'AStockQMT'
$IsDefaultRoot = ([IO.Path]::GetFullPath($InstallRoot).TrimEnd('\') -ieq [IO.Path]::GetFullPath($DefaultRoot).TrimEnd('\'))
if (-not $StartupDir) {
    if ($IsDefaultRoot) { $StartupDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup' }
    else { $StartupDir = Join-Path $InstallRoot 'startup_test' }
}
$Releases = Join-Path $InstallRoot 'releases'
$Current = Join-Path $InstallRoot 'current'
$RuntimeDir = Join-Path $InstallRoot 'runtime'
$BinDir = Join-Path $InstallRoot 'bin'
$StatePath = Join-Path $InstallRoot 'install_state.json'
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'

function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }

function Get-ShanghaiNow { [DateTimeOffset]::UtcNow.ToOffset([TimeSpan]::FromHours(8)) }

function Test-MarketWindow {
    $now = Get-ShanghaiNow
    if ($now.DayOfWeek -eq 'Saturday' -or $now.DayOfWeek -eq 'Sunday') { return $false }
    $minute = $now.Hour * 60 + $now.Minute
    return ($minute -ge (9 * 60 + 5) -and $minute -le (15 * 60 + 5))
}

function Read-State {
    if (Test-Path -LiteralPath $StatePath) {
        return (Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json)
    }
    return $null
}

function Write-State($state) {
    $tmp = "$StatePath.tmp"
    $state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $StatePath -Force
}

function Resolve-Python {
    if ($PythonExe) { $candidates = @($PythonExe) } else { $candidates = @() }
    $watchdog = Join-Path $RuntimeDir 'AStockQMTCloudBridgeWatchdog.cmd'
    if (Test-Path -LiteralPath $watchdog) {
        $m = [regex]::Match((Get-Content -LiteralPath $watchdog -Raw), '"([^"]*python\.exe)"')
        if ($m.Success) { $candidates += $m.Groups[1].Value }
    }
    $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    foreach ($c in $candidates) { if ($c -and (Test-Path -LiteralPath $c)) { return (Resolve-Path -LiteralPath $c).Path } }
    throw 'Python 3.12 for the QMT services was not found. Pass -PythonExe.'
}

function Resolve-Git {
    $c = Get-Command git -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $mingit = Join-Path $env:LOCALAPPDATA 'Programs\MinGit\cmd\git.exe'
    if (Test-Path -LiteralPath $mingit) { return $mingit }
    throw 'git was not found (needed for -Source <local clone>).'
}

function Invoke-Logged([string]$exe, [string[]]$arguments, [string]$workDir, [hashtable]$envVars, [int]$timeoutSeconds, [string]$logFile) {
    $saved = @{}
    foreach ($k in $envVars.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k, 'Process'); [Environment]::SetEnvironmentVariable($k, $envVars[$k], 'Process') }
    try {
        $quoted = $arguments | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }
        $p = Start-Process -FilePath $exe -ArgumentList ($quoted -join ' ') -WorkingDirectory $workDir -NoNewWindow -PassThru `
            -RedirectStandardOutput $logFile -RedirectStandardError "$logFile.err"
        $handle = $p.Handle
        if (-not $p.WaitForExit($timeoutSeconds * 1000)) { $p.Kill(); return 124 }
        $p.Refresh()
        return $p.ExitCode
    } finally {
        foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process') }
    }
}

function Get-ReleaseTree([string]$commit, [string]$destination) {
    $work = Join-Path $InstallRoot "staging\$commit-$Stamp"
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    $zip = Join-Path $work 'release.zip'
    if ($Source -ieq 'github') {
        $url = "https://codeload.github.com/$Repo/zip/$commit"
        Say "Downloading $url"
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    } else {
        $git = Resolve-Git
        $type = (& $git -C $Source cat-file -t "$commit" 2>$null)
        if ($LASTEXITCODE -ne 0 -or "$type".Trim() -ne 'commit') { throw "Commit $commit does not exist in $Source" }
        & $git -C $Source archive --format=zip --prefix=release/ -o $zip $commit
        if ($LASTEXITCODE -ne 0) { throw 'git archive failed' }
    }
    $extract = Join-Path $work 'x'
    Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
    $top = @(Get-ChildItem -LiteralPath $extract -Directory)
    if ($top.Count -ne 1) { throw 'Unexpected archive layout' }
    Move-Item -LiteralPath $top[0].FullName -Destination $destination
    Remove-Item -LiteralPath $work -Recurse -Force
}

function Get-Services([string]$releaseDir) {
    $path = Join-Path $releaseDir 'deploy\services.json'
    if (-not (Test-Path -LiteralPath $path)) { throw "deploy\services.json missing in $releaseDir" }
    return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Invoke-Preflight([string]$py, [string]$releaseDir, $registry) {
    $logDir = Join-Path $RuntimeDir "install_logs\$Stamp"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $scratch = Join-Path $logDir 'scratch_runtime'
    New-Item -ItemType Directory -Force -Path $scratch | Out-Null
    $envVars = @{ PYTHONPATH = $releaseDir; PYTHONDONTWRITEBYTECODE = '1'; ASTOCK_RUNTIME_DIR = $scratch; ASTOCK_INSTALL_ROOT = '' }
    $i = 0
    foreach ($cmd in $registry.preflight) {
        $i++
        $log = Join-Path $logDir ("preflight_{0:00}.log" -f $i)
        $code = Invoke-Logged $py ([string[]]$cmd) $releaseDir $envVars 600 $log
        if ($code -ne 0) { throw ("Preflight failed ({0}): {1}  log: {2}" -f $code, ($cmd -join ' '), $log) }
        Say ("  pass  " + ($cmd -join ' ')) 'Green'
    }
}

function Test-ReleaseManifest([string]$py, [string]$releaseDir) {
    $out = & $py -I (Join-Path $releaseDir 'deploy\verify_install.py') check --release $releaseDir
    if ($LASTEXITCODE -ne 0) { throw "Release tree does not match its manifest: $out" }
}

function Get-AStockProcesses {
    # Workers: python started from this install root. Watchdogs: cmd.exe running
    # runtime\AStock*Watchdog.cmd. Installer/rollback launcher windows are excluded.
    $root = [regex]::Escape($InstallRoot)
    $watchdogs = [regex]::Escape($RuntimeDir) + '\\AStock[A-Za-z0-9]*Watchdog\.cmd'
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and (
            (($_.Name -in @('python.exe', 'pythonw.exe')) -and $_.CommandLine -match $root) -or
            ($_.Name -eq 'cmd.exe' -and $_.CommandLine -match $watchdogs)
        )
    })
}

function Stop-AStockServices {
    $procs = Get-AStockProcesses
    # Watchdogs first so they cannot restart workers, then the workers.
    foreach ($p in ($procs | Where-Object { $_.Name -eq 'cmd.exe' })) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
    foreach ($p in ($procs | Where-Object { $_.Name -ne 'cmd.exe' })) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    $left = @(Get-AStockProcesses)
    if ($left.Count) { throw ("Could not stop: " + (($left | ForEach-Object { $_.ProcessId }) -join ', ')) }
    Say "Stopped $($procs.Count) AStock processes."
}

function Move-LegacyFlatLayout {
    $flat = Join-Path $InstallRoot 'services'
    if (-not (Test-Path -LiteralPath $flat)) { return }
    $legacy = Join-Path $InstallRoot "legacy_flat_$Stamp"
    New-Item -ItemType Directory -Force -Path $legacy | Out-Null
    foreach ($name in @('services', 'modules', 'configs', 'docs')) {
        $p = Join-Path $InstallRoot $name
        if (Test-Path -LiteralPath $p) { Move-Item -LiteralPath $p -Destination (Join-Path $legacy $name) }
    }
    Get-ChildItem -LiteralPath $InstallRoot -File | Where-Object { $_.Extension -in @('.bat', '.cmd', '.ps1') } |
        ForEach-Object { Move-Item -LiteralPath $_.FullName -Destination (Join-Path $legacy $_.Name) }
    Say "Pre-pinning flat runtime moved to $legacy (restorable)." 'Yellow'
}

function Set-CurrentJunction([string]$target) {
    if (Test-Path -LiteralPath $Current) {
        $item = Get-Item -LiteralPath $Current -Force
        if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "$Current exists and is not a junction" }
        cmd /c rmdir "$Current" | Out-Null
    }
    cmd /c mklink /J "$Current" "$target" | Out-Null
    if (-not (Test-Path -LiteralPath (Join-Path $Current 'deploy\services.json'))) { throw 'Junction switch failed' }
}

function Write-Launchers([string]$py, $registry) {
    New-Item -ItemType Directory -Force -Path $RuntimeDir, $BinDir, $StartupDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $Current 'deploy\verify_install.py') -Destination (Join-Path $BinDir 'verify_install.py') -Force
    $verify = Join-Path $BinDir 'verify_install.py'
    foreach ($svc in $registry.services) {
        $log = Join-Path $RuntimeDir $svc.log
        $script = Join-Path $Current ($svc.script -replace '/', '\')
        $stopLine = ''
        if ($null -ne $svc.stop_exit_code) { $stopLine = "if ""%RC%""==""$($svc.stop_exit_code)"" exit /b 0" }
        $lines = @(
            '@echo off',
            'setlocal',
            "set ""ASTOCK_INSTALL_ROOT=$InstallRoot""",
            "set ""ASTOCK_RUNTIME_DIR=$RuntimeDir""",
            "set ""PYTHONPATH=$Current;$InstallRoot\vendor""",
            'set "PYTHONDONTWRITEBYTECODE=1"',
            'set "NO_PROXY=.supabase.co,supabase.co"',
            'set "no_proxy=.supabase.co,supabase.co"',
            ':astock_loop',
            "cd /d ""$Current""",
            """$py"" -I -u ""$verify"" verify --install-root ""$InstallRoot"" --service $($svc.id) --quiet >> ""$log"" 2>&1",
            'if errorlevel 1 (',
            '  timeout /t 60 /nobreak >nul',
            '  goto astock_loop',
            ')',
            """$py"" -u ""$script"" >> ""$log"" 2>&1",
            'set "RC=%ERRORLEVEL%"',
            "echo [%DATE% %TIME%] $($svc.id) exited %RC% - restart in $($svc.restart_seconds)s >> ""$log""",
            $stopLine,
            "timeout /t $($svc.restart_seconds) /nobreak >nul",
            'goto astock_loop'
        ) | Where-Object { $_ -ne '' }
        Set-Content -LiteralPath (Join-Path $RuntimeDir $svc.watchdog) -Value $lines -Encoding ASCII
        $vbs = @(
            'Set sh = CreateObject("WScript.Shell")',
            ('sh.Run "cmd.exe /d /c ""' + (Join-Path $RuntimeDir $svc.watchdog) + '""", 0, False')
        )
        Set-Content -LiteralPath (Join-Path $StartupDir $svc.startup) -Value $vbs -Encoding ASCII
    }
    $ps = 'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File'
    Set-Content -LiteralPath (Join-Path $InstallRoot 'START_MONITOR.cmd') -Encoding ASCII -Value @('@echo off', "$ps ""$Current\services\start_monitor.ps1"" %*")
    Set-Content -LiteralPath (Join-Path $InstallRoot 'CHECK_MONITOR.cmd') -Encoding ASCII -Value @('@echo off', "$ps ""$Current\services\start_monitor.ps1"" -CheckOnly %*")
    Set-Content -LiteralPath (Join-Path $InstallRoot 'ROLLBACK_RUNTIME.cmd') -Encoding ASCII -Value @('@echo off', "$ps ""$Current\deploy\install_runtime.ps1"" -Rollback %*")
    Set-Content -LiteralPath (Join-Path $InstallRoot 'PREPARE_MARKET_ALPHA.cmd') -Encoding ASCII -Value @(
        '@echo off', 'setlocal',
        "set ""PYTHONPATH=$Current;$InstallRoot\vendor""",
        """$py"" ""$Current\services\market_alpha_data.py"" --download --max-seconds 600",
        'if errorlevel 1 (', '  echo Daily data coverage is incomplete. Caches are preserved. Re-run to resume.', '  pause', '  exit /b 2', ')',
        """$py"" ""$Current\services\market_alpha_research.py""", 'pause')
    # QMT keeps only about 21 trading days of tick history: recover lost sessions within 3 weeks.
    Set-Content -LiteralPath (Join-Path $InstallRoot 'BACKFILL_TICKS.cmd') -Encoding ASCII -Value @(
        '@echo off', 'setlocal',
        "set ""PYTHONPATH=$Current;$InstallRoot\vendor""",
        """$py"" ""$Current\services\qmt_tick_backfill.py"" --build-samples %*", 'pause')
}

function Update-Shortcuts {
    if (-not $IsDefaultRoot) { return }
    $wsh = New-Object -ComObject WScript.Shell
    $old = Join-Path $InstallRoot 'services\start_monitor.ps1'
    $new = Join-Path $Current 'services\start_monitor.ps1'
    foreach ($file in @(Get-ChildItem -LiteralPath ([Environment]::GetFolderPath('Desktop')) -Filter '*.lnk' -ErrorAction SilentlyContinue)) {
        $link = $wsh.CreateShortcut($file.FullName)
        if ($link.Arguments -and $link.Arguments.IndexOf($old, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $link.Arguments = $link.Arguments.Replace($old, $new)
            $link.WorkingDirectory = $Current
            $link.Save()
            Say "Shortcut updated: $($file.Name)"
        }
    }
}

function Start-AStockServices {
    # Start every AStock launcher that exists, so a failed install can restart
    # whatever (old or new) layout is currently in place.
    foreach ($vbs in @(Get-ChildItem -LiteralPath $StartupDir -Filter 'AStock*.vbs' -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath 'wscript.exe' -ArgumentList ('"' + $vbs.FullName + '"')
    }
}

function Show-Status {
    $state = Read-State
    if (-not $state) { Say 'No pinned release installed (legacy flat layout or empty).' 'Yellow'; return }
    Say ("Pinned commit : " + $state.current_commit)
    Say ("Installed at  : " + $state.installed_at + "  from " + $state.source)
    Say ("Previous      : " + (($state.previous_commits | Select-Object -First 3) -join ', '))
    $py = Resolve-Python
    & $py -I (Join-Path $BinDir 'verify_install.py') verify --install-root $InstallRoot --service status
    $procs = Get-AStockProcesses
    Say ("AStock processes running: " + $procs.Count)
}

function Install-Release([string]$commit, [bool]$isRollback) {
    $py = Resolve-Python
    New-Item -ItemType Directory -Force -Path $Releases, $RuntimeDir | Out-Null
    $release = Join-Path $Releases $commit
    if (-not (Test-Path -LiteralPath $release)) {
        if ($isRollback) { throw "Release folder for $commit is missing; cannot roll back offline." }
        $staged = Join-Path $Releases "$commit.staging-$Stamp"
        Get-ReleaseTree $commit $staged
        & $py -I (Join-Path $staged 'deploy\verify_install.py') build --release $staged --commit $commit --source $Source | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Manifest build failed' }
        $registry = Get-Services $staged
        if (-not $SkipPreflight) {
            Say 'Running preflight tests against the staged release...'
            Invoke-Preflight $py $staged $registry
        }
        Test-ReleaseManifest $py $staged
        Move-Item -LiteralPath $staged -Destination $release
    } else {
        Say "Release $commit already staged; re-checking its manifest."
        Test-ReleaseManifest $py $release
    }
    $registry = Get-Services $release
    $state = Read-State
    $previous = @()
    if ($state) {
        if ($state.current_commit -and $state.current_commit -ne $commit) { $previous += $state.current_commit }
        $previous += @($state.previous_commits | Where-Object { $_ -and $_ -ne $commit })
    }
    if (-not $NoStart) { Stop-AStockServices }
    try {
        Move-LegacyFlatLayout
        Set-CurrentJunction $release
        Write-Launchers $py $registry
        Write-State ([ordered]@{
            schema = 'astock-install-state-v1'; current_commit = $commit; source = $Source
            installed_at = (Get-ShanghaiNow).ToString('o'); python = $py
            previous_commits = @($previous | Select-Object -Unique | Select-Object -First 10)
            rollback = $isRollback
        })
        & $py -I (Join-Path $BinDir 'verify_install.py') verify --install-root $InstallRoot --service installer
        if ($LASTEXITCODE -ne 0) { throw 'Installed tree failed verification' }
    } catch {
        if (-not $NoStart) {
            Say 'Switch failed after stopping services; restarting whatever launchers are in place.' 'Red'
            Start-AStockServices
        }
        throw
    }
    Update-Shortcuts
    if ($NoStart) { Say 'Installed without starting services (-NoStart).' 'Yellow'; return }
    Start-AStockServices
    Start-Sleep -Seconds 10
    $procs = Get-AStockProcesses
    foreach ($svc in $registry.services) {
        $n = @($procs | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like "*$($svc.script -replace '/', '\')*" }).Count
        $color = 'Green'; if ($n -ne 1) { $color = 'Red' }
        Say ("  {0,-20} workers={1}" -f $svc.id, $n) $color
    }
}

$mutex = New-Object Threading.Mutex($false, 'Local\AStockRuntimeInstaller')
$hasLock = $false
$exitCode = 0
try {
    $hasLock = $mutex.WaitOne(0)
    if (-not $hasLock) { throw 'Another installer instance is running.' }
    if ($Status) { Show-Status; return }
    if ($Rollback) {
        $state = Read-State
        if (-not $state -or -not $state.previous_commits) { throw 'No previous pinned release recorded. Use deploy\restore_pre_pinning_runtime.ps1 to return to the old layout.' }
        $target = @($state.previous_commits)[0]
        Say "Rolling back to $target" 'Yellow'
        Install-Release $target $true
    } else {
        $Commit = $Commit.Trim().ToLower()
        if ($Commit -notmatch '^[0-9a-f]{40}$') { throw 'Pass -Commit with the full 40-character commit id. Branch and tag names are not accepted.' }
        if ((Test-MarketWindow) -and -not $AllowDuringMarketHours) { throw 'Refusing to install between 09:05 and 15:05 Shanghai time on a weekday. Rollback is always allowed.' }
        Install-Release $Commit $false
    }
    Say 'Done.' 'Green'
} catch {
    Say ('FAILED: ' + $_.Exception.Message) 'Red'
    Say 'The previously running release was not modified unless the message above says otherwise.' 'Yellow'
    $exitCode = 1
} finally {
    if ($hasLock) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
    $nonInteractive = [Environment]::GetCommandLineArgs() | Where-Object { $_ -ieq '-NonInteractive' }
    if (-not $NoPause -and -not $nonInteractive -and $Host.Name -eq 'ConsoleHost') { [void](Read-Host 'Press Enter to close') }
}
exit $exitCode
