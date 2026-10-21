"""Test the built image using an isolated, disposable Docker volume/container.

No DeepSeek key is supplied. Only resources named by this invocation are removed.
"""
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
name = 'little-blue-check-' + uuid.uuid4().hex[:12]
token_file = ROOT / '.secrets/core_token'
token = token_file.read_text(encoding='utf-8').strip()
host = uuid.uuid4().hex


def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Docker operation failed: ' + result.stderr[-1200:])
    return result.stdout.strip()


def request(path, data=None, authenticated=True):
    headers = {'Content-Type': 'application/json'}
    if authenticated:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url + path, headers=headers,
                                 data=json.dumps(data).encode() if data is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=3) as response:
        return json.load(response)


def rpc(op, **data):
    return request('/rpc', {'op': op, 'host': host, **data})


def wait_healthy():
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if request('/healthz', authenticated=False)['status'] == 'ok':
                return
        except (OSError, ValueError):
            pass
        time.sleep(.2)
    raise RuntimeError('Container health check timed out')


created_volume = False
created_container = False
try:
    docker('volume', 'create', name)
    created_volume = True
    docker('run', '-d', '--name', name, '--publish', '127.0.0.1::8765',
           '--mount', f'type=volume,src={name},dst=/data',
           '--mount', f'type=bind,src={ROOT / "assets/memes"},dst=/app/assets/memes,readonly',
           '--mount', f'type=bind,src={token_file},dst=/run/secrets/core_token,readonly',
           '--read-only', '--tmpfs', '/tmp:size=64m,mode=1777',
           '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
           'little-blue-core:local')
    created_container = True
    url = 'http://' + docker('port', name, '8765/tcp').splitlines()[0]
    wait_healthy()
    print('Container healthy; checking authentication and persistence.', flush=True)
    try:
        request('/state', authenticated=False)
        raise AssertionError('Unauthenticated state access allowed')
    except urllib.error.HTTPError as error:
        assert error.code == 401
    initial = rpc('sync', environment={'hour': 12})
    identity = rpc('identity')
    assert identity['enabled'] and identity['revision'] == 0
    assert 'DeepSeek' in identity['core']
    assert not rpc('identity', identity_action='off')['enabled']
    rpc('interact', interaction='pet')
    rpc('remember', text='container persistence check', kind='semantic')
    rpc('configure', settings=initial['settings'])  # Also flush life state.
    trust = request('/state')['life']['needs']['trust']
    task_id = uuid.uuid4().hex
    rpc('submit', text='offline smoke check', task_id=task_id)
    for _ in range(30):
        state = rpc('sync', environment={'hour': 12})
        if any(t['id'] == task_id and t['status'] == 'succeeded' for t in state['tasks']):
            break
        time.sleep(.1)
    else:
        raise AssertionError('Offline chat did not complete')
    before = state['life']['elapsed']
    time.sleep(.6)
    assert request('/state')['life']['elapsed'] > before
    docker('restart', name)
    # Docker may assign a different ephemeral host port on restart.
    url = 'http://' + docker('port', name, '8765/tcp').splitlines()[0]
    wait_healthy()
    restored = rpc('sync', environment={'hour': 12})
    assert restored['instance_id'] != initial['instance_id']
    assert restored['life']['needs']['trust'] == trust
    assert restored['current'] is None
    assert not rpc('identity')['enabled']
    assert rpc('search', text='container persistence check')['rows']
    memory = rpc('memory_manage', memory_command={'action': 'view'})
    memory = rpc('memory_manage', memory_command={'action': 'revise', 'revision': memory['revision'],
        'key': 'user.container_check.status', 'text': 'container fact before', 'reason': 'isolated smoke test'})
    memory = rpc('memory_manage', memory_command={'action': 'revise', 'revision': memory['revision'],
        'key': 'user.container_check.status', 'text': 'container fact after', 'reason': 'isolated correction'})
    assert [r['text'] for r in rpc('search', text='container fact')['rows']
            if r['text'].startswith('container fact')] == ['container fact after']
    memory = rpc('memory_manage', memory_command={'action': 'undo', 'revision': memory['revision']})
    assert [r['text'] for r in rpc('search', text='container fact')['rows']
            if r['text'].startswith('container fact')] == ['container fact before']
    assert len([r for r in memory['rows'] if r['text'].startswith('container fact')]) == 2
    assert any(t['id'] == task_id and t['status'] == 'succeeded' for t in restored['tasks'])
    assert [m['role'] for m in restored['relationship']['messages']] == ['user', 'assistant']
    assert restored['relationship']['messages'][0]['text'] == 'offline smoke check'
    assert restored['relationship']['messages'][0]['origin'] == 'desktop'
    phone = restored['phone']
    phone = rpc('phone', phone={'action': 'auto_off', 'revision': phone['revision']})
    rpc('sync', environment={'hour': 12, 'phone_open': True})
    phone = rpc('phone', phone={'action': 'take', 'app': 'aquarium', 'revision': phone['revision']})
    phone = rpc('phone', phone={'action': 'decorate', 'app': 'aquarium', 'decor': 'moon', 'revision': phone['revision']})
    assert phone['tank']['decor'] == 'moon'
    docker('restart', name)
    url = 'http://' + docker('port', name, '8765/tcp').splitlines()[0]
    wait_healthy()
    phone = request('/state')['phone']
    assert phone['owner'] == 'none' and not phone['autonomous'] and phone['tank']['decor'] == 'moon'
    docker('exec', name, 'python', '-c',
           "import os, importlib.util; from pathlib import Path; "
           "assert os.getuid()==10001; assert importlib.util.find_spec('PySide6') is None; "
           "assert not os.getenv('DEEPSEEK_API_KEY'); "
           "assert not Path('/app/.env').exists(); assert not Path('/app/.secrets').exists()")
    catalog = json.loads((ROOT / 'assets/memes/catalog.json').read_text(encoding='utf-8'))
    expected = sum((ROOT / 'assets/memes' / item.get('source', 'community') /
                    (item['id'] + '.webp')).is_file() for item in catalog['items'])
    docker('exec', name, 'python', '-c',
           'from little_blue.memes import MemeCatalog; '
           f'assert len(MemeCatalog().available()) == {expected}; '
           'from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes; '
           'e=Cipher(algorithms.AES(bytes(16)),modes.ECB()).encryptor(); '
           'assert len(e.update(bytes(16))+e.finalize()) == 16')
    print('PASS: authenticated API, offline life/chat, memory and state after restart, non-root, no Qt/key in test image runtime.')
finally:
    if created_container:
        docker('rm', '-f', name)
    if created_volume:
        docker('volume', 'rm', name)
