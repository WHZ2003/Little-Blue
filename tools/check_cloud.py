"""Exercise the core-only cloud layout with isolated, synthetic Docker resources."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    project = 'dafeiyu-cloud-check-' + uuid.uuid4().hex[:12]
    folder = ROOT / ('.test-tmp-' + project)
    folder.mkdir()
    shutil.copy2(ROOT / 'deploy/cloud/compose.yaml', folder / 'compose.yaml')
    (folder / '.secrets').mkdir()
    (folder / 'assets/memes').mkdir(parents=True)
    (folder / 'models').mkdir()
    token = secrets.token_urlsafe(48)
    for name in ('core_token', 'browser_token', 'bridge_token'):
        (folder / '.secrets' / name).write_text(token)
    env = {**os.environ, 'DAFEIYU_CORE_PORT': '0', 'DAFEIYU_BROWSER_URL': ''}

    def run(*args):
        result = subprocess.run(args, cwd=folder, env=env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr[:2000])
        return result.stdout.strip()

    def compose(*args):
        return run('docker', 'compose', '-p', project, *args)

    try:
        compose('up', '-d', '--no-build', '--wait')
        services = compose('ps', '--services', '--status', 'running').splitlines()
        assert services == ['core'], services
        ident = compose('ps', '--quiet', 'core')
        limits = json.loads(run('docker', 'inspect', '--format', '{{json .HostConfig}}', ident))
        assert limits['Memory'] == 1024**3
        assert limits['MemorySwap'] == 1536 * 1024**2
        assert limits['PortBindings']['8765/tcp'][0]['HostIp'] == '127.0.0.1'
        address = compose('port', 'core', '8765')
        assert address.startswith('127.0.0.1:')
        with httpx.Client(base_url='http://' + address, trust_env=False) as client:
            assert client.get('/state').status_code == 401
            client.headers['Authorization'] = 'Bearer ' + token
            state = client.get('/state').json()
            assert state['wechat'] == 'unbound'
            assert not state['settings']['online']
            assert not state['browser']['available']
        # Persist a synthetic chat, then verify the same volume after restart.
        code = 'from little_blue.storage import Store; s=Store("/data/memory.sqlite3"); s.continuity.append("user","cloud-synthetic","desktop"); s.close()'
        compose('exec', '-T', 'core', 'python', '-c', code)
        compose('restart', 'core')
        for _ in range(30):
            if run('docker', 'inspect', '--format', '{{.State.Health.Status}}', ident) == 'healthy':
                break
            time.sleep(1)
        else:
            raise AssertionError('Core did not become healthy after restart')
        code = 'import sqlite3; c=sqlite3.connect("/data/memory.sqlite3"); assert c.execute("SELECT count(*) FROM conversation WHERE text=?", ("cloud-synthetic",)).fetchone()[0] == 1'
        compose('exec', '-T', 'core', 'python', '-c', code)
        print('PASS: core-only profile, 1 GiB memory cap, loopback API/auth, offline startup, chat persistence after restart.')
    finally:
        # Unique project created above; never targets the daily core or its volume.
        compose('down', '--volumes')


if __name__ == '__main__':
    main()
