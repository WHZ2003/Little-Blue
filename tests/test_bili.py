import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from little_blue.bili import BiliRuntime, parse_note, save_note
from little_blue.wechat import WeChatChannel, Account
from little_blue.bili_api import BiliClient, read_credential, save_credential
from little_blue.core import Core
from little_blue.storage import Store
from little_blue.memory_transfer import export_memory, import_memory

BV = 'BV1234567890'
TEXT = '这是一条用于验证真实字幕证据的合成文本，讲述深海生物如何适应高压环境。'


class FakeBili:
    login_state = 'idle'
    login_key = None
    def bound(self): return True
    async def search(self, keyword):
        return [{'bvid': BV, 'title': '合成深海科普', 'url': 'https://www.bilibili.com/video/' + BV + '/'}]
    async def subtitle(self, bvid):
        return {'bvid': bvid, 'status': 'ok', 'text': TEXT, 'title': '合成深海科普', 'url': 'https://www.bilibili.com/video/' + BV + '/'}


def make_core(tmp_path):
    c = Core(Store(tmp_path / 'data/memory.sqlite3'), client=SimpleNamespace())
    c.settings.online = True
    c.bili.client = FakeBili()
    c.store.put('bili_settings', {'enabled': True})
    c.bili.started = time.time() - 120
    return c


async def test_actual_evidence_saved_and_seen_survives_backup(tmp_path):
    c = make_core(tmp_path)
    async def harness(messages, allow_tools, task_id, **kwargs):
        assert not allow_tools and kwargs['private_memory'] is False
        assert {t['function']['name'] for t in kwargs['extra_tools']} == {'bili_search','bili_subtitle','bili_video_info','bili_comments'}
        tool = kwargs['tool_handler']
        assert len((await tool('bili_search', {'keyword':'深海'}))['videos']) == 1
        assert (await tool('bili_search', {'keyword':'深海'}))['error'] == 'tool_limit'
        assert (await tool('bili_subtitle', {'bvid': BV}))['text'] == TEXT
        assert (await tool('bili_subtitle', {'bvid': BV}))['error'] == 'already_seen'
        with pytest.raises(ValueError): await tool('bili_send_message', {'text':'no'})
        return {'content': json.dumps({'bvid':BV,'quote':TEXT[:20],'note':'我对深海适应机制更好奇了。'})[:-1]+',"}'}
    c.agent.harness = SimpleNamespace(run=harness)
    ident = c.bili.launch(); await c.bili.job
    assert c.bili.snapshot()['recent'][0]['status'] == 'read'
    assert c.bili.snapshot()['last_run']['status'] == 'succeeded'
    assert c.store.search('深海')
    assert not c.bili.eligible(time.time(), manual=True)
    save_credential(Path(c.store.path).parent / 'bili-credential.json', {'sessdata':'test-private'})
    archive = tmp_path / 'memory.zip'
    export_memory(Path(c.store.path).parent, archive)
    target = tmp_path / 'restored'
    import_memory(archive, target)
    assert not (target / 'bili-credential.json').exists()
    restored = Core(Store(target / 'memory.sqlite3'), client=SimpleNamespace())
    assert not restored.bili.settings.enabled
    assert restored.bili.snapshot()['recent'][0]['bvid'] == BV
    await restored.close(); await c.close()


async def test_fabricated_reading_never_enters_memory(tmp_path):
    c = make_core(tmp_path)
    async def harness(*args, **kwargs):
        return {'content': json.dumps({'bvid':BV,'quote':'根本没有读取过这份字幕','note':'假心得'})}
    c.agent.harness = SimpleNamespace(run=harness)
    c.bili.launch(); await c.bili.job
    assert c.bili.snapshot()['last_run']['status'] == 'failed'
    assert not c.store.search('假心得')
    await c.close()


def test_note_parser_repairs_only_trailing_delimiter():
    expected={'bvid':BV,'quote':TEXT[:20],'note':'中文心得和标点不变。'}
    raw=json.dumps(expected,ensure_ascii=False)
    assert parse_note(raw[:-1]+',"}')==expected
    assert parse_note('```json\n'+raw+'\n```')==expected
    with pytest.raises(json.JSONDecodeError):parse_note('{"bvid":"unfinished')


async def test_user_chat_cancels_browser_before_using_agent(tmp_path):
    c = make_core(tmp_path)
    started, cancelled = asyncio.Event(), asyncio.Event()
    async def harness(*args, **kwargs):
        started.set()
        try: await asyncio.Event().wait()
        finally: cancelled.set()
    async def agent_run(*args, **kwargs):
        assert cancelled.is_set()
        return 'chat-ok'
    c.agent.harness = SimpleNamespace(run=harness)
    c.agent.run = agent_run
    c.bili.launch(); await started.wait()
    c.submit('用户消息', origin='wechat')
    await c.job
    assert cancelled.is_set()
    assert c.bili.snapshot()['last_run']['status'] == 'cancelled'
    await c.close()


async def test_qr_cookies_are_saved_privately_not_in_status(tmp_path):
    async def transport(request):
        if request.url.path.endswith('generate'):
            return httpx.Response(200, json={'data':{'url':'https://account.bilibili.com/h5/account-h5/auth/scan-web?key=test','qrcode_key':'secret-qr'}})
        if request.url.path.endswith('poll'):
            return httpx.Response(200, json={'data':{'code':0,'url':'https://untrusted.invalid/'}},
                headers=[('set-cookie','SESSDATA=test-session; Path=/'),('set-cookie','DedeUserID=123; Path=/'),('set-cookie','buvid3=test-device; Path=/')])
        raise AssertionError('Unexpected network request')
    client = BiliClient(tmp_path, httpx.MockTransport(transport))
    assert (await client.login_start())['state']=='waiting'
    assert await client.login_poll()=='ok'
    assert read_credential(client.path).sessdata=='test-session'
    assert client.login_key is None and client.bound()


async def test_missing_subtitles_skipped_without_invented_note(tmp_path):
    c = make_core(tmp_path)
    async def subtitle(bvid): return {'status':'no_subtitles','text':''}
    c.bili.client.subtitle = subtitle
    async def harness(*args, **kwargs):
        tool=kwargs['tool_handler']
        await tool('bili_search',{'keyword':'游戏'})
        assert (await tool('bili_subtitle',{'bvid':BV}))['status']=='no_subtitles'
        return {'content':json.dumps({'bvid':'','quote':'','note':'没有字幕'})}
    c.agent.harness=SimpleNamespace(run=harness)
    c.bili.launch();await c.bili.job
    assert c.bili.snapshot()['recent'][0]['status']=='no_subtitles'
    assert not c.bili.snapshot()['recent'][0]['note']
    await c.close()


async def test_rolling_daily_limits_apply_to_manual_and_restart(tmp_path):
    c = make_core(tmp_path)
    c.agent.harness=SimpleNamespace()
    now=time.time()
    for i in range(6):c.bili.execute('INSERT INTO bili_runs VALUES(?,?,?,?)',(str(i),now-7200*(i+1),'failed','test'))
    assert not c.bili.eligible(now,manual=True)
    c.bili=BiliRuntime(c,FakeBili())
    assert not c.bili.eligible(now,manual=True)
    await c.close()


async def test_subtitles_count_toward_daily_limit_even_without_a_note(tmp_path):
    c=make_core(tmp_path); c.agent.harness=SimpleNamespace()
    for i in range(3):
        c.bili.execute('INSERT INTO bili_visits VALUES(?,?,?,?,?,?,?,?)',
                      (str(i),'run',time.time(),'observed','title','url','excerpt',''))
    assert not c.bili.eligible(time.time(),manual=True)
    await c.close()


async def test_explicit_run_allows_open_desktop_but_yields_to_real_chat(tmp_path):
    c=make_core(tmp_path);c.agent.harness=SimpleNamespace()
    c.env.interacting=True
    assert c.bili.eligible(time.time(),manual=True)
    assert not c.bili.eligible(time.time())
    c.current={'id':'chat'}
    assert not c.bili.eligible(time.time(),manual=True)
    c.current=None;c.wechat_receiving=True
    assert not c.bili.eligible(time.time(),manual=True)
    c.wechat_receiving=False;c.settings.dnd=True
    assert not c.bili.eligible(time.time(),manual=True)
    await c.close()


def candidate(c, share=True, text='深海生物适应高压的方式真有意思，我还想再了解一点。'):
    c.store.put('bili_settings', {'enabled':True, 'share_wechat':True})
    c.bili.execute('INSERT INTO bili_visits VALUES(?,?,?,?,?,?,?,?)',
                   (BV,'run',time.time(),'observed','深海','https://untrusted.invalid/',TEXT,''))
    save_note(c.store,'run',{'bvid':BV,'quote':TEXT[:20],'note':'我想继续了解深海。',
                            'share':share,'share_text':text},
              {BV:{'text':TEXT,'title':'深海','url':'https://untrusted.invalid/'}},True)


def channel_for(c, fail=False):
    calls=[]
    async def send(context,text):
        calls.append((context,text))
        if fail:raise TimeoutError('ambiguous')
    channel=WeChatChannel(c,Account(token='private',owner='owner',bot='bot'),SimpleNamespace(send=send))
    channel.context='context';channel.context_time=time.time()-100
    c.wechat_state='connected';c.wechat_ready=True;c.settings.proactive=True
    return channel,calls


async def test_reading_to_wechat_delivered_once_with_real_source(tmp_path):
    c=make_core(tmp_path)
    channel,calls=channel_for(c)
    async def harness(*args,**kwargs):
        tool=kwargs['tool_handler']
        await tool('bili_search',{'keyword':'深海'})
        await tool('bili_subtitle',{'bvid':BV})
        return {'content':json.dumps({'bvid':BV,'quote':TEXT[:20],'note':'想了解深海适应机制。',
                                      'share':True,'share_text':'深海适应高压的方式真有意思。'})}
    c.agent.harness=SimpleNamespace(run=harness)
    c.store.put('bili_settings',{'enabled':True,'share_wechat':True})
    c.bili.launch();await c.bili.job
    c.bili.queue_share()
    await channel.forward_events()
    c.bili.queue_share();await channel.forward_events()
    assert len(calls)==1 and f'https://www.bilibili.com/video/{BV}/' in calls[0][1]
    assert '深海适应高压' in calls[0][1] and '第一 P 字幕' in calls[0][1]
    assert c.bili.snapshot()['sharing'][0]['status']=='delivered'
    assert c.store.continuity.history()[-1]['delivery']=='delivered'
    c.bili=BiliRuntime(c,FakeBili());c.bili.queue_share();await channel.forward_events()
    assert len(calls)==1
    await c.close()


@pytest.mark.parametrize('share,text',[(False,'有趣'),(True,'https://untrusted.invalid/'),('true','有趣'),(True,'')])
async def test_no_share_for_declined_or_invalid_output(tmp_path,share,text):
    c=make_core(tmp_path);candidate(c,share,text)
    assert c.bili.snapshot()['recent'][0]['status']=='read'
    assert not c.bili.snapshot()['sharing']
    await c.close()


async def test_sharing_waits_for_context_and_respects_stop_at_send_boundary(tmp_path):
    c=make_core(tmp_path);candidate(c)
    c.bili.queue_share();assert c.bili.snapshot()['sharing'][0]['status']=='pending'
    channel,calls=channel_for(c)
    c.settings.dnd=True;c.bili.queue_share();assert not calls
    c.settings.dnd=False;c.bili.queue_share()
    c.settings.dnd=True;await channel.forward_events();assert not calls
    c.settings.dnd=False;c.handoff_requested=True;await channel.forward_events();assert not calls
    c.handoff_requested=False
    await c.bili.control({'action':'configure','settings':{'enabled':True,'share_wechat':False}})
    await channel.forward_events()
    assert not calls and c.bili.snapshot()['sharing'][0]['status']=='cancelled'
    await c.close()


async def test_failed_send_and_restart_never_replay(tmp_path):
    c=make_core(tmp_path);candidate(c)
    channel,calls=channel_for(c,fail=True)
    c.bili.queue_share();await channel.forward_events()
    assert len(calls)==1 and c.bili.snapshot()['sharing'][0]['status']=='unconfirmed'
    assert not c.store.continuity.history()
    c.bili=BiliRuntime(c,FakeBili());c.bili.queue_share();await channel.forward_events()
    assert len(calls)==1
    await c.close()


async def test_claimed_share_is_not_replayed_after_restart(tmp_path):
    c=make_core(tmp_path);candidate(c);channel,calls=channel_for(c)
    c.bili.queue_share();assert c.bili.snapshot()['sharing'][0]['status']=='queued'
    c.bili=BiliRuntime(c,FakeBili())
    replacement,calls=channel_for(c);c.bili.queue_share();await replacement.forward_events()
    assert not calls and c.bili.snapshot()['sharing'][0]['status']=='unknown'
    await c.close()


async def test_memory_copy_keeps_reflection_but_disables_pending_sharing(tmp_path):
    c=make_core(tmp_path);candidate(c)
    archive=tmp_path/'share-memory.zip'
    export_memory(Path(c.store.path).parent,archive)
    target=tmp_path/'copy';import_memory(archive,target)
    restored=Core(Store(target/'memory.sqlite3'),client=SimpleNamespace())
    assert not restored.bili.settings.enabled and not restored.bili.settings.share_wechat
    assert restored.bili.snapshot()['sharing'][0]['status']=='cancelled'
    assert restored.store.search('深海')
    await restored.close();await c.close()
