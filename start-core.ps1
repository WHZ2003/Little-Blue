param([switch]$NoBuild)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$petPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $petPython)) { $petPython = 'python' }
& $petPython tools/init_core.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($NoBuild) { docker compose up -d --wait } else { docker compose up -d --build --wait }
exit $LASTEXITCODE
