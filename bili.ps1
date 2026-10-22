param([ValidateSet('status','login','check','run','pause','auto','cancel','report','share-on','share-off','mention-on','mention-off','verify','dm-on','dm-off','dm-check')][string]$Action = 'status', [long]$MentionUid = 0, [string]$MentionName = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& (Join-Path $PSScriptRoot 'start-cloud.ps1') -NoDesktop
$biliArgs = @($Action)
if ($MentionUid -gt 0) { $biliArgs += @('--uid', $MentionUid.ToString()) }
if ($MentionName) { $biliArgs += @('--name', $MentionName) }
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'tools/bili_cloud.py') @biliArgs
if ($LASTEXITCODE -ne 0) { throw 'Bilibili operation failed.' }
