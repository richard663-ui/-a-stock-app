<#
Show, apply or restore the AC power settings that decide whether overnight
collection survives. Run it yourself; the services never change power plans.

  (no switch)   list every power plan's AC values (read-only)
  -Apply        save current AC values to %USERPROFILE%\.a_stock_qmt\power_backup_<time>.json,
                then set idle sleep, idle hibernate, lid, power button and sleep button
                to 0 (never / do nothing) on AC power in EVERY plan
  -Restore <f>  put back the values saved in that backup file

Battery (DC) values are never changed. Every plan is changed because tools such
as Razer Cortex or Armoury Crate switch plans by themselves.
#>
param(
    [switch]$Apply,
    [string]$Restore = ''
)
$ErrorActionPreference = 'Stop'

$Settings = [ordered]@{
    idle_sleep     = @('238c9fa8-0aad-41ed-83f4-97be242c8f20', '29f6c1db-86da-48c5-9fdb-f2b67b1f44da')
    idle_hibernate = @('238c9fa8-0aad-41ed-83f4-97be242c8f20', '9d7815a6-7ee4-497e-8888-515a05f02364')
    lid_close      = @('4f971e89-eebd-4455-a8de-9e59040e7347', '5ca83367-6e45-459f-a27b-476b1d01c936')
    power_button   = @('4f971e89-eebd-4455-a8de-9e59040e7347', '7648efa3-dd9c-4e3e-b566-50f929386280')
    sleep_button   = @('4f971e89-eebd-4455-a8de-9e59040e7347', '96996bc0-ad50-47ec-923b-6f41874dd9eb')
}
$GuidRe = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'

function Get-Schemes {
    foreach ($line in (powercfg /list)) {
        if ($line -match "($GuidRe)\s*(?:\((.*)\))?") {
            [pscustomobject]@{ Guid = $Matches[1]; Name = $Matches[2]; Active = $line.TrimEnd().EndsWith('*') }
        }
    }
}

function Get-AcValue([string]$Scheme, [string[]]$Path) {
    $text = (powercfg /qh $Scheme $Path[0] $Path[1]) -join "`n"
    $values = [regex]::Matches($text, '0x([0-9a-fA-F]{8})\s*$', 'Multiline') | ForEach-Object { [Convert]::ToInt64($_.Groups[1].Value, 16) }
    if (@($values).Count -ge 2) { return @($values)[-2] }
    return $null
}

function Show-Table {
    foreach ($s in Get-Schemes) {
        $row = [ordered]@{ plan = ($s.Name + $(if ($s.Active) { ' *' } else { '' })) }
        foreach ($k in $Settings.Keys) { $row[$k] = Get-AcValue $s.Guid $Settings[$k] }
        [pscustomobject]$row
    }
}

if ($Restore) {
    $saved = Get-Content -Raw -Path $Restore | ConvertFrom-Json
    foreach ($item in $saved.values) {
        if ($null -ne $item.ac) {
            powercfg /setacvalueindex $item.scheme $Settings[$item.setting][0] $Settings[$item.setting][1] $item.ac
        }
    }
    powercfg /setactive SCHEME_CURRENT
    Write-Host "Restored AC values from $Restore"
}
elseif ($Apply) {
    $backup = @()
    foreach ($s in Get-Schemes) {
        foreach ($k in $Settings.Keys) {
            $backup += [pscustomobject]@{ scheme = $s.Guid; name = $s.Name; setting = $k; ac = (Get-AcValue $s.Guid $Settings[$k]) }
        }
    }
    $dir = Join-Path $env:USERPROFILE '.a_stock_qmt'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $file = Join-Path $dir ('power_backup_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.json')
    [pscustomobject]@{ saved_at = (Get-Date).ToString('o'); values = $backup } | ConvertTo-Json -Depth 4 | Set-Content -Path $file -Encoding UTF8
    foreach ($s in Get-Schemes) {
        foreach ($k in $Settings.Keys) {
            powercfg /setacvalueindex $s.Guid $Settings[$k][0] $Settings[$k][1] 0
        }
    }
    powercfg /setactive SCHEME_CURRENT
    Write-Host "Saved previous values to $file"
    Write-Host "Undo with: powershell -ExecutionPolicy Bypass -File $PSCommandPath -Restore `"$file`""
}

Show-Table | Format-Table -AutoSize
Write-Host 'AC values: idle_* in seconds (0 = never); buttons 0 = do nothing, 1 = sleep, 2 = hibernate, 3 = shut down.'
