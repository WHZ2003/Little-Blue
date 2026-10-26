$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'tools\cache-env.ps1')
$appPath = Join-Path $PSScriptRoot '.research\Coopanion\Coopanion.exe'
if (-not (Test-Path -LiteralPath $appPath)) {
    throw 'Coopanion portable app is missing. See docs/coopanion-local.md.'
}
# The portable app stores its independent data beside this executable.
$previousData = $env:CORTICO_COMPANION_DATA
$previousNode = $env:ELECTRON_RUN_AS_NODE
try {
    $env:CORTICO_COMPANION_DATA = Join-Path $PSScriptRoot '.research\Coopanion\data'
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    Start-Process -FilePath $appPath -WorkingDirectory (Split-Path -Parent $appPath)
} finally {
    $env:CORTICO_COMPANION_DATA = $previousData
    $env:ELECTRON_RUN_AS_NODE = $previousNode
}
