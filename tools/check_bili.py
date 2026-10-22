"""Actual Hermes tool loop with synthetic Bilibili data in disposable containers."""
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
import json,sys
sys.path.insert(0,'/app')
from pathlib import Path
from aiohttp import web
from little_blue.service import create_app,CORE_KEY
from little_blue.storage import Store
from little_blue.hermes import HermesBridge
BV='BV1234567890'
TEXT='Synthetic subtitle about deep sea biology and pressure adaptation.'
class FakeProvider:
 async def complete(self,messages,tools=None):
  names={t['function']['name'] for t in tools or []}
  if 'bili_search' not in names:return {'role':'assistant','content':'CHAT_AFTER_BILI_OK'}
  assert names=={'bili_search','bili_subtitle','bili_comments','bili_video_info'},names
  called=[m.get('name') for m in messages if m['role']=='tool']
  count=sum(m['role']=='tool' for m in messages)
  if count==0:name,args='bili_search',{'keyword':'deep sea'}
  elif count==1:name,args='bili_subtitle',{'bvid':BV}
  else:return {'role':'assistant','content':json.dumps({'bvid':BV,'quote':TEXT[:30],'note':'I am curious about deep sea life.'})}
  return {'role':'assistant','content':None,'tool_calls':[{'id':'call_'+str(count),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
class FakeBili:
 login_state='idle';login_key=None
 def bound(self):return True
 async def search(self,keyword):return [{'bvid':BV,'title':'Synthetic marine biology','url':'https://www.bilibili.com/video/'+BV+'/'}]
 async def subtitle(self,bvid):return {'bvid':BV,'title':'Synthetic marine biology','url':'https://www.bilibili.com/video/'+BV+'/','status':'ok','text':TEXT}
s=Store('/data/memory.sqlite3')
s.put('settings',{'online':True,'proactive':False,'tools_enabled':False})
s.put('bili_settings',{'enabled':True})
app=create_app(s,Path('/run/secrets/core_token').read_text().strip(),client=FakeProvider())
c=app[CORE_KEY];c.agent.harness=HermesBridge(c.agent);c.bili.client=FakeBili()
web.run_app(app,host='0.0.0.0',port=8765,access_log=None,print=None)
'''


def main():
    project = 'bili-check-' + uuid.uuid4().hex[:10]
    folder = ROOT / ('.test-tmp-' + project)
    (folder / '.secrets').mkdir(parents=True)
    (folder / 'fixture.py').write_text(FIXTURE, encoding='utf-8')
    token = secrets.token_urlsafe(48)
    for name in ('core_token','browser_token','bridge_token','hermes_token'):
        (folder / '.secrets' / name).write_text(token)
    config = yaml.safe_load((ROOT / 'deploy/cloud/compose.hermes.yaml').read_text())
    config.update(name=project, volumes={'core-data':{}})
    config['networks']['default'] = {}
    config['services']['hermes']['image'] = 'dafeiyu-hermes:bili-v1'
    config['secrets'].update({n:{'file':'./.secrets/'+n} for n in ('core_token','browser_token','bridge_token')})
    config['services']['core'].update(image='little-blue-core:bili-v1', command=['python','/fixture.py'],
        volumes=['core-data:/data','./fixture.py:/fixture.py:ro'], ports=['127.0.0.1::8765'],
        read_only=True,tmpfs=['/tmp:size=64m,mode=1777'])
    (folder / 'compose.yaml').write_text(yaml.safe_dump(config))
    def compose(*args):
        r = subprocess.run(['docker','compose',*args],cwd=folder,capture_output=True,text=True,timeout=150)
        if r.returncode:raise RuntimeError(r.stderr[-2000:])
        return r.stdout.strip()
    try:
        compose('up','-d','--wait')
        address=compose('port','core','8765')
        with httpx.Client(base_url='http://'+address,headers={'Authorization':'Bearer '+token},trust_env=False,timeout=20) as c:
            r=c.post('/bili',json={'action':'run'});r.raise_for_status()
            for _ in range(60):
                s=c.post('/bili',json={'action':'status'}).json()
                if not s['active']:break
                time.sleep(1)
            assert s['last_run']['status']=='succeeded',s
            assert s['recent'][0]['status']=='read',s
            assert c.post('/bili',json={'action':'run'}).status_code==409
            host=uuid.uuid4().hex
            c.post('/rpc',json={'op':'sync','host':host,'environment':{'hour':12}}).raise_for_status()
            ident=uuid.uuid4().hex
            c.post('/rpc',json={'op':'submit','host':host,'task_id':ident,'text':'SYNTHETIC_AFTER_BILI'}).raise_for_status()
            for _ in range(60):
                state=c.post('/rpc',json={'op':'sync','host':host,'environment':{'hour':12}}).json()
                row=next(t for t in state['tasks'] if t['id']==ident)
                if row['status'] in ('succeeded','failed'):break
                time.sleep(1)
            assert row['status']=='succeeded',row
        compose('restart','core','hermes');compose('up','-d','--wait')
        address=compose('port','core','8765')
        with httpx.Client(base_url='http://'+address,headers={'Authorization':'Bearer '+token},trust_env=False) as c:
            s=c.post('/bili',json={'action':'status'}).json()
            assert s['recent'][0]['status']=='read'
        print('PASS: real Hermes Bilibili tool loop, evidence persistence, private-memory isolation, quota and subsequent chat.')
    except Exception:
        print(compose('logs','--tail','30','core'))
        raise
    finally:
        compose('down','--volumes')


if __name__ == '__main__':main()
