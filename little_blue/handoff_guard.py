"""Durable fencing for a cooperative, offline two-device handoff."""
import json
import os
from pathlib import Path


def read_state(folder):
    path = Path(folder) / 'handoff.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def write_state(folder, state):
    path = Path(folder) / 'handoff.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def require_active(folder):
    state = read_state(folder)
    if state and state.get('phase') != 'active':
        raise RuntimeError('This data copy is locked for handoff. Use handoff.ps1; do not rebind WeChat or restart this copy.')
