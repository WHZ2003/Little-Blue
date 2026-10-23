param(
    [Parameter(Mandatory=$true)][ValidateRange(1024,65535)][int]$Port,
    [Parameter(Mandatory=$true)][string]$ProfileDir,
    [switch]$ShowWindow
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$ProfileDir = [IO.Path]::GetFullPath($ProfileDir).TrimEnd('\')
if ($ProfileDir.Contains('"') -or $ProfileDir.Contains("`n") -or $ProfileDir.Contains("`r")) {
    throw 'Invalid dedicated browser profile path.'
}
$endpoint = "http://127.0.0.1:$Port/json/version"
function Debug-Ready {
    try { return [bool](Invoke-RestMethod -Uri $endpoint -TimeoutSec 1).webSocketDebuggerUrl }
    catch { return $false }
}
function Profile-Processes {
    $profilePattern = '--user-data-dir=(?:"' + [regex]::Escape($ProfileDir) + '"|' + [regex]::Escape($ProfileDir) + '(?=\s|$))'
    return @(Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine -notmatch '--type=' -and $_.CommandLine -match $profilePattern
    })
}
function Check-Owner {
    $owners = @(Profile-Processes | Where-Object { $_.CommandLine -match "--remote-debugging-port=$Port(\s|$)" })
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if (-not $owners.Count -or -not $listeners.Count -or
        @($listeners | Where-Object { $_.OwningProcess -notin $owners.ProcessId -or $_.LocalAddress -notin @('127.0.0.1','::1') }).Count) {
        throw 'Debug port is not owned by this dedicated Edge profile. Choose another browserPort.'
    }
}
function Show-GameWindow {
    if (-not $ShowWindow) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class MajsoulWindow {
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr window, int command);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
}
'@
    foreach ($owner in @(Profile-Processes)) {
        $process = Get-Process -Id $owner.ProcessId -ErrorAction SilentlyContinue
        if ($process -and $process.MainWindowHandle -ne 0) {
            $window = $process.MainWindowHandle
            # Restore minimized windows; preserve the size of normal/maximized windows.
            $command = if ([MajsoulWindow]::IsIconic($window)) { 9 } else { 5 }
            [MajsoulWindow]::ShowWindowAsync($window, $command) | Out-Null
            [MajsoulWindow]::SetForegroundWindow($window) | Out-Null
        }
    }
}
if (Debug-Ready) {
    Check-Owner
    Show-GameWindow
    Write-Output '{"ready":true,"reused":true}'
    exit 0
}
if (@(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).Count) {
    throw 'Browser port is occupied by another service. Choose another browserPort.'
}
$owners = @(Profile-Processes)
if ($owners.Count -and @($owners | Where-Object { $_.CommandLine -notmatch "--remote-debugging-port=$Port(\s|$)" }).Count) {
    throw 'Dedicated Edge is already running with a different debug port. Close that dedicated window first.'
}
if (-not $owners.Count) {
    $candidates = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe")
    $edge = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $edge) { throw 'Microsoft Edge is not installed.' }
    New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null
    $browserArgs = '--user-data-dir="' + $ProfileDir + '" --remote-debugging-address=127.0.0.1 --remote-debugging-port=' + $Port + ' --no-first-run --no-default-browser-check --new-window about:blank'
    # This is the interactive game window requested by the user; only the helper is hidden.
    Start-Process -FilePath $edge -ArgumentList $browserArgs -WindowStyle Normal | Out-Null
}
$deadline = (Get-Date).AddSeconds(20)
do {
    if (Debug-Ready) {
        Check-Owner
        Show-GameWindow
        Write-Output '{"ready":true,"reused":false}'
        exit 0
    }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $deadline)
throw 'Dedicated Edge did not expose its debug port. No additional browser was launched.'
