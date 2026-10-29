param([switch]$BrowserSmoke)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $workspace
$installed = Join-Path $workspace '.research/Coopanion/data/extensions/node_modules/cortico-world-bilibili-video'
$candidate = Join-Path $workspace '.research/Coopanion/data/extensions/node_modules/dafeiyu-video-candidate'
New-Item -ItemType Directory -Force -Path $candidate | Out-Null
foreach ($name in @('src', 'dist', 'assets', 'setup', 'package.json', 'describe.json')) {
    $source = Join-Path $installed $name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination $candidate -Recurse -Force
    }
}
& "$workspace/.venv/Scripts/python.exe" "$PSScriptRoot/patch_coopanion_video_watch.py" --package $candidate
if ($LASTEXITCODE -ne 0) { throw 'Video patch failed' }
foreach ($name in @('cleanup.test.ts', 'remote-watch.test.ts', 'storyboard.test.ts', 'browser-smoke.ts', 'remote-browser-smoke.ts', 'storyboard-browser-smoke.ts')) {
    Copy-Item -LiteralPath (Join-Path $workspace "integrations/bilibili-video/$name") -Destination (Join-Path $candidate "src/$name") -Force
}
$imports = @('--import', './.research/Coopanion/resources/app/node_modules/tsx/dist/loader.mjs', '--import', './integrations/bilibili-video/test-loader.mjs')
& node @imports --test "$candidate/src/cleanup.test.ts" "$candidate/src/remote-watch.test.ts" "$candidate/src/storyboard.test.ts" ./tests/storyboard.test.mjs ./integrations/coopanion/chat-format.test.mjs ./integrations/coopanion/preferences.test.mjs ./integrations/coopanion/sync.test.mjs ./integrations/coopanion/video-watch.test.mjs
if ($LASTEXITCODE -ne 0) { throw 'Video and sync tests failed' }
if ($BrowserSmoke) {
    & node @imports "$candidate/src/storyboard-browser-smoke.ts"
    if ($LASTEXITCODE -ne 0) { throw 'Storyboard browser smoke test failed' }
    # Independent headless browser, synthetic local video, no real WeChat sends.
    & node @imports "$candidate/src/browser-smoke.ts"
    if ($LASTEXITCODE -ne 0) { throw 'Browser ownership smoke test failed' }
    & node @imports "$candidate/src/remote-browser-smoke.ts"
    if ($LASTEXITCODE -ne 0) { throw 'Real browser video smoke test failed' }
}
