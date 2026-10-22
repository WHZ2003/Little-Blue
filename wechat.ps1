param([ValidateSet('login','disable','enable','status','logout')][string]$Action = 'login')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Action -ne 'status') {
    & .\.venv\Scripts\python.exe -m little_blue.handoff check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
docker compose exec core python -m little_blue.wechat $Action
exit $LASTEXITCODE
