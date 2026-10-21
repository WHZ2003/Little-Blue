import asyncio
import hashlib
import time

import pytest
from little_blue.core import Core
from little_blue.storage import Store


@pytest.fixture
async def core(tmp_path):
    c=Core(Store(tmp_path/'memory.db'))
    c.settings.online=True
    c.wechat_ready=True
    c.gate.started=time.time()-100
    now=time.time()
    for i in range(100):
        url=f'https://science.nasa.gov/earth/?case={i}'
        if int(hashlib.sha256((url+str(now)).encode()).hexdigest()[:8],16)%100<50:
            break
    c.store.put('browser_share_candidate',{'url':url,'title':'Earth','observed_at':now,'excerpt':'Observed real text.'})
    yield c
    await c.browser_share.close()
    c.store.close()


async def test_share_uses_actual_link_without_pretending_delivery(core):
    class Client:
        async def complete(self,*args):
            return {'content':'{"text":"刚刷到这个，有点意思诶。"}'}
    core.agent.client=Client()
    assert core.browser_share.eligible(time.time())
    await core.browser_share.produce(time.time())
    event=core.events[-1]
    assert event['kind']=='browser_share'
    assert event['text'].endswith(core.store.get('browser_share_candidate')['url'])
    assert core.store.continuity.recent()[-1]['delivery']=='pending'
    assert not core.store.search('刚刷到')
    assert not core.browser_share.eligible(time.time()+900)


async def test_uninteresting_page_is_silent_and_cannot_retry_after_restart(core):
    class Client:
        async def complete(self,*args):
            return {'content':'{"text":null}'}
    core.agent.client=Client()
    await core.browser_share.produce(time.time())
    assert not core.events and not core.store.continuity.recent()
    assert not core.browser_share.eligible(time.time()+3000)


async def test_pause_during_generation_discards_share(core):
    class Client:
        async def complete(self,*args):
            core.store.put('browser_sharing_enabled',False)
            return {'content':'{"text":"想发一句"}'}
    core.agent.client=Client()
    await core.browser_share.produce(time.time())
    assert not core.events


def test_limits_context_and_missing_evidence(core):
    assert core.browser_share.eligible(time.time())
    core.store.put('browser_share_attempts',[time.time()-100])
    assert not core.browser_share.eligible(time.time())
    core.store.put('browser_share_attempts',[])
    core.wechat_ready=False
    assert not core.browser_share.eligible(time.time())
    core.wechat_ready=True
    core.store.put('browser_share_candidate',None)
    assert not core.browser_share.eligible(time.time())


async def test_ilink_shares_once_and_records_only_confirmed_delivery(core):
    from little_blue.wechat import Account, WeChatChannel
    class API:
        def __init__(self): self.sent=[]
        async def send(self,context,text): self.sent.append(text)
    api=API()
    channel=WeChatChannel(core,Account(token='test',owner='owner',bot='bot'),api)
    channel.context='context'
    channel.context_time=time.time()-120
    core.wechat_state='connected'
    text='刚刷到这个，有点意思。\nhttps://science.nasa.gov/earth/'
    mid=core.store.continuity.append('assistant',text,'wechat',delivery='pending')
    core.emit('browser_share',text=text,message_id=mid)
    await channel.forward_events()
    await channel.forward_events()
    assert api.sent==[text]
    assert core.store.continuity.recent()[-1]['delivery']=='delivered'
    assert core.store.search('已向用户分享实际浏览的网页')


async def test_requested_outing_skips_sampling_and_cooldown_and_confirms_delivery(core):
    from little_blue.wechat import Account, WeChatChannel
    class API:
        def __init__(self): self.sent=[]
        async def send(self,context,text): self.sent.append(text)
    api=API()
    channel=WeChatChannel(core,Account(token='test',owner='owner',bot='bot'),api)
    channel.context='context'
    channel.context_time=time.time()
    channel.sent.append(time.time())
    core.wechat_state='connected'
    core.gate.claim(time.time())
    original=core.settings.model_dump()
    async def control(action): return {'mode':'auto'}
    async def explore(share_candidate=True):
        assert not share_candidate
        return core.store.get('browser_share_candidate')
    class Client:
        async def complete(self,messages):
            assert '用户明确要求' in messages[1]['content']
            return {'content':'{"text":"NASA连地球上的小生命都在盯着看诶。"}'}
    core.browser.control=control
    core.browser.explore=explore
    core.agent.client=Client()
    result=await core.browser_share.request_once()
    with pytest.raises(RuntimeError):
        await core.browser_share.request_once()
    await core.browser_share.job
    assert not core.store.get('browser_share_attempts')
    assert core.events[-1]['requested']
    core.settings.dnd=True
    await channel.forward_events()
    assert not api.sent
    core.settings.dnd=False
    await channel.forward_events()
    await channel.forward_events()
    assert len(api.sent)==1
    assert next(t for t in core.store.tasks() if t['id']==result['id'])['status']=='succeeded'
    assert core.settings.model_dump()==original


async def test_request_requires_available_session_and_auto_browser(core):
    core.wechat_ready=False
    with pytest.raises(RuntimeError):
        await core.browser_share.request_once()
    core.wechat_ready=True
    async def control(action): return {'mode':'manual'}
    core.browser.control=control
    with pytest.raises(RuntimeError):
        await core.browser_share.request_once()
    assert not core.store.tasks()


async def test_bedtime_defers_candidate_without_spending_or_marking_seen(core):
    now = time.time()
    core.store.continuity.append('user', '我准备睡觉了', 'wechat', now=now-180)
    class Client:
        async def complete(self, *args):
            raise AssertionError('No API work during quiet context')
    core.agent.client = Client()
    assert not core.browser_share.eligible(now)
    await core.browser_share.produce(now)
    assert not core.store.get('browser_share_seen')
    assert not core.store.get('browser_share_attempts')
    # An explicit wake-up can reopen the gate, but never revive stale evidence.
    core.store.continuity.append('user', '早上好', 'wechat', now=now+4000)
    assert not core.browser_share.eligible(now+4120)


async def test_new_distress_during_model_call_prevents_share(core):
    class Client:
        async def complete(self, *args):
            core.store.continuity.append('user', '我睡不着啊，这个太难受了', 'desktop')
            return {'content':'{"text":"这个海洋知识很有意思"}'}
    core.agent.client = Client()
    await core.browser_share.produce(time.time())
    assert not core.events


async def test_queued_share_rechecks_scene_without_blocking_explicit_request(core):
    from little_blue.wechat import Account, WeChatChannel
    class API:
        def __init__(self): self.sent=[]
        async def send(self, context, text): self.sent.append(text)
    api = API()
    channel = WeChatChannel(core, Account(token='test', owner='owner', bot='bot'), api)
    channel.context = 'context'
    channel.context_time = time.time()-200
    core.wechat_state = 'connected'
    core.store.continuity.append('user', '我准备睡觉了', 'desktop', now=time.time()-180)
    text = '有趣的页面\nhttps://science.nasa.gov/earth/'
    mid = core.store.continuity.append('assistant', text, 'wechat', delivery='pending')
    core.emit('browser_share', text=text, message_id=mid)
    await channel.forward_events()
    assert not api.sent
    core.emit('browser_share', text='明确请求的分享', requested=True)
    await channel.forward_events()
    assert api.sent == ['明确请求的分享']


async def test_desktop_message_invalidates_an_already_generated_share(core):
    from little_blue.wechat import Account, WeChatChannel
    class API:
        async def send(self, *args): raise AssertionError('Stale share sent')
    channel = WeChatChannel(core, Account(token='test', owner='owner', bot='bot'), API())
    channel.context = 'context'
    channel.context_time = time.time()-200
    core.wechat_state = 'connected'
    mid = core.store.continuity.append('assistant', '无关分享', 'wechat', delivery='pending')
    core.emit('browser_share', text='无关分享', message_id=mid)
    core.store.continuity.append('user', '我准备睡觉了', 'desktop')
    await channel.forward_events()
    assert not channel.notifications
    assert core.store.continuity.recent()[-2]['delivery'] == 'failed'
