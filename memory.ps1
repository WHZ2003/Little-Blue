param(
    [ValidateSet('Export', 'Inspect', 'Import')][string]$Action = 'Export',
    [string]$Archive,
    [string]$DataDir,
    [switch]$LocalOnly
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$petPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $petPython)) { throw 'Run setup.ps1 first.' }

if ($Action -eq 'Export') {
    if (!$Archive) { $Archive = Join-Path $PSScriptRoot ('backups\memory-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.zip') }
    $Archive = [System.IO.Path]::GetFullPath($Archive)
    if (Test-Path -LiteralPath $Archive) { throw 'Archive already exists. Choose a new filename.' }
    if ($LocalOnly -or $DataDir) {
        if (!$DataDir) { $DataDir = Join-Path $env:LOCALAPPDATA 'LittleBlue' }
        & $petPython -m little_blue.memory_transfer export --data-dir $DataDir --output $Archive
        if ($LASTEXITCODE -ne 0) { throw 'Memory export failed.' }
    } else {
        $containerArchive = '/tmp/memory-' + [guid]::NewGuid().ToString('N') + '.zip'
        try {
            # Send only this standalone tool, never .env or the project directory.
            Get-Content -LiteralPath (Join-Path $PSScriptRoot 'little_blue\memory_transfer.py') -Raw |
                docker compose exec -T core python - export --output $containerArchive
            if ($LASTEXITCODE -ne 0) { throw 'Docker memory export failed. Is the core running?' }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Archive) | Out-Null
            # Copy to a unique temporary file, then publish without overwriting.
            $temporaryArchive = $Archive + '.' + [guid]::NewGuid().ToString('N') + '.partial'
            # Docker's archive endpoint may not see tmpfs files. Stream bytes
            # through Python, avoiding Windows PowerShell's text transcoding.
            $download = @'
import subprocess, sys
with open(sys.argv[2], 'xb') as output:
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'core', 'python', '-c',
        'import shutil,sys; f=open(sys.argv[1], "rb"); shutil.copyfileobj(f, sys.stdout.buffer)',
        sys.argv[1]], stdout=output)
raise SystemExit(result.returncode)
'@
            $download | & $petPython - $containerArchive $temporaryArchive
            if ($LASTEXITCODE -ne 0) { throw 'Copying the memory archive failed.' }
            & $petPython -m little_blue.memory_transfer inspect --archive $temporaryArchive
            if ($LASTEXITCODE -ne 0) { throw 'Copied archive failed validation.' }
            [System.IO.File]::Move($temporaryArchive, $Archive)
        } finally {
            docker compose exec -T core python -c 'import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(missing_ok=True)' $containerArchive
            if ($temporaryArchive -and (Test-Path -LiteralPath $temporaryArchive)) {
                Remove-Item -LiteralPath $temporaryArchive -Force
            }
        }
    }
    Write-Output "Portable memory saved: $Archive"
} elseif ($Action -eq 'Inspect') {
    if (!$Archive) { throw 'Specify -Archive.' }
    & $petPython -m little_blue.memory_transfer inspect --archive $Archive
    if ($LASTEXITCODE -ne 0) { throw 'Archive validation failed.' }
} else {
    if (!$Archive -or !$DataDir) { throw 'Specify -Archive and a NEW -DataDir. Existing data is never overwritten.' }
    & $petPython -m little_blue.memory_transfer import --archive $Archive --data-dir $DataDir
    if ($LASTEXITCODE -ne 0) { throw 'Memory import failed.' }
    Write-Output "Memory restored to $DataDir. See docs/memory-transfer.md to start it."
}
