$ErrorActionPreference = 'Stop'
$spugKey = Join-Path $env:USERPROFILE '.ssh\miaoyinju_rsa'
$spugSsh = Join-Path $env:WINDIR 'System32\OpenSSH\ssh.exe'
$spugLog = Join-Path $env:TEMP 'spug-tunnel-watchdog.log'
$spugMutex = [Threading.Mutex]::new($false, 'Local\Spug-Demo-Tunnel-18080')
$spugAcquired = $false
try {
    try { $spugAcquired = $spugMutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $spugAcquired = $true }
    if (-not $spugAcquired) { exit 0 }
    if (-not (Test-Path -LiteralPath $spugKey -PathType Leaf)) { throw 'Saved SSH key is missing.' }
    if (-not (Test-Path -LiteralPath $spugSsh -PathType Leaf)) { throw 'Windows OpenSSH is missing.' }
    $spugSshArgs = @('-N', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
        '-o', 'StrictHostKeyChecking=yes', '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
        '-i', $spugKey, '-L', '127.0.0.1:18080:127.0.0.1:8080', 'root@47.104.88.147')
    while ($true) {
        if ((Test-Path -LiteralPath $spugLog) -and (Get-Item -LiteralPath $spugLog).Length -gt 1MB) {
            Move-Item -LiteralPath $spugLog -Destination ($spugLog + '.previous') -Force
        }
        Add-Content -LiteralPath $spugLog -Value ('{0:o} Connecting; watchdog PID {1}' -f (Get-Date), $PID)
        # Windows PowerShell treats native stderr as an error record. Keep reconnecting after SSH exits.
        $ErrorActionPreference = 'Continue'
        & $spugSsh @spugSshArgs 2>> $spugLog
        $spugSshExit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        Add-Content -LiteralPath $spugLog -Value ('{0:o} SSH exited {1}; reconnect in 5 seconds' -f (Get-Date), $spugSshExit)
        Start-Sleep -Seconds 5
    }
} catch {
    Add-Content -LiteralPath $spugLog -Value ('{0:o} Watchdog failed: {1}' -f (Get-Date), $_.Exception.Message)
    exit 1
} finally {
    if ($spugAcquired) { $spugMutex.ReleaseMutex() }
    $spugMutex.Dispose()
}
