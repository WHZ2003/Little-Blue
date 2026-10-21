"""Inspect PE import dependencies in a Windows build."""
from pathlib import Path
import pefile

root = Path(__file__).resolve().parents[1] / 'dist/LittleBlue/_internal'
files = {p.name.lower(): p for p in root.rglob('*') if p.suffix.lower() in ('.dll', '.pyd')}
system = Path('C:/Windows/System32')
missing = set()
for path in files.values():
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
    for entry in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
        name = entry.dll.decode().lower()
        if name not in files and not (system / name).exists() and not name.startswith(('api-ms', 'ext-ms')):
            missing.add((path.name, name))
for item in sorted(missing):
    print(item)
