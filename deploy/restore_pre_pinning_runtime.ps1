<#
.SYNOPSIS
  Return the Windows runtime to the pre-pinning (flat) layout from a backup.

.DESCRIPTION
  Use only if the pinned runtime cannot be made to work. Restores services\,
  modules\, configs\, docs\, the root launchers, the runtime watchdog .cmd files,
  the Startup .vbs launchers and the two desktop shortcuts from a backup folder
  created by the hardening cycle, e.g.
      %USERPROFILE%\AStockBackups\20261023_pre_hardening
  Pinned releases are kept (renamed state) so the pinned layout can be
  reinstalled later. Market data, models, logs and secrets are not touched.
#>
param(
    [Parameter(Mandatory = $true)][string]$BackupDir,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'AStockQMT'),
    [switch]$NoStart
)
$ErrorActionPreference = 'Stop'
$runtimeBackup = Join-Path $BackupDir 'AStockQMT_runtime'
if (-not (Test-Path -LiteralPath (Join-Path $runtimeBackup 'services'))) { throw "Not a runtime backup: $BackupDir" }
$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$runtimeDir = Join-Path $InstallRoot 'runtime'

$root = [regex]::Escape($InstallRoot)
$watchdogs = [regex]::Escape($runtimeDir) + '\\AStock[A-Za-z0-9]*Watchdog\.cmd'
$procs = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and ((($_.Name -in @('python.exe', 'pythonw.exe')) -and $_.CommandLine -match $root) -or
    ($_.Name -eq 'cmd.exe' -and $_.CommandLine -match $watchdogs)) })
$procs | Where-Object { $_.Name -eq 'cmd.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500
$procs | Where-Object { $_.Name -ne 'cmd.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

foreach ($name in @('services', 'modules', 'configs', 'docs')) {
    $src = Join-Path $runtimeBackup $name
    if (Test-Path -LiteralPath $src) { robocopy $src (Join-Path $InstallRoot $name) /MIR /NFL /NDL /NJH /NJS /NP | Out-Null }
}
Get-ChildItem -LiteralPath $runtimeBackup -File | Copy-Item -Destination $InstallRoot -Force
Get-ChildItem -LiteralPath (Join-Path $runtimeBackup 'runtime') -Filter '*.cmd' | Copy-Item -Destination $runtimeDir -Force

$current = Join-Path $InstallRoot 'current'
if (Test-Path -LiteralPath $current) { cmd /c rmdir "$current" | Out-Null }
$state = Join-Path $InstallRoot 'install_state.json'
if (Test-Path -LiteralPath $state) { Move-Item -LiteralPath $state -Destination "$state.disabled-$(Get-Date -Format yyyyMMdd_HHmmss)" }

Get-ChildItem -LiteralPath $startup -Filter 'AStock*.vbs' | Remove-Item -Force
Get-ChildItem -LiteralPath (Join-Path $BackupDir 'Startup') -Filter '*.vbs' | Copy-Item -Destination $startup -Force
$shortcuts = Join-Path $BackupDir 'DesktopShortcuts'
if (Test-Path -LiteralPath $shortcuts) {
    Get-ChildItem -LiteralPath $shortcuts -Filter '*.lnk' | Copy-Item -Destination ([Environment]::GetFolderPath('Desktop')) -Force
}
Write-Host 'Pre-pinning runtime restored.' -ForegroundColor Green
if (-not $NoStart) {
    Get-ChildItem -LiteralPath $startup -Filter 'AStock*.vbs' | ForEach-Object { Start-Process wscript.exe -ArgumentList ('"' + $_.FullName + '"') }
    Write-Host 'Services started from the restored launchers.'
}
