param([ValidateSet('probe','run','bind','status','activate','pause','resume','ilink')][string]$Command='status')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe -m little_blue.windows_bridge $Command
exit $LASTEXITCODE
