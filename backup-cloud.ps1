$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'tools\backup_cloud.py')
if ($LASTEXITCODE -ne 0) { throw 'Cloud backup failed.' }
