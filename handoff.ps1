param(
    [Parameter(Mandatory=$true)][ValidateSet('Prepare','Send','Receive','Finish','Status')][string]$Action,
    [string]$Request,
    [string]$Package,
    [string]$Receipt
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$petPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $petPython)) { throw 'Run setup.ps1 first.' }
$handoffArgs = @('-m', 'little_blue.handoff', $Action.ToLower())
if ($Request) { $handoffArgs += @('--request', [System.IO.Path]::GetFullPath($Request)) }
if ($Package) { $handoffArgs += @('--package', [System.IO.Path]::GetFullPath($Package)) }
if ($Receipt) { $handoffArgs += @('--receipt', [System.IO.Path]::GetFullPath($Receipt)) }
& $petPython @handoffArgs
exit $LASTEXITCODE
