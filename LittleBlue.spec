from pathlib import Path
import sys
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH)
conda_bin = Path(sys.base_prefix) / 'Library' / 'bin'
extra_binaries = [(str(conda_bin / name), '.') for name in
                  ('ffi.dll', 'sqlite3.dll', 'libmpdec-4.dll') if (conda_bin / name).is_file()]
metadata = []
for distribution in ('PySide6', 'PySide6_Essentials', 'shiboken6', 'httpx', 'httpcore',
                     'anyio', 'pydantic', 'pydantic_core', 'certifi', 'idna'):
    metadata += copy_metadata(distribution)
a = Analysis([str(root / 'launch.py')], pathex=[str(root)],
             binaries=extra_binaries,
             datas=[(str(root / 'assets'), 'assets'), (str(root / 'README.md'), '.'),
                    (str(root / 'docs'), 'docs'),
                    (str(root / 'little_blue/dialogue_examples.json'), 'little_blue')] + metadata,
             hiddenimports=[], excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
                                         'PySide6.QtPdf', 'tkinter', 'numpy', 'PIL', 'pytest'])
pyz = PYZ(a.pure)
# Qt on Windows imports the OS ICU ABI (unsuffixed symbols). Conda's icuuc.dll
# exports version-suffixed symbols and must not shadow the Windows system DLL.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icudt78.dll'}]
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LittleBlue',
          debug=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LittleBlue')
