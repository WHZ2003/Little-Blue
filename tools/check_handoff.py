"""Two isolated Docker installations; synthetic data only, no API keys or messages."""
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from little_blue.handoff import Manager
from little_blue.handoff_guard import read_state, require_active


def main():
    run_id = uuid.uuid4().hex[:12]
    lab = ROOT / ('.test-tmp-handoff-docker-' + run_id)
    lab.mkdir()
    volumes = set()
    managers = []

    class LabManager(Manager):
        def run(self, *args, **kwargs):
            if args[:3] == ('docker', 'volume', 'create'):
                volumes.add(args[3])
            return super().run(*args, **kwargs)

    def query(manager):
        with manager.api() as client:
            response = client.get('/state')
            response.raise_for_status()
            return response.json()

    def add_dialogue(manager, word):
        code = ('from little_blue.storage import Store; s=Store("/data/memory.sqlite3"); '
                's.continuity.append("user", "' + word + '", "wechat"); '
                's.remember("' + word + '"); s.close()')
        manager.compose('exec', '-T', 'core', 'python', '-c', code)

    def history(manager):
        return manager.compose('exec', '-T', 'core', 'python', '-c',
            'import sqlite3,json; c=sqlite3.connect("/data/memory.sqlite3"); print(json.dumps(c.execute("SELECT text FROM conversation ORDER BY id").fetchall()))')

    try:
        for suffix in ('a', 'b'):
            folder = lab / suffix
            folder.mkdir()
            shutil.copytree(ROOT / 'little_blue', folder / 'little_blue', ignore=shutil.ignore_patterns('__pycache__'))
            (folder / '.secrets').mkdir()
            (folder / '.secrets/core_token').write_text(secrets.token_urlsafe(48))
            volume = 'little-blue-lab-' + run_id + '-' + suffix
            volumes.add(volume)
            compose = f'''name: little-blue-lab-{run_id}-{suffix}
services:
  core:
    image: little-blue-core:handoff-test
    init: true
    restart: unless-stopped
    ports: ["127.0.0.1::8765"]
    environment:
      LITTLE_BLUE_DATA_DIR: /data
    volumes: ["core-data:/data"]
    secrets: [core_token]
volumes:
  core-data:
    name: {volume}
secrets:
  core_token:
    file: ./.secrets/core_token
'''
            (folder / 'compose.yaml').write_text(compose)
            manager = LabManager(folder)
            managers.append(manager)
            manager.compose('up', '-d', '--wait', timeout=120)
        a, b = managers
        add_dialogue(a, 'before-transfer')
        request_b = lab / 'b-request.json'
        b.prepare(request_b)
        outgoing = lab / 'a-to-b.lbhand'
        a.send(request_b, outgoing)
        assert not a.container()['running'] and not b.container()['running']
        # Restart attempts fail inside the source volume, even bypassing PS scripts.
        a.run('docker', 'start', a.container()['id'])
        time.sleep(2)
        assert not a.container()['running']
        try:
            require_active(a.control)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Source host was not fenced')
        receipt_b = lab / 'b-receipt.json'
        b.receive(outgoing, receipt_b)
        assert 'before-transfer' in history(b)
        assert not a.container()['running'] and b.container()['running']
        b.receive(outgoing, receipt_b)  # Same command retry must be harmless.
        a.finish(receipt_b)
        assert read_state(a.control)['phase'] == 'retired'
        add_dialogue(b, 'after-transfer')
        request_a = lab / 'a-request.json'
        a.prepare(request_a)
        returning = lab / 'b-to-a.lbhand'
        b.send(request_a, returning)
        assert not a.container()['running'] and not b.container()['running']
        a.receive(returning, lab / 'a-receipt.json')
        b.finish(lab / 'a-receipt.json')
        final_history = history(a)
        assert 'before-transfer' in final_history and 'after-transfer' in final_history
        assert read_state(a.control)['generation'] == 2
        assert not b.container()['running']
        try:
            b.receive(outgoing, receipt_b)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Old package replay allowed after handoff back')
        assert query(a)['handoff']['requested'] is False
        print('PASS: A -> B -> A, latest chat/remembered data, stop-before-start, source restart fence, receipt and replay protection.')
    finally:
        for manager in managers:
            try:
                manager.compose('down', timeout=60)
            except Exception:
                print('Test project cleanup needs attention:', manager.root.name)
        for volume in volumes:
            # Only exact resource names created during this invocation.
            result = subprocess.run(['docker', 'volume', 'rm', volume], capture_output=True)
            if result.returncode:
                print('Retained test volume:', volume)


if __name__ == '__main__':
    main()
