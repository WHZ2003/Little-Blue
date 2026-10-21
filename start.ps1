param([switch]$DebugMode, [switch]$ContainerCore, [switch]$LocalOnly, [switch]$Panel)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$petPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $petPython)) { $petPython = 'python' }
$petArgs = @('-m', 'little_blue')
if ($Panel) { $petArgs += '--panel' }
if ($DebugMode) { $petArgs += '--debug' }
if ($ContainerCore -and $LocalOnly) { throw 'ContainerCore and LocalOnly cannot be combined.' }
# A configured shared core must never silently fall back to a separate relationship.
if ($ContainerCore -or (!$LocalOnly -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.secrets/core_token')))) {
    $petArgs += @('--core-url', 'http://127.0.0.1:8765', '--core-token-file', (Join-Path $PSScriptRoot '.secrets/core_token'))
}
& $petPython @petArgs
