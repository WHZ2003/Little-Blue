$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
& .\.venv\Scripts\python.exe -m pytest -q --basetemp=.test-tmp-build
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm LittleBlue.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
& .\.venv\Scripts\python.exe tools\check_build.py
if ($LASTEXITCODE -ne 0) { throw 'Frozen EXE smoke test failed' }
Write-Output 'Built dist\LittleBlue\LittleBlue.exe (keep the _internal folder beside it).'
