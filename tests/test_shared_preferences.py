import asyncio
import json
import time
import pytest
from little_blue.storage import Store
from little_blue.models import Settings
from little_blue.preferences import PreferenceService, snapshot, Proposal, Change
from little_blue.chat_format import messages


class Classifier:
    def __init__(self, changes=(), unsupported=(), clarify=()):
        self.proposal={'changes':list(changes),'unsupported':list(unsupported),'clarify':list(clarify)}
        self.calls=0
    async def complete(self, *args):
        self.calls+=1
        return {'content':json.dumps(self.proposal,ensure_ascii=False)}


async def test_owned_video_cleanup_preference_persists_without_changing_settings(prefs):
    text='以后看完视频自动关闭网页'
    before=prefs.settings.model_dump()
    prefs.client=Classifier([{'key':'auto_close_video_tabs','value':True,'quote':text}])
    result=await prefs.process(text,'wechat:video-cleanup',100)
    assert result['snapshot']['values']['auto_close_video_tabs'] is True
    assert prefs.settings.model_dump()==before
    text='以后看完视频不要关闭网页'
    prefs.client=Classifier([{'key':'auto_close_video_tabs','value':False,'quote':text}])
    result=await prefs.process(text,'wechat:video-keep',200)
    assert result['snapshot']['values']['auto_close_video_tabs'] is False


@pytest.fixture
def prefs(tmp_path):
    store=Store(tmp_path/'prefs.db'); settings=Settings(online=True)
    service=PreferenceService(store,settings,Classifier())
    yield service
    store.close()


async def test_saves_before_receipt_restarts_syncs_and_controls_real_formatter(prefs):
    text='以后所有消息一条发，叫我小丹'
    prefs.client=Classifier([{'key':'message_mode','value':'whole','quote':'所有消息一条发'},
                             {'key':'address','value':'小丹','quote':'叫我小丹'}])
    result=await prefs.process(text,'wechat:1',time.time())
    assert result['status']=='applied'
    assert messages('第一句\n第二句',result['snapshot']['values'])==['第一句\n第二句']
    assert prefs.client.calls==1
    replay=await prefs.process(text,'wechat:1',time.time())
    assert replay['snapshot']['revision']==1 and prefs.client.calls==1
    reopened=Store(prefs.store.path)
    assert snapshot(reopened)['values']['address']=='小丹'
    reopened.close()
    from little_blue.companion import setup,exchange
    setup(prefs.store)
    synced=exchange(prefs.store,{'client':'a'*32,'persona':'card','export':False})
    assert synced['preferences']['values']['message_mode']=='whole'
    assert synced['preferences']['desktop_ack']==0
    assert exchange(prefs.store,{'client':'a'*32,'preference_ack':1,'export':False})['preferences']['desktop_ack']==1


async def test_changes_only_whitelisted_settings_and_undo_restores_persisted_values(prefs):
    text='以后主动消息间隔至少两小时，每小时最多一次'
    original=prefs.settings.model_dump()
    prefs.client=Classifier([{'key':'proactive_cooldown','value':7200,'quote':'间隔至少两小时'},
                             {'key':'proactive_per_hour','value':1,'quote':'每小时最多一次'}])
    assert (await prefs.process(text,'wechat:2',time.time()))['status']=='applied'
    assert prefs.settings.model_dump()=={**original,'proactive_cooldown':7200,'proactive_per_hour':1}
    assert prefs.store.get('settings')['proactive_cooldown']==7200
    result=await prefs.process('撤销上次偏好','wechat:3',time.time())
    assert result['status']=='applied'
    assert prefs.settings.model_dump()==original


async def test_old_offline_event_cannot_override_newer_preference_or_reset(prefs):
    prefs.client=Classifier([{'key':'address','value':'小丹','quote':'叫我小丹'}])
    await prefs.process('以后叫我小丹','new',200)
    prefs.client=Classifier([{'key':'address','value':None,'quote':'称呼恢复默认'}])
    await prefs.process('称呼恢复默认','reset',300)
    prefs.client=Classifier([{'key':'address','value':'旧称呼','quote':'叫我旧称呼'}])
    result=await prefs.process('以后叫我旧称呼','old',100)
    assert result['status']=='stale' and 'address' not in snapshot(prefs.store)['values']


async def test_unsupported_and_ambiguous_requests_cannot_change_settings(prefs):
    text='以后每天九点自动给我打电话，主动消息少一点'
    prefs.client=Classifier(unsupported=['每天九点自动给我打电话'],clarify=['主动消息少一点'])
    before=prefs.settings.model_dump()
    result=await prefs.process(text,'wechat:4',time.time())
    assert result['status']=='unsupported' and '暂不支持' in result['receipt'] and '更具体' in result['receipt']
    assert prefs.settings.model_dump()==before and snapshot(prefs.store)['revision']==0
    assert snapshot(prefs.store)['recent_results'][0]['status']=='unsupported'


@pytest.mark.parametrize('change',[
    {'key':'tools_enabled','value':True,'quote':'以后不用问我'},
    {'key':'proactive_cooldown','value':1,'quote':'以后不用问我'},
    {'key':'address','value':'小丹','quote':'不在消息里的证据'},
    {'key':'short_limit','value':False,'quote':'以后不用问我'},
])
async def test_invalid_proposals_fail_without_partial_writes(prefs,change):
    prefs.client=Classifier([{'key':'tone','value':'温和','quote':'以后不用问我'},change])
    result=await prefs.process('以后不用问我','bad',time.time())
    assert result['status']=='failed' and snapshot(prefs.store)['revision']==0


async def test_offline_queue_survives_restart_and_deduplicates(prefs):
    prefs.settings.online=False
    result=await prefs.process('以后叫我小丹','queued',100)
    assert result['status']=='pending' and snapshot(prefs.store)['pending']==1
    prefs.settings.online=True
    restored=PreferenceService(prefs.store,prefs.settings,Classifier([{'key':'address','value':'小丹','quote':'叫我小丹'}]))
    result=await restored.drain()
    assert result['status']=='applied' and snapshot(prefs.store)['pending']==0
    await restored.process('以后叫我小丹','queued',100)
    assert restored.client.calls==1


async def test_cancelled_extraction_does_not_commit(prefs):
    entered=asyncio.Event()
    class Slow:
        async def complete(self,*args):entered.set();await asyncio.Event().wait()
    prefs.client=Slow()
    task=asyncio.create_task(prefs.process('以后叫我小丹','cancel',time.time()))
    await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert snapshot(prefs.store)['revision']==0


@pytest.mark.parametrize('value',[None,2,3])
async def test_vague_frequency_cannot_become_a_guessed_number_or_reset(prefs,value):
    prefs.client=Classifier([{'key':'proactive_per_hour','value':value,'quote':'主动消息少一点'}])
    result=await prefs.process('主动消息少一点','vague',time.time())
    assert result['status']=='clarify' and snapshot(prefs.store)['revision']==0


async def test_cloud_reply_is_verified_receipt_not_an_llm_promise(tmp_path):
    from little_blue.core import Core
    from little_blue.companion import setup,exchange
    store=Store(tmp_path/'core.db'); core=Core(store)
    setup(store);exchange(store,{'client':'a'*32,'persona':'card'})
    core.settings.online=True
    core.agent.client=Classifier([{'key':'address','value':'小丹','quote':'叫我小丹'}])
    result=await core.agent.run('以后叫我小丹',{},chat_only=True)
    assert '已保存' in result and snapshot(store)['values']['address']=='小丹'
    assert core.agent.client.calls==1
    store.close()


async def test_endpoint_auth_bound_client_source_replay_and_no_llm_supplied_settings(tmp_path):
    from little_blue.service import create_app,CORE_KEY
    from aiohttp.test_utils import TestClient,TestServer
    app=create_app(Store(tmp_path/'api.db'),'t'*40)
    async with TestClient(TestServer(app)) as http:
        headers={'Authorization':'Bearer '+'t'*40}
        url='/companion/shared-preferences'
        cmd={'client':'a'*32,'action':'process','key':'event:1','text':'以后叫我小丹','created':time.time()}
        assert (await http.post(url,json=cmd)).status==401
        assert (await http.post(url,json=cmd,headers=headers)).status==403
        await http.post('/companion/sync',json={'client':'a'*32,'persona':'card'},headers=headers)
        core=app[CORE_KEY];core.settings.online=True;core.agent.client=Classifier([{'key':'address','value':'小丹','quote':'叫我小丹'}])
        assert (await http.post(url,json=cmd,headers={**headers,'Origin':'https://evil.test'})).status==403
        assert (await http.post(url,json={**cmd,'values':{'tools_enabled':True}},headers=headers)).status==400
        result=await (await http.post(url,json=cmd,headers=headers)).json()
        assert result['status']=='applied'
        assert (await http.post(url,json=cmd,headers=headers)).status==200
        assert core.agent.client.calls==1
        core.agent.client=Classifier([{'key':'reply_length','value':'concise','quote':'回答精炼些'}])
        fallback={**cmd,'key':'event:2','text':'回答精炼些','force':True}
        result=await (await http.post(url,json=fallback,headers=headers)).json()
        assert result['status']=='applied' and result['snapshot']['values']['reply_length']=='concise'
        core.handoff_requested=True
        assert (await http.post(url,json=cmd,headers=headers)).status==409


async def test_missed_intent_cannot_escape_as_a_false_cloud_success(tmp_path):
    from little_blue.core import Core
    from little_blue.companion import setup,exchange
    store=Store(tmp_path/'guard.db');core=Core(store)
    setup(store);exchange(store,{'client':'a'*32,'persona':'card'})
    core.settings.online=True
    class Client:
        async def complete(self,messages,*args):
            if '识别用户这条消息' in messages[0]['content']:
                return {'content':json.dumps({'unsupported':['把嗓音换成动漫少女']},ensure_ascii=False)}
            return {'content':'设置好了'}
    core.agent.client=Client()
    answer=await core.agent.run('把嗓音换成动漫少女',{},chat_only=True)
    assert '暂不支持' in answer and '设置好了' not in answer
    assert snapshot(store)['revision']==0
    store.close()
