import asyncio
import json
import time

import pytest
from little_blue.cognition import Agent, ProactiveGate
from little_blue.core import Core
from little_blue.models import Environment, Settings
from little_blue.storage import Store


def test_explicit_activity_controls_source_and_restart(tmp_path):
    path = tmp_path / 'memory.db'
    s = Store(path)
    c = s.continuity
    mid = c.append('user', '我正在写论文', 'desktop', 100)
    assert '记住了' in c.observe('我正在写论文', 'desktop', mid, 100)
    a = c.snapshot()['activity']
    assert a['source_id'] == mid and a['title'] == '写论文'
    assert a['origin'] == 'desktop' and a['due'] == 1900
    assert '晚点' in c.observe('还没做完', 'wechat', 2, 200)
    assert c.snapshot()['activity']['due'] == 3800
    c.observe('/followup off', 'wechat', 3, 300)
    s.task('pending', 'running', 'must not replay')
    s.close()
    s = Store(path)
    assert not s.continuity.snapshot()['followups']
    assert s.continuity.recent()[0]['text'] == '我正在写论文'
    assert s.tasks()[0]['status'] == 'interrupted'
    assert '已经完成' in s.continuity.observe('做完了', 'wechat', 4, 400)
    assert s.continuity.snapshot()['activity']['status'] == 'done'
    s.close()


@pytest.mark.parametrize('text', ['她说我正在写论文', '如果我正在写论文', '我不在写论文',
    '我正在写论文吗？', '“我正在写论文”', '我在想要不要写论文', '我正在写论文，帮我找资料',
    '她做完了', '还没完成', '不希望你说“做完了”'])
def test_ambiguous_or_quoted_text_does_not_mutate_activity(tmp_path, text):
    s = Store(tmp_path / 'memory.db')
    assert s.continuity.observe(text, 'desktop', 1) is None
    assert s.continuity.snapshot()['activity'] is None
    s.close()


def test_followup_gate_expiry_and_no_repeat_after_restart(tmp_path):
    path = tmp_path / 'memory.db'
    s = Store(path)
    c = s.continuity
    c.observe('/doing 写论文', 'desktop', 1, 100)
    settings, env, gate = Settings(), Environment(idle_seconds=60), ProactiveGate(s)
    gate.started = 0
    assert c.claim_followup(1899, settings, env, gate) is None
    for changed in ('dnd', 'proactive', 'focused', 'idle', 'interacting', 'toggle'):
        settings.dnd = changed == 'dnd'
        settings.proactive = changed != 'proactive'
        env.focused = changed == 'focused'
        env.interacting = changed == 'interacting'
        env.idle_seconds = 0 if changed == 'idle' else 60
        s.put('activity_followups', changed != 'toggle')
        assert c.claim_followup(2000, settings, env, gate) is None
    s.put('activity_followups', True)
    assert '写论文' in c.claim_followup(2000, settings, env, gate)
    assert c.claim_followup(5000, settings, env, gate) is None
    s.close()
    s = Store(path)
    gate = ProactiveGate(s)
    gate.started = 0
    assert s.continuity.claim_followup(6000, settings, env, gate) is None
    s.continuity.observe('/later', 'wechat', 2, 6000)
    assert s.continuity.claim_followup(9601, settings, env, gate)
    s.continuity.observe('/later', 'wechat', 3, 10000)
    assert s.continuity.claim_followup(13601, settings, env, gate) is None  # 2 / rolling day
    assert s.continuity.claim_followup(100000, settings, env, gate) is None  # stale activity
    s.close()


async def test_cross_channel_history_and_activity_reach_model_after_restart(tmp_path):
    path = tmp_path / 'memory.db'
    s = Store(path)
    agent = Agent(s, Settings(), None)
    await agent.run('我正在准备答辩', {})
    await agent.run('我的主题是海洋生态', {})
    s.close()
    s = Store(path)
    s.growth.control('off')
    requests = []
    class Client:
        async def complete(self, messages, schemas=None):
            requests.append(messages)
            assert schemas is None
            assert any(m['content'] == '我的主题是海洋生态' for m in messages)
            assert sum(m['content'] == '接着聊刚才的主题' for m in messages) == 1
            assert '准备答辩' in messages[2]['content']
            return {'content': '继续聊海洋生态吧。'}
    agent = Agent(s, Settings(online=True, tools_enabled=True), None, Client())
    assert '海洋生态' in await agent.run('接着聊刚才的主题', {}, chat_only=True)
    assert requests
    messages = s.continuity.recent()
    assert [m['origin'] for m in messages] == ['desktop'] * 4 + ['wechat'] * 2
    await agent.run('做完了', {}, chat_only=True)
    assert s.continuity.snapshot()['activity']['status'] == 'done'
    assert len(requests) == 1  # Explicit updates need no API and work offline too.
    s.close()


async def test_cancel_records_user_fact_without_fake_answer_and_proactive_no_fake_user(tmp_path):
    s = Store(tmp_path / 'memory.db')
    entered = asyncio.Event()
    class Client:
        async def complete(self, *args):
            entered.set()
            await asyncio.Event().wait()
    agent = Agent(s, Settings(online=True), None, Client())
    job = asyncio.create_task(agent.run('我的主题是海洋生态', {}))
    await entered.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert [m['role'] for m in s.continuity.recent()] == ['user']
    agent.settings.online = False
    await agent.run('INTERNAL PROMPT', {}, proactive=True)
    assert 'INTERNAL PROMPT' not in json.dumps(s.continuity.recent())
    assert [m['role'] for m in s.continuity.recent()] == ['user', 'assistant']
    s.close()


async def test_core_followup_routes_once_without_model_or_replaying_task(tmp_path):
    s = Store(tmp_path / 'memory.db')
    core = Core(s)
    core.wechat_ready = True
    now = time.time()
    core.gate.started = now - 100
    s.continuity.observe('/doing 写论文', 'desktop', 1, now - 1900)
    await core.start()
    try:
        await asyncio.sleep(.7)
        followups = [e for e in core.events if e['kind'] == 'followup']
        assert len(followups) == 1 and followups[0]['origin'] == 'wechat'
        assert core.current is None and s.tasks() == []
        assert s.continuity.recent()[-1]['text'] == followups[0]['text']
    finally:
        await core.close()
