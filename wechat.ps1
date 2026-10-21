param([ValidateSet('login','disable','enable','status','logout')][string]$Action = 'login')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
docker compose exec core python -m little_blue.wechat $Action
exit $LASTEXITCODE
