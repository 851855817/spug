param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$spugTaskName = 'Spug-Demo-Tunnel-18080'
$spugWatchdog = Join-Path $PSScriptRoot 'Spug-Tunnel.ps1'
$spugUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if (-not (Test-Path -LiteralPath $spugWatchdog -PathType Leaf)) { throw 'Spug-Tunnel.ps1 is missing.' }
$spugExisting = Get-ScheduledTask -TaskName $spugTaskName -ErrorAction SilentlyContinue
if (-not $spugExisting) {
    $spugAction = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -Argument ('-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File "' + $spugWatchdog + '"')
    $spugTrigger = New-ScheduledTaskTrigger -AtLogOn -User $spugUser
    $spugPrincipal = New-ScheduledTaskPrincipal -UserId $spugUser -LogonType Interactive -RunLevel Limited
    $spugSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval ([TimeSpan]::FromMinutes(1)) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $spugTaskName -Action $spugAction -Trigger $spugTrigger `
        -Principal $spugPrincipal -Settings $spugSettings `
        -Description 'Spug localhost-only SSH tunnel with keepalive and automatic reconnect. Uses the existing user key; no saved password.' | Out-Null
} elseif ($spugExisting.Actions.Arguments -notlike ('*' + $spugWatchdog + '*')) {
    throw 'A different task already uses the Spug tunnel task name.'
}
Start-ScheduledTask -TaskName $spugTaskName
$spugReady = $false
for ($spugAttempt = 0; $spugAttempt -lt 40; $spugAttempt++) {
    try {
        $spugResponse = Invoke-WebRequest -Uri 'http://127.0.0.1:18080/' -UseBasicParsing -TimeoutSec 2
        if ($spugResponse.StatusCode -eq 200 -and $spugResponse.Content -match 'Spug|spug') {
            $spugReady = $true
            break
        }
    } catch { }
    Start-Sleep -Milliseconds 500
}
if (-not $spugReady) { throw 'Spug tunnel is reconnecting. See %TEMP%\spug-tunnel-watchdog.log.' }
Write-Output 'Spug is ready: http://127.0.0.1:18080/'
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:18080/' }
