import json
import random

import pytest

from little_blue.cognition import Agent
from little_blue.dialogue_style import catalog, prompt, select, RECENT_KEY
from little_blue.models import Settings
from little_blue.storage import Store
from little_blue.tools import ToolRuntime


def test_library_has_300_unique_short_replies_and_stable_ids():
    groups = catalog()
    examples = [e for g in groups for e in g['examples']]
    assert len(groups) == 20 and len(examples) == 300
    assert len({e['id'] for e in examples}) == 300
    assert len({e['reply'] for e in examples}) == 300
    assert all('[[meme:' not in e['reply'] and '[[no_reply]]' not in e['reply'] for e in examples)


@pytest.mark.parametrize('text', ['想我了没', '想发表情就发', '帮我解释代码', '我去睡了', '你好', '别的鱼'])
def test_selection_is_bounded_and_does_not_repeat_recent_ids(text):
    first = select(text, {}, rng=random.Random(7))
    second = select(text, {}, [e['id'] for e in first], rng=random.Random(7))
    assert 0 < len(first) <= 3 and 0 < len(second) <= 3
    assert not {e['id'] for e in first} & {e['id'] for e in second}


def test_distress_never_uses_teasing_jealousy_or_affection_fixtures():
    for n in range(20):
        chosen = select('我抽卡歪飞了，想找别的鱼玩', {'avoid_teasing': True}, rng=random.Random(n))
        assert chosen and all(e['group'] in {'comfort', 'repair', 'bedtime', 'busy', 'practical', 'fragments'} for e in chosen)
    assert all(e['group'] != 'jealousy' for e in select('你好', {}, previous_user='今天去看别的鱼了'))
    assert all(e['group'] not in {'affection', 'jealousy', 'emotion'}
               for e in select('别撒娇，帮我解释这个', {}))


def test_fragments_use_adjacent_user_context_and_proactive_respects_quiet():
    assert all(e['group'] == 'study' for e in select('三道', {}, previous_user='今天还有好多作业'))
    assert select('主动聊一下', {'defer_unsolicited': True}, proactive=True) == []
    assert select('这条不用回复哦', {}) == []
    assert all(e['group'] in {'missing', 'daily', 'emotion', 'curiosity'}
               for e in select('想起今天的事', {}, proactive=True))


def test_prompt_only_persists_ids_and_cannot_claim_examples_are_memories(tmp_path):
    store = Store(tmp_path / 'style.db')
    try:
        text = prompt(store, '想我了吗', {})
        assert '虚构' in text and '不是历史' in text and '沉默策略优先' in text
        assert len(text) < 2000
        assert len(json.loads(text.split('\n', 1)[1])) <= 3
        assert store.db.execute('SELECT count(*) FROM memories').fetchone()[0] == 0
        assert store.db.execute('SELECT count(*) FROM conversation').fetchone()[0] == 0
        ids = store.get(RECENT_KEY)
        assert all(isinstance(i, str) and '.' in i for i in ids)
    finally:
        store.close()
    reopened = Store(tmp_path / 'style.db')
    try:
        assert reopened.get(RECENT_KEY) == ids
    finally:
        reopened.close()


@pytest.mark.parametrize('reflection', [False, True])
async def test_agent_injects_style_in_chat_but_not_reflection(tmp_path, reflection):
    store = Store(tmp_path / 'agent.db')
    settings = Settings(online=True, tools_enabled=False)
    store.growth.control('off')
    calls = []
    class Client:
        async def complete(self, messages, tools=None):
            calls.append(messages)
            return {'content': '嗯，今天慢慢聊。'}
    async def approve(*args):
        raise AssertionError('No tool use')
    try:
        agent = Agent(store, settings, ToolRuntime(store, settings, approve), Client())
        await agent.run('想我了吗', {}, reflection=reflection, chat_only=not reflection)
        references = [m for m in calls[0] if m['content'].startswith('以下是虚构的语气范例')]
        assert len(references) == (0 if reflection else 1)
        assert all('以下是虚构的语气范例' not in r['text'] for r in store.search('', 30))
    finally:
        store.close()
