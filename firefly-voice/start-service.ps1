param([switch]$WaitReady)
$ErrorActionPreference = 'Stop'
$serviceRoot = $PSScriptRoot
$healthUrl = 'http://127.0.0.1:18892/health'
function Test-VoiceReady {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $health.service -eq 'dafeiyu-firefly-seed-vc' -and $health.ready
    } catch { return $false }
}
if (Test-VoiceReady) { Write-Output 'Firefly voice is ready'; exit 0 }
$mutex = New-Object System.Threading.Mutex($false, 'Local\DaFeiYu-Firefly-Voice-Launcher')
$locked = $false
try {
    $locked = $mutex.WaitOne(5000)
    if (-not $locked) { throw 'Another voice launcher is starting the service' }
    if (Test-VoiceReady) { exit 0 }
    $pythonExe = Join-Path $serviceRoot '.venv\Scripts\python.exe'
    $script = Join-Path $serviceRoot 'service.py'
    if (-not (Test-Path -LiteralPath (Join-Path $serviceRoot 'models\firefly-zh-v1\ft_model.pth'))) {
        throw 'Trained voice model is missing'
    }
    $pidFile = Join-Path $serviceRoot 'runtime\service.pid'
    New-Item -ItemType Directory -Force (Join-Path $serviceRoot 'runtime'),(Join-Path $serviceRoot 'logs') | Out-Null
    $existing = $null
    if (Test-Path -LiteralPath $pidFile) {
        $oldPid = [int](Get-Content -LiteralPath $pidFile -Raw)
        $existing = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($existing -and $existing.Path -ne $pythonExe) { $existing = $null }
    }
    if (-not $existing) {
        $priorUtf8 = $env:PYTHONUTF8
        $priorUnbuffered = $env:PYTHONUNBUFFERED
        try {
            $env:PYTHONUTF8 = '1'; $env:PYTHONUNBUFFERED = '1'
            $child = Start-Process -FilePath $pythonExe -ArgumentList @('-u', ('"' + $script + '"')) -WorkingDirectory $serviceRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $serviceRoot 'logs\service.out.log') -RedirectStandardError (Join-Path $serviceRoot 'logs\service.err.log')
            $child.Id | Set-Content -LiteralPath $pidFile
        } finally { $env:PYTHONUTF8 = $priorUtf8; $env:PYTHONUNBUFFERED = $priorUnbuffered }
    }
    if ($WaitReady) {
        $deadline = (Get-Date).AddSeconds(55)
        while ((Get-Date) -lt $deadline) {
            if (Test-VoiceReady) { Write-Output 'Firefly voice is ready'; exit 0 }
            Start-Sleep -Milliseconds 500
        }
        throw 'Voice service did not become ready; see firefly-voice/logs'
    }
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
