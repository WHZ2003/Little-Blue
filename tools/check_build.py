"""Smoke-test a frozen build without inheriting Python/Conda search paths."""
import os
from pathlib import Path
import sqlite3
import subprocess
import uuid

root = Path(__file__).resolve().parents[1]
exe = root / 'dist/LittleBlue/LittleBlue.exe'
data = root / 'data' / ('frozen-smoke-' + uuid.uuid4().hex[:8])
env = dict(os.environ)
for name in list(env):
    if name.startswith(('PYTHON', 'CONDA', 'QT_')):
        env.pop(name)
env['PATH'] = str(Path(env.get('WINDIR', 'C:/Windows')) / 'System32')
env['QT_QPA_PLATFORM'] = 'offscreen'
env['DEEPSEEK_API_KEY'] = ''
result = subprocess.run([str(exe), '--data-dir', str(data), '--panel', '--smoke-seconds', '3'],
                        env=env, cwd=root, timeout=30, capture_output=True)
if result.returncode:
    raise RuntimeError(f'Frozen app failed: {result.returncode}; {result.stderr.decode(errors="replace")}')
with sqlite3.connect(data / 'memory.sqlite3') as db:
    state = db.execute("SELECT value FROM kv WHERE key='life'").fetchone()
    assert state and 'energy' in state[0], 'Graceful state saving did not complete'
assert not (data / 'app.lock').exists(), 'Single-instance lock was not released'
print('Frozen EXE passed: isolated PATH, Qt widgets, sprites, asyncio worker, SQLite, graceful save and exit.')
