# DaFeiYu local workflow

This checkout is in `D:\DaFeiYu`, with `origin` pointing to
`https://github.com/WHZ2003/Little-Blue.git` on branch `master`.
Run the following commands in PowerShell from this folder.

## Setup

Python 3.11+ and Docker Desktop using Linux containers are required for the full workflow.
Start Docker Desktop, then run:

```powershell
.\setup.ps1
```

This machine uses standalone CPython 3.13.16 in `.python` because the installed
Anaconda runtime conflicts with Qt DLL loading. `.venv` uses this standalone
runtime. Keep `.python` in place; the setup script reuses it if `.venv` needs
recreating. On a fresh machine, use a standalone Python installation if Conda
causes a Qt import error.

The script creates `.venv`, installs the repository's locked dependencies plus
browser and Windows bridge dependencies, downloads Chromium, the pinned memory
model and curated community images, generates local service tokens, starts both
Docker services, and runs the tests. Existing `.env` and service tokens are kept.
Use `-SkipDownloads`, `-SkipContainers`, or `-SkipTests` when appropriate.
For another Python installation, pass `-PythonExecutable 'C:\path\to\python.exe'`.
If PowerShell blocks scripts, invoke the chosen script with
`powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1`; no persistent
execution-policy change is needed.

## Everyday use

```powershell
.\start-core.ps1 -NoBuild       # Start core and isolated browser
.\start.ps1                    # Desktop pet using the shared Docker core
.\start.ps1 -Panel             # Also show settings/control panel
.\browser.ps1                  # Open the isolated browser viewer
docker compose ps              # Service health
docker compose stop            # Stop services; preserve memories
```

Choose “Save and exit” from the pet/tray menu to close the desktop process.
The Docker services continue until stopped. Browser exploration starts paused;
enable it in the viewer when wanted. `start.ps1 -LocalOnly` instead uses a
separate local database, independent of Docker memories.

## DeepSeek and WeChat

The new installation starts offline. Enter your own key in the ignored `.env`:
`DEEPSEEK_API_KEY=your-key`. Then apply it with:

```powershell
docker compose up -d --force-recreate --wait
.\start.ps1 -Panel
```

Enable online cognition in the app's settings. Local-only mode reads the
`DEEPSEEK_API_KEY` process/user environment variable instead of `.env`.
Keep credentials out of Git and chat.

For optional WeChat binding, run `.\wechat.ps1` and scan/confirm its QR code
with your own account. Binding and live messages require user interaction;
installing the dependencies does not bind an account. See [WeChat](wechat.md).
The optional Windows UI bridge dependencies are installed, but the bridge is
not activated; see [its compatibility limitations](windows-wechat-bridge.md).

## Development and verification

Use the project Python directly; activating Conda or the venv is unnecessary.

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.test-tmp-dev
.\.venv\Scripts\python.exe tools/check_memory_model.py
.\.venv\Scripts\python.exe tools/check_container.py
.\.venv\Scripts\python.exe tools/check_browser.py
.\tools\build.ps1
```

The browser check requires a paused running browser and reads a public NASA
page, then leaves it paused. The container check uses disposable test resources.
The build script tests, packages, and smoke-tests `dist/LittleBlue/LittleBlue.exe`.
Keep `_internal` beside the EXE. Use `start.ps1` for the configured shared core;
launching the EXE without core arguments uses local mode.

After pulling upstream changes, rerun `setup.ps1` to refresh dependencies and
rebuild services. Docker memory is in the named `little-blue_core-data` volume;
local-mode memory is in `%LOCALAPPDATA%\LittleBlue`. Avoid `docker compose down -v`
unless you intend to erase the container data. `.env`, `.secrets`, `.venv`, models,
downloaded community assets, build output, and test data are ignored by Git.

## Initial setup verification on October 21, 2026

- CPython 3.13.16, PySide6 6.12.0; dependency consistency check passed.
- Full test suite: 287 passed.
- Both Docker services healthy, bound only to localhost.
- Container authentication, offline chat, and persistence across restart passed.
- Real Chromium NASA page reading, screenshots, takeover, and pause checks passed.
- Real local memory model retrieval and index rebuild passed. The verification
  script now releases deferred SQLite objects before removing its temporary
  database on Windows.
- Desktop client connected to Docker and completed its timed smoke test.
- Windows executable built and passed its isolated startup/save/exit check.
- Setup script rerun passed with downloads and already-completed tests skipped.

The user subsequently configured a DeepSeek API key and its connectivity was
checked. Online cognition remains disabled. Live WeChat messaging and real
cross-device token reuse remain unverified.

## Two-device handoff

Use [the handoff guide](handoff.md) to move the latest memories and chat history
between two computers. It stops and locks the source before the receiver starts.
After a handoff, the active Docker volume is selected by `compose.override.yaml`;
it may no longer be `little-blue_core-data`. Preserve that file and `.handoff`.
