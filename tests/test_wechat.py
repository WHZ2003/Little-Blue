import asyncio
import json
from pathlib import Path
import time

import httpx
import pytest
from little_blue.core import Core
from little_blue.cognition import NO_REPLY
from little_blue.storage import Store
from little_blue.wechat import (Account, BASE, ILink, SessionExpired, WeChatChannel,
                                api_base, login, read_account, save_account, supervise)


@pytest.fixture
def core(tmp_path):
    c = Core(Store(tmp_path / 'memory.db'))
    yield c
    c.store.close()


def account():
    return Account(token='private-token', owner='owner', bot='bot')


def inbound(number=1, text='你好', **changes):
    return {'message_id': number, 'from_user_id': 'owner', 'message_type': 1,
            'message_state': 2, 'context_token': 'private-context',
            'item_list': [{'type': 1, 'text_item': {'text': text}}], **changes}


class FakeAPI:
    def __init__(self):
        self.updates = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def request(self, name, body):
        assert name == 'getupdates'
        return await self.updates.get()

    async def send(self, context, text):
        self.sent.append((context, text))

    async def close(self):
        self.closed = True


async def test_memory_control_is_local_and_keeps_original_memories(core):
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    core.store.remember('tea', 'semantic')
    try:
        assert await channel.memory_control('/memory off', 'context')
        assert not core.memory.enabled
        assert await channel.memory_control('/memory rebuild', 'context')
        assert core.store.search('tea')
        assert '已索引' in api.sent[-1][1]
        assert not await channel.memory_control('随便聊聊', 'context')
    finally:
        await core.memory.close()


async def until(predicate):
    async with asyncio.timeout(4):
        while not predicate():
            await asyncio.sleep(.01)


async def stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def dialogue_client(core, answers):
    core.settings.online = True
    core.settings.wechat_batch_seconds = .04
    core.store.growth.control('off')
    calls = []

    class Client:
        async def complete(self, messages, schemas):
            assert schemas is None
            calls.append(messages)
            return {'content': answers[len(calls) - 1]}
    core.agent.client = Client()
    return calls


async def test_split_messages_one_reply_individual_receipts(core):
    calls = dialogue_client(core, ['可以，我会适当地来找你。'])
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    task = asyncio.create_task(channel.run())
    try:
        api.updates.put_nowait({'msgs': [inbound(1, '你能主动发消息吗'),
            inbound(2, '需要打开设置吗', context_token='second-context')]})
        await until(lambda: len(api.sent) == 1)
        assert len(calls) == 1
        assert calls[0][-1]['content'] == '你能主动发消息吗\n需要打开设置吗'
        assert api.sent[0][0] == 'second-context'
        rows = core.store.continuity.recent()
        assert [r['role'] for r in rows] == ['user', 'user', 'assistant']
        assert rows[-1]['delivery'] == 'delivered'
        system = '\n'.join(m['content'] for m in calls[0] if m['role'] == 'system')
        assert '能主动给已绑定用户发消息' in system
        assert '微信会话可用' in system
    finally:
        await stop(task)


@pytest.mark.parametrize('second_answer', [NO_REPLY, '对，也可以在桌面里关掉。'])
async def test_second_fragment_after_delivery_sees_delivered_reply(core, second_answer):
    calls = dialogue_client(core, [])
    entered, release = asyncio.Event(), asyncio.Event()
    first_answer = '可以主动发消息，设置里有主动交流开关。'

    class Client:
        async def complete(self, messages, schemas):
            calls.append(messages)
            if len(calls) == 1:
                entered.set()
                await release.wait()
                return {'content': first_answer}
            assert {'role': 'assistant', 'content': first_answer} in messages
            return {'content': second_answer}
    core.agent.client = Client()
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    task = asyncio.create_task(channel.run())
    try:
        api.updates.put_nowait({'msgs': [inbound(1, '能主动发消息吗')]})
        await asyncio.wait_for(entered.wait(), 2)
        release.set()
        await until(lambda: len(api.sent) == 1 and not channel.processing)
        api.updates.put_nowait({'msgs': [inbound(2, '是没配置吗')]})
        await until(lambda: len(calls) == 2 and core.current is None and not channel.processing)
        rows = core.store.continuity.recent()
        assert [r['text'] for r in rows if r['role'] == 'user'] == ['能主动发消息吗', '是没配置吗']
        if second_answer == NO_REPLY:
            assert api.sent == [('private-context', first_answer)]
            assert len(rows) == 3
            assert core.store.search('是没配置吗')
            assert any(t['detail'] == '已接收，不回复' and t['status'] == 'succeeded' for t in core.store.tasks())
            assert core.events[-1]['silent']
        else:
            assert len(api.sent) == 2 and api.sent[-1][1] == second_answer
    finally:
        release.set()
        await stop(task)


async def test_failed_delivery_not_treated_as_previous_reply(core):
    calls = dialogue_client(core, ['这条没有送达', '重新回应新问题'])
    class FailingOnce(FakeAPI):
        async def send(self, context, text):
            await super().send(context, text)
            if len(self.sent) == 1:
                raise TimeoutError()
    api = FailingOnce()
    channel = WeChatChannel(core, account(), api)
    task = asyncio.create_task(channel.run())
    try:
        api.updates.put_nowait({'msgs': [inbound(1, '第一句')]})
        await until(lambda: len(api.sent) == 1 and not channel.processing)
        api.updates.put_nowait({'msgs': [inbound(2, '第二句')]})
        await until(lambda: len(api.sent) == 2)
        assert not any(m['role'] == 'assistant' and m['content'] == '这条没有送达' for m in calls[1])
        assert [r['delivery'] for r in core.store.continuity.recent() if r['role'] == 'assistant'] == ['failed', 'delivered']
    finally:
        await stop(task)


async def test_late_fragment_replaces_unsent_draft_once(core):
    dialogue_client(core, [])
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    class Client:
        async def complete(self, messages, schemas):
            calls.append(messages)
            if len(calls) == 1:
                entered.set()
                await release.wait()
                return {'content': 'UNSENT DRAFT'}
            assert messages[-1]['content'] == '论文的那个部分\n我说的是结论'
            assert not any('UNSENT DRAFT' in m['content'] for m in messages)
            return {'content': '哦，结论那段。'}
    core.agent.client = Client()
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    task = asyncio.create_task(channel.run())
    try:
        api.updates.put_nowait({'msgs': [inbound(1, '论文的那个部分')]})
        await asyncio.wait_for(entered.wait(), 2)
        api.updates.put_nowait({'msgs': [inbound(2, '我说的是结论', context_token='new-context')]})
        await until(lambda: not channel.queue.empty())
        release.set()
        await until(lambda: len(api.sent) == 1 and not channel.processing)
        assert api.sent == [('new-context', '哦，结论那段。')]
        assert len(calls) == 2
        assert not core.store.search('UNSENT DRAFT')
        assert not any('UNSENT' in r['text'] for r in core.store.continuity.snapshot()['messages'])
        assert [r[0] for r in core.store.db.execute('SELECT state FROM conversation_inbox')] == ['answered', 'answered']
    finally:
        release.set()
        await stop(task)


async def test_late_revisions_bounded_and_new_question_not_lost(core):
    dialogue_client(core, [])
    entered = [asyncio.Event() for _ in range(2)]
    release = [asyncio.Event() for _ in range(2)]
    calls = []
    class Client:
        async def complete(self, messages, schemas):
            index = len(calls)
            calls.append(messages)
            if index < 2:
                entered[index].set()
                await release[index].wait()
            return {'content': f'answer-{index}'}
    core.agent.client = Client()
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    task = asyncio.create_task(channel.run())
    try:
        api.updates.put_nowait({'msgs': [inbound(1, '第一句')]})
        await asyncio.wait_for(entered[0].wait(), 2)
        api.updates.put_nowait({'msgs': [inbound(2, '第二句')]})
        await until(lambda: not channel.queue.empty())
        release[0].set()
        await asyncio.wait_for(entered[1].wait(), 2)
        api.updates.put_nowait({'msgs': [inbound(3, '还有一个新问题？')]})
        await until(lambda: not channel.queue.empty())
        release[1].set()
        await until(lambda: len(api.sent) == 2 and not channel.processing)
        assert [text for _, text in api.sent] == ['answer-1', 'answer-2']
        assert calls[-1][-1]['content'] == '还有一个新问题？'
        assert {'role': 'assistant', 'content': 'answer-1'} in calls[-1]
        assert not any('还有一个新问题' in m['content'] for m in calls[1])
    finally:
        for event in release:
            event.set()
        await stop(task)


async def test_explicit_no_reply_offline_is_persisted_without_send(core):
    core.settings.wechat_batch_seconds = .02
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    task = asyncio.create_task(channel.run())
    try:
        api.updates.put_nowait({'msgs': [inbound(1, '先不用回复')]})
        await until(lambda: bool(core.store.tasks()) and core.current is None)
        assert not api.sent
        assert [r['text'] for r in core.store.continuity.recent()] == ['先不用回复']
        assert core.store.search('先不用回复')
    finally:
        await stop(task)


async def test_silence_disabled_does_not_leak_control_marker(core):
    calls = dialogue_client(core, [NO_REPLY])
    core.settings.wechat_silence = False
    core.submit('你好', origin='wechat')
    answer = await core.job
    assert answer and NO_REPLY not in answer
    assert not any(NO_REPLY in m['content'] for m in calls[0])


async def test_budget_failure_is_explained_in_wechat_without_model_request(core):
    from little_blue.cognition import DeepSeekClient
    core.settings.online = True
    core.settings.daily_tokens = 1
    core.settings.wechat_batch_seconds = 0
    def forbidden(request):
        raise AssertionError('Budget must prevent network call')
    core.agent.client = DeepSeekClient(core.store, core.settings, httpx.MockTransport(forbidden), api_key='test')
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    task = asyncio.create_task(channel.run())
    try:
        api.updates.put_nowait({'msgs': [inbound(1, '你会主动发表情包吗')]})
        await until(lambda: len(api.sent) == 1)
        assert '本地 Token 预算拦截' in api.sent[0][1]
        assert core.store.tasks()[0]['detail'] == 'BudgetExceeded'
        api.updates.put_nowait({'msgs': [inbound(2, '/status')]})
        await until(lambda: len(api.sent) == 2)
        assert 'Token 已用与预留：0/1' in api.sent[-1][1]
        assert core.store.budget_usage()['requests'] == 0
    finally:
        await stop(task)


async def test_batch_keeps_length_and_command_boundaries(core):
    channel = WeChatChannel(core, account(), FakeAPI())
    core.settings.wechat_batch_seconds = .02
    first = channel.normalize(('甲' * 1500, 'one'))
    channel.queue.put_nowait(('乙' * 700, 'two'))
    parts = await channel.collect(first)
    assert parts == [first] and channel.held[0] == '乙' * 700
    first, channel.held = channel.held, None
    channel.queue.put_nowait(('/status', 'three'))
    assert await channel.collect(first) == [first]
    assert channel.held[0] == '/status'


async def test_cancel_during_batch_preserves_only_new_task(core):
    calls = dialogue_client(core, ['新话题的回复'])
    core.settings.wechat_batch_seconds = .8
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    task = asyncio.create_task(channel.run())
    try:
        api.updates.put_nowait({'msgs': [inbound(1, '取消前的旧话题')]})
        await until(lambda: channel.processing)
        api.updates.put_nowait({'msgs': [inbound(2, '/cancel'), inbound(3, '新的问题')]})
        await until(lambda: len(api.sent) == 2)
        assert len(calls) == 1 and calls[0][-1]['content'] == '新的问题'
        assert api.sent[-1][1] == '新话题的回复'
        assert len([r for r in core.store.continuity.recent() if r['role'] == 'user']) == 2
    finally:
        await stop(task)


@pytest.mark.parametrize('answer', ['刚钓到一条新鱼，想给你看看。', NO_REPLY])
async def test_life_loop_can_initiate_wechat_without_new_inbound(core, answer):
    calls = dialogue_client(core, [answer])
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    channel.context, channel.context_time = 'valid-session', time.time() - 100
    core.wechat_state = 'connected'
    await channel.forward_events()
    core.gate.started = time.time() - 100
    core.life.needs.social = 95
    core.last_reflection = time.time()
    await core.start()
    try:
        await until(lambda: bool(calls) and core.current is None)
        await channel.forward_events()
        if answer == NO_REPLY:
            assert not api.sent and not core.store.continuity.recent()
        else:
            assert api.sent == [('valid-session', answer)]
            assert core.store.continuity.recent()[-1]['delivery'] == 'delivered'
        assert len(calls) == 1
        assert not any(r['role'] == 'user' for r in core.store.continuity.recent())
    finally:
        await stop(core.loop_task)


async def test_new_inbound_invalidates_old_proactive_draft(core):
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    channel.context, channel.context_time = 'ctx', time.time() - 100
    core.wechat_state = 'connected'
    mid = core.store.continuity.append('assistant', '旧主动消息', 'wechat', delivery='pending')
    core.emit('result', proactive=True, ok=True, started=time.time() - 110,
              message_id=mid, text='旧主动消息')
    await channel.forward_events()
    assert not api.sent and not channel.notifications
    assert core.store.continuity.recent()[-1]['delivery'] == 'failed'


def test_restart_retains_silent_receipts_and_does_not_resume_pending_send(tmp_path):
    path = tmp_path / 'restart.db'
    store = Store(path)
    store.continuity.append('user', '这条不用回复', 'wechat')
    store.continuity.append('assistant', '未确认草稿', 'wechat', delivery='pending')
    store.close()
    restarted = Core(Store(path))
    try:
        rows = restarted.store.continuity.recent()
        assert rows[0]['text'] == '这条不用回复'
        assert rows[1]['delivery'] == 'failed'
        assert restarted.job is None and not restarted.store.tasks()
    finally:
        restarted.store.close()


@pytest.mark.parametrize('url', ['http://ilinkai.weixin.qq.com', 'https://evil.test',
    'https://ilinkai.weixin.qq.com.evil.test', 'https://weixin.qq.com@evil.test',
    'https://user:secret@ilinkai.weixin.qq.com', 'https://127.0.0.1',
    BASE + ':8443', BASE + '/path', BASE + '?token=x', BASE + '#x'])
def test_untrusted_endpoint_rejected(url):
    with pytest.raises(ValueError):
        api_base(url)


async def test_wire_headers_context_and_fixed_recipient():
    calls = []
    def handler(r):
        calls.append(r)
        return httpx.Response(200, json={'ret': 0})
    api = ILink(account(), httpx.MockTransport(handler))
    await api.send('context1', '你好')
    request = calls[-1]
    body = json.loads(request.content)
    assert request.headers['Authorization'] == 'Bearer private-token'
    assert body['msg']['to_user_id'] == 'owner'
    assert body['msg']['context_token'] == 'context1'
    assert body['msg']['message_type'] == body['msg']['message_state'] == 2
    assert 'private-token' not in str(request.url)
    await api.request('get_qrcode_status', params={'qrcode': 'qr'}, authenticated=False)
    assert 'Authorization' not in calls[-1].headers
    assert 'X-WECHAT-UIN' not in calls[-1].headers
    with pytest.raises(PermissionError):
        await api.send('', 'no context')
    await api.close()


async def test_business_error_and_redirect_never_expose_token():
    for status, data, exception in [(200, {'ret': -14}, SessionExpired),
                                  (200, {'ret': 1, 'errmsg': 'private-token'}, RuntimeError),
                                  (302, {}, RuntimeError)]:
        api = ILink(account(), httpx.MockTransport(lambda r: httpx.Response(status, json=data,
                                                 headers={'location': 'https://evil.test'})))
        with pytest.raises(exception) as error:
            await api.send('ctx', 'hello')
        assert 'private-token' not in str(error.value)
        await api.close()


async def test_owner_filter_persistent_dedup_and_no_secret_in_snapshot(core):
    channel = WeChatChannel(core, account(), FakeAPI())
    for change in ({'from_user_id': 'stranger'}, {'group_id': 'group'}, {'message_type': 2},
                   {'message_state': 1}, {'context_token': ''}, {'message_id': {}},
                   {'delete_time_ms': 1}):
        assert channel.accept(inbound(**change)) is None
    assert channel.accept(inbound()) == ('你好', 'private-context')
    assert channel.accept(inbound()) is None
    # A fresh channel (as after restart) still rejects the claimed message.
    restarted = WeChatChannel(core, account(), FakeAPI())
    assert restarted.accept(inbound()) is None
    assert restarted.context is None
    assert 'private-' not in json.dumps(core.snapshot())


async def test_duplex_offline_shared_memory_and_no_host_required(core):
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    task = asyncio.create_task(channel.run())
    await core.start()
    try:
        api.updates.put_nowait({'msgs': [inbound(), inbound(), inbound(2, from_user_id='stranger')],
                               'get_updates_buf': 'cursor1'})
        await until(lambda: len(api.sent) == 1)
        assert '离线模式' in api.sent[0][1]
        assert core.store.search('你好')
        assert core.store.get(channel.key + ':cursor') == 'cursor1'
        api.updates.put_nowait({'msgs': [inbound(3, '/status')], 'get_updates_buf': 'cursor2'})
        await until(lambda: len(api.sent) == 2)
        assert 'DeepSeek' in api.sent[1][1]
    finally:
        await stop(task)
        await stop(core.loop_task)
    assert api.closed


async def test_wechat_growth_controls_work_during_busy_chat_and_do_not_call_llm(core):
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    core.current = {'origin': 'wechat'}
    task = asyncio.create_task(channel.run())
    try:
        api.updates.put_nowait({'msgs': [inbound(1, '/growth off'), inbound(2, '/identity')], 'get_updates_buf': 'next'})
        await until(lambda: len(api.sent) == 2)
        assert not core.store.growth.snapshot()['enabled']
        assert '自动成长：关' in api.sent[1][1]
        assert not core.store.tasks()
        assert not core.store.search('')
        assert channel.queue.empty()
        api.updates.put_nowait({'msgs': [inbound(3, '/growth on', from_user_id='stranger')], 'get_updates_buf': 'next2'})
        await until(lambda: core.store.get(channel.key + ':cursor') == 'next2')
        assert not core.store.growth.snapshot()['enabled']
    finally:
        await stop(task)
        core.current = None


async def test_wechat_cannot_call_tools_even_when_desktop_tools_enabled(core):
    core.settings.online = core.settings.tools_enabled = True
    calls = []
    class Client:
        async def complete(self, messages, schemas):
            calls.append((messages, schemas))
            return {'tool_calls': [{'id': 'x', 'function': {'name': 'system_action',
                                                          'arguments': '{"action":"lock_screen"}'}}]}
    core.agent.client = Client()
    core.submit('锁屏', origin='wechat')
    answer = await core.job
    assert calls[0][1] is None
    assert '微信文字聊天' in calls[0][0][1]['content']
    assert core.store.tasks()[0]['status'] == 'failed'
    assert not core.pending
    assert '无法回复' in answer


async def test_cancel_from_wechat_and_disconnect_does_not_abort_chat(core):
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    core.settings.online = True
    entered = asyncio.Event()
    class Client:
        async def complete(self, *args):
            entered.set()
            await asyncio.Event().wait()
    core.agent.client = Client()
    task = asyncio.create_task(channel.run())
    await core.start()
    try:
        api.updates.put_nowait({'msgs': [inbound()], 'get_updates_buf': 'a'})
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(.35)
        assert core.current is not None  # No desktop heartbeat, still running.
        api.updates.put_nowait({'msgs': [inbound(2, '/cancel')], 'get_updates_buf': 'b'})
        await until(lambda: core.current is None and bool(api.sent))
        assert core.store.tasks()[0]['status'] == 'cancelled'
    finally:
        await stop(task)
        await stop(core.loop_task)


async def test_send_timeout_not_replayed_and_not_reported_success(core):
    class Failing(FakeAPI):
        async def send(self, context, text):
            self.sent.append(text)
            raise TimeoutError('private-token')
    api = Failing()
    channel = WeChatChannel(core, account(), api)
    await channel.send('ctx', 'once')
    assert len(api.sent) == 1
    assert '未确认送达' in core.events[-1]['text']
    assert 'private-token' not in json.dumps(list(core.events))
    assert core.store.db.execute("SELECT decision FROM audit WHERE tool='wechat_send' ORDER BY id DESC").fetchone()[0] == 'failed_no_retry'


async def test_notifications_respect_dnd_cooldown_and_context_expiry(core):
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    channel.accept(inbound())
    core.wechat_state = 'connected'
    core.settings.dnd = True
    core.emit('reminder', text='休息一下')
    await channel.forward_events()
    assert not api.sent
    core.settings.dnd = False
    await channel.forward_events()
    assert api.sent == [('private-context', '休息一下')]
    core.emit('reminder', text='稍后')
    await channel.forward_events()
    assert len(api.sent) == 1 and len(channel.notifications) == 1
    channel.sent.clear()
    channel.context_time = time.time() - 13 * 3600
    await channel.forward_events()
    assert not core.wechat_ready and len(api.sent) == 1


async def test_wechat_continues_desktop_activity_and_controls_during_busy_task(core):
    core.submit('我正在准备答辩')
    await core.job
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    task = asyncio.create_task(channel.run())
    try:
        api.updates.put_nowait({'msgs': [inbound(1, '做完了')]})
        await until(lambda: len(api.sent) == 1)
        assert '准备答辩已经完成' in api.sent[0][1]
        rows = core.snapshot()['relationship']['messages']
        assert [r['origin'] for r in rows] == ['desktop', 'desktop', 'wechat', 'wechat']
        core.current = {'origin': 'desktop'}
        api.updates.put_nowait({'msgs': [inbound(2, '/followup off')]})
        await until(lambda: len(api.sent) == 2)
        assert not core.store.continuity.snapshot()['followups']
    finally:
        core.current = None
        await stop(task)


async def test_stale_followup_dropped_and_desktop_followup_not_duplicated_on_phone(core):
    api = FakeAPI()
    channel = WeChatChannel(core, account(), api)
    channel.accept(inbound())
    core.wechat_state = 'connected'
    core.store.continuity.observe('/doing 写论文', 'desktop', 1)
    core.emit('followup', origin='desktop', source_id=1, text='desktop only')
    await channel.forward_events()
    assert not api.sent and not channel.notifications
    core.settings.dnd = True
    core.emit('followup', origin='wechat', source_id=1, text='old topic')
    await channel.forward_events()
    assert channel.notifications
    core.store.continuity.observe('/done', 'wechat', 2)
    core.settings.dnd = False
    await channel.forward_events()
    assert not api.sent and not channel.notifications


async def test_login_saves_only_confirmed_account_and_svg(tmp_path):
    class LoginAPI(FakeAPI):
        async def request(self, name, *args, **kwargs):
            if name == 'get_bot_qrcode':
                return {'qrcode': 'qr', 'qrcode_img_content': 'https://weixin.qq.com/mock-qr'}
            return {'status': 'confirmed', 'bot_token': 'private-token', 'ilink_user_id': 'owner',
                    'ilink_bot_id': 'bot', 'baseurl': BASE}
    api = LoginAPI()
    path, svg = tmp_path / 'account.json', tmp_path / 'qr.svg'
    await login(path, svg, api=api)
    saved = read_account(path)
    assert saved.token == 'private-token' and saved.owner == 'owner' and saved.enabled
    assert svg.read_text().startswith('<?xml')
    assert api.closed


async def test_disable_stops_channel_and_cancels_only_wechat_job(core, tmp_path, monkeypatch):
    api = FakeAPI()
    original = WeChatChannel
    monkeypatch.setattr('little_blue.wechat.WeChatChannel', lambda c, a: original(c, a, api))
    path = tmp_path / 'account.json'
    save_account(path, account())
    task = asyncio.create_task(supervise(core, path))
    try:
        await until(lambda: core.wechat_state == 'connecting')
        disabled = account()
        disabled.enabled = False
        save_account(path, disabled)
        await until(lambda: core.wechat_state == 'disabled')
        assert api.closed and not core.wechat_ready
    finally:
        await stop(task)
