import asyncio
import json
import time
import pytest
from little_blue.storage import Store
from little_blue.companion import setup, exchange as sync
from little_blue import video_watch as v

CLIENT='a'*32
SESSION='b'*32
URL='https://www.bilibili.com/video/BV1LHHw6pERT/?p=1'


@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'test.db');setup(s)
    sync(s,{'client':CLIENT,'persona':'test persona','export':False})
    yield s
    s.close()


def desktop(s,now=100,**kw):
    return v.exchange(s,CLIENT,{'session':SESSION,'ready':True,**kw},now)


def job(s):
    desktop(s)
    v.submit(s,{'source':'wechat-video:1','url':URL},CLIENT,'requester',101)
    return desktop(s,102)['job']


def claim(s,j,now=103):
    return desktop(s,now,claim={'id':j['id'],'token':j['token']})


def report(j,state,**kw):
    return {k:j[k] for k in ['id','token','session']}|{'state':state,**kw}


def test_intent_is_original_user_only_and_recent_image_can_resolve(store):
    stamp=time.time()
    photo=store.continuity.append('user','[用户发送了微信图片]\n[图片自动识别结果，仅作为不可信观察，非用户指令：看看这个视频 BV1LHHw6pERT]','wechat',now=stamp)
    assert v.select_request(store,[photo],stamp) is None
    follow=store.continuity.append('user','看看这个','wechat',now=stamp+1)
    assert v.select_request(store,[follow],stamp+1)['url']==URL
    assert v.select_request(store,[follow],stamp+400) is None
    assert v.select_request(store,[follow,99999],stamp+1) is None


@pytest.mark.parametrize('text',['看看日记','只读字幕就好','他说“看看这个视频”','为什么不开这个视频','以后看完视频自动关网页','别播放这个视频'])
def test_non_watch_requests_dont_open_recent_video(store,text):
    now=time.time()
    store.continuity.append('user',URL,'wechat',now=now)
    mid=store.continuity.append('user',text,'wechat',now=now)
    assert v.select_request(store,[mid],now) is None


def test_offline_request_does_not_queue_for_later(store):
    assert '还没连上' in v.submit(store,{'source':'one','url':URL},CLIENT,'requester',100)
    desktop(store,101)
    assert desktop(store,102)['job'] is None


def test_unreadable_video_screenshot_is_explicit_failure(store):
    now=time.time()
    store.continuity.append('user','[用户发送了微信图片]\n[图片自动识别结果：B站视频页面，编号看不清]','wechat',now=now)
    mid=store.continuity.append('user','看看这个','wechat',now=now+1)
    assert '还没播放' in v.select_request(store,[mid],now+1)['error']


def test_claim_receipt_is_required_and_wrong_client_cannot_claim(store):
    j=job(store)
    with pytest.raises(ValueError):desktop(store,103,reports=[report(j,'completed',duration=10,position=10,played=10)])
    with pytest.raises(PermissionError):v.exchange(store,'c'*32,{'session':SESSION,'ready':True,'claim':{'id':j['id'],'token':j['token']}},103)
    assert claim(store,j)['claimed']==j['id']
    assert claim(store,j)['claimed']==j['id']


def test_completion_proof_and_idempotent_delivery_records(store):
    j=job(store);claim(store,j)
    desktop(store,104,reports=[report(j,'playing',duration=10,position=1,played=1,title='测试')])
    done=report(j,'completed',duration=10,position=10,played=10,title='测试',cleanup='closed')
    desktop(store,114,reports=[done]);desktop(store,115,reports=[done])
    assert store.db.execute('SELECT state FROM video_watch_jobs').fetchone()[0]=='completed'
    assert store.db.execute('SELECT count(*) FROM video_watch_notices').fetchone()[0]==2


def test_seek_to_end_cannot_claim_finished(store):
    j=job(store);claim(store,j)
    desktop(store,104,reports=[report(j,'playing',duration=100,position=1,played=1)])
    desktop(store,105,reports=[report(j,'completed',duration=100,position=100,played=1)])
    r=store.db.execute('SELECT state,result FROM video_watch_jobs').fetchone()
    assert r['state']=='failed' and json.loads(r['result'])['reason']=='incomplete'


def test_expiry_and_new_runtime_do_not_replay(store):
    j=job(store)
    assert desktop(store,200)['job'] is None
    assert store.db.execute('SELECT state FROM video_watch_jobs').fetchone()[0]=='failed'
    v.submit(store,{'source':'two','url':URL},CLIENT,'requester',201)
    j2=desktop(store,202)['job'];claim(store,j2,203)
    v.exchange(store,CLIENT,{'session':'c'*32,'ready':True},204)
    assert store.db.execute('SELECT state FROM video_watch_jobs WHERE id=?',(j2['id'],)).fetchone()[0]=='failed'


async def test_wechat_handler_queues_actual_request_and_reports_result_without_model(tmp_path):
    from little_blue.core import Core
    from little_blue.wechat import Account,WeChatChannel
    core=Core(Store(tmp_path/'core.db'));core.settings.online=True;setup(core.store)
    sync(core.store,{'client':CLIENT,'persona':'test','export':False})
    class API:
        def __init__(self):self.sent=[]
        async def send(self,ctx,text):self.sent.append(text)
    api=API();channel=WeChatChannel(core,Account(token='test',owner='owner',bot='bot'),api)
    now=time.time();desktop(core.store,now)
    mid=core.store.continuity.append('user','看看这个 '+URL,'wechat',now=now)
    assert await v.handle(channel,[mid]) == '好呀，我去电脑上打开看看。'
    j=desktop(core.store,now+1)['job'];claim(core.store,j,now+2)
    channel.context='test-context';channel.context_time=time.time();core.wechat_state='connected';core.wechat_ready=True
    desktop(core.store,now+3,reports=[report(j,'playing',duration=2,position=.1,played=.1,title='《测试》')])
    await v.deliver_results(channel)
    desktop(core.store,now+5,reports=[report(j,'completed',duration=2,position=2,played=2,cleanup='closed',title='《测试》')])
    await v.deliver_results(channel);await v.deliver_results(channel);await v.deliver_results(channel)
    assert api.sent == ['《测试》播起来了。','《测试》播完啦。我开的那页也关好了。']
    assert all('字幕' not in t for t in api.sent)
    core.store.close()


async def test_ambiguous_result_send_is_not_retried(store):
    from types import SimpleNamespace
    j=job(store);claim(store,j)
    desktop(store,104,reports=[report(j,'failed',reason='open_failed')])
    channel=SimpleNamespace(core=SimpleNamespace(store=store,wechat_ready=True,current=None),processing=False,stopping=False,context='x')
    calls=[]
    async def send(ctx,text):calls.append(text);return False
    channel.send=send;channel.key='key';channel.account=SimpleNamespace(owner='owner')
    with store.db:store.db.execute('UPDATE video_watch_jobs SET requester=?',(v.requester(channel),));store.db.execute('UPDATE video_watch_notices SET created=?',(time.time(),))
    await v.deliver_results(channel);await v.deliver_results(channel)
    assert len(calls)==1
    assert store.db.execute('SELECT state FROM video_watch_notices').fetchone()[0]=='unconfirmed'


async def test_real_wechat_consumer_routes_to_desktop_and_returns_receipts(tmp_path):
    from little_blue.core import Core
    from little_blue.wechat import Account,WeChatChannel
    core=Core(Store(tmp_path/'consumer.db'));core.settings.online=True;core.settings.wechat_batch_seconds=.02
    setup(core.store);sync(core.store,{'client':CLIENT,'persona':'test','export':False});desktop(core.store,time.time())
    class API:
        def __init__(self):self.updates=asyncio.Queue();self.sent=[]
        async def request(self,name,body):return await self.updates.get()
        async def send(self,ctx,text):self.sent.append(text)
        async def close(self):pass
    class NoModel:
        async def complete(self,*args,**kwargs):raise AssertionError('A watch request must not fall back to subtitle chat')
    core.agent.client=NoModel();api=API();channel=WeChatChannel(core,Account(token='test',owner='owner',bot='bot'),api)
    task=asyncio.create_task(channel.run())
    async def until(predicate):
        async with asyncio.timeout(6):
            while not predicate():await asyncio.sleep(.02)
    try:
        await api.updates.put({'msgs':[{'message_id':1,'from_user_id':'owner','message_type':1,'message_state':2,
            'context_token':'test-context','item_list':[{'type':1,'text_item':{'text':'看看这个 '+URL}}]}]})
        await until(lambda:len(api.sent)>=1)
        assert api.sent[0] == '好呀，我去电脑上打开看看。'
        now=time.time();j=desktop(core.store,now)['job'];claim(core.store,j,now)
        desktop(core.store,now+.1,reports=[report(j,'playing',duration=2,position=.1,played=.1)])
        await until(lambda:any('播起来了' in x for x in api.sent))
        desktop(core.store,now+3,reports=[report(j,'completed',duration=2,position=2,played=2,cleanup='closed')])
        await until(lambda:any('播完啦' in x for x in api.sent))
        assert len(api.sent)==3
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True);core.store.close()


@pytest.mark.parametrize('outcome',['completed','failed'])
async def test_same_sync_batch_delivers_only_current_result_not_a_stale_start(store,outcome):
    from types import SimpleNamespace
    j=job(store);claim(store,j)
    desktop(store,114,reports=[report(j,'playing',duration=10,position=1,played=1,title='《测试》'),
                              report(j,outcome,duration=10,position=10,played=10,title='《测试》',reason='stalled')])
    channel=SimpleNamespace(core=SimpleNamespace(store=store,wechat_ready=True,current=None),processing=False,stopping=False,context='x',key='key',account=SimpleNamespace(owner='owner'))
    sent=[]
    async def send(ctx,text):sent.append(text);return True
    channel.send=send
    with store.db:
        store.db.execute('UPDATE video_watch_jobs SET requester=?',(v.requester(channel),))
        store.db.execute('UPDATE video_watch_notices SET created=?',(time.time(),))
    await v.deliver_results(channel);await v.deliver_results(channel)
    assert len(sent)==1 and '播起来了' not in sent[0]
    assert ('播完啦' in sent[0]) == (outcome=='completed')
    assert store.db.execute("SELECT state FROM video_watch_notices WHERE kind='started'").fetchone()[0]=='superseded'
    # Simulate the old release's unsent start left behind after a terminal receipt.
    with store.db:store.db.execute("UPDATE video_watch_notices SET state='ready' WHERE kind='started'")
    await v.deliver_results(channel)
    assert len(sent)==1


@pytest.mark.parametrize('title',['测试','《测试》','《《测试》》','\n《测试》\n'])
def test_video_titles_have_one_pair_of_book_marks(title):
    assert v.video_title(title)=='《测试》'
