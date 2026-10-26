import hashlib
import pytest
from little_blue.storage import Store
from little_blue.companion import exchange, setup, profile, MemoryFile, memory_context

CLIENT='c'*32
CARD='当前原样人设\n不许替换。'

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'memory.sqlite3');setup(s)
    with s.db:s.db.execute('CREATE TABLE diaries(day TEXT PRIMARY KEY,body TEXT)')
    yield s
    s.close()

def sync(store, **kwargs):
    return exchange(store, {'client':CLIENT,'persona':CARD,**kwargs})

def test_replay_atomic_conflict_and_persona_pinned(store):
    e={'key':'event:1','role':'user','text':'本地说的话','created':1000}
    sync(store,events=[e]);sync(store,events=[e])
    assert store.db.execute('SELECT count(*) FROM conversation').fetchone()[0]==1
    with pytest.raises(ValueError):
        sync(store,events=[{**e,'key':'event:2'},{**e,'text':'被改写'}])
    assert store.db.execute('SELECT count(*) FROM conversation').fetchone()[0]==1
    with pytest.raises(ValueError):exchange(store,{'client':CLIENT,'persona':'改人设'})
    with pytest.raises(PermissionError):exchange(store,{'client':'d'*32,'persona':CARD})
    assert profile(store)['persona']==CARD
    assert profile(store)['sha256']==hashlib.sha256(CARD.encode()).hexdigest()

def test_all_history_preserves_failed_drafts_and_files_versions(store):
    for delivery in ('legacy','delivered','failed','superseded','pending'):
        store.continuity.append('assistant','原话-'+delivery,'wechat',delivery=delivery)
    mid=store.remember('原来的记忆')
    out=sync(store,files=[{'path':'PARTNER.md','text':'画像旧版'}])
    assert out['counts']=={'conversation':5,'memories':1,'diaries':0}
    assert {r['delivery'] for r in out['conversation']}=={'legacy','delivered','failed','superseded','pending'}
    assert out['memories'][0]['id']==mid
    sync(store,files=[{'path':'PARTNER.md','text':'画像新版'}])
    sync(store,files=[{'path':'PARTNER.md','text':'画像新版'}])
    assert store.db.execute('SELECT count(*) FROM companion_file_versions').fetchone()[0]==2
    assert memory_context(store,'画像')[0]['text']=='画像新版'
    sync(store,removed=['PARTNER.md'])
    assert memory_context(store,'画像')==[]
    assert store.db.execute('SELECT count(*) FROM companion_file_versions').fetchone()[0]==2

def test_imported_old_messages_do_not_replace_latest_context(store):
    from little_blue.companion import history
    store.continuity.append('user','最新微信','wechat')
    sync(store,events=[{'key':'old:1','role':'user','text':'离线旧消息','created':1000}])
    assert history(store)[-1]['text']=='最新微信'

@pytest.mark.parametrize('path',['../secret.md','/absolute.md','.env','shared/history.md','a\\b.md','CONSTITUTION.md','a/../b.md','x/.hidden.md','C:/file.md'])
def test_files_cannot_touch_persona_or_private_paths(path):
    with pytest.raises(ValueError):MemoryFile(path=path,text='x')

def test_paging_and_delivery_update_tail(store):
    for i in range(503):store.continuity.append('assistant',str(i),'wechat',delivery='pending' if i==502 else 'legacy')
    out=sync(store)
    assert len(out['conversation'])==500 and out['more']
    end=sync(store,after=out['after']);assert len(end['conversation'])==3 and not end['more']
    store.continuity.delivered(503,True)
    repaired=sync(store,after=end['after'])
    assert not repaired['conversation'] and repaired['tail'][-1]['delivery']=='delivered'

async def test_chat_uses_exact_card_and_not_legacy_style(store):
    from little_blue.cognition import Agent,DeepSeekClient
    from little_blue.models import Settings
    from little_blue.tools import ToolRuntime
    import httpx,json
    sync(store,files=[{'path':'PARTNER.md','text':'共同的小事'}])
    settings=Settings(online=True,daily_tokens=500000)
    async def approve(*a):return False
    seen=[]
    def handle(request):
        body=json.loads(request.content);seen.append(body)
        assert body['messages'][0]['content']==CARD
        assert '共同的小事' in json.dumps(body,ensure_ascii=False)
        instructions = '\n'.join(m['content'] for m in body['messages'] if m['role'] == 'system')
        assert '共享偏好' in instructions
        assert '微信每条消息最多30个字' not in instructions
        return httpx.Response(200,json={'choices':[{'message':{'content':'库...'}}]})
    agent=Agent(store,settings,ToolRuntime(store,settings,approve),DeepSeekClient(store,settings,httpx.MockTransport(handle),api_key='test'))
    assert await agent.run('你好',{},chat_only=True)=='库...'
    assert len(seen)==1

async def test_replication_endpoint_auth_and_drain(tmp_path):
    from little_blue.service import create_app,CORE_KEY
    from aiohttp.test_utils import TestClient,TestServer
    token='test-token'*5
    app=create_app(Store(tmp_path/'api.db'),token)
    async with TestClient(TestServer(app)) as c:
        data={'client':CLIENT,'persona':CARD}
        assert (await c.post('/companion/sync',json=data)).status==401
        headers={'Authorization':'Bearer '+token}
        assert (await c.post('/companion/sync',json=data,headers={**headers,'Origin':'https://evil.test'})).status==403
        r=await c.post('/companion/sync',json=data,headers=headers);assert r.status==200
        app[CORE_KEY].handoff_requested=True
        assert (await c.post('/companion/sync',json=data,headers=headers)).status==409


async def test_preferences_are_paired_bounded_persistent_and_conflict_checked(tmp_path):
    from little_blue.service import create_app, CORE_KEY
    from aiohttp.test_utils import TestClient, TestServer
    app = create_app(Store(tmp_path/'preferences.db'), 'token'*10)
    async with TestClient(TestServer(app)) as client:
        headers = {'Authorization': 'Bearer '+'token'*10}
        read = {'client': CLIENT, 'action': 'read'}
        url = '/companion/preferences'
        assert (await client.post(url, json=read)).status == 401
        assert (await client.post(url, json=read, headers=headers)).status == 403
        await client.post('/companion/sync', json={'client': CLIENT, 'persona': CARD}, headers=headers)
        assert (await client.post(url, json=read, headers={**headers, 'Origin': 'https://evil.test'})).status == 403
        assert (await client.post(url, json={**read, 'client': 'b'*32}, headers=headers)).status == 403
        before = await (await client.post(url, json=read, headers=headers)).json()
        original = app[CORE_KEY].settings.model_dump()
        values = {'proactive': False, 'proactive_cooldown': 7200, 'proactive_per_hour': 1}
        save = {**read, 'action': 'save', 'values': values, 'expected': before['values']}
        for invalid in ({**values, 'model': 'other'}, {**values, 'proactive_cooldown': 59},
                        {**values, 'proactive_per_hour': 13}, {**values, 'proactive': 'false'}):
            assert (await client.post(url, json={**save, 'values': invalid}, headers=headers)).status == 400
        response = await client.post(url, json=save, headers=headers)
        assert response.status == 200 and (await response.json())['values'] == values
        assert app[CORE_KEY].settings.model_dump() == {**original, **values}
        assert all(app[CORE_KEY].store.get('settings')[k] == v for k, v in values.items())
        assert profile(app[CORE_KEY].store)['persona'] == CARD
        assert (await client.post(url, json=save, headers=headers)).status == 409
        assert (await client.post(url, json={**read, 'values': values}, headers=headers)).status == 400
        app[CORE_KEY].handoff_requested = True
        assert (await client.post(url, json=read, headers=headers)).status == 409
