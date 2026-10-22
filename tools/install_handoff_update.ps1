param(
    [Parameter(Mandatory=$true)][string]$TargetFolder,
    [string]$PackageFolder
)
$ErrorActionPreference = 'Stop'
if (!$PackageFolder) { $PackageFolder = $PSScriptRoot }
$targetRoot = (Resolve-Path -LiteralPath $TargetFolder).Path
$packageRoot = (Resolve-Path -LiteralPath $PackageFolder).Path
if (!(Test-Path -LiteralPath (Join-Path $targetRoot 'compose.yaml'))) {
    throw 'Target must be an existing Little-Blue checkout with compose.yaml.'
}
$manifest = Get-Content -LiteralPath (Join-Path $packageRoot 'manifest.json') -Raw | ConvertFrom-Json
if ($manifest.format -ne 'little-blue-handoff-update-1') { throw 'Unsupported package.' }
$checked = @()
foreach ($item in $manifest.files) {
    $relative = [string]$item.path
    if ($relative -notmatch '^(little_blue|tools|tests|docs)/[A-Za-z0-9_./-]+$' -and
        $relative -notin @('handoff.ps1','memory.ps1','setup.ps1','start.ps1','start-core.ps1','wechat.ps1',
                          'Dockerfile','pyproject.toml','requirements-lock.txt','requirements-core.txt',
                          'requirements-bridge.txt','.dockerignore','.gitignore')) {
        throw "Unexpected package path: $relative"
    }
    $source = [IO.Path]::GetFullPath((Join-Path $packageRoot ('payload/' + $relative)))
    $destination = [IO.Path]::GetFullPath((Join-Path $targetRoot $relative))
    if (!$source.StartsWith($packageRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        !$destination.StartsWith($targetRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Package path escapes its directory.'
    }
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $actualHash = [BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes($source))).Replace('-','').ToLowerInvariant() }
    finally { $hasher.Dispose() }
    if ($actualHash -ne $item.sha256) {
        throw "Package checksum mismatch: $relative"
    }
    $checked += [PSCustomObject]@{Relative=$relative; Source=$source; Destination=$destination}
}
Push-Location -LiteralPath $targetRoot
try {
    $running = & docker compose ps --status running --quiet core
    if ($LASTEXITCODE -ne 0) { throw 'Cannot check Docker. Start Docker Desktop and retry.' }
    if ($running) { throw 'Wait for the current reply, close the pet, run docker compose stop core, then retry installation.' }
} finally { Pop-Location }
$backup = Join-Path $targetRoot ('backups/handoff-code-before-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
# Back up every existing destination before changing any source file.
foreach ($item in $checked) {
    if (Test-Path -LiteralPath $item.Destination) {
        $old = Join-Path $backup $item.Relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $old) -Force | Out-Null
        Copy-Item -LiteralPath $item.Destination -Destination $old
    }
}
foreach ($item in $checked) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $item.Destination) -Force | Out-Null
    Copy-Item -LiteralPath $item.Source -Destination $item.Destination -Force
}
Write-Output "Code installed. Previous files: $backup"
Write-Output 'From the target folder run: .\setup.ps1 -SkipDownloads -SkipTests'
Write-Output 'Then follow docs/handoff.md. Keep each device .env, .secrets and .handoff private and separate.'
