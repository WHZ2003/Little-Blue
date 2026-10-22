"""Exercise the real core↔Hermes containers with a synthetic provider and fresh volumes."""
import json
from pathlib import Path
import secrets
import subprocess
import time
import uuid

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = '''
import asyncio,json
from pathlib import Path
from aiohttp import web
from little_blue.service import create_app,CORE_KEY
from little_blue.storage import Store
from little_blue.hermes import HermesBridge
class FakeProvider:
    async def complete(self,messages,tools=None):
        last_user=next((m.get('content','') for m in reversed(messages) if m['role']=='user'),'')
        if last_user=='SLOW_SYNTHETIC':
            await asyncio.sleep(60)
        if tools and messages[-1]['role']=='user':
            return {'role':'assistant','content':None,'tool_calls':[{'id':'call_memory','type':'function','function':{'name':'dafeiyu_memory_search','arguments':'{"query":"synthetic"}'}}]}
        return {'role':'assistant','content':'HERMES_APP_OK'}
s=Store('/data/memory.sqlite3')
if not s.get('fixture_ready'):
    s.remember('synthetic remembered fact','semantic')
    s.put('settings',{'online':True,'proactive':False,'tools_enabled':False})
    s.put('fixture_ready',True)
app=create_app(s,Path('/run/secrets/core_token').read_text().strip(),client=FakeProvider())
app[CORE_KEY].agent.harness=HermesBridge(app[CORE_KEY].agent)
web.run_app(app,host='0.0.0.0',port=8765,access_log=None,print=None)
'''


def main():
    project = 'hermes-app-check-' + uuid.uuid4().hex[:10]
    folder = ROOT / ('.test-tmp-' + project)
    folder.mkdir()
    (folder / '.secrets').mkdir()
    (folder / 'fixture.py').write_text(FIXTURE, encoding='utf-8')
    token = secrets.token_urlsafe(48)
    for name in ('core_token', 'browser_token', 'bridge_token', 'hermes_token'):
        (folder / '.secrets' / name).write_text(token)
    config = yaml.safe_load((ROOT / 'deploy/cloud/compose.hermes.yaml').read_text())
    config['name'] = project
    config['services']['hermes']['image'] = 'dafeiyu-hermes:55ee4ac'
    config['networks']['default'] = {}
    config['volumes'] = {'core-data': {}}
    config['secrets'].update({n: {'file': './.secrets/' + n} for n in ('core_token', 'browser_token', 'bridge_token')})
    config['services']['core'].update(image='little-blue-core:hermes-55ee4ac',
        command=['python', '/fixture.py'], volumes=['core-data:/data', './fixture.py:/fixture.py:ro'],
        ports=['127.0.0.1::8765'], read_only=True, tmpfs=['/tmp:size=64m,mode=1777'],
        healthcheck={'test': ['CMD', 'python', '-c', "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz').read()"], 'interval': '2s', 'timeout': '2s', 'retries': 15})
    (folder / 'compose.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
    def compose(*args):
        result = subprocess.run(['docker','compose',*args],cwd=folder,capture_output=True,text=True,timeout=150)
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:])
        return result.stdout.strip()
    try:
        compose('up','-d','--wait')
        address = compose('port','core','8765')
        host = uuid.uuid4().hex
        with httpx.Client(base_url='http://' + address,headers={'Authorization':'Bearer '+token},trust_env=False,timeout=15) as client:
            def rpc(op, **kwargs):
                r=client.post('/rpc',json={'op':op,'host':host,**kwargs});r.raise_for_status();return r.json()
            def wait_task(task):
                for _ in range(70):
                    state=rpc('sync',environment={'hour':12})
                    row=next((t for t in state['tasks'] if t['id']==task),{})
                    if row.get('status') in ('succeeded','failed','cancelled'):
                        return row
                    time.sleep(.5)
                raise AssertionError('Task timeout')
            assert client.get('/state').json()['harness']=='hermes'
            rpc('sync',environment={'hour':12})
            task=uuid.uuid4().hex;rpc('submit',text='SYNTHETIC_CHAT',task_id=task)
            assert wait_task(task)['status']=='succeeded'
            task=uuid.uuid4().hex;rpc('submit',text='SLOW_SYNTHETIC',task_id=task)
            time.sleep(3);rpc('cancel')
            assert wait_task(task)['status']=='cancelled'
            task=uuid.uuid4().hex;rpc('submit',text='SYNTHETIC_AFTER_CANCEL',task_id=task)
            assert wait_task(task)['status']=='succeeded'
        code='import sqlite3; c=sqlite3.connect("/data/memory.sqlite3"); assert c.execute("SELECT COUNT(*) FROM conversation WHERE text=?",("HERMES_APP_OK",)).fetchone()[0]==2'
        compose('exec','-T','core','python','-c',code)
        compose('restart','core','hermes');compose('up','-d','--wait')
        compose('exec','-T','core','python','-c',code)
        result=compose('exec','-T','core','python','-m','little_blue.memory_transfer','export','--data-dir','/data','--output','/tmp/hermes-backup.zip')
        manifest=json.loads(result)['manifest']
        assert any(f['path']=='hermes/state.db' for f in manifest['harness_files'])
        print('PASS: core/Hermes provider/tool roundtrip, cancellation and next turn, restart persistence, combined backup.')
    finally:
        compose('down','--volumes')


if __name__ == '__main__':
    main()
