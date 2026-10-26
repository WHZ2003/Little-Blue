param(
    [string]$PythonExecutable,
    [switch]$SkipDownloads,
    [switch]$SkipContainers,
    [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'tools\cache-env.ps1')
Set-Location -LiteralPath $PSScriptRoot

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Setup command failed: $Executable (exit $LASTEXITCODE)"
    }
}

$petPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $petPython)) {
    if (!$PythonExecutable) {
        $managedPython = Get-ChildItem -Path (Join-Path $PSScriptRoot '.python\cpython-3.13.*\python.exe') -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($managedPython) { $PythonExecutable = $managedPython.FullName }
        else { $PythonExecutable = 'python' }
    }
    Invoke-Checked $PythonExecutable @('-m', 'venv', '.venv')
}
Invoke-Checked $petPython @('-m', 'ensurepip', '--upgrade')
Invoke-Checked $petPython @('-m', 'pip', 'install', '-r', 'requirements-lock.txt', '-e', '.[dev,browser,memory]', '-r', 'requirements-bridge.txt')
Invoke-Checked $petPython @('-m', 'pip', 'check')

if (!(Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
if (!$SkipDownloads) {
    Invoke-Checked $petPython @('-m', 'playwright', 'install', 'chromium')
    Invoke-Checked $petPython @('tools/prepare_memory_model.py')
    Invoke-Checked $petPython @('tools/fetch_memes.py')
}
if (!$SkipContainers) {
    Invoke-Checked $petPython @('-m', 'little_blue.handoff', 'check')
    Invoke-Checked 'docker' @('info', '--format', '{{.OSType}}')
    Invoke-Checked $petPython @('tools/init_core.py')
    Invoke-Checked 'docker' @('compose', 'config', '--quiet')
    Invoke-Checked 'docker' @('compose', 'up', '-d', '--build', '--wait')
}
if (!$SkipTests) {
    Invoke-Checked $petPython @('-m', 'pytest', '-q', '--basetemp=.test-tmp-setup')
}
Write-Output 'Setup complete. Launch with .\start.ps1 (or .\start.ps1 -LocalOnly without Docker).'
Write-Output 'See docs/local-setup.md for credentials, WeChat, tests, and packaging.'
