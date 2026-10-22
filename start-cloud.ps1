param([switch]$NoDesktop)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$key = Join-Path $PSScriptRoot '.secrets/aliyun_dafeiyu_ed25519'
$hostsFile = Join-Path $PSScriptRoot '.secrets/aliyun_known_hosts'
$tokenFile = Join-Path $PSScriptRoot '.secrets/cloud_core_token'
$targetFile = Join-Path $PSScriptRoot '.secrets/cloud_ssh_target'
$sshTarget = [IO.File]::ReadAllText($targetFile).Trim()
if ($sshTarget -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*$') {
    throw 'Set .secrets/cloud_ssh_target to your SSH user@hostname.'
}
foreach ($file in @($key, $hostsFile, $tokenFile)) {
    if (!(Test-Path -LiteralPath $file)) { throw "Missing cloud connection file: $file" }
}
$headers = @{ Authorization = 'Bearer ' + [IO.File]::ReadAllText($tokenFile).Trim() }
function Test-CloudCore {
    try {
        $null = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/state' -Headers $headers -TimeoutSec 3
        return $true
    } catch { return $false }
}
if (!(Test-CloudCore)) {
    if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 18765 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Port 18765 is occupied but cloud authentication failed. Check the existing tunnel.'
    }
    $sshArgs = @('-N', '-T', '-i', ('"' + $key + '"'),
        '-o', ('"UserKnownHostsFile=' + $hostsFile + '"'),
        '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes',
        '-o', 'KexAlgorithms=curve25519-sha256', '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
        '-L', '127.0.0.1:18765:127.0.0.1:8765', $sshTarget)
    $tunnel = Start-Process -FilePath 'ssh.exe' -ArgumentList $sshArgs -WindowStyle Hidden -PassThru
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if ($tunnel.HasExited) { throw 'SSH tunnel exited. Check network access and the deployment key.' }
        if (Test-CloudCore) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (!$ready) {
        if (!$tunnel.HasExited) { $tunnel.Kill() }
        throw 'Cloud core did not respond. The cloud service may need attention.'
    }
}
Write-Host 'Cloud connected through SSH on localhost:18765.'
if (!$NoDesktop) {
    $python = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    if (!(Test-Path -LiteralPath $python)) { throw 'Set up the local desktop environment first.' }
    $existing = Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -eq $python -and $_.CommandLine -like '*-m little_blue*' -and $_.CommandLine -like '*127.0.0.1:18765*'
    }
    if (!$existing) {
        $clientData = Join-Path $PSScriptRoot 'data\cloud-client'
        Start-Process -FilePath $python -ArgumentList @('-m', 'little_blue', '--core-url', 'http://127.0.0.1:18765', '--core-token-file', ('"' + $tokenFile + '"'), '--data-dir', ('"' + $clientData + '"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
    }
}
