import json
import time

import pytest

from little_blue.cognition import Agent, ProactiveGate
from little_blue.conversation_scene import scene
from little_blue.models import Environment, Settings
from little_blue.storage import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'memory.db')
    yield s
    s.close()


def test_fragments_repair_and_bedtime_keep_evidence_without_learning_traits(store):
    now = time.time()
    first = store.continuity.append('user', '我抽卡歪飞了呜呜呜', 'wechat', now=now)
    store.continuity.append('user', '220抽0+0', 'wechat', now=now + 1)
    assert scene(store, now + 2)['cues']['distress']['source_id'] == first
    store.continuity.append('assistant', '明天有精神了再接着歪', 'wechat', now=now + 3)
    correction = store.continuity.append('user', '呜呜呜不准你这么说😭', 'wechat', now=now + 4)
    store.continuity.append('assistant', '我收回', 'wechat', now=now + 5)
    store.continuity.append('user', '菲欧尼', 'wechat', now=now + 6)
    state = scene(store, now + 7)
    assert state['avoid_teasing'] and state['cues']['repair']['source_id'] == correction
    store.continuity.append('user', '我准备睡觉了', 'desktop', now=now + 8)
    store.continuity.append('user', '我睡不着啊，这个太难受了', 'wechat', now=now + 9)
    assert set(scene(store, now + 10)['cues']) == {'distress', 'repair', 'bedtime'}
    assert not store.semantic() and not store.search('')
    assert not scene(store, now + 8 * 3600 + 10)['defer_unsolicited']


@pytest.mark.parametrize('text', ['你睡不着吗', '他说晚安', '“我准备睡觉了”是什么意思',
                                  '我不难受', '我没有睡不着', '晚安是什么意思', '/status'])
def test_questions_quotes_negations_are_not_user_state(store, text):
    store.continuity.append('user', text, 'wechat')
    store.continuity.append('assistant', '我睡不着，晚安', 'wechat')
    assert not scene(store)['defer_unsolicited']


def test_receipt_blocks_before_processing_and_wakeup_reopens_gate(store):
    now = time.time()
    settings, env = Settings(), Environment()
    gate = ProactiveGate(store)
    gate.started = now - 100
    store.continuity.admit('test', 'one', '我准备睡觉了')
    assert not gate.available(now + 120, settings, env)
    store.continuity.append('user', '/status', 'wechat', now=now + 180)
    assert not gate.available(now + 300, settings, env)
    store.continuity.append('user', '我睡醒了', 'desktop', now=now + 400)
    assert gate.available(now + 500, settings, env)


async def test_agent_sees_one_turn_with_parts_and_keeps_previous_correction(store):
    store.growth.control('off')
    correction = store.continuity.append('user', '别拿我开玩笑', 'desktop')
    store.continuity.append('assistant', '好，我收回。', 'desktop')
    ids = [store.continuity.admit('test', str(i), text) for i, text in enumerate(
        ['我抽卡歪飞了呜呜呜', '220抽0+0', '菲欧尼'])]
    store.continuity.inbox_state(ids, 'processing')

    class Client:
        async def complete(self, messages, schemas):
            context = json.loads(messages[2]['content'].split('：', 1)[1])
            assert [r['id'] for r in context['current_input_parts']] == ids
            assert context['conversation_scene']['cues']['repair']['source_id'] == correction
            assert messages[-1]['content'] == '我抽卡歪飞了呜呜呜\n220抽0+0\n菲欧尼'
            assert sum(m['content'] == '220抽0+0' for m in messages) == 0
            assert schemas is None
            return {'content': '220抽才0+0，真的太背了😭'}

    result = await Agent(store, Settings(online=True), None, Client()).run(
        '', {}, chat_only=True, received_ids=ids)
    assert result and scene(store)['avoid_teasing']
    assert not store.semantic()


def test_scene_survives_restart_but_expires(tmp_path):
    path = tmp_path / 'pet.db'
    s = Store(path)
    now = time.time()
    s.continuity.append('user', '别再开玩笑', 'wechat', now=now)
    s.close()
    s = Store(path)
    assert scene(s, now + 60)['avoid_teasing']
    assert not scene(s, now + 3601)['avoid_teasing']
    s.close()
