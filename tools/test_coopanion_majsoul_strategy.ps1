$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $workspace
$installed = Join-Path $workspace '.research/Coopanion/data/extensions/node_modules/cortico-world-majsoul'
$candidate = Join-Path $workspace '.research/Coopanion/data/extensions/node_modules/dafeiyu-majsoul-candidate'
New-Item -ItemType Directory -Force $candidate | Out-Null
foreach ($name in @('src', 'assets', 'tests', 'setup', 'package.json')) {
    Copy-Item -LiteralPath (Join-Path $installed $name) -Destination $candidate -Recurse -Force
}
& "$workspace/.venv/Scripts/python.exe" "$PSScriptRoot/patch_coopanion_majsoul_strategy.py" --package $candidate
if ($LASTEXITCODE -ne 0) { throw 'Strategy patch failed' }
foreach ($name in @('strategy.test.ts', 'autonomous-browser.test.ts', 'companion.test.ts', 'native-companion.test.ts')) {
    Copy-Item -LiteralPath (Join-Path $workspace "integrations/majsoul/$name") -Destination (Join-Path $candidate "src/$name") -Force
}
& node --import './.research/Coopanion/resources/app/node_modules/tsx/dist/loader.mjs' --test "$candidate/src/strategy.test.ts" "$candidate/src/autonomous-browser.test.ts" "$candidate/src/companion.test.ts" "$candidate/src/native-companion.test.ts" "$candidate/tests/game.test.ts" "$candidate/tests/render-state.test.ts" "$candidate/tests/render.test.ts"
if ($LASTEXITCODE -ne 0) { throw 'Mahjong tests failed' }
