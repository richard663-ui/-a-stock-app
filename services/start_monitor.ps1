param(
    [switch]$CheckOnly,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$installRoot = Join-Path $env:LOCALAPPDATA 'AStockQMT'
$runtimeDir = Join-Path $installRoot 'runtime'
# This script runs from <install root>\current\services; the release root holds deploy\services.json.
$releaseRoot = Split-Path -Parent $PSScriptRoot
$registryPath = Join-Path $releaseRoot 'deploy\services.json'
if (Test-Path -LiteralPath $registryPath) {
    $serviceSpecs = @((Get-Content -LiteralPath $registryPath -Raw -Encoding UTF8 | ConvertFrom-Json).services | ForEach-Object {
        @{ Name = $_.name; Script = ($_.script -replace '/', '\'); Watchdog = $_.watchdog }
    })
} else {
    $serviceSpecs = @(
        @{ Name = '行情同步'; Script = 'services\qmt_cloud_bridge.py'; Watchdog = 'AStockQMTCloudBridgeWatchdog.cmd' },
        @{ Name = '训练数据采集'; Script = 'services\qmt_l1_training_recorder_v1.py'; Watchdog = 'AStockL2TrainingWatchdog.cmd' },
        @{ Name = '自动学习'; Script = 'services\l1_ml_autotrain_daemon_v2.py'; Watchdog = 'AStockL2MLAutoTrainWatchdog.cmd' },
        @{ Name = '预测研究记录'; Script = 'services\qmt_research_recorder_v7.py'; Watchdog = 'AStockResearchWatchdog.cmd' },
        @{ Name = '影子验证'; Script = 'services\l1_v6_shadow_runner_v1.py'; Watchdog = 'AStockV6ShadowWatchdog.cmd' }
    )
}

function Get-Processes {
    return @(Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe', 'pythonw.exe', 'cmd.exe', 'XtMiniQmt.exe', 'XtItClient.exe') })
}

function Get-Workers($Spec, $Processes) {
    $scriptPath = Join-Path $releaseRoot $Spec.Script
    return @($Processes | Where-Object {
        $_.Name -in @('python.exe', 'pythonw.exe') -and
        $_.CommandLine -and $_.CommandLine.IndexOf($scriptPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

function Get-Watchdogs($Spec, $Processes) {
    $watchdogPath = Join-Path $runtimeDir $Spec.Watchdog
    return @($Processes | Where-Object {
        $_.Name -eq 'cmd.exe' -and $_.CommandLine -and
        $_.CommandLine.IndexOf($watchdogPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

function Find-QmtShortcut {
    $desktopDir = [Environment]::GetFolderPath('Desktop')
    $wsh = New-Object -ComObject WScript.Shell
    foreach ($file in @(Get-ChildItem -LiteralPath $desktopDir -Filter '*.lnk')) {
        $link = $wsh.CreateShortcut($file.FullName)
        if ([IO.Path]::GetFileName($link.TargetPath) -eq 'XtItClient.exe' -and
            (Test-Path -LiteralPath $link.TargetPath)) {
            return $file.FullName
        }
    }
    return $null
}

function Get-DataHealth($PythonPath) {
    $probeScript = Join-Path $releaseRoot 'services\monitor_health.py'
    $probeOut = Join-Path $runtimeDir 'monitor_health_probe.json'
    $probeErr = Join-Path $runtimeDir 'monitor_health_probe.err'
    $probe = Start-Process -FilePath $PythonPath -ArgumentList ('"{0}"' -f $probeScript) `
        -WorkingDirectory $releaseRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $probeOut -RedirectStandardError $probeErr
    # Retain the handle before a short-lived process exits (Windows PowerShell 5).
    $probeHandle = $probe.Handle
    if (-not $probe.WaitForExit(15000)) {
        $probe.Kill()
        throw '行情连接检查超时。请确认 QMT 已登录，随后点“查看盯盘状态”。'
    }
    $probe.Refresh()
    if ($probe.ExitCode -ne 0) {
        throw '数据检查未完成，请检查本地 Python 和 xtquant 环境。'
    }
    $jsonLine = @(Get-Content -LiteralPath $probeOut | Where-Object { $_.StartsWith('{') }) | Select-Object -Last 1
    if (-not $jsonLine) { throw '数据检查未返回有效状态。' }
    return $jsonLine | ConvertFrom-Json
}

$mutex = New-Object Threading.Mutex($false, 'Local\AStockMonitorStartup20261017')
$hasLock = $false
try {
    $hasLock = $mutex.WaitOne(0)
    if (-not $hasLock) {
        Write-Host '另一个启动检查正在进行，请稍候。'
        return
    }
    Write-Host ''
    Write-Host 'A股盯盘：启动与采集检查' -ForegroundColor Cyan
    Write-Host '先登录“迅投QMT交易终端 国盛证券实盘版”，保持行情连接。'
    Write-Host ''
    if (-not (Test-Path -LiteralPath $runtimeDir)) { throw '未找到已安装的盯盘后台。' }
    $processes = Get-Processes
    $pythonPath = @($processes | Where-Object { $_.Name -eq 'python.exe' -and $_.ExecutablePath } |
        Select-Object -ExpandProperty ExecutablePath -Unique | Where-Object { Test-Path -LiteralPath $_ }) |
        Select-Object -First 1
    if (-not $pythonPath) { $pythonPath = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw '未找到本地 Python 运行环境。' }

    $qmt = @($processes | Where-Object { $_.Name -in @('XtMiniQmt.exe', 'XtItClient.exe') })
    if (-not $CheckOnly -and $qmt.Count -eq 0) {
        $shortcut = Find-QmtShortcut
        if ($shortcut) {
            Start-Process -FilePath $shortcut
            Write-Host '已打开 QMT 登录窗口，请完成登录。' -ForegroundColor Yellow
        } else {
            Write-Host '请手动打开桌面的国盛证券 QMT，并完成登录。' -ForegroundColor Yellow
        }
    }
    $started = $false
    foreach ($spec in $serviceSpecs) {
        $workers = @(Get-Workers $spec $processes)
        $watchdogs = @(Get-Watchdogs $spec $processes)
        if (-not $CheckOnly -and $workers.Count -eq 0 -and $watchdogs.Count -eq 0) {
            $watchdogPath = Join-Path $runtimeDir $spec.Watchdog
            if (-not (Test-Path -LiteralPath $watchdogPath)) {
                Write-Host ($spec.Name + '：缺少启动文件') -ForegroundColor Red
                continue
            }
            Start-Process -FilePath $env:ComSpec -ArgumentList ('/d /c ""{0}""' -f $watchdogPath) `
                -WorkingDirectory $installRoot -WindowStyle Hidden
            $started = $true
        }
    }
    if ($started) {
        Write-Host '正在启动缺少的后台，已有后台会继续使用……'
        Start-Sleep -Seconds 8
    }
    $processes = Get-Processes
    $serviceResults = @(foreach ($spec in $serviceSpecs) {
        $workers = @(Get-Workers $spec $processes)
        $watchdogs = @(Get-Watchdogs $spec $processes)
        $message = if ($workers.Count -eq 1) { '运行中' } elseif ($workers.Count -gt 1) { '存在重复进程，需要检查' } else { '未启动或正在重启' }
        Write-Host ($spec.Name + '：' + $message)
        [PSCustomObject]@{ name = $spec.Name; script = $spec.Script; worker_count = $workers.Count; watchdog_count = $watchdogs.Count }
    })
    $qmtProcesses = @($processes | Where-Object { $_.Name -eq 'XtMiniQmt.exe' })
    Write-Host ('MiniQMT：' + $(if ($qmtProcesses.Count) { '已启动，连接状态见下方' } else { '未发现，请登录 QMT 并启用行情连接' }))
    $health = Get-DataHealth $pythonPath
    Write-Host ''
    Write-Host ('检查时间（北京时间）：' + $health.checked_at)
    if (-not $health.scheduled_session) {
        Write-Host '当前在采样时段之外；此时没有新增样本属于正常现象。' -ForegroundColor Yellow
    } else {
        Write-Host '当前处于工作日采样时段；还需确认真实行情正在更新（节假日例外）。'
    }
    $feeds = @($health.qmt.feeds.PSObject.Properties | ForEach-Object { $_.Value })
    $available = @($feeds | Where-Object { $_.available }).Count
    $fresh = @($feeds | Where-Object { $_.fresh }).Count
    if ($health.qmt.error) {
        Write-Host ('QMT 行情读取失败：' + $health.qmt.error + '。请确认已登录。') -ForegroundColor Red
    } else {
        Write-Host ('能读取报价：{0}/{1} 只；当前新鲜报价：{2}/{1} 只' -f $available, $feeds.Count, $fresh)
        $latestTick = @($feeds | Where-Object { $_.exchange_time } | Sort-Object exchange_time -Descending | Select-Object -First 1)
        if ($latestTick.Count) {
            $exchangeTime = [DateTimeOffset]::Parse($latestTick[0].exchange_time).ToOffset([TimeSpan]::FromHours(8))
            Write-Host ('最近交易所行情（北京时间）：' + $exchangeTime.ToString('yyyy-MM-dd HH:mm:ss'))
        }
        if ($health.scheduled_session -and $fresh -lt $feeds.Count) {
            Write-Host '部分或全部行情未更新。请检查 QMT 登录、网络和行情权限。' -ForegroundColor Red
        }
    }
    if (-not $health.recorder_heartbeat_fresh) {
        Write-Host '采集状态没有及时更新；后台进程存在也不代表正在采集。' -ForegroundColor Red
    }
    Write-Host ('本次采集进程今日新增：{0} 条样本；有效标签：{1} 条' -f $health.samples_today, $health.valid_labels_today)
    if ($health.scheduled_session -and $fresh -gt 0 -and $health.samples_today -eq 0) {
        Write-Host '已有新行情，请预热约 1～2 分钟后再检查；数量仍为 0 就需要排查。' -ForegroundColor Yellow
    }
    if ($health.learning.data_latest_date) {
        Write-Host ('最近有效训练数据日期：' + $health.learning.data_latest_date)
        Write-Host ('上次训练后新增：' + $health.learning.new_samples_since_last_training)
    }
    if ($health.recorder.collection_state) {
        $stateNames = @{ MARKET_CLOSED = '采样时段之外'; READY = '全部行情已就绪'; PARTIAL_FEED = '部分行情可用'; WARMING_UP = '正在预热'; WAITING_FRESH_TICKS = '等待新鲜行情' }
        Write-Host ('采集状态：' + $stateNames[$health.recorder.collection_state])
    }
    $statePath = Join-Path $installRoot 'install_state.json'
    if (Test-Path -LiteralPath $statePath) {
        $installState = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host ('已安装代码版本（commit）：' + $installState.current_commit.Substring(0, 12) + '  安装于 ' + $installState.installed_at)
    } else {
        Write-Host '未找到固定版本记录：请用 INSTALL_RUNTIME.cmd 安装已审核的版本。' -ForegroundColor Yellow
    }
    $integrityPath = Join-Path $runtimeDir 'integrity_status.json'
    if (Test-Path -LiteralPath $integrityPath) {
        $integrity = Get-Content -LiteralPath $integrityPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($integrity.ok) { Write-Host '代码完整性：通过（与审核版本一致）' }
        else { Write-Host ('代码完整性：失败，后台拒绝启动。' + (($integrity.problems) -join '; ')) -ForegroundColor Red }
    }
    $livePath = Join-Path $env:USERPROFILE 'AStockData\reports\collection\live_status.json'
    if (Test-Path -LiteralPath $livePath) {
        $live = Get-Content -LiteralPath $livePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $color = 'Gray'; if ($live.overall -eq 'CRITICAL') { $color = 'Red' } elseif ($live.overall -eq 'WARN') { $color = 'Yellow' }
        Write-Host ('采集健康监控：' + $live.overall + '（' + $live.checked_at + '）') -ForegroundColor $color
        foreach ($f in @($live.findings)) { if ($f.title) { Write-Host ('  - ' + $f.title + '：' + $f.message) -ForegroundColor $color } }
        if (@($live.power_setting_risks).Count) {
            Write-Host ('电源风险：' + (@($live.power_setting_risks) -join '; ') + '。按电源键或合盖会让电脑待机、停止采集；请用 Win+L 锁屏。') -ForegroundColor Yellow
        }
    }
    $result = [PSCustomObject]@{
        launcher_version = 'monitor-startup-20261023-pinned'
        services = $serviceResults
        miniqmt_running = $qmtProcesses.Count -gt 0
        health = $health
    }
    $reportPath = Join-Path $runtimeDir 'monitor_startup_status.json'
    $result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Host ''
    Write-Host '以后双击桌面“启动盯盘”，登录 QMT 后保持电脑联网、不要休眠。'
    Write-Host '“查看盯盘状态”只检查状态。采集训练数据不需要一直打开手机版。'
    Write-Host '手机版：https://richard663-ui.github.io/-a-stock-app/'
} catch {
    Write-Host ('检查未完成：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($hasLock) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
    if (-not $NoPause) {
        Write-Host ''
        [void](Read-Host '按回车关闭窗口')
    }
}
